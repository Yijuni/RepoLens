"""脱敏、滚动的本地运行日志。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading


LOGGER_NAME = "repolens"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_configured = False
_lock = threading.Lock()


def log_directory() -> Path:
    """Return the per-user log directory without creating it."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "RepoLens" / "logs"
    return Path.home() / ".repolens" / "logs"


def configure_logging() -> Path | None:
    """Configure console and rotating file handlers once.

    A file permission failure must not stop the local server; console logging
    remains available in that case.
    """
    global _configured
    with _lock:
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if _configured:
            return next((Path(handler.baseFilename) for handler in logger.handlers
                         if isinstance(handler, RotatingFileHandler)), None)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)
        path = None
        directories = [log_directory(), Path.cwd() / ".repolens" / "logs"]
        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / "server.log"
                file_handler = RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                    encoding="utf-8", delay=False,
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
                break
            except (OSError, PermissionError):
                continue
        if path is None:
            logger.info("logging.file_unavailable")
        _configured = True
        return path


def get_logger() -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(LOGGER_NAME)


def error_text(exc: BaseException) -> str:
    """Return a bounded error description without request bodies or secrets."""
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return f"{type(exc).__name__}: {message[:240]}"
