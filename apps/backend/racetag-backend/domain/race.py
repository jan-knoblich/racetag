from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


# Defence for the "reader has no battery-backed RTC and reports year-1999
# timestamps" failure mode (W-030 + reader-service info.time push). We
# substitute server-now ONLY for timestamps that are obviously the reader's
# manufacturer epoch (year-2000 or earlier). A narrower guard than "older
# than race start by N seconds" so that legitimate replay/test fixtures with
# fixed past timestamps still flow through unchanged.
_IMPLAUSIBLE_BEFORE_YEAR = 2020


class Participant(BaseModel):
    tag_id: str
    laps: int = 0
    last_pass_time: Optional[str] = None
    finish_time: Optional[str] = None
    finished: bool = False
    total_time_ms: Optional[int] = None
    # Computed transient value (not persisted), time gap to leader in ms
    gap_ms: Optional[int] = None
    # Computed: laps behind the leader (0 if same lap)
    laps_behind: Optional[int] = None


class RaceState:
    def __init__(
        self,
        total_laps: int = 20,
        min_pass_interval_s: float = 8.0,
        race_id: Optional[str] = None,
    ) -> None:
        # Which persisted race this runtime state belongs to. Optional so
        # in-memory-only tests can construct a RaceState without storage.
        self.race_id = race_id
        self.total_laps = total_laps
        # Defence-in-depth (W-003 / P0-1): reject duplicate lap events that arrive
        # within this many seconds of the previous pass for the same tag. Also
        # the minimum elapsed time between race-start and any tag's first
        # counted lap — guards against tags sitting on an antenna at start.
        self.min_pass_interval_s = min_pass_interval_s
        self.start_time = datetime.now(timezone.utc)
        # Explicit-start model. Tag events are still ingested and persisted
        # before start (storage.append_event happens in the app layer), but
        # add_lap is a no-op until start() is called. started_at is the
        # authoritative anchor for the first-lap cooldown.
        self.started: bool = False
        self.started_at: Optional[datetime] = None
        # Explicit-end model (multi-race). After end() the race is frozen:
        # add_lap is a no-op so late/stray reads don't change standings, but
        # the event is still persisted upstream so the record is complete.
        self.ended: bool = False
        self.ended_at: Optional[datetime] = None
        self.participants: Dict[str, Participant] = {}

    def start(self, now: Optional[datetime] = None) -> datetime:
        """Mark the race as started. Idempotent: returns the existing started_at
        if already started, so accidental double-clicks don't reset the clock."""
        if self.started and self.started_at is not None:
            return self.started_at
        self.started_at = now or datetime.now(timezone.utc)
        self.started = True
        return self.started_at

    def end(self, now: Optional[datetime] = None) -> datetime:
        """Mark the race as ended. Idempotent. After this, add_lap is a no-op."""
        if self.ended and self.ended_at is not None:
            return self.ended_at
        self.ended_at = now or datetime.now(timezone.utc)
        self.ended = True
        return self.ended_at

    def add_lap(self, tag_id: str, pass_time_iso: str) -> Participant:
        """Add a lap pass. Increments laps and updates last_pass_time.

        Pre-start: the race must be started() before any lap counts. Calls
        before start() create the participant row if one doesn't exist (so the
        tag shows up in the registry) but laps stays at 0.

        Duplicate suppression (W-003): if `pass_time_iso` is within `min_pass_interval_s`
        of the participant's current `last_pass_time`, the call is a no-op and the
        unchanged Participant is returned. The same cooldown also applies between
        started_at and a tag's very first counted pass, so a tag sitting on the
        antenna at start can't register lap-1 immediately.

        If the participant crosses the finish threshold for the first time, marks finished and
        freezes finish_time/total_time_ms. Subsequent passes will keep laps and last_pass_time
        advancing, but standings/gaps are computed against the finish state.
        """
        # Defensive timestamp sanity check. If the upstream reader-service is
        # configured to push the host clock (sirit_client._maybe_bind_and_config)
        # this branch never fires. But on a reader whose clock failed to set —
        # e.g. the info.time push raced the first arrive event, or the user
        # ran a custom reader script — pass_time_iso can be year-1999 nonsense
        # (the Sirit's manufacturer epoch) that breaks the standings math.
        # Substitute server-now for anything before 2020 or unparseable.
        try:
            t_in = parse_iso(pass_time_iso)
            if t_in.year < _IMPLAUSIBLE_BEFORE_YEAR:
                pass_time_iso = _now_iso_utc()
        except (ValueError, TypeError):
            pass_time_iso = _now_iso_utc()

        p = self.participants.get(tag_id)
        if p is None:
            p = Participant(tag_id=tag_id)
            self.participants[tag_id] = p

        # Pre-start: don't count laps yet. Storage of the event has already
        # happened upstream so the read survives in the event log.
        if not self.started or self.started_at is None:
            return p

        # Post-end: race is frozen — late/stray reads don't change standings,
        # but the upstream event log keeps the record (storage.append_event ran
        # already in the app layer).
        if self.ended:
            return p

        # First-pass-after-start cooldown: at least min_pass_interval_s must
        # elapse between race start and a tag's first counted lap.
        if p.last_pass_time is None:
            delta_s = (
                parse_iso(pass_time_iso) - self.started_at
            ).total_seconds()
            if delta_s < self.min_pass_interval_s:
                return p

        # Cooldown check: suppress passes that arrive too soon after the last one.
        if p.last_pass_time is not None:
            delta_s = abs(
                (parse_iso(pass_time_iso) - parse_iso(p.last_pass_time)).total_seconds()
            )
            if delta_s < self.min_pass_interval_s:
                return p

        p.laps += 1
        p.last_pass_time = pass_time_iso
        if not p.finished and p.laps >= self.total_laps:
            p.finished = True
            p.finish_time = pass_time_iso
        t = parse_iso(p.finish_time or p.last_pass_time) if (p.finish_time or p.last_pass_time) else None
        if t is not None:
            p.total_time_ms = int((t - self.start_time).total_seconds() * 1000)
        return p

    def standings(self) -> List[Participant]:
        def _cap_laps(p: Participant) -> int:
            return min(p.laps, self.total_laps)

        # Sentinel for participants with no pass yet (pre-start, or zero-lap
        # rows created by the unknown-tag SSE flow). float('inf') would
        # OverflowError on int() cast — use a large finite ms value that
        # always sorts after real timestamps.
        _NO_PASS_TS_MS = 10**18

        def key(p: Participant) -> Tuple[int, int, int]:
            finished_flag = 1 if p.finished else 0
            ref = p.finish_time or p.last_pass_time
            tt_i = int(parse_iso(ref).timestamp() * 1000) if ref else _NO_PASS_TS_MS
            # Use capped laps for ordering to avoid post-finish extra passes affecting classification
            return (finished_flag, _cap_laps(p), -tt_i)

        arr = list(self.participants.values())
        arr.sort(key=key, reverse=True)

        # Compute gap vs. leader using reference times
        def ref_ms(p: Participant) -> Optional[int]:
            ref = p.finish_time or p.last_pass_time
            if not ref:
                return None
            return int(parse_iso(ref).timestamp() * 1000)

        leader = arr[0] if arr else None
        leader_ref = ref_ms(leader) if leader else None
        leader_laps_capped = _cap_laps(leader) if leader else 0
        for p in arr:
            rm = ref_ms(p)
            # Compute laps_behind using capped laps so extra passes after finish don't affect it
            cap = _cap_laps(p)
            p.laps_behind = max(leader_laps_capped - cap, 0) if leader else None
            if leader and p.laps_behind == 0 and leader_ref is not None and rm is not None:
                # Same lap (based on capped laps): positive gap = participant - leader (leader has 0)
                p.gap_ms = max(rm - leader_ref, 0)
            elif leader and p.laps_behind and p.laps_behind > 0:
                p.gap_ms = None
            else:
                p.gap_ms = None
        return arr
