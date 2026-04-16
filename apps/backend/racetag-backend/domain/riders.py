from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel


class Rider(BaseModel):
    """A rider coupled to a specific RFID tag.

    tag_id is the primary key (uppercase hex, matches TagEventDTO.tag_id).
    """

    tag_id: str
    bib: str
    name: str
    created_at: datetime


class RiderStore:
    """In-memory store for Rider entities.

    Thread-safety note: CPython GIL makes individual dict operations atomic, but
    callers that iterate and mutate concurrently should hold their own lock.
    Persistence will be added in W-050 (SQLite).
    """

    def __init__(self) -> None:
        self._riders: dict[str, Rider] = {}

    def upsert(self, rider: Rider) -> Rider:
        """Insert or overwrite a rider keyed by tag_id. Returns the stored rider."""
        self._riders[rider.tag_id] = rider
        return rider

    def get(self, tag_id: str) -> Optional[Rider]:
        """Return the rider for tag_id, or None if not registered."""
        return self._riders.get(tag_id)

    def list(self) -> List[Rider]:
        """Return all riders in insertion/key order."""
        return list(self._riders.values())

    def delete(self, tag_id: str) -> bool:
        """Remove rider by tag_id. Returns True if a record was removed, False if not found."""
        if tag_id in self._riders:
            del self._riders[tag_id]
            return True
        return False

    def __contains__(self, tag_id: str) -> bool:
        return tag_id in self._riders
