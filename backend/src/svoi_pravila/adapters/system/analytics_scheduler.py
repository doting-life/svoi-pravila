"""In-process daily analytics scheduler (ADR-0001 §2)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol

import structlog

from svoi_pravila.application.analytics_schedule import next_wake
from svoi_pravila.application.errors import AnalyticsJobFailed
from svoi_pravila.application.ports.clock import Clock

logger = structlog.get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class DailyAnalyticsJob(Protocol):
    """Executable daily analytics job."""

    async def execute(self, now: datetime) -> None:
        """Run catch-up, cohorts, and purge."""
        ...


@dataclass(frozen=True, slots=True)
class AnalyticsSchedulerSettings:
    """Local time, zone, and enable flag for the in-process loop."""

    timezone: str
    run_at: time
    enabled: bool


class AnalyticsScheduler:
    """Run ``RunDailyAnalytics`` at startup and then on the wake schedule."""

    def __init__(
        self,
        use_case: DailyAnalyticsJob,
        clock: Clock,
        settings: AnalyticsSchedulerSettings,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._use_case = use_case
        self._clock = clock
        self._timezone = settings.timezone
        self._run_at = settings.run_at
        self._enabled = settings.enabled
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the background loop when jobs are enabled."""
        if not self._enabled:
            return
        self._task = asyncio.create_task(self._loop(), name="analytics_scheduler")
        self._task.add_done_callback(self._on_done)

    async def shutdown(self) -> None:
        """Cancel the loop task; do not touch the database."""
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
        failures = 0
        while True:
            try:
                await self._use_case.execute(self._clock.now())
            except AnalyticsJobFailed as exc:
                failures += 1
                logger.warning(
                    "analytics_job_failed",
                    job=None if exc.job is None else exc.job.value,
                    error_kind=exc.kind.value,
                    day=None if exc.target_day is None else exc.target_day.isoformat(),
                )
            else:
                failures = 0
            delay = (
                next_wake(self._clock.now(), self._timezone, self._run_at, failures)
                - self._clock.now()
            ).total_seconds()
            await self._sleep(max(delay, 0.0))

    @staticmethod
    def _on_done(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.error("analytics_scheduler_stopped", error_type=type(exc).__name__)
