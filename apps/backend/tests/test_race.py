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
    """A year-1999 pass_time gets rewritten to ~now; total_time_ms stays
    positive. total_time_ms is now anchored to started_at (BUG-005 fix), so
    its absolute size depends on (now - started_at) — what we really test is
    that it's not negative or garbage from a 1999 substitution leaking through."""
    race = _started_race(total_laps=5, min_pass_interval_s=0.0)

    race.add_lap("OLDREADER", "1999-11-29T23:03:20.000Z")

    p = race.participants["OLDREADER"]
    assert p.laps == 1
    assert p.last_pass_time is not None
    # The 1999 timestamp must NOT have leaked through verbatim.
    assert not p.last_pass_time.startswith("1999")
    assert p.total_time_ms is not None
    # Positive (would be huge negative if a 1999 timestamp were used as-is
    # against a 2026 started_at).
    assert p.total_time_ms >= 0


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


# ---------------------------------------------------------------------------
# BUG-005: total_time_ms must be measured from started_at (race start),
# not from self.start_time (app boot / RaceState construction).
# ---------------------------------------------------------------------------

def test_total_time_ms_is_anchored_to_started_at_not_app_boot():
    """If the app has been up for 1 h before "Start race" is clicked, a pass
    20 s after start must report total_time_ms ~= 20_000, not ~= 3_620_000."""
    race = RaceState(total_laps=5, min_pass_interval_s=0.0)
    # Simulate: app booted at noon (self.start_time was assigned ~now in
    # __init__), operator clicks "Start race" 1 h later.
    start_at = "2026-04-15T13:00:00.000Z"
    race.start(now=parse_iso(start_at))

    # Pass arrives 20 s after race start
    race.add_lap("RIDER1", iso(20, base=start_at))

    p = race.participants["RIDER1"]
    assert p.total_time_ms is not None
    # 20 s after start_at → 20_000 ms (give or take a millisecond for parsing)
    assert 19_900 <= p.total_time_ms <= 20_100, (
        f"total_time_ms should be ~20_000 (since race start), got {p.total_time_ms}. "
        f"If this is in the millions, the bug is back — likely anchored to "
        f"self.start_time (app boot) instead of self.started_at (race start)."
    )


# ---------------------------------------------------------------------------
# AUDIT-2026-07 H3: signed-delta cooldown — out-of-order / duplicate events
# must never rewind last_pass_time or over-count laps.
# ---------------------------------------------------------------------------

def test_add_lap_out_of_order_old_event_is_suppressed():
    """t0, t1=t0+20s, then a re-delivered t0 duplicate: with the old abs()
    cooldown the stale t0 counted as lap 3 and rewound last_pass_time.
    Signed delta must keep laps at 2 and last_pass_time at t1."""
    race = _started_race(total_laps=10, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(20))
    race.add_lap("TAG001", iso(0))  # duplicate of the first pass, re-POSTed

    p = race.participants["TAG001"]
    assert p.laps == 2, f"stale duplicate counted as a lap: laps={p.laps}"
    assert p.last_pass_time == iso(20), (
        f"last_pass_time was rewound to the stale timestamp: {p.last_pass_time}"
    )


def test_add_lap_exact_duplicate_timestamp_is_suppressed():
    """The same event delivered twice (crash-during-commit re-POST)."""
    race = _started_race(total_laps=10, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(0))

    assert race.participants["TAG001"].laps == 1


def test_add_lap_older_event_between_passes_is_suppressed():
    """An out-of-order event that lands BETWEEN two counted passes (e.g. a
    spool drain delivering the outage window late) must not count."""
    race = _started_race(total_laps=10, min_pass_interval_s=8.0)

    race.add_lap("TAG001", iso(0))
    race.add_lap("TAG001", iso(40))
    race.add_lap("TAG001", iso(15))  # late-arriving mid-window event

    p = race.participants["TAG001"]
    assert p.laps == 2
    assert p.last_pass_time == iso(40)


def test_add_lap_total_time_never_negative_from_stale_timestamp():
    """With abs(), a stale pre-start-adjacent event could rewind
    total_time_ms below zero. Signed delta suppresses the event entirely."""
    started_at = "2026-04-15T12:00:00.000Z"
    race = RaceState(total_laps=10, min_pass_interval_s=8.0)
    race.start(now=parse_iso(started_at))

    race.add_lap("TAG001", iso(30, base=started_at))
    race.add_lap("TAG001", iso(60, base=started_at))
    # Stale duplicate from before the first pass
    race.add_lap("TAG001", iso(30, base=started_at))

    p = race.participants["TAG001"]
    assert p.laps == 2
    assert p.total_time_ms is not None and p.total_time_ms >= 0
    assert p.total_time_ms == 60_000


# ---------------------------------------------------------------------------
# AUDIT-2026-07 F1: leader-finishes-race (criterium / Abwink model).
# ---------------------------------------------------------------------------

def _leader_race(total_laps=3, min_pass_interval_s=0.0,
                 started_at="2026-04-15T11:58:20.000Z"):
    r = RaceState(total_laps=total_laps, min_pass_interval_s=min_pass_interval_s,
                  finish_mode="leader")
    r.start(now=parse_iso(started_at))
    return r


def test_leader_reaching_target_triggers_finishing_phase():
    r = _leader_race(total_laps=3)
    # LEAD does 3 laps, others fewer
    for i in range(3):
        r.add_lap("LEAD", iso(i * 20))
    assert r.participants["LEAD"].finished is True
    assert r.finishing is True
    assert r.finishing_at is not None


def test_other_riders_finished_at_next_pass_after_leader():
    r = _leader_race(total_laps=3)
    # CHASER is on lap 2 when leader finishes
    r.add_lap("CHASER", iso(0))
    r.add_lap("CHASER", iso(20))
    for i in range(3):
        r.add_lap("LEAD", iso(i * 20 + 5))
    assert r.finishing is True
    assert r.participants["CHASER"].finished is False  # not yet — no pass since

    # CHASER's next pass flags them off in their current lap (3, not total)
    r.add_lap("CHASER", iso(80))
    c = r.participants["CHASER"]
    assert c.finished is True
    assert c.laps == 3  # they were on lap 2, this pass makes 3 — flagged in lap 3
    assert c.finish_time == iso(80)


def test_leader_mode_lapped_rider_finished_with_laps_behind():
    r = _leader_race(total_laps=5)
    # Leader completes 5, lapped rider only managed 3 by then
    for i in range(3):
        r.add_lap("SLOW", iso(i * 30))
    for i in range(5):
        r.add_lap("LEAD", iso(i * 10 + 1))
    assert r.finishing
    # SLOW crosses once more → finished on lap 4
    r.add_lap("SLOW", iso(200))
    standings = r.standings()
    slow = next(p for p in standings if p.tag_id == "SLOW")
    lead = next(p for p in standings if p.tag_id == "LEAD")
    assert lead.laps == 5 and lead.finished
    assert slow.finished and slow.laps == 4
    assert slow.laps_behind == 1  # one lap down on the 5-lap leader


def test_per_rider_mode_each_finishes_independently():
    r = RaceState(total_laps=3, min_pass_interval_s=0.0, finish_mode="per_rider")
    r.start(now=parse_iso("2026-04-15T11:58:20.000Z"))
    for i in range(3):
        r.add_lap("A", iso(i * 20))
    assert r.participants["A"].finished
    assert r.finishing is False  # per_rider never triggers finishing phase
    # B is still going and only finishes on its own 3rd lap
    r.add_lap("B", iso(5))
    assert r.participants["B"].finished is False


def test_laps_to_go_reports_remaining_for_leader():
    r = _leader_race(total_laps=5)
    assert r.laps_to_go() == 5
    r.add_lap("LEAD", iso(0))
    assert r.laps_to_go() == 4
    r.add_lap("LEAD", iso(20))
    assert r.laps_to_go() == 3


# ---------------------------------------------------------------------------
# AUDIT-2026-07 F2: time-based race ("duration + final laps").
# ---------------------------------------------------------------------------

def test_time_based_race_locks_target_when_timer_expires():
    started = "2026-04-15T12:00:00.000Z"
    r = RaceState(total_laps=999, min_pass_interval_s=0.0,
                  finish_mode="leader", duration_s=100, final_laps=2)
    r.start(now=parse_iso(started))

    # Before the timer expires, no target and nobody finishes even at high laps
    for i in range(4):
        r.add_lap("LEAD", iso(i * 20, base=started))  # passes at 0,20,40,60 s
    assert r.time_target_laps is None
    assert r.laps_to_go() is None  # timer hasn't rung yet
    assert r.participants["LEAD"].finished is False

    # A pass after 100 s locks target = leader_laps(4) + final_laps(2) = 6
    r.add_lap("LEAD", iso(110, base=started))  # lap 5, timer expired
    assert r.time_target_laps == 5 + 2  # leader now on lap 5, +2 to go
    assert r.laps_to_go() == 2


def test_time_based_race_leader_finishes_after_final_laps():
    started = "2026-04-15T12:00:00.000Z"
    r = RaceState(total_laps=999, min_pass_interval_s=0.0,
                  finish_mode="leader", duration_s=50, final_laps=1)
    r.start(now=parse_iso(started))

    r.add_lap("LEAD", iso(20, base=started))   # lap 1, timer not expired
    r.add_lap("LEAD", iso(60, base=started))   # lap 2, timer expired → target=2+1=3
    assert r.time_target_laps == 3
    assert not r.participants["LEAD"].finished
    r.add_lap("LEAD", iso(90, base=started))   # lap 3 == target → finished
    assert r.participants["LEAD"].finished
    assert r.finishing


# ---------------------------------------------------------------------------
# AUDIT-2026-07 F3: DNF/DNS/DSQ status model.
# ---------------------------------------------------------------------------

def test_status_riders_sort_below_all_finishers():
    r = _leader_race(total_laps=5)
    # Two racing riders + one who will be DNF
    for i in range(3):
        r.add_lap("A", iso(i * 20))
    for i in range(2):
        r.add_lap("B", iso(i * 20 + 5))
    for i in range(4):
        r.add_lap("DROP", iso(i * 15 + 2))  # DROP has the MOST laps
    r.set_status("DROP", "dnf")

    standings = r.standings()
    order = [p.tag_id for p in standings]
    # DROP led on laps but is DNF → must be last despite highest lap count
    assert order[-1] == "DROP"
    assert order[:2] == ["A", "B"] or order[:2] == ["A", "B"]
    drop = next(p for p in standings if p.tag_id == "DROP")
    assert drop.status == "dnf"


def test_dnf_rider_does_not_anchor_gap_column():
    r = _leader_race(total_laps=10)
    # DROP has most laps but is DNF; A is the real leader
    for i in range(5):
        r.add_lap("DROP", iso(i * 10))
    for i in range(3):
        r.add_lap("A", iso(i * 10 + 3))
    r.set_status("DROP", "dnf")
    standings = r.standings()
    a = next(p for p in standings if p.tag_id == "A")
    # A is the top classified rider → laps_behind 0 (leader), gap 0
    assert a.laps_behind == 0
    assert a.gap_ms == 0


def test_status_ordering_dnf_before_dsq_before_dns():
    r = _leader_race(total_laps=5)
    r.add_lap("FIN", iso(0))
    for tag, st in (("X", "dns"), ("Y", "dsq"), ("Z", "dnf")):
        r.add_lap(tag, iso(1))
        r.set_status(tag, st)
    order = [p.tag_id for p in r.standings()]
    # FIN classified first, then dnf(Z), dsq(Y), dns(X)
    assert order == ["FIN", "Z", "Y", "X"]


def test_set_status_invalid_raises():
    r = _leader_race()
    r.add_lap("A", iso(0))
    try:
        r.set_status("A", "quit")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_clear_status_reclassifies_rider():
    r = _leader_race(total_laps=5)
    r.add_lap("A", iso(0))
    r.set_status("A", "dnf")
    assert r.standings()[0].status == "dnf"
    r.set_status("A", None)
    assert r.standings()[0].status is None


# ---------------------------------------------------------------------------
# AUDIT-2026-07 F5: missed-read detection (annotation only, never mutates laps).
# ---------------------------------------------------------------------------

def test_suspected_missed_read_flags_double_length_lap():
    from domain.race import suspected_missed_reads
    # Median lap ~30 s; one lap is ~60 s (a missed read).
    base = "2026-04-15T12:00:00.000Z"
    times = [iso(t, base=base) for t in (0, 30, 60, 90, 150, 180, 210)]
    #                                              ^^^ 90→150 is a 60 s gap
    missed, mid = suspected_missed_reads(times)
    assert missed == 1
    assert mid is not None  # midpoint of the 90→150 gap for the +1 dialog


def test_suspected_missed_read_quiet_on_regular_laps():
    from domain.race import suspected_missed_reads
    base = "2026-04-15T12:00:00.000Z"
    times = [iso(t, base=base) for t in (0, 30, 61, 89, 121, 150)]
    missed, mid = suspected_missed_reads(times)
    assert missed == 0
    assert mid is None


def test_suspected_missed_read_needs_enough_laps():
    from domain.race import suspected_missed_reads
    base = "2026-04-15T12:00:00.000Z"
    # Only 2 intervals — not enough to trust a median.
    times = [iso(t, base=base) for t in (0, 30, 200)]
    missed, _ = suspected_missed_reads(times)
    assert missed == 0


def test_missed_read_annotation_does_not_change_laps():
    r = _leader_race(total_laps=99, min_pass_interval_s=0.0)
    base = "2026-04-15T12:00:00.000Z"
    for t in (0, 30, 60, 90, 150, 180, 210):  # 90→150 is a missed read
        r.add_lap("A", iso(t, base=base))
    standings = r.standings()
    a = next(p for p in standings if p.tag_id == "A")
    assert a.laps == 7  # exactly the passes seen — NOT auto-corrected to 8
    assert a.suspected_missed_reads == 1
    assert a.suspected_gap_midpoint is not None


def test_dns_rider_without_laps_still_appears_in_standings():
    """A rider marked DNS who never crossed the line must still show in the
    result (flagged, at the bottom) — not silently vanish."""
    r = _leader_race(total_laps=5)
    r.add_lap("RACER", iso(0))
    r.set_status("GHOST", "dns")  # never had a lap
    standings = r.standings()
    tags = [p.tag_id for p in standings]
    assert "GHOST" in tags
    ghost = next(p for p in standings if p.tag_id == "GHOST")
    assert ghost.status == "dns"
    assert ghost.laps == 0
    assert tags[-1] == "GHOST"  # sorts last


# ---------------------------------------------------------------------------
# RECHECK-2026-07-25 #3: pauses are not missed reads.
# ---------------------------------------------------------------------------

def test_long_pause_is_not_flagged_as_missed_reads():
    """A 10× median gap (rider stopped at the pits) must NOT flag — the old
    behaviour reported '⚠×N' for every training break."""
    from domain.race import suspected_missed_reads
    base = "2026-04-15T12:00:00.000Z"
    # Regular 30 s laps, then a 300 s pit stop, then more regular laps.
    times = [iso(t, base=base) for t in (0, 30, 60, 90, 390, 420, 450)]
    missed, mid = suspected_missed_reads(times)
    assert missed == 0
    assert mid is None


def test_double_lap_still_flagged_next_to_pause():
    """A genuine ~2× median missed read still flags even when a pause exists
    elsewhere in the history."""
    from domain.race import suspected_missed_reads
    base = "2026-04-15T12:00:00.000Z"
    # 30 s laps, one 60 s gap (missed read), one 400 s pause.
    times = [iso(t, base=base) for t in (0, 30, 90, 120, 150, 550, 580, 610)]
    missed, mid = suspected_missed_reads(times)
    assert missed == 1  # only the 30→90 gap; the 150→550 pause is ignored
    assert mid is not None


# ---------------------------------------------------------------------------
# RECHECK-2026-07-25 #5: net time (TT / staggered starts).
# ---------------------------------------------------------------------------

def test_net_time_is_first_to_finish_pass():
    """TT setup: rider rolls over the line at start (read 1) and finish
    (read 2, total_laps=2) — net time = read2 − read1."""
    r = RaceState(total_laps=2, min_pass_interval_s=0.0, finish_mode="per_rider")
    r.start(now=parse_iso("2026-04-15T07:50:00.000Z"))
    start_ts = "2026-04-15T08:00:00.000Z"
    finish_ts = "2026-04-15T08:09:30.000Z"
    r.add_lap("TT1", start_ts)
    r.add_lap("TT1", finish_ts)
    p = r.standings()[0]
    assert p.finished
    assert p.net_time_ms == 9 * 60 * 1000 + 30 * 1000  # 9:30


def test_net_time_ignores_cooldown_crossing_after_finish():
    """A third crossing (rolling out) must not stretch net time — finish_time
    is frozen."""
    r = RaceState(total_laps=2, min_pass_interval_s=0.0, finish_mode="per_rider")
    r.start(now=parse_iso("2026-04-15T07:50:00.000Z"))
    r.add_lap("TT1", "2026-04-15T08:00:00.000Z")
    r.add_lap("TT1", "2026-04-15T08:10:00.000Z")   # finish
    r.add_lap("TT1", "2026-04-15T08:20:00.000Z")   # cool-down crossing
    p = r.standings()[0]
    assert p.net_time_ms == 10 * 60 * 1000  # still 10:00, not 20:00


def test_net_time_none_with_single_pass():
    race = RaceState(total_laps=5, min_pass_interval_s=0.0)
    race.start(now=parse_iso("2026-04-15T07:50:00.000Z"))
    race.add_lap("SOLO", "2026-04-15T08:00:00.000Z")
    p = race.standings()[0]
    assert p.net_time_ms is None
