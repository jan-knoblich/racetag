from __future__ import annotations

from typing import List

from models import TagEvent
from utils import get_logger
from .base import BackendClient

logger = get_logger("reader.backend.mock")


class MockBackendClient(BackendClient):
    """Mock backend client that just logs events locally.

    Useful for testing the reader pipeline without an actual backend service.
    """

    def __init__(self) -> None:
        self._events: List[TagEvent] = []

    def start(self) -> None:
        logger.info("[BACKEND] Mock client started")

    def stop(self) -> None:
        logger.info("[BACKEND] Mock client stopped (total events: %d)", len(self._events))

    def send(self, event: TagEvent) -> None:
        self._events.append(event)
        logger.info(
            "[BACKEND][MOCK] %s tag=%s ant=%s rssi=%s first=%s last=%s",
            event.event_type.upper(), event.tag_id, event.antenna, event.rssi,
            event.first, event.last,
        )

    # Optional helper for tests
    def collected(self) -> List[TagEvent]:  # pragma: no cover
        return list(self._events)
