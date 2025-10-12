from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QMutex, QMutexLocker, QThread, Signal, Qt
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)


class VideoBuffer:
    def __init__(self, width: int = 1280, height: int = 720):
        self.width = width
        self.height = height
        self.stride = self.width * 4
        self.size = self.stride * self.height
        self.buf = (ctypes.c_ubyte * self.size)()
        self.mutex = QMutex()

    def lock(self, opaque, planes):
        self.mutex.lock()
        arr = ctypes.cast(planes, ctypes.POINTER(ctypes.c_void_p))
        arr[0] = ctypes.addressof(self.buf)
        return None

    def unlock(self, opaque, picture, planes):
        self.mutex.unlock()

    def display(self, opaque, picture):
        # handled by update_frame
        pass


class ThumbnailWorker(QThread):
    thumbnail_ready = Signal(int, QImage)

    def __init__(self, video_path: str, interval_s: int = 5, width: int = 96, height: int = 54):
        super().__init__()
        self.video_path = video_path
        self.interval_s = interval_s
        self.width = width
        self.height = height
        self._running = True

    def run(self):
        import cv2  # local import to keep module import fast

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            log.error("Could not open video for thumbnails: %s", self.video_path)
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            log.warning("Invalid FPS in %s; skipping thumbnail generation", self.video_path)
            cap.release()
            return
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        dur_ms = (frame_count / fps) * 1000
        t = 0.0
        while self._running and t < dur_ms:
            frame_num = int((t / 1000.0) * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            success, frame = cap.read()
            if success:
                frame = cv2.resize(frame, (self.width, self.height))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.thumbnail_ready.emit(int(t), qimg.copy())
            t += self.interval_s * 1000
        cap.release()

    def stop(self):
        self._running = False
        self.wait()


def get_frame_at(video_path: str, ms: int, width: int = 96, height: int = 54) -> QImage | None:
    time_sec = ms / 1000.0
    tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(time_sec),
        "-i",
        video_path,
        "-vframes",
        "1",
        "-vf",
        f"scale={width}:{height}",
        "-loglevel",
        "quiet",
        tmp_path,
    ]

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        result = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
            check=False,
        )
    except FileNotFoundError:
        log.info("ffmpeg missing; using fallbacks for %s", video_path)
        Path(tmp_path).unlink(missing_ok=True)
        return _frame_via_fallback(video_path, ms, width, height)
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception("Failed to spawn ffmpeg for %s", video_path)
        Path(tmp_path).unlink(missing_ok=True)
        return _frame_via_fallback(video_path, ms, width, height)

    if result.returncode != 0 or not Path(tmp_path).exists():
        log.info("ffmpeg returned code %s for %s; falling back", result.returncode, video_path)
        Path(tmp_path).unlink(missing_ok=True)
        return _frame_via_fallback(video_path, ms, width, height)

    image = QImage(tmp_path)
    Path(tmp_path).unlink(missing_ok=True)
    if image.isNull():
        log.info("ffmpeg produced null image for %s; falling back", video_path)
        return _frame_via_fallback(video_path, ms, width, height)
    return image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _frame_via_fallback(video_path: str, ms: int, width: int, height: int) -> QImage | None:
    image = _frame_via_opencv(video_path, ms, width, height)
    if image is not None and not image.isNull():
        return image
    return _frame_via_vlc(video_path, ms, width, height)


def _frame_via_opencv(video_path: str, ms: int, width: int, height: int) -> QImage | None:
    try:
        import cv2
    except ImportError:
        log.warning("OpenCV not available; cannot fallback for %s", video_path)
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened() and hasattr(cv2, "CAP_FFMPEG"):
        cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        log.warning("OpenCV could not open %s", video_path)
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps and fps > 0:
            frame_idx = int((ms / 1000.0) * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, ms)
        success, frame = cap.read()
        attempts = 0
        while (not success or frame is None) and attempts < 5:
            success, frame = cap.read()
            attempts += 1
        if not success or frame is None:
            log.debug("OpenCV read failed for %s at %sms after retries", video_path, ms)
            return None
        frame = cv2.resize(frame, (width, height))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888)
        return qimg.copy()
    except Exception:  # pragma: no cover
        log.exception("OpenCV fallback failed for %s at %sms", video_path, ms)
        return None
    finally:
        cap.release()


def _frame_via_vlc(video_path: str, ms: int, width: int, height: int) -> QImage | None:
    try:
        import vlc  # type: ignore
    except ImportError:
        log.debug("python-vlc not installed; cannot use VLC fallback for %s", video_path)
        return None

    instance = None
    media = None
    player = None
    try:
        instance = vlc.Instance("--no-xlib", "--quiet")
        media = instance.media_new(video_path)
        player = instance.media_player_new()
        player.set_media(media)
        player.video_set_scale(0)
        player.play()

        # Wait briefly for player to start decoding
        for _ in range(40):
            state = player.get_state()
            if state in (vlc.State.Playing, vlc.State.Paused):
                break
            time.sleep(0.05)

        player.set_time(ms)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_path = tmp.name
        tmp.close()
        result = player.video_take_snapshot(0, tmp_path, width, height)
        player.stop()

        if result != 0:
            Path(tmp_path).unlink(missing_ok=True)
            log.debug("VLC snapshot failed for %s at %sms", video_path, ms)
            return None

        image = QImage(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        if image.isNull():
            log.debug("VLC snapshot produced null image for %s", video_path)
            return None
        return image
    except Exception:  # pragma: no cover
        log.exception("VLC fallback failed for %s", video_path)
        return None
    finally:
        try:
            if player:
                player.release()
            if media:
                media.release()
            if instance:
                instance.release()
        except Exception:
            pass


class ThumbnailListWorker(QThread):
    thumb_ready = Signal(str, QImage)

    def __init__(self, folder_path: str, thumb_ms: int = 3000, width: int = 160, height: int = 90):
        super().__init__()
        self.folder_path = Path(folder_path)
        self.thumb_ms = thumb_ms
        self.width = width
        self.height = height
        self._running = True

    def stop(self):
        self._running = False
        self.wait()

    def run(self):
        if not self.folder_path.is_dir():
            return
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
        files = sorted(
            (f for f in self.folder_path.iterdir() if f.suffix.lower() in video_exts),
            key=lambda p: p.name.lower(),
        )
        for file_path in files:
            if not self._running:
                break
            path_str = str(file_path)
            image = None
            for offset in (self.thumb_ms, 1000, 0):
                if offset is None or offset < 0:
                    continue
                image = get_frame_at(path_str, offset, self.width, self.height)
                if image is not None and not image.isNull():
                    log.debug("Thumbnail generated for %s at %sms", path_str, offset)
                    break
            if image is None or image.isNull():
                log.debug("Thumbnail generation failed for %s; using placeholder", path_str)
                self.thumb_ready.emit(path_str, QImage())
            else:
                self.thumb_ready.emit(path_str, image)
