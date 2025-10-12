from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional
import ctypes

if __package__ is None:  # running as script
    import sys

    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    __package__ = "nexa_player"

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen, QDialog, QMessageBox

try:  # pragma: no cover - runtime environment detail
    from . import resources_rc  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    _project_root = Path(__file__).resolve().parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    import resources_rc  # type: ignore  # noqa: F401

from .helpers import get_video_duration, ms_to_minsec
from .services.dependency_check import DependencyChecker
from .services.state import StateStore
from .services.thumbnails import ThumbnailWorker, VideoBuffer
from .ui.file_loader import FileLoader
from .ui.player_window import PlayerWindow
from .ui.playlist_dialog import PlaylistDialog

log = logging.getLogger(__name__)


class NexaApp(QApplication):
    media_finished = Signal()

    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName("Nexa Player")

        self.state = StateStore()
        self.settings = self.state.settings
        vlc_instance = DependencyChecker(parent=self).ensure()
        if vlc_instance is None:
            sys.exit(0)

        self.vlc = __import__("vlc")
        self.instance = vlc_instance
        self.mediaplayer = self.instance.media_player_new()

        event_manager = self.mediaplayer.event_manager()
        event_manager.event_attach(self.vlc.EventType.MediaPlayerPlaying, self._on_media_playing)
        event_manager.event_attach(self.vlc.EventType.MediaPlayerEndReached, self._on_media_end)

        self.video_path: Optional[str] = None
        self.has_media = False
        self.video_duration_ms: Optional[int] = None
        self.thumbnail_worker: Optional[ThumbnailWorker] = None
        self.thumbnail_cache: dict[int, QPixmap] = {}
        self._playing_expected = False

        saved_playlist = [p for p in self.state.get_last_playlist() if Path(p).exists()]
        self.playlist: List[str] = saved_playlist
        self.current_index = 0 if self.playlist else -1
        last_file = self.state.get_last_file()
        if last_file and last_file in self.playlist:
            self.current_index = self.playlist.index(last_file)

        self.loop_playlist = False
        self.loop_enabled = False
        self.media_finished.connect(self._play_next_safe)

        self.miniplayer_enabled = self.state.get_miniplayer_enabled()

        self.video_buf = VideoBuffer()
        LockCB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        UnlockCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        DisplayCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

        def _lock(opaque, planes):
            return self.video_buf.lock(opaque, planes)

        def _unlock(opaque, picture, planes):
            self.video_buf.unlock(opaque, picture, planes)

        def _display(opaque, picture):
            self.video_buf.display(opaque, picture)

        self._lock_cb = LockCB(_lock)
        self._unlock_cb = UnlockCB(_unlock)
        self._display_cb = DisplayCB(_display)

        self.mediaplayer.video_set_callbacks(self._lock_cb, self._unlock_cb, self._display_cb, None)
        self.mediaplayer.video_set_format("RGBA", self.video_buf.width, self.video_buf.height, self.video_buf.stride)

        self.broadcast = PlayerWindow("Nexa Player", is_broadcast=True)
        self.broadcast.show()
        self.broadcast.destroyed.connect(self._on_broadcast_closed)

        if self.broadcast.resume_banner:
            self.broadcast.resume_banner.resume_requested.connect(self._on_resume_clicked)
            self.broadcast.resume_banner.restart_requested.connect(self._on_restart_clicked)

        if self.miniplayer_enabled:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
            if self.mini.resume_banner:
                self.mini.resume_banner.resume_requested.connect(self._on_resume_clicked)
                self.mini.resume_banner.restart_requested.connect(self._on_restart_clicked)
        else:
            self.mini = None

        saved_volume = self.state.get_volume()
        self.mediaplayer.audio_set_volume(saved_volume)
        if self.broadcast.volume_slider:
            self.broadcast.volume_slider.setValue(saved_volume)

        saved_rate = self.state.get_rate()
        if saved_rate != 1.0:
            self.mediaplayer.set_rate(saved_rate)

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(250)
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start()

        self._last_ui_second = -1
        self._pending_resume_ms: Optional[int] = None

        self.aboutToQuit.connect(self._cleanup)

    # ------------------------------------------------------------------
    # Playlist helpers

    def add_to_playlist(self, path: str):
        if os.path.exists(path):
            self.playlist.append(path)
            if self.current_index == -1:
                self.current_index = 0
            self.state.set_last_playlist(self.playlist)

    def load_playlist(self, paths: list[str]):
        self.playlist = [p for p in paths if os.path.exists(p)]
        self.current_index = 0 if self.playlist else -1
        self.state.set_last_playlist(self.playlist)

    def play_from_playlist(self, path: str):
        if not path or not os.path.exists(path):
            return
        try:
            self.current_index = self.playlist.index(path)
        except ValueError:
            self.playlist.append(path)
            self.current_index = len(self.playlist) - 1
            self.state.set_last_playlist(self.playlist)
        self.open_path(path)

    def show_playlist(self):
        dlg = PlaylistDialog(self.playlist, play_callback=self.play_from_playlist, parent=self.broadcast)
        if dlg.exec():
            updated = dlg.get_playlist()
            self.playlist = [p for p in updated if os.path.exists(p)]
            self.state.set_last_playlist(self.playlist)
            if not self.playlist:
                self.current_index = -1
            elif self.current_index < 0 or self.current_index >= len(self.playlist):
                self.current_index = 0

    def play_current(self):
        if not self.playlist:
            return
        if self.current_index < 0 or self.current_index >= len(self.playlist):
            return
        path = self.playlist[self.current_index]
        self.open_path(path)

    def next_track(self):
        if not self.playlist:
            self.current_index = -1
            return
        self.current_index += 1
        if self.current_index >= len(self.playlist):
            if self.loop_playlist:
                self.current_index = 0
            else:
                self.current_index = len(self.playlist) - 1
                return
        self.play_current()

    def previous_track(self):
        if not self.playlist:
            return
        self.current_index -= 1
        if self.current_index < 0:
            if self.loop_playlist:
                self.current_index = len(self.playlist) - 1
            else:
                self.current_index = 0
                return
        self.play_current()

    def toggle_loop_playlist(self):
        self.loop_playlist = not self.loop_playlist
        self.broadcast.show_overlay_message(f"Loop Playlist: {'On' if self.loop_playlist else 'Off'}")

    # ------------------------------------------------------------------
    # VLC callbacks & events

    def _on_media_playing(self, event):
        try:
            self.mediaplayer.video_set_scale(0)
            saved_ratio = self.state.get_aspect_ratio()
            if saved_ratio:
                self.mediaplayer.video_set_aspect_ratio(saved_ratio.encode("utf-8"))
            w = self.mediaplayer.video_get_width()
            h = self.mediaplayer.video_get_height()
            if not w or not h:
                size = self.mediaplayer.video_get_size(0)
                if size:
                    w, h = size
                else:
                    w, h = (640, 360)
            self.video_buf = VideoBuffer(w, h)
            self.mediaplayer.video_set_format("RGBA", w, h, self.video_buf.stride)
            log.debug("Video started: %sx%s", w, h)
            # leave resume prompt active until user chooses
        except Exception:  # pragma: no cover - defensive
            log.exception("Error in _on_media_playing")

    def _on_media_end(self, event):
        log.info("End of media detected by VLC")
        self.state.clear_resume_position(self.video_path or "")
        if self.loop_enabled:
            QTimer.singleShot(0, self._restart_media)
        else:
            self.media_finished.emit()

        self.mediaplayer.stop()
        self.mediaplayer.set_time(0)
        self._start_playback()
        self.mediaplayer.play()
        self._set_play_icon(True)

    def _start_playback(self):
        self._playback_retry_attempts = 0
        result = self.mediaplayer.play()
        if result == -1:
            log.warning("Initial play request returned -1; will retry")
        self._schedule_playback_check()

    def _schedule_playback_check(self):
        QTimer.singleShot(200, self._ensure_playing_state)

    def _ensure_playing_state(self):
        if not self._playing_expected:
            return
        state = self.mediaplayer.get_state()
        if state in (self.vlc.State.Playing, self.vlc.State.Buffering):
            return
        if state in (self.vlc.State.Opening, self.vlc.State.NothingSpecial):
            self._schedule_playback_check()
            return
        if state == self.vlc.State.Paused and self._playing_expected:
            self.mediaplayer.play()
            self._schedule_playback_check()
            return
        attempts = getattr(self, "_playback_retry_attempts", 0)
        if attempts >= 5:
            log.warning("Unable to start playback after retries (state=%s)", state)
            return
        self._playback_retry_attempts = attempts + 1
        log.debug("Retrying playback (attempt %s, state=%s)", self._playback_retry_attempts, state)
        self.mediaplayer.play()
        self._schedule_playback_check()

    # ------------------------------------------------------------------
    # UI integration

    def _set_play_icon(self, playing: bool):
        self.broadcast.play_btn.setIcon(self.broadcast.icon_pause if playing else self.broadcast.icon_play)
        if self.mini:
            self.mini.play_btn.setIcon(self.mini.icon_pause if playing else self.mini.icon_play)
        self._playing_expected = playing

    def _on_broadcast_closed(self):
        if self.mini is not None:
            still_visible = self.mini.isVisible()
            self.state.set_miniplayer_enabled(still_visible)
            self.mini.close()
            self.mini = None

    def toggle_miniplayer(self, enabled: bool):
        self.miniplayer_enabled = enabled
        self.state.set_miniplayer_enabled(enabled)
        if enabled and self.mini is None:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
            if self.mini.resume_banner:
                self.mini.resume_banner.resume_requested.connect(self._on_resume_clicked)
                self.mini.resume_banner.restart_requested.connect(self._on_restart_clicked)
            if self._pending_resume_ms:
                message = f"Resume from {ms_to_minsec(self._pending_resume_ms)}?"
                self.mini.show_resume_prompt(message)
            if self.video_path:
                self.update_titles(self.video_path)
        elif not enabled and self.mini is not None:
            self.mini.hide_resume_prompt()
            self.mini.close()
            self.mini = None

    # ------------------------------------------------------------------
    # File operations

    def open_file(self):
        dlg = FileLoader(self.broadcast)
        if dlg.exec() == QDialog.Accepted:
            file = dlg.get_selected_file()
            if file:
                self.open_path(file)

    def open_path(self, path: str):
        if self.mediaplayer.is_playing():
            self.mediaplayer.stop()
        media = self.instance.media_new(path)
        self.mediaplayer.set_media(media)
        self._start_playback()
        self.video_path = path
        self.state.set_last_file(path)
        self.video_duration_ms = get_video_duration(path)
        self.has_media = True
        self.state.set_last_playlist(self.playlist)

        self.thumbnail_cache = {}
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
        self.thumbnail_worker = ThumbnailWorker(path, interval_s=30)
        self.thumbnail_worker.thumbnail_ready.connect(self._store_thumbnail)
        self.thumbnail_worker.start()

        self.update_titles(path)
        self._set_play_icon(True)
        resume_positions = self.state.get_resume_positions()
        resume_ms = resume_positions.get(path)
        if resume_ms:
            self._pending_resume_ms = resume_ms
            message = f"Resume from {ms_to_minsec(resume_ms)}?"
            self.broadcast.show_resume_prompt(message)
            if self.mini:
                self.mini.show_resume_prompt(message)
        else:
            self.broadcast.hide_resume_prompt()
            if self.mini:
                self.mini.hide_resume_prompt()

    def update_titles(self, mrl: str):
        filename = os.path.basename(mrl)
        name_only, _ = os.path.splitext(filename)
        self.broadcast.setWindowTitle(f"{name_only} - Nexa Player")
        if self.mini is not None:
            self.mini.setWindowTitle(f"{name_only} - Nexa Player - PIP")

    def _store_thumbnail(self, time_ms: int, image):
        if image.isNull():
            return
        self.thumbnail_cache[time_ms] = QPixmap.fromImage(image)

    # ------------------------------------------------------------------
    # Playback controls

    def play_pause(self):
        state = self.mediaplayer.get_state()
        if state == self.vlc.State.Ended:
            self._restart_media()
            return

        if self._playing_expected:
            self.mediaplayer.pause()
            self._set_play_icon(False)
        else:
            self.mediaplayer.play()
            self._set_play_icon(True)

    def stop(self):
        self.state.clear_resume_position(self.video_path or "")
        self.mediaplayer.stop()
        self.broadcast.hide_resume_prompt()
        if self.mini:
            self.mini.hide_resume_prompt()
        for win in (self.broadcast, self.mini):
            if not win:
                continue
            win.position.setValue(0)
            win.time_label.setText("--:-- / --:--")
        self._set_play_icon(False)

    def set_position(self, value: int):
        if self.has_media:
            length = self.mediaplayer.get_length()
            if length > 0:
                new_time = int((value / 1000) * length)
                self.mediaplayer.set_time(new_time)

    def seek(self, delta_ms):
        t = max(0, self.mediaplayer.get_time() + delta_ms)
        self.mediaplayer.set_time(t)

    def set_volume(self, val):
        self.mediaplayer.audio_set_volume(val)
        self.state.set_volume(val)

    def adjust_volume(self, delta):
        v = self.mediaplayer.audio_get_volume()
        self.set_volume(max(0, min(125, v + delta)))
        if self.broadcast.volume_slider:
            self.broadcast.volume_slider.setValue(self.mediaplayer.audio_get_volume())

    def set_playback_rate(self, rate: float):
        self.mediaplayer.set_rate(rate)
        self.state.set_rate(rate)
        self.broadcast.show_overlay_message(f"Speed: {rate:.2f}x")

    def adjust_rate(self, delta):
        rate = self.mediaplayer.get_rate()
        new_rate = max(0.5, min(2.0, rate + delta))
        self.set_playback_rate(new_rate)

    def toggle_loop(self):
        self.loop_enabled = not self.loop_enabled
        self.broadcast.show_overlay_message(f"Loop: {'On' if self.loop_enabled else 'Off'}")

    def set_aspect_ratio(self, ratio: str | None):
        state = self.mediaplayer.get_state()
        if ratio:
            self.mediaplayer.video_set_aspect_ratio(ratio.encode("utf-8"))
            self.mediaplayer.video_set_scale(0)
            self.state.set_aspect_ratio(ratio)
            self.broadcast.show_overlay_message(f"Aspect Ratio: {ratio}")
        else:
            self.mediaplayer.video_set_aspect_ratio(None)
            self.mediaplayer.video_set_scale(0)
            self.state.set_aspect_ratio("")
            self.broadcast.show_overlay_message("Aspect Ratio: Auto")
        if state in (self.vlc.State.Playing, self.vlc.State.Paused):
            self.mediaplayer.pause()
            QTimer.singleShot(50, self.mediaplayer.play)

    def list_audio_tracks(self):
        return self.mediaplayer.audio_get_track_description() or []

    def set_audio_track(self, track_id):
        self.mediaplayer.audio_set_track(track_id)
        desc = dict(self.list_audio_tracks()).get(track_id, f"Track {track_id}")
        self.broadcast.show_overlay_message(f"Audio: {desc}")

    def cycle_audio_track(self):
        tracks = self.list_audio_tracks()
        if not tracks:
            return
        current = self.mediaplayer.audio_get_track()
        ids = [tid for tid, _ in tracks]
        next_id = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self.set_audio_track(next_id)

    def list_subtitles(self):
        return self.mediaplayer.video_get_spu_description() or []

    def set_subtitle(self, sub_id):
        self.mediaplayer.video_set_spu(sub_id)
        desc = dict(self.list_subtitles()).get(sub_id, f"Subtitle {sub_id}")
        self.broadcast.show_overlay_message(f"Subtitles: {desc}")

    def cycle_subtitle_track(self):
        subs = self.list_subtitles()
        if not subs:
            return
        current = self.mediaplayer.video_get_spu()
        ids = [sid for sid, _ in subs]
        next_id = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self.set_subtitle(next_id)

    # ------------------------------------------------------------------
    # Resume banner callbacks

    def _on_resume_clicked(self):
        if not self._pending_resume_ms:
            return

        target = self._pending_resume_ms
        self._pending_resume_ms = None
        self._schedule_resume(target)

    def _on_restart_clicked(self):
        self.broadcast.hide_resume_prompt()
        if self.mini:
            self.mini.hide_resume_prompt()
        if self.video_path:
            self.state.clear_resume_position(self.video_path)
        self._pending_resume_ms = None
        self.mediaplayer.set_time(0)
        self.mediaplayer.play()

    def _schedule_resume(self, target_ms: int, retries: int = 10):
        def attempt(remaining: int):
            state = self.mediaplayer.get_state()
            if state in (self.vlc.State.Opening, self.vlc.State.NothingSpecial):
                if remaining > 0:
                    QTimer.singleShot(100, lambda: attempt(remaining - 1))
                return

            result = self.mediaplayer.set_time(target_ms)
            if result == -1 and remaining > 0:
                QTimer.singleShot(100, lambda: attempt(remaining - 1))
                return

            self.mediaplayer.play()
            self.broadcast.hide_resume_prompt()
            if self.mini:
                self.mini.hide_resume_prompt()
            self._set_play_icon(True)

        QTimer.singleShot(0, lambda: attempt(retries))

    # ------------------------------------------------------------------
    # Update loop

    def update_ui(self):
        length = self.mediaplayer.get_length()
        time_ = self.mediaplayer.get_time()
        current_second = time_ // 1000

        if length > 0:
            pos = int((time_ / length) * 1000)
            for win in (self.broadcast, self.mini):
                if not win:
                    continue
                win.position.blockSignals(True)
                win.position.setValue(pos)
                win.position.blockSignals(False)

        if current_second != self._last_ui_second:
            for win in (self.broadcast, self.mini):
                if not win:
                    continue
                if length > 0:
                    win.time_label.setText(f"{ms_to_minsec(time_)} / {ms_to_minsec(length)}")
                else:
                    win.time_label.setText("--:-- / --:--")
            self._last_ui_second = current_second

            if self.video_path and length > 0:
                if time_ < length - 3000:
                    self.state.set_resume_position(self.video_path, int(time_))
                else:
                    self.state.clear_resume_position(self.video_path)

        if self.loop_enabled and length > 0:
            state = self.mediaplayer.get_state()
            if state == self.vlc.State.Ended:
                self._restart_media()
            elif time_ >= length - 200:
                self.mediaplayer.set_time(0)
                self.mediaplayer.play()
                self._set_play_icon(True)

        if not self.loop_enabled and self.mediaplayer.get_state() == self.vlc.State.Ended:
            self._set_play_icon(False)

    # ------------------------------------------------------------------
    # Playlist advancement

    def _play_next_safe(self):
        if not self.playlist:
            self.current_index = -1
            return
        next_index = self.current_index + 1
        if next_index >= len(self.playlist):
            if self.loop_playlist:
                next_index = 0
            else:
                return
        self.current_index = next_index
        path = self.playlist[self.current_index]
        self.mediaplayer.stop()
        self.open_path(path)

    # ------------------------------------------------------------------
    # Shutdown

    def _cleanup(self):
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()
            self.thumbnail_worker = None


if __name__ == "__main__":  # pragma: no cover
    from .main import run

    raise SystemExit(run())