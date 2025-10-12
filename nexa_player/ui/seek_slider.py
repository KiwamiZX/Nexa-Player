from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QSlider, QStyle

from ..helpers import ms_to_minsec


class SeekSlider(QSlider):
    """
    Slider with preview thumbnail while scrubbing.
    """

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
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
        x = event.pos().x()
        value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, self.width()
        )
        preview_time = int((value / 1000.0) * duration_ms)
        pix = self._pick_preview_image(cache, preview_time)
        if pix is None:
            return
        pix = self._draw_time(pix, preview_time)
        self.preview_label.setPixmap(pix)
        self.preview_label.adjustSize()
        global_pos = self.mapToGlobal(event.pos())
        self.preview_label.move(
            global_pos + QPoint(-pix.width() // 2, -pix.height() - 10)
        )
        self.preview_label.show()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.preview_label.hide()
        super().leaveEvent(event)

    @staticmethod
    def _pick_preview_image(cache: dict[int, QImage | QPixmap], preview_time: int):
        if not cache:
            placeholder = QPixmap(96, 54)
            placeholder.fill(Qt.black)
            return placeholder
        nearest = min(cache.keys(), key=lambda k: abs(k - preview_time))
        image = cache[nearest]
        if isinstance(image, QPixmap):
            return image
        return QPixmap.fromImage(image)

    @staticmethod
    def _draw_time(pix: QPixmap, preview_time: int) -> QPixmap:
        image = pix.toImage()
        painter = QPainter(image)
        text = ms_to_minsec(preview_time)
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.setPen(QColor("black"))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                painter.drawText(
                    5 + dx, image.height() - 5 + dy, text
                )
        painter.setPen(QColor("white"))
        painter.drawText(5, image.height() - 5, text)
        painter.end()
        return QPixmap.fromImage(image)
