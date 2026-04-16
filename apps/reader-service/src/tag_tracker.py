from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Set


@dataclass
class TagTracker:
    """Tracks RFID tag presence across multiple antennas and enforces a minimum lap interval.

    Per-antenna presence (W-001): a tag is considered "present" only while at least one
    antenna still sees it.  This prevents a depart from antenna-1 from clearing presence
    while antenna-2 still has the tag in view, which was the root cause of P0-1.

    Minimum lap interval cooldown (W-002): even if per-antenna gating lets a new "arrive"
    through, we suppress it if less than `min_lap_interval_s` seconds have elapsed since
    the last emitted arrive for that tag.
    """

    # tag_id (upper) -> set of antenna ids currently seeing the tag
    present: Dict[str, Set[int]] = field(default_factory=dict)

    # Seen-tag registry for new-tag detection (unchanged from original)
    seen: Set[str] = field(default_factory=set)

    # Per-tag monotonic timestamp of last emitted arrive
    last_emitted_at: Dict[str, float] = field(default_factory=dict)

    # Minimum seconds between two emitted arrives for the same tag
    min_lap_interval_s: float = 10.0

    # Injectable clock (defaults to time.monotonic; override in tests)
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_seen(self, tag_hex: str) -> bool:
        """Return True the first time this tag is seen (ever)."""
        key = tag_hex.upper()
        if key not in self.seen:
            self.seen.add(key)
            return True
        return False

    def mark_present(self, tag_hex: str, antenna: int) -> bool:
        """Register `antenna` as currently seeing `tag_hex`.

        Returns True iff:
        - the per-tag antenna set transitioned from empty → non-empty (genuine new arrive), AND
        - the minimum lap interval has elapsed since the last emitted arrive for this tag.

        When True is returned, `last_emitted_at[tag]` is updated to the current clock value.
        """
        key = tag_hex.upper()
        antennas = self.present.setdefault(key, set())
        was_empty = len(antennas) == 0
        antennas.add(antenna)

        if not was_empty:
            # Tag was already present on another antenna — not a new lap event.
            return False

        # Tag just became visible (set went from empty to non-empty).
        # Apply the cooldown check.
        now = self.clock()
        last = self.last_emitted_at.get(key, -math.inf)
        if now - last < self.min_lap_interval_s:
            # Within cooldown window — suppress.
            return False

        # Genuine new lap: record emission time and signal the caller.
        self.last_emitted_at[key] = now
        return True

    def mark_absent(self, tag_hex: str, antenna: int) -> bool:
        """Remove `antenna` from the set of antennas seeing `tag_hex`.

        Returns True iff the antenna set became empty (tag has fully departed).
        """
        key = tag_hex.upper()
        antennas = self.present.get(key)
        if antennas is None:
            return True  # already empty — treat as departed
        antennas.discard(antenna)
        if len(antennas) == 0:
            del self.present[key]
            return True
        return False
