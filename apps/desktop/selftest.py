"""Headless end-to-end self-test (plan A4, contract 4.3).

``Racetag.exe --selftest`` / ``python app.py --selftest`` proves that a build
can import and run every component without a GUI and without touching the
operator's ``~/.racetag``:

1. temp data/log dirs, no instance lock;
2. build the combined backend + frontend app, start uvicorn on a free port;
3. ``GET /config`` (with ``desktop`` and ``version``), ``/races``, ``/``,
   ``/strings.js``, ``/tooltips.js`` all answer 200;
4. start the reader supervisor against a closed local port with discovery off;
5. within 15 s ``GET /reader/status`` reports a state other than ``unknown``,
   i.e. the reader-service booted, imported, logged and sent a heartbeat;
6. stop everything.

Exit code 0 only when every step passed. Steps are logged to
``<RACETAG_LOG_DIR or temp>/selftest.log`` and to stdout when it exists.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import desktop_logging
from reader_supervisor import ReaderSupervisor

log = logging.getLogger("racetag.selftest")

SERVER_READY_TIMEOUT_S = 10.0
READER_STATUS_TIMEOUT_S = 15.0
TOTAL_BUDGET_S = 30.0
HTTP_TIMEOUT_S = 5.0
STATIC_PATHS = ("/", "/strings.js", "/tooltips.js")


# The probes only talk to the local server: never through a configured proxy
# (Windows Internet Options would otherwise catch 127.0.0.1).
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SelftestFailure(Exception):
    """A step did not meet its expectation."""


def _closed_port() -> int:
    """A local port with nothing listening on it (bound once, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(url: str) -> Tuple[int, str, bytes]:
    try:
        with _LOCAL_OPENER.open(url, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310 - local server
            return resp.status, resp.headers.get("content-type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type", "") if exc.headers else "", exc.read()


def _tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(not available)"


def run_selftest(desktop=None) -> int:
    """Run all steps; return the process exit code.

    ``desktop`` is the desktop ``app`` module. ``app.main()`` passes itself
    (it runs as ``__main__``); without it the module is imported.
    """
    if desktop is None:
        import app as desktop  # noqa: PLC0415

    t0 = time.monotonic()
    tmp_root = Path(tempfile.mkdtemp(prefix="racetag-selftest-"))
    data_dir = tmp_root / "data"
    log_dir = Path(os.environ.get("RACETAG_LOG_DIR") or (tmp_root / "logs"))

    # The backend reads these at import time, so they must be set before the
    # app is built. The reader-service child inherits them.
    os.environ["RACETAG_DATA_DIR"] = str(data_dir)
    os.environ["RACETAG_LOG_DIR"] = str(log_dir)
    os.environ["RACETAG_NO_DIALOGS"] = "1"
    os.environ.pop("RACETAG_API_KEY", None)  # the probes below send no key
    os.environ.pop("READER_IP", None)
    version = desktop._read_version()  # noqa: SLF001
    if version:
        os.environ.setdefault("RACETAG_VERSION", version)
    data_dir.mkdir(parents=True, exist_ok=True)

    desktop_logging.setup_logging(log_dir)
    step_handler = logging.FileHandler(log_dir / "selftest.log", encoding="utf-8")
    step_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(step_handler)
    log.setLevel(logging.INFO)

    handle = desktop._ServerHandle()  # noqa: SLF001
    ready = threading.Event()
    done = threading.Event()
    server_thread: Optional[threading.Thread] = None
    supervisor: Optional[ReaderSupervisor] = None
    state = {"url": None}
    failed = False

    def step_build() -> None:
        nonlocal supervisor, server_thread
        backend_app = desktop._build_combined_app()  # noqa: SLF001
        port = desktop._pick_free_port()  # noqa: SLF001
        url = f"http://127.0.0.1:{port}"
        state["url"] = url
        control_port, event_port = _closed_port(), _closed_port()
        extra = [
            "--control-port", str(control_port),
            "--event-port", str(event_port),
            "--no-discover",
            "--heartbeat-interval", "0.5",
        ]
        supervisor = ReaderSupervisor(
            argv_builder=lambda: desktop._reader_command(url, "127.0.0.1", None, extra),  # noqa: SLF001
            pid_file=tmp_root / "reader-service.pid",
            backoff_s=(1.0,),
            stderr_log=log_dir / "reader-stderr.log",
        )
        backend_app.state.reader_controller = supervisor
        server_thread = threading.Thread(
            target=desktop._run_server,  # noqa: SLF001
            args=(backend_app, port, handle, ready, done),
            name="uvicorn-selftest",
            daemon=True,
        )
        server_thread.start()
        if not ready.wait(SERVER_READY_TIMEOUT_S):
            raise SelftestFailure(f"server not ready within {SERVER_READY_TIMEOUT_S:.0f} s")
        log.info("server listening on %s", url)

    def step_http() -> None:
        url = state["url"]
        status, _, body = _http_get(url + "/config")
        if status != 200:
            raise SelftestFailure(f"GET /config returned {status}")
        cfg = json.loads(body)
        missing = [k for k in ("desktop", "version") if k not in cfg]
        if missing:
            raise SelftestFailure(f"GET /config lacks {missing}: {cfg}")
        if cfg.get("desktop") is not True:
            raise SelftestFailure(f"GET /config desktop is {cfg.get('desktop')!r}, expected true")
        status, _, _ = _http_get(url + "/races")
        if status != 200:
            raise SelftestFailure(f"GET /races returned {status}")
        for path in STATIC_PATHS:
            status, content_type, _ = _http_get(url + path)
            if status != 200:
                raise SelftestFailure(f"GET {path} returned {status}")
            if path == "/" and "text/html" not in content_type:
                raise SelftestFailure(f"GET / content-type is {content_type!r}")

    def step_reader_start() -> None:
        supervisor.start()

    def step_reader_status() -> None:
        url = state["url"]
        deadline = time.monotonic() + READER_STATUS_TIMEOUT_S
        last = "no response"
        while time.monotonic() < deadline:
            try:
                status, _, body = _http_get(url + "/reader/status")
                if status == 200:
                    reader_state = json.loads(body).get("state")
                    last = f"state={reader_state!r}"
                    if reader_state not in (None, "unknown"):
                        log.info("reader-service reported state %r", reader_state)
                        return
                else:
                    last = f"HTTP {status}"
            except (OSError, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        raise SelftestFailure(
            f"no reader heartbeat within {READER_STATUS_TIMEOUT_S:.0f} s "
            f"(last: {last}; supervisor: {supervisor.status()})"
        )

    steps: List[Tuple[str, Callable[[], None]]] = [
        ("build app and start server", step_build),
        ("HTTP endpoints", step_http),
        ("start reader-service", step_reader_start),
        ("reader-service heartbeat", step_reader_status),
    ]

    log.info("Racetag self-test %s started (temp dir %s, logs %s)", version or "unknown", tmp_root, log_dir)
    try:
        for name, fn in steps:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - every failure becomes a FAIL line
                failed = True
                log.error("FAIL %s: %s", name, exc, exc_info=not isinstance(exc, SelftestFailure))
                break
            log.info("PASS %s", name)
    finally:
        if supervisor is not None:
            supervisor.stop(timeout_s=5.0)
        handle.stop()
        if server_thread is not None:
            done.wait(timeout=5.0)
        if failed:
            # Read every tail before logging any of them: shell.log receives
            # these lines too.
            names = ("reader-stderr.log", "reader.log", "backend.log", "shell.log")
            tails = [(name, _tail(log_dir / name)) for name in names]
            for name, text in tails:
                log.info("--- tail of %s ---\n%s", name, text)
        elapsed = time.monotonic() - t0
        if elapsed > TOTAL_BUDGET_S:
            log.warning("self-test took %.1f s (budget %.0f s)", elapsed, TOTAL_BUDGET_S)
        log.info("self-test %s in %.1f s", "FAILED" if failed else "PASSED", elapsed)
        log.removeHandler(step_handler)
        step_handler.close()
        # The backend module keeps its SQLite file open; on Windows the data
        # dir can only be removed after exit, so errors are ignored.
        shutil.rmtree(data_dir, ignore_errors=True)
    return 1 if failed else 0
