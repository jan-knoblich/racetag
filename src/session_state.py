from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionState:
    id: Optional[int] = None
    bound: bool = False
