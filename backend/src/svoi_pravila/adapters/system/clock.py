"""Wall-clock adapter."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Return the current timezone-aware UTC time."""

    def now(self) -> datetime:
        """Return ``datetime.now(UTC)``."""
        return datetime.now(UTC)
