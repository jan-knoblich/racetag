"""Tests for W-036: POST /race/reset and PATCH /race.

Each test reloads the app module (importlib.reload) so that module-level
state (race, storage, events) is always fresh.  The autouse _isolate_data_dir
fixture in conftest.py redirects RACETAG_DATA_DIR to a per-test tmp dir.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag_event(
    tag_id: str,
    ts: str = "2026-04-15T12:00:00.000Z",
    antenna: int = 1,
) -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
        "antenna": antenna,
    }


def _batch(events: list) -> dict:
    return {"events": events}


def _fresh_app(data_dir: str | None = None, monkeypatch=None):
    """Reload app module, optionally pointing at a specific data dir."""
    if data_dir is not None and monkeypatch is not None:
        monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    import app as app_module
    importlib.reload(app_module)
    return app_module


# ---------------------------------------------------------------------------
# test_reset_clears_participants_and_events
# ---------------------------------------------------------------------------

def test_reset_clears_participants_and_events(tmp_path, monkeypatch):
    """POST 3 arrive events for different tags, then POST /race/reset, then
    GET /classification — standings must be empty."""
    monkeypatch.setenv("RACETAG_MIN_PASS_INTERVAL_S", "0")
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    app_module = _fresh_app(str(tmp_path), monkeypatch)

    with TestClient(app_module.app) as client:
        # Ingest one lap each for three different tags.
        for tag in ("T001", "T002", "T003"):
            resp = client.post(
                "/events/tag/batch",
                json=_batch([_tag_event(tag)]),
            )
            assert resp.status_code == 200, resp.text

        # Confirm standings are non-empty.
        cls = client.get("/classification").json()
        assert cls["count"] == 3

        # Reset.
        reset_resp = client.post("/race/reset")
        assert reset_resp.status_code == 204

        # Standings must now be empty.
        cls_after = client.get("/classification").json()
        assert cls_after["count"] == 0, f"Expected 0, got: {cls_after}"

        # Persisted events must also be gone.
        assert app_module.storage.count_events() == 0


# ---------------------------------------------------------------------------
# test_patch_race_total_laps_persists
# ---------------------------------------------------------------------------

def test_patch_race_total_laps_persists(tmp_path, monkeypatch):
    """PATCH /race with total_laps=7, reload (simulate restart), GET /race
    must return total_laps=7."""
    data_dir = str(tmp_path / "lapsdata")

    # Phase 1: set total_laps to 7.
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    app_module = _fresh_app(data_dir, monkeypatch)

    with TestClient(app_module.app) as client:
        resp = client.patch("/race", json={"total_laps": 7})
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_laps"] == 7

    # Phase 2: reload — simulate restart using same data dir.
    app_module2 = _fresh_app(data_dir, monkeypatch)

    with TestClient(app_module2.app) as client2:
        race_resp = client2.get("/race")
        assert race_resp.status_code == 200
        assert race_resp.json()["total_laps"] == 7, (
            f"Expected total_laps=7 after restart, got: {race_resp.json()}"
        )


# ---------------------------------------------------------------------------
# test_reset_preserves_riders
# ---------------------------------------------------------------------------

def test_reset_preserves_riders(tmp_path, monkeypatch):
    """Register 2 riders, POST /race/reset, GET /riders must still return 2."""
    app_module = _fresh_app(str(tmp_path), monkeypatch)

    with TestClient(app_module.app) as client:
        # Register two riders.
        for i in range(2):
            resp = client.post(
                "/riders",
                json={"tag_id": f"RIDER{i}", "bib": str(i + 1), "name": f"Rider {i}"},
            )
            assert resp.status_code == 201, resp.text

        # Verify 2 riders present.
        assert client.get("/riders").json()["count"] == 2

        # Reset race.
        assert client.post("/race/reset").status_code == 204

        # Riders must still exist.
        riders_after = client.get("/riders").json()
        assert riders_after["count"] == 2, f"Expected 2 riders, got: {riders_after}"
