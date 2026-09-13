"""Reader status protocol, backend side (PLAN-WINDOWS-NONTECHIE contract §2).

The reader-service POSTs its connection state to ``/reader/status`` every
couple of seconds and on every state change. This module owns everything the
backend does with those heartbeats:

- input sanitising (``ReaderStatusIn``): a malformed heartbeat must never
  break the endpoint, so every field except ``state`` degrades to ``None`` /
  empty instead of failing validation;
- the last status in memory with a monotonic receive time;
- SSE ``reader_status`` publishing (on change, plus a periodic refresh);
- staleness: no heartbeat for ``stale_after_s`` → effective state ``unknown``;
- the command queue delivered in heartbeat replies (``discover``/``reconnect``)
  and the rendezvous between ``POST /reader/discover`` and the discovery
  result that arrives in a later heartbeat.

It deliberately never imports ``app`` (the desktop shell loads the backend
under the module name ``racetag_backend_app``); everything it needs from the
app — the SSE publisher, the clock, the supervisor status — is injected.
"""
from __future__ import annotations

import collections
import logging
import math
import threading
from typing import Any, Callable, Deque, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from domain.config import is_valid_ipv4

logger = logging.getLogger("racetag.backend")

# States the reader-service may report. "unknown" is backend-only (staleness)
# and therefore rejected as input.
ReaderState = Literal["searching", "connecting", "configuring", "active", "lost", "stopped"]
STATE_UNKNOWN = "unknown"

STALE_AFTER_S = 6.0
REFRESH_INTERVAL_S = 5.0

DISCOVERY_ERROR_TIMEOUT = "timeout"
DISCOVERY_ERROR_UNAVAILABLE = "reader_service_unavailable"

_TARGET_SOURCES = {"cli", "config", "discovery"}
_CANDIDATE_SOURCES = {"arp", "sweep", "linklocal", "connected"}
# Sirit 510 has four ports; leave headroom for other readers without letting
# garbage numbers through.
_MAX_ANTENNA = 32
_MAX_CANDIDATES = 256
_MAX_SHORT_STR = 64
_MAX_ERROR_STR = 500

# Fields whose change triggers an immediate SSE frame (contract §2.2 rule 3).
# antenna_reads / last_event_at only ride along on the periodic refresh.
_CHANGE_FIELDS = ("state", "ip", "serial", "antennas", "error", "candidates",
                  "consecutive_failures")


# ---------------------------------------------------------------------------
# Sanitising helpers — each returns a clean value or None, never raises.
# ---------------------------------------------------------------------------

def _clean_str(value: Any, max_len: int) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return value[:max_len]


def _clean_int(value: Any, lo: int, hi: int) -> Optional[int]:
    # bool is an int subclass; True must not become antenna 1.
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    return value if lo <= value <= hi else None


def _clean_ipv4(value: Any) -> Optional[str]:
    text = _clean_str(value, _MAX_SHORT_STR)
    return text if text is not None and is_valid_ipv4(text) else None


def _clean_candidates(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[Dict[str, Any]] = []
    seen: set = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        ip = _clean_ipv4(item.get("ip"))
        if ip is None or ip in seen:
            continue
        seen.add(ip)
        source = _clean_str(item.get("source"), _MAX_SHORT_STR)
        result.append({
            "ip": ip,
            "serial": _clean_str(item.get("serial"), _MAX_SHORT_STR),
            "source": source if source in _CANDIDATE_SOURCES else None,
        })
        if len(result) >= _MAX_CANDIDATES:
            break
    return result


# ---------------------------------------------------------------------------
# Input DTOs
# ---------------------------------------------------------------------------

class ReaderCandidate(BaseModel):
    ip: str
    serial: Optional[str] = None
    source: Optional[str] = None


class DiscoveryResultIn(BaseModel):
    request_id: int
    candidates: List[ReaderCandidate] = []
    error: Optional[str] = None


class ReaderStatusIn(BaseModel):
    """Body of ``POST /reader/status`` (contract §2.1).

    Only ``state`` is strict (unknown values → 422). Every other field is
    sanitised in a ``before`` validator so a malformed value degrades to
    ``None`` / empty instead of rejecting the whole heartbeat.
    """

    model_config = ConfigDict(extra="ignore")

    state: ReaderState
    ip: Optional[str] = None
    target_source: Optional[str] = None
    serial: Optional[str] = None
    antennas: List[int] = []
    antenna_power: Optional[int] = None
    antenna_reads: Dict[str, int] = {}
    last_event_at: Optional[str] = None
    connected_since: Optional[str] = None
    error: Optional[str] = None
    consecutive_failures: int = 0
    next_retry_s: Optional[float] = None
    candidates: List[ReaderCandidate] = []
    discovered_ip: Optional[str] = None
    discovery: Optional[DiscoveryResultIn] = None
    reader_service_version: Optional[str] = None
    pid: Optional[int] = None

    @field_validator("ip", "serial", "last_event_at", "connected_since",
                     "reader_service_version", mode="before")
    @classmethod
    def _v_short_str(cls, v: Any) -> Optional[str]:
        return _clean_str(v, _MAX_SHORT_STR)

    @field_validator("error", mode="before")
    @classmethod
    def _v_error(cls, v: Any) -> Optional[str]:
        return _clean_str(v, _MAX_ERROR_STR)

    @field_validator("target_source", mode="before")
    @classmethod
    def _v_target_source(cls, v: Any) -> Optional[str]:
        text = _clean_str(v, _MAX_SHORT_STR)
        return text if text in _TARGET_SOURCES else None

    @field_validator("antennas", mode="before")
    @classmethod
    def _v_antennas(cls, v: Any) -> List[int]:
        if not isinstance(v, (list, tuple)):
            return []
        ports = {_clean_int(p, 1, _MAX_ANTENNA) for p in v}
        return sorted(p for p in ports if p is not None)

    @field_validator("antenna_power", mode="before")
    @classmethod
    def _v_antenna_power(cls, v: Any) -> Optional[int]:
        return _clean_int(v, 0, 10_000)

    @field_validator("antenna_reads", mode="before")
    @classmethod
    def _v_antenna_reads(cls, v: Any) -> Dict[str, int]:
        if not isinstance(v, dict):
            return {}
        reads: Dict[str, int] = {}
        for key, count in v.items():
            port = _clean_int(key, 1, _MAX_ANTENNA)
            clean_count = _clean_int(count, 0, 2**53)
            if port is not None and clean_count is not None:
                reads[str(port)] = clean_count
        return reads

    @field_validator("consecutive_failures", mode="before")
    @classmethod
    def _v_failures(cls, v: Any) -> int:
        return _clean_int(v, 0, 2**31) or 0

    @field_validator("next_retry_s", mode="before")
    @classmethod
    def _v_next_retry(cls, v: Any) -> Optional[float]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if math.isfinite(v) and v >= 0 else None

    @field_validator("candidates", mode="before")
    @classmethod
    def _v_candidates(cls, v: Any) -> List[Dict[str, Any]]:
        return _clean_candidates(v)

    @field_validator("discovered_ip", mode="before")
    @classmethod
    def _v_discovered_ip(cls, v: Any) -> Optional[str]:
        return _clean_ipv4(v)

    @field_validator("discovery", mode="before")
    @classmethod
    def _v_discovery(cls, v: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(v, dict):
            return None
        request_id = _clean_int(v.get("request_id"), 0, 2**63)
        if request_id is None:
            return None
        return {
            "request_id": request_id,
            "candidates": _clean_candidates(v.get("candidates")),
            "error": _clean_str(v.get("error"), _MAX_ERROR_STR),
        }

    @field_validator("pid", mode="before")
    @classmethod
    def _v_pid(cls, v: Any) -> Optional[int]:
        return _clean_int(v, 0, 2**32)


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------

def _empty_status() -> Dict[str, Any]:
    """Status fields before any heartbeat: unknown, everything null/empty."""
    return {
        "state": STATE_UNKNOWN,
        "ip": None,
        "target_source": None,
        "serial": None,
        "antennas": [],
        "antenna_power": None,
        "antenna_reads": {},
        "last_event_at": None,
        "connected_since": None,
        "error": None,
        "consecutive_failures": None,
        "next_retry_s": None,
        "candidates": [],
        "discovered_ip": None,
        "reader_service_version": None,
        "pid": None,
    }


class _DiscoveryRequest:
    """One queued ``discover`` command and everyone waiting for its result."""

    __slots__ = ("request_id", "done", "result", "waiters")

    def __init__(self, request_id: int) -> None:
        self.request_id = request_id
        self.done = threading.Event()
        self.result: Optional[Dict[str, Any]] = None
        self.waiters = 0


class ReaderStatusHub:
    """Thread-safe holder for the reader status and the command queue.

    Every public method may be called from any thread (sync FastAPI routes run
    in a worker pool; the staleness check runs on its own daemon thread).
    """

    def __init__(
        self,
        publish: Callable[[Dict[str, Any]], None],
        clock: Callable[[], float],
        now_iso: Callable[[], str],
        supervisor_status: Callable[[], Optional[Dict[str, Any]]],
        stale_after_s: float = STALE_AFTER_S,
        refresh_interval_s: float = REFRESH_INTERVAL_S,
    ) -> None:
        self._publish = publish
        self._clock = clock
        self._now_iso = now_iso
        self._supervisor_status = supervisor_status
        self._stale_after_s = stale_after_s
        self._refresh_interval_s = refresh_interval_s

        self._lock = threading.Lock()
        self._status: Optional[Dict[str, Any]] = None
        self._received_at: Optional[float] = None
        self._updated_at: Optional[str] = None
        self._stale = False
        self._published_key: Optional[tuple] = None
        self._published_at: Optional[float] = None

        self._next_command_id = 1
        self._commands: Deque[Dict[str, Any]] = collections.deque()
        self._discovery: Optional[_DiscoveryRequest] = None

    # -- heartbeat ingest --------------------------------------------------

    def ingest(self, body: ReaderStatusIn) -> Optional[Dict[str, Any]]:
        """Store a heartbeat, publish SSE as needed, wake a discovery waiter.

        Returns the command to deliver in the reply (removed from the queue),
        or ``None``. The ``stopped`` heartbeat is the reader-service's last
        words, so no command is handed to it — it stays queued for the next
        process instead of being silently lost.
        """
        data = body.model_dump()
        discovery = data.pop("discovery")
        supervisor = self._supervisor_status()

        with self._lock:
            now = self._clock()
            previous_state = self._effective_state_locked(now)
            self._status = data
            self._received_at = now
            self._updated_at = self._now_iso()
            self._stale = False

            if previous_state != data["state"]:
                logger.info(
                    "reader state %s -> %s (ip=%s, error=%s)",
                    previous_state, data["state"], data["ip"], data["error"],
                )

            if discovery is not None:
                self._resolve_discovery_locked(discovery)

            key = self._change_key(data)
            due_refresh = (
                self._published_at is None
                or now - self._published_at >= self._refresh_interval_s
            )
            if key != self._published_key or due_refresh:
                self._publish_locked(now, supervisor)

            if data["state"] == "stopped" or not self._commands:
                return None
            return self._commands.popleft()

    def _resolve_discovery_locked(self, discovery: Dict[str, Any]) -> None:
        pending = self._discovery
        if pending is None or pending.request_id != discovery["request_id"]:
            # A late result for a request whose waiters already timed out, or
            # one we never issued. Its candidates still reach the UI through
            # the status' own `candidates` field.
            logger.debug("ignoring discovery result for request %s", discovery["request_id"])
            return
        pending.result = {"candidates": discovery["candidates"], "error": discovery["error"]}
        pending.done.set()
        self._discovery = None

    # -- staleness ---------------------------------------------------------

    def check_stale(self) -> bool:
        """Flip to ``unknown`` when heartbeats stopped; publish once.

        Returns True only on the call that performed the transition.
        """
        supervisor = self._supervisor_status()
        with self._lock:
            now = self._clock()
            if self._status is None or self._stale:
                return False
            age = now - self._received_at
            if age < self._stale_after_s:
                return False
            self._stale = True
            logger.warning(
                "no reader-service heartbeat for %.1f s (last state %s); reader state unknown",
                age, self._status["state"],
            )
            self._publish_locked(now, supervisor)
            return True

    def is_unavailable(self) -> bool:
        with self._lock:
            return self._effective_state_locked(self._clock()) == STATE_UNKNOWN

    def _effective_state_locked(self, now: float) -> str:
        if self._status is None or self._stale:
            return STATE_UNKNOWN
        if now - self._received_at >= self._stale_after_s:
            return STATE_UNKNOWN
        return self._status["state"]

    # -- snapshot / publishing ---------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """The ``GET /reader/status`` body (contract §2.3)."""
        supervisor = self._supervisor_status()
        with self._lock:
            return self._snapshot_locked(self._clock(), supervisor)

    def _snapshot_locked(self, now: float, supervisor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if self._status is None:
            result = _empty_status()
            result.update(updated_at=None, age_s=None)
        else:
            result = dict(self._status)
            result["antennas"] = list(result["antennas"])
            result["antenna_reads"] = dict(result["antenna_reads"])
            result["candidates"] = [dict(c) for c in result["candidates"]]
            result["state"] = self._effective_state_locked(now)
            result["updated_at"] = self._updated_at
            result["age_s"] = round(max(0.0, now - self._received_at), 1)
        result["supervisor"] = supervisor
        return result

    def _publish_locked(self, now: float, supervisor: Optional[Dict[str, Any]]) -> None:
        # Published while holding the lock so a heartbeat and the staleness
        # thread can never emit their frames out of order. _publish only
        # schedules deliveries and never calls back into the hub.
        snapshot = self._snapshot_locked(now, supervisor)
        self._published_key = self._change_key(snapshot)
        self._published_at = now
        self._publish({"type": "reader_status", **snapshot})

    @staticmethod
    def _change_key(status: Dict[str, Any]) -> tuple:
        values = []
        for field in _CHANGE_FIELDS:
            value = status[field]
            if field == "antennas":
                value = tuple(value)
            elif field == "candidates":
                value = tuple((c["ip"], c["serial"], c["source"]) for c in value)
            values.append(value)
        return tuple(values)

    # -- commands ----------------------------------------------------------

    def _enqueue_locked(self, command_type: str) -> Dict[str, Any]:
        command = {"id": self._next_command_id, "type": command_type}
        self._next_command_id += 1
        self._commands.append(command)
        return command

    def queue_reconnect(self) -> None:
        """Queue a ``reconnect`` command unless one is already waiting."""
        with self._lock:
            if any(c["type"] == "reconnect" for c in self._commands):
                return
            self._enqueue_locked("reconnect")

    def request_discovery(self, timeout_s: float) -> Dict[str, Any]:
        """Queue ``discover`` and block (worker thread) until the result.

        Concurrent callers share one in-flight request instead of making the
        reader-service sweep the subnet several times in a row.

        Returns ``reader_service_unavailable`` at once when the status is
        ``unknown``, except while a reader-service is still starting up: no
        heartbeat yet but the supervisor reports its process as running. The
        command then waits in the queue for the first heartbeat, within the
        normal timeout, so "Reader suchen" right after launch does not tell
        the operator the service is down.
        """
        supervisor = self._supervisor_status()
        with self._lock:
            if self._effective_state_locked(self._clock()) == STATE_UNKNOWN:
                booting = (
                    self._status is None
                    and supervisor is not None
                    and supervisor.get("running") is True
                )
                if not booting:
                    return {"candidates": [], "error": DISCOVERY_ERROR_UNAVAILABLE}
            request = self._discovery
            if request is None:
                command = self._enqueue_locked("discover")
                request = _DiscoveryRequest(command["id"])
                self._discovery = request
            request.waiters += 1

        request.done.wait(timeout_s)

        with self._lock:
            request.waiters -= 1
            # Re-check under the lock: the result may have landed between the
            # wait timing out and us getting here.
            if request.done.is_set():
                return request.result
            if request.waiters == 0 and self._discovery is request:
                # Nobody is waiting any more; don't let a stale discover run
                # later if the command was never picked up.
                self._discovery = None
                self._commands = collections.deque(
                    c for c in self._commands if c["id"] != request.request_id
                )
            return {"candidates": [], "error": DISCOVERY_ERROR_TIMEOUT}
