"""Unit tests for RaceState.add_lap — covers W-003 (defence-in-depth duplicate suppression).

Tests use ISO-8601 timestamps with Z suffix, matching the production format.
"""
from __future__ import annotations

import pytest

from domain.race import RaceState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso(seconds_offset: float, base: str = "2026-04-15T12:00:00.000Z") -> str:
    """Return a UTC ISO-8601 timestamp that is `seconds_offset` seconds after `base`."""
    from domain.race import parse_iso
    from datetime import timedelta

    dt = parse_iso(base) + timedelta(seconds=seconds_offset)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# W-003: minimum pass interval tests
# ---------------------------------------------------------------------------

def test_add_lap_ignores_duplicates_inside_min_interval():
    """Two add_lap calls 3 s apart with min_pass_interval_s=8 → laps == 1."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    t0 = iso(0)
    t1 = iso(3)  # 3 s later — within the 8 s window

    race.add_lap("TAG001", t0)
    race.add_lap("TAG001", t1)

    assert race.participants["TAG001"].laps == 1


def test_add_lap_counts_after_min_interval():
    """Two add_lap calls 12 s apart → laps == 2."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    t0 = iso(0)
    t1 = iso(12)  # 12 s later — past the 8 s window

    race.add_lap("TAG001", t0)
    race.add_lap("TAG001", t1)

    assert race.participants["TAG001"].laps == 2


def test_add_lap_boundary_exact_interval_allowed():
    """A pass exactly at min_pass_interval_s is allowed (condition is strictly <).
    delta == min_pass_interval_s is NOT < min_pass_interval_s, so it counts."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    t0 = iso(0)
    t1 = iso(8.0)  # exactly 8 s — equals the window, NOT less than it → allowed

    race.add_lap("TAG001", t0)
    race.add_lap("TAG001", t1)

    assert race.participants["TAG001"].laps == 2


def test_add_lap_multiple_tags_independent():
    """Each tag has its own cooldown; a duplicate for one tag does not affect another."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    t0 = iso(0)
    t1 = iso(3)

    race.add_lap("TAG001", t0)
    race.add_lap("TAG002", t0)

    # Duplicate for TAG001 — suppressed
    race.add_lap("TAG001", t1)

    # Fresh pass for TAG002 (also 3 s later, also suppressed)
    race.add_lap("TAG002", t1)

    assert race.participants["TAG001"].laps == 1
    assert race.participants["TAG002"].laps == 1


def test_add_lap_first_pass_always_counts():
    """The very first pass for a tag (no previous last_pass_time) must always count."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("NEWTAG", iso(0))

    assert race.participants["NEWTAG"].laps == 1


def test_add_lap_finish_on_total_laps():
    """Finishing: laps reaches total_laps → finished flag set and finish_time recorded."""
    race = RaceState(total_laps=3, min_pass_interval_s=0.0)  # disable cooldown

    for i in range(3):
        race.add_lap("RIDER1", iso(i * 30))

    p = race.participants["RIDER1"]
    assert p.laps == 3
    assert p.finished is True
    assert p.finish_time is not None


def test_add_lap_z_suffix_timestamps():
    """Timestamps with Z suffix are parsed correctly (no ValueError)."""
    race = RaceState(total_laps=5, min_pass_interval_s=8.0)

    race.add_lap("TAGZ01", "2026-04-15T10:00:00.000Z")
    race.add_lap("TAGZ01", "2026-04-15T10:00:15.000Z")  # 15 s later — allowed

    assert race.participants["TAGZ01"].laps == 2
