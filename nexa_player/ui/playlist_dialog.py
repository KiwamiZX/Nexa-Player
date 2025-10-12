from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from ..services.playlist_io import load_playlist, save_playlist
from .file_loader import FileLoader


class PlaylistDialog(QDialog):
    """
    Playlist manager dialog. Supports reordering, saving, loading and playback.
    """

    def __init__(
        self,
        playlist: Iterable[str],
        play_callback: Callable[[str], None],
        parent=None,
    ):
        super().__init__(parent)
        self.play_callback = play_callback
        self._started_playback = False
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Playlist")
        self.setMinimumSize(480, 360)
        self.setStyleSheet(
            """
            QDialog { background-color: #1e1e1e; }
            QListWidget { background-color: #2b2b2b; color: white; border: 1px solid #444; padding: 4px; }
            QListWidget::item:selected { background-color: #00bfff; color: black; }
            QPushButton { background-color: #333; border: 1px solid #555; border-radius: 4px; padding: 6px 12px; color: white; }
            QPushButton:hover { background-color: #444; }
            """
        )

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        layout.addWidget(self.list_widget)

        for path in playlist:
            if isinstance(path, str) and os.path.exists(path):
                self.list_widget.addItem(path)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        controls = QHBoxLayout()

        self.btn_add = self._make_button("Add", QStyle.SP_FileDialogNewFolder, self.add_with_loader)
        self.btn_play = self._make_button("Play", QStyle.SP_MediaPlay, self.play_selected)
        self.btn_remove = self._make_button("Remove", QStyle.SP_DialogDiscardButton, self.remove_selected)
        self.btn_save = self._make_button("Save...", QStyle.SP_DialogSaveButton, self.save_playlist)
        self.btn_load = self._make_button("Load...", QStyle.SP_DialogOpenButton, self.load_playlist)
        self.btn_close = self._make_button("Close", QStyle.SP_DialogCloseButton, self.accept)

        controls.addWidget(self.btn_add)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_remove)
        controls.addWidget(self.btn_save)
        controls.addWidget(self.btn_load)
        controls.addStretch()
        controls.addWidget(self.btn_close)

        layout.addLayout(controls)

        self.list_widget.itemDoubleClicked.connect(self.play_selected)

    # ------------------------------------------------------------------
    # Helpers

    def _make_button(self, text: str, icon_role: QStyle.StandardPixmap, slot) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(self.style().standardIcon(icon_role))
        button.clicked.connect(slot)
        return button

    def get_playlist(self) -> List[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    # ------------------------------------------------------------------
    # Actions

    def add_with_loader(self) -> None:
        dlg = FileLoader(self)
        if dlg.exec() == QDialog.Accepted:
            file = dlg.get_selected_file()
            if file and os.path.exists(file):
                existing = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
                if file not in existing:
                    was_empty = self.list_widget.count() == 0
                    self.list_widget.addItem(file)
                    if was_empty:
                        self.list_widget.setCurrentRow(0)

    def remove_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)
            if self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(min(row, self.list_widget.count() - 1))

    def play_selected(self, item: Optional[QListWidgetItem] = None) -> None:
        if item is None:
            item = self.list_widget.currentItem()
        if not item:
            return
        path = item.text()
        if not (path and os.path.exists(path)):
            return
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if hasattr(app, "playlist"):
            app.playlist = self.get_playlist()
        self._started_playback = True
        self.play_callback(path)
        self.accept()

    def accept(self) -> None:
        from PySide6.QtWidgets import QApplication

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

    def save_playlist(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Playlist",
            str(Path.home() / "playlist.json"),
            "Nexa Playlist (*.json)",
        )
        if not path:
            return
        save_playlist(self.get_playlist(), Path(path))

    def load_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Playlist",
            str(Path.home()),
            "Nexa Playlist (*.json)",
        )
        if not path:
            return
        items = load_playlist(Path(path))
        self.list_widget.clear()
        for item in items:
            if os.path.exists(item):
                self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
