from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings


class StateStore:
    """
    Wrapper around QSettings for typed access.
    """

    KEY_VOLUME = "audio/volume"
    KEY_RATE = "playback/rate"
    KEY_MINI = "ui/miniplayer_enabled"
    KEY_ASPECT = "video/aspect_ratio"
    KEY_LAST_PLAYLIST = "playlist/last_paths"
    KEY_LAST_POSITIONS = "playback/last_positions"
    KEY_LAST_FILE = "playback/last_file"

    def __init__(self) -> None:
        self.settings = QSettings("Nexa Player", "Player")

    # --- volume ---------------------------------------------------------
    def get_volume(self) -> int:
        return self.settings.value(self.KEY_VOLUME, 80, type=int)

    def set_volume(self, volume: int) -> None:
        self.settings.setValue(self.KEY_VOLUME, volume)

    # --- playback rate --------------------------------------------------
    def get_rate(self) -> float:
        return float(self.settings.value(self.KEY_RATE, 1.0, type=float))

    def set_rate(self, rate: float) -> None:
        self.settings.setValue(self.KEY_RATE, rate)

    # --- mini player ----------------------------------------------------
    def get_miniplayer_enabled(self) -> bool:
        return self.settings.value(self.KEY_MINI, True, type=bool)

    def set_miniplayer_enabled(self, enabled: bool) -> None:
        self.settings.setValue(self.KEY_MINI, enabled)

    # --- aspect ratio ---------------------------------------------------
    def get_aspect_ratio(self) -> str:
        return self.settings.value(self.KEY_ASPECT, "", type=str)

    def set_aspect_ratio(self, ratio: str) -> None:
        self.settings.setValue(self.KEY_ASPECT, ratio)

    # --- playlist -------------------------------------------------------
    def get_last_playlist(self) -> List[str]:
        values = self.settings.value(self.KEY_LAST_PLAYLIST, [], type=list)
        return [p for p in values if isinstance(p, str)]

    def set_last_playlist(self, items: List[str]) -> None:
        self.settings.setValue(self.KEY_LAST_PLAYLIST, items)

    def get_last_file(self) -> Optional[str]:
        return self.settings.value(self.KEY_LAST_FILE, "", type=str) or None

    def set_last_file(self, path: Optional[str]) -> None:
        self.settings.setValue(self.KEY_LAST_FILE, path or "")

    # --- resume positions ------------------------------------------------
    def get_resume_positions(self) -> Dict[str, int]:
        raw = self.settings.value(self.KEY_LAST_POSITIONS, "", type=str)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {k: int(v) for k, v in data.items()}

    def set_resume_position(self, path: str, position_ms: int) -> None:
        data = self.get_resume_positions()
        data[path] = position_ms
        self.settings.setValue(self.KEY_LAST_POSITIONS, json.dumps(data))

    def clear_resume_position(self, path: str) -> None:
        data = self.get_resume_positions()
        if path in data:
            del data[path]
            self.settings.setValue(self.KEY_LAST_POSITIONS, json.dumps(data))
