from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, List

log = logging.getLogger(__name__)


def save_playlist(paths: Iterable[str], destination: Path) -> None:
    payload = {"paths": list(paths)}
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Playlist saved to %s", destination)


def load_playlist(source: Path) -> List[str]:
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to read playlist: %s", source)
        return []
    if not isinstance(data, dict):
        return []
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return []
    return [p for p in paths if isinstance(p, str)]
