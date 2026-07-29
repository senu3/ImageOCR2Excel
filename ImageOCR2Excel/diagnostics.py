from __future__ import annotations

import logging
import os
import platform
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from ImageOCR2Excel import __version__


LOGGER_NAME = "ImageOCR2Excel"
LOG_FILE_NAME = "app.log"
DEFAULT_MAX_BYTES = 1 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3

_active_log_path: Path | None = None
_original_sys_excepthook = sys.excepthook
_original_threading_excepthook = getattr(threading, "excepthook", None)

_package_logger = logging.getLogger(LOGGER_NAME)
_package_logger.addHandler(logging.NullHandler())
_package_logger.propagate = False


def default_app_data_dir(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    app_directory_name: str = "ImageOCR2Excel",
) -> Path:
    values = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    local_app_data = values.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
    return base / app_directory_name


def default_log_dir(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    app_directory_name: str = "ImageOCR2Excel",
) -> Path:
    return default_app_data_dir(environ, home, app_directory_name) / "logs"


def current_log_path() -> Path:
    return _active_log_path or default_log_dir() / LOG_FILE_NAME


def ensure_log_directory() -> Path:
    directory = current_log_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def configure_logging(
    log_dir: Path | None = None,
    *,
    app_directory_name: str = "ImageOCR2Excel",
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    """Configure the application logger without making startup depend on AppData."""

    global _active_log_path
    requested_dir = (
        Path(log_dir)
        if log_dir is not None
        else default_log_dir(app_directory_name=app_directory_name)
    )

    def create_handler(directory: Path) -> tuple[Path, RotatingFileHandler]:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILE_NAME
        return path, RotatingFileHandler(
            path,
            maxBytes=max(1, int(max_bytes)),
            backupCount=max(0, int(backup_count)),
            encoding="utf-8",
            delay=False,
        )

    try:
        log_path, file_handler = create_handler(requested_dir)
    except OSError:
        requested_dir = (
            Path(tempfile.gettempdir()) / app_directory_name / "logs"
        )
        log_path, file_handler = create_handler(requested_dir)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing_handler in tuple(logger.handlers):
        if getattr(existing_handler, "_image_ocr_managed", False):
            logger.removeHandler(existing_handler)
            existing_handler.close()

    file_handler._image_ocr_managed = True  # type: ignore[attr-defined]
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | "
            "%(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    _active_log_path = log_path
    logger.info(
        "Application started | version=%s | python=%s | platform=%s",
        __version__,
        platform.python_version(),
        platform.platform(),
    )
    return log_path


def shutdown_logging() -> None:
    global _active_log_path
    logger = logging.getLogger(LOGGER_NAME)
    for handler in tuple(logger.handlers):
        if getattr(handler, "_image_ocr_managed", False):
            logger.removeHandler(handler)
            handler.close()
    _active_log_path = None


def install_exception_hooks() -> None:
    """Record otherwise invisible main-thread and worker-thread failures."""

    logger = logging.getLogger(LOGGER_NAME)

    def report_main_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            _original_sys_excepthook(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled main-thread exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def report_thread_exception(args) -> None:
        if args.exc_type is SystemExit:
            return
        logger.critical(
            "Unhandled worker-thread exception | thread=%s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = report_main_exception
    if _original_threading_excepthook is not None:
        threading.excepthook = report_thread_exception

