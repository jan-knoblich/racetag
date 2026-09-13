from __future__ import annotations

import logging
import logging.handlers
import os
import re
import socket
import sys
import threading
from datetime import datetime, timezone
from typing import Optional


_TRUTHY = {"1", "true", "yes", "y", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    """Interpret an environment variable as a boolean flag."""
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in _TRUTHY


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
    (spool file, reader.log).

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
# W-060 / A3: logging
# ---------------------------------------------------------------------------

READER_LOGGER_NAME = "reader"
LOG_FILE_NAME = "reader.log"
LOG_FILE_MAX_BYTES = 2 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5

_CONSOLE_FORMAT = "[%(asctime)s.%(msecs)03d] %(message)s"
_FILE_FORMAT = "[%(asctime)s.%(msecs)03d] [%(name)s] %(levelname)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Marker attribute on the handlers configure_logging() installs, so a
# reconfiguration removes exactly those and never handlers added by others
# (e.g. pytest's caplog on the root logger).
_HANDLER_MARK = "_racetag_reader_handler"

_logging_lock = threading.Lock()
_logging_configured = False


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


class _PlainFormatter(logging.Formatter):
    """Strips ANSI colour codes: message text may carry them (SiritClient
    colorize), and they are noise in reader.log or a non-TTY console."""

    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_ESCAPE_RE.sub("", super().format(record))


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
        return f"{col}{super().format(record)}{_C.RESET}"


def _stream_is_tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:  # closed stream, or an object without isatty()
        return False


def configure_logging(debug: Optional[bool] = None) -> logging.Logger:
    """(Re)build the handlers of the shared ``reader`` parent logger.

    Every reader-service logger is a child (``reader.sirit``, ``reader.main``,
    ...) without handlers of its own; records propagate to this parent, so
    there is exactly ONE console handler and ONE RotatingFileHandler per
    process. One handler per file matters on Windows, where a second open
    handle on reader.log makes rotation fail.

    - Console handler only when ``sys.stdout`` exists: a windowed exe (and its
      children) has ``sys.stdout is None``, and ``StreamHandler(None)`` would
      silently fall back to a non-existent stderr.
    - File handler ``resolve_log_dir()/reader.log`` (2 MB x 5) is always on
      unless ``RACETAG_FILE_LOG=0``; a non-writable directory is ignored.
    - Level INFO, DEBUG when ``debug`` is true (default: ``RACETAG_DEBUG``).
      Child loggers keep level NOTSET, so raising the parent level here also
      raises loggers that were created at import time (``--debug``).
    """
    global _logging_configured
    with _logging_lock:
        if debug is None:
            debug = env_flag("RACETAG_DEBUG")
        level = logging.DEBUG if debug else logging.INFO
        parent = logging.getLogger(READER_LOGGER_NAME)
        for handler in list(parent.handlers):
            if getattr(handler, _HANDLER_MARK, False):
                parent.removeHandler(handler)
                handler.close()
        parent.setLevel(level)

        stdout = sys.stdout
        if stdout is not None:
            console = logging.StreamHandler(stdout)
            if _stream_is_tty(stdout):
                console.setFormatter(_ColorFormatter(fmt=_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
            else:
                console.setFormatter(_PlainFormatter(fmt=_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
            setattr(console, _HANDLER_MARK, True)
            parent.addHandler(console)

        if env_flag("RACETAG_FILE_LOG", True):
            try:
                log_dir = resolve_log_dir()
                os.makedirs(log_dir, exist_ok=True)
                file_handler = logging.handlers.RotatingFileHandler(
                    os.path.join(log_dir, LOG_FILE_NAME),
                    maxBytes=LOG_FILE_MAX_BYTES,
                    backupCount=LOG_FILE_BACKUP_COUNT,
                    encoding="utf-8",
                    delay=True,
                )
                file_handler.setFormatter(_PlainFormatter(fmt=_FILE_FORMAT, datefmt=_DATE_FORMAT))
                setattr(file_handler, _HANDLER_MARK, True)
                parent.addHandler(file_handler)
            except OSError:
                pass  # Non-fatal: file logging is best-effort

        _logging_configured = True
        return parent


def get_logger(name: str) -> logging.Logger:
    """Return a reader-service logger (use names below ``reader.``).

    The handlers live on the ``reader`` parent (see configure_logging); the
    first call configures them from the environment. ``propagate`` stays on,
    so pytest's caplog keeps working.
    """
    if not _logging_configured:
        configure_logging()
    return logging.getLogger(name)


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


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with milliseconds and a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Sockets (B1)
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT_S = 3.0


class ConnectError(Exception):
    """A reader socket could not be opened. The message is a short English
    reason suitable for the status ``error`` field, e.g. "CONTROL connect timeout"."""


# Windows IPPROTO_TCP option (ws2ipdef.h), not exposed by the socket module:
# seconds of unacknowledged retransmission before the connection is aborted.
_WIN_TCP_MAXRT = 5
# Windows' default (TcpMaxDataRetransmissions = 5) can abort a connection with
# an unacknowledged probe after roughly 20 s on a LAN, which would undo the
# liveness-probe rule that keeps a connection open through short outages.
RETRANSMIT_TIMEOUT_S = 60


def enable_tcp_keepalive(sock: socket.socket, idle_s: int = 15, interval_s: int = 5, count: int = 8) -> None:
    """Turn on TCP keepalive so a dead peer surfaces as a socket error within
    idle + interval * count seconds (40-55 s after the link went quiet, since
    the idle timer also runs while the link is healthy) instead of a recv()
    that blocks forever on a half-open connection.

    Deliberately not aggressive: during a short outage (cable wobble, switch
    port renegotiating) the reader keeps its unsent passes in TCP and delivers
    them once the link is back, but only while our sockets stay open. A
    rebooted reader is still noticed at once through the RST it answers the
    next liveness probe with.

    Every option is looked up with getattr and applied best-effort: the set of
    constants differs per platform and Python build (Linux TCP_KEEPIDLE,
    macOS TCP_KEEPALIVE, Windows SIO_KEEPALIVE_VALS; Windows 10 1709+ also
    accepts TCP_KEEPIDLE/TCP_KEEPCNT; without TCP_KEEPCNT Windows sends 10
    probes).
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    if sys.platform == "win32":
        sio_keepalive = getattr(socket, "SIO_KEEPALIVE_VALS", None)
        if sio_keepalive is not None:
            try:
                sock.ioctl(sio_keepalive, (1, int(idle_s * 1000), int(interval_s * 1000)))
            except (OSError, ValueError, AttributeError):
                pass
        try:
            sock.setsockopt(socket.IPPROTO_TCP, _WIN_TCP_MAXRT, RETRANSMIT_TIMEOUT_S)
        except OSError:
            pass
    for option_name, value in (
        ("TCP_KEEPIDLE", idle_s),
        ("TCP_KEEPALIVE", idle_s),  # macOS spelling of TCP_KEEPIDLE
        ("TCP_KEEPINTVL", interval_s),
        ("TCP_KEEPCNT", count),
    ):
        option = getattr(socket, option_name, None)
        if option is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass


def connect_socket(ip: str, port: int, name: str, timeout_s: float = CONNECT_TIMEOUT_S) -> socket.socket:
    """Open a TCP connection to the reader with a bounded connect timeout.

    Returns a blocking socket with TCP keepalive enabled. Raises ConnectError
    on failure; the OS default connect timeout (~21 s on Windows, over a
    minute on macOS/Linux) would otherwise stall the reconnect loop.
    """
    logger = get_logger("reader.utils")
    logger.debug("Connecting to %s at %s:%d (timeout %.1fs)...", name, ip, port, timeout_s)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout_s)
        s.connect((ip, port))
        s.settimeout(None)
    except ConnectionRefusedError:
        s.close()
        raise ConnectError(f"{name} connection refused") from None
    except TimeoutError:
        s.close()
        raise ConnectError(f"{name} connect timeout") from None
    except OSError as e:
        s.close()
        raise ConnectError(f"{name} connect failed: {e}") from None
    enable_tcp_keepalive(s)
    logger.info("%s connected to %s:%d.", name, ip, port)
    return s
