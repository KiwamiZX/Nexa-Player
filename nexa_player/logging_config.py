import logging
from pathlib import Path

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

    logging.getLogger(__name__).debug("Logging initialised (console only)")
    return Path.cwd() / LOG_DIR_NAME / LOG_FILE_NAME
