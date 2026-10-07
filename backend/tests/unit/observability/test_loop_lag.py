"""Event-loop lag monitor starts and cancels cleanly."""

from __future__ import annotations

import asyncio

import pytest

from svoi_pravila.observability.metrics.loop_lag import EventLoopLagMonitor


@pytest.mark.unit
@pytest.mark.asyncio
async def test_loop_lag_start_and_cancel() -> None:
    woke = asyncio.Event()
    cancelled = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        woke.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monitor = EventLoopLagMonitor(sleep=sleep)
    await monitor.start()
    await asyncio.wait_for(woke.wait(), timeout=1.0)
    await monitor.shutdown()
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    # Second shutdown is a no-op.
    await monitor.shutdown()
