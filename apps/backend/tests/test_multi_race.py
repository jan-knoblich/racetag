"""Tests for multi-race functionality (2026-05-25).

Covers:
- /races CRUD (create, list, get, patch, delete).
- Active-race switching (/races/{id}/activate) — including rebuild of
  in-memory race + rider_store + replayed events.
- Per-race rider isolation: the same tag_id can map to different riders in
  different races.
- /race/end gates add_lap (post-end reads don't change standings).
- CSV export (text/csv with BOM, attachment filename, expected columns).
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fresh_app():
    import app as app_module
    importlib.reload(app_module)
    from app import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, app_module


# ---------------------------------------------------------------------------
# /races CRUD
# ---------------------------------------------------------------------------

def test_races_list_contains_default_race_on_fresh_db(fresh_app):
    """Storage bootstrap creates a "Default race" on a fresh DB and sets it active."""
    client, _ = fresh_app
    resp = client.get("/races")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert body["active_race_id"] is not None
    # Default race should be marked active.
    active = [r for r in body["items"] if r["is_active"]]
    assert len(active) == 1


def test_create_race_returns_summary(fresh_app):
    client, _ = fresh_app
    resp = client.post("/races", json={
        "name": "Volksradrennen Sonntag",
        "scheduled_at": "2026-06-24T10:00:00Z",
        "total_laps": 8,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Volksradrennen Sonntag"
    assert body["scheduled_at"].startswith("2026-06-24")
    assert body["total_laps"] == 8
    # Newly created race is NOT auto-activated.
    assert body["is_active"] is False


def test_get_specific_race(fresh_app):
    client, _ = fresh_app
    created = client.post("/races", json={"name": "Race A"}).json()
    resp = client.get(f"/races/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Race A"


def test_patch_race_updates_fields(fresh_app):
    client, _ = fresh_app
    created = client.post("/races", json={"name": "Old name", "total_laps": 5}).json()
    resp = client.patch(f"/races/{created['id']}", json={
        "name": "New name",
        "total_laps": 10,
        "scheduled_at": "2026-07-01T09:00:00Z",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "New name"
    assert body["total_laps"] == 10
    assert body["scheduled_at"].startswith("2026-07-01")


def test_cannot_delete_active_race(fresh_app):
    client, _ = fresh_app
    # The default race is active and shouldn't be deletable.
    active_id = client.get("/races").json()["active_race_id"]
    resp = client.delete(f"/races/{active_id}")
    assert resp.status_code == 409
    assert "active" in resp.json()["detail"].lower()


def test_delete_non_active_race(fresh_app):
    client, _ = fresh_app
    created = client.post("/races", json={"name": "To be deleted"}).json()
    resp = client.delete(f"/races/{created['id']}")
    assert resp.status_code == 204
    # Confirm gone
    assert client.get(f"/races/{created['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Active-race switching
# ---------------------------------------------------------------------------

def test_activate_race_changes_active_and_resets_in_memory(fresh_app):
    client, app_module = fresh_app
    # Create a second race and start a lap on the first
    resp = client.post("/race/start")
    assert resp.status_code == 200

    other = client.post("/races", json={"name": "Race 2", "total_laps": 3}).json()

    # Activate the new race
    resp = client.post(f"/races/{other['id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    # The in-memory race should now mirror Race 2 (not started, total_laps=3)
    assert app_module.race.race_id == other["id"]
    assert app_module.race.total_laps == 3
    assert app_module.race.started is False


# ---------------------------------------------------------------------------
# Per-race rider isolation
# ---------------------------------------------------------------------------

def test_same_tag_can_be_different_riders_in_different_races(fresh_app):
    """Same physical tag → different (bib, name) in race A vs race B."""
    client, _ = fresh_app

    # Race A (default), register tag X as "Alice / 1"
    resp = client.post("/riders", json={"tag_id": "TAG-X", "bib": "1", "name": "Alice"})
    assert resp.status_code == 201

    race_b = client.post("/races", json={"name": "Race B"}).json()
    client.post(f"/races/{race_b['id']}/activate")

    # In Race B, the same tag should NOT be registered
    resp = client.get("/riders/TAG-X")
    assert resp.status_code == 404

    # Register it differently here
    resp = client.post("/riders", json={"tag_id": "TAG-X", "bib": "42", "name": "Bob"})
    assert resp.status_code == 201

    # Switch back to Race A — Alice should still be there
    races = client.get("/races").json()["items"]
    race_a_id = next(r["id"] for r in races if r["name"] == "Default race")
    client.post(f"/races/{race_a_id}/activate")

    resp = client.get("/riders/TAG-X")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alice"
    assert resp.json()["bib"] == "1"


# ---------------------------------------------------------------------------
# End-race
# ---------------------------------------------------------------------------

def test_race_end_freezes_standings(fresh_app):
    """After /race/end, add_lap is a no-op so standings stay frozen."""
    client, app_module = fresh_app

    # Start race, post a pass that counts a lap
    from domain.race import parse_iso as _parse_iso
    app_module.race.start(now=_parse_iso("2026-04-15T11:00:00.000Z"))

    # BUG-003 fix: register tag before it appears in standings.
    client.post("/riders", json={"tag_id": "ENDTEST", "bib": "1", "name": "T"})

    client.post("/events/tag/batch", json={"events": [{
        "source": "test", "reader_ip": "127.0.0.1",
        "timestamp": "2026-04-15T12:00:00.000Z",
        "event_type": "arrive", "tag_id": "ENDTEST",
    }]})

    before = client.get("/classification").json()
    laps_before = next(r["laps"] for r in before["standings"] if r["tag_id"] == "ENDTEST")
    assert laps_before == 1

    # End the race
    resp = client.post("/race/end")
    assert resp.status_code == 200
    assert resp.json()["ended"] is True

    # Another pass — well outside the cooldown — must NOT change laps
    client.post("/events/tag/batch", json={"events": [{
        "source": "test", "reader_ip": "127.0.0.1",
        "timestamp": "2026-04-15T12:01:00.000Z",
        "event_type": "arrive", "tag_id": "ENDTEST",
    }]})

    after = client.get("/classification").json()
    laps_after = next(r["laps"] for r in after["standings"] if r["tag_id"] == "ENDTEST")
    assert laps_after == laps_before, "post-end pass must not change laps"


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def test_classification_csv_returns_csv_with_bom_and_columns(fresh_app):
    """GET /classification.csv returns text/csv with UTF-8 BOM and the expected columns."""
    client, app_module = fresh_app

    # Set up: rename active race + start + post one counted lap
    from domain.race import parse_iso as _parse_iso

    active_id = client.get("/races").json()["active_race_id"]
    client.patch(f"/races/{active_id}", json={
        "name": "Test Cup",
        "scheduled_at": "2026-06-24T10:00:00Z",
    })

    # Register a rider so the CSV has bib/name populated
    client.post("/riders", json={"tag_id": "CSVTAG", "bib": "7", "name": "Lance"})

    app_module.race.start(now=_parse_iso("2026-04-15T11:00:00.000Z"))
    client.post("/events/tag/batch", json={"events": [{
        "source": "test", "reader_ip": "127.0.0.1",
        "timestamp": "2026-04-15T12:00:00.000Z",
        "event_type": "arrive", "tag_id": "CSVTAG",
    }]})

    resp = client.get("/classification.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "test-cup" in resp.headers["content-disposition"].lower()

    body = resp.text
    # UTF-8 BOM at start
    assert body.startswith("﻿")
    # Metadata comments
    assert "# Race: Test Cup" in body
    assert "# Scheduled: 2026-06-24" in body
    # Header row + at least one data row containing our rider
    assert "position,bib,name,tag_id" in body
    assert "Lance" in body
    assert "CSVTAG" in body


def test_classification_csv_for_non_active_race_returns_409(fresh_app):
    """Per-race CSV export only works on the active race for now."""
    client, _ = fresh_app
    other = client.post("/races", json={"name": "Inactive"}).json()
    resp = client.get(f"/races/{other['id']}/classification.csv")
    assert resp.status_code == 409
