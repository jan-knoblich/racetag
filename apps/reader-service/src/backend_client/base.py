from __future__ import annotations

from abc import ABC, abstractmethod

from models import TagEvent


class BackendClient(ABC):
    """Abstract client contract for sending TagEvents to the backend (HTTP/WS/MQTT)."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def send(self, event: TagEvent) -> None: ...
