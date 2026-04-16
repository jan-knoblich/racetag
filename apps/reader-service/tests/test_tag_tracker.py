"""Unit tests for TagTracker — covers W-001 (per-antenna presence) and W-002 (cooldown).

All tests use a simple fake-clock callable to avoid real-time waits and any
third-party freezegun dependency.
"""
from __future__ import annotations

from tag_tracker import TagTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Monotonic fake clock that starts at 0.0 and can be advanced manually."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


def make_tracker(min_lap_interval_s: float = 10.0, clock_start: float = 0.0) -> tuple[TagTracker, FakeClock]:
    clock = FakeClock(clock_start)
    tracker = TagTracker(min_lap_interval_s=min_lap_interval_s, clock=clock)
    return tracker, clock


# ---------------------------------------------------------------------------
# W-001: per-antenna presence tests
# ---------------------------------------------------------------------------

def test_tag_tracker_multi_antenna():
    """arrive ant=1, arrive ant=2, depart ant=1, depart ant=2 → exactly one emitted
    arrive and one emitted depart."""
    tracker, clock = make_tracker()

    # ant=1 arrive: set goes {} → {1}, past cooldown → should emit
    assert tracker.mark_present("AABBCC", 1) is True

    # ant=2 arrive: set goes {1} → {1,2}, not empty→non-empty transition → no emit
    assert tracker.mark_present("AABBCC", 2) is False

    # ant=1 depart: set {1,2} → {2}, still non-empty → no emit
    assert tracker.mark_absent("AABBCC", 1) is False

    # ant=2 depart: set {2} → {}, became empty → emit
    assert tracker.mark_absent("AABBCC", 2) is True


def test_tag_tracker_overlap_race():
    """The exact race-condition scenario from P0-1:
    arrive ant=1 → depart ant=1 → arrive ant=2 → depart ant=2

    The second arrive must NOT emit because the tag's lap was already counted
    (via the cooldown: arrival of ant=2 comes within the cooldown window after ant=1).
    """
    tracker, clock = make_tracker(min_lap_interval_s=10.0, clock_start=0.0)

    # t=0: ant=1 arrive — first time: emits
    assert tracker.mark_present("DDEEFF", 1) is True

    # t=0.41: ant=1 depart — set becomes empty → emits depart signal
    clock.advance(0.41)
    assert tracker.mark_absent("DDEEFF", 1) is True

    # t=0.47: ant=2 arrive — set goes {} → {2}, but within 10 s cooldown → NO emit
    clock.advance(0.06)
    assert tracker.mark_present("DDEEFF", 2) is False

    # ant=2 depart
    assert tracker.mark_absent("DDEEFF", 2) is True


def test_single_antenna_single_pass():
    """Baseline: one arrive, one depart → one of each emitted."""
    tracker, clock = make_tracker()
    assert tracker.mark_present("112233", 1) is True
    assert tracker.mark_absent("112233", 1) is True


# ---------------------------------------------------------------------------
# W-002: minimum lap interval cooldown tests
# ---------------------------------------------------------------------------

def test_tag_tracker_cooldown():
    """Two arrives for the same tag 3 s apart with min_lap_interval_s=10 → exactly
    one emitted arrive.  Advance past 10 s and arrive again → two total."""
    tracker, clock = make_tracker(min_lap_interval_s=10.0, clock_start=0.0)

    # First arrive at t=0 — emits
    assert tracker.mark_present("FFEE00", 1) is True

    # Depart so presence is cleared (makes the next arrive a fresh transition)
    assert tracker.mark_absent("FFEE00", 1) is True

    # Second arrive 3 s later — within 10 s cooldown → suppressed
    clock.advance(3.0)
    assert tracker.mark_present("FFEE00", 1) is False

    # Depart again
    tracker.mark_absent("FFEE00", 1)

    # Third arrive at t=11 — past cooldown → emits
    clock.advance(8.0)  # total 11 s from first emit
    assert tracker.mark_present("FFEE00", 1) is True


def test_cooldown_boundary_exact():
    """Boundary semantics: condition is `delta < min_lap_interval_s`.
    At exactly min_lap_interval_s the arrive IS allowed; just under it is suppressed."""
    tracker, clock = make_tracker(min_lap_interval_s=10.0, clock_start=0.0)

    assert tracker.mark_present("AA0011", 1) is True
    tracker.mark_absent("AA0011", 1)

    # Advance to just under the interval — suppressed
    clock.advance(9.999)
    assert tracker.mark_present("AA0011", 1) is False

    tracker.mark_absent("AA0011", 1)

    # Advance 1 ms more — now at exactly 10.0 s elapsed → allowed (10.0 is NOT < 10.0)
    clock.advance(0.001)
    assert tracker.mark_present("AA0011", 1) is True


def test_cooldown_independent_per_tag():
    """Cooldown is per-tag: one tag's cooldown does not affect another."""
    tracker, clock = make_tracker(min_lap_interval_s=10.0, clock_start=0.0)

    assert tracker.mark_present("TAG001", 1) is True
    tracker.mark_absent("TAG001", 1)

    # A different tag at t=1 — its own cooldown starts now
    clock.advance(1.0)
    assert tracker.mark_present("TAG002", 1) is True
    tracker.mark_absent("TAG002", 1)

    # TAG001 at t=2 — within its 10 s cooldown → suppressed
    clock.advance(1.0)
    assert tracker.mark_present("TAG001", 1) is False

    # TAG002 at t=2 — also within its 10 s cooldown → suppressed
    assert tracker.mark_present("TAG002", 1) is False


def test_record_seen_first_time_only():
    """record_seen returns True only on first encounter."""
    tracker, _ = make_tracker()
    assert tracker.record_seen("AABB01") is True
    assert tracker.record_seen("AABB01") is False
    assert tracker.record_seen("AABB02") is True
