"""In-process fake Sirit INfinity 510 for connection, discovery and reconnect tests.

Listens on 127.0.0.1 with ephemeral (or given) CONTROL and EVENT ports.

- EVENT: on accept sends ``event.connection id = N`` (N increments per
  accept, starting at ``first_connection_id``); ``send_event`` pushes lines.
- CONTROL: records every command line; answers ``info.serial_number`` with
  ``ok <serial>``, ``antennas.detected`` with ``ok <detected>`` and the bare
  ``info.time`` probe with ``ok <time>``. Other commands are only answered
  when ``answer_all`` is true: a real reader answers everything, but stray
  ``ok`` lines racing an armed query make assertions flaky.

Every message ends with ``\\r\\n\\r\\n`` like the real reader.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, List, Optional


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def reserve_port() -> int:
    """A port that is free right now (and refuses connections until reused)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeSiritServer:
    def __init__(
        self,
        serial: str = "DEADBEEF01",
        detected: str = "1 2",
        control_port: int = 0,
        event_port: int = 0,
        first_connection_id: int = 1,
        answer_time_probe: bool = True,
        answer_all: bool = False,
    ):
        self.serial = serial
        self.detected = detected
        self.answer_time_probe = answer_time_probe
        self.answer_all = answer_all
        self._requested_ports = (control_port, event_port)
        self._next_connection_id = first_connection_id
        self._lock = threading.Lock()
        self._commands: List[str] = []
        self._listeners: List[socket.socket] = []
        self._clients: List[socket.socket] = []
        self._event_clients: List[socket.socket] = []
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self.control_port = 0
        self.event_port = 0
        self.event_accepts = 0
        self.control_accepts = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> "FakeSiritServer":
        control = self._listen(self._requested_ports[0])
        event = self._listen(self._requested_ports[1])
        self.control_port = control.getsockname()[1]
        self.event_port = event.getsockname()[1]
        self._spawn(self._accept_loop, control, self._serve_control)
        self._spawn(self._accept_loop, event, self._serve_event)
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sockets = self._listeners + self._clients
            self._listeners, self._clients, self._event_clients = [], [], []
        for s in sockets:
            _close(s)
        for t in self._threads:
            t.join(timeout=2.0)

    def __enter__(self) -> "FakeSiritServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Test controls
    # ------------------------------------------------------------------

    @property
    def commands(self) -> List[str]:
        with self._lock:
            return list(self._commands)

    def count(self, prefix: str) -> int:
        return sum(1 for c in self.commands if c.startswith(prefix))

    def drop_clients(self) -> None:
        """Close every accepted connection, like a rebooting reader."""
        with self._lock:
            clients, self._clients, self._event_clients = self._clients, [], []
        for s in clients:
            _close(s)

    def send_event(self, line: str) -> None:
        payload = (line + "\r\n\r\n").encode("utf-8")
        with self._lock:
            clients = list(self._event_clients)
        for s in clients:
            try:
                s.sendall(payload)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _listen(self, port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(8)
        s.settimeout(0.1)
        with self._lock:
            self._listeners.append(s)
        return s

    def _spawn(self, target, *args) -> None:
        t = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(t)
        t.start()

    def _accept_loop(self, listener: socket.socket, serve) -> None:
        while not self._stop.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            client.settimeout(0.1)
            with self._lock:
                self._clients.append(client)
            self._spawn(serve, client)

    def _serve_event(self, client: socket.socket) -> None:
        with self._lock:
            connection_id = self._next_connection_id
            self._next_connection_id += 1
            self.event_accepts += 1
            self._event_clients.append(client)
        try:
            client.sendall(f"event.connection id = {connection_id}\r\n\r\n".encode("utf-8"))
        except OSError:
            return
        self._drain(client)

    def _drain(self, client: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                if not client.recv(4096):
                    return
            except TimeoutError:
                continue
            except OSError:
                return

    def _serve_control(self, client: socket.socket) -> None:
        with self._lock:
            self.control_accepts += 1
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = client.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\r\n" in buffer:
                raw, buffer = buffer.split(b"\r\n", 1)
                command = raw.decode("utf-8", errors="replace").strip()
                if not command:
                    continue
                with self._lock:
                    self._commands.append(command)
                reply = self._reply_for(command)
                if reply is not None:
                    try:
                        client.sendall((reply + "\r\n\r\n").encode("utf-8"))
                    except OSError:
                        return

    def _reply_for(self, command: str) -> Optional[str]:
        if command == "info.serial_number":
            return f"ok {self.serial}"
        if command == "antennas.detected":
            return f"ok {self.detected}"
        if command == "info.time":
            return "ok 2026-09-13T10:00:00.000" if self.answer_time_probe else None
        return "ok" if self.answer_all else None


class PausableProxy:
    """TCP proxy on 127.0.0.1 in front of one upstream port.

    While ``paused`` is set nothing is forwarded in either direction, but
    nothing is closed either: both sides keep writing into the proxy's
    buffers, like two TCP stacks retransmitting across a pulled cable. On
    resume the buffered bytes are delivered in order.
    """

    def __init__(self, upstream_port: int):
        self.upstream_port = upstream_port
        self.paused = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sockets: List[socket.socket] = []
        self._threads: List[threading.Thread] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._spawn(self._accept_loop)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sockets, self._sockets = self._sockets + [self._listener], []
        for s in sockets:
            _close(s)
        for t in self._threads:
            t.join(timeout=2.0)

    def _spawn(self, target, *args) -> None:
        t = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(t)
        t.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", self.upstream_port), timeout=1.0)
            except OSError:
                _close(client)
                continue
            with self._lock:
                self._sockets += [client, upstream]
            self._spawn(self._pump, client, upstream)
            self._spawn(self._pump, upstream, client)

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        pending = b""
        src.settimeout(0.02)
        while not self._stop.is_set():
            try:
                chunk = src.recv(4096)
                if not chunk:
                    break
                pending += chunk
            except TimeoutError:
                pass
            except OSError:
                break
            if pending and not self.paused.is_set():
                try:
                    dst.sendall(pending)
                except OSError:
                    break
                pending = b""
        _close(src)
        _close(dst)


def _close(s: socket.socket) -> None:
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass
