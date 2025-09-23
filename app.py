import sys, os, urllib.parse, ctypes, tempfile
import ffmpeg
import numpy as np
import time
import ctypes
from ctypes.wintypes import MSG
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QLabel, QFileDialog, QSizePolicy, QStyle, QMenu, QGraphicsOpacityEffect, QToolTip
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QMutex, QMutexLocker, QSize, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QAction, QKeySequence, QImage, QPixmap, QIcon, QCursor, QPainter, QColor, QFont
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtCore import QSettings

import vlc
import resources_rc


def probe_duration_ms(path: str) -> int | None:
    try:
        import ffmpeg
        info = ffmpeg.probe(path)
        dur_s = float(info["format"]["duration"])
        return int(dur_s * 1000)
    except Exception as e:
        print("Erro FFmpeg probe:", e)
        return None
# ---------------- Helpers ----------------
def ms_to_minsec(ms):
    if ms <= 0:
        return "00:00"
    s = ms // 1000
    return f"{s//60:02d}:{s%60:02d}"

def clean_filename_from_mrl(mrl: str) -> str:
    if mrl.startswith("file:///"):
        path = urllib.parse.unquote(mrl[8:])
    else:
        path = urllib.parse.unquote(mrl)
    return os.path.basename(path)


class ThumbnailWorker(QThread):
    thumbnail_ready = Signal(int, QPixmap)  # tempo_ms, pixmap

    def __init__(self, video_path: str, interval_s: int = 5, width=160, height=90):
        super().__init__()
        self.video_path = video_path
        self.interval_s = interval_s
        self.width = width
        self.height = height
        self._running = True

    def run(self):
        try:
            info = ffmpeg.probe(self.video_path)
            dur_s = float(info["format"]["duration"])
        except Exception as e:
            print("Erro probe:", e)
            return

        t = 0
        while self._running and t < dur_s:
            try:
                out, _ = (
                    ffmpeg
                    .input(self.video_path, ss=t)
                    .filter('scale', self.width, self.height)
                    .output('pipe:', vframes=1, format='rawvideo', pix_fmt='rgb24')
                    .run(capture_stdout=True, capture_stderr=True, quiet=True)
                )
                if out:
                    frame = np.frombuffer(out, np.uint8).reshape((self.height, self.width, 3))
                    qimg = QImage(frame.data, self.width, self.height, 3*self.width, QImage.Format_RGB888)
                    pix = QPixmap.fromImage(qimg)
                    self.thumbnail_ready.emit(int(t*1000), pix)
            except Exception as e:
                print("Erro thumb:", e)
            t += self.interval_s

    def stop(self):
        self._running = False
        self.wait()
# ---------------- Video buffer ----------------
class VideoBuffer:
    def __init__(self, width=1280, height=720):
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
        pass


def get_frame_at(video_path: str, ms: int, width: int = 160, height: int = 90) -> QPixmap | None:
    try:
        time_sec = ms / 1000.0
        out, _ = (
            ffmpeg
            .input(video_path, ss=time_sec)
            .filter('scale', width, height)
            .output('pipe:', vframes=1, format='rawvideo', pix_fmt='rgb24')
            .run(capture_stdout=True, capture_stderr=True, quiet=True)
        )
        if not out:
            return None
        frame = np.frombuffer(out, np.uint8).reshape((height, width, 3))
        qimg = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)
    except Exception as e:
        print("Erro FFmpeg:", e)
        return None


class SeekSlider(QSlider):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)

        # Label flutuante
        self.preview_label = QLabel(parent)
        self.preview_label.setWindowFlags(Qt.ToolTip)
        self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.preview_label.hide()

        # Cache de miniaturas
        self.thumbnail_cache = {}
        self.last_preview_time = 0
        self.throttle_ms = 200  # só gera preview a cada 200ms

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            x = event.pos().x()
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), x, self.width()
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
            self.sliderReleased.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        app = QApplication.instance()
        video_path = getattr(app, "video_path", None)
        duration_ms = getattr(app, "video_duration_ms", None)
        cache = getattr(app, "thumbnail_cache", {})

        if not video_path or not duration_ms:
            return

        # calcula tempo proporcional
        x = event.pos().x()
        value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, self.width()
        )
        preview_time = int((value / 1000.0) * duration_ms)

        # pega a miniatura mais próxima do cache
        if cache:
            nearest = min(cache.keys(), key=lambda k: abs(k - preview_time))
            pix = cache[nearest]
        else:
            # fallback: caixinha preta
            pix = QPixmap(160, 90)
            pix.fill(Qt.black)

        # desenha tempo em cima da miniatura
        img = pix.toImage()
        painter = QPainter(img)
        text = ms_to_minsec(preview_time)
        painter.setFont(QFont("Arial", 10, QFont.Bold))

        # stroke preto
        painter.setPen(QColor("black"))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                painter.drawText(5 + dx, img.height() - 5 + dy, text)

        # texto branco
        painter.setPen(QColor("white"))
        painter.drawText(5, img.height() - 5, text)
        painter.end()

        pix = QPixmap.fromImage(img)
        self.preview_label.setPixmap(pix)
        self.preview_label.adjustSize()

        global_pos = self.mapToGlobal(event.pos())
        self.preview_label.move(global_pos + QPoint(-pix.width() // 2, -pix.height() - 10))
        self.preview_label.show()

    def leaveEvent(self, event):
        self.preview_label.hide()
        super().leaveEvent(event)

# ---------------- Player window ----------------
class PlayerWindow(QWidget):
    def __init__(self, title, is_broadcast=False):
        super().__init__()
        self.is_broadcast = is_broadcast
        self.setWindowIcon(QIcon(":icons/nexaplayer.png"))
        self.fullscreen = False
        self.hud_visible = True
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Window |
            Qt.WindowStaysOnTopHint
        )
        self.setMouseTracking(True)
        self.setMinimumSize(160, 90)

        if is_broadcast:
            self.setWindowFlags(Qt.Window)
        else:
            self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowStaysOnTopHint)

        self.setWindowTitle(title)
        self.resize(640, 360)
        self.setStyleSheet("background-color: black;")
        self.setAcceptDrops(True)

        # Vídeo
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background-color: black; border: none; margin: 0; padding: 0;")
        self.label.setMinimumSize(100, 60)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        # HUD
        self.position = SeekSlider(Qt.Horizontal, parent=self)
        self.position.setRange(0, 1000)
        self.position.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 6px;
                background: #222;
                margin: 0px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #00bfff;
                border: 1px solid #00aaff;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: #00bfff;
                border-radius: 3px;
            }
            QSlider::add-page:horizontal {
                background: #555;
                border-radius: 3px;
            }
        """)

        btn_style = """
        QPushButton {
            background-color: #333;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px;
        }
        QPushButton:hover {
            background-color: #444;
        }
        """

        self.icon_play = QIcon(":/icons/play.png")
        self.icon_pause = QIcon(":/icons/pause.png")

        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.icon_play)
        self.play_btn.setIconSize(QSize(24, 24))
        self.play_btn.setStyleSheet(btn_style)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(QIcon(":/icons/stop.png"))
        self.stop_btn.setIconSize(QSize(24, 24))
        self.stop_btn.setStyleSheet(btn_style)

        self.open_btn = QPushButton()
        self.open_btn.setIcon(QIcon(":/icons/open.png"))
        self.open_btn.setIconSize(QSize(24, 24))
        self.open_btn.setStyleSheet(btn_style)

        self.full_btn = None
        if is_broadcast:
            self.full_btn = QPushButton()
            self.full_btn.setIcon(QIcon(":/icons/fullscreen.png"))
            self.full_btn.setIconSize(QSize(24, 24))
            self.full_btn.setStyleSheet(btn_style)

        self.volume_slider = None
        if is_broadcast:
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 125)
            self.volume_slider.setValue(80)
            self.volume_slider.setFixedWidth(100)
            self.volume_slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.volume_slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    border: 1px solid dark blue;
                    height: 6px;
                    background: white;
                }
                QSlider::handle:horizontal {
                    background: blue;
                    width: 12px;
                    margin: -4px 0;
                    border-radius: 6px;
                }
            """)

        self.time_label = QLabel("--:-- / --:--")
        self.time_label.setStyleSheet("color: white; font-weight: bold;")

        self.controls_row = QHBoxLayout()
        self.controls_row.setContentsMargins(0, 0, 0, 0)
        self.controls_row.setSpacing(6)
        self.controls_row.addWidget(self.open_btn)
        self.controls_row.addWidget(self.play_btn)
        self.controls_row.addWidget(self.stop_btn)
        if self.full_btn: self.controls_row.addWidget(self.full_btn)
        if self.volume_slider: self.controls_row.addWidget(self.volume_slider)
        self.controls_row.addWidget(self.time_label)

        self.hud_container = QWidget(self)
        self.hud_container.setStyleSheet("background: transparent;")
        self.hud_container.setLayout(self.controls_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.position)
        layout.addWidget(self.hud_container)

        # Frame timer
        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self.update_frame)
        self.frame_timer.start()

        # Conexões
        self.play_btn.clicked.connect(lambda: QApplication.instance().play_pause())
        self.stop_btn.clicked.connect(lambda: QApplication.instance().stop())
        self.open_btn.clicked.connect(lambda: QApplication.instance().open_file())
        self.position.sliderMoved.connect(lambda v: QApplication.instance().set_position(v))
        if self.volume_slider:
            self.volume_slider.valueChanged.connect(lambda v: QApplication.instance().set_volume(v))
        if self.full_btn:
            self.full_btn.clicked.connect(self.toggle_fullscreen)

        # Overlay
        self.overlay_label = QLabel(self.label)
        self.overlay_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0,0,0,160);
                color: white;
                padding: 6px 12px;
                border-radius: 6px;
                font-size: 14px;
            }
        """)
        self.overlay_label.setAlignment(Qt.AlignCenter)
        self.overlay_label.hide()
        self.overlay_opacity = QGraphicsOpacityEffect(self.overlay_label)
        self.overlay_label.setGraphicsEffect(self.overlay_opacity)
        self.overlay_anim = QPropertyAnimation(self.overlay_opacity, b"opacity")
        self.overlay_anim.setEasingCurve(QEasingCurve.InOutQuad)

        # Context menu
        self.label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self.show_context_menu)

        self.add_shortcuts()

    def mousePressEvent(self, event):
        # Mini-player: sempre permitir arrastar pelo centro com botão esquerdo
        if not self.is_broadcast and event.button() == Qt.LeftButton:
            hwnd = int(self.winId())
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 0x2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
            event.accept()
            return

        # Broadcast: se estiver frameless (HUD off), também permite arrastar pelo centro
        if self.is_broadcast and not self.hud_visible and event.button() == Qt.LeftButton:
            hwnd = int(self.winId())
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 0x2, 0)
            event.accept()
            return

        super().mousePressEvent(event)

    def nativeEvent(self, eventType, message):
        if eventType == "windows_generic_MSG":
            msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
            if msg.message == 0x84:  # WM_NCHITTEST
                pos = self.mapFromGlobal(QCursor.pos())
                w, h = self.width(), self.height()
                margin = 8

                left = pos.x() < margin
                right = pos.x() > w - margin
                top = pos.y() < margin
                bottom = pos.y() > h - margin

                if top and left:    return True, 13  # HTTOPLEFT
                if top and right:   return True, 14  # HTTOPRIGHT
                if bottom and left: return True, 16  # HTBOTTOMLEFT
                if bottom and right: return True, 17  # HTBOTTOMRIGHT
                if left:            return True, 10  # HTLEFT
                if right:           return True, 11  # HTRIGHT
                if top:             return True, 12  # HTTOP
                if bottom:          return True, 15  # HTBOTTOM

                # Força centro como arrastável, mesmo com barra de título
                buttons = QGuiApplication.mouseButtons()
                if buttons & Qt.LeftButton:
                    return True, 2  # HTCAPTION

        return False, 0

    # --- Drag & Drop ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                QApplication.instance().open_path(path)

    # --- Atalhos ---
    def add_shortcuts(self):
        act_play = QAction(self); act_play.setShortcut(QKeySequence(Qt.Key_Space))
        act_play.triggered.connect(lambda: QApplication.instance().play_pause()); self.addAction(act_play)

        if self.is_broadcast:
            act_f = QAction(self); act_f.setShortcut(QKeySequence(Qt.Key_F))
            act_f.triggered.connect(self.toggle_fullscreen); self.addAction(act_f)
            act_esc = QAction(self); act_esc.setShortcut(QKeySequence(Qt.Key_Escape))
            act_esc.triggered.connect(self.exit_fullscreen); self.addAction(act_esc)

        act_left = QAction(self); act_left.setShortcut(QKeySequence(Qt.Key_Left))
        act_left.triggered.connect(lambda: QApplication.instance().seek(-5000)); self.addAction(act_left)
        act_right = QAction(self); act_right.setShortcut(QKeySequence(Qt.Key_Right))
        act_right.triggered.connect(lambda: QApplication.instance().seek(5000)); self.addAction(act_right)

        if self.is_broadcast:
            act_plus = QAction(self); act_plus.setShortcut(QKeySequence(Qt.Key_Plus))
            act_plus.triggered.connect(lambda: QApplication.instance().adjust_volume(5)); self.addAction(act_plus)
            act_minus = QAction(self); act_minus.setShortcut(QKeySequence(Qt.Key_Minus))
            act_minus.triggered.connect(lambda: QApplication.instance().adjust_volume(-5)); self.addAction(act_minus)

        if not self.is_broadcast:
            act_h = QAction(self); act_h.setShortcut(QKeySequence(Qt.Key_H))
            act_h.triggered.connect(self.toggle_hud); self.addAction(act_h)

        # Extras: velocidade, loop, áudio, legendas
        act_speed_up = QAction(self); act_speed_up.setShortcut(QKeySequence("]"))
        act_speed_up.triggered.connect(lambda: QApplication.instance().adjust_rate(0.25)); self.addAction(act_speed_up)
        act_speed_down = QAction(self); act_speed_down.setShortcut(QKeySequence("["))
        act_speed_down.triggered.connect(lambda: QApplication.instance().adjust_rate(-0.25)); self.addAction(act_speed_down)

        act_loop = QAction(self); act_loop.setShortcut(QKeySequence("L"))
        act_loop.triggered.connect(lambda: QApplication.instance().toggle_loop()); self.addAction(act_loop)

        act_audio = QAction(self); act_audio.setShortcut(QKeySequence("A"))
        act_audio.triggered.connect(lambda: QApplication.instance().cycle_audio_track()); self.addAction(act_audio)

        act_subs = QAction(self); act_subs.setShortcut(QKeySequence("S"))
        act_subs.triggered.connect(lambda: QApplication.instance().cycle_subtitle_track()); self.addAction(act_subs)

    def toggle_hud(self):
        self.hud_visible = not self.hud_visible

        if self.is_broadcast:
            # --- Broadcast ---
            if self.hud_visible:
                # Barra de título normal
                self.setWindowFlags(Qt.Window)
                self.showNormal()
                self.position.show()
                self.hud_container.show()
            else:
                # Frameless
                self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
                self.showNormal()
                self.position.hide()
                self.hud_container.hide()

        else:
            # --- Mini-player ---
            if self.hud_visible:
                # Com barra de título (se quiser só fechar, pode usar só Qt.Window)
                self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowStaysOnTopHint)
                self.showNormal()
                self.position.show()
                self.hud_container.show()
            else:
                # Frameless e sempre on-top
                self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
                self.showNormal()
                self.position.hide()
                self.hud_container.hide()

    def toggle_fullscreen(self):
        if not self.is_broadcast:
            return
        if not self.fullscreen:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.showFullScreen()
            self.position.hide()
            self.hud_container.hide()
            self.fullscreen = True
        else:
            self.exit_fullscreen()

    def exit_fullscreen(self):
        if not self.is_broadcast or not self.fullscreen:
            return
        self.setWindowFlags(Qt.Window)
        self.showNormal()
        self.position.show()
        self.showMaximized()
        self.hud_container.show()
        self.fullscreen = False

    # --- Overlay ---
    def show_overlay_message(self, text, visible_duration=2000, fade_duration=500):
        self.overlay_label.setText(text)
        self.overlay_label.adjustSize()
        x = (self.label.width() - self.overlay_label.width()) // 2
        y = (self.label.height() - self.overlay_label.height()) // 2
        self.overlay_label.move(x, y)
        self.overlay_label.show()
        # Fade in
        self.overlay_anim.stop()
        self.overlay_anim.setDuration(fade_duration)
        self.overlay_anim.setStartValue(0.0)
        self.overlay_anim.setEndValue(1.0)
        self.overlay_anim.start()
        # Fade out
        def fade_out():
            self.overlay_anim.stop()
            self.overlay_anim.setDuration(fade_duration)
            self.overlay_anim.setStartValue(1.0)
            self.overlay_anim.setEndValue(0.0)
            self.overlay_anim.start()
        QTimer.singleShot(visible_duration, fade_out)

    # --- Context menu ---
    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #222;
                color: white;
                border: 1px solid #444;
            }
            QMenu::item:selected {
                background-color: #339CFF;
                color: black;
            }
        """)
        app = QApplication.instance()

        # --- HUD toggle ---
        hud_action = QAction("Toggle HUD", self)
        hud_action.setCheckable(True)
        hud_action.setChecked(self.hud_visible)
        hud_action.triggered.connect(self.toggle_hud)
        menu.addAction(hud_action)

        # --- Fullscreen toggle ---
        fs_action = QAction("Fullscreen", self)
        fs_action.setCheckable(True)
        fs_action.setChecked(self.isFullScreen())
        fs_action.triggered.connect(self.toggle_fullscreen)
        menu.addAction(fs_action)

        # --- Loop toggle ---
        loop_action = QAction("Loop", self)
        loop_action.setCheckable(True)
        loop_action.setChecked(app.loop_enabled)
        loop_action.triggered.connect(app.toggle_loop)
        menu.addAction(loop_action)

        # --- Playback speed submenu ---
        speed_menu = menu.addMenu("Playback Speed")
        for rate in [0.5, 1.0, 1.25, 1.5, 2.0]:
            act = QAction(f"{rate:.2f}x", self)
            act.setCheckable(True)
            current = abs(app.mediaplayer.get_rate() - rate) < 0.01
            act.setChecked(current)
            act.triggered.connect(lambda checked, r=rate: app.set_playback_rate(r))
            speed_menu.addAction(act)

        # --- Audio tracks ---
        audio_menu = menu.addMenu("Audio Tracks")
        tracks = app.list_audio_tracks()
        if tracks:
            for tid, desc in tracks:
                text = desc.decode("utf-8", errors="ignore") if isinstance(desc, (bytes, bytearray)) else str(desc)
                action = QAction(text, self)
                action.setCheckable(True)
                if tid == app.mediaplayer.audio_get_track():
                    action.setChecked(True)
                action.triggered.connect(lambda checked, t=tid: app.set_audio_track(t))
                audio_menu.addAction(action)
        else:
            audio_menu.addAction("(No tracks)").setEnabled(False)

        # --- Subtitles ---
        subs_menu = menu.addMenu("Subtitles")
        subs = app.list_subtitles()
        if subs:
            for sid, desc in subs:
                text = desc.decode("utf-8", errors="ignore") if isinstance(desc, (bytes, bytearray)) else str(desc)
                action = QAction(text, self)
                action.setCheckable(True)
                if sid == app.mediaplayer.video_get_spu():
                    action.setChecked(True)
                action.triggered.connect(lambda checked, s=sid: app.set_subtitle(s))
                subs_menu.addAction(action)
        else:
            subs_menu.addAction("(No subtitles)").setEnabled(False)

            # --- Mini-player toggle ---
            mini_action = QAction("Enable Mini-player", self)
            mini_action.setCheckable(True)
            mini_action.setChecked(app.miniplayer_enabled)
            mini_action.triggered.connect(lambda checked: app.toggle_miniplayer(checked))
            menu.addAction(mini_action)

        menu.exec_(self.label.mapToGlobal(pos))

    def update_frame(self):
        app = QApplication.instance()
        buf = app.video_buf
        with QMutexLocker(buf.mutex):
            img = QImage(buf.buf, buf.width, buf.height, buf.stride, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(img.copy())
        self.label.setPixmap(
            pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def closeEvent(self, event):
        app = QApplication.instance()
        if self.is_broadcast:
            # Fechou a janela principal → fecha o mini se existir e encerra o app
            if getattr(app, "mini", None):
                app.mini.close()
                app.mini = None
            app.quit()
        else:
            # Fechou só o mini-player → NÃO encerra o app
            event.accept()
            # opcional: atualiza flag global e persiste


# ---------------- App ----------------
class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.settings = QSettings("Nexa Player", "PIP Player")
        self.video_path = None
        self.video_duration_ms = None
        self.thumbnail_worker = None
        self.thumbnail_cache = {}
        # lê configuração salva (default = True)
        self.miniplayer_enabled = self.settings.value("miniplayer_enabled", True, type=bool)
        self.instance = vlc.Instance("--no-osd --no-video-title-show")
        self.mediaplayer = self.instance.media_player_new()
        # cria janelas
        if self.miniplayer_enabled:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
        else:
            self.mini = None
        # Event: loop ao terminar
        em = self.mediaplayer.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end_reached)

        # Buffer e callbacks (compartilhado para broadcast + mini)
        self.video_buf = VideoBuffer()
        LockCB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        UnlockCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        DisplayCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

        def _lock(opaque, planes): return self.video_buf.lock(opaque, planes)
        def _unlock(opaque, picture, planes): self.video_buf.unlock(opaque, picture, planes)
        def _display(opaque, picture): self.video_buf.display(opaque, picture)

        # Guarde as referências para evitar GC
        self._lock_cb = LockCB(_lock)
        self._unlock_cb = UnlockCB(_unlock)
        self._display_cb = DisplayCB(_display)

        self.mediaplayer.video_set_callbacks(self._lock_cb, self._unlock_cb, self._display_cb, None)
        self.mediaplayer.video_set_format("RGBA", self.video_buf.width, self.video_buf.height, self.video_buf.stride)

        # Janelas
        self.broadcast = PlayerWindow("Nexa Player", is_broadcast=True)
        self.broadcast.show()
        self.broadcast.destroyed.connect(self._on_broadcast_closed)


        # Timer de UI
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(200)
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start()

        self.loop_enabled = False

    def _on_broadcast_closed(self):
        if self.mini is not None:
            # salva estado real antes de fechar
            still_visible = self.mini.isVisible()
            self.settings.setValue("miniplayer_enabled", still_visible)
            self.mini.close()
            self.mini = None

    def toggle_miniplayer(self, enabled: bool):
        self.miniplayer_enabled = enabled
        self.settings.setValue("miniplayer_enabled", enabled)

        if enabled and self.mini is None:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
        elif not enabled and self.mini is not None:
            self.mini.close()
            self.mini = None

    # -------- Ações globais --------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.broadcast, "Open Video", "",
            "Videos (*.mp4 *.mkv *.avi *.mov *.webm);;Todos (*.*)"
        )
        if path:
            self.open_path(path)

    def set_playback_rate(self, rate: float):
        """Define a taxa de reprodução diretamente."""
        self.mediaplayer.set_rate(rate)
        self.broadcast.show_overlay_message(f"Speed: {rate:.2f}x")



    def open_path(self, path: str):
        try:
            media = self.instance.media_new(path)
            media.add_option(":input-repeat=-1")  # mantém como estava antes
            self.mediaplayer.set_media(media)
            self.mediaplayer.play()

            # Atualiza títulos o quanto antes (é leve)
            mrl = media.get_mrl()
            self.update_titles(mrl)
        except Exception as e:
            print("Erro ao preparar/rodar mídia:", e)

        # Sempre salva o caminho (mesmo se VLC falhar)
        self.video_path = path
        if self.thumbnail_worker:
            self.thumbnail_worker.stop()

        self.thumbnail_cache.clear()
        self.thumbnail_worker = ThumbnailWorker(path, interval_s=5)
        self.thumbnail_worker.thumbnail_ready.connect(self._store_thumbnail)
        self.thumbnail_worker.start()

        # Sonda duração com ffmpeg, sem deixar travar a função
        try:
            self.video_duration_ms = probe_duration_ms(path)
            # Se quiser, loga para confirmar
            print("Duração (ms):", self.video_duration_ms)
        except Exception as e:
            print("Erro ao sondar duração FFmpeg:", e)
            self.video_duration_ms = None

        # Ícones (com segurança)
        if getattr(self, "broadcast", None):
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
        if getattr(self, "mini", None):
            self.mini.play_btn.setIcon(self.mini.icon_pause)

    def _store_thumbnail(self, time_ms: int, pix: QPixmap):
        self.thumbnail_cache[time_ms] = pix

    def _on_end_reached(self, event):
        if getattr(self, "loop_enabled", False):
            # agenda no thread principal do Qt
            QTimer.singleShot(0, self._restart_media)

    def _restart_media(self):
        # Reinicia corretamente o estado
        self.mediaplayer.stop()
        self.mediaplayer.set_time(0)
        self.mediaplayer.play()

    def play_pause(self):
        state = self.mediaplayer.get_state()
        if state == vlc.State.Ended:
            # Reinicia do zero se o vídeo terminou
            self.mediaplayer.stop()
            self.mediaplayer.set_time(0)
            self.mediaplayer.play()
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
            self.mini.play_btn.setIcon(self.mini.icon_pause)
        elif self.mediaplayer.is_playing():
            self.mediaplayer.pause()
            self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
            self.mini.play_btn.setIcon(self.mini.icon_play)
        else:
            self.mediaplayer.play()
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
            self.mini.play_btn.setIcon(self.mini.icon_pause)

    def stop(self):
        self.mediaplayer.stop()
        for win in (self.broadcast, self.mini):
            win.position.setValue(0)
            win.time_label.setText("--:-- / --:--")
        # volta ícones para play
        self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
        self.mini.play_btn.setIcon(self.mini.icon_play)

    def set_position(self, val):
        length = self.mediaplayer.get_length()
        if length > 0:
            self.mediaplayer.set_position(val / 1000.0)

    def seek(self, delta_ms):
        t = max(0, self.mediaplayer.get_time() + delta_ms)
        self.mediaplayer.set_time(t)

    def set_volume(self, val):
        self.mediaplayer.audio_set_volume(val)

    def adjust_volume(self, delta):
        v = self.mediaplayer.audio_get_volume()
        self.set_volume(max(0, min(125, v + delta)))

    # --- Novos recursos ---
    def adjust_rate(self, delta):
        rate = self.mediaplayer.get_rate()
        new_rate = max(0.5, min(2.0, rate + delta))
        self.mediaplayer.set_rate(new_rate)
        self.broadcast.show_overlay_message(f"Speed: {new_rate:.2f}x")

    def toggle_loop(self):
        self.loop_enabled = not self.loop_enabled
        self.broadcast.show_overlay_message(f"Loop: {'On' if self.loop_enabled else 'Off'}")

    def list_audio_tracks(self):
        return self.mediaplayer.audio_get_track_description() or []

    def set_audio_track(self, track_id):
        self.mediaplayer.audio_set_track(track_id)
        desc = dict(self.list_audio_tracks()).get(track_id, f"Track {track_id}")
        self.broadcast.show_overlay_message(f"Audio: {desc}")

    def cycle_audio_track(self):
        tracks = self.list_audio_tracks()
        if not tracks: return
        current = self.mediaplayer.audio_get_track()
        ids = [tid for tid, _ in tracks]
        next_id = ids[(ids.index(current)+1) % len(ids)] if current in ids else ids[0]
        self.set_audio_track(next_id)

    def list_subtitles(self):
        return self.mediaplayer.video_get_spu_description() or []

    def set_subtitle(self, sub_id):
        self.mediaplayer.video_set_spu(sub_id)
        desc = dict(self.list_subtitles()).get(sub_id, f"Subtitle {sub_id}")
        self.broadcast.show_overlay_message(f"Subtitles: {desc}")

    def cycle_subtitle_track(self):
        subs = self.list_subtitles()
        if not subs: return
        current = self.mediaplayer.video_get_spu()
        ids = [sid for sid, _ in subs]
        next_id = ids[(ids.index(current)+1) % len(ids)] if current in ids else ids[0]
        self.set_subtitle(next_id)

    def update_ui(self):
        length = self.mediaplayer.get_length()
        time_ = self.mediaplayer.get_time()

        if length > 0:
            pos = int((time_ / length) * 1000)

            for win in (self.broadcast, self.mini):
                if not win:  # ignora se None
                    continue
                if not hasattr(win, "position") or not hasattr(win, "time_label"):
                    continue

                win.position.blockSignals(True)
                win.position.setValue(pos)
                win.position.blockSignals(False)
                win.time_label.setText(f"{ms_to_minsec(time_)} / {ms_to_minsec(length)}")

        # --- Loop robusto ---
        if self.loop_enabled and length > 0:
            state = self.mediaplayer.get_state()
            if state == vlc.State.Ended:
                # Reinicia do zero sempre que terminar
                self.mediaplayer.stop()
                self.mediaplayer.set_time(0)
                self.mediaplayer.play()
            elif time_ >= length - 200:
                # Margem de segurança para não deixar chegar em Ended
                self.mediaplayer.set_time(0)
                self.mediaplayer.play()

        # --- Corrigir ícone quando termina sem loop ---
        if not self.loop_enabled and self.mediaplayer.get_state() == vlc.State.Ended:
            if self.broadcast:
                self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
            if self.mini:
                self.mini.play_btn.setIcon(self.mini.icon_play)

    def update_titles(self, mrl: str):
        filename = clean_filename_from_mrl(mrl)
        self.broadcast.setWindowTitle(f"{filename} - Nexa Player")
        self.mini.setWindowTitle(f"{filename} - Nexa Player - PIP")

# --- Main ---
if __name__ == "__main__":
    app = App(sys.argv)
    sys.exit(app.exec())
