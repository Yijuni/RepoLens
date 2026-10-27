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
PROJECT_ROOT = Path(__file__).resolve().parents[4]
_configured = False
_lock = threading.Lock()


def log_directory() -> Path:
    """Return the default project-local log directory without creating it."""
    return PROJECT_ROOT / "logs"


def _configured_directory(log_dir: str | Path | None) -> Path:
    value = log_dir
    if value is None:
        value = os.environ.get("REPOLENS_LOG_DIR")
    if value is None or not str(value).strip():
        return log_directory()
    return Path(os.path.expandvars(str(value))).expanduser()


def _file_handler(logger: logging.Logger) -> RotatingFileHandler | None:
    return next((handler for handler in logger.handlers
                 if isinstance(handler, RotatingFileHandler)), None)


def configure_logging(log_dir: str | Path | None = None) -> Path | None:
    """Configure console and rotating file handlers once.

    A file permission failure must not stop the local server; console logging
    remains available in that case.
    """
    global _configured
    with _lock:
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        requested = _configured_directory(log_dir)
        requested_resolved = requested.resolve()
        current = _file_handler(logger)
        if _configured and current and (log_dir is None or
                                       Path(current.baseFilename).parent == requested_resolved):
            return Path(current.baseFilename) if current else None
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        if not _configured:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(formatter)
            logger.addHandler(console)
        elif current:
            logger.removeHandler(current)
            current.close()
        path = None
        directories = [requested]
        default = log_directory()
        if default.resolve() != requested_resolved:
            directories.append(default)
        legacy = Path.cwd() / ".repolens" / "logs"
        if legacy not in directories:
            directories.append(legacy)
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
            logger.warning("logging.file_unavailable requested=%s", requested)
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
