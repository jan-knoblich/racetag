"""Always-on rotating file logs for the desktop shell (plan A3).

Layout under ``log_dir`` (normally ``~/.racetag/logs``):

- ``shell.log``   root logger: the desktop shell itself (supervisor, dialogs,
                  watchdog, startup line).
- ``backend.log`` ``racetag.backend``, ``racetag.snapshots`` and the uvicorn
                  loggers. They do not propagate to the root, so backend noise
                  stays out of ``shell.log``.
- ``reader.log``  written by the reader-service child itself (not here).

A windowed Windows exe (``console=False``) has ``sys.stdout is None``; a
console handler is only attached when a stream actually exists, and nothing in
this module calls ``isatty()``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

BACKEND_LOGGERS = (
    "racetag.backend",
    "racetag.snapshots",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(threadName)s: %(message)s"

# Marker attribute on every handler this module installs, so a second
# setup_logging() call (self-test after a normal start, repeated tests)
# replaces them instead of stacking duplicates.
_MARKER = "_racetag_desktop_handler"


def _log_level() -> int:
    raw = os.environ.get("RACETAG_DEBUG", "").strip().lower()
    return logging.DEBUG if raw in {"1", "true", "yes", "y", "on"} else logging.INFO


def _file_handler(path: Path, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _MARKER, True)
    return handler


def _console_handler(level: int) -> Optional[logging.Handler]:
    stream = sys.stdout
    if stream is None:
        return None
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _MARKER, True)
    return handler


def _remove_own_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _MARKER, False):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001 - closing a broken stream must not fail setup
                pass


def setup_logging(log_dir: Path) -> None:
    """Route shell logs to ``shell.log`` and backend/uvicorn logs to ``backend.log``.

    Idempotent: handlers installed by an earlier call are closed and replaced.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    level = _log_level()

    root = logging.getLogger()
    _remove_own_handlers(root)
    root.setLevel(level)
    root.addHandler(_file_handler(log_dir / "shell.log", level))
    console = _console_handler(level)
    if console is not None:
        root.addHandler(console)

    # One shared backend.log handler: several RotatingFileHandlers on the same
    # file would rotate independently and clobber each other.
    backend_file = _file_handler(log_dir / "backend.log", level)
    backend_console = _console_handler(level)
    for name in BACKEND_LOGGERS:
        logger = logging.getLogger(name)
        _remove_own_handlers(logger)
        logger.setLevel(level)
        logger.propagate = False
        logger.addHandler(backend_file)
        if backend_console is not None:
            logger.addHandler(backend_console)


def uvicorn_log_config() -> None:
    """Value for ``uvicorn.Config(log_config=...)``.

    Always ``None``: uvicorn's default dict config installs its
    ``ColourizedFormatter``, which calls ``sys.stdout.isatty()`` and crashes the
    server thread in a windowed exe. With ``None`` uvicorn leaves logging alone
    and its loggers use the handlers from :func:`setup_logging`.
    """
    return None
