"""Port for product-analytics SQL aggregates and job bookkeeping."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobName,
    AnalyticsJobStatus,
)


@dataclass(frozen=True, slots=True)
class JobRun:
    """One ``job_runs`` row."""

    id: UUID
    job: AnalyticsJobName
    target_day: date | None
    started_at: datetime
    finished_at: datetime | None
    status: AnalyticsJobStatus
    rows_affected: int
    error_kind: AnalyticsErrorKind | None


class AnalyticsStore(Protocol):
    """Idempotent daily analytics against Postgres."""

    def hold_lock(self) -> AbstractAsyncContextManager[bool]:
        """Session advisory lock; yields False when the lock is not acquired."""
        ...

    async def ensure_timezone(self, tz_name: str) -> None:
        """Reject names absent from ``pg_timezone_names``."""
        ...

    async def compute_day(self, day: date, tz_name: str, computed_at: datetime) -> int:
        """Upsert ``analytics_daily`` and scenario rows for ``day``. Return rows written."""
        ...

    async def compute_cohorts(
        self,
        from_day: date,
        to_day: date,
        tz_name: str,
        as_of_day: date,
        computed_at: datetime,
    ) -> int:
        """Upsert cohort rows for first-appeal days in ``[from_day, to_day]``."""
        ...

    async def purge_usage_events(self, older_than: datetime, batch_size: int) -> int:
        """Delete expired events in batches of ``batch_size``; return total deleted."""
        ...

    async def count_usage_events_older_than(self, older_than: datetime) -> int:
        """Count events with ``occurred_at < older_than`` (CLI dry-run)."""
        ...

    async def last_completed_day(self, job: AnalyticsJobName) -> date | None:
        """Latest ``target_day`` with ``status=succeeded`` for ``job``."""
        ...

    async def earliest_event_day(self, tz_name: str) -> date | None:
        """Earliest usage-event calendar day in ``tz_name``."""
        ...

    async def record_run(self, run: JobRun) -> None:
        """Insert or update a ``job_runs`` row by id."""
        ...

    async def close_open_runs(self, kind: AnalyticsErrorKind, finished_at: datetime) -> int:
        """Mark leftover ``running`` rows as ``failed`` with ``kind``."""
        ...
