"""Logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, log_file: str | Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)

    handlers: list[logging.Handler] = []
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_FORMAT))
    handlers.append(stream)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        handlers.append(file_handler)

    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
