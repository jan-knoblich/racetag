from __future__ import annotations

import re
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from session_state import SessionState
from tag_tracker import TagTracker

from utils import _ts, _color, _C, connect_socket
from models import TagEvent, EventType
from backend_client import BackendClient, HttpBackendClient, MockBackendClient


class SiritClient:
    def __init__(self, ip: str, control_port: int, event_port: int, init_commands_path: Optional[str], colorize: bool, raw: bool, interactive: bool, backend_url: Optional[str] = None, backend_token: Optional[str] = None, backend_transport: str = "http"):
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

        self.session = SessionState()
        self.tags = TagTracker()
        self.control_sock: Optional[socket.socket] = None
        self.event_sock: Optional[socket.socket] = None
        self._control_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopping = threading.Event()
        self._stopped = threading.Event()
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
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n[{_ts()}] Interrupted by user.")
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
                    print(f"[{_ts()}] {name} connection closed by the reader.")
                    break
                data = chunk.decode("utf-8", errors="replace")
                if self.raw:
                    print(f"[{_ts()}] [{name}] <<RAW_CHUNK {len(chunk)} bytes>> {repr(data)}")
                buffer += data
                while True:
                    idx = buffer.find(delim)
                    if idx == -1:
                        break
                    msg, buffer = buffer[:idx], buffer[idx + len(delim):]
                    msg = msg.strip("\r\n")
                    if msg:
                        if self.raw:
                            print(f"[{_ts()}] [{name}] <<RAW_MSG>> {msg}")
                        self._handle_message(name, msg)
        except OSError as e:
            print(f"[{_ts()}] {name} socket error: {e}")
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
            if self.session.id is None:
                m = re.search(r"event\.connection\s+id\s*=\s*(\d+)", msg, re.IGNORECASE)
                if m:
                    self.session.id = int(m.group(1))
                    print(f"[{_ts()}] [SESSION] obtained id from EVENT: {self.session.id}")
                    self._maybe_bind_and_config()
            low = msg.lower()
            if "event.tag.arrive" in low:
                ev = self._parse_event_message("arrive", msg)
                if ev and self.tags.mark_present(ev.tag_id):
                    label = _color("ARRIVE", _C.GREEN) if self.colorize else "ARRIVE"
                    print(f"[{_ts()}] [{name}] [{label}] {msg}")
                    self._print_tag_id(ev.tag_id)
                    self._emit_event(ev)
                return
            if "event.tag.depart" in low:
                ev = self._parse_event_message("depart", msg)
                if ev:
                    self.tags.mark_absent(ev.tag_id)
                    label = _color("DEPART", _C.RED) if self.colorize else "DEPART"
                    print(f"[{_ts()}] [{name}] [{label}] {msg}")
                    self._print_tag_id(ev.tag_id)
                    self._emit_event(ev)
                return
        base = f"[{_ts()}] [{name}] {msg}"
        if name.upper() == "EVENT":
            tag = "EVENT"
            base = f"[{_ts()}] [{name}] [{_color(tag, _C.CYAN) if self.colorize else tag}] {msg}"
        elif name.upper() == "CONTROL":
            tag = "CTRL"
            base = f"[{_ts()}] [{name}] [{_color(tag, _C.YELLOW) if self.colorize else tag}] {msg}"
            # Capture reader serial number once when still unknown
            if self.reader_serial is None:
                m = re.match(r"ok\s+([0-9A-Fa-f]{8,})\b", msg.strip())
                if m:
                    self.reader_serial = m.group(1).upper()
                    print(f"[{_ts()}] [READER] serial_number={self.reader_serial}")
        print(base)

    def _send_control(self, cmds: List[str]):
        if not self.control_sock:
            return
        try:
            with self._control_lock:
                for c in cmds:
                    self.control_sock.sendall((c + "\r\n").encode("utf-8", errors="ignore"))
                    print(f"[{_ts()}] [CONTROL] >> {c}")
                    time.sleep(0.02)
        except OSError as e:
            print(f"[{_ts()}] [CONTROL] send error: {e}")

    def _maybe_bind_and_config(self):
        if self.session.id is None or self.session.bound:
            return
        sid = self.session.id
        try:
            self._send_control([f"reader.events.bind(id = {sid})"])
            print(f"[{_ts()}] [SESSION] bound event channel id {sid}")
            extra_cmds: List[str] = []
            if self.init_commands_path:
                try:
                    with open(self.init_commands_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            extra_cmds.append(line)
                except Exception as e:
                    print(f"[{_ts()}] [CONFIG] Could not read init commands file '{self.init_commands_path}': {e}. Continuing without extra config.")
            self._send_control(extra_cmds)
            if extra_cmds:
                print(f"[{_ts()}] [SESSION] configuration applied ({len(extra_cmds)} commands from {self.init_commands_path} init file)")
            else:
                print(f"[{_ts()}] [SESSION] no extra configuration commands were sent (file empty or missing)")
            self.session.bound = True
        except Exception as e:
            print(f"[{_ts()}] [SESSION] configuration failed: {e}")

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
        print(f"[{_ts()}] [EVENT] {prefix} TAG={tag_hex}")

    def _emit_event(self, ev: TagEvent) -> None:
        if not self._backend:
            return
        try:
            self._backend.send(ev)
        except Exception as e:
            print(f"[{_ts()}] [BACKEND] error queueing event: {e}")

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

        fields: Dict[str, object] = {
            "source": "sirit-510",
            "reader_ip": self.ip,
            "timestamp": self._now_iso(),
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
                    print(f"[{_ts()}] [CONTROL] >> {c}")
            except OSError as e:
                print(f"[{_ts()}] [CONTROL] send error: {e}")
                break
