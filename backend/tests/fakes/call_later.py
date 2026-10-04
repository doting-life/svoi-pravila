"""Fake ``call_later`` bound to ``FakeClock`` for deterministic TTL tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tests.fakes.clock import FakeClock


@dataclass(slots=True)
class FakeCallLaterHandle:
    """Cancellable handle for a scheduled fake callback."""

    cancelled: bool = False

    def cancel(self) -> None:
        """Mark the scheduled callback as cancelled."""
        self.cancelled = True


@dataclass(slots=True)
class _Scheduled:
    deadline: float
    callback: Callable[[], None]
    handle: FakeCallLaterHandle = field(default_factory=FakeCallLaterHandle)


class FakeCallLater:
    """Schedule callbacks against absolute monotonic deadlines from ``FakeClock``."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._pending: list[_Scheduled] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> FakeCallLaterHandle:
        """Record ``callback`` to run after ``delay`` seconds of fake time."""
        item = _Scheduled(
            deadline=self._clock.monotonic() + delay,
            callback=callback,
        )
        self._pending.append(item)
        return item.handle

    def fire_due(self) -> None:
        """Run all non-cancelled callbacks whose deadline is at or before now."""
        now = self._clock.monotonic()
        due = [item for item in self._pending if not item.handle.cancelled and item.deadline <= now]
        for item in due:
            item.handle.cancelled = True
            item.callback()
        self._pending = [item for item in self._pending if not item.handle.cancelled]
