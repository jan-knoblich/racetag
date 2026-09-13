"""Reader status heartbeat (plan B2, contract §2.1 / §3.2a).

POSTs the reader-service's connection state to ``{backend}/reader/status``
every ``interval_s`` and immediately on every state change. Latest state
wins: one attempt with a 1 s timeout, no retry, no spool. The reply carries
the backend's current reader config and at most one command; it is handed to
``on_reply`` on the reporter thread.

Deliberately separate from HttpBackendClient: that worker blocks for seconds
in retries and spool drains, and a heartbeat queued behind race data would
arrive stale (or end up spooled to disk).
"""
from __future__ import annotations

import copy
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from utils import get_logger

try:
    import requests
except Exception:  # pragma: no cover - requests is a declared dependency
    requests = None

logger = get_logger("reader.status")

__version__ = "0.2.0"

STATES = ("searching", "connecting", "configuring", "active", "lost", "stopped")

POST_TIMEOUT_S = 1.0
JOIN_TIMEOUT_S = 2.0
ERROR_LOG_INTERVAL_S = 60.0


def reader_service_version() -> str:
    """Version reported in the heartbeat: the shell's RACETAG_VERSION when
    set (single source of truth for bundled builds), else the module version."""
    return os.environ.get("RACETAG_VERSION") or __version__


def default_status() -> Dict[str, Any]:
    """A complete status body; every key of contract §2.1 is always present."""
    return {
        "state": "searching",
        "ip": None,
        "target_source": None,
        "serial": None,
        "antennas": [],
        "antenna_power": None,
        "antenna_reads": {},
        "last_event_at": None,
        "connected_since": None,
        "error": None,
        "consecutive_failures": 0,
        "next_retry_s": None,
        "candidates": [],
        "discovered_ip": None,
        "discovery": None,
        "reader_service_version": reader_service_version(),
        "pid": os.getpid(),
    }


class ReaderStatusReporter:
    """Thread-safe status holder plus heartbeat sender.

    ``url=None`` or ``interval_s <= 0`` makes the sender a no-op (mock
    transport, heartbeat disabled) while ``update``/``snapshot`` keep working,
    so the client's state machine is identical with and without a backend.
    """

    def __init__(
        self,
        url: Optional[str],
        token: Optional[str],
        interval_s: float = 2.0,
        on_reply: Optional[Callable[[dict], None]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self.url = url
        self.token = token
        self.interval_s = interval_s
        self.on_reply = on_reply
        if session_factory is None and requests is not None:
            session_factory = requests.Session
        self._session_factory = session_factory
        self._fields: Dict[str, Any] = default_status()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_sent: Dict[str, Any] = {}
        self._last_error_log = -math.inf
        self._monotonic = time.monotonic

    @property
    def enabled(self) -> bool:
        return bool(self.url) and self.interval_s > 0 and self._session_factory is not None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def update(self, **fields: Any) -> None:
        """Merge fields into the status. Unknown keys raise KeyError.

        Wakes the sender immediately when ``state`` changed or a ``discovery``
        result is set (the backend's POST /reader/discover waits for it).
        """
        wake = False
        with self._lock:
            for key, value in fields.items():
                if key not in self._fields:
                    raise KeyError(f"unknown status field: {key}")
                if key == "state":
                    if value not in STATES:
                        raise ValueError(f"invalid state: {value!r}")
                    if value != self._fields["state"]:
                        wake = True
                elif key == "discovery" and value is not None:
                    wake = True
                self._fields[key] = copy.deepcopy(value)
        if wake:
            self._wake.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._fields)

    def last_sent(self) -> Dict[str, Any]:
        """Body of the POST whose reply is currently being delivered to
        ``on_reply`` (only meaningful on the reporter thread)."""
        return self._last_sent

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reader-status", daemon=True)
        self._thread.start()
        logger.info("[STATUS] heartbeat -> %s every %.1fs", self.url, self.interval_s)

    def stop(self) -> None:
        """Mark the status ``stopped``, send one final best-effort POST and
        join the sender (at most JOIN_TIMEOUT_S)."""
        with self._lock:
            self._fields["state"] = "stopped"
            self._fields["next_retry_s"] = None
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        self._wake.set()
        thread.join(timeout=JOIN_TIMEOUT_S)
        self._thread = None

    # ------------------------------------------------------------------
    # Sender thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        session = self._session_factory()
        try:
            while not self._stop.is_set():
                # Clear before sending: an update that lands while the POST is
                # in flight re-arms the event and triggers the next POST at once.
                self._wake.clear()
                self._post(session, deliver_reply=True)
                self._wake.wait(self.interval_s)
            self._post(session, deliver_reply=False)  # final "stopped"
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _post(self, session: Any, deliver_reply: bool) -> None:
        payload = self.snapshot()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Key"] = str(self.token)
        try:
            resp = session.post(self.url, json=payload, headers=headers, timeout=POST_TIMEOUT_S)
        except Exception as exc:
            self._log_error("POST failed: %s", exc)
            return
        status_code = getattr(resp, "status_code", 0)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            self._log_error("POST rejected with HTTP %s", status_code)
            return
        if payload["discovery"] is not None:
            # A discovery result is delivered exactly once: clear it after a
            # successful POST unless a newer result replaced it meanwhile.
            with self._lock:
                if self._fields["discovery"] == payload["discovery"]:
                    self._fields["discovery"] = None
        if not deliver_reply or self.on_reply is None:
            return
        try:
            reply = resp.json()
        except Exception as exc:
            self._log_error("reply is not JSON: %s", exc)
            return
        if not isinstance(reply, dict):
            self._log_error("reply is not an object: %r", reply)
            return
        self._last_sent = payload
        try:
            self.on_reply(reply)
        except Exception as exc:
            self._log_error("reply handler failed: %s", exc)

    def _log_error(self, fmt: str, *args: Any) -> None:
        """Log heartbeat problems at most once per ERROR_LOG_INTERVAL_S: a
        backend that is down would otherwise add a line every 2 s."""
        now = self._monotonic()
        if now - self._last_error_log < ERROR_LOG_INTERVAL_S:
            return
        self._last_error_log = now
        logger.warning("[STATUS] " + fmt, *args)
