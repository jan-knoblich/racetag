from __future__ import annotations

import re
import socket
import sys
import threading
import time
from typing import List, Optional

from session_state import SessionState
from tag_tracker import TagTracker

from utils import _ts, _color, _C, connect_socket


class SiritClient:
    def __init__(self, ip: str, control_port: int, event_port: int, init_commands_path: Optional[str], colorize: bool, raw: bool, interactive: bool):
        self.ip = ip
        self.control_port = control_port
        self.event_port = event_port
        self.init_commands_path = init_commands_path or "init_commands"
        self.colorize = colorize
        self.raw = raw
        self.interactive = interactive

        self.session = SessionState()
        self.tags = TagTracker()
        self.control_sock: Optional[socket.socket] = None
        self.event_sock: Optional[socket.socket] = None
        self._control_lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self):
        self.control_sock = connect_socket(self.ip, self.control_port, "CONTROL")
        if not self.control_sock:
            raise RuntimeError("CONTROL connection failed")
        threading.Thread(target=self._recv_loop, args=("CONTROL", self.control_sock), daemon=True).start()
        if self.interactive:
            threading.Thread(target=self._stdin_loop, daemon=True).start()
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
        try:
            if self.control_sock:
                self._send_control(["setup.operating_mode=standby"])
        except Exception:
            pass
        self._stop_event.set()
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
                tag = self._extract_tag_id(msg)
                if tag and self.tags.mark_present(tag):
                    label = _color("ARRIVE", _C.GREEN) if self.colorize else "ARRIVE"
                    print(f"[{_ts()}] [{name}] [{label}] {msg}")
                    self._maybe_print_tag(name, msg)
                return
            if "event.tag.depart" in low:
                tag = self._extract_tag_id(msg)
                if tag:
                    self.tags.mark_absent(tag)
                    label = _color("DEPART", _C.RED) if self.colorize else "DEPART"
                    print(f"[{_ts()}] [{name}] [{label}] {msg}")
                    self._maybe_print_tag(name, msg)
                return
        base = f"[{_ts()}] [{name}] {msg}"
        if name.upper() == "EVENT":
            tag = "EVENT"
            base = f"[{_ts()}] [{name}] [{_color(tag, _C.CYAN) if self.colorize else tag}] {msg}"
        elif name.upper() == "CONTROL":
            tag = "CTRL"
            base = f"[{_ts()}] [{name}] [{_color(tag, _C.YELLOW) if self.colorize else tag}] {msg}"
        print(base)
        self._maybe_print_tag(name, msg)

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
    def _extract_tag_id(msg: str) -> Optional[str]:
        m = re.search(r"tag_id\s*=\s*0x([0-9A-Fa-f]{8,64})", msg)
        return m.group(1) if m else None

    def _maybe_print_tag(self, name: str, msg: str):
        tag = self._extract_tag_id(msg)
        if not tag:
            return
        tag_hex = tag.upper()
        is_new = self.tags.record_seen(tag_hex)
        if is_new:
            prefix = f"[TAG][{_color('NEW', _C.GREEN)}]" if self.colorize else "[TAG][NEW]"
        else:
            prefix = f"[{_color('TAG', _C.DIM)}]" if self.colorize else "[TAG]"
        print(f"[{_ts()}] [{name}] {prefix} TAG={tag_hex}")

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
