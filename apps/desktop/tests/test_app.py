"""Unit tests for apps/desktop/app.py (W-070, W-071, W-072, W-073).

These tests exercise the helper functions in isolation without launching
pywebview or a real uvicorn server, so they run headlessly in CI.
"""
import ast
import importlib
import os
import socket
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_desktop_app():
    """Import apps/desktop/app.py as a fresh module.

    We reload on each call so that module-level state (e.g. sys.path mutations
    from _build_combined_app) doesn't bleed between tests.
    """
    # Remove cached copy if present so importlib.import_module re-executes
    # module-level code (important after monkeypatching env vars).
    sys.modules.pop("app", None)
    import app as desktop_app  # noqa: PLC0415
    return desktop_app


# ---------------------------------------------------------------------------
# test_pick_free_port_returns_valid_port
# ---------------------------------------------------------------------------

def test_pick_free_port_returns_valid_port():
    """_pick_free_port() must return an ephemeral port in the valid range."""
    desktop_app = _import_desktop_app()
    port = desktop_app._pick_free_port()
    assert isinstance(port, int), "port must be an int"
    assert 1024 < port < 65536, f"port {port} out of ephemeral range"

    # Verify the port is actually free (nothing should be listening on it yet).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # If the port is free we can bind; if not, this raises OSError.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


# ---------------------------------------------------------------------------
# test_bootstrap_env_creates_data_dir
# ---------------------------------------------------------------------------

def test_bootstrap_env_creates_data_dir(tmp_path, monkeypatch):
    """_bootstrap_env() must create RACETAG_DATA_DIR when it does not exist."""
    target = tmp_path / "racetag_test_data"
    assert not target.exists(), "precondition: dir must not exist yet"

    monkeypatch.setenv("RACETAG_DATA_DIR", str(target))

    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()

    assert target.exists(), "_bootstrap_env() must create RACETAG_DATA_DIR"
    assert target.is_dir(), "RACETAG_DATA_DIR must be a directory"


def test_bootstrap_env_uses_default_when_unset(tmp_path, monkeypatch):
    """_bootstrap_env() sets a default path when RACETAG_DATA_DIR is absent."""
    monkeypatch.delenv("RACETAG_DATA_DIR", raising=False)
    # We don't want to touch the real ~/.racetag/data, so override HOME.
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    desktop_app = _import_desktop_app()
    desktop_app._bootstrap_env()

    expected = fake_home / ".racetag" / "data"
    assert expected.exists(), "default data dir must be created under $HOME"


# ---------------------------------------------------------------------------
# test_build_combined_app_mounts_frontend
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# W-073 tests: reader-service subprocess helpers
# ---------------------------------------------------------------------------

_DESKTOP_DIR = Path(__file__).resolve().parent.parent


def test_reader_service_entry_non_frozen():
    """_reader_service_entry() in source mode must point at racetag_reader_service.py."""
    # Ensure sys.frozen is not set (source mode).
    import builtins  # noqa: PLC0415

    desktop_app = _import_desktop_app()

    # Remove 'frozen' attribute from sys if present (it shouldn't be in tests).
    frozen_backup = getattr(sys, "frozen", None)
    if hasattr(sys, "frozen"):
        del sys.frozen

    try:
        entry = desktop_app._reader_service_entry()
    finally:
        if frozen_backup is not None:
            sys.frozen = frozen_backup

    assert isinstance(entry, list), "_reader_service_entry() must return a list"
    assert len(entry) >= 2, "entry list must have at least 2 elements"
    # Last element must be the reader script path.
    last = entry[-1]
    assert last.endswith("racetag_reader_service.py"), (
        f"Expected entry to end with racetag_reader_service.py, got: {last}"
    )
    # The script must actually exist on disk.
    assert Path(last).exists(), f"Reader script does not exist: {last}"


def test_spawn_reader_skipped_when_env_set(monkeypatch):
    """_spawn_reader_service() must return None when RACETAG_BUNDLED_READER=0."""
    monkeypatch.setenv("RACETAG_BUNDLED_READER", "0")
    # Also ensure READER_IP has a value so env.copy() doesn't stumble.
    monkeypatch.setenv("READER_IP", "127.0.0.1")

    desktop_app = _import_desktop_app()
    result = desktop_app._spawn_reader_service(backend_url="http://127.0.0.1:9999")

    assert result is None, (
        "_spawn_reader_service() must return None when RACETAG_BUNDLED_READER=0"
    )
    # Confirm no subprocess was stored.
    assert desktop_app._reader_proc is None


# ---------------------------------------------------------------------------
# W-071 / W-072 spec parse tests
# ---------------------------------------------------------------------------


def test_pyinstaller_mac_spec_parses():
    """pyinstaller.mac.spec must be syntactically valid Python."""
    spec_path = _DESKTOP_DIR / "pyinstaller.mac.spec"
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    source = spec_path.read_text(encoding="utf-8")
    # ast.parse raises SyntaxError on invalid Python.
    ast.parse(source)


def test_pyinstaller_win_spec_parses():
    """pyinstaller.win.spec must be syntactically valid Python."""
    spec_path = _DESKTOP_DIR / "pyinstaller.win.spec"
    assert spec_path.exists(), f"Spec file not found: {spec_path}"
    source = spec_path.read_text(encoding="utf-8")
    ast.parse(source)


# ---------------------------------------------------------------------------
# W-072 version info generator test
# ---------------------------------------------------------------------------


def test_win_version_info_generated(tmp_path, monkeypatch):
    """generate_win_version_info.py must produce a file containing 'Racetag'
    and the correct version tuple."""
    import importlib.util as ilu  # noqa: PLC0415

    gen_path = _DESKTOP_DIR / "generate_win_version_info.py"
    assert gen_path.exists(), f"Generator script not found: {gen_path}"

    # Patch __file__ inside the generator so it writes to tmp_path.
    # Strategy: load the generator module and temporarily swap its __file__
    # so the Path(__file__).resolve().parent resolves to a tmp dir that has a
    # VERSION file in it.
    fake_script_dir = tmp_path / "desktop"
    fake_script_dir.mkdir()
    (fake_script_dir / "VERSION").write_text("0.1.0\n")

    spec = ilu.spec_from_file_location("_gen_win_ver", str(gen_path))
    mod = ilu.module_from_spec(spec)
    # Override __file__ so Path(__file__).resolve().parent is our fake dir.
    mod.__file__ = str(fake_script_dir / "generate_win_version_info.py")
    spec.loader.exec_module(mod)

    # Run main() — it will write to fake_script_dir/win_version_info.txt.
    mod.main()

    out = (fake_script_dir / "win_version_info.txt").read_text(encoding="utf-8")
    assert "Racetag" in out, "win_version_info.txt must contain 'Racetag'"
    assert "(0, 1, 0, 0)" in out, "win_version_info.txt must contain version tuple (0, 1, 0, 0)"
    assert "0.1.0" in out, "win_version_info.txt must contain version string '0.1.0'"


# ---------------------------------------------------------------------------
# W-070 tests (original)
# ---------------------------------------------------------------------------

def test_build_combined_app_mounts_frontend(tmp_path, monkeypatch):
    """_build_combined_app() must produce a FastAPI app with a / static mount.

    We exercise this by calling the function and inspecting the returned app's
    routes for the mounted StaticFiles route, then making a TestClient request
    to "/" and confirming it returns a 200 with HTML content.
    """
    # Redirect data dir so the backend's module-level Storage() writes here.
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path / "data"))

    # Remove any previously imported backend app so _build_combined_app gets a
    # clean import (avoids cross-test state from module-level race/storage init).
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("app",) and "desktop" not in str(
            getattr(sys.modules.get(mod_name), "__file__", "")
        ):
            sys.modules.pop(mod_name, None)

    desktop_app = _import_desktop_app()
    combined = desktop_app._build_combined_app()

    # --- structural check: there must be a Mount covering "/" ---
    # Starlette normalises a mount path of "/" to "" internally, so we check
    # for either form.
    from starlette.routing import Mount  # noqa: PLC0415

    mount_paths = [
        r.path for r in combined.routes if isinstance(r, Mount)
    ]
    assert any(p in ("", "/") for p in mount_paths), (
        f"Expected a root Mount ('/' or '') but found mounts at: {mount_paths}"
    )

    # --- functional check: GET / returns 200 with HTML ---
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(combined, raise_server_exceptions=False) as client:
        resp = client.get("/")
    assert resp.status_code == 200, f"GET / returned {resp.status_code}"
    content_type = resp.headers.get("content-type", "")
    assert "text/html" in content_type, (
        f"Expected text/html content-type, got: {content_type}"
    )
