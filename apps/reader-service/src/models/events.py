from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Literal


@dataclass(frozen=True)
class TagEvent:
    source: str              # e.g., "sirit-510"
    reader_serial: str       # reader serial number
    reader_ip: str           # reader IP
    timestamp: str           # ISO8601 UTC string with milliseconds, e.g., 2025-10-16T10:20:30.123Z
    event_type: str          # "arrive" | "depart"
    tag_id: str              # HEX uppercase, without 0x
    session_id: Optional[int] = None
    antenna: Optional[int] = None
    rssi: Optional[int] = None
    # Reader-provided timestamps for first seen (arrive) / last seen (depart)
    first: Optional[str] = None
    last: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    def to_payload(self) -> Dict[str, Any]:
        """Serialize event to backend payload, excluding None fields and merging extra.

        Keeps a stable transport shape under models, so transports (HTTP/WS/MQTT)
        don't duplicate mapping logic.
        """
        base = asdict(self)
        # Merge extra and drop it from root
        extra = base.pop("extra", None) or {}
        # Filter None values
        clean = {k: v for k, v in base.items() if v is not None}
        if extra:
            clean.update(extra)
        return clean


# Known Sirit event types
EventType = Literal["arrive", "depart"]


@dataclass(frozen=True)
class ArriveEventData:
    """Parsed data from an event.tag.arrive message.

    - tag_id: required uppercase HEX (without 0x)
    - first: optional ISO8601 local/UTC timestamp string as provided by the reader
    - antenna/rssi: optional numeric values
    - extra: optional extra fields if we decide to propagate more metadata
    """
    tag_id: str
    first: Optional[str] = None
    antenna: Optional[int] = None
    rssi: Optional[int] = None
    extra: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class DepartEventData:
    """Parsed data from an event.tag.depart message.

    - tag_id: required uppercase HEX (without 0x)
    - antenna/rssi: optional numeric values
    - extra: optional extra fields if we decide to propagate more metadata
    """
    tag_id: str
    last: Optional[str] = None
    antenna: Optional[int] = None
    rssi: Optional[int] = None
    extra: Optional[Dict[str, Any]] = None
