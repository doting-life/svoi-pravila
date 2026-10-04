"""Injectable sleep seam for inline debounce (no real sleeps in tests)."""

from __future__ import annotations

import asyncio
from typing import Protocol


class Sleeper(Protocol):
    """Async delay used by the inline query coordinator."""

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` (zero is allowed)."""
        ...


class AsyncioSleeper:
    """Production sleeper backed by ``asyncio.sleep``."""

    async def sleep(self, seconds: float) -> None:
        """Delay using the event loop."""
        await asyncio.sleep(seconds)
