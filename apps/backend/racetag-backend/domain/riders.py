from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from storage import Storage


class Rider(BaseModel):
    """A rider coupled to a specific RFID tag.

    tag_id is the primary key (uppercase hex, matches TagEventDTO.tag_id).
    """

    tag_id: str
    bib: str
    name: str
    created_at: datetime


class RiderStore:
    """Rider store backed by an optional SQLite Storage.

    When `storage` is provided (W-050), all mutations are written-through to
    the database and reads are served from an in-memory cache populated at
    construction time.  Without storage the store is purely in-memory
    (existing behaviour, existing tests continue to pass unchanged).

    Thread-safety note: CPython GIL makes individual dict operations atomic,
    but Storage._execute acquires its own lock for write operations.
    """

    def __init__(self, storage: Optional[Storage] = None) -> None:
        self._storage: Optional[Storage] = storage
        self._riders: dict[str, Rider] = {}

        # Pre-populate cache from persistent storage on startup.
        if storage is not None:
            for rider in storage.list_riders():
                self._riders[rider.tag_id] = rider

    def upsert(self, rider: Rider) -> Rider:
        """Insert or overwrite a rider keyed by tag_id. Returns the stored rider."""
        if self._storage is not None:
            self._storage.upsert_rider(rider)
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
        if tag_id not in self._riders:
            return False
        if self._storage is not None:
            self._storage.delete_rider(tag_id)
        del self._riders[tag_id]
        return True

    def __contains__(self, tag_id: str) -> bool:
        return tag_id in self._riders
