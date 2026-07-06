from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from datetime import datetime, timezone
from typing import Optional


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class _C:
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"
    GREEN = "\x1b[32m"
    RED = "\x1b[31m"
    CYAN = "\x1b[36m"
    YELLOW = "\x1b[33m"


def _color(s: str, col: str) -> str:
    return f"{col}{s}{_C.RESET}"


# ---------------------------------------------------------------------------
# Writable log/spool directory (AUDIT-2026-07 H1)
# ---------------------------------------------------------------------------

def resolve_log_dir() -> str:
    """Absolute, writable directory for the reader-service's disk artefacts
    (spool file, debug log).

    Previously these paths were CWD-relative ("logs/..."), which meant the
    crash-recovery spool silently vanished depending on how the app was
    launched: a Finder-launched .app has CWD "/" (read-only APFS system
    volume), so the spool write failed and the batch was DROPPED.

    Resolution order:
    1. RACETAG_LOG_DIR env var (set by the desktop shell so parent and any
       future tooling agree on the location),
    2. ~/.racetag/logs — same root as the backend's RACETAG_DATA_DIR default,
       guaranteed writable for the current user.
    """
    d = os.environ.get("RACETAG_LOG_DIR")
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".racetag", "logs")
    return d


# ---------------------------------------------------------------------------
# W-060: structured logging helper
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a logger configured for the reader service.

    Reads RACETAG_DEBUG env var (default off) to choose log level.
    Writes DEBUG entries to logs/reader.log when debug is enabled.
    Uses ANSI colour on the console only when stdout is a TTY.
    """
    logger = logging.getLogger(name)

    # Only configure handlers once (idempotent across multiple calls)
    if logger.handlers:
        return logger

    debug_mode = os.getenv("RACETAG_DEBUG", "").strip().lower() in {"1", "true", "yes", "y", "on"}
    level = logging.DEBUG if debug_mode else logging.INFO
    logger.setLevel(level)

    # --- console handler ---
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)

    use_color = sys.stdout.isatty()
    if use_color:
        class _ColorFormatter(logging.Formatter):
            _COLORS = {
                logging.DEBUG: _C.DIM,
                logging.INFO: _C.RESET,
                logging.WARNING: _C.YELLOW,
                logging.ERROR: _C.RED,
                logging.CRITICAL: _C.RED,
            }

            def format(self, record: logging.LogRecord) -> str:
                col = self._COLORS.get(record.levelno, _C.RESET)
                msg = super().format(record)
                return f"{col}{msg}{_C.RESET}"

        formatter: logging.Formatter = _ColorFormatter(
            fmt="[%(asctime)s.%(msecs)03d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="[%(asctime)s.%(msecs)03d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # --- file handler (debug only) ---
    if debug_mode:
        try:
            log_dir = resolve_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "reader.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                fmt="[%(asctime)s.%(msecs)03d] [%(name)s] %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(fh)
        except OSError:
            pass  # Non-fatal: file logging is best-effort

    return logger


# ---------------------------------------------------------------------------
# W-030: timestamp helpers
# ---------------------------------------------------------------------------

def parse_reader_time(s: str) -> str:
    """Parse a reader-supplied timestamp and return an ISO-8601 UTC string with Z suffix.

    Supported input forms:
      - Naive:       ``2026-04-15T15:15:04.403``   (assumed UTC — reader is configured to UTC)
      - Z-suffixed:  ``2026-04-15T15:15:04.403Z``
      - +00:00 form: ``2026-04-15T15:15:04.403+00:00``
    """
    s = s.strip()
    # Strip Z or +00:00 to get the naive form; we always treat the value as UTC.
    if s.endswith("Z"):
        s = s[:-1]
    elif s.endswith("+00:00"):
        s = s[:-6]

    # Parse naive string — try with and without fractional seconds.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except ValueError:
            continue

    # Fallback: return as-is with Z appended (best-effort for unexpected formats)
    return s + "Z"


def connect_socket(ip: str, port: int, name: str) -> Optional[socket.socket]:
    logger = get_logger("reader.utils")
    logger.info("Connecting to %s at %s:%d...", name, ip, port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(None)
        s.connect((ip, port))
    except ConnectionRefusedError:
        logger.error("%s connection refused at %s:%d.", name, ip, port)
        return None
    except TimeoutError:
        logger.error("%s connect timeout to %s:%d.", name, ip, port)
        return None
    except OSError as e:
        logger.error("%s failed to connect to %s:%d: %s", name, ip, port, e)
        return None
    logger.info("%s connected.", name)
    return s
