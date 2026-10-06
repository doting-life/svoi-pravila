"""In-process analytics scheduler loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

import pytest
from tests.fakes.analytics_store import FakeAnalyticsStore
from tests.fakes.clock import FakeClock

from svoi_pravila.adapters.system.analytics_scheduler import (
    AnalyticsScheduler,
    AnalyticsSchedulerSettings,
)
from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobFailed

NOW = datetime(2026, 1, 15, 0, 31, tzinfo=UTC)


class _FailingJob:
    async def execute(self, now: datetime) -> None:
        _ = now
        raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE)


class _HangingJob:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, now: datetime) -> None:
        _ = now
        self.started.set()
        await self.release.wait()


@pytest.mark.unit
async def test_disabled_scheduler_does_not_start_a_task() -> None:
    store = FakeAnalyticsStore()
    scheduler = AnalyticsScheduler(
        _FailingJob(),
        store,
        FakeClock(NOW),
        AnalyticsSchedulerSettings(
            timezone="Europe/Moscow",
            run_at=time(3, 30),
            enabled=False,
        ),
    )
    await scheduler.start()
    assert scheduler._task is None
    await scheduler.shutdown()
    assert store.closed == []


@pytest.mark.unit
async def test_loop_logs_typed_failure_then_waits(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    store = FakeAnalyticsStore()
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    scheduler = AnalyticsScheduler(
        _FailingJob(),
        store,
        FakeClock(NOW),
        AnalyticsSchedulerSettings(
            timezone="Europe/Moscow",
            run_at=time(3, 30),
            enabled=True,
        ),
        sleep=_sleep,
    )
    await scheduler.start()
    assert scheduler._task is not None
    with pytest.raises(asyncio.CancelledError):
        await scheduler._task
    logged = capture_log_events()
    assert any(
        item.get("event") == "analytics_job_failed" and item.get("error_kind") == "database"
        for item in logged
    )
    assert sleeps
    await scheduler.shutdown()


@pytest.mark.unit
async def test_shutdown_cancels_in_flight_run_and_closes_running_rows() -> None:
    store = FakeAnalyticsStore()
    job = _HangingJob()
    scheduler = AnalyticsScheduler(
        job,
        store,
        FakeClock(NOW),
        AnalyticsSchedulerSettings(
            timezone="Europe/Moscow",
            run_at=time(3, 30),
            enabled=True,
        ),
    )
    await scheduler.start()
    await asyncio.wait_for(job.started.wait(), timeout=2)
    await scheduler.shutdown()
    assert store.closed[0][0] is AnalyticsErrorKind.CANCELLED
    assert scheduler._task is None
