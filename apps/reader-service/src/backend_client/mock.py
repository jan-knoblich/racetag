from __future__ import annotations

from typing import List

from models import TagEvent
from utils import _ts
from .base import BackendClient


class MockBackendClient(BackendClient):
    """Mock backend client that just logs events locally.

    Useful for testing the reader pipeline without an actual backend service.
    """

    def __init__(self) -> None:
        self._events: List[TagEvent] = []

    def start(self) -> None:
        print(f"[{_ts()}] [BACKEND] Mock client started")

    def stop(self) -> None:
        print(f"[{_ts()}] [BACKEND] Mock client stopped (total events: {len(self._events)})")

    def send(self, event: TagEvent) -> None:
        self._events.append(event)
        print(f"[{_ts()}] [BACKEND][MOCK] {event.event_type.upper()} tag={event.tag_id} ant={event.antenna} rssi={event.rssi} first={event.first} last={event.last}")

    # Optional helper for tests
    def collected(self) -> List[TagEvent]:  # pragma: no cover
        return list(self._events)
