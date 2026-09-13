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
    # Antenna conducted power in 0.1 dBm units (300 = 30 dBm max). The
    # reader-service receives it in every heartbeat reply and reconnects with
    # the new value when it changes.
    antenna_power: Optional[int] = None
    # First-run assistant finished or skipped. Persisted in the meta table
    # because the desktop webview wipes localStorage on every launch
    # (private mode + random port).
    assistant_done: bool = False
    # Read-only, derived per request (never persisted, ignored in PATCH):
    # True when the desktop shell registered its reader controller, i.e. the
    # UI runs inside the packaged app and can hide the backend URL controls.
    desktop: bool = False
    # App version from RACETAG_VERSION (set by the desktop shell), else None.
    version: Optional[str] = None


class ConfigStore:
    """Read/write typed config values via the Storage meta table."""

    _KEY_READER_IP = "reader_ip"
    _KEY_MIN_LAP = "min_lap_interval_s"
    _KEY_TOTAL_LAPS = "total_laps"
    _KEY_ANTENNA_POWER = "antenna_power"
    _KEY_ASSISTANT_DONE = "assistant_done"

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

    def get_antenna_power(self) -> Optional[int]:
        raw = self._storage.get_meta(self._KEY_ANTENNA_POWER)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def get_assistant_done(self) -> bool:
        raw = self._storage.get_meta(self._KEY_ASSISTANT_DONE)
        return raw is not None and raw.strip().lower() in ("1", "true")

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def set_reader_ip(self, value: str) -> None:
        self._storage.set_meta(self._KEY_READER_IP, value)

    def clear_reader_ip(self) -> None:
        self._storage.delete_meta(self._KEY_READER_IP)

    def set_min_lap_interval_s(self, value: float) -> None:
        self._storage.set_meta(self._KEY_MIN_LAP, str(value))

    def set_total_laps(self, value: int) -> None:
        self._storage.set_meta(self._KEY_TOTAL_LAPS, str(value))

    def set_antenna_power(self, value: int) -> None:
        self._storage.set_meta(self._KEY_ANTENNA_POWER, str(value))

    def set_assistant_done(self, value: bool) -> None:
        self._storage.set_meta(self._KEY_ASSISTANT_DONE, "true" if value else "false")
