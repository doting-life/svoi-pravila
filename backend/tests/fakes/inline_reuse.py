"""In-process inline reuse adapter for tests (no I/O)."""

from __future__ import annotations

from svoi_pravila.adapters.system.inline_result_reuse import InProcessInlineResultReuse
from svoi_pravila.application.ports.monotonic import MonotonicClock
from tests.fakes.clock import FakeClock


def make_inline_reuse(
    monotonic: MonotonicClock | None = None,
    *,
    ttl_seconds: float = 30.0,
    max_entries: int = 10_000,
) -> InProcessInlineResultReuse:
    """Build a fresh in-process reuse adapter for unit and integration tests."""
    return InProcessInlineResultReuse(
        monotonic if monotonic is not None else FakeClock(),
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
    )
