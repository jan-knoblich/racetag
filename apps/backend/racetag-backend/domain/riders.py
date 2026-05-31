from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from storage import Storage


class Rider(BaseModel):
    """A rider coupled to a specific RFID tag, scoped to a race.

    Uniqueness is `(race_id, tag_id)` in the persistent layer — the same
    physical tag can map to different bibs/names in different races.
    """

    tag_id: str
    bib: str
    name: str
    created_at: datetime


class RiderStore:
    """In-memory cache of riders for ONE race, backed by an optional Storage.

    Multi-race (2026-05-25): each RiderStore corresponds to a single race
    (identified by ``race_id``). The app constructs a RiderStore for the
    active race and rebuilds it when the active race changes.

    All Storage calls are scoped by the stored ``race_id`` — never by the
    Storage's notion of "active race" — so a RiderStore for race A continues
    to write to race A even if the active race is switched mid-flight.

    Without ``storage`` the store is purely in-memory (legacy tests).
    """

    def __init__(
        self,
        storage: Optional[Storage] = None,
        race_id: Optional[str] = None,
    ) -> None:
        self._storage: Optional[Storage] = storage
        # When storage is provided, race_id is required for race-scoped queries.
        # Fall back to the storage's active race id for backward compat with
        # callers that haven't been migrated yet.
        if storage is not None and race_id is None:
            race_id = storage.get_active_race_id()
        self._race_id: Optional[str] = race_id
        self._riders: dict[str, Rider] = {}

        if storage is not None and race_id is not None:
            for rider in storage.list_riders(race_id=race_id):
                self._riders[rider.tag_id] = rider

    @property
    def race_id(self) -> Optional[str]:
        return self._race_id

    def upsert(self, rider: Rider) -> Rider:
        """Insert or overwrite a rider keyed by tag_id, scoped to this race."""
        if self._storage is not None:
            self._storage.upsert_rider(rider, race_id=self._race_id)
        self._riders[rider.tag_id] = rider
        return rider

    def get(self, tag_id: str) -> Optional[Rider]:
        return self._riders.get(tag_id)

    def list(self) -> List[Rider]:
        return list(self._riders.values())

    def delete(self, tag_id: str) -> bool:
        if tag_id not in self._riders:
            return False
        if self._storage is not None:
            self._storage.delete_rider(tag_id, race_id=self._race_id)
        del self._riders[tag_id]
        return True

    def __contains__(self, tag_id: str) -> bool:
        return tag_id in self._riders
