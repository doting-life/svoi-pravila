"""In-process inline reuse adapter for tests (no I/O)."""

from __future__ import annotations

from svoi_pravila.adapters.system.inline_result_reuse import CallLater, InProcessInlineResultReuse
from svoi_pravila.application.ports.monotonic import MonotonicClock
from tests.fakes.call_later import FakeCallLater
from tests.fakes.clock import FakeClock


def make_inline_reuse(
    monotonic: MonotonicClock | None = None,
    *,
    ttl_seconds: float = 30.0,
    max_entries: int = 10_000,
    call_later: CallLater | FakeCallLater | None = None,
) -> InProcessInlineResultReuse:
    """Build a fresh in-process reuse adapter for unit and integration tests."""
    clock: MonotonicClock = monotonic if monotonic is not None else FakeClock()
    scheduler: CallLater | FakeCallLater | None = call_later
    if scheduler is None and isinstance(clock, FakeClock):
        scheduler = FakeCallLater(clock)
    return InProcessInlineResultReuse(
        clock,
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
        call_later=scheduler,
    )
