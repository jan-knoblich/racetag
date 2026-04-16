"""Tests for W-050: Storage class (SQLite durability layer).

Covers pragma assertions, rider round-trip, and event append/iter.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage import Storage
from domain.riders import Rider
from models_api import EventType, TagEventDTO


def _make_rider(tag_id: str = "AABB0001", bib: str = "1", name: str = "Alice") -> Rider:
    return Rider(tag_id=tag_id, bib=bib, name=name, created_at=datetime.now(timezone.utc))


def _make_event(
    tag_id: str = "AABB0001",
    ts: str = "2026-04-15T12:00:00.000Z",
    antenna: int = 1,
    rssi: int = -55,
) -> TagEventDTO:
    return TagEventDTO(
        source="test",
        reader_ip="127.0.0.1",
        reader_serial="SN001",
        timestamp=ts,
        event_type=EventType.arrive,
        tag_id=tag_id,
        antenna=antenna,
        rssi=rssi,
    )


# ---------------------------------------------------------------------------
# Pragma tests
# ---------------------------------------------------------------------------

def test_pragmas_are_full_and_wal(tmp_path):
    """PRAGMA synchronous must return 2 (FULL) and journal_mode must be WAL."""
    db = Storage(tmp_path / "test.db")
    try:
        sync_row = db._conn.execute("PRAGMA synchronous;").fetchone()
        assert sync_row[0] == 2, f"Expected synchronous=2 (FULL), got {sync_row[0]}"

        jm_row = db._conn.execute("PRAGMA journal_mode;").fetchone()
        assert jm_row[0].lower() == "wal", f"Expected journal_mode=wal, got {jm_row[0]}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Rider round-trip tests
# ---------------------------------------------------------------------------

def test_rider_roundtrip(tmp_path):
    """Upsert a rider, open a new Storage instance, list should return the rider."""
    db_path = tmp_path / "riders.db"

    # First instance: write
    db1 = Storage(db_path)
    rider = _make_rider("RIDER001", bib="7", name="Bob")
    db1.upsert_rider(rider)
    db1.close()

    # Second instance: read back (simulates restart)
    db2 = Storage(db_path)
    try:
        riders = db2.list_riders()
        assert len(riders) == 1
        r = riders[0]
        assert r.tag_id == "RIDER001"
        assert r.bib == "7"
        assert r.name == "Bob"
    finally:
        db2.close()


def test_rider_upsert_overwrites(tmp_path):
    """Upserting the same tag_id twice updates the row."""
    db = Storage(tmp_path / "test.db")
    try:
        r1 = _make_rider("TAG001", bib="10", name="Original")
        db.upsert_rider(r1)

        r2 = _make_rider("TAG001", bib="99", name="Updated")
        db.upsert_rider(r2)

        riders = db.list_riders()
        assert len(riders) == 1
        assert riders[0].bib == "99"
        assert riders[0].name == "Updated"
    finally:
        db.close()


def test_rider_delete(tmp_path):
    """delete_rider returns True when found, False when missing."""
    db = Storage(tmp_path / "test.db")
    try:
        db.upsert_rider(_make_rider("DEL001"))
        assert db.delete_rider("DEL001") is True
        assert db.list_riders() == []
        assert db.delete_rider("DEL001") is False
    finally:
        db.close()


def test_rider_get(tmp_path):
    """get_rider returns the rider when present, None when missing."""
    db = Storage(tmp_path / "test.db")
    try:
        assert db.get_rider("MISS") is None
        db.upsert_rider(_make_rider("FIND001", bib="5", name="Eve"))
        r = db.get_rider("FIND001")
        assert r is not None
        assert r.name == "Eve"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tag-event tests
# ---------------------------------------------------------------------------

def test_event_append_and_iter(tmp_path):
    """Append 3 events; iter_events returns them in insertion order."""
    db = Storage(tmp_path / "events.db")
    try:
        events = [
            _make_event("TAG001", "2026-04-15T12:00:00.000Z"),
            _make_event("TAG002", "2026-04-15T12:00:15.000Z"),
            _make_event("TAG001", "2026-04-15T12:00:30.000Z"),
        ]
        for ev in events:
            db.append_event(ev)

        replayed = list(db.iter_events())
        assert len(replayed) == 3
        assert replayed[0].tag_id == "TAG001"
        assert replayed[1].tag_id == "TAG002"
        assert replayed[2].tag_id == "TAG001"
        # Timestamps preserved
        assert replayed[0].timestamp == "2026-04-15T12:00:00.000Z"
        assert replayed[1].timestamp == "2026-04-15T12:00:15.000Z"
    finally:
        db.close()


def test_count_events(tmp_path):
    """count_events returns the correct row count."""
    db = Storage(tmp_path / "cnt.db")
    try:
        assert db.count_events() == 0
        db.append_event(_make_event("T1", "2026-04-15T12:00:00.000Z"))
        db.append_event(_make_event("T2", "2026-04-15T12:00:15.000Z"))
        assert db.count_events() == 2
    finally:
        db.close()


def test_event_optional_fields_null(tmp_path):
    """Events with no antenna/rssi/reader_serial round-trip without error."""
    db = Storage(tmp_path / "nulls.db")
    try:
        ev = TagEventDTO(
            source="test",
            reader_ip="127.0.0.1",
            timestamp="2026-04-15T12:00:00.000Z",
            event_type=EventType.arrive,
            tag_id="NULLTAG",
        )
        db.append_event(ev)
        replayed = list(db.iter_events())
        assert len(replayed) == 1
        assert replayed[0].tag_id == "NULLTAG"
        assert replayed[0].antenna is None
        assert replayed[0].rssi is None
        assert replayed[0].reader_serial is None
    finally:
        db.close()
