"""Catch-up daily aggregates, 8-day cohort window, and 13-month purge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobFailed,
    AnalyticsJobName,
    AnalyticsJobStatus,
)
from svoi_pravila.application.ports.analytics_store import AnalyticsStore, JobRun
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator

CATCH_UP_MAX_DAYS = 35
COHORT_WINDOW_START_OFFSET = 8
COHORT_WINDOW_END_OFFSET = 1
PURGE_BATCH_SIZE = 5_000


@dataclass(frozen=True, slots=True)
class RunDailyAnalyticsPorts:
    """Collaborators for ``RunDailyAnalytics``."""

    store: AnalyticsStore
    ids: IdGenerator
    clock: Clock
    timezone: str


class RunDailyAnalytics:
    """Idempotent daily analytics job (lock, catch-up, cohorts, purge)."""

    def __init__(self, ports: RunDailyAnalyticsPorts) -> None:
        self._store = ports.store
        self._ids = ports.ids
        self._clock = ports.clock
        self._timezone = ports.timezone

    async def execute(self, now: datetime) -> None:
        """Run catch-up through yesterday, recompute recent cohorts, then purge."""
        async with self._store.hold_lock() as acquired:
            if not acquired:
                await self._store.record_run(
                    JobRun(
                        id=self._ids.new_id(),
                        job=AnalyticsJobName.DAILY_AGGREGATES,
                        target_day=None,
                        started_at=now,
                        finished_at=now,
                        status=AnalyticsJobStatus.SKIPPED_LOCKED,
                        rows_affected=0,
                        error_kind=None,
                    )
                )
                return
            await self._store.close_open_runs(AnalyticsErrorKind.CANCELLED, now)
            await self._store.ensure_timezone(self._timezone)
            yesterday = now.astimezone(ZoneInfo(self._timezone)).date() - timedelta(days=1)
            await self._catch_up(now, yesterday)
            await self._cohorts(now, yesterday)
            await self._purge(now)

    async def _catch_up(self, now: datetime, yesterday: date) -> None:
        last = await self._store.last_completed_day(AnalyticsJobName.DAILY_AGGREGATES)
        if last is None:
            earliest = await self._store.earliest_event_day(self._timezone)
            if earliest is None:
                return
            start = earliest
        else:
            start = last + timedelta(days=1)
        start = min(start, yesterday)
        span = (yesterday - start).days + 1
        capped = span > CATCH_UP_MAX_DAYS
        if capped:
            start = yesterday - timedelta(days=CATCH_UP_MAX_DAYS - 1)
        day = start
        while day <= yesterday:
            await self._compute_one_day(day, now, capped=capped and day == start)
            day = day + timedelta(days=1)

    async def _compute_one_day(self, day: date, now: datetime, *, capped: bool) -> None:
        async def _work() -> int:
            return await self._store.compute_day(day, self._timezone, now)

        await self._step(
            job=AnalyticsJobName.DAILY_AGGREGATES,
            target_day=day,
            now=now,
            error_kind=AnalyticsErrorKind.CATCH_UP_CAPPED if capped else None,
            work=_work,
        )

    async def _cohorts(self, now: datetime, yesterday: date) -> None:
        from_day = yesterday - timedelta(days=COHORT_WINDOW_START_OFFSET)
        to_day = yesterday - timedelta(days=COHORT_WINDOW_END_OFFSET)

        async def _work() -> int:
            return await self._store.compute_cohorts(
                from_day, to_day, self._timezone, yesterday, now
            )

        await self._step(
            job=AnalyticsJobName.COHORTS,
            target_day=None,
            now=now,
            error_kind=None,
            work=_work,
        )

    async def _purge(self, now: datetime) -> None:
        async def _work() -> int:
            return await self._store.purge_usage_events(now, PURGE_BATCH_SIZE)

        await self._step(
            job=AnalyticsJobName.PURGE,
            target_day=None,
            now=now,
            error_kind=None,
            work=_work,
        )

    async def _step(
        self,
        *,
        job: AnalyticsJobName,
        target_day: date | None,
        now: datetime,
        error_kind: AnalyticsErrorKind | None,
        work: Callable[[], Awaitable[int]],
    ) -> None:
        run_id = self._ids.new_id()
        started = JobRun(
            id=run_id,
            job=job,
            target_day=target_day,
            started_at=now,
            finished_at=None,
            status=AnalyticsJobStatus.RUNNING,
            rows_affected=0,
            error_kind=None,
        )
        await self._store.record_run(started)
        try:
            rows = await work()
        except AnalyticsJobFailed as exc:
            await self._store.record_run(
                replace(
                    started,
                    finished_at=self._clock.now(),
                    status=AnalyticsJobStatus.FAILED,
                    error_kind=exc.kind,
                )
            )
            raise AnalyticsJobFailed(exc.kind, job=job, target_day=target_day) from exc
        await self._store.record_run(
            replace(
                started,
                finished_at=self._clock.now(),
                status=AnalyticsJobStatus.SUCCEEDED,
                rows_affected=rows,
                error_kind=error_kind,
            )
        )
