from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
)


class DependencyDialog(QDialog):
    """
    Styled dialog used for dependency guidance (python-vlc / libvlc).
    """

    def __init__(
        self,
        title: str,
        message: str,
        parent=None,
        allow_browse: bool = False,
        default_dir: Optional[Path] = None,
        download_url: Optional[str] = None,
        show_cancel: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(":/icons/nexaplayer.png"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.allow_browse = allow_browse
        self.default_dir = default_dir
        self.download_url = download_url
        self.show_cancel = show_cancel
        self.selected_path: Optional[Path] = None

        self._setup_ui(message)

    def _setup_ui(self, message: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.setStyleSheet(
            """
            QDialog { background-color: #1e1e1e; color: #f0f0f0; font-family: Segoe UI; font-size: 11pt; }
            QLabel { color: #f0f0f0; }
            QPushButton { background-color: #0078d7; color: white; border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #2899f5; }
            QPushButton:pressed { background-color: #005a9e; }
            QPushButton#secondary { background-color: #333; border: 1px solid #555; }
            QPushButton#secondary:hover { background-color: #444; }
            QLineEdit { background-color: #2d2d2d; border: 1px solid #555; padding: 6px; color: #ffffff; }
            """
        )

        msg_label = QLabel(message, self)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        self.path_edit: Optional[QLineEdit] = None
        if self.allow_browse:
            browse_row = QHBoxLayout()
            self.path_edit = QLineEdit(self)
            self.path_edit.setReadOnly(True)
            self.path_edit.setPlaceholderText("Select your VLC installation folder...")
            browse_row.addWidget(self.path_edit)

            browse_btn = QPushButton("Browse...", self)
            browse_btn.setObjectName("secondary")
            browse_btn.clicked.connect(self._browse_for_folder)
            browse_row.addWidget(browse_btn)
            layout.addLayout(browse_row)

        button_row = QHBoxLayout()
        button_row.addStretch()

        if self.download_url:
            download_btn = QPushButton("Download VLC", self)
            download_btn.setObjectName("secondary")
            download_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.download_url)))
            button_row.addWidget(download_btn)

        if self.show_cancel:
            cancel_btn = QPushButton("Cancel", self)
            cancel_btn.setObjectName("secondary")
            cancel_btn.clicked.connect(self.reject)
            button_row.addWidget(cancel_btn)

        self.ok_btn = QPushButton("Continue" if self.allow_browse else "OK", self)
        self.ok_btn.clicked.connect(self._accept_if_valid)
        self.ok_btn.setEnabled(not self.allow_browse)
        button_row.addWidget(self.ok_btn)

        layout.addLayout(button_row)

    def _browse_for_folder(self) -> None:
        start_dir = str(self.default_dir) if self.default_dir else ""
        chosen = QFileDialog.getExistingDirectory(self, "Locate VLC Installation", start_dir)
        if chosen:
            self.selected_path = Path(chosen)
            if self.path_edit:
                self.path_edit.setText(chosen)
            self.ok_btn.setEnabled(True)

    def _accept_if_valid(self) -> None:
        if self.allow_browse and not self.selected_path:
            return
        self.accept()

    @staticmethod
    def show_python_bindings_warning(parent=None) -> None:
        msg = (
            "The python-vlc bindings are not installed.\n\n"
            "Please install them with:\n"
            "    pip install python-vlc\n\n"
            "After installation, restart Nexa Player."
        )
        DependencyDialog(
            "Python-VLC Missing",
            msg,
            parent=parent,
            allow_browse=False,
            download_url="https://pypi.org/project/python-vlc/",
            show_cancel=False,
        ).exec()

    @staticmethod
    def show_invalid_vlc_folder(parent=None) -> None:
        msg = (
            "The selected folder does not contain a complete VLC installation.\n\n"
            "Ensure you select the root of a 64-bit VLC install (contains libvlc.dll, "
            "libvlccore.dll, and the plugins folder), then try again."
        )
        DependencyDialog(
            "Incomplete VLC Installation",
            msg,
            parent=parent,
            allow_browse=False,
            show_cancel=False,
        ).exec()

    @staticmethod
    def ask_for_vlc_folder(parent=None, default_dir: Optional[Path] = None) -> Optional[Path]:
        msg = (
            "Nexa Player could not find the VLC runtime libraries.\n\n"
            "Select the root of your 64-bit VLC installation (contains libvlc.dll, libvlccore.dll, and the plugins folder).\n"
            "If VLC is not installed, click 'Download VLC' to install it first."
        )
        dlg = DependencyDialog(
            "Locate VLC",
            msg,
            parent=parent,
            allow_browse=True,
            default_dir=default_dir,
            download_url="https://www.videolan.org/vlc/",
            show_cancel=True,
        )
        if dlg.exec() == QDialog.Accepted:
            return dlg.selected_path
        return None
