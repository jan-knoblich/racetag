"""W-074: Config model and ConfigStore for persistent app settings.

Settings are stored as string values in the meta table (already created by
W-036).  ConfigStore wraps get_meta/set_meta with typed accessors.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from storage import Storage

# Simple IPv4 pattern: four dotted octets (0–255 each).
_IPV4_RE = re.compile(
    r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


def is_valid_ipv4(value: str) -> bool:
    return bool(_IPV4_RE.match(value))


class Config(BaseModel):
    """Effective application configuration — all fields optional (nullable)."""

    reader_ip: Optional[str] = None
    min_lap_interval_s: Optional[float] = None
    total_laps: Optional[int] = None


class ConfigStore:
    """Read/write typed config values via the Storage meta table."""

    _KEY_READER_IP = "reader_ip"
    _KEY_MIN_LAP = "min_lap_interval_s"
    _KEY_TOTAL_LAPS = "total_laps"

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def get_reader_ip(self) -> Optional[str]:
        return self._storage.get_meta(self._KEY_READER_IP)

    def get_min_lap_interval_s(self) -> Optional[float]:
        raw = self._storage.get_meta(self._KEY_MIN_LAP)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def get_total_laps(self) -> Optional[int]:
        raw = self._storage.get_meta(self._KEY_TOTAL_LAPS)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def set_reader_ip(self, value: str) -> None:
        self._storage.set_meta(self._KEY_READER_IP, value)

    def set_min_lap_interval_s(self, value: float) -> None:
        self._storage.set_meta(self._KEY_MIN_LAP, str(value))

    def set_total_laps(self, value: int) -> None:
        self._storage.set_meta(self._KEY_TOTAL_LAPS, str(value))
