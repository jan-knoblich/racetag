"""Tests for manual lap correction (POST/DELETE /riders/{tag_id}/laps).

Operator can credit (+1) or revoke (-1) a lap for a registered rider when
the reader miscounts. Synthetic events are persisted with
reader_serial="MANUAL" so they can be distinguished in audits / replay.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fresh_app(monkeypatch):
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, app_module


def _start_race(app_module, started_at_iso="2026-04-15T11:00:00.000Z"):
    from domain.race import parse_iso as _parse_iso
    started_at_dt = _parse_iso(started_at_iso)
    app_module.race.start(now=started_at_dt)
    app_module.storage.set_meta("race_started_at", started_at_iso)
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True, started_at=started_at_dt
        )


def _register(client, tag_id, bib="1", name="Tester"):
    return client.post(
        "/riders", json={"tag_id": tag_id, "bib": bib, "name": name}
    )


# ---------------------------------------------------------------------------
# +1 lap
# ---------------------------------------------------------------------------

def test_manual_add_lap_with_server_timestamp_increments_laps(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    r = client.post("/riders/TAG_A/laps", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tag_id"] == "TAG_A"
    assert body["laps"] == 1
    assert body["last_pass_time"] is not None


def test_manual_add_lap_with_explicit_past_timestamp(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    custom_ts = "2026-04-15T12:00:00.000Z"
    r = client.post("/riders/TAG_A/laps", json={"timestamp": custom_ts})
    assert r.status_code == 201
    assert r.json()["last_pass_time"] == custom_ts


def test_manual_add_lap_404_when_rider_not_registered(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)

    r = client.post("/riders/UNKNOWN/laps", json={})
    assert r.status_code == 404


def test_manual_add_lap_persists_with_manual_marker(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    custom_ts = "2026-04-15T12:00:05.000Z"
    assert client.post(
        "/riders/TAG_A/laps", json={"timestamp": custom_ts}
    ).status_code == 201

    # Check tag_events for the MANUAL marker
    rows = list(app_module.storage.iter_events())
    manual_rows = [r for r in rows if r.reader_serial == "MANUAL"]
    assert len(manual_rows) == 1
    assert manual_rows[0].tag_id == "TAG_A"
    assert manual_rows[0].timestamp == custom_ts


def test_manual_add_lap_respects_cooldown(fresh_app, monkeypatch):
    """Two manual clicks within min_pass_interval_s — second returns 409 and is
    NOT persisted (so it can't reappear as a lap on restart)."""
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "10")
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        _start_race(app_module)
        assert _register(client, "TAG_A").status_code == 201

        ts1 = "2026-04-15T12:00:00.000Z"
        ts2 = "2026-04-15T12:00:05.000Z"  # 5s later, < 10s cooldown
        r1 = client.post("/riders/TAG_A/laps", json={"timestamp": ts1})
        r2 = client.post("/riders/TAG_A/laps", json={"timestamp": ts2})
        assert r1.status_code == 201
        assert r1.json()["laps"] == 1
        # Second click hits the cooldown → 409, no lap, no synthetic event persisted.
        assert r2.status_code == 409
        # Sanity: only one tag_event in storage (the first), so a restart can't
        # rehydrate a phantom 2nd lap.
        assert app_module.storage.count_events() == 1


# ---------------------------------------------------------------------------
# -1 lap
# ---------------------------------------------------------------------------

def test_manual_remove_lap_decrements_laps(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    # Add 3 laps
    for i in range(3):
        ts = f"2026-04-15T12:0{i}:00.000Z"
        assert client.post(
            "/riders/TAG_A/laps", json={"timestamp": ts}
        ).status_code == 201

    cls = client.get("/classification").json()
    assert cls["standings"][0]["laps"] == 3

    # Remove one
    r = client.delete("/riders/TAG_A/laps")
    assert r.status_code == 200, r.text
    assert r.json()["laps"] == 2

    cls_after = client.get("/classification").json()
    assert cls_after["standings"][0]["laps"] == 2


def test_manual_remove_lap_400_when_no_events(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    r = client.delete("/riders/TAG_A/laps")
    assert r.status_code == 400


def test_manual_remove_lap_404_when_not_registered(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)

    r = client.delete("/riders/UNKNOWN/laps")
    assert r.status_code == 404


def test_manual_remove_lap_deletes_from_storage(fresh_app):
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201

    # 2 manual laps
    for i in range(2):
        ts = f"2026-04-15T12:0{i}:00.000Z"
        client.post("/riders/TAG_A/laps", json={"timestamp": ts})

    before = app_module.storage.count_events()
    assert before == 2

    client.delete("/riders/TAG_A/laps")
    after = app_module.storage.count_events()
    assert after == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_manual_laps_survive_restart(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "manualdata")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    import app as app_module
    importlib.reload(app_module)
    _start_race(app_module)
    with TestClient(app_module.app) as client:
        _register(client, "TAG_A")
        for i in range(3):
            client.post("/riders/TAG_A/laps", json={
                "timestamp": f"2026-04-15T12:0{i}:00.000Z",
            })
        assert client.get("/classification").json()["standings"][0]["laps"] == 3

    # Reload
    import app as app_module2
    importlib.reload(app_module2)
    with TestClient(app_module2.app) as client2:
        after = client2.get("/classification").json()
        assert after["standings"][0]["laps"] == 3, (
            f"manual laps did not survive restart: {after}"
        )


# ---------------------------------------------------------------------------
# Interaction with finish state
# ---------------------------------------------------------------------------

def test_manual_add_lap_rejected_when_race_not_started(fresh_app):
    """Adversarial review (HIGH #18 / MEDIUM #4): manual lap before /race/start
    must return 409 (was: 201 with laps unchanged, persisting a phantom event)."""
    client, app_module = fresh_app
    # Do NOT start the race.
    assert _register(client, "TAG_A").status_code == 201
    r = client.post("/riders/TAG_A/laps", json={})
    assert r.status_code == 409
    # And nothing should have been persisted.
    assert app_module.storage.count_events() == 0


def test_manual_add_lap_rejected_when_race_ended(fresh_app):
    """Adversarial review (HIGH #18): manual lap after /race/end must return
    409. Otherwise the synthetic event sits in tag_events and on the next
    restart gets counted as a real lap (race.ended is False during replay)."""
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201
    # End the race
    assert client.post("/race/end").status_code in (200, 204)
    # Manual lap must now fail
    r = client.post("/riders/TAG_A/laps", json={
        "timestamp": "2026-04-15T12:00:00.000Z",
    })
    assert r.status_code == 409
    assert app_module.storage.count_events() == 0


def test_manual_remove_lap_rejected_when_race_ended(fresh_app):
    """Adversarial review (HIGH #2): -1 button on an ended race used to invoke
    _rebuild_active_race_state_in_place, which (because BUG-004 defers end())
    re-counted all post-end events as laps, inflating the standings instead of
    decrementing."""
    client, app_module = fresh_app
    _start_race(app_module)
    assert _register(client, "TAG_A").status_code == 201
    client.post("/riders/TAG_A/laps", json={"timestamp": "2026-04-15T12:00:00.000Z"})
    client.post("/riders/TAG_A/laps", json={"timestamp": "2026-04-15T12:01:00.000Z"})
    assert client.post("/race/end").status_code in (200, 204)

    r = client.delete("/riders/TAG_A/laps")
    assert r.status_code == 409


def test_manual_lap_can_trigger_finish(fresh_app, monkeypatch):
    """A manual lap that pushes a rider over total_laps must mark finished."""
    client, app_module = fresh_app
    # Force total_laps=3 so we can finish fast
    assert client.patch("/race", json={"total_laps": 3}).status_code == 200
    _start_race(app_module)
    _register(client, "TAG_A")
    for i in range(3):
        ts = f"2026-04-15T12:0{i}:00.000Z"
        r = client.post("/riders/TAG_A/laps", json={"timestamp": ts})
        assert r.status_code == 201
    body = r.json()
    assert body["laps"] == 3
    assert body["finished"] is True
    assert body["finish_time"] is not None
