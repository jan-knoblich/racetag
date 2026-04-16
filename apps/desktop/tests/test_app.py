"""Unit tests for apps/desktop/app.py (W-070).

These tests exercise the helper functions in isolation without launching
pywebview or a real uvicorn server, so they run headlessly in CI.
"""
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
