"""AnalyticsStore decorator that records terminal job_runs as Prometheus counters."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date, datetime

from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobName, AnalyticsJobStatus
from svoi_pravila.application.ports.analytics_store import AnalyticsStore, JobRun
from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import (
    TERMINAL_ANALYTICS_STATUSES,
    analytics_job_label,
    analytics_status_label,
)


class MetricsAnalyticsStore:
    """Delegate to an inner store; increment metrics on terminal ``record_run``."""

    def __init__(self, inner: AnalyticsStore) -> None:
        self._inner = inner

    def hold_lock(self) -> AbstractAsyncContextManager[bool]:
        return self._inner.hold_lock()

    async def ensure_timezone(self, tz_name: str) -> None:
        await self._inner.ensure_timezone(tz_name)

    async def compute_day(
        self,
        day: date,
        tz_name: str,
        computed_at: datetime,
        llm_budget_tokens: int,
    ) -> int:
        return await self._inner.compute_day(day, tz_name, computed_at, llm_budget_tokens)

    async def compute_cohorts(
        self,
        from_day: date,
        to_day: date,
        tz_name: str,
        as_of_day: date,
        computed_at: datetime,
    ) -> int:
        return await self._inner.compute_cohorts(from_day, to_day, tz_name, as_of_day, computed_at)

    async def purge_usage_events(self, older_than: datetime, batch_size: int) -> int:
        return await self._inner.purge_usage_events(older_than, batch_size)

    async def count_usage_events_older_than(self, older_than: datetime) -> int:
        return await self._inner.count_usage_events_older_than(older_than)

    async def last_completed_day(self, job: AnalyticsJobName) -> date | None:
        return await self._inner.last_completed_day(job)

    async def earliest_event_day(self, tz_name: str) -> date | None:
        return await self._inner.earliest_event_day(tz_name)

    async def record_run(self, run: JobRun) -> None:
        await self._inner.record_run(run)
        if run.status is AnalyticsJobStatus.RUNNING:
            return
        status = analytics_status_label(run.status)
        if status not in TERMINAL_ANALYTICS_STATUSES:
            return
        families.ANALYTICS_JOB_RUNS.labels(
            job=analytics_job_label(run.job),
            status=status,
        ).inc()

    async def close_open_runs(self, kind: AnalyticsErrorKind, finished_at: datetime) -> int:
        return await self._inner.close_open_runs(kind, finished_at)
