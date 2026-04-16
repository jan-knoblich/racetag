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

  TODO (W-073): spawn the reader-service subprocess here, passing
  --backend-url http://127.0.0.1:<port>.  Pattern:
      reader_proc = subprocess.Popen([
          sys.executable, "-m", "racetag_reader_service.cli",
          "--ip", reader_ip,
          "--backend-url", f"http://127.0.0.1:{port}",
      ])
  Kill it (reader_proc.terminate()) after webview.start() returns.
"""

import os
import socket
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
BACKEND_SRC = REPO_ROOT / "apps" / "backend" / "racetag-backend"
FRONTEND_DIR = REPO_ROOT / "apps" / "frontend"


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

    import webview  # noqa: PLC0415  (optional dep; import late so tests skip it)

    webview.create_window("Racetag", url, width=1280, height=800)
    webview.start()  # blocks on main thread until the window is closed

    # Window closed — signal uvicorn to stop and wait briefly for clean teardown.
    handle.stop()
    done.wait(timeout=3)


if __name__ == "__main__":
    main()
