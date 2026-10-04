"""In-memory usage-event sink."""

from __future__ import annotations

from svoi_pravila.domain.usage import UsageEvent


class RecordingUsageEventSink:
    """Collect usage events without I/O."""

    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.events.append(event)


class FailingUsageEventSink:
    """Sink that always fails (decode must still complete)."""

    async def record(self, event: UsageEvent) -> None:
        msg = "sink unavailable"
        raise RuntimeError(msg)
