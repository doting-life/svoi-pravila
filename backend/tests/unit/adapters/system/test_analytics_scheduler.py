"""In-process analytics scheduler loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
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


class _BoomJob:
    async def execute(self, now: datetime) -> None:
        _ = now
        raise RuntimeError("unexpected")


@pytest.mark.unit
async def test_disabled_scheduler_does_not_start_a_task() -> None:
    scheduler = AnalyticsScheduler(
        _FailingJob(),
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


@pytest.mark.unit
async def test_loop_logs_typed_failure_then_retries_with_backoff(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    scheduler = AnalyticsScheduler(
        _FailingJob(),
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
        item.get("event") == "analytics_job_failed"
        and item.get("error_kind") == "database"
        and item.get("level") == "warning"
        for item in logged
    )
    assert sleeps == [timedelta(minutes=5).total_seconds()]
    await scheduler.shutdown()


@pytest.mark.unit
async def test_shutdown_cancels_in_flight_run_without_db() -> None:
    job = _HangingJob()
    scheduler = AnalyticsScheduler(
        job,
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
    assert scheduler._task is None


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = NOW
        self._calls = 0

    def now(self) -> datetime:
        self._calls += 1
        if self._calls >= 3:
            return datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
        return self._now


@pytest.mark.unit
async def test_loop_clamps_negative_delay() -> None:
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 1:
            return
        raise asyncio.CancelledError

    class _Ok:
        async def execute(self, now: datetime) -> None:
            _ = now

    scheduler = AnalyticsScheduler(
        _Ok(),
        _AdvancingClock(),
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
    assert sleeps[0] == 0.0
    await scheduler.shutdown()


@pytest.mark.unit
async def test_done_callback_logs_unexpected_stop(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    async def _sleep(_delay: float) -> None:
        raise asyncio.CancelledError

    scheduler = AnalyticsScheduler(
        _BoomJob(),
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
    with pytest.raises(RuntimeError, match="unexpected"):
        await scheduler._task
    logged = capture_log_events()
    assert any(
        item.get("event") == "analytics_scheduler_stopped"
        and item.get("error_type") == "RuntimeError"
        and item.get("level") == "error"
        for item in logged
    )
    await scheduler.shutdown()


@pytest.mark.unit
async def test_done_callback_ignores_clean_completion(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    async def _ok() -> None:
        return None

    task = asyncio.create_task(_ok())
    task.add_done_callback(AnalyticsScheduler._on_done)
    await task
    assert not any(
        item.get("event") == "analytics_scheduler_stopped" for item in capture_log_events()
    )


@pytest.mark.unit
async def test_shutdown_retrieves_exception_from_finished_task() -> None:
    scheduler = AnalyticsScheduler(
        _BoomJob(),
        FakeClock(NOW),
        AnalyticsSchedulerSettings(
            timezone="Europe/Moscow",
            run_at=time(3, 30),
            enabled=True,
        ),
        sleep=asyncio.sleep,
    )
    await scheduler.start()
    assert scheduler._task is not None
    with pytest.raises(RuntimeError, match="unexpected"):
        await asyncio.wait_for(scheduler._task, timeout=2)
    await scheduler.shutdown()
    assert scheduler._task is None
