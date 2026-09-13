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
    # Start race before posting events (pre-start events don't count laps).
    # Persist started_at to meta so the phase-2 reloaded module rehydrates
    # the started state — otherwise replay would re-apply events to a
    # not-started race and laps would stay at 0.
    from domain.race import parse_iso as _parse_iso
    started_at_iso = "2026-04-15T11:00:00.000Z"
    app_module.race.start(now=_parse_iso(started_at_iso))
    app_module.storage.set_meta("race_started_at", started_at_iso)
    with TestClient(app_module.app) as client:
        # BUG-003 fix: tags must be registered as riders before they count.
        for tag, bib in (("TAGA", "1"), ("TAGB", "2"), ("TAGC", "3")):
            client.post("/riders", json={"tag_id": tag, "bib": bib, "name": tag})

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


# ---------------------------------------------------------------------------
# BUG-004 regression: ended race rehydrates with non-empty standings
# ---------------------------------------------------------------------------

def test_ended_race_rehydrates_standings_after_restart(tmp_path, monkeypatch):
    """BUG-004: starting an ended race, posting events, ending it, then restarting
    the app must NOT leave the standings at zero laps.

    The bug was that _load_active_race_state called rs.end() BEFORE replaying
    persisted events. Since add_lap() is a no-op post-end, every replayed event
    was silently dropped and the standings rehydrated empty.
    """
    data_dir = str(tmp_path / "endeddata")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    # Phase 1: start race, register 2 riders, post 3 laps for one + 2 for the other, end race.
    app_module = _load_fresh_app(data_dir)
    # Start the race at a fixed past timestamp so the fixed event timestamps
    # land safely after started_at (otherwise the first-pass-after-start
    # cooldown would suppress them). Persist BOTH the meta key (for reload
    # compat) and the race row's started_at/started so the rehydrated race
    # has consistent state.
    from domain.race import parse_iso as _parse_iso
    started_at_iso = "2026-04-15T11:00:00.000Z"
    started_at_dt = _parse_iso(started_at_iso)
    app_module.race.start(now=started_at_dt)
    app_module.storage.set_meta("race_started_at", started_at_iso)
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True, started_at=started_at_dt
        )

    with TestClient(app_module.app) as client:
        for tag, bib in (("ENDED_A", "1"), ("ENDED_B", "2")):
            assert client.post(
                "/riders", json={"tag_id": tag, "bib": bib, "name": tag}
            ).status_code == 201

        # ENDED_A: 3 laps
        for lap in range(3):
            ts = f"2026-04-15T12:{lap:02d}:00.000Z"
            assert client.post(
                "/events/tag/batch", json=_batch([_tag_event("ENDED_A", ts)])
            ).status_code == 200
        # ENDED_B: 2 laps
        for lap in range(2):
            ts = f"2026-04-15T12:{lap:02d}:01.000Z"
            assert client.post(
                "/events/tag/batch", json=_batch([_tag_event("ENDED_B", ts)])
            ).status_code == 200

        baseline = {
            s["tag_id"]: s["laps"]
            for s in client.get("/classification").json()["standings"]
        }
        assert baseline == {"ENDED_A": 3, "ENDED_B": 2}

        # End the race so the race row is persisted as ended.
        assert client.post("/race/end").status_code in (200, 204)
        # Sanity: race row really is marked ended.
        race_resp = client.get("/race").json()
        assert race_resp["ended"] is True

    # Phase 2: reload app with the same data dir — the BUG-004 fix means the
    # replay rebuilds standings BEFORE the ended state is applied, so laps
    # survive.
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        after = {
            s["tag_id"]: s["laps"]
            for s in client2.get("/classification").json()["standings"]
        }
        assert after == baseline, (
            f"BUG-004 regression: ended race rehydrated with wrong standings: "
            f"{after} != {baseline}"
        )
        # The race must STILL be marked ended after restart (cosmetic but
        # important — operator should see "ended" not "live").
        race_resp = client2.get("/race").json()
        assert race_resp["ended"] is True, (
            "Race lost its ended state after restart"
        )


def test_post_end_events_do_not_count_on_replay(tmp_path, monkeypatch):
    """Adversarial-review finding (HIGH #1): the BUG-004 fix moved rs.end()
    after replay, but storage.append_event still persists post-end arrives
    for the audit trail. On the next restart, _replay_event used to run with
    race.ended=False and silently count those audit-only events as laps. The
    ended_cutoff fix in _replay_event filters them out.

    Repro: 2 pre-end laps, race ends, 3 more arrive events flow in (the
    reader has no knowledge that the race ended). Then reload. Standings
    must still be {tag: 2}, NOT {tag: 5}.
    """
    data_dir = str(tmp_path / "postenddata")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    app_module = _load_fresh_app(data_dir)
    from domain.race import parse_iso as _parse_iso
    started_at_iso = "2026-04-15T11:00:00.000Z"
    started_at_dt = _parse_iso(started_at_iso)
    app_module.race.start(now=started_at_dt)
    app_module.storage.set_meta("race_started_at", started_at_iso)
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True, started_at=started_at_dt
        )

    with TestClient(app_module.app) as client:
        assert client.post(
            "/riders", json={"tag_id": "PE_A", "bib": "1", "name": "PE_A"}
        ).status_code == 201

        # 2 pre-end laps
        for lap in range(2):
            ts = f"2026-04-15T12:{lap:02d}:00.000Z"
            assert client.post(
                "/events/tag/batch", json=_batch([_tag_event("PE_A", ts)])
            ).status_code == 200

        # End the race so the race row is persisted as ended.
        assert client.post("/race/end").status_code in (200, 204)
        # The end timestamp comes from server-now (real "now") — the test
        # timestamps below are intentionally in the past so they're "before"
        # in wall-clock order but the relevant comparison is against the
        # persisted ended_at. To exercise the replay cutoff properly we need
        # post-end events whose timestamps are AFTER the persisted ended_at.
        # ended_at is now ~2026-06-25T... (server time). Pick a stamp later
        # than that.
        post_end_ts = "2030-01-01T00:00:00.000Z"
        for i in range(3):
            ts = f"2030-01-01T00:{i:02d}:00.000Z"
            # In-session: add_lap is a no-op (race.ended=True), but
            # storage.append_event still persists for the audit trail.
            assert client.post(
                "/events/tag/batch", json=_batch([_tag_event("PE_A", ts)])
            ).status_code == 200

        baseline = client.get("/classification").json()["standings"]
        baseline_laps = {s["tag_id"]: s["laps"] for s in baseline}
        assert baseline_laps == {"PE_A": 2}

    # Reload — the cutoff filter must drop the 3 post-end events.
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        after = client2.get("/classification").json()["standings"]
        after_laps = {s["tag_id"]: s["laps"] for s in after}
        assert after_laps == {"PE_A": 2}, (
            f"HIGH #1 regression: post-end events counted as laps on replay. "
            f"Expected {{'PE_A': 2}} after restart, got {after_laps}"
        )


# ---------------------------------------------------------------------------
# AUDIT-2026-07 H3+H4 end-to-end: re-POSTed batches and out-of-order spool
# recovery must not change standings — live or after restart.
# ---------------------------------------------------------------------------

def test_reposted_batch_does_not_double_count(tmp_path, monkeypatch):
    """The reader-service re-POSTs a batch when the backend committed but died
    before responding. Same batch twice → one lap, one audit row."""
    data_dir = str(tmp_path / "repost")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    app_module = _load_fresh_app(data_dir)
    from domain.race import parse_iso as _parse_iso
    started_iso = "2026-04-15T11:00:00.000Z"
    app_module.race.start(now=_parse_iso(started_iso))
    app_module.storage.set_meta("race_started_at", started_iso)
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True, started_at=_parse_iso(started_iso)
        )

    with TestClient(app_module.app) as client:
        client.post("/riders", json={"tag_id": "RP_A", "bib": "1", "name": "RP_A"})
        batch = _batch([_tag_event("RP_A", "2026-04-15T12:00:00.000Z")])
        assert client.post("/events/tag/batch", json=batch).status_code == 200
        assert client.post("/events/tag/batch", json=batch).status_code == 200  # re-POST

        laps = {s["tag_id"]: s["laps"] for s in client.get("/classification").json()["standings"]}
        assert laps == {"RP_A": 1}, f"re-POST double-counted: {laps}"
        assert app_module.storage.count_events() == 1

    # And after restart (replay must agree)
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        laps2 = {s["tag_id"]: s["laps"] for s in client2.get("/classification").json()["standings"]}
        assert laps2 == {"RP_A": 1}


def test_out_of_order_spool_recovery_replays_correctly(tmp_path, monkeypatch):
    """Spool drain delivers an outage window AFTER newer live events. Live
    state suppresses the stale events (signed-delta cooldown); after restart
    the chronological replay must reach the same standings."""
    data_dir = str(tmp_path / "outoforder")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "8.0")

    app_module = _load_fresh_app(data_dir)
    from domain.race import parse_iso as _parse_iso
    started_iso = "2026-04-15T11:00:00.000Z"
    app_module.race.start(now=_parse_iso(started_iso))
    app_module.storage.set_meta("race_started_at", started_iso)
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True, started_at=_parse_iso(started_iso)
        )

    with TestClient(app_module.app) as client:
        client.post("/riders", json={"tag_id": "OO_A", "bib": "1", "name": "OO_A"})

        # Live events: laps at 12:00:00 and 12:01:00
        for ts in ("2026-04-15T12:00:00.000Z", "2026-04-15T12:01:00.000Z"):
            assert client.post(
                "/events/tag/batch", json=_batch([_tag_event("OO_A", ts)])
            ).status_code == 200

        # Late spool drain: an event from 12:00:30 (mid-window, distinct) —
        # arrives AFTER the 12:01:00 live event. It is within 8 s of nothing
        # (30 s gaps), but it is OLDER than last_pass_time → suppressed live.
        assert client.post(
            "/events/tag/batch",
            json=_batch([_tag_event("OO_A", "2026-04-15T12:00:30.000Z")]),
        ).status_code == 200

        live = {s["tag_id"]: s["laps"] for s in client.get("/classification").json()["standings"]}
        assert live == {"OO_A": 2}, f"stale spool event counted live: {live}"

    # After restart, chronological replay sees 12:00:00, 12:00:30, 12:01:00 —
    # all >8 s apart, so THREE laps is the chronologically-correct count.
    # (The mid-window pass was a real pass the reader captured during the
    # outage; the replay is allowed to know better than the live view.)
    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        after = {s["tag_id"]: s["laps"] for s in client2.get("/classification").json()["standings"]}
        assert after == {"OO_A": 3}, (
            f"chronological replay wrong: {after} (expected 3 laps: "
            f"12:00:00 / 12:00:30 / 12:01:00 all beyond the 8 s cooldown)"
        )


def test_rider_status_survives_restart(tmp_path, monkeypatch):
    """F3: a DNF set before restart must rehydrate into the RaceState so
    standings still sort it below finishers after a reload."""
    data_dir = str(tmp_path / "statusdata")
    monkeypatch.setenv("RACETAG_DATA_DIR", data_dir)
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")

    app_module = _load_fresh_app(data_dir)
    from domain.race import parse_iso as _parse_iso
    app_module.race.start(now=_parse_iso("2026-04-15T11:00:00.000Z"))
    app_module.storage.set_meta("race_started_at", "2026-04-15T11:00:00.000Z")
    if app_module.race.race_id:
        app_module.storage.update_race(
            app_module.race.race_id, started=True,
            started_at=_parse_iso("2026-04-15T11:00:00.000Z"),
        )
    with TestClient(app_module.app) as client:
        client.post("/riders", json={"tag_id": "DNF1", "bib": "1", "name": "Q"})
        client.patch("/riders/DNF1/status", json={"status": "dnf"})

    app_module2 = _load_fresh_app(data_dir)
    with TestClient(app_module2.app) as client2:
        # Rider status rehydrated
        assert client2.get("/riders/DNF1").json()["status"] == "dnf"
        # And mirrored into RaceState
        assert app_module2.race.status.get("DNF1") == "dnf"
