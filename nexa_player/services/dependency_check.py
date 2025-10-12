from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QWidget

from ..ui.dependency_dialog import DependencyDialog

log = logging.getLogger(__name__)


class DependencyChecker:
    """
    Validates runtime dependencies (VLC + FFmpeg) and gives the user an
    opportunity to locate them when missing.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._parent = parent if isinstance(parent, QWidget) else None
        self._settings = QSettings("Nexa Player", "Player")

    def _apply_saved_vlc_path(self) -> None:
        saved = self._settings.value("vlc/last_dir", "", str)
        if not saved:
            return
        path = Path(saved)
        if path.exists():
            self._register_vlc_path(path)

    def ensure(self) -> Optional["vlc.Instance"]:
        """
        Perform dependency checks. Returns a ready VLC instance or None if the
        user cancels / requirements remain unsatisfied.
        """
        self._apply_saved_vlc_path()
        vlc_instance = self._ensure_vlc()
        if vlc_instance is None:
            return None

        if not self._ensure_ffmpeg():
            return None

        return vlc_instance

    def _ensure_vlc(self):
        while True:
            vlc_module = self._import_vlc()
            if vlc_module is None:
                return None

            try:
                return vlc_module.Instance("--no-osd", "--no-video-title-show")
            except OSError:
                log.warning("Unable to initialise VLC runtime", exc_info=True)
                lib_path = self._ask_for_vlc_folder()
                if not lib_path:
                    return None
                self._register_vlc_path(lib_path)
                importlib.invalidate_caches()
                sys.modules.pop("vlc", None)

    def _import_vlc(self):
        manual_attempt = False
        while True:
            try:
                return importlib.import_module("vlc")
            except ImportError:
                log.exception("python-vlc not installed")
                DependencyDialog.show_python_bindings_warning(self._parent)
                return None
            except Exception:  # pylint: disable=broad-except
                log.warning("python-vlc failed to load; prompting for VLC folder", exc_info=True)
                if manual_attempt:
                    DependencyDialog.show_invalid_vlc_folder(self._parent)
                lib_path = self._ask_for_vlc_folder()
                if not lib_path:
                    return None
                self._register_vlc_path(lib_path)
                importlib.invalidate_caches()
                sys.modules.pop("vlc", None)
                manual_attempt = True

    def _ensure_ffmpeg(self) -> bool:
        if shutil.which("ffmpeg"):
            return True

        log.warning("ffmpeg command not found on PATH")
        response = QMessageBox.question(
            self._parent,
            "FFmpeg not found",
            "FFmpeg was not found on your PATH. Thumbnails and scrubbing previews "
            "will not work without it.\n\nDo you want to continue anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return response == QMessageBox.Yes

    def _ask_for_vlc_folder(self) -> Optional[Path]:
        saved = self._settings.value("vlc/last_dir", "", str)
        default_dir = Path(saved) if saved else self._default_vlc_dir()
        return DependencyDialog.ask_for_vlc_folder(self._parent, default_dir)

    def _register_vlc_path(self, path: Path) -> None:
        log.info("Registering VLC path: %s", path)
        path = path.resolve()
        if platform.system().lower() == "windows":
            os.environ["PATH"] = f"{path};{os.environ.get('PATH', '')}"
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(path))
                except OSError:
                    log.exception("Failed to register DLL directory", exc_info=True)
        else:
            os.environ["LD_LIBRARY_PATH"] = (
                f"{path}:{os.environ.get('LD_LIBRARY_PATH', '')}"
            )
        sys.path.append(str(path))
        if path.exists():
            self._settings.setValue("vlc/last_dir", str(path))
            self._settings.sync()

    @staticmethod
    def _default_vlc_dir() -> Optional[Path]:
        system = platform.system().lower()
        potential = []
        if system == "windows":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            potential.append(Path(program_files) / "VideoLAN" / "VLC")
        elif system == "darwin":
            potential.append(Path("/Applications/VLC.app/Contents/MacOS/lib"))
        else:
            potential.extend(
                Path(p) for p in ("/usr/lib", "/usr/local/lib", "/snap/vlc/current/lib")
            )

        for candidate in potential:
            if candidate.exists():
                return candidate
        return None
