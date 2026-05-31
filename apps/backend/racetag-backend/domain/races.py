"""Race entity + in-memory cache backed by Storage.

Multi-race model (introduced 2026-05-25 for the 24.6. two-race event day):
- A Race is a persistent record with its own name, schedule, total_laps, and
  started/ended state.
- Riders and tag events are scoped to a race via `race_id`.
- The application operates on a single "active" race at a time (its id lives
  in the meta table as `active_race_id`); the reader-service feeds events
  into the active race.

The RaceState runtime object (in `race.py`) is per-race and reads its
configuration from the Race record held here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def new_race_id() -> str:
    """Generate a fresh race id (uuid4 hex, stable + URL-safe)."""
    return uuid.uuid4().hex


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Race(BaseModel):
    """Persistent race record. Mirrors the `races` SQLite table 1:1."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=new_race_id)
    name: str
    scheduled_at: Optional[datetime] = None
    total_laps: int = 5
    started: bool = False
    started_at: Optional[datetime] = None
    ended: bool = False
    ended_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now_utc)


__all__ = ["Race", "new_race_id"]
