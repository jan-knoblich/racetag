"""Unit tests for RaceState.add_lap — covers W-003 (defence-in-depth duplicate
suppression), the year-1999-RTC defensive guard, and the explicit-start model.

Tests use ISO-8601 timestamps with Z suffix, matching the production format.
"""
from __future__ import annotations

import pytest

from domain.race import RaceState, _now_iso_utc, parse_iso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso(seconds_offset: float, base: str = "2026-04-15T12:00:00.000Z") -> str:
    """Return a UTC ISO-8601 timestamp `seconds_offset` seconds after `base`."""
    from datetime import timedelta

    dt = parse_iso(base) + timedelta(seconds=seconds_offset)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _started_race(total_laps: int = 5, min_pass_interval_s: float = 8.0,
                  started_at: str = "2026-04-15T11:58:20.000Z") -> RaceState:
    """RaceState that's already started.

    Default `started_at` is 100 s before the iso(0) base, so any pass at
    iso(0)+ passes the first-lap cooldown without further setup.
    """
    race = RaceState(total_laps=total_laps, min_pass_interval_s=min_pass_interval_s)
    race.start(now=parse_iso(started_at))
    return race


# ---------------------------------------------------------------------------
# W-003: minimum pass interval tests
# ---------------------------------------------------------------------------

def test_add_lap_ignores_duplicates_inside_min_interval():
    """Two add_lap calls 3 s apart with min_pass_interval_s=8 → laps == 1."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(3))  # 3 s later — within the 8 s window

    assert race.participants["TAG001"].laps == 1


def test_add_lap_counts_after_min_interval():
    """Two add_lap calls 12 s apart → laps == 2."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(12))  # 12 s later — past the 8 s window

    assert race.participants["TAG001"].laps == 2


def test_add_lap_boundary_exact_interval_allowed():
    """A pass exactly at min_pass_interval_s is allowed (strictly <)."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(8.0))  # exactly 8 s — allowed

    assert race.participants["TAG001"].laps == 2


def test_add_lap_multiple_tags_independent():
    """Each tag has its own cooldown; a duplicate for one tag doesn't affect another."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG002", iso(0))

    race.add_lap("TAG001", iso(3))  # duplicate — suppressed
    race.add_lap("TAG002", iso(3))  # duplicate — suppressed

    assert race.participants["TAG001"].laps == 1
    assert race.participants["TAG002"].laps == 1


def test_add_lap_first_pass_always_counts():
    """The first pass for a tag (no last_pass_time) counts once the race is started
    AND the first-lap-after-start cooldown is satisfied."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("NEWTAG", iso(0))

    assert race.participants["NEWTAG"].laps == 1


def test_add_lap_finish_on_total_laps():
    """Finishing: laps reaches total_laps → finished flag set and finish_time recorded."""
    race = _started_race(total_laps=3, min_pass_interval_s=0.0)

    for i in range(3):
        race.add_lap("RIDER1", iso(i * 30))

    p = race.participants["RIDER1"]
    assert p.laps == 3
    assert p.finished is True
    assert p.finish_time is not None


def test_add_lap_z_suffix_timestamps():
    """Timestamps with Z suffix are parsed correctly (no ValueError)."""
    race = _started_race(total_laps=5, min_pass_interval_s=8.0,
                         started_at="2026-04-15T09:58:20.000Z")

    race.add_lap("TAGZ01", "2026-04-15T10:00:00.000Z")
    race.add_lap("TAGZ01", "2026-04-15T10:00:15.000Z")  # 15 s later — allowed

    assert race.participants["TAGZ01"].laps == 2


# ---------------------------------------------------------------------------
# Defensive guard for implausibly-old reader timestamps.
# ---------------------------------------------------------------------------

def test_add_lap_replaces_year_1999_timestamp_with_server_now():
    """A year-1999 pass_time gets rewritten to ~now; total_time_ms stays positive."""
    race = _started_race(total_laps=5, min_pass_interval_s=0.0)

    race.add_lap("OLDREADER", "1999-11-29T23:03:20.000Z")

    p = race.participants["OLDREADER"]
    assert p.laps == 1
    assert p.last_pass_time is not None
    assert not p.last_pass_time.startswith("1999")
    assert p.total_time_ms is not None
    assert p.total_time_ms >= 0
    # total_time_ms is "synthetic-now minus race.start_time" — both very close to now,
    # within a second.
    assert p.total_time_ms < 5_000


def test_add_lap_keeps_realistic_timestamp_unmodified():
    """Sane recent timestamps are passed through unchanged."""
    race = _started_race(total_laps=5, min_pass_interval_s=0.0)

    realistic = _now_iso_utc()
    race.add_lap("OK_TAG", realistic)

    assert race.participants["OK_TAG"].last_pass_time == realistic


def test_add_lap_handles_garbage_timestamp_string():
    """A non-ISO timestamp string is replaced by server-now rather than crashing."""
    race = _started_race(total_laps=5, min_pass_interval_s=0.0)

    race.add_lap("GARBAGE", "this is not a date")

    p = race.participants["GARBAGE"]
    assert p.laps == 1
    assert p.last_pass_time is not None
    parse_iso(p.last_pass_time)  # must not raise


# ---------------------------------------------------------------------------
# Explicit-start model — race must be started() before laps count.
# ---------------------------------------------------------------------------

def test_add_lap_before_start_does_not_count():
    """Events delivered before race.start() create the participant row but
    laps stays at 0. (Storage of the raw event happens upstream in app.py
    regardless of race state.)"""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAG_BEFORE", iso(0))
    race.add_lap("TAG_BEFORE", iso(30))

    p = race.participants["TAG_BEFORE"]
    assert p.laps == 0
    assert p.last_pass_time is None
    assert race.started is False
    assert race.started_at is None


def test_add_lap_after_start_counts_normally():
    """Once started(), add_lap behaves as before for any pass after the
    first-lap cooldown."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)
    race.start(now=parse_iso("2026-04-15T11:58:20.000Z"))  # 100 s before iso(0)

    race.add_lap("TAG_AFTER", iso(0))  # 100 s after start — passes cooldown

    assert race.participants["TAG_AFTER"].laps == 1
    assert race.started is True


def test_first_lap_cooldown_after_start_blocks_tag_sitting_on_antenna():
    """A tag that's on the antenna at start time tries to register a lap
    within milliseconds of started_at. It must be blocked until
    min_pass_interval_s has elapsed since started_at."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)
    race.start(now=parse_iso(iso(0)))

    # Pass arrives 1 s after start — within the cooldown window
    race.add_lap("STICKY", iso(1))
    assert race.participants["STICKY"].laps == 0

    # Pass arrives 10 s after start — past the cooldown
    race.add_lap("STICKY", iso(10))
    assert race.participants["STICKY"].laps == 1


def test_start_is_idempotent():
    """Calling start() twice doesn't change started_at — protects against
    double-clicks on the UI Start button."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)
    first = race.start(now=parse_iso("2026-04-15T12:00:00.000Z"))
    second = race.start(now=parse_iso("2026-04-15T13:00:00.000Z"))

    assert first == second
    assert race.started_at == first


def test_start_returns_started_at():
    """start() returns the canonical started_at datetime."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)
    t = parse_iso("2026-04-15T12:00:00.000Z")

    returned = race.start(now=t)

    assert returned == t
    assert race.started_at == t
