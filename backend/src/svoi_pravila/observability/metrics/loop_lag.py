"""Background task that records event-loop sleep overshoot."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

from svoi_pravila.observability.metrics.families import EVENT_LOOP_LAG

Sleep = Callable[[float], Awaitable[None]]

_SLEEP_SECONDS = 0.5


class EventLoopLagMonitor:
    """Sleep 0.5 s in a loop and observe overshoot as ``sp_event_loop_lag_seconds``."""

    def __init__(self, *, sleep: Sleep = asyncio.sleep) -> None:
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the background lag sampler."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="event_loop_lag")

    async def shutdown(self) -> None:
        """Cancel the sampler cleanly."""
        task = self._task
        self._task = None
        if task is None:
            return
        if task.done():
            if not task.cancelled():
                _ = task.exception()
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            started = time.perf_counter()
            await self._sleep(_SLEEP_SECONDS)
            overshoot = time.perf_counter() - started - _SLEEP_SECONDS
            EVENT_LOOP_LAG.observe(max(overshoot, 0.0))
