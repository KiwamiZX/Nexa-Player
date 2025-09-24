import sys, os, urllib.parse, ctypes, tempfile
import cv2
import numpy as np
import time
import ctypes
from ctypes.wintypes import MSG
from PySide6.QtWidgets import (
    QApplication, QListWidget, QWidget, QLineEdit, QVBoxLayout,QAbstractItemView, QHBoxLayout, QTreeView, QPushButton, QSplashScreen,
    QSlider, QLabel, QFileDialog, QSizePolicy, QStyle, QMenu, QGraphicsOpacityEffect, QToolTip
)
from PySide6.QtCore import QThread, QDir, Signal, Qt, QTimer, QMetaObject, QMutex, QMutexLocker, QSize, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QAction, QKeySequence, QImage, QPixmap, QIcon, QCursor, QPainter, QColor, QFont
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtCore import QSettings, QSize
from PySide6.QtWidgets import QDialog, QFileSystemModel



import vlc
import resources_rc




def get_video_duration(path: str) -> int:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if fps > 0:
        return int((frame_count / fps) * 1000)  # duração em milissegundos
    return 0
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
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            print("Erro ao abrir vídeo:", self.video_path)
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        dur_ms = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps * 1000

        t = 0
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
                pix = QPixmap.fromImage(qimg)
                self.thumbnail_ready.emit(int(t), pix)
            t += self.interval_s * 1000

        cap.release()

    def stop(self):
        self._running = False
        self.wait()
# ---------------- Video buffer ----------------
class FileLoader(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load File - NexaPlayer")
        self.resize(800, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        # pega último diretório salvo
        app = QApplication.instance()
        last_dir = None
        if hasattr(app, "settings"):
            last_dir = app.settings.value("last_dir", QDir.homePath(), type=str)
        if not last_dir:
            last_dir = QDir.homePath()

        # barra de caminho
        self.path_edit = QLineEdit(last_dir)
        layout.addWidget(self.path_edit)

        # modelo de arquivos
        self.model = QFileSystemModel()
        self.model.setRootPath(QDir.rootPath())
        self.model.setNameFilters(["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm"])
        self.model.setNameFilterDisables(False)

        # árvore de arquivos
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(last_dir))
        self.view.doubleClicked.connect(self.on_double_click)
        self.view.setAlternatingRowColors(True)
        self.view.setIconSize(QSize(20, 20))
        self.view.setColumnWidth(0, 300)
        self.view.setColumnWidth(1, 80)
        self.view.setColumnWidth(2, 100)
        self.view.setColumnWidth(3, 150)
        layout.addWidget(self.view)

        # botões
        btn_layout = QHBoxLayout()
        self.back_btn = QPushButton("Return")
        btn_layout.insertWidget(0, self.back_btn)  # coloca antes do Abrir
        self.back_btn.clicked.connect(self.go_back)
        self.open_btn = QPushButton("Open")
        self.cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(self.open_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.open_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        self.selected_file = None

        # aplica CSS dark mode
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #f0f0f0;
                font-family: Segoe UI, sans-serif;
                font-size: 11pt;
            }
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #555;
                padding: 4px;
                color: #ffffff;
            }
            QTreeView {
                background-color: #2d2d2d;
                alternate-background-color: #3a3a3a;
                color: #f0f0f0;
                selection-background-color: #0078d7;
                selection-color: #ffffff;
                border: none;
            }
            QHeaderView::section {
                background-color: #333;
                color: #ccc;
                padding: 4px;
                border: none;
            }
            QPushButton {
                background-color: #0078d7;
                color: white;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #2899f5;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
        """)

    def go_back(self):
        current_path = self.path_edit.text()
        parent = os.path.dirname(current_path)
        if os.path.exists(parent):
            self.view.setRootIndex(self.model.index(parent))
            self.path_edit.setText(parent)
            # salva último diretório
            self._save_last_dir(parent)

    def on_double_click(self, index):
        path = self.model.filePath(index)
        if QDir(path).exists():
            self.view.setRootIndex(self.model.index(path))
            self.path_edit.returnPressed.connect(self.change_dir)
        else:
            self.selected_file = path
            self._save_last_dir(path)
            self.accept()

    def accept(self):
        index = self.view.currentIndex()
        if index.isValid():
            path = self.model.filePath(index)
            if os.path.isdir(path):
                self._save_last_dir(path)
            else:
                self._save_last_dir(os.path.dirname(path))
                self.selected_file = path
        super().accept()

    def change_dir(self):
        path = self.path_edit.text()
        if os.path.isdir(path):
            self.view.setRootIndex(self.model.index(path))
            self._save_last_dir(path)

    def get_selected_file(self):
        index = self.view.currentIndex()
        if index.isValid():
            return self.model.filePath(index)
        return self.selected_file

    def _save_last_dir(self, path):
        app = QApplication.instance()
        if hasattr(app, "settings"):
            app.settings.setValue("last_dir", path)

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


import subprocess
import sys

def get_frame_at(video_path: str, ms: int, width: int = 160, height: int = 90) -> QPixmap | None:
    try:
        time_sec = ms / 1000.0
        args = [
            "ffmpeg",
            "-ss", str(time_sec),
            "-i", video_path,
            "-vframes", "1",
            "-vf", f"scale={width}:{height}",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-loglevel", "quiet",
            "pipe:1"
        ]

        # suprimir janela no Windows
        startupinfo = None
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo
        )

        raw = proc.stdout.read(width * height * 3)
        proc.stdout.close()
        proc.wait()

        if not raw:
            return None

        frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3))
        qimg = QImage(frame.data, width, height, 3 * width, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)

    except Exception as e:
        print("Erro FFmpeg:", e)
        return None


from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
    QAbstractItemView, QFileDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


class PlaylistDialog(QDialog):
    def __init__(self, playlist, play_callback, parent=None):
        super().__init__(parent)
        self.play_callback = play_callback
        self._started_playback = False  # tracks if Play was pressed or double-clicked
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Playlist")
        self.setMinimumSize(420, 320)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QListWidget {
                background-color: #2b2b2b;
                color: white;
                border: 1px solid #444;
                padding: 4px;
            }
            QListWidget::item:selected { background-color: #00bfff; color: black; }
            QPushButton {
                background-color: #333;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 12px;
                color: white;
            }
            QPushButton:hover { background-color: #444; }
        """)

        layout = QVBoxLayout(self)

        # List (preload existing files only)
        self.list_widget = QListWidget()
        for p in playlist:
            if isinstance(p, str) and os.path.exists(p):
                self.list_widget.addItem(p)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(self.list_widget)

        # Buttons
        btns = QHBoxLayout()
        self.btn_add = QPushButton("➕ Add")
        self.btn_play = QPushButton("▶ Play")
        self.btn_remove = QPushButton("🗑 Remove")
        self.btn_close = QPushButton("✖ Close")

        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_play)
        btns.addWidget(self.btn_remove)
        btns.addStretch()
        btns.addWidget(self.btn_close)
        layout.addLayout(btns)

        # Connections
        self.btn_close.clicked.connect(self.accept)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_play.clicked.connect(lambda: self.play_selected())
        self.list_widget.itemDoubleClicked.connect(self.play_selected)
        self.btn_add.clicked.connect(self.add_with_custom_loader)

    def add_with_custom_loader(self):
        # Use your custom FileLoader
        app = QApplication.instance()
        parent = getattr(app, "broadcast", None)
        dlg = FileLoader(parent or self)
        if dlg.exec() == QDialog.Accepted:
            file = dlg.get_selected_file()
            if file and os.path.exists(file):
                existing = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
                if file not in existing:
                    before = self.list_widget.count()
                    self.list_widget.addItem(file)
                    # auto-select first added item
                    if before == 0:
                        self.list_widget.setCurrentRow(0)

    def remove_selected(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)
            # keep selection valid
            if self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(min(row, self.list_widget.count() - 1))

    def play_selected(self, item=None):
        if item is None:
            item = self.list_widget.currentItem()
        if not item:
            return

        path = item.text()
        if not (path and os.path.exists(path)):
            return

        app = QApplication.instance()
        app.playlist = self.get_playlist()

        self._started_playback = True
        self.play_callback(path)  # <-- aqui em vez de open_path
        self.accept()

    def accept(self):
        # If user closes without pressing Play, auto-play the first item IF nothing is playing
        app = QApplication.instance()
        is_playing = False
        try:
            is_playing = app.mediaplayer.is_playing()
        except Exception:
            is_playing = False

        if (not self._started_playback) and (self.list_widget.count() > 0) and (not is_playing):
            first = self.list_widget.item(0).text()
            if first and os.path.exists(first):
                app.playlist = self.get_playlist()
                self.play_callback(first)

        super().accept()

    def get_playlist(self):
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]







class SeekSlider(QSlider):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)

        # Label flutuante
        self.preview_label = QLabel(parent)
        self.preview_label.setWindowFlags(Qt.ToolTip)
        self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.preview_label.hide()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            x = event.pos().x()
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), x, self.width()
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
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
        super().mouseMoveEvent(event)

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
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setScaledContents(False)  # não esticar automaticamente

        # Splash inicial
        splash = QPixmap(":/icons/splash.png")
        self.label.setPixmap(
            splash.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
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
        self.icon_prev = QIcon(":/icons/prev.png")
        self.icon_next = QIcon(":/icons/next.png")
        # Botão Previous
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(self.icon_prev)
        self.prev_btn.setIconSize(QSize(24, 24))
        self.prev_btn.setStyleSheet(btn_style)

        # Botão Next
        self.next_btn = QPushButton()
        self.next_btn.setIcon(self.icon_next)
        self.next_btn.setIconSize(QSize(24, 24))
        self.next_btn.setStyleSheet(btn_style)


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

        self.playlist_btn = QPushButton()
        self.playlist_btn.setStyleSheet(btn_style)
        self.playlist_btn.setIcon(QIcon(":/icons/playlist.png"))
        self.playlist_btn.setIconSize(QSize(24, 24))
        self.playlist_btn.clicked.connect(lambda: QApplication.instance().show_playlist())



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
        self.controls_row.addWidget(self.prev_btn)
        self.controls_row.addWidget(self.stop_btn)
        self.controls_row.addWidget(self.next_btn)
        if self.full_btn: self.controls_row.addWidget(self.full_btn)
        self.controls_row.addWidget(self.playlist_btn)
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

        self.prev_btn.clicked.connect(lambda: QApplication.instance().previous_track())
        self.next_btn.clicked.connect(lambda: QApplication.instance().next_track())
        # Atualiza posição no App quando o usuário arrasta
        self.position.sliderMoved.connect(lambda v: QApplication.instance().set_position(v))
        self.position.sliderReleased.connect(lambda: QApplication.instance().set_position(self.position.value()))
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

    def set_position_from_slider(self, value=None):
        if self.has_media:
            length = self.mediaplayer.get_length()
            if length > 0:
                if value is None:  # caso venha do sliderReleased
                    value = self.position_slider.value()
                new_time = int((value / 1000) * length)
                self.mediaplayer.set_time(new_time)

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
        if not isinstance(app, App):
            print("ERRO: QApplication.instance() não é App!")
            return
        menu.addAction("Open File", lambda: QApplication.instance().open_file())
        menu.addAction("Play/Pause", lambda: QApplication.instance().play_pause())
        menu.addAction("Stop", lambda: QApplication.instance().stop())
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

       # -- PLAYLIST ---

        playlist_menu = menu.addMenu("Playlist")
        app = QApplication.instance()
        if isinstance(app, App):
            playlist_menu.addAction("List", lambda: QApplication.instance().show_playlist())
            playlist_menu.addAction("Next", app.next_track)
            playlist_menu.addAction("Previous", app.previous_track)
            playlist_menu.addAction("Loop Playlist", app.toggle_loop_playlist)



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



        menu.exec(self.label.mapToGlobal(pos))

    def update_frame(self):
        app = QApplication.instance()
        if not app.has_media:
            return  # 👉 não sobrescreve o splash se não houver vídeo

        buf = app.video_buf
        with QMutexLocker(buf.mutex):
            img = QImage(buf.buf, buf.width, buf.height, buf.stride, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(img.copy())
            self.label.setPixmap(
                pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        app = QApplication.instance()
        if not getattr(app, "has_media", False):
            splash = QPixmap(":/icons/splash.png")
            if not splash.isNull():
                # enche o espaço: mantém proporção, pode cortar bordas
                target = self.label.contentsRect().size()
                self.label.setPixmap(splash.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

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
    media_finished = Signal()
    def __init__(self, argv):
        super().__init__(argv)
        self.settings = QSettings("Nexa Player", "PIP Player")
        self.video_path = None
        self.has_media = False
        self.video_duration_ms = None
        self.thumbnail_worker = None
        self.playlist = []  # lista de arquivos
        self.current_index = -1  # índice atual
        self.loop_playlist = False  # se quiser loopar a lista inteira
        self.media_finished.connect(self._play_next_safe)
        self.thumbnail_cache = {}
        # lê configuração salva (default = True)
        self.miniplayer_enabled = self.settings.value("miniplayer_enabled", True, type=bool)
        self.instance = vlc.Instance("--no-osd --no-video-title-show")
        self.mediaplayer = self.instance.media_player_new()
        # conecta evento de fim de mídia
        event_manager = self.mediaplayer.event_manager()
        event_manager.event_attach(vlc.EventType.MediaPlayerPlaying, self._on_media_playing)
        event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_media_end)
        # cria janelas
        if self.miniplayer_enabled:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
        else:
            self.mini = None
        # Event: loop ao terminar


        saved_ratio = self.settings.value("aspect_ratio", "", type=str)
        if saved_ratio:
            self.mediaplayer.video_set_aspect_ratio(saved_ratio.encode("utf-8"))
            self.mediaplayer.video_set_scale(0)

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

    def play_from_playlist(self, path: str):
        if not path or not os.path.exists(path):
            return

        try:
            self.current_index = self.playlist.index(path)
        except ValueError:
            self.playlist.append(path)
            self.current_index = len(self.playlist) - 1

        self.open_path(path)

    def show_playlist(self):
        dlg = PlaylistDialog(self.playlist, play_callback=self.play_from_playlist, parent=self.broadcast)
        if dlg.exec():
            updated = dlg.get_playlist()
            self.playlist = [p for p in updated if os.path.exists(p)]
            if not self.playlist:
                self.current_index = -1
            elif self.current_index < 0 or self.current_index >= len(self.playlist):
                self.current_index = 0

    def _on_media_playing(self, event):
        try:
            # Ajustes de escala e proporção só quando o vídeo está pronto
            self.mediaplayer.video_set_scale(0)  # auto scale
            self.mediaplayer.video_set_aspect_ratio(None)

            # Descobre a resolução real do vídeo
            w = self.mediaplayer.video_get_width()
            h = self.mediaplayer.video_get_height()
            if not w or not h:
                # Fallback, se o VLC ainda não reportou
                w, h = self.mediaplayer.video_get_size(0) or (640, 360)

            # Recria o buffer com a resolução nativa e reaplica o formato
            self.video_buf = VideoBuffer(w, h)
            self.mediaplayer.video_set_format("RGBA", w, h, self.video_buf.stride)

            print(f"Vídeo iniciado: {w}x{h} — formato aplicado.")

        except Exception as e:
            print("Erro em _on_media_playing:", e)

    def add_to_playlist(self, path: str):
        """Adiciona um arquivo à playlist"""
        if os.path.exists(path):
            self.playlist.append(path)
            if self.current_index == -1:
                self.current_index = 0

    def load_playlist(self, paths: list[str]):
        """Carrega uma lista inteira de arquivos"""
        self.playlist = [p for p in paths if os.path.exists(p)]
        self.current_index = 0 if self.playlist else -1

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
                return  # não tenta tocar além do fim

        self.play_current()

    def previous_track(self):
        """Volta para o item anterior"""
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
            self.broadcast.show_overlay_message(
                f"Loop Playlist: {'On' if self.loop_playlist else 'Off'}"
            )

    def _on_media_end(self, event):
        print(">>> Evento: fim do vídeo detectado pelo VLC")
        if self.loop_enabled:
            print(">>> Loop simples ativo, reiniciando o mesmo vídeo")
            QTimer.singleShot(0, self._restart_media)
        else:
            print(">>> Tentando avançar para o próximo da playlist")
            self.media_finished.emit()

    def _play_next_safe(self):
        if not self.playlist:
            print(">>> Playlist vazia, nada a tocar")
            self.current_index = -1
            return

        next_index = self.current_index + 1
        if next_index >= len(self.playlist):
            if self.loop_playlist:
                print(">>> Chegou ao fim, loop_playlist ativo → voltando ao primeiro")
                next_index = 0
            else:
                print(">>> Chegou ao fim da playlist e loop_playlist está desligado → parando")
                return

        self.current_index = next_index
        path = self.playlist[self.current_index]
        print(f">>> Tocando próximo: {path}")

        # força o player a parar antes de carregar o próximo
        self.mediaplayer.stop()
        self.open_path(path)


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

            # se já houver vídeo carregado, atualiza o título
            if self.video_path:
                self.update_titles(self.video_path)
        elif not enabled and self.mini is not None:
            self.mini.close()
            self.mini = None

    # -------- Ações globais --------
    def open_file(self):
        dlg = FileLoader(self.broadcast)  # usa o diálogo customizado
        if dlg.exec() == QDialog.Accepted:
            file = dlg.get_selected_file()
            if file:
                self.open_path(file)

    def set_playback_rate(self, rate: float):
        """Define a taxa de reprodução diretamente."""
        self.mediaplayer.set_rate(rate)
        self.broadcast.show_overlay_message(f"Speed: {rate:.2f}x")

    def open_path(self, path: str):
        # Para o player atual, se estiver tocando
        if self.mediaplayer.is_playing():
            self.mediaplayer.stop()

        # Define a nova mídia
        media = self.instance.media_new(path)
        self.mediaplayer.set_media(media)

        # Dá play
        QTimer.singleShot(0, self.mediaplayer.play)

        # Atualiza estado interno
        self.video_path = path
        self.video_duration_ms = get_video_duration(path)
        self.has_media = True

        # Limpa cache de miniaturas
        self.thumbnail_cache = {}

        # Para worker anterior, se existir
        if hasattr(self, "thumbnail_worker") and self.thumbnail_worker:
            self.thumbnail_worker.stop()

        # Inicia novo worker de thumbs
        self.thumbnail_worker = ThumbnailWorker(path, interval_s=5)
        self.thumbnail_worker.thumbnail_ready.connect(self._store_thumbnail)
        self.thumbnail_worker.start()

        # Atualiza títulos
        self.update_titles(path)

    def _apply_native_size(self):
        try:
            w, h = self.mediaplayer.video_get_size(0)
        except:
            w, h = (640, 360)
        if not w or not h:
            w, h = (640, 360)

        self.video_buf = VideoBuffer(w, h)
        self.mediaplayer.video_set_format("RGBA", w, h, self.video_buf.stride)

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
            # ícones
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
            if self.mini:
                self.mini.play_btn.setIcon(self.mini.icon_pause)

        elif self.mediaplayer.is_playing():
            self.mediaplayer.pause()
            self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
            if self.mini:
                self.mini.play_btn.setIcon(self.mini.icon_play)

        else:
            self.mediaplayer.play()
            self.broadcast.play_btn.setIcon(self.broadcast.icon_pause)
            if self.mini:
                self.mini.play_btn.setIcon(self.mini.icon_pause)

    def stop(self):
        self.mediaplayer.stop()

        for win in (self.broadcast, self.mini):
            if not win:
                continue
            win.position.setValue(0)
            win.time_label.setText("--:-- / --:--")

        # volta ícones para play
        self.broadcast.play_btn.setIcon(self.broadcast.icon_play)
        if self.mini:
            self.mini.play_btn.setIcon(self.mini.icon_play)

    def set_position(self, value: int):
        """Recebe o valor do slider (0–1000) e ajusta o tempo do vídeo."""
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

    # App.set_aspect_ratio
    def set_aspect_ratio(self, ratio: str | None):
        # aplicar enquanto está reproduzindo
        state = self.mediaplayer.get_state()

        if ratio:
            self.mediaplayer.video_set_aspect_ratio(ratio.encode("utf-8"))
            self.mediaplayer.video_set_scale(0)  # auto scale respeita proporção
            self.settings.setValue("aspect_ratio", ratio)
            self.broadcast.show_overlay_message(f"Aspect Ratio: {ratio}")
        else:
            # auto: proporção original, ajusta à janela sem esticar
            self.mediaplayer.video_set_aspect_ratio(None)
            self.mediaplayer.video_set_scale(0)
            self.settings.setValue("aspect_ratio", "")
            self.broadcast.show_overlay_message("Aspect Ratio: Auto")

        # truque: força o reapply sem parar playback
        if state in (vlc.State.Playing, vlc.State.Paused):
            self.mediaplayer.pause()
            QTimer.singleShot(50, self.mediaplayer.play)  # pequeno toggle para aplicar

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
        filename = os.path.basename(mrl)
        name_only, _ = os.path.splitext(filename)

        # atualiza janela principal
        self.broadcast.setWindowTitle(f"{name_only} - Nexa Player")

        # atualiza miniplayer se existir
        if self.mini is not None:
            self.mini.setWindowTitle(f"{name_only} - Nexa Player - PIP")

# --- Main ---
if __name__ == "__main__":
    app = App(sys.argv)

    splash = QSplashScreen(QPixmap("icons/splash.png"))

    # Se o usuário arrastou um arquivo no ícone ou usou "Abrir com"
    if len(sys.argv) > 1:
        path = sys.argv[1]
        app.open_path(path)

    sys.exit(app.exec())