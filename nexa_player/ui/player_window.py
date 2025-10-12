from __future__ import annotations

import ctypes
import logging

from PySide6.QtCore import QEasingCurve, QPoint, Qt, QTimer, QPropertyAnimation, QSize, QMutexLocker
from PySide6.QtGui import (
    QAction,
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QPixmap,
    QMouseEvent,
    QPainter,
    QColor,
)

try:
    from ctypes import wintypes
except ImportError:  # pragma: no cover - non-Windows
    wintypes = None
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

try:  # pragma: no cover
    from .. import resources_rc  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    import resources_rc  # type: ignore  # noqa: F401

from .resume_banner import ResumeBanner
from .seek_slider import SeekSlider

log = logging.getLogger(__name__)


class PlayerWindow(QWidget):
    def __init__(self, title: str, is_broadcast: bool = False):
        super().__init__()
        self.is_broadcast = is_broadcast
        self.fullscreen = False
        self.hud_visible = True

        self.setWindowIcon(QIcon(":icons/nexaplayer.png"))
        if is_broadcast:
            self.setWindowFlags(Qt.Window)
        else:
            self.setWindowFlags(
                Qt.Window | Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint
            )
        self.setMouseTracking(True)
        self.setMinimumSize(160, 90)
        self.setWindowTitle(title)
        self.resize(640, 360)
        self.setStyleSheet("background-color: black;")
        self.setAcceptDrops(True)

        self.resume_banner = ResumeBanner(self)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background-color: black; border: none; margin: 0; padding: 0;")
        self.label.setMinimumSize(100, 60)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setScaledContents(False)

        splash = QIcon(":/icons/splash.png").pixmap(320, 180)
        if splash:
            self.label.setPixmap(splash)

        self.position = SeekSlider(Qt.Horizontal, parent=self)
        self.position.setRange(0, 1000)
        self.position.setStyleSheet(
            """
            QSlider::groove:horizontal { border: 1px solid #444; height: 6px; background: #222; margin: 0px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #00bfff; border: 1px solid #00aaff; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
            QSlider::sub-page:horizontal { background: #00bfff; border-radius: 3px; }
            QSlider::add-page:horizontal { background: #555; border-radius: 3px; }
            """
        )

        btn_style = """
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 4px; padding: 0 6px; min-height: 26px; }
        QPushButton:hover { background-color: #444; }
        """

        style = self.style()

        def _white_icon(role: QStyle.StandardPixmap) -> QIcon:
            pix = style.standardIcon(role).pixmap(24, 24)
            painter = QPainter(pix)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pix.rect(), QColor(255, 255, 255))
            painter.end()
            return QIcon(pix)

        def _playlist_icon() -> QIcon:
            pix = QPixmap(24, 24)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255))
            rows = (5, 10, 15)
            for y in rows:
                painter.drawEllipse(4, y, 4, 4)
                painter.drawRoundedRect(11, y, 9, 4, 2, 2)
            painter.end()
            return QIcon(pix)

        self.icon_play = _white_icon(QStyle.SP_MediaPlay)
        self.icon_pause = _white_icon(QStyle.SP_MediaPause)
        self.icon_prev = _white_icon(QStyle.SP_MediaSkipBackward)
        self.icon_next = _white_icon(QStyle.SP_MediaSkipForward)

        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(self.icon_prev)
        self.prev_btn.setIconSize(QSize(24, 24))
        self.prev_btn.setStyleSheet(btn_style)

        self.next_btn = QPushButton()
        self.next_btn.setIcon(self.icon_next)
        self.next_btn.setIconSize(QSize(24, 24))
        self.next_btn.setStyleSheet(btn_style)

        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.icon_play)
        self.play_btn.setIconSize(QSize(24, 24))
        self.play_btn.setStyleSheet(btn_style)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(_white_icon(QStyle.SP_MediaStop))
        self.stop_btn.setIconSize(QSize(24, 24))
        self.stop_btn.setStyleSheet(btn_style)

        self.open_btn = QPushButton()
        self.open_btn.setIcon(_white_icon(QStyle.SP_DialogOpenButton))
        self.open_btn.setIconSize(QSize(24, 24))
        self.open_btn.setStyleSheet(btn_style)

        self.playlist_btn = QPushButton()
        self.playlist_btn.setStyleSheet(btn_style)
        self.playlist_btn.setIcon(_playlist_icon())
        self.playlist_btn.setIconSize(QSize(24, 24))
        self.playlist_btn.clicked.connect(lambda: QApplication.instance().show_playlist())

        self.full_btn = None
        if is_broadcast:
            self.full_btn = QPushButton()
            self.full_btn.setIcon(_white_icon(QStyle.SP_TitleBarMaxButton))
            self.full_btn.setIconSize(QSize(24, 24))
            self.full_btn.setStyleSheet(btn_style)

        self.volume_slider = None
        if is_broadcast:
            self.volume_slider = QSlider(Qt.Horizontal)
            self.volume_slider.setRange(0, 125)
            self.volume_slider.setFixedWidth(100)
            self.volume_slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.volume_slider.setStyleSheet(
            self.volume_slider.setStyleSheet("QSlider::groove:horizontal { border: 1px solid #004f9e; height: 6px; background: #ffffff; } QSlider::handle:horizontal { background: #0078d7; width: 12px; margin: -4px 0; border-radius: 6px; }")
            )

        self.time_label = QLabel("--:-- / --:--")
        self.time_label.setStyleSheet("color: white; font-weight: bold;")

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(8, 2, 8, 2)
        controls_row.setSpacing(4)
        controls_row.addWidget(self.open_btn)
        controls_row.addWidget(self.play_btn)
        controls_row.addWidget(self.prev_btn)
        controls_row.addWidget(self.stop_btn)
        controls_row.addWidget(self.next_btn)
        if self.full_btn:
            controls_row.addWidget(self.full_btn)
        controls_row.addWidget(self.playlist_btn)
        if self.volume_slider:
            controls_row.addWidget(self.volume_slider)
        controls_row.addWidget(self.time_label)

        self.hud_container = QWidget(self)
        self.hud_container.setStyleSheet("background: transparent;")
        self.hud_container.setLayout(controls_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.resume_banner)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.position)
        layout.addWidget(self.hud_container)

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(50)
        self.frame_timer.timeout.connect(self.update_frame)
        self.frame_timer.start()

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

        self.overlay_label = QLabel(self.label)
        self.overlay_label.setStyleSheet(
            "QLabel { background-color: rgba(0,0,0,160); color: white; padding: 6px 12px; border-radius: 6px; font-size: 14px; }"
        )
        self.overlay_label.setAlignment(Qt.AlignCenter)
        self.overlay_label.hide()
        self.overlay_opacity = QGraphicsOpacityEffect(self.overlay_label)
        self.overlay_label.setGraphicsEffect(self.overlay_opacity)
        self.overlay_anim = QPropertyAnimation(self.overlay_opacity, b"opacity")
        self.overlay_anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self.show_context_menu)

        self.add_shortcuts()

    # --- UI helpers -----------------------------------------------------

    def show_resume_prompt(self, message: str):
        if self.resume_banner:
            self.resume_banner.prompt(message)

    def hide_resume_prompt(self):
        if self.resume_banner:
            self.resume_banner.dismiss()

    # --- Qt overrides ---------------------------------------------------

    def mousePressEvent(self, event):
        if not self.is_broadcast and event.button() == Qt.LeftButton:
            hwnd = int(self.winId())
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 0x2, 0)
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
        if eventType == "windows_generic_MSG" and wintypes is not None:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x84:  # WM_NCHITTEST
                pos = self.mapFromGlobal(QCursor.pos())
                w, h = self.width(), self.height()
                margin = 8
                left = pos.x() < margin
                right = pos.x() > w - margin
                top = pos.y() < margin
                bottom = pos.y() > h - margin
                if top and left:
                    return True, 13  # HTTOPLEFT
                if top and right:
                    return True, 14  # HTTOPRIGHT
                if bottom and left:
                    return True, 16  # HTBOTTOMLEFT
                if bottom and right:
                    return True, 17  # HTBOTTOMRIGHT
                if left:
                    return True, 10  # HTLEFT
                if right:
                    return True, 11  # HTRIGHT
                if top:
                    return True, 12  # HTTOP
                if bottom:
                    return True, 15  # HTBOTTOM
                buttons = QGuiApplication.mouseButtons()
                if buttons & Qt.LeftButton:
                    return True, 2  # HTCAPTION
        return False, 0

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                QApplication.instance().open_path(path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        app = QApplication.instance()
        if not getattr(app, "has_media", False):
            splash = QIcon(":/icons/splash.png").pixmap(self.label.size())
            if not splash.isNull():
                target = self.label.contentsRect().size()
                self.label.setPixmap(
                    splash.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )

    def closeEvent(self, event):
        app = QApplication.instance()
        if self.is_broadcast:
            if getattr(app, "mini", None):
                app.mini.close()
                app.mini = None
            app.quit()
        else:
            event.accept()

    # --- Behaviour ------------------------------------------------------

    def add_shortcuts(self):
        act_play = QAction(self)
        act_play.setShortcut(Qt.Key_Space)
        act_play.triggered.connect(lambda: QApplication.instance().play_pause())
        self.addAction(act_play)

        if self.is_broadcast:
            act_f = QAction(self)
            act_f.setShortcut(Qt.Key_F)
            act_f.triggered.connect(self.toggle_fullscreen)
            self.addAction(act_f)

            act_esc = QAction(self)
            act_esc.setShortcut(Qt.Key_Escape)
            act_esc.triggered.connect(self.exit_fullscreen)
            self.addAction(act_esc)

        act_left = QAction(self)
        act_left.setShortcut(Qt.Key_Left)
        act_left.triggered.connect(lambda: QApplication.instance().seek(-5000))
        self.addAction(act_left)

        act_right = QAction(self)
        act_right.setShortcut(Qt.Key_Right)
        act_right.triggered.connect(lambda: QApplication.instance().seek(5000))
        self.addAction(act_right)

        if self.is_broadcast:
            act_plus = QAction(self)
            act_plus.setShortcut(Qt.Key_Plus)
            act_plus.triggered.connect(lambda: QApplication.instance().adjust_volume(5))
            self.addAction(act_plus)

            act_minus = QAction(self)
            act_minus.setShortcut(Qt.Key_Minus)
            act_minus.triggered.connect(lambda: QApplication.instance().adjust_volume(-5))
            self.addAction(act_minus)

        if not self.is_broadcast:
            act_h = QAction(self)
            act_h.setShortcut(Qt.Key_H)
            act_h.triggered.connect(self.toggle_hud)
            self.addAction(act_h)

        act_speed_up = QAction(self)
        act_speed_up.setShortcut("]")
        act_speed_up.triggered.connect(lambda: QApplication.instance().adjust_rate(0.25))
        self.addAction(act_speed_up)

        act_speed_down = QAction(self)
        act_speed_down.setShortcut("[")
        act_speed_down.triggered.connect(lambda: QApplication.instance().adjust_rate(-0.25))
        self.addAction(act_speed_down)

        act_loop = QAction(self)
        act_loop.setShortcut("L")
        act_loop.triggered.connect(lambda: QApplication.instance().toggle_loop())
        self.addAction(act_loop)

        act_audio = QAction(self)
        act_audio.setShortcut("A")
        act_audio.triggered.connect(lambda: QApplication.instance().cycle_audio_track())
        self.addAction(act_audio)

        act_subs = QAction(self)
        act_subs.setShortcut("S")
        act_subs.triggered.connect(lambda: QApplication.instance().cycle_subtitle_track())
        self.addAction(act_subs)

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
                self.setWindowFlags(
                    Qt.Window | Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint
                )
                self.showNormal()
                self.setWindowTitle(self.windowTitle())  # preserve title bar
                self.position.show()
                self.hud_container.show()
            else:
                self.setWindowFlags(
                    Qt.Window | Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint
                )
                self.showNormal()
                self.setWindowTitle(self.windowTitle())
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
        menu.addAction("Open File", lambda: app.open_file())
        menu.addAction("Play/Pause", lambda: app.play_pause())
        menu.addAction("Stop", lambda: app.stop())

        hud_action = QAction("Toggle HUD", self)
        hud_action.setCheckable(True)
        hud_action.setChecked(self.hud_visible)
        hud_action.triggered.connect(self.toggle_hud)
        menu.addAction(hud_action)

        if self.full_btn:
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
        playlist_menu.addAction("List", lambda: app.show_playlist())
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

        menu.exec(self.label.mapToGlobal(pos))
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
        if not getattr(app, "has_media", False):
            return
        buf = app.video_buf
        locker = QMutexLocker(buf.mutex)
        try:
            image = QImage(
                buf.buf, buf.width, buf.height, buf.stride, QImage.Format_RGBA8888
            )
            pix = QPixmap.fromImage(image.copy())
        finally:
            del locker
        self.label.setPixmap(
            pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )