"""Monotonic clock port for TTFC and latency measurement."""

from __future__ import annotations

from typing import Protocol


class MonotonicClock(Protocol):
    """Monotonic seconds, faked in tests."""

    def monotonic(self) -> float:
        """Return a monotonic timestamp in seconds."""
        ...
