"""In-process daily analytics scheduler (ADR-0001 §2)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol

import structlog

from svoi_pravila.application.analytics_schedule import next_run
from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobFailed
from svoi_pravila.application.ports.analytics_store import AnalyticsStore
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
    """Run ``RunDailyAnalytics`` at startup and then at a daily local time."""

    def __init__(
        self,
        use_case: DailyAnalyticsJob,
        store: AnalyticsStore,
        clock: Clock,
        settings: AnalyticsSchedulerSettings,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._use_case = use_case
        self._store = store
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
        self._task = asyncio.create_task(self._loop())

    async def shutdown(self) -> None:
        """Cancel the loop and fail leftover ``running`` journal rows."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if not self._enabled:
            return
        await self._store.close_open_runs(
            AnalyticsErrorKind.CANCELLED,
            self._clock.now(),
        )

    async def _loop(self) -> None:
        await self._run_safe()
        while True:
            delay = (
                next_run(self._clock.now(), self._timezone, self._run_at) - self._clock.now()
            ).total_seconds()
            await self._sleep(max(delay, 0.0))
            await self._run_safe()

    async def _run_safe(self) -> None:
        try:
            await self._use_case.execute(self._clock.now())
        except AnalyticsJobFailed as exc:
            logger.info(
                "analytics_job_failed",
                job=None if exc.job is None else exc.job.value,
                error_kind=exc.kind.value,
                day=None if exc.target_day is None else exc.target_day.isoformat(),
            )
