from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from session_state import SessionState
from tag_tracker import TagTracker

from utils import _ts, _color, _C, connect_socket, get_logger, parse_reader_time
from models import TagEvent, EventType
from backend_client import BackendClient, HttpBackendClient, MockBackendClient

logger = get_logger("reader.sirit")


class SiritClient:
    def __init__(self, ip: str, control_port: int, event_port: int, init_commands_path: Optional[str], colorize: bool, raw: bool, interactive: bool, backend_url: Optional[str] = None, backend_token: Optional[str] = None, backend_transport: str = "http", min_lap_interval_s: float = 10.0, antenna_power: int = 300, antenna_ports_fallback: str = "1 2"):
        self.ip = ip
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
        self.antenna_power = antenna_power
        self.antenna_ports_fallback = antenna_ports_fallback
        self.session = SessionState()
        self.tags = TagTracker(min_lap_interval_s=min_lap_interval_s)
        self.control_sock: Optional[socket.socket] = None
        self.event_sock: Optional[socket.socket] = None
        self._control_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopping = threading.Event()
        self._stopped = threading.Event()
        # Query/response coordination for CONTROL reads (e.g. antennas.detected).
        # The recv loop (CONTROL thread) fills _query_result and sets the event
        # when a query is pending; the caller (EVENT/config thread) waits on it.
        self._query_pending = threading.Event()
        self._query_result: Optional[str] = None
        self._query_done = threading.Event()
        # Backend client (HTTP/WS/MQTT)
        self._backend: Optional[BackendClient] = None

        # Reader identity
        self.reader_serial: Optional[str] = None

    def start(self):
        # Initialize backend client
        if self.backend_transport == "mock":
            self._backend = MockBackendClient()
        else:
            if not self.backend_url:
                raise RuntimeError("Backend URL must be provided when using HTTP transport")
            self._backend = HttpBackendClient(url=self.backend_url, token=self.backend_token, batch_size=10, flush_interval_ms=50)
        self._backend.start()

        # Start control socket
        self.control_sock = connect_socket(self.ip, self.control_port, "CONTROL")
        if not self.control_sock:
            raise RuntimeError("CONTROL connection failed")
        threading.Thread(target=self._recv_loop, args=("CONTROL", self.control_sock), daemon=True).start()
        # Request reader serial number immediately after CONTROL is up
        self._send_control(["info.serial_number"])

        # Enable interactive stdin commands if requested
        if self.interactive:
            threading.Thread(target=self._stdin_loop, daemon=True).start()

        # Start event socket
        self.event_sock = connect_socket(self.ip, self.event_port, "EVENT")
        if not self.event_sock:
            raise RuntimeError("EVENT connection failed")
        threading.Thread(target=self._recv_loop, args=("EVENT", self.event_sock), daemon=True).start()

    def run_forever(self):
        # Parent-liveness tie (AUDIT-2026-07 H6): when the desktop shell dies
        # HARD (SIGKILL, force-quit, WKWebView crash) its atexit/finally
        # cleanup never runs and this process would keep the reader session
        # alive forever, POSTing to a dead backend URL and spooling every
        # pass. On POSIX, parent death reparents us (getppid changes, to
        # launchd/init) — detect that in the tick and shut down cleanly,
        # which also flushes/spools the backend queue. No-op on Windows
        # (ppid stays stable there). A dev shell counts as parent too, which
        # is desirable: closing the terminal stops the service.
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
        if self._stopping.is_set():
            return
        self._stopping.set()
        try:
            if self.control_sock:
                self._send_control(["setup.operating_mode=standby"])
        except Exception:
            pass
        self._stop_event.set()
        if self._backend:
            try:
                self._backend.stop()
            except Exception:
                pass
        for s in (self.control_sock, self.event_sock):
            try:
                if s:
                    s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self._stopped.set()

    def request_stop(self) -> None:
        """Signal the main loop to stop; used by external signal handlers."""
        self._stop_event.set()

    def _recv_loop(self, name: str, sock: socket.socket):
        buffer = ""
        delim = "\r\n\r\n"
        try:
            while not self._stop_event.is_set():
                chunk = sock.recv(4096)
                if not chunk:
                    logger.info("%s connection closed by the reader.", name)
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
                    if msg:
                        if self.raw:
                            logger.debug("[%s] <<RAW_MSG>> %s", name, msg)
                        self._handle_message(name, msg)
        except OSError as e:
            logger.error("%s socket error: %s", name, e)
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

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
                    self._maybe_bind_and_config()
                elif new_id != self.session.id:
                    # W-061: reader reconnected with a new session id — rebind
                    logger.warning(
                        "[SESSION] connection id changed %d -> %d; resetting bind and reconfiguring",
                        self.session.id, new_id,
                    )
                    self.session.id = new_id
                    self.session.bound = False
                    self._maybe_bind_and_config()
            low = msg.lower()
            if "event.tag.arrive" in low:
                ev = self._parse_event_message("arrive", msg)
                if ev:
                    antenna = ev.antenna if ev.antenna is not None else 0
                    if self.tags.mark_present(ev.tag_id, antenna):
                        label = _color("ARRIVE", _C.GREEN) if self.colorize else "ARRIVE"
                        logger.info("[%s] [%s] %s", name, label, msg)
                        self._print_tag_id(ev.tag_id)
                        self._emit_event(ev)
                return
            if "event.tag.depart" in low:
                ev = self._parse_event_message("depart", msg)
                if ev:
                    antenna = ev.antenna if ev.antenna is not None else 0
                    if self.tags.mark_absent(ev.tag_id, antenna):
                        label = _color("DEPART", _C.RED) if self.colorize else "DEPART"
                        logger.info("[%s] [%s] %s", name, label, msg)
                        self._print_tag_id(ev.tag_id)
                        self._emit_event(ev)
                return
        if name.upper() == "EVENT":
            tag = "EVENT"
            base = "[%s] [%s] %s"
            logger.info(base, name, _color(tag, _C.CYAN) if self.colorize else tag, msg)
        elif name.upper() == "CONTROL":
            tag = "CTRL"
            logger.info("[%s] [%s] %s", name, _color(tag, _C.YELLOW) if self.colorize else tag, msg)
            # Capture a pending CONTROL query response (e.g. antennas.detected).
            # Armed by _query_control() immediately before its send, so the very
            # next "ok ..." line is the reply.
            if self._query_pending.is_set():
                stripped = msg.strip()
                if stripped.lower().startswith("ok") or stripped.lower().startswith("error"):
                    self._query_result = stripped
                    self._query_pending.clear()
                    self._query_done.set()
                    return
            # Capture reader serial number once when still unknown
            if self.reader_serial is None:
                m = re.match(r"ok\s+([0-9A-Fa-f]{8,})\b", msg.strip())
                if m:
                    self.reader_serial = m.group(1).upper()
                    logger.info("[READER] serial_number=%s", self.reader_serial)
        else:
            logger.info("[%s] %s", name, msg)

    def _send_control(self, cmds: List[str]):
        if not self.control_sock:
            return
        try:
            with self._control_lock:
                for c in cmds:
                    self.control_sock.sendall((c + "\r\n").encode("utf-8", errors="ignore"))
                    logger.info("[CONTROL] >> %s", c)
                    time.sleep(0.02)
        except OSError as e:
            logger.error("[CONTROL] send error: %s", e)

    def _query_control(self, cmd: str, timeout: float = 2.0) -> Optional[str]:
        """Send a CONTROL query and return the reader's reply line ("ok ...").

        The CONTROL recv loop captures the next ok/error line into
        _query_result while _query_pending is armed. Returns None on timeout or
        when there's no socket. Used for antennas.detected (auto-detection).
        """
        if not self.control_sock:
            return None
        self._query_result = None
        self._query_done.clear()
        self._query_pending.set()
        self._send_control([cmd])
        got = self._query_done.wait(timeout=timeout)
        self._query_pending.clear()
        if not got:
            logger.warning("[CONTROL] query timed out after %.1fs: %s", timeout, cmd)
            return None
        return self._query_result

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
        or fails, so the reader is always left in a working state.
        """
        power = int(self.antenna_power)
        all_ports = [1, 2, 3, 4]
        try:
            # Phase 1: light up every port so detection has something to measure.
            probe_cmds = [f"antennas.{n}.conducted_power={power}" for n in all_ports]
            probe_cmds.append("antennas.mux_sequence=" + " ".join(str(n) for n in all_ports))
            probe_cmds.append("setup.operating_mode=active")
            self._send_control(probe_cmds)
            # Give the reader time to cycle the mux and run antenna checks.
            time.sleep(1.5)

            # Phase 2: ask which ports actually have an antenna.
            reply = self._query_control("antennas.detected", timeout=2.5)
            detected = self._parse_detected_ports(reply)
            if detected:
                logger.info("[ANTENNA] detected connected ports: %s", detected)
            else:
                detected = self._parse_ports_str(self.antenna_ports_fallback)
                logger.warning(
                    "[ANTENNA] auto-detection returned nothing (reply=%r); "
                    "falling back to configured ports: %s", reply, detected,
                )
        except Exception as e:
            detected = self._parse_ports_str(self.antenna_ports_fallback)
            logger.error(
                "[ANTENNA] auto-detection failed (%s); falling back to ports %s",
                e, detected,
            )

        if not detected:
            detected = [1]  # last-ditch: at least port 1

        # Phase 3: final config — power the detected ports, disable the rest.
        final_cmds = []
        for n in all_ports:
            final_cmds.append(f"antennas.{n}.conducted_power={power if n in detected else 0}")
        final_cmds.append("antennas.mux_sequence=" + " ".join(str(n) for n in detected))
        final_cmds.append("setup.operating_mode=active")
        self._send_control(final_cmds)
        logger.info(
            "[ANTENNA] configured: ports=%s power=%d (0.1 dBm)", detected, power
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
        if self.session.id is None or self.session.bound:
            return
        sid = self.session.id
        try:
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
            #
            # Format: ISO 8601 without timezone suffix, e.g. "2026-05-12T20:05:00.123".
            now_for_reader = (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            )
            self._send_control([
                "info.time_zone=UTC",
                f"info.time={now_for_reader}",
            ])
            logger.info("[SESSION] set reader zone=UTC and pushed host UTC clock: %s", now_for_reader)
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
            self.session.bound = True
        except Exception as e:
            logger.error("[SESSION] configuration failed: %s", e)

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
    def _now_iso() -> str:
        # ISO8601 UTC with milliseconds and trailing Z
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

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
            ts = self._now_iso()

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
        if "antenna" in kv:
            fields["antenna"] = int(kv["antenna"])
        if "rssi" in kv:
            fields["rssi"] = int(kv["rssi"])
        if event_type == "arrive" and "first" in kv:
            fields["first"] = kv["first"]
        if event_type == "depart" and "last" in kv:
            fields["last"] = kv["last"]

        return TagEvent(**fields)

    def _stdin_loop(self):
        while not self._stop_event.is_set():
            line = sys.stdin.readline()
            if not line:
                time.sleep(0.05)
                continue
            c = line.strip()
            if not c:
                continue
            try:
                if self.control_sock:
                    self.control_sock.sendall((c + "\r\n").encode("utf-8"))
                    logger.info("[CONTROL] >> %s", c)
            except OSError as e:
                logger.error("[CONTROL] send error: %s", e)
                break
