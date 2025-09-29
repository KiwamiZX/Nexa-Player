import sys, os, urllib.parse, ctypes, tempfile
import cv2
import numpy as np
import time
import ctypes
from ctypes.wintypes import MSG

from PySide6.QtWidgets import (
    QApplication, QListWidget, QListWidgetItem, QWidget, QLineEdit, QVBoxLayout, QAbstractItemView, QHBoxLayout,
    QTreeView, QPushButton, QSplashScreen, QSlider, QLabel, QFileDialog, QSizePolicy, QStyle, QMenu,
    QGraphicsOpacityEffect, QToolTip, QDialog, QFileSystemModel, QListView
)
from PySide6.QtCore import (
    QThread, QDir, Signal, Qt, QTimer, QMetaObject, QMutex, QMutexLocker, QSize, QPropertyAnimation,
    QEasingCurve, QPoint, QSettings
)
from PySide6.QtGui import (
    QAction, QKeySequence, QImage, QPixmap, QIcon, QCursor, QPainter, QColor, QFont, QGuiApplication, QMouseEvent
)

import vlc
import resources_rc

# ---------------- Helpers ----------------

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

# ---------------- Thumbnails ----------------

class ThumbnailWorker(QThread):
    thumbnail_ready = Signal(int, QPixmap)  # tempo_ms, pixmap

    def __init__(self, video_path: str, interval_s: int = 5, width=96, height=54):
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

import subprocess
import sys

def get_frame_at(video_path: str, ms: int, width: int = 96, height: int = 54) -> QPixmap | None:
    try:
        time_sec = ms / 1000.0
        args = [
            "ffmpeg", "-ss", str(time_sec), "-i", video_path,
            "-vframes", "1", "-vf", f"scale={width}:{height}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-loglevel", "quiet", "pipe:1"
        ]
        startupinfo = None
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, startupinfo=startupinfo
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

# ---------------- File loader (updated) ----------------

class FileLoader(QDialog):
    # Thumbnails grid/list with toggle
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load File - NexaPlayer")
        self.resize(830, 560)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._thumb_cache: dict[str, QIcon] = {}
        self._thumb_thread: ThumbnailListWorker | None = None

        layout = QVBoxLayout(self)

        app = QApplication.instance()
        last_dir = None
        if hasattr(app, "settings"):
            last_dir = app.settings.value("last_dir", QDir.homePath(), type=str)
        if not last_dir:
            last_dir = QDir.homePath()

        # path bar
        self.path_edit = QLineEdit(last_dir)
        layout.addWidget(self.path_edit)

        # toggle bar
        toggles = QHBoxLayout()
        self.btn_list_view = QPushButton("List view")
        self.btn_grid_view = QPushButton("Grid view")
        self.btn_list_view.setCheckable(True)
        self.btn_grid_view.setCheckable(True)
        self.btn_list_view.setChecked(True)  # default: List
        toggles.addWidget(self.btn_list_view)
        toggles.addWidget(self.btn_grid_view)
        toggles.addStretch()
        layout.addLayout(toggles)

        # file system model + tree (list view)
        self.model = QFileSystemModel()
        self.model.setRootPath(QDir.rootPath())
        self.model.setNameFilters(["*.mp4", "*.mkv", "*.avi", "*.mov", "*.webm"])
        self.model.setNameFilterDisables(False)

        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(last_dir))
        self.view.doubleClicked.connect(self.on_double_click)
        self.view.setAlternatingRowColors(True)
        self.view.setIconSize(QSize(20, 20))
        self.view.setColumnWidth(0, 380)
        self.view.setColumnWidth(1, 80)
        self.view.setColumnWidth(2, 120)
        self.view.setColumnWidth(3, 180)

        # grid view with thumbnails
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(120, 68))  # tamanho do thumb
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setSpacing(12)

        # ajustes para centralizar e alinhar
        self.grid.setWrapping(True)  # permite quebrar linha
        self.grid.setUniformItemSizes(True)  # todos os itens com mesmo tamanho
        self.grid.setGridSize(QSize(150, 110))  # largura x altura de cada célula
        self.grid.setFlow(QListView.LeftToRight)  # fluxo horizontal
        self.grid.setLayoutMode(QListView.Batched)  # otimiza renderização
        self.grid.setStyleSheet("QListWidget { qproperty-alignment: AlignHCenter; }")

        # centralizar texto embaixo do ícone
        # (aplique isso quando criar cada item)
        #item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)

        self.grid.hide()

        layout.addWidget(self.view)
        layout.addWidget(self.grid)
        self.grid.itemDoubleClicked.connect(self._on_grid_double_clicked)

        # buttons
        btn_layout = QHBoxLayout()
        self.back_btn = QPushButton("Return")
        btn_layout.insertWidget(0, self.back_btn)
        self.back_btn.clicked.connect(self.go_back)
        self.open_btn = QPushButton("Open")
        self.add_btn = QPushButton("Add to playlist")
        self.cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(self.open_btn)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.open_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.add_btn.clicked.connect(self.add_selected_to_playlist)

        self.selected_file = None

        # styles
        self.setStyleSheet("""
        QDialog { background-color: #1e1e1e; color: #f0f0f0; font-family: Segoe UI; font-size: 11pt; }
        QLineEdit { background-color: #2d2d2d; border: 1px solid #555; padding: 4px; color: #ffffff; }
        QTreeView {
            background-color: #2d2d2d; alternate-background-color: #3a3a3a; color: #f0f0f0;
            selection-background-color: #0078d7; selection-color: #ffffff; border: none;
        }
        QHeaderView::section { background-color: #333; color: #ccc; padding: 4px; border: none; }
        QListWidget { background-color: #2d2d2d; border: 1px solid #444; padding: 8px; color: #f0f0f0; }
        QPushButton { background-color: #0078d7; color: white; border-radius: 4px; padding: 6px 12px; }
        QPushButton:hover { background-color: #2899f5; }
        QPushButton:pressed { background-color: #005a9e; }
        """)

        # connections for toggle
        self.btn_list_view.clicked.connect(lambda: self._switch_mode(list_mode=True))
        self.btn_grid_view.clicked.connect(lambda: self._switch_mode(list_mode=False))
        self.path_edit.returnPressed.connect(self.change_dir)

        # initial populate grid
        self._populate_grid(last_dir)

    def _switch_mode(self, list_mode: bool):
        self.btn_list_view.setChecked(list_mode)
        self.btn_grid_view.setChecked(not list_mode)
        if list_mode:
            self.grid.hide()
            self.view.show()
        else:
            self.view.hide()
            self.grid.show()
            # refresh grid thumbnails for current dir
            current_path = self.path_edit.text() or QDir.homePath()
            self._populate_grid(current_path)

    def _populate_grid(self, folder_path: str):
        self.grid.clear()
        if not folder_path or not os.path.isdir(folder_path):
            return

        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
        try:
            entries = os.listdir(folder_path)
        except Exception:
            entries = []

        entries.sort(key=lambda x: x.lower())

        for entry in entries:
            full_path = os.path.join(folder_path, entry)

            # se for pasta → mostra também
            if os.path.isdir(full_path):
                item = QListWidgetItem(entry)
                item.setIcon(QIcon.fromTheme("folder") or QIcon(":/icons/folder.png"))
                item.setData(Qt.UserRole, full_path)
                self.grid.addItem(item)
                continue

            # se for arquivo de vídeo → mostra com thumbnail
            if any(entry.lower().endswith(ext) for ext in video_exts):
                item = QListWidgetItem(entry)
                ph = QPixmap(160, 90);
                ph.fill(Qt.black)
                item.setIcon(QIcon(ph))
                item.setData(Qt.UserRole, full_path)
                self.grid.addItem(item)

        # inicia geração de thumbs só para arquivos de vídeo
        self._start_thumb_thread(folder_path)

    def _on_grid_double_clicked(self, item: QListWidgetItem):
        if not item:
            return
        path = item.data(Qt.UserRole)
        if os.path.isdir(path):
            # abre a pasta no grid
            self.path_edit.setText(path)
            self._save_last_dir(path)
            self._populate_grid(path)
        else:
            # é arquivo → seleciona e fecha
            self.selected_file = path
            self._save_last_dir(os.path.dirname(path))
            self.accept()

    def _start_thumb_thread(self, folder_path: str):
        # cancel previous
        if hasattr(self, "_thumb_thread") and self._thumb_thread:
            self._thumb_thread.stop()
            self._thumb_thread = None
        self._thumb_thread = ThumbnailListWorker(folder_path)
        self._thumb_thread.thumb_ready.connect(self._apply_item_thumb)
        self._thumb_thread.start()

    def _apply_item_thumb(self, file_path: str, icon: QIcon):
        # cache and apply
        self._thumb_cache[file_path] = icon
        # find item
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(Qt.UserRole) == file_path:
                it.setIcon(icon)
                break

    def go_back(self):
        current_path = self.path_edit.text()
        parent = os.path.dirname(current_path)
        if os.path.exists(parent):
            self.view.setRootIndex(self.model.index(parent))
            self.path_edit.setText(parent)
            self._save_last_dir(parent)
            # refresh grid if in grid mode
            if self.btn_grid_view.isChecked():
                self._populate_grid(parent)

    def on_double_click(self, index):
        path = self.model.filePath(index)
        if QDir(path).exists():
            self.view.setRootIndex(self.model.index(path))
            self.path_edit.setText(path)
            self._save_last_dir(path)
            # adjust grid if needed
            if self.btn_grid_view.isChecked():
                self._populate_grid(path)
        else:
            self.selected_file = path
            self._save_last_dir(path)
            self.accept()

    def accept(self):
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                path = self.model.filePath(index)
                if os.path.isdir(path):
                    # se for pasta → entra nela em vez de fechar
                    self.view.setRootIndex(self.model.index(path))
                    self.path_edit.setText(path)
                    self._save_last_dir(path)
                    if self.btn_grid_view.isChecked():
                        self._populate_grid(path)
                    return  # não fecha o diálogo
                else:
                    self.selected_file = path
                    self._save_last_dir(os.path.dirname(path))
        else:
            item = self.grid.currentItem()
            if item:
                path = item.data(Qt.UserRole)
                if os.path.isdir(path):
                    # se for pasta → entra nela
                    self.path_edit.setText(path)
                    self._save_last_dir(path)
                    self._populate_grid(path)
                    return
                else:
                    self.selected_file = path
                    self._save_last_dir(os.path.dirname(path))

        super().accept()

    def change_dir(self):
        path = self.path_edit.text()
        if os.path.isdir(path):
            self.view.setRootIndex(self.model.index(path))
            self._save_last_dir(path)
            if self.btn_grid_view.isChecked():
                self._populate_grid(path)

    def add_selected_to_playlist(self):
        app = QApplication.instance()
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                p = self.model.filePath(index)
                if os.path.isfile(p):
                    app.add_to_playlist(p)
        else:
            items = self.grid.selectedItems()
            for it in items:
                p = it.data(Qt.UserRole)
                if os.path.isfile(p):
                    app.add_to_playlist(p)

    def get_selected_file(self):
        # preserved API
        if self.selected_file:
            return self.selected_file
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                return self.model.filePath(index)
        else:
            it = self.grid.currentItem()
            if it:
                return it.data(Qt.UserRole)
        return None

    def _save_last_dir(self, path):
        app = QApplication.instance()
        if hasattr(app, "settings"):
            app.settings.setValue("last_dir", path)

class ThumbnailListWorker(QThread):
    thumb_ready = Signal(str, QIcon)  # path -> icon
    def __init__(self, folder_path: str, thumb_ms: int = 3000, width=160, height=90):
        super().__init__()
        self.folder_path = folder_path
        self.thumb_ms = thumb_ms
        self.width = width
        self.height = height
        self._running = True

    def stop(self):
        self._running = False
        self.wait()

    def run(self):
        if not os.path.isdir(self.folder_path):
            return
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
        files = [f for f in os.listdir(self.folder_path) if any(f.lower().endswith(ext) for ext in video_exts)]
        files.sort(key=lambda x: x.lower())
        for file in files:
            if not self._running:
                break
            full = os.path.join(self.folder_path, file)
            # generate thumb with ffmpeg
            pix = get_frame_at(full, self.thumb_ms, self.width, self.height)
            if pix is None:
                # fallback black
                ph = QPixmap(self.width, self.height)
                ph.fill(Qt.black)
                icon = QIcon(ph)
            else:
                icon = QIcon(pix)
            self.thumb_ready.emit(full, icon)

# ---------------- Player window ----------------

class SeekSlider(QSlider):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        # floating label
        self.preview_label = QLabel(parent)
        self.preview_label.setWindowFlags(Qt.ToolTip)
        self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.preview_label.hide()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            x = event.pos().x()
            value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, self.width())
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
        x = event.pos().x()
        value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, self.width())
        preview_time = int((value / 1000.0) * duration_ms)
        if cache:
            nearest = min(cache.keys(), key=lambda k: abs(k - preview_time))
            pix = cache[nearest]
        else:
            # don’t generate on the fly, just show a placeholder
            pix = QPixmap(96, 54)
            pix.fill(Qt.black)
        img = pix.toImage()
        painter = QPainter(img)
        text = ms_to_minsec(preview_time)
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.setPen(QColor("black"))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                painter.drawText(5 + dx, img.height() - 5 + dy, text)
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

class PlayerWindow(QWidget):
    def __init__(self, title, is_broadcast=False):
        super().__init__()
        self.is_broadcast = is_broadcast
        self.setWindowIcon(QIcon(":icons/nexaplayer.png"))
        self.fullscreen = False
        self.hud_visible = True

        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint if not is_broadcast else Qt.Window)
        self.setMouseTracking(True)
        self.setMinimumSize(160, 90)
        self.setWindowTitle(title)
        self.resize(640, 360)
        self.setStyleSheet("background-color: black;")
        self.setAcceptDrops(True)

        # Video surface
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background-color: black; border: none; margin: 0; padding: 0;")
        self.label.setMinimumSize(100, 60)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setScaledContents(False)

        splash = QPixmap(":/icons/splash.png")
        self.label.setPixmap(splash.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

        # HUD
        self.position = SeekSlider(Qt.Horizontal, parent=self)
        self.position.setRange(0, 1000)
        self.position.setStyleSheet("""
        QSlider::groove:horizontal { border: 1px solid #444; height: 6px; background: #222; margin: 0px; border-radius: 3px; }
        QSlider::handle:horizontal { background: #00bfff; border: 1px solid #00aaff; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
        QSlider::sub-page:horizontal { background: #00bfff; border-radius: 3px; }
        QSlider::add-page:horizontal { background: #555; border-radius: 3px; }
        """)

        btn_style = """
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 4px; padding: 4px; }
        QPushButton:hover { background-color: #444; }
        """

        self.icon_play = QIcon(":/icons/play.png")
        self.icon_pause = QIcon(":/icons/pause.png")
        self.icon_prev = QIcon(":/icons/prev.png")
        self.icon_next = QIcon(":/icons/next.png")

        self.prev_btn = QPushButton(); self.prev_btn.setIcon(self.icon_prev); self.prev_btn.setIconSize(QSize(24, 24)); self.prev_btn.setStyleSheet(btn_style)
        self.next_btn = QPushButton(); self.next_btn.setIcon(self.icon_next); self.next_btn.setIconSize(QSize(24, 24)); self.next_btn.setStyleSheet(btn_style)
        self.play_btn = QPushButton(); self.play_btn.setIcon(self.icon_play); self.play_btn.setIconSize(QSize(24, 24)); self.play_btn.setStyleSheet(btn_style)
        self.stop_btn = QPushButton(); self.stop_btn.setIcon(QIcon(":/icons/stop.png")); self.stop_btn.setIconSize(QSize(24, 24)); self.stop_btn.setStyleSheet(btn_style)
        self.open_btn = QPushButton(); self.open_btn.setIcon(QIcon(":/icons/open.png")); self.open_btn.setIconSize(QSize(24, 24)); self.open_btn.setStyleSheet(btn_style)
        self.playlist_btn = QPushButton(); self.playlist_btn.setStyleSheet(btn_style); self.playlist_btn.setIcon(QIcon(":/icons/playlist.png")); self.playlist_btn.setIconSize(QSize(24, 24))
        self.playlist_btn.clicked.connect(lambda: QApplication.instance().show_playlist())

        self.full_btn = None
        if is_broadcast:
            self.full_btn = QPushButton(); self.full_btn.setIcon(QIcon(":/icons/fullscreen.png")); self.full_btn.setIconSize(QSize(24, 24)); self.full_btn.setStyleSheet(btn_style)

        self.volume_slider = None
        if is_broadcast:
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 125)
            self.volume_slider.setValue(80)
            self.volume_slider.setFixedWidth(100)
            self.volume_slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal { border: 1px solid dark blue; height: 6px; background: white; }
            QSlider::handle:horizontal { background: blue; width: 12px; margin: -4px 0; border-radius: 6px; }
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

        # Frame timer (reduced for performance)
        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(50)  # ~20fps
        self.frame_timer.timeout.connect(self.update_frame)
        self.frame_timer.start()

        # Connections
        self.play_btn.clicked.connect(lambda: QApplication.instance().play_pause())
        self.stop_btn.clicked.connect(lambda: QApplication.instance().stop())
        self.open_btn.clicked.connect(lambda: QApplication.instance().open_file())
        self.prev_btn.clicked.connect(lambda: QApplication.instance().previous_track())
        self.next_btn.clicked.connect(lambda: QApplication.instance().next_track())
        self.position.sliderMoved.connect(lambda v: QApplication.instance().set_position(v))
        self.position.sliderReleased.connect(lambda: QApplication.instance().set_position(self.position.value()))
        if self.volume_slider:
            self.volume_slider.valueChanged.connect(lambda v: QApplication.instance().set_volume(v))
        if self.full_btn:
            self.full_btn.clicked.connect(self.toggle_fullscreen)

        # Overlay
        self.overlay_label = QLabel(self.label)
        self.overlay_label.setStyleSheet("""
        QLabel { background-color: rgba(0,0,0,160); color: white; padding: 6px 12px; border-radius: 6px; font-size: 14px; }
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
        if not self.is_broadcast and event.button() == Qt.LeftButton:
            hwnd = int(self.winId())
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 0x2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
            event.accept()
            return
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
                if top and left: return True, 13  # HTTOPLEFT
                if top and right: return True, 14  # HTTOPRIGHT
                if bottom and left: return True, 16  # HTBOTTOMLEFT
                if bottom and right: return True, 17  # HTBOTTOMRIGHT
                if left: return True, 10  # HTLEFT
                if right: return True, 11  # HTRIGHT
                if top: return True, 12  # HTTOP
                if bottom: return True, 15  # HTBOTTOM
                buttons = QGuiApplication.mouseButtons()
                if buttons & Qt.LeftButton:
                    return True, 2  # HTCAPTION
        return False, 0

    # Drag & Drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                QApplication.instance().open_path(path)

    # Shortcuts
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
            if self.hud_visible:
                self.setWindowFlags(Qt.Window)
                self.showNormal()
                self.position.show()
                self.hud_container.show()
            else:
                self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
                self.showNormal()
                self.position.hide()
                self.hud_container.hide()
        else:
            if self.hud_visible:
                self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowStaysOnTopHint)
                self.showNormal()
                self.position.show()
                self.hud_container.show()
            else:
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

    def show_overlay_message(self, text, visible_duration=2000, fade_duration=500):
        self.overlay_label.setText(text)
        self.overlay_label.adjustSize()
        x = (self.label.width() - self.overlay_label.width()) // 2
        y = (self.label.height() - self.overlay_label.height()) // 2
        self.overlay_label.move(x, y)
        self.overlay_label.show()
        self.overlay_anim.stop()
        self.overlay_anim.setDuration(fade_duration)
        self.overlay_anim.setStartValue(0.0)
        self.overlay_anim.setEndValue(1.0)
        self.overlay_anim.start()
        def fade_out():
            self.overlay_anim.stop()
            self.overlay_anim.setDuration(fade_duration)
            self.overlay_anim.setStartValue(1.0)
            self.overlay_anim.setEndValue(0.0)
            self.overlay_anim.start()
        QTimer.singleShot(visible_duration, fade_out)

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
        QMenu { background-color: #222; color: white; border: 1px solid #444; }
        QMenu::item:selected { background-color: #339CFF; color: black; }
        """)
        app = QApplication.instance()
        if not isinstance(app, App):
            print("ERRO: QApplication.instance() não é App!")
            return
        menu.addAction("Open File", lambda: QApplication.instance().open_file())
        menu.addAction("Play/Pause", lambda: QApplication.instance().play_pause())
        menu.addAction("Stop", lambda: QApplication.instance().stop())

        hud_action = QAction("Toggle HUD", self)
        hud_action.setCheckable(True)
        hud_action.setChecked(self.hud_visible)
        hud_action.triggered.connect(self.toggle_hud)
        menu.addAction(hud_action)

        fs_action = QAction("Fullscreen", self)
        fs_action.setCheckable(True)
        fs_action.setChecked(self.isFullScreen())
        fs_action.triggered.connect(self.toggle_fullscreen)
        menu.addAction(fs_action)

        loop_action = QAction("Loop", self)
        loop_action.setCheckable(True)
        loop_action.setChecked(app.loop_enabled)
        loop_action.triggered.connect(app.toggle_loop)
        menu.addAction(loop_action)

        playlist_menu = menu.addMenu("Playlist")
        playlist_menu.addAction("List", lambda: QApplication.instance().show_playlist())
        playlist_menu.addAction("Next", app.next_track)
        playlist_menu.addAction("Previous", app.previous_track)
        playlist_menu.addAction("Loop Playlist", app.toggle_loop_playlist)

        speed_menu = menu.addMenu("Playback Speed")
        for rate in [0.5, 1.0, 1.25, 1.5, 2.0]:
            act = QAction(f"{rate:.2f}x", self)
            act.setCheckable(True)
            current = abs(app.mediaplayer.get_rate() - rate) < 0.01
            act.setChecked(current)
            act.triggered.connect(lambda checked, r=rate: app.set_playback_rate(r))
            speed_menu.addAction(act)

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

        mini_action = QAction("Enable Mini-player", self)
        mini_action.setCheckable(True)
        mini_action.setChecked(app.miniplayer_enabled)
        mini_action.triggered.connect(lambda checked: app.toggle_miniplayer(checked))
        menu.addAction(mini_action)

        menu.exec(self.label.mapToGlobal(pos))

    def update_frame(self):
        app = QApplication.instance()
        if not app.has_media:
            return
        buf = app.video_buf
        with QMutexLocker(buf.mutex):
            img = QImage(buf.buf, buf.width, buf.height, buf.stride, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(img.copy())
            self.label.setPixmap(pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        app = QApplication.instance()
        if not getattr(app, "has_media", False):
            splash = QPixmap(":/icons/splash.png")
            if not splash.isNull():
                target = self.label.contentsRect().size()
                self.label.setPixmap(splash.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def closeEvent(self, event):
        app = QApplication.instance()
        if self.is_broadcast:
            if getattr(app, "mini", None):
                app.mini.close()
                app.mini = None
            app.quit()
        else:
            event.accept()

# ---------------- Playlist Dialog ----------------

class PlaylistDialog(QDialog):
    def __init__(self, playlist, play_callback, parent=None):
        super().__init__(parent)
        self.play_callback = play_callback
        self._started_playback = False
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Playlist")
        self.setMinimumSize(420, 320)
        self.setStyleSheet("""
        QDialog { background-color: #1e1e1e; }
        QListWidget { background-color: #2b2b2b; color: white; border: 1px solid #444; padding: 4px; }
        QListWidget::item:selected { background-color: #00bfff; color: black; }
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 4px; padding: 6px 12px; color: white; }
        QPushButton:hover { background-color: #444; }
        """)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        for p in playlist:
            if isinstance(p, str) and os.path.exists(p):
                self.list_widget.addItem(p)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(self.list_widget)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("➕ Add")
        self.btn_play = QPushButton("▶ Play")
        self.btn_remove = QPushButton("🗑 Remove")
        self.btn_close = QPushButton("✖ Close")
        btns.addWidget(self.btn_add); btns.addWidget(self.btn_play); btns.addWidget(self.btn_remove); btns.addStretch(); btns.addWidget(self.btn_close)
        layout.addLayout(btns)

        self.btn_close.clicked.connect(self.accept)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_play.clicked.connect(lambda: self.play_selected())
        self.list_widget.itemDoubleClicked.connect(self.play_selected)
        self.btn_add.clicked.connect(self.add_with_custom_loader)

    def add_with_custom_loader(self):
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
                    if before == 0:
                        self.list_widget.setCurrentRow(0)

    def remove_selected(self):
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)
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
        self.play_callback(path)
        self.accept()

    def accept(self):
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
        self.playlist = []
        self.current_index = -1
        self.loop_playlist = False
        self.media_finished.connect(self._play_next_safe)
        self.thumbnail_cache = {}
        self.miniplayer_enabled = self.settings.value("miniplayer_enabled", True, type=bool)
        self.instance = vlc.Instance("--no-osd --no-video-title-show")
        self.mediaplayer = self.instance.media_player_new()

        event_manager = self.mediaplayer.event_manager()
        event_manager.event_attach(vlc.EventType.MediaPlayerPlaying, self._on_media_playing)
        event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_media_end)

        if self.miniplayer_enabled:
            self.mini = PlayerWindow("Nexa Player - PIP", is_broadcast=False)
            self.mini.resize(320, 180)
            self.mini.show()
        else:
            self.mini = None

        saved_ratio = self.settings.value("aspect_ratio", "", type=str)
        if saved_ratio:
            self.mediaplayer.video_set_aspect_ratio(saved_ratio.encode("utf-8"))
            self.mediaplayer.video_set_scale(0)

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

        self.broadcast = PlayerWindow("Nexa Player", is_broadcast=True)
        self.broadcast.show()
        self.broadcast.destroyed.connect(self._on_broadcast_closed)

        # UI timer (reduced and debounced)
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(250)  # lighter
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start()
        self._last_ui_second = -1

        self.loop_enabled = False

    # Helpers

    def _set_play_icon(self, playing: bool):
        # centralize icon sync
        self.broadcast.play_btn.setIcon(self.broadcast.icon_pause if playing else self.broadcast.icon_play)
        if self.mini:
            self.mini.play_btn.setIcon(self.mini.icon_pause if playing else self.mini.icon_play)

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
            self.mediaplayer.video_set_scale(0)
            self.mediaplayer.video_set_aspect_ratio(None)
            w = self.mediaplayer.video_get_width()
            h = self.mediaplayer.video_get_height()
            if not w or not h:
                w, h = self.mediaplayer.video_get_size(0) or (640, 360)
            self.video_buf = VideoBuffer(w, h)
            self.mediaplayer.video_set_format("RGBA", w, h, self.video_buf.stride)
            print(f"Vídeo iniciado: {w}x{h} — formato aplicado.")
        except Exception as e:
            print("Erro em _on_media_playing:", e)

    def add_to_playlist(self, path: str):
        if os.path.exists(path):
            self.playlist.append(path)
            if self.current_index == -1:
                self.current_index = 0

    def load_playlist(self, paths: list[str]):
        self.playlist = [p for p in paths if os.path.exists(p)]
        self.current_index = 0 if self.playlist else -1

    def play_current(self):
        if not self.playlist: return
        if self.current_index < 0 or self.current_index >= len(self.playlist): return
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
        self.mediaplayer.stop()
        self.open_path(path)

    def _on_broadcast_closed(self):
        if self.mini is not None:
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
            if self.video_path:
                self.update_titles(self.video_path)
        elif not enabled and self.mini is not None:
            self.mini.close()
            self.mini = None

    # -------- Ações globais --------

    def open_file(self):
        dlg = FileLoader(self.broadcast)
        if dlg.exec() == QDialog.Accepted:
            file = dlg.get_selected_file()
            if file:
                self.open_path(file)

    def set_playback_rate(self, rate: float):
        self.mediaplayer.set_rate(rate)
        self.broadcast.show_overlay_message(f"Speed: {rate:.2f}x")

    def open_path(self, path: str):
        if self.mediaplayer.is_playing():
            self.mediaplayer.stop()
        media = self.instance.media_new(path)
        self.mediaplayer.set_media(media)
        QTimer.singleShot(0, self.mediaplayer.play)
        self.video_path = path
        self.video_duration_ms = get_video_duration(path)
        self.has_media = True

        self.thumbnail_cache = {}
        if hasattr(self, "thumbnail_worker") and self.thumbnail_worker:
            self.thumbnail_worker.stop()
        self.thumbnail_worker = ThumbnailWorker(path, interval_s=30)
        self.thumbnail_worker.thumbnail_ready.connect(self._store_thumbnail)
        self.thumbnail_worker.start()

        self.update_titles(path)
        self._set_play_icon(True)

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
            QTimer.singleShot(0, self._restart_media)

    def _restart_media(self):
        self.mediaplayer.stop()
        self.mediaplayer.set_time(0)
        self.mediaplayer.play()
        self._set_play_icon(True)

    def play_pause(self):
        state = self.mediaplayer.get_state()
        if state == vlc.State.Ended:
            self.mediaplayer.stop()
            self.mediaplayer.set_time(0)
            self.mediaplayer.play()
            self._set_play_icon(True)
        elif self.mediaplayer.is_playing():
            self.mediaplayer.pause()
            self._set_play_icon(False)
        else:
            self.mediaplayer.play()
            self._set_play_icon(True)

    def stop(self):
        self.mediaplayer.stop()
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

    def set_aspect_ratio(self, ratio: str | None):
        state = self.mediaplayer.get_state()
        if ratio:
            self.mediaplayer.video_set_aspect_ratio(ratio.encode("utf-8"))
            self.mediaplayer.video_set_scale(0)
            self.settings.setValue("aspect_ratio", ratio)
            self.broadcast.show_overlay_message(f"Aspect Ratio: {ratio}")
        else:
            self.mediaplayer.video_set_aspect_ratio(None)
            self.mediaplayer.video_set_scale(0)
            self.settings.setValue("aspect_ratio", "")
            self.broadcast.show_overlay_message("Aspect Ratio: Auto")
        if state in (vlc.State.Playing, vlc.State.Paused):
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
        # debounce: only update once per second for labels
        current_second = time_ // 1000
        pos_changed = False

        if length > 0:
            pos = int((time_ / length) * 1000)
            for win in (self.broadcast, self.mini):
                if not win: continue
                if not hasattr(win, "position") or not hasattr(win, "time_label"): continue
                # update slider every tick (lightweight)
                win.position.blockSignals(True)
                win.position.setValue(pos)
                win.position.blockSignals(False)
                pos_changed = True

        if current_second != self._last_ui_second:
            # update labels more sparsely
            for win in (self.broadcast, self.mini):
                if not win: continue
                if length > 0:
                    win.time_label.setText(f"{ms_to_minsec(time_)} / {ms_to_minsec(length)}")
                else:
                    win.time_label.setText("--:-- / --:--")
            self._last_ui_second = current_second

        # Loop robustness
        if self.loop_enabled and length > 0:
            state = self.mediaplayer.get_state()
            if state == vlc.State.Ended:
                self.mediaplayer.stop()
                self.mediaplayer.set_time(0)
                self.mediaplayer.play()
                self._set_play_icon(True)
            elif time_ >= length - 200:
                self.mediaplayer.set_time(0)
                self.mediaplayer.play()
                self._set_play_icon(True)

        # fix play icon when ended without loop
        if not self.loop_enabled and self.mediaplayer.get_state() == vlc.State.Ended:
            self._set_play_icon(False)

    def update_titles(self, mrl: str):
        filename = os.path.basename(mrl)
        name_only, _ = os.path.splitext(filename)
        self.broadcast.setWindowTitle(f"{name_only} - Nexa Player")
        if self.mini is not None:
            self.mini.setWindowTitle(f"{name_only} - Nexa Player - PIP")

# --- Main ---
if __name__ == "__main__":
    app = App(sys.argv)
    splash = QSplashScreen(QPixmap("icons/splash.png"))
    if len(sys.argv) > 1:
        path = sys.argv[1]
        app.open_path(path)
    sys.exit(app.exec())
