"""In-memory AnalyticsStore for use-case tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import UUID

from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobFailed,
    AnalyticsJobName,
    AnalyticsJobStatus,
)
from svoi_pravila.application.ports.analytics_store import JobRun


class FakeAnalyticsStore:
    """Records calls; optional failures and lock contention."""

    def __init__(self) -> None:
        self.acquired = True
        self.timezone_ok = True
        self.last_day: date | None = None
        self.earliest: date | None = None
        self.compute_days: list[date] = []
        self.cohort_calls: list[tuple[date, date, date]] = []
        self.purge_calls: list[tuple[datetime, int]] = []
        self.runs: list[JobRun] = []
        self.fail_compute: AnalyticsErrorKind | None = None
        self.closed: list[tuple[AnalyticsErrorKind, datetime]] = []

    @asynccontextmanager
    async def hold_lock(self) -> AsyncIterator[bool]:
        yield self.acquired

    async def ensure_timezone(self, tz_name: str) -> None:
        _ = tz_name
        if not self.timezone_ok:
            raise AnalyticsJobFailed(AnalyticsErrorKind.UNKNOWN_TIMEZONE)

    async def compute_day(self, day: date, tz_name: str, computed_at: datetime) -> int:
        _ = tz_name, computed_at
        if self.fail_compute is not None:
            raise AnalyticsJobFailed(self.fail_compute)
        self.compute_days.append(day)
        return 1

    async def compute_cohorts(
        self,
        from_day: date,
        to_day: date,
        tz_name: str,
        as_of_day: date,
        computed_at: datetime,
    ) -> int:
        _ = tz_name, computed_at
        self.cohort_calls.append((from_day, to_day, as_of_day))
        return 0

    async def purge_usage_events(self, older_than: datetime, batch_size: int) -> int:
        self.purge_calls.append((older_than, batch_size))
        return 0

    async def count_usage_events_older_than(self, older_than: datetime) -> int:
        _ = older_than
        return 0

    async def last_completed_day(self, job: AnalyticsJobName) -> date | None:
        _ = job
        return self.last_day

    async def earliest_event_day(self, tz_name: str) -> date | None:
        _ = tz_name
        return self.earliest

    async def record_run(self, run: JobRun) -> None:
        self.runs = [item for item in self.runs if item.id != run.id]
        self.runs.append(run)

    async def close_open_runs(self, kind: AnalyticsErrorKind, finished_at: datetime) -> int:
        self.closed.append((kind, finished_at))
        running = [item for item in self.runs if item.status is AnalyticsJobStatus.RUNNING]
        for item in running:
            self.runs.remove(item)
        return len(running)

    def run_ids(self) -> list[UUID]:
        return [item.id for item in self.runs]
