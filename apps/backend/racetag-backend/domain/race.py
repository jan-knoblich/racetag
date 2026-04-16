from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


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
    def __init__(self, total_laps: int = 20) -> None:
        self.total_laps = total_laps
        self.start_time = datetime.now(timezone.utc)
        self.participants: Dict[str, Participant] = {}

    def add_lap(self, tag_id: str, pass_time_iso: str) -> Participant:
        """Add a lap pass. Always increments laps and updates last_pass_time.

        If the participant crosses the finish threshold for the first time, marks finished and
        freezes finish_time/total_time_ms. Subsequent passes will keep laps and last_pass_time
        advancing, but standings/gaps are computed against the finish state.
        """
        p = self.participants.get(tag_id)
        if p is None:
            p = Participant(tag_id=tag_id)
            self.participants[tag_id] = p
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

        def key(p: Participant) -> Tuple[int, int, int]:
            finished_flag = 1 if p.finished else 0
            ref = p.finish_time or p.last_pass_time
            tt = parse_iso(ref).timestamp() if ref else float("inf")
            tt_i = int(tt * 1000)
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
