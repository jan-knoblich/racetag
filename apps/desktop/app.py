#!/usr/bin/env python3
"""Racetag desktop app — pywebview shell bundling FastAPI + static frontend.

Design:
  Single-origin: the same FastAPI app serves both the API and the static
  frontend via app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True))
  mounted AFTER all API routes.  This keeps fetch("/events/tag/batch") and
  every other relative-URL call in the frontend working without any port
  injection or extra server.

  Port: picked dynamically at startup via bind-to-zero; no hard-coded ports,
  no cross-run conflicts.

  Threading model:
    - uvicorn runs on a background daemon thread with its own asyncio loop.
    - pywebview.start() runs on the main thread (required on macOS/WKWebView).
    - On window close pywebview returns; we flip server.should_exit which
      causes uvicorn's serve() coroutine to finish its shutdown sequence.
    - The _ServerHandle container lets the main thread reach the server object
      that lives inside the background thread without sharing mutable state
      across threads beyond a single flag write.

  W-073: Reader-service subprocess.
    The reader-service is spawned as a subprocess before the pywebview window
    opens, and terminated after the window closes.  The subprocess entry point
    is resolved via _reader_service_entry() which handles both source-tree and
    PyInstaller frozen modes.

    Frozen mode dispatch: when PyInstaller freezes the app into a single
    binary, sys.executable is the Racetag binary.  We re-invoke ourselves with
    "--reader-service" which routes to racetag_reader_service.main().

    Set RACETAG_BUNDLED_READER=0 to skip spawning (useful during development
    when running a separate reader with mocks).
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent

# ---------------------------------------------------------------------------
# Frozen-mode path resolution (PyInstaller).
# When frozen, all bundled data lives under sys._MEIPASS.
# We bundle backend source as "backend_src/" and frontend as "frontend/".
# In source mode we fall back to the repo layout.
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    _BUNDLE = Path(sys._MEIPASS)  # noqa: SLF001
    BACKEND_SRC = _BUNDLE / "backend_src"
    FRONTEND_DIR = _BUNDLE / "frontend"
else:
    BACKEND_SRC = REPO_ROOT / "apps" / "backend" / "racetag-backend"
    FRONTEND_DIR = REPO_ROOT / "apps" / "frontend"

# ---------------------------------------------------------------------------
# Reader-service subprocess (W-073)
# ---------------------------------------------------------------------------

# Module-level handle to the reader-service Popen object.  Set by
# _spawn_reader_service(); read by _stop_reader_service().
_reader_proc: "subprocess.Popen | None" = None


def _reader_service_entry() -> list:
    """Return the argv list used to spawn the reader-service.

    Source (non-frozen) mode:
        [sys.executable, "<repo>/apps/reader-service/src/racetag_reader_service.py"]

    Frozen (PyInstaller) mode:
        [sys.executable, "--reader-service"]
        The main entry point dispatches "--reader-service" to
        racetag_reader_service.main() before the UI path runs.
    """
    if getattr(sys, "frozen", False):
        # sys.executable is the Racetag binary; re-invoke with dispatch flag.
        return [sys.executable, "--reader-service"]
    # Source mode: invoke the script directly.
    reader_script = REPO_ROOT / "apps" / "reader-service" / "src" / "racetag_reader_service.py"
    return [sys.executable, str(reader_script)]


def _spawn_reader_service(backend_url: str) -> "subprocess.Popen | None":
    """Spawn the reader-service subprocess and return the Popen handle.

    Returns None if RACETAG_BUNDLED_READER=0 (developer opt-out).
    """
    global _reader_proc

    if os.environ.get("RACETAG_BUNDLED_READER", "1") == "0":
        print("RACETAG_BUNDLED_READER=0 — skipping reader-service spawn", flush=True)
        return None

    reader_ip = os.environ.get("READER_IP", "192.168.1.130")
    min_lap = os.environ.get("MIN_LAP_INTERVAL_S", "10")

    argv = _reader_service_entry() + [
        "--ip", reader_ip,
        "--backend-url", backend_url,
        "--min-lap-interval", min_lap,
    ]

    env = os.environ.copy()
    env["READER_IP"] = reader_ip
    env["BACKEND_URL"] = backend_url
    env["MIN_LAP_INTERVAL_S"] = min_lap

    # Add reader-service src to PYTHONPATH so its local imports resolve when
    # invoked as a plain script (source mode only; frozen mode uses bundle).
    if not getattr(sys, "frozen", False):
        reader_src = str(REPO_ROOT / "apps" / "reader-service" / "src")
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{reader_src}:{existing_pp}" if existing_pp else reader_src

    print(f"Spawning reader-service: {' '.join(argv)}", flush=True)
    try:
        _reader_proc = subprocess.Popen(argv, env=env)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to spawn reader-service: {exc}", file=sys.stderr)
        _reader_proc = None

    return _reader_proc


def _stop_reader_service() -> None:
    """Terminate the reader-service subprocess with a 5 s grace period."""
    global _reader_proc
    proc = _reader_proc
    if proc is None:
        return
    _reader_proc = None

    if proc.poll() is not None:
        return  # already exited

    print("Stopping reader-service…", flush=True)
    proc.terminate()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        print("reader-service did not stop in 5 s — killing", file=sys.stderr)
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Env bootstrap — must run before importing the backend module so that
# RACETAG_DATA_DIR is set before module-level code in the backend fires.
# ---------------------------------------------------------------------------

def _bootstrap_env() -> None:
    """Set RACETAG_DATA_DIR to a writable user location if not already set.

    The backend defaults to "./data" (relative to cwd), which breaks in a
    packaged .app bundle where the bundle dir is read-only.  We redirect to
    ~/.racetag/data so the path always resolves to something writable.
    """
    os.environ.setdefault(
        "RACETAG_DATA_DIR",
        str(Path.home() / ".racetag" / "data"),
    )
    Path(os.environ["RACETAG_DATA_DIR"]).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Free-port helper
# ---------------------------------------------------------------------------

def _pick_free_port() -> int:
    """Bind to port 0 and let the OS hand back a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Combined FastAPI app (backend + static frontend)
# ---------------------------------------------------------------------------

def _build_combined_app():
    """Import the backend FastAPI app and mount the frontend static files.

    StaticFiles is mounted at "/" AFTER all existing API routes so API paths
    (/events/tag/batch, /classification, /riders, /stream, ...) win over the
    catch-all static handler.  html=True makes a bare "/" request serve
    index.html.

    We use importlib to load the backend app.py by explicit file path rather
    than a bare `import app`, because the desktop entry-point is also named
    app.py and would shadow the backend module when both are on sys.path.
    The backend module is registered under the key "racetag_backend_app" so it
    does not collide with any other "app" in sys.modules.
    """
    import importlib.util  # noqa: PLC0415

    backend_app_path = BACKEND_SRC / "app.py"
    spec = importlib.util.spec_from_file_location(
        "racetag_backend_app",
        str(backend_app_path),
        submodule_search_locations=[str(BACKEND_SRC)],
    )
    # Ensure the backend's own sub-modules (domain, storage, models_api …) are
    # findable by adding BACKEND_SRC to sys.path before executing the module.
    if str(BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(BACKEND_SRC))

    backend_module = importlib.util.module_from_spec(spec)
    sys.modules["racetag_backend_app"] = backend_module
    spec.loader.exec_module(backend_module)

    backend_app = backend_module.app  # FastAPI instance

    from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

    backend_app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )
    return backend_app


# ---------------------------------------------------------------------------
# Uvicorn server thread
# ---------------------------------------------------------------------------

class _ServerHandle:
    """Thin container so the main thread can signal the background server."""

    def __init__(self) -> None:
        self._server = None  # set by the background thread before ready fires

    def set_server(self, server) -> None:
        self._server = server

    def stop(self) -> None:
        """Ask uvicorn to exit gracefully."""
        if self._server is not None:
            self._server.should_exit = True


def _run_server(
    app,
    port: int,
    handle: _ServerHandle,
    ready_event: threading.Event,
    shutdown_event: threading.Event,
) -> None:
    """Run uvicorn in its own asyncio event loop on this thread.

    Stores the Server instance in *handle* before signalling *ready_event*,
    so the main thread can call handle.stop() after the window closes.
    Sets *shutdown_event* when the serve() coroutine returns.
    """
    import asyncio
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    handle.set_server(server)

    async def _serve():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        ready_event.set()
        await task

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
    finally:
        loop.close()
    shutdown_event.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # ---------------------------------------------------------------------------
    # Frozen-mode dispatch (W-073): when re-invoked as "--reader-service", hand
    # off to the reader-service main() immediately.  This keeps one binary that
    # can play two roles without shipping a second interpreter.
    # ---------------------------------------------------------------------------
    if "--reader-service" in sys.argv:
        # Reader-service src must be on sys.path so its relative imports work.
        if getattr(sys, "frozen", False):
            # In frozen mode, _MEIPASS contains the bundled reader-service src.
            reader_src_in_bundle = str(Path(sys._MEIPASS) / "reader_src")  # noqa: SLF001
            if reader_src_in_bundle not in sys.path:
                sys.path.insert(0, reader_src_in_bundle)
        else:
            reader_src = str(REPO_ROOT / "apps" / "reader-service" / "src")
            if reader_src not in sys.path:
                sys.path.insert(0, reader_src)

        # Remove our dispatch flag before forwarding to the reader's argparser.
        reader_argv = [a for a in sys.argv[1:] if a != "--reader-service"]
        import racetag_reader_service  # noqa: PLC0415
        sys.exit(racetag_reader_service.main(reader_argv))

    _bootstrap_env()

    app = _build_combined_app()
    port = _pick_free_port()

    handle = _ServerHandle()
    ready = threading.Event()
    done = threading.Event()

    server_thread = threading.Thread(
        target=_run_server,
        args=(app, port, handle, ready, done),
        daemon=True,
    )
    server_thread.start()

    if not ready.wait(timeout=10):
        print("Server failed to start within 10 s", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"Racetag backend listening on {url}", flush=True)

    # Spawn the reader-service subprocess (W-073).
    _spawn_reader_service(backend_url=url)

    import webview  # noqa: PLC0415  (optional dep; import late so tests skip it)

    webview.create_window("Racetag", url, width=1280, height=800)
    webview.start()  # blocks on main thread until the window is closed

    # Window closed — stop reader then signal uvicorn.
    _stop_reader_service()
    handle.stop()
    done.wait(timeout=3)


if __name__ == "__main__":
    main()
