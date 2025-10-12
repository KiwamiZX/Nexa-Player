from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDir, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QFileSystemModel,
)

from ..services.thumbnails import ThumbnailListWorker

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}


class FileLoader(QDialog):
    """
    Custom dialog that supports both list and thumbnail views for video files.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load File - Nexa Player")
        self.resize(830, 560)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._thumb_cache: dict[str, QIcon] = {}
        self._thumb_thread: Optional[ThumbnailListWorker] = None

        layout = QVBoxLayout(self)

        app = QApplication.instance()
        self._settings = getattr(app, "settings", None)
        last_dir = (
            self._settings.value("last_dir", QDir.homePath(), type=str)
            if self._settings is not None
            else QDir.homePath()
        )

        self.path_edit = QLineEdit(last_dir)
        layout.addWidget(self.path_edit)

        toggles = QHBoxLayout()
        self.btn_list_view = QPushButton("List View")
        self.btn_grid_view = QPushButton("Grid View")
        self.btn_list_view.setCheckable(True)
        self.btn_grid_view.setCheckable(True)
        self.btn_list_view.setChecked(True)
        toggles.addWidget(self.btn_list_view)
        toggles.addWidget(self.btn_grid_view)
        toggles.addStretch()
        layout.addLayout(toggles)

        self.model = QFileSystemModel()
        self.model.setRootPath(QDir.rootPath())
        self.model.setNameFilters([f"*{ext}" for ext in VIDEO_EXTENSIONS])
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

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(160, 90))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setSpacing(12)
        self.grid.setWrapping(True)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(150, 110))
        self.grid.setFlow(QListView.LeftToRight)
        self.grid.setLayoutMode(QListView.Batched)
        self.grid.hide()
        self.grid.itemDoubleClicked.connect(self._on_grid_double_clicked)

        layout.addWidget(self.view)
        layout.addWidget(self.grid)

        btn_layout = QHBoxLayout()

        self.back_btn = QPushButton("Back")
        self.back_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        btn_layout.addWidget(self.back_btn)
        self.back_btn.clicked.connect(self.go_back)

        self.open_btn = QPushButton("Open")
        self.open_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.add_btn = QPushButton("Add to Playlist")
        self.add_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogNewFolder))
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))

        btn_layout.addWidget(self.open_btn)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.open_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.add_btn.clicked.connect(self.add_selected_to_playlist)

        self.selected_file: Optional[str] = None

        self.setStyleSheet(
            """
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
            """
        )

        self.btn_list_view.clicked.connect(lambda: self._switch_mode(True))
        self.btn_grid_view.clicked.connect(lambda: self._switch_mode(False))
        self.path_edit.returnPressed.connect(self.change_dir)

        self._populate_grid(last_dir)

    # ------------------------------------------------------------------
    # Slots

    def _stop_thumb_thread(self) -> None:
        if self._thumb_thread:
            self._thumb_thread.stop()
            self._thumb_thread = None

    def _switch_mode(self, list_mode: bool) -> None:
        self.btn_list_view.setChecked(list_mode)
        self.btn_grid_view.setChecked(not list_mode)
        if list_mode:
            self.grid.hide()
            self.view.show()
        else:
            self.view.hide()
            self.grid.show()
            current_path = self.path_edit.text() or QDir.homePath()
            self._populate_grid(current_path)

    def _populate_grid(self, folder_path: str) -> None:
        self.grid.clear()
        if not folder_path or not os.path.isdir(folder_path):
            return

        try:
            entries = os.listdir(folder_path)
        except Exception:  # pragma: no cover - defensive
            log.exception("Failed to list directory: %s", folder_path)
            entries = []

        entries.sort(key=lambda x: x.lower())
        for entry in entries:
            full_path = os.path.normpath(os.path.join(folder_path, entry))
            item = QListWidgetItem(entry)
            if os.path.isdir(full_path):
                icon = QIcon.fromTheme("folder")
                if icon.isNull():
                    icon = self.style().standardIcon(QStyle.SP_DirIcon)
                item.setIcon(icon)
                item.setData(Qt.UserRole, full_path)
                self.grid.addItem(item)
                continue

            if any(entry.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                placeholder = QIcon(self.style().standardIcon(QStyle.SP_FileIcon))
                item.setIcon(placeholder)
                item.setData(Qt.UserRole, full_path)
                self.grid.addItem(item)

        self._start_thumb_thread(folder_path)

    def _start_thumb_thread(self, folder_path: str) -> None:
        if self._thumb_thread:
            self._thumb_thread.stop()
            self._thumb_thread = None
        self._thumb_thread = ThumbnailListWorker(folder_path)
        self._thumb_thread.thumb_ready.connect(self._apply_item_thumb)
        self._thumb_thread.start()

    def _apply_item_thumb(self, file_path: str, image: QImage) -> None:
        if file_path in self._thumb_cache:
            return
        icon_size = self.grid.iconSize()
        if image.isNull():
            pix = QPixmap(icon_size.width(), icon_size.height())
            pix.fill(Qt.black)
        else:
            pix = QPixmap.fromImage(
                image.scaled(icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

        icon = QIcon(pix)
        normalized = os.path.normpath(file_path)
        self._thumb_cache[normalized] = icon
        log.debug("Applied thumbnail for %s (norm=%s, grid count=%s)", file_path, normalized, self.grid.count())
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            item_path = item.data(Qt.UserRole)
            log.debug("Comparing grid item path %s to %s", item_path, normalized)
            if os.path.normpath(item_path) == normalized:
                item.setIcon(icon)
                log.debug("Updated QListWidgetItem icon for %s (isNull=%s)", file_path, item.icon().isNull())
                break
        self.grid.viewport().update()

    def _save_last_dir(self, path: str) -> None:
        if self._settings is not None:
            self._settings.setValue("last_dir", path)

    def on_double_click(self, index) -> None:
        path = self.model.filePath(index)
        if QDir(path).exists():
            self.view.setRootIndex(self.model.index(path))
            self.path_edit.setText(path)
            self._save_last_dir(path)
            if self.btn_grid_view.isChecked():
                self._populate_grid(path)
        else:
            self.selected_file = path
            self._save_last_dir(os.path.dirname(path))
            self.accept()

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        if not item:
            return
        path = item.data(Qt.UserRole)
        if os.path.isdir(path):
            self.path_edit.setText(path)
            self._save_last_dir(path)
            self._populate_grid(path)
        else:
            self.selected_file = path
            self._save_last_dir(os.path.dirname(path))
            self.accept()

    def go_back(self) -> None:
        current_path = self.path_edit.text()
        parent = os.path.dirname(current_path)
        if os.path.exists(parent):
            self.view.setRootIndex(self.model.index(parent))
            self.path_edit.setText(parent)
            self._save_last_dir(parent)
            if self.btn_grid_view.isChecked():
                self._populate_grid(parent)

    def change_dir(self) -> None:
        path = self.path_edit.text()
        if os.path.isdir(path):
            self.view.setRootIndex(self.model.index(path))
            self._save_last_dir(path)
            if self.btn_grid_view.isChecked():
                self._populate_grid(path)

    def add_selected_to_playlist(self) -> None:
        app = QApplication.instance()
        if not hasattr(app, "add_to_playlist"):
            return

        paths = []
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                p = self.model.filePath(index)
                if os.path.isfile(p):
                    paths.append(p)
        else:
            item = self.grid.currentItem()
            if item:
                p = item.data(Qt.UserRole)
                if os.path.isfile(p):
                    paths.append(p)

        for path in paths:
            app.add_to_playlist(path)

    def get_selected_file(self) -> Optional[str]:
        if self.selected_file:
            return self.selected_file
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                return self.model.filePath(index)
        else:
            item = self.grid.currentItem()
            if item:
                return item.data(Qt.UserRole)
        return None

    def accept(self) -> None:
        if self.btn_list_view.isChecked():
            index = self.view.currentIndex()
            if index.isValid():
                path = self.model.filePath(index)
                if os.path.isdir(path):
                    self.view.setRootIndex(self.model.index(path))
                    self.path_edit.setText(path)
                    self._save_last_dir(path)
                    if self.btn_grid_view.isChecked():
                        self._populate_grid(path)
                    return
                self.selected_file = path
                self._save_last_dir(os.path.dirname(path))
        else:
            item = self.grid.currentItem()
            if item:
                path = item.data(Qt.UserRole)
                if os.path.isdir(path):
                    self.path_edit.setText(path)
                    self._save_last_dir(path)
                    self._populate_grid(path)
                    return
                self.selected_file = path
                self._save_last_dir(os.path.dirname(path))
        super().accept()
        self._stop_thumb_thread()

    def reject(self) -> None:
        self._stop_thumb_thread()
        super().reject()
