"""Fake clock for deterministic tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeClock:
    """Mutable UTC clock."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Advance the clock by ``delta``."""
        self._now = self._now + delta
