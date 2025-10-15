import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "nexa_player.log"

def setup_logging(debug: bool = False) -> Path:
    level = logging.DEBUG if debug else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Determine a non-root, platform-specific log directory (prefer per-user app data)
    try:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            # Use XDG_STATE_HOME if available, otherwise ~/.local/state
            base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        log_dir = base / "NexaPlayer" / LOG_DIR_NAME
    except Exception:
        log_dir = Path.cwd() / LOG_DIR_NAME

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_dir / LOG_FILE_NAME
        file_handler = RotatingFileHandler(str(file_path), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception:
        root_logger.exception("Failed to create file log handler")

    logging.getLogger(__name__).debug("Logging initialised (console + file)")
    return file_path
