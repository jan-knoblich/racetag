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
    - A watchdog thread notices an unexpected death of the server thread and
      offers the operator a restart (plan B5).

  Reader-service child (W-073, plan A1):
    reader_supervisor.ReaderSupervisor spawns, watches and restarts the
    reader-service. It is registered on the backend as
    ``app.state.reader_controller`` (contract 2.7). The argv is rebuilt for
    every spawn from the in-process backend config, so a restart always uses
    the persisted reader IP and antenna power.

    Frozen mode dispatch: when PyInstaller freezes the app into a single
    binary, sys.executable is the Racetag binary.  We re-invoke ourselves with
    "--reader-service" which routes to racetag_reader_service.main().

    Set RACETAG_BUNDLED_READER=0 to skip spawning (useful during development
    when running a separate reader with mocks).

  Self-test: ``Racetag.exe --selftest`` / ``python app.py --selftest`` runs a
  headless end-to-end check (selftest.py, contract 4.3).
"""

import datetime as _dt
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import desktop_logging
import native
import support_bundle
from reader_supervisor import ReaderSupervisor

log = logging.getLogger("racetag.shell")

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

WINDOW_TITLE = "Racetag"
MIN_FREE_DISK_BYTES = 200 * 1024 * 1024
SERVER_START_TIMEOUT_S = 10.0
WATCHDOG_INTERVAL_S = 5.0
# A previous instance keeps the lock through its whole shutdown (WebView2
# cleanup, graceful reader-service stop, server stop), which can take ~15 s.
INSTANCE_LOCK_WAIT_S = 20.0
INSTANCE_LOCK_POLL_S = 0.25

# Shown when the window's X / Alt+F4 is used (pywebview confirm_close) and for
# pywebview's own dialog and menu labels, which are English by default.
WEBVIEW_LOCALIZATION = {
    "global.quitConfirmation": (
        "Racetag wirklich beenden?\n\n"
        "Solange Racetag geschlossen ist, werden keine Durchfahrten erfasst."
    ),
    "global.ok": "OK",
    "global.quit": "Beenden",
    "global.cancel": "Abbrechen",
    "global.saveFile": "Datei speichern",
    "windows.fileFilter.allFiles": "Alle Dateien",
    "windows.fileFilter.otherFiles": "Andere Dateitypen",
    "cocoa.menu.about": "Über",
    "cocoa.menu.services": "Dienste",
    "cocoa.menu.view": "Darstellung",
    "cocoa.menu.edit": "Bearbeiten",
    "cocoa.menu.hide": "Ausblenden",
    "cocoa.menu.hideOthers": "Andere ausblenden",
    "cocoa.menu.showAll": "Alle einblenden",
    "cocoa.menu.quit": "Beenden",
    "cocoa.menu.fullscreen": "Vollbildmodus",
    "cocoa.menu.cut": "Ausschneiden",
    "cocoa.menu.copy": "Kopieren",
    "cocoa.menu.paste": "Einsetzen",
    "cocoa.menu.selectAll": "Alles auswählen",
}

# The local backend must never be reached through a proxy. On Windows Python
# (requests and urllib) reads a manual proxy from the Internet Options, and
# its "<local>" bypass only matches dot-less host names, so 127.0.0.1 would
# be sent to the proxy. Environment no_proxy is checked before the registry.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _racetag_home() -> Path:
    return Path.home() / ".racetag"


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def _version_file() -> Path:
    """apps/desktop/VERSION in source mode; bundled next to the data in frozen mode."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "VERSION"  # noqa: SLF001
    return THIS_DIR / "VERSION"


def _read_version() -> Optional[str]:
    try:
        version = _version_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return version or None


# ---------------------------------------------------------------------------
# Reader-service command line (W-073)
# ---------------------------------------------------------------------------

def _reader_service_entry() -> list:
    """Return the argv prefix used to spawn the reader-service.

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


def _init_commands_path() -> str:
    # Absolute on purpose: the relative default 'init_commands' silently fails
    # when the app is launched with another CWD, and then no event
    # registration commands reach the reader.
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "reader_src" / "init_commands")  # noqa: SLF001
    return str(REPO_ROOT / "apps" / "reader-service" / "src" / "init_commands")


def _with_loopback_no_proxy(env) -> None:
    """Add the loopback hosts to ``NO_PROXY``/``no_proxy`` in ``env``, keeping existing entries."""
    entries: List[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        for part in (env.get(key) or "").split(","):
            part = part.strip()
            if part and part not in entries:
                entries.append(part)
    for host in _LOOPBACK_HOSTS:
        if host not in entries:
            entries.append(host)
    value = ",".join(entries)
    env["NO_PROXY"] = value
    if sys.platform != "win32":
        # Windows environment names are case-insensitive: one key is enough,
        # and two spellings in a child's environment block would be ambiguous.
        env["no_proxy"] = value


def _reader_command(
    backend_url: str,
    reader_ip: Optional[str],
    antenna_power: Optional[int],
    extra_args: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, str]]:
    """Build (argv, env) for one reader-service spawn.

    ``--ip`` is only passed when an IP is known; without it the reader-service
    starts in ``searching`` and runs discovery (contract 3.1).
    ``--stop-on-stdin-eof`` is always passed: the supervisor stops the child by
    closing its stdin, and the child also exits when the shell dies.
    """
    # Reader-side cooldown defaults to 0 (presence-union only): the backend is
    # the single lap-cooldown authority (AUDIT-2026-07 M6). Overridable via
    # MIN_LAP_INTERVAL_S for debugging.
    min_lap = os.environ.get("MIN_LAP_INTERVAL_S", "0")
    power = str(int(antenna_power)) if antenna_power else os.environ.get("ANTENNA_POWER", "300")
    antenna_ports = os.environ.get("ANTENNA_PORTS", "1 2")
    init_commands_path = _init_commands_path()

    argv = _reader_service_entry() + [
        "--backend-url", backend_url,
        "--min-lap-interval", min_lap,
        "--init_commands_file", init_commands_path,
        "--antenna-power", power,
        "--antenna-ports", antenna_ports,
        "--stop-on-stdin-eof",
    ]

    env = os.environ.copy()
    if reader_ip:
        argv += ["--ip", reader_ip]
        env["READER_IP"] = reader_ip
    else:
        # The reader-service falls back to $READER_IP; drop it so "unknown"
        # really means "search".
        env.pop("READER_IP", None)
    # stdin is the supervisor's stop pipe, never an interactive console.
    env.pop("INTERACTIVE", None)
    argv += list(extra_args)

    env["BACKEND_URL"] = backend_url
    env["MIN_LAP_INTERVAL_S"] = min_lap
    env["INIT_COMMANDS_FILE"] = init_commands_path
    env["ANTENNA_POWER"] = power
    env["ANTENNA_PORTS"] = antenna_ports
    # The reader-service posts laps and heartbeats to the local backend.
    _with_loopback_no_proxy(env)

    # Source mode: the script's sibling modules must be importable. Frozen
    # mode resolves them from the bundle.
    if not getattr(sys, "frozen", False):
        reader_src = str(REPO_ROOT / "apps" / "reader-service" / "src")
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{reader_src}{os.pathsep}{existing_pp}" if existing_pp else reader_src
    return argv, env


def _make_reader_argv_builder(
    backend_module, backend_url: str
) -> Callable[[], Tuple[List[str], Dict[str, str]]]:
    """Argv builder for the supervisor, reading config from the loaded backend.

    In-process on purpose: the controller's restart() may be called from a
    backend request thread, and an HTTP call back into the same server from
    there could block on it.
    """

    def build() -> Tuple[List[str], Dict[str, str]]:
        cfg = backend_module._effective_config()  # noqa: SLF001
        return _reader_command(
            backend_url,
            getattr(cfg, "reader_ip", None),
            getattr(cfg, "antenna_power", None),
        )

    return build


def _bundled_reader_enabled() -> bool:
    return os.environ.get("RACETAG_BUNDLED_READER", "1") != "0"


# ---------------------------------------------------------------------------
# AUDIT-2026-07 H6/H7: process-lifecycle guards.
#
# H6: a hard shell death used to orphan the reader-service. On Windows the
#     supervisor's Job Object kills the child with the shell; on POSIX the
#     PID file + stale-kill on the next launch and the reader-service's
#     parent-liveness check cover it.
# H7: no single-instance guard meant a double-launch ran two backends on the
#     same SQLite DB and two reader-services against the same reader.
# ---------------------------------------------------------------------------

_READER_PID_FILE = Path.home() / ".racetag" / "reader-service.pid"

# Module-level reference keeps the locked file handle (and thus the lock)
# alive for the process lifetime; released automatically on ANY exit.
_instance_lock_fh = None


def _try_lock_file(path: Path):
    """Return an open, exclusively-locked file handle, or None if the lock is
    held by another process. POSIX flock / Windows msvcrt.locking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            # msvcrt.locking locks bytes starting at the CURRENT position, and
            # "a+" opens at EOF. Without seek(0) the first instance locks byte 0
            # of the empty file, writes its PID, and a second launch then locks
            # byte N of the longer file — and "gets" the lock.
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _acquire_single_instance_lock() -> bool:
    """Take the app-wide instance lock. Keeps the handle in a module global so
    the lock lives exactly as long as the process (incl. crash release)."""
    global _instance_lock_fh
    _instance_lock_fh = _try_lock_file(_racetag_home() / "racetag.lock")
    if _instance_lock_fh is None:
        return False
    try:
        _instance_lock_fh.truncate(0)
        _instance_lock_fh.write(str(os.getpid()))
        _instance_lock_fh.flush()
    except OSError:
        pass  # informational content only
    return True


def _wait_for_single_instance_lock(timeout_s: float, poll_s: Optional[float] = None) -> bool:
    """Retry the instance lock until ``timeout_s`` has passed."""
    if poll_s is None:
        poll_s = INSTANCE_LOCK_POLL_S
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        if _acquire_single_instance_lock():
            return True
    return False


def _handle_second_instance() -> bool:
    """The instance lock is held by another process.

    Returns True when this process got the lock after all (the previous
    instance finished shutting down) and should start normally; otherwise
    shows a German dialog and returns False.
    """
    if native.focus_existing_window(WINDOW_TITLE):
        log.warning("another Racetag instance is running; exiting")
        native.message_box(
            "Racetag läuft bereits",
            "Racetag ist schon geöffnet. Bitte das vorhandene Racetag-Fenster "
            "verwenden (es ist eventuell in der Taskleiste minimiert).",
            "info",
        )
        return False
    if sys.platform == "win32":
        # No visible window: the other instance is probably still shutting
        # down (or just starting). Only Windows can tell, see
        # focus_existing_window.
        log.warning(
            "instance lock held but no visible Racetag window; waiting up to %.0f s", INSTANCE_LOCK_WAIT_S
        )
        if _wait_for_single_instance_lock(INSTANCE_LOCK_WAIT_S):
            log.info("previous Racetag instance has ended; starting normally")
            return True
        if native.focus_existing_window(WINDOW_TITLE):
            log.warning("another Racetag instance has started meanwhile; exiting")
            native.message_box(
                "Racetag läuft bereits",
                "Racetag ist schon geöffnet. Bitte das vorhandene Racetag-Fenster "
                "verwenden (es ist eventuell in der Taskleiste minimiert).",
                "info",
            )
            return False
        log.warning("instance lock still held after %.0f s and no window; exiting", INSTANCE_LOCK_WAIT_S)
        native.message_box(
            "Racetag läuft bereits",
            "Racetag wird gerade noch beendet oder gestartet.\n\n"
            "Bitte ein paar Sekunden warten und Racetag dann erneut öffnen. "
            "Hilft das nicht, den Computer neu starten.",
            "info",
        )
        return False
    log.warning("another Racetag instance is running; exiting")
    native.message_box(
        "Racetag läuft bereits",
        "Racetag ist schon geöffnet. Bitte das vorhandene Racetag-Fenster "
        "verwenden (es ist eventuell im Dock oder in der Taskleiste minimiert).",
        "info",
    )
    return False


def _release_single_instance_lock() -> None:
    global _instance_lock_fh
    fh, _instance_lock_fh = _instance_lock_fh, None
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass


def _kill_stale_reader_service() -> None:
    """Kill a reader-service left over from a crashed previous run (H6).

    Verifies via `ps` that the PID actually still is a reader-service before
    killing — PIDs get reused, and we must never kill an innocent process.
    """
    try:
        pid = int(_READER_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return
    if sys.platform == "win32":
        # Never probe with os.kill on Windows: it calls TerminateProcess. The
        # Job Object already killed the old child together with the old shell.
        _READER_PID_FILE.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, 0)  # existence probe, no signal delivered
    except (ProcessLookupError, PermissionError):
        _READER_PID_FILE.unlink(missing_ok=True)
        return
    try:
        cmd = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:  # noqa: BLE001
        return
    if "--reader-service" not in cmd and "racetag_reader_service" not in cmd:
        # PID was reused by something else — just drop the stale file.
        _READER_PID_FILE.unlink(missing_ok=True)
        return
    log.warning("killing stale reader-service from a previous run (pid %s)", pid)
    try:
        os.kill(pid, 15)  # SIGTERM
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)  # SIGKILL
    except (ProcessLookupError, PermissionError):
        pass
    _READER_PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Env bootstrap — must run before importing the backend module so that
# RACETAG_DATA_DIR is set before module-level code in the backend fires.
# ---------------------------------------------------------------------------

def _bootstrap_env() -> None:
    """Point data/log dirs at writable user locations and export the version.

    The backend defaults to "./data" (relative to cwd), which breaks in a
    packaged app where the bundle dir is read-only.  We redirect to
    ~/.racetag/data so the path always resolves to something writable.
    """
    os.environ.setdefault("RACETAG_DATA_DIR", str(_racetag_home() / "data"))
    # Reader-service disk artefacts (crash-recovery spool, logs) are pinned
    # next to the data dir so the location does not depend on the launch CWD
    # (AUDIT-2026-07 H1).
    os.environ.setdefault("RACETAG_LOG_DIR", str(_racetag_home() / "logs"))
    for key in ("RACETAG_DATA_DIR", "RACETAG_LOG_DIR"):
        try:
            Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
        except OSError:
            # Reported with a dialog by _check_data_dir / the log fallback.
            pass

    # Shown in the UI and used by the update notice (contract 2.6). An
    # explicit value from the environment wins, which helps testing.
    version = _read_version()
    if version:
        os.environ.setdefault("RACETAG_VERSION", version)

    # In-process urllib calls and every child inherit this.
    _with_loopback_no_proxy(os.environ)


def _setup_logging_with_fallback(log_dir: Path) -> Path:
    """Set up file logging; fall back to a temp dir if ``log_dir`` is unusable."""
    try:
        desktop_logging.setup_logging(log_dir)
        return log_dir
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "racetag-logs"
        desktop_logging.setup_logging(fallback)
        log.exception("log dir %s unusable; logging to %s", log_dir, fallback)
        # Export it: the reader-service child keeps its crash-recovery spool
        # and reader.log in RACETAG_LOG_DIR, and crash.log is written there.
        os.environ["RACETAG_LOG_DIR"] = str(fallback)
        return fallback


def _check_data_dir(data_dir: Path) -> Optional[str]:
    """Return a German error text if the data dir is unusable, else None."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / f".racetag-write-test-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        log.error("data dir %s is not writable: %s", data_dir, exc)
        return (
            "Racetag kann nicht in seinen Datenordner schreiben:\n"
            f"{data_dir}\n\n"
            "Bitte prüfen, ob der Ordner existiert und nicht schreibgeschützt ist, "
            "und Racetag danach neu starten."
        )
    free = native.free_disk_bytes(data_dir)
    if free < MIN_FREE_DISK_BYTES:
        log.error("only %d bytes free for data dir %s", free, data_dir)
        return (
            f"Auf dem Laufwerk mit dem Racetag-Datenordner sind nur noch "
            f"{free // (1024 * 1024)} MB frei:\n{data_dir}\n\n"
            "Racetag braucht mindestens 200 MB, damit keine Zeiten verloren gehen. "
            "Bitte Speicherplatz freigeben und Racetag danach neu starten."
        )
    return None


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
    Sets *shutdown_event* when the serve() coroutine returns or fails.
    """
    import asyncio  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        # Heartbeats hit the server every 2 s; request lines would drown
        # backend.log.
        access_log=False,
        log_config=desktop_logging.uvicorn_log_config(),
    )
    server = uvicorn.Server(config)
    handle.set_server(server)

    async def _serve():
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task  # startup failed (e.g. bind error): surface it
                return
            await asyncio.sleep(0.05)
        ready_event.set()
        await task

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
    except (Exception, SystemExit):  # uvicorn calls sys.exit(1) on startup errors
        log.exception("uvicorn server thread failed")
    finally:
        loop.close()
        shutdown_event.set()


# ---------------------------------------------------------------------------
# Backend watchdog (plan B5)
# ---------------------------------------------------------------------------

def _self_command() -> List[str]:
    """Command line that starts this app again with the same arguments."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + sys.argv[1:]
    return [sys.executable] + sys.argv


def _relaunch_self() -> None:
    cmd = _self_command()
    log.info("relaunching: %s", " ".join(cmd))
    try:
        subprocess.Popen(cmd, close_fds=True)  # noqa: S603
    except OSError:
        log.exception("relaunch failed")


def _handle_backend_death(
    supervisor: Optional[ReaderSupervisor],
    ask: Callable[[str, str], bool] = native.ask_yes_no,
    relaunch: Callable[[], None] = _relaunch_self,
    exit_now: Callable[[int], None] = os._exit,
) -> None:
    """Ask the operator whether to restart after the server thread died."""
    log.error("backend server thread died unexpectedly")
    restart = ask(
        "Racetag – Interner Fehler",
        "Der interne Server von Racetag wurde unerwartet beendet. "
        "Bereits gespeicherte Rennen und Zeiten bleiben erhalten.\n\n"
        "Racetag jetzt neu starten?",
    )
    if supervisor is not None:
        supervisor.stop()
    # The new instance must be able to take the lock before this one is gone.
    _release_single_instance_lock()
    if restart:
        relaunch()
    logging.shutdown()
    # os._exit: the pywebview main loop owns the main thread and would keep a
    # regular sys.exit from a worker thread from ending the process.
    exit_now(0 if restart else 1)


def _backend_watchdog(
    server_thread: threading.Thread,
    shutting_down: threading.Event,
    interval_s: float,
    on_dead: Callable[[], None],
) -> None:
    """Call ``on_dead`` once if the server thread ends while we are not shutting down."""
    while not shutting_down.wait(interval_s):
        if not server_thread.is_alive():
            if not shutting_down.is_set():
                on_dead()
            return


# ---------------------------------------------------------------------------
# JS bridge (window.pywebview.api)
# ---------------------------------------------------------------------------

# Talks only to the local backend: never consult proxy settings.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _RacetagApi:
    """Methods callable from the frontend via ``window.pywebview.api``.

    pywebview exposes every public attribute, so all state lives in
    underscore attributes. ``webview`` is imported lazily: tests inject a
    fake module and never need a GUI.
    """

    def __init__(
        self,
        backend_url: str,
        data_dir: Path,
        log_dir: Path,
        racetag_home: Path,
        supervisor: Optional[ReaderSupervisor] = None,
    ) -> None:
        self._backend_url = backend_url
        self._data_dir = Path(data_dir)
        self._log_dir = Path(log_dir)
        self._racetag_home = Path(racetag_home)
        self._supervisor = supervisor

    # Native save dialog for CSV exports: blob + <a download> in WKWebView
    # opens the CSV inside the app window instead of downloading it.
    # Returns True when written, False when cancelled; a failed write shows a
    # German dialog and raises (the JS promise rejects).
    def save_csv(self, csv_text: str, default_filename: str = "racetag-export.csv") -> bool:
        import webview  # noqa: PLC0415

        if not webview.windows:
            return False
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=default_filename or "racetag-export.csv",
            file_types=("CSV Dateien (*.csv)", "Alle Dateien (*.*)"),
        )
        if not result:
            return False  # user cancelled
        path = result if isinstance(result, str) else result[0]
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
        except OSError:
            log.exception("save_csv failed for %s", path)
            native.message_box(
                "Racetag – Export",
                "Die Datei konnte nicht gespeichert werden:\n"
                f"{path}\n\n"
                "Ist sie noch in Excel oder einem anderen Programm geöffnet? Dann "
                "das Programm schließen und erneut exportieren. Sonst bitte einen "
                "anderen Speicherort wählen.",
                "error",
            )
            # Re-raised on purpose: pywebview rejects the JS promise, so the
            # UI reports a failed export instead of "Export abgebrochen"
            # (False means the operator cancelled the dialog).
            raise
        return True

    def open_data_folder(self) -> bool:
        try:
            self._racetag_home.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.exception("could not create %s", self._racetag_home)
            return False
        return native.open_path(self._racetag_home)

    def app_info(self) -> dict:
        return {
            "version": os.environ.get("RACETAG_VERSION") or _read_version(),
            "data_dir": str(self._data_dir),
            "log_dir": str(self._log_dir),
            "platform": platform.platform(),
        }

    def create_support_bundle(self) -> dict:
        import webview  # noqa: PLC0415

        if not webview.windows:
            return {"ok": False, "path": None, "error": "no window"}
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE,
            # The real Desktop, also when Windows redirects it to OneDrive.
            directory=str(native.desktop_dir()),
            save_filename=support_bundle.default_bundle_name(_dt.datetime.now()),
            file_types=("ZIP Dateien (*.zip)",),
        )
        if not result:
            # Cancelled by the operator: not an error.
            return {"ok": False, "path": None, "error": None}
        dest = Path(result if isinstance(result, str) else result[0])
        if dest.suffix.lower() != ".zip":
            dest = dest.with_name(dest.name + ".zip")
        try:
            info = dict(self.app_info())
            info["python"] = platform.python_version()
            info["webview2"] = native.webview2_version()
            info["supervisor"] = self._supervisor.status() if self._supervisor is not None else None
            out = support_bundle.build_support_bundle(
                dest,
                self._data_dir,
                self._log_dir,
                {
                    "config": self._fetch_json("/config"),
                    "reader_status": self._fetch_json("/reader/status"),
                    "app_info": info,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report to the UI instead of raising into JS
            log.exception("support bundle failed")
            return {"ok": False, "path": None, "error": str(exc)}
        return {"ok": True, "path": str(out), "error": None}

    def _fetch_json(self, path: str) -> object:
        """GET a backend endpoint for the bundle; errors become part of the JSON."""
        request = urllib.request.Request(self._backend_url + path)
        api_key = os.environ.get("RACETAG_API_KEY")
        if api_key:
            request.add_header("X-API-Key", api_key)
        try:
            with _LOCAL_OPENER.open(request, timeout=3) as resp:  # noqa: S310 - local backend
                return json.load(resp)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"error": f"GET {path} failed: {exc}"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _dispatch_reader_service() -> None:
    """Frozen-mode dispatch (W-073): run the reader-service in this process."""
    # The child is invisible and restarted by the supervisor: a crash dialog
    # from here would come back after every restart. Crashes still go to
    # crash.log and stderr (reader-stderr.log); the UI shows the reader state.
    os.environ["RACETAG_NO_DIALOGS"] = "1"
    if getattr(sys, "frozen", False):
        reader_src = str(Path(sys._MEIPASS) / "reader_src")  # noqa: SLF001
    else:
        reader_src = str(REPO_ROOT / "apps" / "reader-service" / "src")
    if reader_src not in sys.path:
        sys.path.insert(0, reader_src)
    # Remove our dispatch flag before forwarding to the reader's argparser.
    reader_argv = [a for a in sys.argv[1:] if a != "--reader-service"]
    import racetag_reader_service  # noqa: PLC0415

    sys.exit(racetag_reader_service.main(reader_argv))


def main() -> None:
    # One binary, several roles: the reader-service child and the self-test
    # never touch the instance lock or the GUI.
    if "--reader-service" in sys.argv:
        _dispatch_reader_service()
    if "--selftest" in sys.argv[1:]:
        import selftest  # noqa: PLC0415

        sys.exit(selftest.run_selftest(sys.modules[__name__]))

    _bootstrap_env()
    data_dir = Path(os.environ["RACETAG_DATA_DIR"])
    log_dir = _setup_logging_with_fallback(Path(os.environ["RACETAG_LOG_DIR"]))
    native.install_excepthook(log_dir)
    log.info(
        "Racetag %s starting (Python %s, %s, data dir %s, log dir %s)",
        os.environ.get("RACETAG_VERSION", "unknown"),
        platform.python_version(),
        platform.platform(),
        data_dir,
        log_dir,
    )

    # H7: refuse to run a second instance — two backends on the same SQLite
    # plus two reader-services on the same reader corrupt each other.
    if not _acquire_single_instance_lock() and not _handle_second_instance():
        sys.exit(1)

    problem = _check_data_dir(data_dir)
    if problem:
        native.message_box("Racetag – Datenordner", problem, "error")
        sys.exit(1)

    if sys.platform == "win32" and native.webview2_version() is None:
        log.error("WebView2 runtime missing")
        if native.ask_yes_no(
            "Racetag – WebView2 fehlt",
            "Racetag braucht die „Microsoft Edge WebView2 Runtime“, um sein Fenster "
            "anzuzeigen. Sie wurde auf diesem Computer nicht gefunden.\n\n"
            "Soll die Download-Seite von Microsoft jetzt geöffnet werden? "
            "Nach der Installation Racetag bitte neu starten.",
        ):
            native.open_url(native.WEBVIEW2_DOWNLOAD_URL)
        sys.exit(1)

    # H6: reap a reader-service orphaned by a hard crash of a previous run.
    _kill_stale_reader_service()

    backend_app = _build_combined_app()
    backend_module = sys.modules["racetag_backend_app"]
    port = _pick_free_port()
    url = f"http://127.0.0.1:{port}"

    supervisor: Optional[ReaderSupervisor] = None
    if _bundled_reader_enabled():
        supervisor = ReaderSupervisor(
            argv_builder=_make_reader_argv_builder(backend_module, url),
            pid_file=_READER_PID_FILE,
            stderr_log=log_dir / "reader-stderr.log",
        )
        # Contract 2.7: registered before the server starts serving.
        backend_app.state.reader_controller = supervisor
    else:
        log.info("RACETAG_BUNDLED_READER=0; the shell does not start a reader-service")

    handle = _ServerHandle()
    ready = threading.Event()
    done = threading.Event()
    server_thread = threading.Thread(
        target=_run_server,
        args=(backend_app, port, handle, ready, done),
        name="uvicorn",
        daemon=True,
    )
    server_thread.start()

    if not ready.wait(timeout=SERVER_START_TIMEOUT_S):
        log.error("server did not start within %.0f s", SERVER_START_TIMEOUT_S)
        handle.stop()
        native.message_box(
            "Racetag – Startfehler",
            "Racetag konnte nicht gestartet werden: Der interne Server ist nicht "
            "rechtzeitig bereit geworden.\n\n"
            "Bitte Racetag neu starten. Tritt der Fehler wieder auf, bitte die "
            f"Protokolle aus diesem Ordner an den Support schicken:\n{log_dir}",
            "error",
        )
        sys.exit(1)
    log.info("backend listening on %s", url)

    shutting_down = threading.Event()
    keep_awake_set = False
    # H6: try/finally so ANY exception between the reader start and a clean
    # window close (webview import/window failures included) still stops the
    # reader-service and uvicorn.
    try:
        if supervisor is not None:
            supervisor.start()
        threading.Thread(
            target=_backend_watchdog,
            args=(server_thread, shutting_down, WATCHDOG_INTERVAL_S, lambda: _handle_backend_death(supervisor)),
            name="backend-watchdog",
            daemon=True,
        ).start()

        import webview  # noqa: PLC0415  (import late so tests never need a GUI)

        api = _RacetagApi(url, data_dir, log_dir, _racetag_home(), supervisor)
        # confirm_close: closing the window stops timing, so a stray X or
        # Alt+F4 during a race must be confirmed (German text below).
        webview.create_window(WINDOW_TITLE, url, js_api=api, width=1280, height=800, confirm_close=True)
        # Idle sleep or a display timeout during a race freezes the reader
        # and loses every pass until someone touches the PC. Set and reset
        # on the main thread: the Windows request belongs to the calling
        # thread, and this one lives as long as the window.
        native.keep_system_awake(True)
        keep_awake_set = True
        # Blocks on the main thread until the window is closed. On Windows the
        # WinForms/Edge Chromium backend is requested explicitly so an
        # installed Qt or CEF never gets picked; pywebview itself would still
        # fall back to MSHTML without WebView2, hence the runtime check above.
        webview.start(
            gui="edgechromium" if sys.platform == "win32" else None,
            localization=WEBVIEW_LOCALIZATION,
        )
    finally:
        shutting_down.set()
        if supervisor is not None:
            supervisor.stop()
        handle.stop()
        done.wait(timeout=3)
        if keep_awake_set:
            # Last: the reader-service flushes its spool while it stops.
            native.keep_system_awake(False)
        log.info("Racetag stopped")


def _crash_log_dir() -> Path:
    return Path(os.environ.get("RACETAG_LOG_DIR") or (_racetag_home() / "logs"))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - last resort: never fail silently in a windowed exe
        native.report_crash(*sys.exc_info(), log_dir=_crash_log_dir(), where="main")
        sys.exit(1)
