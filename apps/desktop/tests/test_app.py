"""Unit tests for apps/desktop/app.py (W-070..W-073, plan A2-A5, B5, D6, E2).

These tests exercise the helper functions in isolation without launching
pywebview, so they run headlessly in CI. ``webview`` is replaced by a fake
module wherever the JS bridge is tested.
"""
import ast
import os
import re
import socket
import sys
import threading
import time
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DESKTOP_DIR = Path(__file__).resolve().parent.parent


def _import_desktop_app():
    """Import apps/desktop/app.py — robust against sys.path pollution.

    _build_combined_app() inserts the BACKEND dir (which also contains an
    app.py) at sys.path[0]; force the desktop dir to the front and verify.
    """
    sys.modules.pop("app", None)
    if str(_DESKTOP_DIR) in sys.path:
        sys.path.remove(str(_DESKTOP_DIR))
    sys.path.insert(0, str(_DESKTOP_DIR))
    import app as desktop_app  # noqa: PLC0415

    assert Path(desktop_app.__file__).parent == _DESKTOP_DIR, desktop_app.__file__
    return desktop_app


@pytest.fixture()
def clean_env(monkeypatch):
    """Unset env vars that _bootstrap_env() sets with os.environ.setdefault.

    Setting them first makes monkeypatch record them, so whatever the code
    under test writes is rolled back after the test.
    """
    for key in ("RACETAG_LOG_DIR", "RACETAG_VERSION", "READER_IP", "PYTHONPATH", "INTERACTIVE", "NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    return monkeypatch


# Same pattern pywebview 6 validates file_types against (webview/util.py
# parse_file_type); an invalid filter raises ValueError at dialog time.
_PYWEBVIEW_FILE_FILTER = re.compile(r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$")


class _FakeWindow:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create_file_dialog(self, dialog_type, **kwargs):
        for file_type in kwargs.get("file_types", ()):
            assert _PYWEBVIEW_FILE_FILTER.search(file_type), file_type
        self.calls.append((dialog_type, kwargs))
        return self.result


def _install_fake_webview(monkeypatch, result, with_window=True):
    fake = types.ModuleType("webview")
    fake.FileDialog = SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30)
    window = _FakeWindow(result)
    fake.windows = [window] if with_window else []
    monkeypatch.setitem(sys.modules, "webview", fake)
    return window


# ---------------------------------------------------------------------------
# Free port, env bootstrap, version
# ---------------------------------------------------------------------------

def test_pick_free_port_returns_valid_port():
    """_pick_free_port() must return an ephemeral port in the valid range."""
    desktop_app = _import_desktop_app()
    port = desktop_app._pick_free_port()
    assert isinstance(port, int), "port must be an int"
    assert 1024 < port < 65536, f"port {port} out of ephemeral range"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


def test_bootstrap_env_creates_data_dir(tmp_path, clean_env):
    """_bootstrap_env() must create RACETAG_DATA_DIR when it does not exist."""
    target = tmp_path / "racetag_test_data"
    assert not target.exists(), "precondition: dir must not exist yet"
    clean_env.setenv("RACETAG_DATA_DIR", str(target))
    clean_env.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))

    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()

    assert target.is_dir(), "_bootstrap_env() must create RACETAG_DATA_DIR"
    assert (tmp_path / "logs").is_dir()


def test_bootstrap_env_uses_default_when_unset(tmp_path, clean_env):
    """_bootstrap_env() sets a default path when RACETAG_DATA_DIR is absent."""
    clean_env.delenv("RACETAG_DATA_DIR", raising=False)
    # Never touch the real ~/.racetag: Path.home() reads HOME on POSIX and
    # USERPROFILE on Windows.
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    clean_env.setenv("HOME", str(fake_home))
    clean_env.setenv("USERPROFILE", str(fake_home))

    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()

    assert (fake_home / ".racetag" / "data").is_dir(), "default data dir must be created under home"
    assert (fake_home / ".racetag" / "logs").is_dir()


def test_bootstrap_env_sets_version_from_version_file(tmp_path, clean_env):
    clean_env.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()
    expected = (_DESKTOP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    assert os.environ["RACETAG_VERSION"] == expected


def test_bootstrap_env_keeps_explicit_version(tmp_path, clean_env):
    clean_env.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    clean_env.setenv("RACETAG_VERSION", "9.9.9")
    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()
    assert os.environ["RACETAG_VERSION"] == "9.9.9"


def test_read_version_frozen_uses_meipass(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert desktop_app._read_version() == "1.2.3"


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

def test_check_data_dir_ok(tmp_path):
    desktop_app = _import_desktop_app()
    assert desktop_app._check_data_dir(tmp_path / "data") is None
    assert list((tmp_path / "data").iterdir()) == [], "write probe must be removed"


def test_check_data_dir_not_writable(tmp_path):
    desktop_app = _import_desktop_app()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    message = desktop_app._check_data_dir(blocker / "data")
    assert message is not None and "schreiben" in message


def test_check_data_dir_low_disk(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    monkeypatch.setattr(desktop_app.native, "free_disk_bytes", lambda path: 50 * 1024 * 1024)
    message = desktop_app._check_data_dir(tmp_path)
    assert message is not None and "50 MB" in message and "200 MB" in message


# ---------------------------------------------------------------------------
# Reader-service command line
# ---------------------------------------------------------------------------

def test_reader_service_entry_non_frozen(monkeypatch):
    """_reader_service_entry() in source mode must point at racetag_reader_service.py."""
    desktop_app = _import_desktop_app()
    monkeypatch.delattr(sys, "frozen", raising=False)

    entry = desktop_app._reader_service_entry()

    assert isinstance(entry, list) and len(entry) >= 2
    last = entry[-1]
    assert last.endswith("racetag_reader_service.py"), last
    assert Path(last).exists(), f"Reader script does not exist: {last}"


def test_reader_command_without_ip(clean_env):
    clean_env.setenv("READER_IP", "10.0.0.99")  # inherited env must not sneak in
    clean_env.setenv("INTERACTIVE", "1")
    clean_env.setenv("PYTHONPATH", "/existing")
    desktop_app = _import_desktop_app()

    argv, env = desktop_app._reader_command("http://127.0.0.1:1234", None, None)

    assert "--ip" not in argv
    assert "--stop-on-stdin-eof" in argv
    assert argv[argv.index("--backend-url") + 1] == "http://127.0.0.1:1234"
    assert argv[argv.index("--antenna-power") + 1] == "300"
    assert "READER_IP" not in env
    assert "INTERACTIVE" not in env
    reader_src = str(desktop_app.REPO_ROOT / "apps" / "reader-service" / "src")
    assert env["PYTHONPATH"] == f"{reader_src}{os.pathsep}/existing"
    init_file = argv[argv.index("--init_commands_file") + 1]
    assert Path(init_file).is_absolute()


def test_reader_command_with_ip_power_and_extra_args(clean_env):
    desktop_app = _import_desktop_app()

    argv, env = desktop_app._reader_command(
        "http://127.0.0.1:1234", "192.168.178.22", 250, ["--no-discover", "--heartbeat-interval", "0.5"]
    )

    assert argv[argv.index("--ip") + 1] == "192.168.178.22"
    assert argv[argv.index("--antenna-power") + 1] == "250"
    assert argv[-3:] == ["--no-discover", "--heartbeat-interval", "0.5"]
    assert env["READER_IP"] == "192.168.178.22"
    assert env["ANTENNA_POWER"] == "250"
    reader_src = str(desktop_app.REPO_ROOT / "apps" / "reader-service" / "src")
    assert env["PYTHONPATH"] == reader_src


def _no_proxy_entries(env):
    return {part.strip() for part in env.get("NO_PROXY", "").split(",") if part.strip()}


def test_reader_command_exempts_loopback_from_proxy(clean_env):
    """A proxy from the Windows Internet Options must never catch the POSTs to
    the local backend (laps, heartbeats)."""
    clean_env.setenv("NO_PROXY", "intranet.example")
    desktop_app = _import_desktop_app()

    _, env = desktop_app._reader_command("http://127.0.0.1:1234", "192.168.178.22", None)

    assert {"intranet.example", "127.0.0.1", "localhost"} <= _no_proxy_entries(env)
    if sys.platform != "win32":
        assert env["no_proxy"] == env["NO_PROXY"]


def test_bootstrap_env_exempts_loopback_from_proxy(tmp_path, clean_env):
    clean_env.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()
    desktop_app._bootstrap_env()  # idempotent, no duplicates

    value = os.environ["NO_PROXY"]
    assert value.split(",") == ["127.0.0.1", "localhost"]


class _JsonHandler:
    """Factory for a tiny local HTTP server answering every GET with JSON."""

    @staticmethod
    def serve():
        import http.server  # noqa: PLC0415

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


@pytest.fixture()
def dead_proxy_env(clean_env):
    """A system-wide proxy that cannot reach anything (stands in for a
    corporate proxy from the Windows Internet Options)."""
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        clean_env.delenv(key, raising=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    clean_env.setenv("HTTP_PROXY", f"http://127.0.0.1:{closed_port}")
    return clean_env


def test_reader_service_env_reaches_local_backend_despite_proxy(dead_proxy_env):
    requests = pytest.importorskip("requests")
    desktop_app = _import_desktop_app()
    server = _JsonHandler.serve()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert requests.utils.get_environ_proxies(url + "/reader/status"), "precondition: proxy applies"

        _, env = desktop_app._reader_command(url, None, None)
        for key in ("NO_PROXY", "no_proxy"):
            if key in env:
                dead_proxy_env.setenv(key, env[key])

        assert requests.utils.get_environ_proxies(url + "/reader/status") == {}
        with requests.Session() as session:  # same default trust_env as the reader-service
            assert session.post(url + "/events/tag/batch", json=[], timeout=5).status_code in (200, 501)
            assert session.get(url + "/reader/status", timeout=5).json() == {"ok": True}
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_json_ignores_proxy_settings(dead_proxy_env, tmp_path):
    desktop_app = _import_desktop_app()
    server = _JsonHandler.serve()
    try:
        api = _api(desktop_app, tmp_path, backend_url=f"http://127.0.0.1:{server.server_address[1]}")
        assert api._fetch_json("/config") == {"ok": True}
    finally:
        server.shutdown()
        server.server_close()


def test_reader_argv_builder_reads_fresh_config_each_call(clean_env):
    desktop_app = _import_desktop_app()
    cfg = SimpleNamespace(reader_ip=None, antenna_power=None)
    backend = SimpleNamespace(_effective_config=lambda: cfg)
    build = desktop_app._make_reader_argv_builder(backend, "http://127.0.0.1:1")

    argv, _ = build()
    assert "--ip" not in argv

    cfg.reader_ip, cfg.antenna_power = "192.168.178.22", 200
    argv, _ = build()
    assert argv[argv.index("--ip") + 1] == "192.168.178.22"
    assert argv[argv.index("--antenna-power") + 1] == "200"


def test_reader_argv_builder_with_real_backend(tmp_path, clean_env):
    """Reads the in-process backend config: no persisted IP -> no --ip."""
    clean_env.setenv("RACETAG_DATA_DIR", str(tmp_path / "data"))
    desktop_app = _import_desktop_app()
    desktop_app._build_combined_app()
    backend_module = sys.modules["racetag_backend_app"]

    argv, _ = desktop_app._make_reader_argv_builder(backend_module, "http://127.0.0.1:1")()
    assert "--ip" not in argv

    backend_module.config_store.set_reader_ip("192.168.178.23")
    argv, _ = desktop_app._make_reader_argv_builder(backend_module, "http://127.0.0.1:1")()
    assert argv[argv.index("--ip") + 1] == "192.168.178.23"


# ---------------------------------------------------------------------------
# Spec / version-info files
# ---------------------------------------------------------------------------

def test_pyinstaller_mac_spec_parses():
    """pyinstaller.mac.spec must be syntactically valid Python."""
    spec_path = _DESKTOP_DIR / "pyinstaller.mac.spec"
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    ast.parse(spec_path.read_text(encoding="utf-8"))


def test_pyinstaller_win_spec_parses():
    """pyinstaller.win.spec must be syntactically valid Python."""
    spec_path = _DESKTOP_DIR / "pyinstaller.win.spec"
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    ast.parse(spec_path.read_text(encoding="utf-8"))


# First-party modules both specs must list (contract 6, plus the backend's
# reader status module), so a missed import fails the build, not race day.
_REQUIRED_HIDDENIMPORTS = {
    "desktop_logging", "native", "reader_supervisor", "support_bundle", "selftest",
    "discovery", "status_reporter", "reader_status_hub",
}
_FIRST_PARTY_DIRS = (
    _DESKTOP_DIR,
    _DESKTOP_DIR.parent / "reader-service" / "src",
    _DESKTOP_DIR.parent / "backend" / "racetag-backend",
)


def _spec_tree(name: str) -> ast.Module:
    return ast.parse((_DESKTOP_DIR / name).read_text(encoding="utf-8"))


def _spec_hiddenimports(tree: ast.Module) -> list:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "hiddenimports" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("hiddenimports list not found")


@pytest.mark.parametrize("spec_name", ["pyinstaller.mac.spec", "pyinstaller.win.spec"])
def test_spec_lists_first_party_modules_that_exist(spec_name):
    names = _spec_hiddenimports(_spec_tree(spec_name))
    missing = _REQUIRED_HIDDENIMPORTS - set(names)
    assert not missing, f"{spec_name} lacks hiddenimports {sorted(missing)}"
    for name in _REQUIRED_HIDDENIMPORTS:
        assert any((d / f"{name}.py").is_file() for d in _FIRST_PARTY_DIRS), (
            f"{spec_name} lists {name!r} but no such module exists"
        )
    source = (_DESKTOP_DIR / spec_name).read_text(encoding="utf-8")
    assert re.search(r'\(str\(SPEC_DIR / "VERSION"\), "\."\)', source), "VERSION must be bundled to '.'"


def test_win_spec_disables_upx_in_exe_and_collect():
    calls = {}
    for node in ast.walk(_spec_tree("pyinstaller.win.spec")):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("EXE", "COLLECT"):
            for kw in node.keywords:
                if kw.arg == "upx":
                    calls[node.func.id] = ast.literal_eval(kw.value)
    assert calls == {"EXE": False, "COLLECT": False}


# Release gates (packaging): regressions here would let a broken or mislabelled
# build reach the race marshal.
_WORKFLOWS_DIR = _DESKTOP_DIR.parent.parent / ".github" / "workflows"


def _workflow_steps(workflow: str, job: str) -> dict:
    import yaml  # shipped with uvicorn[standard]

    data = yaml.safe_load((_WORKFLOWS_DIR / workflow).read_text(encoding="utf-8"))
    return {step.get("name"): step for step in data["jobs"][job]["steps"] if step.get("name")}


def test_release_fails_on_tag_version_mismatch_in_build_and_release_jobs():
    build = _workflow_steps("release.yml", "build")
    names = list(build)
    assert names.index("Read version") < names.index("Set up Python 3.12"), "version check must run before the build"
    run = build["Read version"]["run"]
    assert '"${GITHUB_REF_NAME%%-*}"' in run and '"v${v}"' in run
    assert "exit 1" in run and "::warning::" not in run
    release = _workflow_steps("release.yml", "release")
    names = list(release)
    assert "Check tag matches VERSION" in release
    assert names.index("Check tag matches VERSION") < names.index("Download all artefacts")
    assert "exit 1" in release["Check tag matches VERSION"]["run"]


def test_release_build_gates_hidden_imports_and_gui_stack():
    build = _workflow_steps("release.yml", "build")
    assert build["Install backend + reader-service + desktop deps"].get("shell") == "bash"
    pyi = build["Build with PyInstaller"]
    assert pyi.get("shell") == "bash"
    assert "ERROR: Hidden import" in pyi["run"] and "exit 1" in pyi["run"]
    assert "Check GUI stack imports (Windows)" in build
    assert "Check GUI stack DLLs in bundle (Windows)" in build
    ci = _workflow_steps("ci.yml", "desktop-tests-windows")
    assert any("webview.platforms.winforms" in (step.get("run") or "") for step in ci.values())


@pytest.mark.parametrize("spec_name", ["pyinstaller.mac.spec", "pyinstaller.win.spec"])
def test_spec_hiddenimports_contain_no_non_modules(spec_name):
    names = set(_spec_hiddenimports(_spec_tree(spec_name)))
    assert not names & {"racetag_backend_app", "multiprocessing.freeze_support"}


def test_win_spec_lists_gui_stack_hiddenimports():
    names = set(_spec_hiddenimports(_spec_tree("pyinstaller.win.spec")))
    missing = {"clr", "pythonnet", "clr_loader", "webview.platforms.winforms",
               "webview.platforms.edgechromium"} - names
    assert not missing, f"pyinstaller.win.spec lacks {sorted(missing)}"


def test_win_version_info_generated(tmp_path):
    """generate_win_version_info.py must produce a file containing 'Racetag'
    and the correct version tuple."""
    import importlib.util as ilu  # noqa: PLC0415

    gen_path = _DESKTOP_DIR / "generate_win_version_info.py"
    assert gen_path.exists(), f"Generator script not found: {gen_path}"

    fake_script_dir = tmp_path / "desktop"
    fake_script_dir.mkdir()
    (fake_script_dir / "VERSION").write_text("0.1.0\n")

    spec = ilu.spec_from_file_location("_gen_win_ver", str(gen_path))
    mod = ilu.module_from_spec(spec)
    # Override __file__ so Path(__file__).resolve().parent is our fake dir.
    mod.__file__ = str(fake_script_dir / "generate_win_version_info.py")
    spec.loader.exec_module(mod)
    mod.main()

    out = (fake_script_dir / "win_version_info.txt").read_text(encoding="utf-8")
    assert "Racetag" in out
    assert "(0, 1, 0, 0)" in out
    assert "0.1.0" in out


# ---------------------------------------------------------------------------
# Combined app and server thread
# ---------------------------------------------------------------------------

def test_build_combined_app_mounts_frontend(tmp_path, monkeypatch):
    """_build_combined_app() must produce a FastAPI app with a / static mount
    that serves index.html."""
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path / "data"))
    desktop_app = _import_desktop_app()
    combined = desktop_app._build_combined_app()

    from starlette.routing import Mount  # noqa: PLC0415

    mount_paths = [r.path for r in combined.routes if isinstance(r, Mount)]
    # Starlette normalises a mount path of "/" to "".
    assert any(p in ("", "/") for p in mount_paths), mount_paths

    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(combined, raise_server_exceptions=False) as client:
        resp = client.get("/")
    assert resp.status_code == 200, f"GET / returned {resp.status_code}"
    assert "text/html" in resp.headers.get("content-type", "")


def _tiny_asgi_app():
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.responses import PlainTextResponse  # noqa: PLC0415
    from starlette.routing import Route  # noqa: PLC0415

    return Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])


def test_run_server_works_without_stdout(monkeypatch):
    """uvicorn's default log config calls sys.stdout.isatty(); log_config=None avoids it."""
    desktop_app = _import_desktop_app()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    handle = desktop_app._ServerHandle()
    ready, done = threading.Event(), threading.Event()
    port = desktop_app._pick_free_port()
    thread = threading.Thread(
        target=desktop_app._run_server, args=(_tiny_asgi_app(), port, handle, ready, done), daemon=True
    )
    thread.start()
    try:
        assert ready.wait(10), "server did not become ready"
        import urllib.request  # noqa: PLC0415

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.read() == b"ok"
    finally:
        handle.stop()
        assert done.wait(5)
        thread.join(5)


def test_run_server_bind_failure_does_not_hang():
    desktop_app = _import_desktop_app()
    handle = desktop_app._ServerHandle()
    ready, done = threading.Event(), threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        if sys.platform == "win32":
            # uvicorn sets SO_REUSEADDR, which on Windows would share the port.
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        thread = threading.Thread(
            target=desktop_app._run_server, args=(_tiny_asgi_app(), port, handle, ready, done), daemon=True
        )
        thread.start()
        try:
            thread.join(10)
        finally:
            if thread.is_alive():
                handle.stop()
                thread.join(5)
    assert done.is_set()
    assert not ready.is_set(), "server must not report ready after a bind error"


# ---------------------------------------------------------------------------
# Backend watchdog (B5)
# ---------------------------------------------------------------------------

def test_watchdog_reports_dead_server_thread_once():
    desktop_app = _import_desktop_app()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    on_dead = MagicMock()
    desktop_app._backend_watchdog(dead, threading.Event(), 0.01, on_dead)
    on_dead.assert_called_once_with()


def test_watchdog_ignores_death_during_shutdown():
    desktop_app = _import_desktop_app()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    shutting_down = threading.Event()
    shutting_down.set()
    on_dead = MagicMock()
    desktop_app._backend_watchdog(dead, shutting_down, 0.01, on_dead)
    on_dead.assert_not_called()


def test_watchdog_keeps_watching_live_thread():
    desktop_app = _import_desktop_app()
    release = threading.Event()
    alive = threading.Thread(target=release.wait, daemon=True)
    alive.start()
    shutting_down = threading.Event()
    on_dead = MagicMock()
    watchdog = threading.Thread(
        target=desktop_app._backend_watchdog, args=(alive, shutting_down, 0.01, on_dead), daemon=True
    )
    watchdog.start()
    try:
        time.sleep(0.1)
        assert watchdog.is_alive()
    finally:
        shutting_down.set()
        release.set()
        watchdog.join(2)
        alive.join(2)
    on_dead.assert_not_called()


@pytest.mark.parametrize("answer, exit_code", [(True, 0), (False, 1)])
def test_handle_backend_death(answer, exit_code):
    desktop_app = _import_desktop_app()
    supervisor = MagicMock()
    ask = MagicMock(return_value=answer)
    relaunch = MagicMock()
    exit_now = MagicMock()

    desktop_app._handle_backend_death(supervisor, ask=ask, relaunch=relaunch, exit_now=exit_now)

    title, text = ask.call_args.args
    assert "neu starten" in text
    supervisor.stop.assert_called_once_with()
    assert relaunch.called is answer
    exit_now.assert_called_once_with(exit_code)


def test_self_command(monkeypatch):
    desktop_app = _import_desktop_app()
    monkeypatch.setattr(sys, "argv", ["app.py", "--debug"])
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert desktop_app._self_command() == [sys.executable, "app.py", "--debug"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert desktop_app._self_command() == [sys.executable, "--debug"]


# ---------------------------------------------------------------------------
# main() early exits (A2, A5, E2)
# ---------------------------------------------------------------------------

@pytest.fixture()
def main_env(tmp_path, clean_env):
    desktop_app = _import_desktop_app()
    clean_env.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    clean_env.setattr(sys, "argv", ["app.py"])
    clean_env.setattr(desktop_app.native, "install_excepthook", MagicMock())
    clean_env.setattr(desktop_app, "_setup_logging_with_fallback", lambda log_dir: log_dir)
    clean_env.setattr(desktop_app, "_build_combined_app", MagicMock(side_effect=AssertionError("must not build")))
    return desktop_app


def test_main_second_instance_focuses_window_then_shows_dialog(main_env, monkeypatch):
    desktop_app = main_env
    calls = MagicMock()
    calls.focus.return_value = True  # a visible Racetag window was found and focused
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(desktop_app.native, "focus_existing_window", calls.focus)
    monkeypatch.setattr(desktop_app.native, "message_box", calls.message_box)

    with pytest.raises(SystemExit) as excinfo:
        desktop_app.main()

    assert excinfo.value.code == 1
    assert [c[0] for c in calls.mock_calls] == ["focus", "message_box"]
    assert calls.message_box.call_args.args[0] == "Racetag läuft bereits"


def _lock_sequence(*results):
    remaining = list(results)

    def acquire():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return acquire


def test_main_second_instance_waits_for_closing_instance_then_starts(main_env, monkeypatch):
    """No visible window: the previous instance is still shutting down. Once it
    releases the lock this launch must start normally instead of claiming
    that a (non-existent) window is open."""
    desktop_app = main_env
    box = MagicMock()
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", _lock_sequence(False, False, True))
    monkeypatch.setattr(desktop_app.native, "focus_existing_window", MagicMock(return_value=False))
    monkeypatch.setattr(desktop_app.native, "message_box", box)
    monkeypatch.setattr(desktop_app, "_check_data_dir", lambda path: "Datenordner kaputt")
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_WAIT_S", 5.0)
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_POLL_S", 0.01)
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(SystemExit):
        desktop_app.main()

    # Startup went on past the lock to the next check.
    box.assert_called_once_with("Racetag – Datenordner", "Datenordner kaputt", "error")


def test_second_instance_without_window_and_lock_held_explains_wait(main_env, monkeypatch):
    desktop_app = main_env
    box = MagicMock()
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(desktop_app.native, "focus_existing_window", MagicMock(return_value=False))
    monkeypatch.setattr(desktop_app.native, "message_box", box)
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_WAIT_S", 0.05)
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_POLL_S", 0.01)
    monkeypatch.setattr(sys, "platform", "win32")

    assert desktop_app._handle_second_instance() is False

    title, text, kind = box.call_args.args
    assert title == "Racetag läuft bereits"
    assert "gerade noch beendet oder gestartet" in text
    assert "Fenster verwenden" not in text


def test_second_instance_whose_window_appears_while_waiting(main_env, monkeypatch):
    desktop_app = main_env
    box = MagicMock()
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(desktop_app.native, "focus_existing_window", MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(desktop_app.native, "message_box", box)
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_WAIT_S", 0.05)
    monkeypatch.setattr(desktop_app, "INSTANCE_LOCK_POLL_S", 0.01)
    monkeypatch.setattr(sys, "platform", "win32")

    assert desktop_app._handle_second_instance() is False
    assert "vorhandene Racetag-Fenster" in box.call_args.args[1]


def test_second_instance_with_visible_window_does_not_wait(main_env, monkeypatch):
    desktop_app = main_env
    wait = MagicMock(side_effect=AssertionError("must not wait when a window was focused"))
    monkeypatch.setattr(desktop_app, "_wait_for_single_instance_lock", wait)
    monkeypatch.setattr(desktop_app.native, "focus_existing_window", MagicMock(return_value=True))
    box = MagicMock()
    monkeypatch.setattr(desktop_app.native, "message_box", box)
    monkeypatch.setattr(sys, "platform", "win32")

    assert desktop_app._handle_second_instance() is False
    assert "vorhandene Racetag-Fenster" in box.call_args.args[1]


def test_main_data_dir_problem_shows_dialog(main_env, monkeypatch):
    desktop_app = main_env
    box = MagicMock()
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(desktop_app, "_check_data_dir", lambda path: "Datenordner kaputt")
    monkeypatch.setattr(desktop_app.native, "message_box", box)

    with pytest.raises(SystemExit) as excinfo:
        desktop_app.main()

    assert excinfo.value.code == 1
    box.assert_called_once_with("Racetag – Datenordner", "Datenordner kaputt", "error")


def test_main_missing_webview2_offers_download(main_env, monkeypatch):
    desktop_app = main_env
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(desktop_app, "_check_data_dir", lambda path: None)
    monkeypatch.setattr(desktop_app.native, "webview2_version", lambda: None)
    ask = MagicMock(return_value=True)
    open_url = MagicMock()
    monkeypatch.setattr(desktop_app.native, "ask_yes_no", ask)
    monkeypatch.setattr(desktop_app.native, "open_url", open_url)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "win32")
        with pytest.raises(SystemExit) as excinfo:
            desktop_app.main()

    assert excinfo.value.code == 1
    assert "WebView2" in ask.call_args.args[0]
    open_url.assert_called_once_with("https://go.microsoft.com/fwlink/p/?LinkId=2124703")


def test_main_happy_path_order(main_env, monkeypatch):
    """Contract 4.2 order: reap -> build -> register controller -> server ->
    ready -> supervisor.start -> window -> finally supervisor.stop."""
    desktop_app = main_env
    events = []
    supervisors = []
    backend_app = SimpleNamespace(state=SimpleNamespace())

    class FakeSupervisor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            supervisors.append(self)

        def start(self):
            events.append("supervisor.start")

        def stop(self, timeout_s=5.0):
            events.append("supervisor.stop")

    def fake_build():
        events.append("build")
        return backend_app

    def fake_run_server(app, port, handle, ready, done):
        assert getattr(app.state, "reader_controller", None) is supervisors[0], "controller before server"
        events.append("server")
        ready.set()
        done.set()

    fake_webview = types.ModuleType("webview")

    def create_window(title, url, js_api=None, **kwargs):
        assert isinstance(js_api, desktop_app._RacetagApi)
        # A stray X / Alt+F4 during a race must be confirmed.
        assert kwargs.get("confirm_close") is True
        events.append(("create_window", title, url.startswith("http://127.0.0.1:")))

    def start(gui=None, **kwargs):
        localization = kwargs.get("localization") or {}
        assert "keine Durchfahrten" in localization.get("global.quitConfirmation", "")
        assert localization.get("global.cancel") == "Abbrechen"
        events.append(("webview.start", gui))

    fake_webview.create_window = create_window
    fake_webview.start = start
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setitem(
        sys.modules,
        "racetag_backend_app",
        SimpleNamespace(_effective_config=lambda: SimpleNamespace(reader_ip=None, antenna_power=None)),
    )
    monkeypatch.setattr(desktop_app, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(desktop_app, "_check_data_dir", lambda path: None)
    monkeypatch.setattr(desktop_app.native, "webview2_version", lambda: "120.0.2210.91")
    monkeypatch.setattr(desktop_app, "_kill_stale_reader_service", lambda: events.append("reap"))
    monkeypatch.setattr(desktop_app, "_build_combined_app", fake_build)
    monkeypatch.setattr(desktop_app, "_run_server", fake_run_server)
    monkeypatch.setattr(desktop_app, "ReaderSupervisor", FakeSupervisor)
    monkeypatch.setattr(desktop_app.native, "keep_system_awake", lambda enabled: events.append(("awake", enabled)))
    monkeypatch.delenv("RACETAG_BUNDLED_READER", raising=False)

    desktop_app.main()

    expected_gui = "edgechromium" if sys.platform == "win32" else None
    assert events == [
        "reap",
        "build",
        "server",
        "supervisor.start",
        ("create_window", "Racetag", True),
        ("awake", True),
        ("webview.start", expected_gui),
        "supervisor.stop",
        ("awake", False),
    ]
    assert supervisors[0].kwargs["pid_file"] == desktop_app._READER_PID_FILE


def test_setup_logging_fallback_to_temp(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    calls = []

    def flaky_setup(log_dir):
        calls.append(log_dir)
        if len(calls) == 1:
            raise PermissionError("read-only")

    monkeypatch.setattr(desktop_app.desktop_logging, "setup_logging", flaky_setup)
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path / "ro"))
    result = desktop_app._setup_logging_with_fallback(tmp_path / "ro")
    assert result != tmp_path / "ro"
    assert calls == [tmp_path / "ro", result]
    # The reader-service child (spool, reader.log) and crash.log follow the fallback.
    assert os.environ["RACETAG_LOG_DIR"] == str(result)
    _, env = desktop_app._reader_command("http://127.0.0.1:1", None, None)
    assert env["RACETAG_LOG_DIR"] == str(result)
    assert desktop_app._crash_log_dir() == result


def test_setup_logging_without_fallback_keeps_log_dir(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    monkeypatch.setattr(desktop_app.desktop_logging, "setup_logging", lambda log_dir: None)
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    assert desktop_app._setup_logging_with_fallback(tmp_path / "logs") == tmp_path / "logs"
    assert os.environ["RACETAG_LOG_DIR"] == str(tmp_path / "logs")


def test_reader_service_role_crash_shows_no_dialog(tmp_path, monkeypatch):
    """The invisible, supervisor-restarted child must never open a crash
    dialog: it would come back after every restart."""
    import runpy  # noqa: PLC0415

    import native  # noqa: PLC0415

    monkeypatch.delenv("RACETAG_NO_DIALOGS")
    monkeypatch.setenv("RACETAG_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(native, "_crash_dialog_shown", False)
    osascript, user32 = MagicMock(), MagicMock()
    monkeypatch.setattr(native, "_osascript", osascript)
    monkeypatch.setattr(native, "_user32_dll", user32)
    fake_reader = types.ModuleType("racetag_reader_service")
    fake_reader.main = MagicMock(side_effect=OSError("DLL blockiert"))
    monkeypatch.setitem(sys.modules, "racetag_reader_service", fake_reader)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["Racetag.exe", "--reader-service", "--backend-url", "http://127.0.0.1:1"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_DESKTOP_DIR / "app.py"), run_name="__main__")

    assert excinfo.value.code == 1
    fake_reader.main.assert_called_once_with(["--backend-url", "http://127.0.0.1:1"])
    assert "DLL blockiert" in (tmp_path / "logs" / "crash.log").read_text(encoding="utf-8")
    osascript.assert_not_called()
    user32.assert_not_called()


def test_main_dispatches_selftest(monkeypatch):
    desktop_app = _import_desktop_app()
    fake_selftest = types.ModuleType("selftest")
    fake_selftest.run_selftest = MagicMock(return_value=0)
    monkeypatch.setitem(sys.modules, "selftest", fake_selftest)
    monkeypatch.setattr(sys, "argv", ["app.py", "--selftest"])
    monkeypatch.setattr(desktop_app, "_bootstrap_env", MagicMock(side_effect=AssertionError("no bootstrap")))

    with pytest.raises(SystemExit) as excinfo:
        desktop_app.main()

    assert excinfo.value.code == 0
    fake_selftest.run_selftest.assert_called_once_with(desktop_app)


# ---------------------------------------------------------------------------
# JS bridge (_RacetagApi)
# ---------------------------------------------------------------------------

def _api(desktop_app, tmp_path, backend_url=None, supervisor=None):
    if backend_url is None:
        backend_url = f"http://127.0.0.1:{desktop_app._pick_free_port()}"  # nothing listens
    return desktop_app._RacetagApi(
        backend_url, tmp_path / "data", tmp_path / "logs", tmp_path / "home" / ".racetag", supervisor
    )


def test_api_exposes_exactly_the_bridge_methods(tmp_path):
    desktop_app = _import_desktop_app()
    api = _api(desktop_app, tmp_path)
    public = sorted(name for name in dir(api) if not name.startswith("_"))
    assert public == ["app_info", "create_support_bundle", "open_data_folder", "save_csv"]


def test_save_csv_writes_file(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    target = tmp_path / "export.csv"
    window = _install_fake_webview(monkeypatch, [str(target)])

    assert _api(desktop_app, tmp_path).save_csv("a;b\n1;2\n", "rennen.csv") is True

    assert target.read_text(encoding="utf-8") == "a;b\n1;2\n"
    dialog_type, kwargs = window.calls[0]
    assert dialog_type == 30  # FileDialog.SAVE
    assert kwargs["save_filename"] == "rennen.csv"


def test_save_csv_cancel_and_no_window(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    _install_fake_webview(monkeypatch, None)
    assert _api(desktop_app, tmp_path).save_csv("x") is False
    _install_fake_webview(monkeypatch, None, with_window=False)
    assert _api(desktop_app, tmp_path).save_csv("x") is False


def test_save_csv_write_failure_is_reported_not_cancelled(tmp_path, monkeypatch):
    """E.g. the file is still open in Excel: the operator must learn that the
    export failed, not read 'Export abgebrochen'."""
    desktop_app = _import_desktop_app()
    target = tmp_path / "export.csv"
    target.write_text("alte Ergebnisse", encoding="utf-8")
    _install_fake_webview(monkeypatch, [str(target)])
    box = MagicMock()
    monkeypatch.setattr(desktop_app.native, "message_box", box)

    def locked_open(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(desktop_app, "open", locked_open, raising=False)

    with pytest.raises(OSError):
        _api(desktop_app, tmp_path).save_csv("a;b\n", "export.csv")

    title, text, kind = box.call_args.args
    assert title == "Racetag – Export" and kind == "error"
    assert str(target) in text and "Excel" in text
    assert target.read_text(encoding="utf-8") == "alte Ergebnisse"


def test_open_data_folder(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    open_path = MagicMock(return_value=True)
    monkeypatch.setattr(desktop_app.native, "open_path", open_path)

    assert _api(desktop_app, tmp_path).open_data_folder() is True

    home = tmp_path / "home" / ".racetag"
    assert home.is_dir()
    open_path.assert_called_once_with(home)


def test_app_info(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    monkeypatch.setenv("RACETAG_VERSION", "0.3.0")
    info = _api(desktop_app, tmp_path).app_info()
    assert set(info) == {"version", "data_dir", "log_dir", "platform"}
    assert info["version"] == "0.3.0"
    assert info["data_dir"] == str(tmp_path / "data")
    assert info["log_dir"] == str(tmp_path / "logs")
    assert info["platform"]


def test_create_support_bundle(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "shell.log").write_text("hello\n", encoding="utf-8")
    window = _install_fake_webview(monkeypatch, [str(tmp_path / "Support-Paket")])
    onedrive_desktop = tmp_path / "OneDrive" / "Desktop"
    monkeypatch.setattr(desktop_app.native, "desktop_dir", lambda: onedrive_desktop)
    supervisor = MagicMock()
    supervisor.status.return_value = {"running": True, "pid": 1, "restart_count": 0, "last_exit_code": None}

    result = _api(desktop_app, tmp_path, supervisor=supervisor).create_support_bundle()

    assert result == {"ok": True, "path": str(tmp_path / "Support-Paket.zip"), "error": None}
    dialog_type, kwargs = window.calls[0]
    assert dialog_type == 30
    assert re.fullmatch(r"Racetag-Support-\d{8}-\d{4}\.zip", kwargs["save_filename"])
    assert kwargs["directory"] == str(onedrive_desktop), "the real (possibly redirected) Desktop"
    with zipfile.ZipFile(result["path"]) as zf:
        names = set(zf.namelist())
        import json  # noqa: PLC0415

        app_info = json.loads(zf.read("app_info.json"))
        config = json.loads(zf.read("config.json"))
    assert {"logs/shell.log", "config.json", "reader_status.json", "app_info.json"} <= names
    assert app_info["supervisor"]["running"] is True
    assert "error" in config, "unreachable backend is recorded, not fatal"


def test_create_support_bundle_cancelled(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    _install_fake_webview(monkeypatch, None)
    assert _api(desktop_app, tmp_path).create_support_bundle() == {"ok": False, "path": None, "error": None}


def test_create_support_bundle_error_is_reported(tmp_path, monkeypatch):
    desktop_app = _import_desktop_app()
    _install_fake_webview(monkeypatch, [str(tmp_path / "x.zip")])
    monkeypatch.setattr(
        desktop_app.support_bundle, "build_support_bundle", MagicMock(side_effect=OSError("disk full"))
    )
    result = _api(desktop_app, tmp_path).create_support_bundle()
    assert result["ok"] is False and result["path"] is None and "disk full" in result["error"]
