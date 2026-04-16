from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TagTracker:
    seen: set = field(default_factory=set)
    present: set = field(default_factory=set)

    def record_seen(self, tag_hex: str) -> bool:
        tag_hex = tag_hex.upper()
        if tag_hex not in self.seen:
            self.seen.add(tag_hex)
            return True
        return False

    def mark_present(self, tag_hex: str) -> bool:
        key = tag_hex.upper()
        if key in self.present:
            return False
        self.present.add(key)
        return True

    def mark_absent(self, tag_hex: str):
        key = tag_hex.upper()
        if key in self.present:
            self.present.remove(key)
