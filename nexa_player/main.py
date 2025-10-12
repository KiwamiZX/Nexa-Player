from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import NexaApp
from .logging_config import setup_logging


def run(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv)
    parser = argparse.ArgumentParser(prog="nexa-player")
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging.")
    parser.add_argument("media", nargs="?", help="Media file to open on startup.")
    args, unknown = parser.parse_known_args(argv[1:])

    # reconstruct sys.argv for Qt (exclude handled flags)
    qt_argv = [argv[0], *unknown]
    setup_logging(debug=args.debug)

    logging.getLogger(__name__).info("Starting Nexa Player")

    app = NexaApp(qt_argv)

    media_to_open = args.media
    if media_to_open and os.path.exists(media_to_open):
        app.open_path(media_to_open)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
