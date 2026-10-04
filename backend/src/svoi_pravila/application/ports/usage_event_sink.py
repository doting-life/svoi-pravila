"""Port that records C0 usage events."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.usage import UsageEvent


class UsageEventSink(Protocol):
    """Persist one usage event in its own unit of work."""

    async def record(self, event: UsageEvent) -> None:
        """Write ``event``. Failures must not change the user-visible result."""
        ...
