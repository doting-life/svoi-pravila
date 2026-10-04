"""Monotonic time adapter."""

from __future__ import annotations

import time


class SystemMonotonicClock:
    """``time.monotonic`` in seconds."""

    def monotonic(self) -> float:
        """Return a monotonic timestamp."""
        return time.monotonic()
