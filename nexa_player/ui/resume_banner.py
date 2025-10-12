from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class ResumeBanner(QWidget):
    """
    Slim widget displayed above the video asking whether to resume playback.
    """

    resume_requested = Signal()
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "background-color: rgba(30, 30, 30, 220); color: white; padding: 6px;"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(12)

        self.label = QLabel("Resume playback from where you left off?")
        self.label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self.label, 1)

        btn_resume = QPushButton("Resume")
        btn_restart = QPushButton("Start Over")
        for btn in (btn_resume, btn_restart):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background-color: #0078d7; color: white; padding: 4px 12px; "
                "border-radius: 4px; }"
                "QPushButton:hover { background-color: #2899f5; }"
                "QPushButton:pressed { background-color: #005a9e; }"
            )
            layout.addWidget(btn)

        btn_resume.clicked.connect(self.resume_requested)
        btn_restart.clicked.connect(self.restart_requested)

    def prompt(self, label: str):
        self.label.setText(label)
        self.setVisible(True)

    def dismiss(self):
        self.setVisible(False)
