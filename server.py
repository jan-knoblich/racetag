"""
Sirit INfinity 510 minimal client

"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import re
from datetime import datetime
import time
from typing import List, Optional


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# --- Coloring helpers ---
_COLORIZE = True
class _C:
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"
    # Colors
    GREEN = "\x1b[32m"
    RED = "\x1b[31m"
    CYAN = "\x1b[36m"
    YELLOW = "\x1b[33m"

def _color(s: str, col: str) -> str:
    if not _COLORIZE:
        return s
    return f"{col}{s}{_C.RESET}"


def connect_socket(ip: str, port: int, name: str) -> Optional[socket.socket]:
    print(f"[{_ts()}] Connecting to {name} at {ip}:{port}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(None)
        s.connect((ip, port))
    except ConnectionRefusedError:
        print(f"[{_ts()}] {name} connection refused at {ip}:{port}.")
        return None
    except TimeoutError:
        print(f"[{_ts()}] {name} connect timeout to {ip}:{port}.")
        return None
    except OSError as e:
        print(f"[{_ts()}] {name} failed to connect to {ip}:{port}: {e}")
        return None
    print(f"[{_ts()}] {name} connected.")
    return s


def start_receiver_thread(name: str, sock: socket.socket, on_message, raw: bool=False) -> threading.Thread:
    def loop():
        buffer = ""
        delim = "\r\n\r\n"
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    print(f"[{_ts()}] {name} connection closed by the reader.")
                    break
                data = chunk.decode("utf-8", errors="replace")
                if raw:
                    print(f"[{_ts()}] [{name}] <<RAW_CHUNK {len(chunk)} bytes>> {repr(data)}")
                buffer += data
                while True:
                    idx = buffer.find(delim)
                    if idx == -1:
                        break
                    msg, buffer = buffer[:idx], buffer[idx + len(delim):]
                    msg = msg.strip("\r\n")
                    if msg:
                        if raw:
                            print(f"[{_ts()}] [{name}] <<RAW_MSG>> {msg}")
                        on_message(name, msg)
        except OSError as e:
            print(f"[{_ts()}] {name} socket error: {e}")
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            sock.close()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def _print_and_parse(name: str, msg: str):
    if name.upper() == "EVENT":
        # Capture session id if it appears here (fallback)
        if _SESSION.get("id") is None:
            m = re.search(r"event\.connection\s+id\s*=\s*(\d+)", msg, re.IGNORECASE)
            if m:
                _SESSION["id"] = int(m.group(1))
                print(f"[{_ts()}] [SESSION] obtained id from EVENT: {_SESSION['id']}")
                _send_bind_and_registration()

    # Presence dedup for event.tag.arrive/depart
    if name.upper() == "EVENT":
        low_msg = msg.lower()
        if "event.tag.arrive" in low_msg:
            tagid = _extract_tag_id(msg)
            if tagid and _mark_present(tagid):
                label = _color("ARRIVE", _C.GREEN)
                print(f"[{_ts()}] [{name}] [{label}] {msg}")
                _maybe_print_tags(name, msg)
            return
        elif "event.tag.depart" in low_msg:
            tagid = _extract_tag_id(msg)
            if tagid:
                _mark_absent(tagid)
                label = _color("DEPART", _C.RED)
                print(f"[{_ts()}] [{name}] [{label}] {msg}")
                _maybe_print_tags(name, msg)
            return

    # Default path (non-arrive/depart)
    base = f"[{_ts()}] [{name}] {msg}"
    if name.upper() == "EVENT":
        base = f"[{_ts()}] [{name}] [{_color('EVENT', _C.CYAN)}] {msg}"
    elif name.upper() == "CONTROL":
        base = f"[{_ts()}] [{name}] [{_color('CTRL', _C.YELLOW)}] {msg}"
    print(base)
    _maybe_print_tags(name, msg)




def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sirit Infinity 510 minimal client (CONTROL then EVENT)")
    parser.add_argument("--ip", required=True, help="Reader IP address")
    parser.add_argument("--event-port", type=int, default=50008, help="Event channel port")
    parser.add_argument("--control-port", type=int, default=50007, help="Control channel port")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--interactive", action="store_true", help="Allow typing CONTROL commands (stdin)")
    parser.add_argument("--raw", action="store_true", help="Print raw chunks/messages received on sockets")
    parser.add_argument("--init_commands_file", help="Path to file with configuration commands sent AFTER reader.events.bind once session id known (defaults to 'init_commands')")
    args = parser.parse_args(argv)

    global _COLORIZE
    _COLORIZE = not args.no_color

    ip = args.ip

    # Expose config path (optional, defaults to 'init_commands')
    global INIT_COMMANDS_FILE_PATH
    INIT_COMMANDS_FILE_PATH = args.init_commands_file or "init_commands"

    # 1. Connect CONTROL first
    control_sock = connect_socket(ip, args.control_port, "CONTROL")
    if not control_sock:
        return 1

    # Keep a reference to the persistent CONTROL socket and a send lock
    global CONTROL_SOCKET, CONTROL_SEND_LOCK
    CONTROL_SOCKET = control_sock
    if 'CONTROL_SEND_LOCK' not in globals():
        CONTROL_SEND_LOCK = threading.Lock()

    # Start CONTROL receiver thread
    start_receiver_thread("CONTROL", control_sock, _print_and_parse, raw=args.raw)

    # If CONTROL is interactive, spawn stdin loop
    if args.interactive:
        threading.Thread(target=_stdin_loop, args=(control_sock,), daemon=True).start()

    # 2. Connect EVENT
    event_sock = connect_socket(ip, args.event_port, "EVENT")
    if not event_sock:
        print("EVENT channel unavailable; exiting.")
        return 2
    start_receiver_thread("EVENT", event_sock, _print_and_parse, raw=args.raw)

    # 3. Wait for session id; if not yet known, rely on EVENT to announce it.
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] Interrupted by user.")
    finally:
        try:
            if CONTROL_SOCKET:
                CONTROL_SOCKET.sendall(("setup.operating_mode=standby\r\n").encode("utf-8"))
                print(f"[{_ts()}] [CONTROL] >> setup.operating_mode=standby")
        except Exception:
            pass
    return 0


# --- Simple NEW tag tracking ---
_SEEN_TAGS = set()
_PRESENT_TAGS = set()

def _record_tag_seen(tag_hex: str) -> bool:
    if tag_hex not in _SEEN_TAGS:
        _SEEN_TAGS.add(tag_hex)
        return True
    return False


def _mark_present(tagid: str) -> bool:
    """Returns True only when tag becomes newly present (ARRIVE not seen before)."""
    key = tagid.upper()
    if key in _PRESENT_TAGS:
        return False
    _PRESENT_TAGS.add(key)
    return True


def _mark_absent(tagid: str):
    key = tagid.upper()
    if key in _PRESENT_TAGS:
        _PRESENT_TAGS.remove(key)


def _extract_tag_id(msg: str) -> Optional[str]:
    # Expect tag_id as 0x-prefixed hex
    m = re.search(r"tag_id\s*=\s*0x([0-9A-Fa-f]{8,64})", msg)
    return m.group(1) if m else None


def _maybe_print_tags(name: str, msg: str):
    tagid = _extract_tag_id(msg)
    if not tagid:
        return
    tag_hex = tagid.upper()
    is_new = _record_tag_seen(tag_hex)
    if is_new:
        prefix = f"[TAG][{_color('NEW', _C.GREEN)}]"
    else:
        prefix = f"[{_color('TAG', _C.DIM)}]"
    print(f"[{_ts()}] [{name}] {prefix} TAG={tag_hex}")


_SESSION: dict = {"id": None, "bound": False}


def _stdin_loop(control_sock: socket.socket):
    while True:
        line = sys.stdin.readline()
        if not line:
            time.sleep(0.05)
            continue
        c = line.strip()
        if not c:
            continue
        try:
            control_sock.sendall((c + "\r\n").encode("utf-8"))
            print(f"[{_ts()}] [CONTROL] >> {c}")
        except OSError as e:
            print(f"[{_ts()}] [CONTROL] send error: {e}")
            break


def _send_bind_and_registration():
    if not _SESSION.get("id") or _SESSION.get("bound"):
        return
    sid = _SESSION["id"]
    try:
        # First only bind
        _send_multi_control([f"reader.events.bind(id = {sid})"])
        print(f"[{_ts()}] [SESSION] bound event channel id {sid}")
        # Then load extra config from file if provided
        cfg_path = getattr(sys.modules['__main__'], 'INIT_COMMANDS_FILE_PATH', None)
        extra_cmds: List[str] = []
        if cfg_path:
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        extra_cmds.append(line)
            except Exception as e:
                print(f"[{_ts()}] [CONFIG] Could not read init commands file '{cfg_path}': {e}. Continuing without extra config.")
        _send_multi_control(extra_cmds)
        if extra_cmds:
            print(f"[{_ts()}] [SESSION] configuration applied ({len(extra_cmds)} commands from {cfg_path} init file)")
        else:
            print(f"[{_ts()}] [SESSION] no extra configuration commands were sent (file empty or missing)")
        _SESSION["bound"] = True
    except Exception as e:
        print(f"[{_ts()}] [SESSION] configuration failed: {e}")


def _send_multi_control(cmds: List[str]):
    # Prefer sending through the existing CONTROL socket to keep session/context
    ctrl = globals().get('CONTROL_SOCKET')
    if ctrl is not None:
        try:
            lock = globals().get('CONTROL_SEND_LOCK')
            if lock:
                with lock:
                    for c in cmds:
                        ctrl.sendall((c + "\r\n").encode('utf-8', errors='ignore'))
                        print(f"[{_ts()}] [CONTROL] >> {c}")
                        time.sleep(0.02)
            else:
                for c in cmds:
                    ctrl.sendall((c + "\r\n").encode('utf-8', errors='ignore'))
                    print(f"[{_ts()}] [CONTROL] >> {c}")
                    time.sleep(0.02)
            return
        except OSError as e:
            print(f"[{_ts()}] [CONTROL] send error on persistent socket, falling back: {e}")




if __name__ == "__main__":
    sys.exit(main())