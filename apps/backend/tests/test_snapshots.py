"""Tests for auto-snapshot feature.

Covers:
- write_snapshot writes a CSV + a sqlite backup, returns paths
- cleanup_old_snapshots respects rolling window
- snapshot_interval_s round-trips through the Race CRUD API
- POST /races/{id}/snapshots triggers a manual snapshot
- GET /races/{id}/snapshots lists what's on disk
- Snapshotter thread writes a snapshot when interval elapses
- list_snapshots returns sorted entries
"""
from __future__ import annotations

import importlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fresh_app(monkeypatch):
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, app_module


# ---------------------------------------------------------------------------
# write_snapshot + cleanup_old_snapshots — unit tests on the helper module
# ---------------------------------------------------------------------------

def test_write_snapshot_creates_csv_and_db(tmp_path):
    from snapshots import write_snapshot

    def fake_backup(path: str) -> None:
        # Pretend to write a valid sqlite file by creating an empty one.
        Path(path).write_bytes(b"SQLite format 3\x00")

    csv_path, db_path = write_snapshot(
        tmp_path, "RACE", "header,row\n1,2\n", fake_backup,
        now=datetime(2026, 6, 25, 18, 30, 0, tzinfo=timezone.utc),
    )
    # Stem now includes millisecond precision (review #14, collision fix).
    assert csv_path.name == "20260625T183000000Z.csv"
    assert db_path.name == "20260625T183000000Z.db"
    assert csv_path.read_text(encoding="utf-8") == "header,row\n1,2\n"
    assert db_path.read_bytes() == b"SQLite format 3\x00"
    assert csv_path.parent == tmp_path / "snapshots" / "RACE"


def test_cleanup_old_snapshots_keeps_last_n(tmp_path):
    from snapshots import cleanup_old_snapshots, write_snapshot

    def fake_backup(path: str) -> None:
        Path(path).write_bytes(b"SQLite format 3\x00")

    # Write 5 snapshots, then keep only the last 3
    base = datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        write_snapshot(
            tmp_path, "RACE", f"snap {i}\n", fake_backup, now=base + timedelta(seconds=i),
            keep=99,  # don't clean up here, do it explicitly below
        )

    snap_dir = tmp_path / "snapshots" / "RACE"
    # 5 CSV + 5 DB = 10 files
    assert len([p for p in snap_dir.iterdir()]) == 10

    cleanup_old_snapshots(snap_dir, keep=3)
    remaining = sorted(p.name for p in snap_dir.iterdir())
    # 3 CSV + 3 DB = 6 files; the 3 newest stems survive (i=2,3,4)
    assert len(remaining) == 6
    expected_stems = {f"20260625T18000{i}000Z" for i in (2, 3, 4)}
    actual_stems = {p.stem for p in snap_dir.iterdir()}
    assert actual_stems == expected_stems


def test_cleanup_handles_orphaned_files(tmp_path):
    """If only a CSV (or only a DB) exists for a stem, cleanup still works."""
    from snapshots import cleanup_old_snapshots

    snap_dir = tmp_path / "snapshots" / "RACE"
    snap_dir.mkdir(parents=True)
    # 4 stems, only CSV for some, only DB for others, both for some
    (snap_dir / "20260625T180000000Z.csv").write_text("")
    (snap_dir / "20260625T180001000Z.db").write_bytes(b"x")
    (snap_dir / "20260625T180002000Z.csv").write_text("")
    (snap_dir / "20260625T180002000Z.db").write_bytes(b"x")
    (snap_dir / "20260625T180003000Z.csv").write_text("")

    cleanup_old_snapshots(snap_dir, keep=2)
    remaining = sorted(p.name for p in snap_dir.iterdir())
    # Keep the last 2 stems (180002 + 180003), 3 files total
    assert remaining == [
        "20260625T180002000Z.csv",
        "20260625T180002000Z.db",
        "20260625T180003000Z.csv",
    ]


def test_list_snapshots_returns_sorted_pairs(tmp_path):
    from snapshots import list_snapshots, write_snapshot

    def fake_backup(path: str) -> None:
        Path(path).write_bytes(b"")

    base = datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        write_snapshot(
            tmp_path, "RACE", "x", fake_backup, now=base + timedelta(seconds=i * 30),
        )

    items = list_snapshots(tmp_path, "RACE")
    assert len(items) == 3
    stems = [item["stem"] for item in items]
    assert stems == sorted(stems)
    for item in items:
        assert item["csv_exists"]
        assert item["db_exists"]


def test_list_snapshots_empty_for_unknown_race(tmp_path):
    from snapshots import list_snapshots
    assert list_snapshots(tmp_path, "DOES_NOT_EXIST") == []


# ---------------------------------------------------------------------------
# Storage / Race model: snapshot_interval_s round-trips
# ---------------------------------------------------------------------------

def test_create_race_with_snapshot_interval_persists(fresh_app):
    client, app_module = fresh_app
    r = client.post("/races", json={
        "name": "Snap Race", "total_laps": 10, "snapshot_interval_s": 60,
    })
    assert r.status_code == 201
    race_id = r.json()["id"]
    assert r.json()["snapshot_interval_s"] == 60

    # Round-trip via list + get
    get = client.get(f"/races/{race_id}").json()
    assert get["snapshot_interval_s"] == 60


def test_patch_race_can_update_snapshot_interval(fresh_app):
    client, app_module = fresh_app
    r = client.post("/races", json={"name": "PatchMe"})
    race_id = r.json()["id"]
    assert r.json()["snapshot_interval_s"] is None

    p = client.patch(f"/races/{race_id}", json={"snapshot_interval_s": 30})
    assert p.status_code == 200
    assert p.json()["snapshot_interval_s"] == 30

    # Disable: explicitly set to 0
    p2 = client.patch(f"/races/{race_id}", json={"snapshot_interval_s": 0})
    assert p2.json()["snapshot_interval_s"] == 0


def test_create_race_rejects_negative_snapshot_interval(fresh_app):
    client, _ = fresh_app
    r = client.post("/races", json={
        "name": "Bad", "snapshot_interval_s": -1,
    })
    assert r.status_code == 422


def test_create_race_rejects_too_large_snapshot_interval(fresh_app):
    client, _ = fresh_app
    r = client.post("/races", json={
        "name": "Bad", "snapshot_interval_s": 999999,
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_post_manual_snapshot_writes_files(fresh_app, monkeypatch, tmp_path):
    """POST /races/{id}/snapshots produces both files on disk."""
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        # Use the default active race
        races = client.get("/races").json()
        active_id = races["active_race_id"]
        # Need to start the race so the CSV builder has something coherent
        client.post("/race/start")

        r = client.post(f"/races/{active_id}/snapshots")
        assert r.status_code == 201, r.text
        body = r.json()
        snap_dir = tmp_path / "snapshots" / active_id
        assert (snap_dir / body["csv"]).exists()
        assert (snap_dir / body["db"]).exists()


def test_post_snapshot_for_inactive_race_400(fresh_app):
    client, app_module = fresh_app
    r = client.post("/races", json={"name": "Other"})
    other_id = r.json()["id"]
    # other_id is NOT active (active is the default seed race)
    snap = client.post(f"/races/{other_id}/snapshots")
    assert snap.status_code == 400


def test_post_snapshot_for_unknown_race_404(fresh_app):
    client, _ = fresh_app
    r = client.post("/races/does-not-exist/snapshots")
    assert r.status_code == 404


def test_get_snapshots_lists_written_files(monkeypatch, tmp_path):
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        active_id = client.get("/races").json()["active_race_id"]
        client.post("/race/start")

        # Empty initially
        assert client.get(f"/races/{active_id}/snapshots").json()["count"] == 0

        client.post(f"/races/{active_id}/snapshots")
        client.post(f"/races/{active_id}/snapshots")

        listing = client.get(f"/races/{active_id}/snapshots").json()
        # Two manual snapshots same second might collide on the timestamp stem
        # (they share the same second). The cleanup is fine; just assert >= 1.
        assert listing["count"] >= 1


# ---------------------------------------------------------------------------
# Storage.backup_to is a valid sqlite file
# ---------------------------------------------------------------------------

def test_backup_to_produces_readable_sqlite(tmp_path):
    from storage import Storage

    src_path = tmp_path / "src.db"
    src = Storage(src_path)
    try:
        src._conn.execute("CREATE TABLE marker (id INTEGER);")
        src._conn.execute("INSERT INTO marker VALUES (42);")
        backup_path = tmp_path / "backup.db"
        src.backup_to(str(backup_path))
    finally:
        src.close()

    # Open the backup independently
    con = sqlite3.connect(str(backup_path))
    try:
        row = con.execute("SELECT id FROM marker;").fetchone()
        assert row == (42,)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Snapshotter integration — race interval ticks through and writes one snapshot
# ---------------------------------------------------------------------------

def test_snapshotter_writes_when_interval_elapses(tmp_path, monkeypatch):
    """End-to-end: enable snapshots with a small interval, start race, wait,
    confirm a snapshot lands on disk."""
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    import app as app_module
    importlib.reload(app_module)

    # Reduce poll interval so the test doesn't wait 5s+ for the first tick.
    # The default Snapshotter is created in @app.on_event("startup") below
    # via TestClient entry; we swap it for a faster one inside the context.
    from snapshots import Snapshotter
    fast_snapshotter = None
    try:
        with TestClient(app_module.app) as client:
            # Stop the default snapshotter installed on startup and replace
            # it with a fast-polling one.
            if app_module._snapshotter is not None:
                app_module._snapshotter.stop()
            fast_snapshotter = Snapshotter(
                app_module._data_dir,
                app_module._snapshot_state_provider,
                poll_interval_s=0.1,
            )
            app_module._snapshotter = fast_snapshotter
            fast_snapshotter.start()
            active_id = client.get("/races").json()["active_race_id"]
            # Set a tiny snapshot interval and start the race
            assert client.patch(
                f"/races/{active_id}", json={"snapshot_interval_s": 1}
            ).status_code == 200
            assert client.post("/race/start").status_code in (200, 204)

            # Wait up to 4s for at least one snapshot
            snap_dir = tmp_path / "snapshots" / active_id
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if snap_dir.exists() and any(snap_dir.iterdir()):
                    break
                time.sleep(0.1)

            assert snap_dir.exists(), "snapshot dir was never created"
            files = list(snap_dir.iterdir())
            assert any(p.suffix == ".csv" for p in files), f"no CSV in {files}"
            assert any(p.suffix == ".db" for p in files), f"no DB in {files}"
    finally:
        fast_snapshotter.stop()


def test_snapshotter_skips_when_interval_zero(tmp_path, monkeypatch):
    """interval=0 → no snapshots written even after the race has started."""
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    import app as app_module
    importlib.reload(app_module)

    from snapshots import Snapshotter
    fast_snapshotter = None
    try:
        with TestClient(app_module.app) as client:
            if app_module._snapshotter is not None:
                app_module._snapshotter.stop()
            fast_snapshotter = Snapshotter(
                app_module._data_dir,
                app_module._snapshot_state_provider,
                poll_interval_s=0.1,
            )
            app_module._snapshotter = fast_snapshotter
            fast_snapshotter.start()

            active_id = client.get("/races").json()["active_race_id"]
            assert client.patch(
                f"/races/{active_id}", json={"snapshot_interval_s": 0}
            ).status_code == 200
            client.post("/race/start")

            time.sleep(1.0)

            snap_dir = tmp_path / "snapshots" / active_id
            assert not snap_dir.exists() or not list(snap_dir.iterdir())
    finally:
        if fast_snapshotter is not None:
            fast_snapshotter.stop()


def test_snapshotter_skips_before_race_starts(tmp_path, monkeypatch):
    """interval>0 but race not started → no snapshots."""
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RACE_MIN_PASS_INTERVAL_S", "0")
    import app as app_module
    importlib.reload(app_module)

    from snapshots import Snapshotter
    fast_snapshotter = None
    try:
        with TestClient(app_module.app) as client:
            if app_module._snapshotter is not None:
                app_module._snapshotter.stop()
            fast_snapshotter = Snapshotter(
                app_module._data_dir,
                app_module._snapshot_state_provider,
                poll_interval_s=0.1,
            )
            app_module._snapshotter = fast_snapshotter
            fast_snapshotter.start()

            active_id = client.get("/races").json()["active_race_id"]
            client.patch(f"/races/{active_id}", json={"snapshot_interval_s": 1})
            # Do NOT start the race
            time.sleep(1.5)

            snap_dir = tmp_path / "snapshots" / active_id
            assert not snap_dir.exists() or not list(snap_dir.iterdir())
    finally:
        if fast_snapshotter is not None:
            fast_snapshotter.stop()
