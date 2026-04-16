"""Integration tests for W-050: backend state survives restart.

Strategy:
  1. Set RACETAG_DATA_DIR to a tmp_path, reload app → use TestClient to POST state.
  2. Reload app again with the same RACETAG_DATA_DIR → replay kicks in.
  3. Assert GET endpoints return the same state.

The autouse `_isolate_data_dir` fixture in conftest.py would point every test
at its own fresh tmp_path.  We override it by calling monkeypatch.setenv again
with our controlled path before any reload, so both the "write" and "read"
phases share the same DB file.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _tag_event(
    tag_id: str,
    ts: str = "2026-04-15T12:00:00.000Z",
    antenna: int = 1,
    rssi: int = -55,
) -> dict:
    return {
        "source": "test",
        "reader_ip": "127.0.0.1",
        "reader_serial": "SN001",
        "timestamp": ts,
        "event_type": "arrive",
        "tag_id": tag_id,
        "antenna": antenna,
        "rssi": rssi,
    }


def _batch(events: list) -> dict:
    return {"events": events}


def _load_fresh_app(data_dir: str):
    """Reload app with RACETAG_DATA_DIR pointing at data_dir.

    Returns (TestClient, app_module).  Caller is responsible for entering the
    TestClient context manager if needed; we return it un-entered so tests can
    use it as a context manager or directly.
    """
    import app as app_module
    importlib.reload(app_module)
    return app_module


# ---------------------------------------------------------------------------
# test_restart_preserves_riders
# ---------------------------------------------------------------------------

def test_restart_preserves_riders(tmp_path, monkeypatch):
    """POST 5 riders, reload with same data dir, GET /riders returns all 5."""
    data_dir = str(tmp_path / "racedata")

    # Phase 1: write state
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    app_module = _load_fresh_app(data_dir)
    with TestClient(app_module.app) as client:
        for i in range(5):
            resp = client.post(
                "/riders",
                json={"tag_id": f"RIDER{i:02d}", "bib": str(i), "name": f"Rider {i}"},
            )
            assert resp.status_code == 201, f"POST /riders failed: {resp.text}"

        # Sanity: all 5 visible in same session
        list_resp = client.get("/riders")
        assert list_resp.json()["count"] == 5

    # Phase 2: simulate restart — reload the module (same env var still set)
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        list_resp2 = client2.get("/riders")
        body = list_resp2.json()
        assert body["count"] == 5, f"Expected 5 riders after restart, got: {body}"
        returned_tags = {r["tag_id"] for r in body["items"]}
        expected_tags = {f"RIDER{i:02d}" for i in range(5)}
        assert returned_tags == expected_tags


# ---------------------------------------------------------------------------
# test_restart_preserves_standings
# ---------------------------------------------------------------------------

def test_restart_preserves_standings(tmp_path, monkeypatch):
    """POST events for 3 tags, reload, GET /classification returns same lap counts."""
    data_dir = str(tmp_path / "standingsdata")

    # Use a very short min_pass_interval so multiple laps are easy to post.
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    # Phase 1: write events — 3 tags with different lap counts.
    # Timestamps must be strictly increasing per tag so the 0-second interval
    # gate is never triggered (delta > 0 is needed for the boundary check).
    app_module = _load_fresh_app(data_dir)
    with TestClient(app_module.app) as client:
        # TAG-A: 3 laps
        for lap in range(3):
            ts = f"2026-04-15T12:{lap:02d}:00.000Z"
            resp = client.post("/events/tag/batch", json=_batch([_tag_event("TAGA", ts)]))
            assert resp.status_code == 200

        # TAG-B: 2 laps
        for lap in range(2):
            ts = f"2026-04-15T12:{lap:02d}:01.000Z"
            resp = client.post("/events/tag/batch", json=_batch([_tag_event("TAGB", ts)]))
            assert resp.status_code == 200

        # TAG-C: 1 lap
        resp = client.post(
            "/events/tag/batch",
            json=_batch([_tag_event("TAGC", "2026-04-15T12:00:02.000Z")]),
        )
        assert resp.status_code == 200

        # Baseline standings in same session
        cls_resp = client.get("/classification")
        assert cls_resp.status_code == 200
        baseline = {s["tag_id"]: s["laps"] for s in cls_resp.json()["standings"]}
        assert baseline["TAGA"] == 3
        assert baseline["TAGB"] == 2
        assert baseline["TAGC"] == 1

    # Phase 2: simulate restart
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        cls_resp2 = client2.get("/classification")
        assert cls_resp2.status_code == 200
        after = {s["tag_id"]: s["laps"] for s in cls_resp2.json()["standings"]}
        assert after == baseline, f"Standings after restart differ: {after} != {baseline}"


# ---------------------------------------------------------------------------
# test_durability_autocommit
# ---------------------------------------------------------------------------

def test_durability_autocommit(tmp_path, monkeypatch):
    """Verify events are committed even if storage object is not explicitly closed.

    Drops the storage object without calling .close(); a new Storage instance
    pointing at the same file must still see the row.  This proves autocommit
    (isolation_level=None) is working — no explicit COMMIT needed after each
    statement.
    """
    from storage import Storage
    from models_api import EventType, TagEventDTO

    db_path = tmp_path / "durable.db"

    ev = TagEventDTO(
        source="test",
        reader_ip="127.0.0.1",
        timestamp="2026-04-15T12:00:00.000Z",
        event_type=EventType.arrive,
        tag_id="DURABLETAG",
    )

    # Write — intentionally do NOT call .close()
    db1 = Storage(db_path)
    db1.append_event(ev)
    del db1  # drop without explicit close

    # Read with a fresh connection
    db2 = Storage(db_path)
    try:
        count = db2.count_events()
        assert count == 1, f"Expected 1 event after drop-without-close, got {count}"
    finally:
        db2.close()
