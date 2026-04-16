"""Tests for W-074: GET /config and PATCH /config.

Each test reloads the app module (importlib.reload) so that module-level
state (race, storage, config_store) is always fresh.  The autouse
_isolate_data_dir fixture in conftest.py redirects RACETAG_DATA_DIR to a
per-test tmp dir.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_app(data_dir: str, monkeypatch):
    """Reload app module pointing at *data_dir*."""
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    import app as app_module
    importlib.reload(app_module)
    return app_module


# ---------------------------------------------------------------------------
# test_get_config_returns_defaults_from_env
# ---------------------------------------------------------------------------

def test_get_config_returns_defaults_from_env(tmp_path, monkeypatch):
    """No meta rows exist; env default for total_laps (5) should be returned."""
    monkeypatch.setenv("RACE_TOTAL_LAPS", "5")
    monkeypatch.setenv("MIN_LAP_INTERVAL_S", "8.0")

    app_module = _fresh_app(str(tmp_path), monkeypatch)

    with TestClient(app_module.app) as client:
        resp = client.get("/config")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_laps"] == 5
        assert data["min_lap_interval_s"] == 8.0
        # reader_ip not set in env → None
        assert data["reader_ip"] is None


# ---------------------------------------------------------------------------
# test_patch_config_persists
# ---------------------------------------------------------------------------

def test_patch_config_persists(tmp_path, monkeypatch):
    """PATCH {total_laps: 7} should survive a module reload (simulated restart)."""
    data_dir = str(tmp_path / "cfgdata")

    # Phase 1: write total_laps=7
    app_module = _fresh_app(data_dir, monkeypatch)
    with TestClient(app_module.app) as client:
        resp = client.patch("/config", json={"total_laps": 7})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_laps"] == 7

    # Phase 2: reload (simulate restart) — persisted value must come back
    app_module2 = _fresh_app(data_dir, monkeypatch)
    with TestClient(app_module2.app) as client2:
        resp2 = client2.get("/config")
        assert resp2.status_code == 200
        assert resp2.json()["total_laps"] == 7, (
            f"Expected total_laps=7 after restart, got: {resp2.json()}"
        )


# ---------------------------------------------------------------------------
# test_patch_config_validates_ranges
# ---------------------------------------------------------------------------

def test_patch_config_validates_ranges(tmp_path, monkeypatch):
    """Out-of-range total_laps (1000) must be rejected with 422."""
    app_module = _fresh_app(str(tmp_path), monkeypatch)

    with TestClient(app_module.app) as client:
        resp = client.patch("/config", json={"total_laps": 1000})
        assert resp.status_code == 422, resp.text

    # Also verify min_lap_interval_s out of range (negative)
    with TestClient(app_module.app) as client:
        resp = client.patch("/config", json={"min_lap_interval_s": -1.0})
        assert resp.status_code == 422, resp.text

    # Also verify invalid IPv4
    with TestClient(app_module.app) as client:
        resp = client.patch("/config", json={"reader_ip": "999.999.999.999"})
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# test_patch_config_updates_race_total_laps_live
# ---------------------------------------------------------------------------

def test_patch_config_updates_race_total_laps_live(tmp_path, monkeypatch):
    """PATCH {total_laps: 9} must update race.total_laps in-process immediately
    (no restart required)."""
    app_module = _fresh_app(str(tmp_path), monkeypatch)

    with TestClient(app_module.app) as client:
        resp = client.patch("/config", json={"total_laps": 9})
        assert resp.status_code == 200, resp.text

    # Check the in-memory race object directly
    assert app_module.race.total_laps == 9, (
        f"Expected race.total_laps=9, got {app_module.race.total_laps}"
    )
