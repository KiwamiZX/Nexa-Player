import sys, os, urllib.parse, ctypes
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QLabel, QFileDialog, QSizePolicy, QStyle
)
from PySide6.QtCore import Qt, QTimer, QMutex, QMutexLocker, QSize
from PySide6.QtGui import QAction, QKeySequence, QImage, QPixmap
from PySide6.QtGui import QIcon
import vlc
import resources_rc

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

# ---------------- Player window ----------------
class PlayerWindow(QWidget):
    def __init__(self, title, is_broadcast=False):
        super().__init__()
        self.is_broadcast = is_broadcast
        self.setWindowIcon(QIcon(":icons/nexaplayer.png"))
        self.fullscreen = False
        self.hud_visible = True


        if is_broadcast:
            self.setWindowFlags(Qt.Window)
        else:
            self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowStaysOnTopHint)

        self.setWindowTitle(title)
        self.resize(640, 360)
        self.setStyleSheet("background-color: black;")

        # habilita drag & drop
        self.setAcceptDrops(True)

        # Vídeo
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background-color: black; border: none; margin: 0; padding: 0;")
        self.label.setMinimumSize(100, 60)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        # HUD
        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 1000)

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
                    border: 1px solid #0f0;
                    height: 6px;
                    background: #222;
                }
                QSlider::handle:horizontal {
                    background: #0f0;
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

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self.update_frame)
        self.frame_timer.start()

        self.play_btn.clicked.connect(lambda: QApplication.instance().play_pause())
        self.stop_btn.clicked.connect(lambda: QApplication.instance().stop())
        self.open_btn.clicked.connect(lambda: QApplication.instance().open_file())
        self.position.sliderMoved.connect(lambda v: QApplication.instance().set_position(v))
        if self.volume_slider:
            self.volume_slider.valueChanged.connect(lambda v: QApplication.instance().set_volume(v))
        if self.full_btn:
            self.full_btn.clicked.connect(self.toggle_fullscreen)

        self.add_shortcuts()

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

    def toggle_hud(self):
        self.hud_visible = not self.hud_visible
        if self.hud_visible:
            # volta barra de título (só minimizar) + HUD
            self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowStaysOnTopHint)
            self.showNormal()  # reaplica os flags
            self.position.show()
            self.hud_container.show()
        else:
            # remove barra de título (frameless) + esconde HUD
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.showNormal()  # reaplica os flags
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
        self.hud_container.show()
        self.fullscreen = False

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
        QApplication.instance().quit()

# ---------------- App ----------------
class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)

        self.instance = vlc.Instance()
        self.mediaplayer = self.instance.media_player_new()

        # Buffer e callbacks
        self.video_buf = VideoBuffer()
        LockCB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        UnlockCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        DisplayCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

        def _lock(opaque, planes): return self.video_buf.lock(opaque, planes)
        def _unlock(opaque, picture, planes): self.video_buf.unlock(opaque, picture, planes)
        def _display(opaque, picture): self.video_buf.display(opaque, picture)

        self._lock_cb = LockCB(_lock)
        self._unlock_cb = UnlockCB(_unlock)
        self._display_cb = DisplayCB(_display)

        self.mediaplayer.video_set_callbacks(self._lock_cb, self._unlock_cb, self._display_cb, None)
        self.mediaplayer.video_set_format("RGBA", self.video_buf.width, self.video_buf.height, self.video_buf.stride)

        # Janelas
        self.broadcast = PlayerWindow("Nexa Player", is_broadcast=True)
        self.broadcast.show()

        self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
        self.mini.resize(320, 180)
        self.mini.show()

        # Timer de UI
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(200)
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start()

    # -------- Ações globais --------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.broadcast, "Open Video", "",
            "Videos (*.mp4 *.mkv *.avi *.mov *.webm);;Todos (*.*)"
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str):
        media = self.instance.media_new(path)
        self.mediaplayer.set_media(media)
        self.mediaplayer.play()
        self.update_titles(media.get_mrl())

    def play_pause(self):
        if self.mediaplayer.is_playing():
            self.mediaplayer.pause()
            # troca ícone para "play"
            self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
            self.mini.play_btn.setIcon(self.mini.icon_play)
        else:
            self.mediaplayer.play()
            # troca ícone para "pause"
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
            self.mini.play_btn.setIcon(self.mini.icon_pause)

    def stop(self):
        self.mediaplayer.stop()
        for win in (self.broadcast, self.mini):
            win.position.setValue(0)
            win.time_label.setText("--:-- / --:--")

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

    def update_ui(self):
        length = self.mediaplayer.get_length()
        time_ = self.mediaplayer.get_time()
        if length > 0:
            pos = int((time_ / length) * 1000)
            for win in (self.broadcast, self.mini):
                win.position.blockSignals(True)
                win.position.setValue(pos)
                win.position.blockSignals(False)
                win.time_label.setText(f"{ms_to_minsec(time_)} / {ms_to_minsec(length)}")

    def update_titles(self, mrl: str):
        filename = clean_filename_from_mrl(mrl)
        self.broadcast.setWindowTitle(f"{filename} - Broadcast")
        self.mini.setWindowTitle(f"{filename} - Miniplayer")

# --- Main ---
if __name__ == "__main__":
    app = App(sys.argv)
    sys.exit(app.exec())
