"""Fake sleepers for inline debounce tests (no wall-clock sleep)."""

from __future__ import annotations

import asyncio


class ImmediateSleeper:
    """Complete sleep immediately without waiting."""

    async def sleep(self, seconds: float) -> None:
        _ = seconds


class GateSleeper:
    """Park sleepers on an Event so tests can cancel or release them."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.entered.set()
        await self.release.wait()
