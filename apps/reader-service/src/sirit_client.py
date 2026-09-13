from __future__ import annotations

import ipaddress
import logging
import math
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discovery
from backend_client import BackendClient, HttpBackendClient, MockBackendClient
from models import EventType, TagEvent
from session_state import SessionState
from status_reporter import ReaderStatusReporter
from tag_tracker import TagTracker
from utils import (
    CONNECT_TIMEOUT_S,
    ConnectError,
    _C,
    _color,
    connect_socket,
    get_logger,
    parse_reader_time,
    utc_now_iso,
)

logger = get_logger("reader.sirit")

# Wait between reconnect attempts; the last value repeats. Capped at 2 s
# (deviation from contract §3.2's 1, 2, 5, 10): an attempt on the reader LAN
# costs one SYN, and every second of back-off after the reader is reachable
# again is a second in which passes are lost. The log throttle in
# _after_failure keeps a long outage from flooding reader.log.
DEFAULT_BACKOFF_S: Tuple[float, ...] = (1.0, 2.0)

# Outcomes of one connection attempt, consumed by the connection thread.
_STOPPED = "stopped"      # stop requested
_RECONNECT = "reconnect"  # a request wants a fresh connection now (no back-off)
_FAILED = "failed"        # sockets never came up
_LOST = "lost"            # sockets were up (configuring/active) and dropped


class ControlSendError(OSError):
    """A CONTROL command could not be sent: no socket, a dead socket, or the
    caller belongs to a connection that has already been replaced."""


class _MessageThrottle:
    """Rate-limit repeated log lines by key (2026-07-25 log-noise fix).

    The reader emits event.warning.antenna / event.error.antenna
    continuously for powered ports whose antenna check is unhappy — by
    design we power the fallback ports even when detection is unsure (union
    semantics), so these warnings are EXPECTED and would otherwise flood the
    log many times per second. First occurrence per key logs immediately;
    repeats within the window are counted and surface as one summary line
    when the window rolls over.
    """

    def __init__(self, window_s: float = 60.0, clock=time.monotonic):
        self.window_s = window_s
        self.clock = clock
        # key -> [window_start, suppressed_count]
        self._state: Dict[str, list] = {}

    def check(self, key: str) -> tuple:
        """Return (log_now, suppressed_since_last_log)."""
        now = self.clock()
        st = self._state.get(key)
        if st is None or now - st[0] >= self.window_s:
            suppressed = st[1] if st else 0
            self._state[key] = [now, 0]
            return True, suppressed
        st[1] += 1
        return False, 0


@dataclass
class _Connection:
    """One CONTROL + EVENT socket pair and everything tied to its lifetime."""

    generation: int
    ip: str
    control: socket.socket
    event: socket.socket
    threads: List[threading.Thread] = field(default_factory=list)
    lost: threading.Event = field(default_factory=threading.Event)
    reason: Optional[str] = None
    closed: bool = False


def _close_socket(sock: Optional[socket.socket]) -> None:
    """shutdown() before close(): on Linux only shutdown reliably unblocks a
    recv() that another thread is blocked in."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _is_ipv4(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


class SiritClient:
    """Sirit INfinity 510 client with a self-healing connection (plan B1).

    ``start()`` returns immediately; a connection thread owns the sockets and
    walks the states searching -> connecting -> configuring -> active, and
    back through lost/connecting with back-off whenever the link drops. The
    state is published through a ReaderStatusReporter (plan B2), whose reply
    carries config changes and commands back to the connection thread.
    """

    def __init__(
        self,
        ip: Optional[str],
        control_port: int,
        event_port: int,
        init_commands_path: Optional[str],
        colorize: bool,
        raw: bool,
        interactive: bool,
        backend_url: Optional[str] = None,
        backend_token: Optional[str] = None,
        backend_transport: str = "http",
        min_lap_interval_s: float = 10.0,
        antenna_power: int = 300,
        antenna_ports_fallback: str = "1 2",
        heartbeat_interval_s: float = 2.0,
        discover_after_failures: int = 6,
        discover_interval_s: float = 60.0,
        discovery_enabled: bool = True,
        clock_resync_interval_s: float = 1800.0,
    ):
        self.ip: Optional[str] = ip or None
        self.target_source: Optional[str] = "cli" if self.ip else None
        self.control_port = control_port
        self.event_port = event_port
        self.init_commands_path = init_commands_path or "init_commands"
        self.colorize = colorize
        self.raw = raw
        self.interactive = interactive
        self.backend_url = backend_url
        self.backend_token = backend_token
        self.backend_transport = backend_transport

        self.min_lap_interval_s = min_lap_interval_s
        # Auto-antenna config (conducted power in 0.1 dBm units; ports fallback
        # is used when the reader's antennas.detected returns nothing).
        self.antenna_power = int(antenna_power)
        self.antenna_ports_fallback = antenna_ports_fallback
        self.discover_after_failures = discover_after_failures
        self.discover_interval_s = discover_interval_s
        self.discovery_enabled = discovery_enabled
        self.clock_resync_interval_s = clock_resync_interval_s

        self.session = SessionState()
        self.tags = TagTracker(min_lap_interval_s=min_lap_interval_s)
        self.control_sock: Optional[socket.socket] = None
        self.event_sock: Optional[socket.socket] = None
        self.antennas: List[int] = []
        self._control_lock = threading.Lock()
        self._standby_lock = threading.Lock()
        self._standby_sent = False
        self._stop_event = threading.Event()
        self._stopping = threading.Event()
        # Query/response coordination for CONTROL reads (antennas.detected,
        # liveness probe, clock resync). The CONTROL recv thread appends every
        # ok/error line to _query_replies while _query_pending is armed and
        # sets _query_done once _query_expected replies arrived. The RLock
        # serialises queries; the bind holds it for its whole duration so a
        # probe never interleaves with configuration commands.
        self._query_lock = threading.RLock()
        self._query_pending = threading.Event()
        self._query_done = threading.Event()
        self._query_replies: List[str] = []
        self._query_expected = 1
        self._query_quiet = False
        # Log throttle for repeated reader warning/error notifications.
        self._log_throttle = _MessageThrottle(window_s=60.0)
        # Backend client (HTTP/WS/MQTT)
        self._backend: Optional[BackendClient] = None

        # Reader identity
        self.reader_serial: Optional[str] = None

        # Connection lifecycle. The generation increments per socket pair;
        # recv threads and the bind carry the generation they were started
        # for (thread-local) and are ignored once it is outdated.
        self._generation = 0
        self._conn: Optional[_Connection] = None
        self._conn_lock = threading.Lock()
        self._bind_lock = threading.Lock()
        self._tls = threading.local()
        self._wake = threading.Event()
        self._conn_thread: Optional[threading.Thread] = None
        self._state = "connecting" if self.ip else "searching"
        self._consecutive_failures = 0
        self._last_discovery_at: Optional[float] = None
        self._discovered_ip: Optional[str] = None
        self._antenna_reads: Dict[str, int] = {}

        # Requests from heartbeat replies (reporter thread), consumed by the
        # connection thread.
        self._request_lock = threading.Lock()
        self._pending_reader_ip: Optional[str] = None
        self._pending_antenna_power: Optional[int] = None
        self._pending_commands: List[Dict[str, Any]] = []

        # Timings as instance attributes so tests can shrink them.
        self._backoff_s: Tuple[float, ...] = DEFAULT_BACKOFF_S
        self._connect_timeout_s = CONNECT_TIMEOUT_S
        # Socket timeout after connect: bounds sendall() on a stalled link and
        # lets recv threads re-check stop/generation; recv timeouts are not errors.
        self._io_timeout_s = 5.0
        self._session_id_timeout_s = 10.0
        self._configure_timeout_s = 30.0
        self._probe_interval_s = 15.0
        self._probe_timeout_s = 5.0
        # Consecutive unanswered probes/resyncs before the connection counts
        # as lost: with a probe every 15 s a silent link is torn down 35-50 s
        # after it went quiet, so shorter outages lose no passes. Kept below
        # the TCP keepalive total (utils.enable_tcp_keepalive, 40-55 s).
        self._probe_max_misses = 3
        self._probe_misses = 0
        self._antenna_probe_delay_s = 1.5
        self._send_delay_s = 0.02
        self._join_timeout_s = 2.0
        self._connect = connect_socket
        self._discover = discovery.discover
        self._monotonic = time.monotonic

        heartbeat_url = None
        if backend_transport == "http" and backend_url and heartbeat_interval_s > 0:
            heartbeat_url = f"{backend_url.rstrip('/')}/reader/status"
        self._status = ReaderStatusReporter(
            url=heartbeat_url,
            token=backend_token,
            interval_s=heartbeat_interval_s,
            on_reply=self._on_status_reply,
        )
        self._status.update(
            state=self._state,
            ip=self.ip,
            target_source=self.target_source,
            antenna_power=self.antenna_power,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start backend client, status reporter and connection thread.

        Returns immediately. Raises only for configuration errors; an
        unreachable reader is retried in the background.
        """
        if self.backend_transport == "mock":
            self._backend = MockBackendClient()
        else:
            if not self.backend_url:
                raise RuntimeError("Backend URL must be provided when using HTTP transport")
            self._backend = HttpBackendClient(url=self.backend_url, token=self.backend_token, batch_size=10, flush_interval_ms=50)
        self._backend.start()
        self._status.start()

        # Enable interactive stdin commands if requested
        if self.interactive:
            threading.Thread(target=self._stdin_loop, name="reader-stdin", daemon=True).start()

        self._conn_thread = threading.Thread(target=self._connection_loop, name="reader-connection", daemon=True)
        self._conn_thread.start()

    def run_forever(self):
        # Parent-liveness tie (AUDIT-2026-07 H6): when the desktop shell dies
        # HARD (SIGKILL, force-quit, WKWebView crash) its atexit/finally
        # cleanup never runs and this process would keep the reader session
        # alive forever, POSTing to a dead backend URL and spooling every
        # pass. On POSIX, parent death reparents us (getppid changes, to
        # launchd/init) — detect that in the tick and shut down cleanly,
        # which also flushes/spools the backend queue. No-op on Windows
        # (ppid stays stable there; the shell uses a Job Object and
        # --stop-on-stdin-eof instead). A dev shell counts as parent too,
        # which is desirable: closing the terminal stops the service.
        # time.sleep rather than an Event wait: signal handlers call
        # request_stop() on this thread.
        parent_pid = os.getppid()
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
                current_ppid = os.getppid()
                if current_ppid != parent_pid:
                    logger.warning(
                        "Parent process %d gone (reparented to %d) — "
                        "shutting down to avoid an orphaned reader-service",
                        parent_pid, current_ppid,
                    )
                    break
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            self.stop()

    def stop(self):
        """Idempotent shutdown: reader standby, close sockets, final
        ``stopped`` heartbeat, backend flush (spools what is undelivered)."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        # Usually already sent by the connection thread (request_stop path),
        # which tears the sockets down before this runs.
        self._send_standby(self.control_sock)
        self._stop_event.set()
        self._wake.set()
        thread = self._conn_thread
        if thread is not None and thread is not threading.current_thread():
            # Short join: the thread may be inside a discovery sweep; the
            # sockets are torn down below either way, and the spool flush in
            # _backend.stop() must finish before the shell loses patience.
            thread.join(timeout=self._join_timeout_s)
        conn = self._conn
        if conn is not None:
            self._teardown_connection(conn)
        self._status.stop()
        if self._backend:
            try:
                self._backend.stop()
            except Exception:
                pass
        logger.info("[CONN] reader client stopped")

    def request_stop(self) -> None:
        """Signal the main loop to stop; used by signal handlers and the stdin watcher.
        The connection thread puts the reader into standby before closing."""
        self._stop_event.set()
        self._wake.set()

    def _send_standby(self, sock: Optional[socket.socket]) -> None:
        """Best-effort ``setup.operating_mode=standby`` (RF field off), once per
        client. Written straight to ``sock``: on a dying link a failure is
        expected and must not be reported as a lost connection."""
        if sock is None:
            return
        with self._standby_lock:
            if self._standby_sent:
                return
            try:
                with self._control_lock:
                    sock.sendall(b"setup.operating_mode=standby\r\n")
            except OSError as exc:
                logger.debug("[CONTROL] standby on shutdown not sent: %s", exc)
                return
            self._standby_sent = True
            logger.info("[CONTROL] >> setup.operating_mode=standby")

    def status_snapshot(self) -> Dict[str, Any]:
        """Current reader status (the heartbeat body of contract §2.1)."""
        return self._status.snapshot()

    # ------------------------------------------------------------------
    # Connection thread
    # ------------------------------------------------------------------

    def _connection_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._handle_requests()
                if not self.ip:
                    self._search()
                    continue
                outcome, reason = self._connect_and_run()
                if self._stop_event.is_set() or outcome in (_STOPPED, _RECONNECT):
                    continue
                self._after_failure(outcome, reason)
            except Exception:
                # A bug must not end reconnection for the rest of the race.
                logger.exception("[CONN] unexpected error in the connection thread; retrying")
                self._idle(self._backoff_s[-1])

    def _set_state(self, state: str, **fields: Any) -> None:
        previous = self._state
        self._state = state
        self._status.update(state=state, **fields)
        if previous != state:
            logger.info("[STATE] %s -> %s%s", previous, state, f" ({self.ip})" if self.ip else "")

    def _idle(self, timeout: Optional[float]) -> bool:
        """Wait ``timeout`` seconds (None: until a request arrives).

        Returns True when interrupted by stop or by a request that wants the
        target connected right away, False when the time simply ran out.
        """
        deadline = None if timeout is None else self._monotonic() + timeout
        while not self._stop_event.is_set():
            self._wake.clear()
            if self._handle_requests():
                return True
            if self._stop_event.is_set():
                break
            if deadline is None:
                self._wake.wait()
                continue
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            self._wake.wait(remaining)
        return True

    def _search(self) -> None:
        """No target: discover (unless disabled), else wait for the next run
        or for a config/command from the backend."""
        self._set_state("searching", ip=None, target_source=None)
        if not self.discovery_enabled:
            self._status.update(error="no reader ip configured", next_retry_s=None)
            self._idle(None)
            return
        now = self._monotonic()
        if self._last_discovery_at is None or now - self._last_discovery_at >= self.discover_interval_s:
            if self._select_target(self._run_discovery()):
                return
            now = self._monotonic()
        wait_s = max(0.0, self._last_discovery_at + self.discover_interval_s - now)
        self._status.update(next_retry_s=round(wait_s, 1))
        self._idle(wait_s)
        self._status.update(next_retry_s=None)

    def _connect_and_run(self) -> Tuple[str, Optional[str]]:
        ip = self.ip
        self._set_state("connecting", ip=ip, target_source=self.target_source)
        try:
            control = self._connect(ip, self.control_port, "CONTROL", self._connect_timeout_s)
        except ConnectError as exc:
            return _FAILED, str(exc)
        try:
            event = self._connect(ip, self.event_port, "EVENT", self._connect_timeout_s)
        except ConnectError as exc:
            _close_socket(control)
            return _FAILED, str(exc)
        if self._stop_event.is_set():
            _close_socket(control)
            _close_socket(event)
            return _STOPPED, None

        conn = self._begin_connection(ip, control, event)
        try:
            outcome = self._await_configured(conn)
            if outcome is None:
                self._on_active(conn)
                outcome = self._run_active(conn)
            return outcome
        finally:
            if self._stop_event.is_set() and not conn.lost.is_set():
                # Contract §3.2 stop(): standby first. request_stop() only
                # wakes this thread, and the teardown below clears
                # control_sock before stop() could send it.
                self._send_standby(conn.control)
            self._teardown_connection(conn)

    def _begin_connection(self, ip: str, control: socket.socket, event: socket.socket) -> _Connection:
        for sock in (control, event):
            sock.settimeout(self._io_timeout_s)
        with self._conn_lock:
            self._generation += 1
            conn = _Connection(generation=self._generation, ip=ip, control=control, event=event)
            self._conn = conn
        self._tls.generation = conn.generation

        # Per-connection reset (contract §3.2 step 3). Deliberately kept:
        # tags.seen, tags.last_emitted_at, the log throttle, the backend client.
        self.session.id = None
        self.session.bound = False
        self.tags.reset_presence()
        self.reader_serial = None
        self.antennas = []
        self._antenna_reads = {}
        self._probe_misses = 0
        self._query_pending.clear()
        self._query_replies = []
        with self._control_lock:
            self.control_sock = control
            self.event_sock = event
        self._status.update(serial=None, antennas=[], antenna_reads={}, connected_since=None, next_retry_s=None)
        self._set_state("configuring")

        self._start_recv_thread(conn, "CONTROL", control)
        try:
            # Sent before the EVENT thread runs, so its reply precedes any
            # bind/antenna query on the wire.
            self._send_control(["info.serial_number"])
        except ControlSendError:
            pass  # already marked lost; the configuring wait returns at once
        self._start_recv_thread(conn, "EVENT", event)
        return conn

    def _start_recv_thread(self, conn: _Connection, name: str, sock: socket.socket) -> None:
        thread = threading.Thread(
            target=self._recv_loop,
            args=(name, sock, conn.generation),
            name=f"reader-recv-{name.lower()}-{conn.generation}",
            daemon=True,
        )
        conn.threads.append(thread)
        thread.start()

    def _await_configured(self, conn: _Connection) -> Optional[Tuple[str, Optional[str]]]:
        """Wait for event.connection id and the bind/config it triggers (on the
        EVENT thread). None when configured, else the failure outcome."""
        started = self._monotonic()
        id_seen_at: Optional[float] = None
        while True:
            self._wake.clear()
            if self._stop_event.is_set():
                return _STOPPED, None
            if conn.lost.is_set():
                return _LOST, conn.reason
            if self._handle_requests():
                return _RECONNECT, None
            if self.session.bound:
                return None
            now = self._monotonic()
            if self.session.id is None:
                remaining = started + self._session_id_timeout_s - now
                if remaining <= 0:
                    return _LOST, f"no event.connection id within {self._session_id_timeout_s:g}s"
            else:
                if id_seen_at is None:
                    id_seen_at = now
                remaining = id_seen_at + self._configure_timeout_s - now
                if remaining <= 0:
                    return _LOST, "configuration timeout"
            self._wake.wait(remaining)

    def _on_active(self, conn: _Connection) -> None:
        self._consecutive_failures = 0
        self._status.update(
            connected_since=utc_now_iso(),
            error=None,
            consecutive_failures=0,
            next_retry_s=None,
        )
        self._set_state("active")
        logger.info(
            "[CONN] reader %s active (serial=%s, antennas=%s)",
            conn.ip, self.reader_serial or "?", self.antennas,
        )

    def _run_active(self, conn: _Connection) -> Tuple[str, Optional[str]]:
        """Stay here while events flow: liveness probe every
        _probe_interval_s, clock resync every clock_resync_interval_s."""
        now = self._monotonic()
        next_probe = now + self._probe_interval_s
        next_resync = now + self.clock_resync_interval_s if self.clock_resync_interval_s > 0 else math.inf
        while True:
            self._wake.clear()
            if self._stop_event.is_set():
                return _STOPPED, None
            if conn.lost.is_set():
                return _LOST, conn.reason
            dead = [t.name for t in conn.threads if not t.is_alive()]
            if dead and not self._stop_event.is_set():
                # A receive thread that ended without flagging the loss.
                logger.error("[CONN] receive thread(s) ended unexpectedly: %s", ", ".join(dead))
                return _LOST, "receive thread ended"
            if self._handle_requests():
                return _RECONNECT, None
            if self.session.bound and self._state != "active":
                self._set_state("active")  # W-061 rebind finished
            now = self._monotonic()
            if now >= next_resync:
                result = self._resync_clock()
                # Busy (bind or query in progress) or unanswered: try again
                # soon, not in 30 min.
                retry = self._probe_interval_s if result in ("busy", "missed") else self.clock_resync_interval_s
                next_resync = now + retry
                continue
            if now >= next_probe:
                self._liveness_probe()
                next_probe = now + self._probe_interval_s
                continue
            self._wake.wait(min(next_probe, next_resync) - now)

    def _after_failure(self, outcome: str, reason: Optional[str]) -> None:
        self._consecutive_failures += 1
        failures = self._consecutive_failures
        self._status.update(error=reason, consecutive_failures=failures, connected_since=None)
        self._set_state(_LOST if outcome == _LOST else "connecting")
        if self._should_auto_discover():
            candidates = self._run_discovery()
            if self._select_target(candidates):
                return
            if self.ip and any(c.ip == self.ip for c in candidates):
                # The sweep just reached the target: it is back, retry now.
                logger.info("[CONN] reader %s answers again; reconnecting without back-off", self.ip)
                return
        delay = self._backoff_delay()
        # Every attempt in the first few, then sparsely: a reader that is off
        # for an hour must not fill the rotating log.
        level = logging.WARNING if failures <= 3 or failures % 30 == 0 else logging.DEBUG
        logger.log(level, "[CONN] %s: %s (failure %d); retrying in %gs", self.ip, reason, failures, delay)
        self._status.update(next_retry_s=delay)
        self._idle(delay)
        self._status.update(next_retry_s=None)

    def _backoff_delay(self) -> float:
        index = min(max(self._consecutive_failures - 1, 0), len(self._backoff_s) - 1)
        return float(self._backoff_s[index])

    def _mark_lost(self, generation: Optional[int], reason: str) -> None:
        """Flag the connection of ``generation`` (None: the current one) as
        lost; the connection thread tears it down and reconnects."""
        if self._stopping.is_set():
            return
        with self._conn_lock:
            conn = self._conn
            if conn is None or conn.lost.is_set():
                return
            if generation is not None and generation != conn.generation:
                return
            conn.reason = reason
            conn.lost.set()
        logger.warning("[CONN] connection to %s lost: %s", conn.ip, reason)
        self._wake.set()

    def _teardown_connection(self, conn: _Connection) -> None:
        with self._conn_lock:
            if conn.closed:
                return
            conn.closed = True
            if not conn.lost.is_set():
                conn.reason = conn.reason or "closed"
                conn.lost.set()
            if self._conn is conn:
                self._conn = None
        # Close first: a sendall() blocked on the dead socket holds
        # _control_lock and only returns once the socket is shut down.
        _close_socket(conn.control)
        _close_socket(conn.event)
        with self._control_lock:
            if self.control_sock is conn.control:
                self.control_sock = None
            if self.event_sock is conn.event:
                self.event_sock = None
        self._query_done.set()  # release a query still waiting on this connection
        current = threading.current_thread()
        for thread in conn.threads:
            if thread is not current:
                thread.join(timeout=self._join_timeout_s)

    def _closing_expected(self, generation: Optional[int], conn: Optional[_Connection]) -> bool:
        """True when an I/O failure is the expected fallout of our own
        shutdown or teardown (stop, reconnect request) rather than a fault:
        the caller runs for a connection that is gone or already lost."""
        if self._stopping.is_set():
            return True
        if generation is None:
            return False  # not a connection thread (stop(), stdin, tests)
        return conn is None or conn.generation != generation or conn.lost.is_set()

    def _thread_generation(self) -> Optional[int]:
        return getattr(self._tls, "generation", None)

    def _is_current(self, generation: int) -> bool:
        conn = self._conn
        return (
            conn is not None
            and conn.generation == generation
            and not conn.lost.is_set()
            and not self._stopping.is_set()
        )

    # ------------------------------------------------------------------
    # Liveness probe and clock resync (B4)
    # ------------------------------------------------------------------

    def _exclusive_control_batch(self, cmds: List[str], label: str) -> str:
        """Send ``cmds`` and wait for one ok/error reply each, but only when
        no bind or other query owns the CONTROL reply stream.

        Returns "ok", "busy" (skipped), "missed" (no reply, but fewer than
        _probe_max_misses in a row) or "failed" (connection marked lost).
        """
        if not self.session.bound or self._bind_lock.locked():
            return "busy"
        if not self._query_lock.acquire(blocking=False):
            return "busy"
        try:
            replies = self._query_locked(cmds, self._probe_timeout_s, quiet=True)
        except ControlSendError:
            return "failed"  # _send_control already marked the connection lost
        finally:
            self._query_lock.release()
        if replies is None:
            # A short link outage (cable wobble, switch port renegotiating)
            # only delays TCP: the reader keeps the passes it emits meanwhile
            # and delivers them once the link is back, unless we close the
            # sockets. So one silent probe is not a loss; only a run of them
            # is. EOF, RST, recv errors and failed sends still count at once.
            self._probe_misses += 1
            if self._probe_misses < self._probe_max_misses:
                logger.warning(
                    "[CONN] no reply to %s within %gs (%d/%d); keeping the connection",
                    label, self._probe_timeout_s, self._probe_misses, self._probe_max_misses,
                )
                return "missed"
            self._mark_lost(
                self._thread_generation(),
                f"no reply to {self._probe_misses} {label}s in a row ({self._probe_timeout_s:g}s each)",
            )
            return "failed"
        if self._probe_misses:
            logger.info("[CONN] reader %s answers again after %d unanswered probe(s)", self.ip, self._probe_misses)
        self._probe_misses = 0
        return "ok"

    def _liveness_probe(self) -> str:
        # EVENT is silent while no tag is in the field, and a probe that is
        # never answered is the only signal when unacknowledged data keeps TCP
        # keepalive from firing.
        return self._exclusive_control_batch(["info.time"], "liveness probe")

    def _resync_clock(self) -> str:
        commands = self._clock_commands()
        result = self._exclusive_control_batch(commands, "clock resync")
        if result == "ok":
            logger.info("[CLOCK] re-pushed host UTC clock to the reader: %s", commands[1].split("=", 1)[1])
        return result

    @staticmethod
    def _clock_commands() -> List[str]:
        # Format: ISO 8601 without timezone suffix, e.g. "2026-05-12T20:05:00.123".
        now_for_reader = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        return ["info.time_zone=UTC", f"info.time={now_for_reader}"]

    # ------------------------------------------------------------------
    # Discovery and heartbeat replies (C3, contract §3.4)
    # ------------------------------------------------------------------

    def _is_connected(self) -> bool:
        return self._conn is not None and self._state in ("configuring", "active")

    def _should_auto_discover(self) -> bool:
        if not self.discovery_enabled or self._consecutive_failures < self.discover_after_failures:
            return False
        return self._last_discovery_at is None or self._monotonic() - self._last_discovery_at >= self.discover_interval_s

    def _run_discovery(self, request_id: Optional[int] = None) -> List[discovery.Candidate]:
        current_ip = self.ip if self._is_connected() else None
        logger.info("[DISCOVERY] %s run started", "requested" if request_id is not None else "automatic")
        error = None
        try:
            # The connected reader is not probed: it is reported as-is.
            candidates = list(self._discover(port=self.control_port, skip_ips=[current_ip] if current_ip else []))
        except Exception as exc:  # discovery.discover never raises; be safe anyway
            logger.error("[DISCOVERY] failed: %s", exc)
            candidates = []
            error = str(exc)
        if current_ip:
            candidates.insert(0, discovery.Candidate(ip=current_ip, serial=self.reader_serial, source="connected"))
        self._last_discovery_at = self._monotonic()
        # "Reader suchen" clicked while this run was sweeping (or while the
        # thread was busy before it): the sweep ran after the click, so answer
        # with its result instead of sweeping a second time, which could push
        # the answer past the backend's 15 s wait.
        queued_id = self._take_queued_discover_id()
        if queued_id is not None:
            logger.info("[DISCOVERY] answering request id=%s with this run's result", queued_id)
            request_id = queued_id
        as_dicts = [c.to_dict() for c in candidates]
        fields: Dict[str, Any] = {"candidates": as_dicts}
        if request_id is not None:
            fields["discovery"] = {"request_id": request_id, "candidates": as_dicts, "error": error}
        self._status.update(**fields)
        return candidates

    def _take_queued_discover_id(self) -> Optional[int]:
        """Remove queued ``discover`` commands; return the newest id (the
        backend waits for one request at a time, older ids have given up)."""
        with self._request_lock:
            discovers = [c for c in self._pending_commands if c["type"] == "discover"]
            if not discovers:
                return None
            self._pending_commands = [c for c in self._pending_commands if c["type"] != "discover"]
        return discovers[-1]["id"]

    def _select_target(self, candidates: List[discovery.Candidate]) -> bool:
        """Automatic target selection. True when the target switched."""
        ips: List[str] = []
        for candidate in candidates:
            if candidate.ip not in ips:
                ips.append(candidate.ip)
        if self.ip and self.ip in ips:
            return False
        if len(ips) == 1:
            new_ip = ips[0]
            logger.warning("[DISCOVERY] reader target %s -> %s (found by discovery)", self.ip or "none", new_ip)
            self.ip = new_ip
            self.target_source = "discovery"
            self._discovered_ip = new_ip
            self._consecutive_failures = 0
            self._status.update(
                ip=new_ip, target_source="discovery", discovered_ip=new_ip,
                error=None, consecutive_failures=0,
            )
            return True
        error = "no reader found" if not ips else "multiple readers found"
        logger.warning("[DISCOVERY] %s; keeping target %s", error, self.ip or "none")
        self._status.update(error=error)
        return False

    def _on_status_reply(self, reply: Dict[str, Any]) -> None:
        """Heartbeat reply (reporter thread): queue config changes and
        commands for the connection thread. Must not block."""
        reader_ip: Optional[str] = None
        antenna_power: Optional[int] = None
        config = reply.get("config")
        if isinstance(config, dict):
            candidate_ip = config.get("reader_ip")
            pending = self._discovered_ip
            # Until a POST carrying our discovered_ip has been answered, the
            # backend still echoes the old IP; adopting it would undo the switch.
            if _is_ipv4(candidate_ip) and (pending is None or self._status.last_sent().get("discovered_ip") == pending):
                if candidate_ip != self.ip or candidate_ip == pending:
                    reader_ip = candidate_ip
            power = config.get("antenna_power")
            if isinstance(power, int) and not isinstance(power, bool) and power >= 0 and power != self.antenna_power:
                antenna_power = power
        command = reply.get("command")
        queued_command = None
        if isinstance(command, dict):
            if command.get("type") in ("discover", "reconnect"):
                queued_command = {"id": command.get("id"), "type": command["type"]}
            else:
                logger.warning("[STATUS] ignoring unknown command: %r", command)
        if reader_ip is None and antenna_power is None and queued_command is None:
            return
        with self._request_lock:
            if reader_ip is not None:
                self._pending_reader_ip = reader_ip
            if antenna_power is not None:
                self._pending_antenna_power = antenna_power
            if queued_command is not None:
                self._pending_commands.append(queued_command)
        self._wake.set()

    def _handle_requests(self) -> bool:
        """Apply queued config changes and commands (connection thread only).
        True when the target must be (re)connected now."""
        with self._request_lock:
            reader_ip, self._pending_reader_ip = self._pending_reader_ip, None
            antenna_power, self._pending_antenna_power = self._pending_antenna_power, None
            commands, self._pending_commands = self._pending_commands, []
        reconnect = False
        if reader_ip is not None:
            if reader_ip == self._discovered_ip:
                self._discovered_ip = None  # the backend persisted the discovered IP
                self._status.update(discovered_ip=None)
            if reader_ip != self.ip:
                logger.info("[CONFIG] reader_ip changed by backend config: %s -> %s", self.ip or "none", reader_ip)
                self.ip = reader_ip
                self.target_source = "config"
                self._discovered_ip = None
                self._consecutive_failures = 0
                self._status.update(
                    ip=reader_ip, target_source="config", discovered_ip=None,
                    consecutive_failures=0, error=None,
                )
                reconnect = True
        if antenna_power is not None and antenna_power != self.antenna_power:
            logger.info("[CONFIG] antenna_power changed by backend config: %d -> %d", self.antenna_power, antenna_power)
            self.antenna_power = antenna_power
            self._status.update(antenna_power=antenna_power)
            if self.ip:
                reconnect = True
        discover_id: Optional[int] = None
        for command in commands:
            if command["type"] == "reconnect":
                logger.info("[COMMAND] reconnect requested (id=%s)", command["id"])
                self._consecutive_failures = 0
                self._status.update(consecutive_failures=0)
                if not self.ip:
                    self._last_discovery_at = None  # searching: run discovery now
                reconnect = True
            elif command["type"] == "discover":
                discover_id = command["id"]  # one sweep answers the newest request
        if discover_id is not None:
            candidates = self._run_discovery(request_id=discover_id)
            # With a target the operator picks from the list (PATCH
            # /config); only a client without any target adopts a single
            # result on its own (startup policy C3).
            if not self.ip and self._select_target(candidates):
                reconnect = True
        return reconnect

    # ------------------------------------------------------------------
    # Socket I/O
    # ------------------------------------------------------------------

    def _recv_loop(self, name: str, sock: socket.socket, generation: int):
        self._tls.generation = generation
        buffer = ""
        delim = "\r\n\r\n"
        reason: Optional[str] = None
        try:
            while not self._stop_event.is_set() and generation == self._generation:
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue  # idle link; keepalive and the liveness probe detect dead peers
                if not chunk:
                    reason = f"{name} connection closed by the reader"
                    break
                data = chunk.decode("utf-8", errors="replace")
                if self.raw:
                    logger.debug("[%s] <<RAW_CHUNK %d bytes>> %r", name, len(chunk), data)
                buffer += data
                while True:
                    idx = buffer.find(delim)
                    if idx == -1:
                        break
                    msg, buffer = buffer[:idx], buffer[idx + len(delim):]
                    msg = msg.strip("\r\n")
                    if msg and generation == self._generation:
                        if self.raw:
                            logger.debug("[%s] <<RAW_MSG>> %s", name, msg)
                        try:
                            self._handle_message(name, msg)
                        except Exception:
                            # One unparseable line must never end this thread:
                            # a dead EVENT thread behind a healthy CONTROL
                            # probe would silently drop every later pass.
                            logger.exception("[%s] could not handle reader message %r; continuing", name, msg)
        except OSError as e:
            reason = f"{name} socket error: {e}"
        except Exception as e:
            # Safety net: never leave the connection "active" without a
            # receive thread; marking it lost makes the loop reconnect.
            logger.exception("[%s] receive thread failed", name)
            reason = f"{name} receive error: {e}"
        finally:
            _close_socket(sock)
            if reason is not None and self._is_current(generation):
                self._mark_lost(generation, reason)

    def _handle_message(self, name: str, msg: str):
        if name.upper() == "EVENT":
            # W-061: handle connection id changes — reset bind if session id differs
            m = re.search(r"event\.connection\s+id\s*=\s*(\d+)", msg, re.IGNORECASE)
            if m:
                new_id = int(m.group(1))
                if self.session.id is None:
                    # First time: record and bind
                    self.session.id = new_id
                    logger.info("[SESSION] obtained id from EVENT: %d", self.session.id)
                    self._wake.set()
                    self._maybe_bind_and_config()
                elif new_id != self.session.id:
                    # W-061: reader reconnected with a new session id — rebind
                    logger.warning(
                        "[SESSION] connection id changed %d -> %d; resetting bind and reconfiguring",
                        self.session.id, new_id,
                    )
                    self.session.id = new_id
                    self.session.bound = False
                    self._wake.set()
                    self._maybe_bind_and_config()
            low = msg.lower()
            if "event.tag.arrive" in low:
                ev = self._parse_event_message("arrive", msg)
                antenna = None
                if ev:
                    antenna = ev.antenna if ev.antenna is not None else 0
                # Raw read counts (before presence gating) feed the operator's
                # antenna test; a gated duplicate still proves the port works.
                self._record_tag_line(antenna)
                if ev and self.tags.mark_present(ev.tag_id, antenna):
                    label = _color("ARRIVE", _C.GREEN) if self.colorize else "ARRIVE"
                    logger.info("[%s] [%s] %s", name, label, msg)
                    self._print_tag_id(ev.tag_id)
                    self._emit_event(ev)
                return
            if "event.tag.depart" in low:
                self._record_tag_line(None)
                ev = self._parse_event_message("depart", msg)
                if ev:
                    antenna = ev.antenna if ev.antenna is not None else 0
                    if self.tags.mark_absent(ev.tag_id, antenna):
                        label = _color("DEPART", _C.RED) if self.colorize else "DEPART"
                        logger.info("[%s] [%s] %s", name, label, msg)
                        self._print_tag_id(ev.tag_id)
                        self._emit_event(ev)
                return
            if "event.tag." in low:
                self._record_tag_line(None)
            tag = "EVENT"
            base = "[%s] [%s] %s"
            if not self._log_noisy_reader_message(name, msg):
                logger.info(base, name, _color(tag, _C.CYAN) if self.colorize else tag, msg)
        elif name.upper() == "CONTROL":
            tag = "CTRL"
            stripped = msg.strip()
            low = stripped.lower()
            # Capture replies for a pending CONTROL query. Armed by
            # _query_locked() immediately before its send, so the next
            # ok/error lines are the replies.
            if self._query_pending.is_set() and (low.startswith("ok") or low.startswith("error")):
                log = logger.debug if self._query_quiet else logger.info
                log("[%s] [%s] %s", name, _color(tag, _C.YELLOW) if self.colorize else tag, msg)
                self._query_replies.append(stripped)
                if len(self._query_replies) >= self._query_expected:
                    self._query_pending.clear()
                    self._query_done.set()
                return
            if not self._log_noisy_reader_message(name, msg):
                logger.info("[%s] [%s] %s", name, _color(tag, _C.YELLOW) if self.colorize else tag, msg)
            # Capture reader serial number once when still unknown
            if self.reader_serial is None:
                m = re.match(r"ok\s+([0-9A-Fa-f]{8,})\b", stripped)
                if m:
                    self.reader_serial = m.group(1).upper()
                    logger.info("[READER] serial_number=%s", self.reader_serial)
                    self._status.update(serial=self.reader_serial)
        else:
            logger.info("[%s] %s", name, msg)

    def _record_tag_line(self, antenna_read: Optional[int]) -> None:
        """last_event_at for any event.tag.* line; per-antenna raw read count
        (since this connection) for arrives."""
        fields: Dict[str, Any] = {"last_event_at": utc_now_iso()}
        if antenna_read is not None:
            key = str(antenna_read)
            self._antenna_reads[key] = self._antenna_reads.get(key, 0) + 1
            fields["antenna_reads"] = dict(self._antenna_reads)
        self._status.update(**fields)

    def _log_noisy_reader_message(self, channel: str, msg: str) -> bool:
        """Throttled logging for repeated reader notifications.

        Returns True when the message matched a noisy class (event.warning.*/
        event.error.*) and was handled here — first occurrence per event name
        logs immediately, repeats within the 60 s window are counted and shown
        as one "(+N suppressed)" summary when the window rolls over. Returns
        False for everything else so the caller logs normally.
        """
        stripped = msg.strip()
        low = stripped.lower()
        if not (low.startswith("event.warning.") or low.startswith("event.error.")):
            return False
        # Key by the event name (first token) so e.g. all
        # "event.warning.antenna ..." lines share one budget.
        key = stripped.split(None, 1)[0]
        log_now, suppressed = self._log_throttle.check(key)
        if log_now:
            suffix = f" (+{suppressed} repeats suppressed in the last 60s)" if suppressed else ""
            logger.info("[%s] [READER-WARN] %s%s", channel, stripped, suffix)
        return True

    def _send_control(self, cmds: List[str], quiet: bool = False) -> None:
        """Send CONTROL commands in order.

        Raises ControlSendError when there is no socket, the send fails (the
        connection is then marked lost) or the calling thread belongs to a
        replaced connection. Failing loudly keeps a dead socket from being
        configured "successfully".
        """
        if not cmds:
            return
        generation = self._thread_generation()
        if generation is not None and generation != self._generation:
            raise ControlSendError("stale connection")
        conn = self._conn
        error: Optional[OSError] = None
        with self._control_lock:
            sock = self.control_sock
            if sock is None:
                raise ControlSendError("CONTROL socket not connected")
            try:
                for c in cmds:
                    sock.sendall((c + "\r\n").encode("utf-8", errors="ignore"))
                    (logger.debug if quiet else logger.info)("[CONTROL] >> %s", c)
                    if self._send_delay_s > 0:
                        time.sleep(self._send_delay_s)
            except OSError as e:
                error = e
        if error is not None:
            if self._closing_expected(generation, conn):
                logger.debug("[CONTROL] send on a closing connection failed: %s", error)
            else:
                logger.error("[CONTROL] send error: %s", error)
            self._mark_lost(generation, f"CONTROL send failed: {error}")
            raise ControlSendError(f"CONTROL send failed: {error}") from error

    def _query_locked(self, cmds: List[str], timeout: float, quiet: bool = False) -> Optional[List[str]]:
        """Send ``cmds`` and wait for one ok/error reply per command. Caller
        holds _query_lock. None on timeout or when the connection went away."""
        self._query_replies = []
        self._query_expected = len(cmds)
        self._query_quiet = quiet
        self._query_done.clear()
        self._query_pending.set()
        try:
            if quiet:
                self._send_control(cmds, quiet=True)
            else:
                self._send_control(cmds)
            got = self._query_done.wait(timeout=timeout)
        finally:
            self._query_pending.clear()
        replies = list(self._query_replies)
        if not got or len(replies) < len(cmds):
            return None
        return replies

    def _query_control(self, cmd: str, timeout: float = 2.0) -> Optional[str]:
        """Send a CONTROL query and return the reader's reply line ("ok ...").

        Returns None on timeout or when there's no socket. Used for
        antennas.detected (auto-detection).
        """
        if not self.control_sock:
            return None
        with self._query_lock:
            replies = self._query_locked([cmd], timeout)
        if replies is None:
            generation = self._thread_generation()
            if generation is None or generation == self._generation:
                logger.warning("[CONTROL] query timed out after %.1fs: %s", timeout, cmd)
            return None
        return replies[0]

    def _configure_antennas(self):
        """Auto-detect connected antennas and configure the reader (F: fast
        setup / TODO-antenna-port-config).

        Guest-permitted flow (perform_check() is admin-only, so we use the
        guest-readable antennas.detected var instead):
          1. Power on all 4 ports + mux 1..4 + active, so the reader runs its
             per-port antenna checks and populates antennas.detected.
          2. Read antennas.detected → "ok 1 2".
          3. Set the final mux_sequence to the detected ports, power them,
             power=0 on the rest, re-activate.
        Falls back to the configured default ports if detection returns nothing
        or fails, so the reader is always left in a working state. A failed
        send (dead connection) propagates as ControlSendError.
        """
        power = int(self.antenna_power)
        all_ports = [1, 2, 3, 4]
        fallback = self._parse_ports_str(self.antenna_ports_fallback)
        try:
            # Phase 1: light up every port so detection has something to measure.
            probe_cmds = [f"antennas.{n}.conducted_power={power}" for n in all_ports]
            probe_cmds.append("antennas.mux_sequence=" + " ".join(str(n) for n in all_ports))
            probe_cmds.append("setup.operating_mode=active")
            self._send_control(probe_cmds)
            # Give the reader time to cycle the mux and run antenna checks.
            # Without a CONTROL socket there is nothing to wait for: the query
            # below returns None at once.
            if self.control_sock is not None:
                time.sleep(self._antenna_probe_delay_s)

            # Phase 2: ask which ports actually have an antenna.
            reply = self._query_control("antennas.detected", timeout=2.5)
            detected = self._parse_detected_ports(reply)
            if detected:
                logger.info("[ANTENNA] detected connected ports: %s", detected)
            else:
                logger.warning(
                    "[ANTENNA] auto-detection returned nothing (reply=%r); "
                    "using fallback ports only: %s", reply, fallback,
                )
        except ControlSendError:
            raise
        except Exception as e:
            detected = []
            logger.error(
                "[ANTENNA] auto-detection failed (%s); using fallback ports %s",
                e, fallback,
            )

        # RECHECK-2026-07-25 #2: UNION of detected and fallback — the fallback
        # ports are the guaranteed minimum, detection can only ADD ports. A
        # borderline VSWR reading must never silently power off an antenna the
        # operator plugged in (a powered open port is harmless; a silently dead
        # port loses race data). Detection's job is catching EXTRA antennas.
        ports = sorted(set(detected) | set(fallback))
        if not ports:
            ports = [1]  # last-ditch: at least port 1
        extra = [p for p in ports if p not in fallback]
        if extra:
            logger.info("[ANTENNA] detection added ports beyond fallback: %s", extra)

        # Phase 3: final config — power the selected ports, disable the rest.
        final_cmds = []
        for n in all_ports:
            final_cmds.append(f"antennas.{n}.conducted_power={power if n in ports else 0}")
        final_cmds.append("antennas.mux_sequence=" + " ".join(str(n) for n in ports))
        final_cmds.append("setup.operating_mode=active")
        self._send_control(final_cmds)
        self.antennas = ports
        self._status.update(antennas=ports)
        logger.info(
            "[ANTENNA] configured: ports=%s power=%d (0.1 dBm)", ports, power
        )

    @staticmethod
    def _parse_ports_str(s: Optional[str]) -> List[int]:
        """Parse a "1 2" / "1,2" port list into [1, 2] (ports 1..4 only)."""
        if not s:
            return []
        out = []
        for tok in re.split(r"[\s,]+", s.strip()):
            if tok.isdigit() and 1 <= int(tok) <= 4 and int(tok) not in out:
                out.append(int(tok))
        return out

    @classmethod
    def _parse_detected_ports(cls, reply: Optional[str]) -> List[int]:
        """Parse an antennas.detected reply "ok 1 2" into [1, 2]. A bare "ok"
        (no ports) or an error reply yields []."""
        if not reply:
            return []
        r = reply.strip()
        if not r.lower().startswith("ok"):
            return []
        return cls._parse_ports_str(r[2:])  # drop the leading "ok"

    def _maybe_bind_and_config(self):
        """Bind the EVENT session and configure the reader. Runs on the EVENT
        recv thread (the CONTROL thread must stay free to deliver query
        replies). Serialised by _bind_lock; work for a replaced connection is
        dropped. Any failure marks the connection lost so it is retried."""
        generation = self._thread_generation()
        if generation is None:
            generation = self._generation
        with self._bind_lock:
            if generation != self._generation:
                return
            if self.session.id is None or self.session.bound:
                return
            sid = self.session.id
            if self._state == "active":
                self._set_state("configuring")  # W-061 rebind on a live connection
            try:
                # Holding the query lock keeps liveness probes and clock
                # resyncs (connection thread) out of the configuration stream.
                with self._query_lock:
                    self._send_control([f"reader.events.bind(id = {sid})"])
                    logger.info("[SESSION] bound event channel id %d", sid)
                    # Push the host's UTC clock to the reader. The Sirit INfinity 510
                    # has no battery-backed RTC and boots at a manufacturer epoch
                    # (~1999), which would make every event timestamp wildly in the
                    # past and break race-time / standings math downstream.
                    #
                    # ORDER MATTERS: we set info.time_zone=UTC *before* pushing the
                    # clock. If the reader is still in a non-UTC zone (e.g. Europe/Berlin,
                    # UTC+2) when we send a UTC value, it interprets our value as LOCAL
                    # time and converts it — leaving the reader clock offset by the zone
                    # (observed: 2 h behind), which makes event timestamps land before
                    # the race start and total times go negative. Setting the zone first
                    # means the naive value we send is taken as UTC, as intended.
                    clock_cmds = self._clock_commands()
                    self._send_control(clock_cmds)
                    logger.info("[SESSION] set reader zone=UTC and pushed host UTC clock: %s", clock_cmds[1].split("=", 1)[1])
                    extra_cmds: List[str] = []
                    if self.init_commands_path:
                        try:
                            with open(self.init_commands_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    # Strip inline comments (everything from the first '#')
                                    # AND full-line comments. The reader's parser does not
                                    # tolerate trailing comments, e.g.
                                    #   tag.reporting.depart_time = 300  # milliseconds
                                    # would otherwise be sent verbatim and rejected with
                                    # error.parser.illegal_value.
                                    line = line.split('#', 1)[0].strip()
                                    if not line:
                                        continue
                                    extra_cmds.append(line)
                        except Exception as e:
                            logger.warning("[CONFIG] Could not read init commands file '%s': %s. Continuing without extra config.", self.init_commands_path, e)
                    self._send_control(extra_cmds)
                    if extra_cmds:
                        logger.info("[SESSION] configuration applied (%d commands from %s init file)", len(extra_cmds), self.init_commands_path)
                    else:
                        logger.info("[SESSION] no extra configuration commands were sent (file empty or missing)")
                    # Auto-detect + configure antennas (mux_sequence + per-port power)
                    # so the operator doesn't have to SSH in and set them by hand.
                    self._configure_antennas()
                if generation != self._generation:
                    return
                self.session.bound = True
            except Exception as e:
                if generation != self._generation or self._closing_expected(self._thread_generation(), self._conn):
                    logger.debug("[SESSION] configuration of a closed connection aborted: %s", e)
                    return
                logger.error("[SESSION] configuration failed: %s", e)
                self._mark_lost(generation, f"configuration failed: {e}")
                return
        self._wake.set()

    @staticmethod
    def _extract_kv(msg: str) -> Dict[str, str]:
        """Extract simple key=value pairs from a message into a dict with lowercase keys.

        Values are returned as raw strings (without surrounding punctuation)."""
        pairs: Dict[str, str] = {}
        # Match tokens like key = value (value up to whitespace)
        for k, v in re.findall(r"([A-Za-z0-9_.]+)\s*=\s*([^\s]+)", msg):
            # Strip trailing commas or periods often present in log-style lines
            v = v.strip().rstrip(",.")
            pairs[k.lower()] = v
        return pairs

    @staticmethod
    def _parse_int_field(value: Optional[str]) -> Optional[int]:
        """``"-61"`` -> -61, ``"-61.5"`` -> -61; None for missing or unparseable values."""
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            pass
        try:
            number = float(value)
        except ValueError:
            return None
        if math.isnan(number) or math.isinf(number):
            return None
        return int(number)

    def _print_tag_id(self, tag_hex: str):
        is_new = self.tags.record_seen(tag_hex)
        if is_new:
            prefix = f"[TAG][{_color('NEW', _C.GREEN)}]" if self.colorize else "[TAG][NEW]"
        else:
            prefix = f"[{_color('TAG', _C.DIM)}]" if self.colorize else "[TAG]"
        logger.info("[EVENT] %s TAG=%s", prefix, tag_hex)

    def _emit_event(self, ev: TagEvent) -> None:
        if not self._backend:
            return
        try:
            self._backend.send(ev)
        except Exception as e:
            logger.error("[BACKEND] error queueing event: %s", e)

    def _parse_event_message(self, event_type: EventType, msg: str) -> Optional[TagEvent]:
        """Parse a raw EVENT line into a TagEvent by first building a typed event data model.

        - arrive: supports first (ISO string), antenna, rssi (all optional except tag_id)
        - depart: supports antenna, rssi (optional), tag_id required
        Returns None if tag_id cannot be found.
        """
        kv = self._extract_kv(msg)
        tag_raw = kv.get("tag_id")
        if not tag_raw:
            return None
        tag_hex = tag_raw.upper()
        if tag_hex.startswith("0X"):
            tag_hex = tag_hex[2:]

        # W-030: use reader-supplied timestamp (first/last) when available; fall back to wall clock.
        if event_type == "arrive" and "first" in kv:
            ts = parse_reader_time(kv["first"])
        elif event_type == "depart" and "last" in kv:
            ts = parse_reader_time(kv["last"])
        else:
            ts = utc_now_iso()

        fields: Dict[str, object] = {
            "source": "sirit-510",
            "reader_ip": self.ip,
            "timestamp": ts,
            "event_type": event_type,
            "tag_id": tag_hex,
            "session_id": self.session.id,
        }
        if self.reader_serial:
            fields["reader_serial"] = self.reader_serial
        # The protocol documents integers; anything else (a fractional or empty
        # value from odd firmware output) drops the field, never the pass.
        antenna = self._parse_int_field(kv.get("antenna"))
        if antenna is not None:
            fields["antenna"] = antenna
        rssi = self._parse_int_field(kv.get("rssi"))
        if rssi is not None:
            fields["rssi"] = rssi
        if event_type == "arrive" and "first" in kv:
            fields["first"] = kv["first"]
        if event_type == "depart" and "last" in kv:
            fields["last"] = kv["last"]

        return TagEvent(**fields)

    def _stdin_loop(self):
        while not self._stop_event.is_set():
            line = sys.stdin.readline() if sys.stdin is not None else ""
            if not line:
                time.sleep(0.05)
                continue
            c = line.strip()
            if not c:
                continue
            try:
                self._send_control([c])
            except ControlSendError as e:
                logger.error("[CONTROL] %s", e)
