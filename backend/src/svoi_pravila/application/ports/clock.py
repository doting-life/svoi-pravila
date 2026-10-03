"""Clock port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Provides the current UTC time."""

    def now(self) -> datetime:
        """Return timezone-aware UTC datetime."""
        ...
