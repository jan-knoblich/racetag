from __future__ import annotations

import socket
from datetime import datetime
from typing import Optional


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class _C:
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"
    GREEN = "\x1b[32m"
    RED = "\x1b[31m"
    CYAN = "\x1b[36m"
    YELLOW = "\x1b[33m"


def _color(s: str, col: str) -> str:
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
