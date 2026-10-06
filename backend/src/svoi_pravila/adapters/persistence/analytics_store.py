"""Postgres analytics store: SQL aggregates, session advisory lock, job journal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobFailed,
    AnalyticsJobName,
    AnalyticsJobStatus,
)
from svoi_pravila.application.ports.analytics_store import JobRun
from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    DayAggregate,
    ScenarioTotals,
)
from svoi_pravila.domain.enums import UsageScenario, UsageSurface

LOCK_KEY = "svoi_pravila.analytics.daily"

_APPEAL = """
(
  e.scenario <> 'suggest_rule'
  AND (
    e.event_kind = 'result_chosen'
    OR (
      e.event_kind = 'generation'
      AND e.surface IN ('dm', 'miniapp')
      AND e.outcome IN ('ok', 'refused', 'screened')
    )
  )
)
"""


class SqlAlchemyAnalyticsStore:
    """``AnalyticsStore`` over a shared async engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def hold_lock(self) -> AsyncIterator[bool]:
        """Hold ``pg_try_advisory_lock`` on a dedicated connection for the job."""
        acquired = False
        try:
            async with self._engine.connect() as conn:
                try:
                    result = await conn.execute(
                        text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                        {"k": LOCK_KEY},
                    )
                    acquired = bool(result.scalar())
                    await conn.commit()
                except OSError as exc:
                    raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
                except SQLAlchemyError as exc:
                    raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc
                try:
                    yield acquired
                finally:
                    if acquired:
                        try:
                            await conn.execute(
                                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                                {"k": LOCK_KEY},
                            )
                            await conn.commit()
                        except OSError as exc:
                            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
                        except SQLAlchemyError as exc:
                            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc

    async def ensure_timezone(self, tz_name: str) -> None:
        """Raise ``unknown_timezone`` when Postgres does not know ``tz_name``."""
        found = await self._scalar(
            "SELECT 1 FROM pg_timezone_names WHERE name = :tz",
            {"tz": tz_name},
        )
        if found is None:
            raise AnalyticsJobFailed(AnalyticsErrorKind.UNKNOWN_TIMEZONE)

    async def compute_day(self, day: date, tz_name: str, computed_at: datetime) -> int:
        """Idempotent upsert of daily totals and scenario slices."""
        params = {"day": day, "tz": tz_name, "computed_at": computed_at}
        await self._execute(_DAILY_UPSERT, params)
        await self._execute(
            "DELETE FROM analytics_daily_scenario WHERE day = :day",
            {"day": day},
        )
        scenario_count = await self._scalar_write(_SCENARIO_INSERT, params)
        return 1 + _as_int(scenario_count)

    async def compute_cohorts(
        self,
        from_day: date,
        to_day: date,
        tz_name: str,
        as_of_day: date,
        computed_at: datetime,
    ) -> int:
        """Idempotent upsert of cohort rows in ``[from_day, to_day]``."""
        count = await self._scalar_write(
            _COHORT_UPSERT,
            {
                "from_day": from_day,
                "to_day": to_day,
                "tz": tz_name,
                "as_of": as_of_day,
                "computed_at": computed_at,
            },
        )
        return _as_int(count)

    async def purge_usage_events(self, older_than: datetime, batch_size: int) -> int:
        """Delete events older than ``now - interval '13 months'`` in batches."""
        deleted = 0
        while True:
            n = await self._execute_rowcount(
                _PURGE_BATCH,
                {"now": older_than, "batch": batch_size},
            )
            deleted += n
            if n < batch_size:
                return deleted

    async def count_usage_events_older_than(self, older_than: datetime) -> int:
        """Count events past the 13-month Postgres cutoff."""
        count = await self._scalar(
            "SELECT COUNT(*) FROM usage_events "
            "WHERE occurred_at < (CAST(:now AS timestamptz) - interval '13 months')",
            {"now": older_than},
        )
        return _as_int(count)

    async def last_completed_day(self, job: AnalyticsJobName) -> date | None:
        """Latest succeeded ``target_day`` for ``job``."""
        value = await self._scalar(
            "SELECT MAX(target_day) FROM job_runs "
            "WHERE job = :job AND status = 'succeeded' AND target_day IS NOT NULL",
            {"job": job.value},
        )
        return value if isinstance(value, date) else None

    async def earliest_event_day(self, tz_name: str) -> date | None:
        """Minimum event calendar day in ``tz_name``."""
        value = await self._scalar(
            "SELECT MIN((occurred_at AT TIME ZONE :tz)::date) FROM usage_events",
            {"tz": tz_name},
        )
        return value if isinstance(value, date) else None

    async def record_run(self, run: JobRun) -> None:
        """Insert or update a ``job_runs`` row."""
        await self._execute(
            """
            INSERT INTO job_runs (
                id, job, target_day, started_at, finished_at, status, rows_affected, error_kind
            ) VALUES (
                :id, :job, :target_day, :started_at, :finished_at, :status,
                :rows_affected, :error_kind
            )
            ON CONFLICT (id) DO UPDATE SET
                job = EXCLUDED.job,
                target_day = EXCLUDED.target_day,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                status = EXCLUDED.status,
                rows_affected = EXCLUDED.rows_affected,
                error_kind = EXCLUDED.error_kind
            """,
            {
                "id": run.id,
                "job": run.job.value,
                "target_day": run.target_day,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status.value,
                "rows_affected": run.rows_affected,
                "error_kind": None if run.error_kind is None else run.error_kind.value,
            },
        )

    async def close_open_runs(self, kind: AnalyticsErrorKind, finished_at: datetime) -> int:
        """Fail leftover ``running`` rows."""
        return await self._execute_rowcount(
            """
            UPDATE job_runs
            SET status = :status, error_kind = :kind, finished_at = :finished_at
            WHERE status = 'running'
            """,
            {
                "status": AnalyticsJobStatus.FAILED.value,
                "kind": kind.value,
                "finished_at": finished_at,
            },
        )

    async def fetch_day(self, day: date) -> DayAggregate | None:
        """Load stored aggregates for oracle comparison."""
        daily_row = await self._all("SELECT * FROM analytics_daily WHERE day = :day", {"day": day})
        if not daily_row:
            return None
        raw = daily_row[0]
        daily = DailyTotals(
            day=raw.day,
            active_users=raw.active_users,
            appeals=raw.appeals,
            new_users=raw.new_users,
            generations=raw.generations,
            generation_errors=raw.generation_errors,
            computed_at=raw.computed_at,
        )
        scenario_rows = await self._all(
            "SELECT * FROM analytics_daily_scenario WHERE day = :day ORDER BY scenario, surface",
            {"day": day},
        )
        scenarios = tuple(
            ScenarioTotals(
                day=item.day,
                scenario=UsageScenario(item.scenario),
                surface=UsageSurface(item.surface),
                appeals=item.appeals,
                users=item.users,
                ok=item.ok,
                refused=item.refused,
                screened=item.screened,
                invalid_output=item.invalid_output,
                unavailable=item.unavailable,
                chosen=item.chosen,
                latency_p50_ms=_float_or_none(item.latency_p50_ms),
                latency_p95_ms=_float_or_none(item.latency_p95_ms),
                ttfc_p50_ms=_float_or_none(item.ttfc_p50_ms),
                ttfc_p95_ms=_float_or_none(item.ttfc_p95_ms),
                input_tokens=int(item.input_tokens),
                output_tokens=int(item.output_tokens),
                billable_tokens=int(item.billable_tokens),
            )
            for item in scenario_rows
        )
        return DayAggregate(daily=daily, scenarios=scenarios)

    async def fetch_cohorts(self, from_day: date, to_day: date) -> tuple[CohortTotals, ...]:
        """Load stored cohort rows for oracle comparison."""
        rows = await self._all(
            "SELECT * FROM analytics_cohorts "
            "WHERE cohort_day BETWEEN :from_day AND :to_day ORDER BY cohort_day",
            {"from_day": from_day, "to_day": to_day},
        )
        return tuple(
            CohortTotals(
                cohort_day=item.cohort_day,
                size=item.size,
                d1_retained=item.d1_retained,
                d7_retained=item.d7_retained,
                computed_at=item.computed_at,
            )
            for item in rows
        )

    async def fetch_job_runs(self) -> list[JobRun]:
        """Load job journal rows ordered by start time."""
        rows = await self._all(
            "SELECT * FROM job_runs ORDER BY started_at, id",
            {},
        )
        return [
            JobRun(
                id=item.id,
                job=AnalyticsJobName(item.job),
                target_day=item.target_day,
                started_at=item.started_at,
                finished_at=item.finished_at,
                status=AnalyticsJobStatus(item.status),
                rows_affected=item.rows_affected,
                error_kind=(
                    None if item.error_kind is None else AnalyticsErrorKind(item.error_kind)
                ),
            )
            for item in rows
        ]

    async def _scalar_write(self, sql: str, params: dict[str, object]) -> object:
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return result.scalar()
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc

    async def _scalar(self, sql: str, params: dict[str, object]) -> object:
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(text(sql), params)
                return result.scalar()
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc

    async def _all(self, sql: str, params: dict[str, object]) -> list[Any]:
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(text(sql), params)
                return list(result.all())
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc

    async def _execute(self, sql: str, params: dict[str, object]) -> None:
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text(sql), params)
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc

    async def _execute_rowcount(self, sql: str, params: dict[str, object]) -> int:
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return max(int(result.rowcount), 0)
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return float(value)
    return None


_DAILY_UPSERT = """
WITH bounds AS (
    SELECT
        timezone(:tz, CAST(:day AS timestamp without time zone)) AS start_ts,
        timezone(:tz, CAST(:day AS timestamp without time zone) + interval '1 day') AS end_ts
)
INSERT INTO analytics_daily (
    day, active_users, appeals, new_users, generations, generation_errors, computed_at
)
SELECT
    :day,
    (
        SELECT COUNT(DISTINCT user_pseudonym)
        FROM usage_events e, bounds b
        WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
          AND __APPEAL__
    ),
    (
        SELECT COUNT(*)
        FROM usage_events e, bounds b
        WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
          AND __APPEAL__
    ),
    (
        SELECT COUNT(DISTINCT e.user_pseudonym)
        FROM usage_events e, bounds b
        WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
          AND __APPEAL__
          AND NOT EXISTS (
            SELECT 1 FROM usage_events p
            WHERE p.user_pseudonym = e.user_pseudonym
              AND p.occurred_at < b.start_ts
              AND (
                p.scenario <> 'suggest_rule'
                AND (
                  p.event_kind = 'result_chosen'
                  OR (
                    p.event_kind = 'generation'
                    AND p.surface IN ('dm', 'miniapp')
                    AND p.outcome IN ('ok', 'refused', 'screened')
                  )
                )
              )
          )
    ),
    (
        SELECT COUNT(*)
        FROM usage_events e, bounds b
        WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
          AND e.event_kind = 'generation'
    ),
    (
        SELECT COUNT(*)
        FROM usage_events e, bounds b
        WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
          AND e.event_kind = 'generation'
          AND e.outcome IN ('unavailable', 'invalid_output')
    ),
    :computed_at
FROM bounds
ON CONFLICT (day) DO UPDATE SET
    active_users = EXCLUDED.active_users,
    appeals = EXCLUDED.appeals,
    new_users = EXCLUDED.new_users,
    generations = EXCLUDED.generations,
    generation_errors = EXCLUDED.generation_errors,
    computed_at = EXCLUDED.computed_at
""".replace("__APPEAL__", _APPEAL)

_SCENARIO_INSERT = """
WITH bounds AS (
    SELECT
        timezone(:tz, CAST(:day AS timestamp without time zone)) AS start_ts,
        timezone(:tz, CAST(:day AS timestamp without time zone) + interval '1 day') AS end_ts
),
inserted AS (
    INSERT INTO analytics_daily_scenario (
        day, scenario, surface, appeals, users,
        ok, refused, screened, invalid_output, unavailable, chosen,
        latency_p50_ms, latency_p95_ms, ttfc_p50_ms, ttfc_p95_ms,
        input_tokens, output_tokens, billable_tokens
    )
    SELECT
        :day,
        e.scenario,
        e.surface,
        COUNT(*) FILTER (WHERE __APPEAL__),
        COUNT(DISTINCT e.user_pseudonym) FILTER (WHERE __APPEAL__),
        COUNT(*) FILTER (WHERE e.event_kind = 'generation' AND e.outcome = 'ok'),
        COUNT(*) FILTER (WHERE e.event_kind = 'generation' AND e.outcome = 'refused'),
        COUNT(*) FILTER (WHERE e.event_kind = 'generation' AND e.outcome = 'screened'),
        COUNT(*) FILTER (
            WHERE e.event_kind = 'generation' AND e.outcome = 'invalid_output'
        ),
        COUNT(*) FILTER (
            WHERE e.event_kind = 'generation' AND e.outcome = 'unavailable'
        ),
        COUNT(*) FILTER (WHERE e.event_kind = 'result_chosen'),
        percentile_cont(0.5) WITHIN GROUP (ORDER BY e.latency_ms)
            FILTER (WHERE e.event_kind = 'generation' AND e.outcome = 'ok'),
        percentile_cont(0.95) WITHIN GROUP (ORDER BY e.latency_ms)
            FILTER (WHERE e.event_kind = 'generation' AND e.outcome = 'ok'),
        percentile_cont(0.5) WITHIN GROUP (ORDER BY e.ttfc_ms)
            FILTER (
                WHERE e.event_kind = 'generation' AND e.outcome = 'ok' AND e.ttfc_ms IS NOT NULL
            ),
        percentile_cont(0.95) WITHIN GROUP (ORDER BY e.ttfc_ms)
            FILTER (
                WHERE e.event_kind = 'generation' AND e.outcome = 'ok' AND e.ttfc_ms IS NOT NULL
            ),
        COALESCE(SUM(e.input_tokens) FILTER (WHERE e.event_kind = 'generation'), 0),
        COALESCE(SUM(e.output_tokens) FILTER (WHERE e.event_kind = 'generation'), 0),
        COALESCE(SUM(e.billable_tokens) FILTER (WHERE e.event_kind = 'generation'), 0)
    FROM usage_events e, bounds b
    WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts
    GROUP BY e.scenario, e.surface
    RETURNING 1
)
SELECT COUNT(*) FROM inserted
""".replace("__APPEAL__", _APPEAL)

_COHORT_UPSERT = """
WITH first_appeals AS (
    SELECT DISTINCT ON (user_pseudonym)
        user_pseudonym,
        occurred_at
    FROM usage_events e
    WHERE __APPEAL__
    ORDER BY user_pseudonym, occurred_at
),
members AS (
    SELECT
        user_pseudonym,
        (occurred_at AT TIME ZONE :tz)::date AS cohort_day
    FROM first_appeals
    WHERE (occurred_at AT TIME ZONE :tz)::date BETWEEN :from_day AND :to_day
),
appeal_days AS (
    SELECT DISTINCT
        user_pseudonym,
        (occurred_at AT TIME ZONE :tz)::date AS d
    FROM usage_events e
    WHERE __APPEAL__
),
per_member AS (
    SELECT
        m.cohort_day,
        EXISTS (
            SELECT 1 FROM appeal_days a
            WHERE a.user_pseudonym = m.user_pseudonym AND a.d = m.cohort_day + 1
        ) AS has_d1,
        EXISTS (
            SELECT 1 FROM appeal_days a
            WHERE a.user_pseudonym = m.user_pseudonym AND a.d = m.cohort_day + 7
        ) AS has_d7
    FROM members m
),
upserted AS (
    INSERT INTO analytics_cohorts (cohort_day, size, d1_retained, d7_retained, computed_at)
    SELECT
        cohort_day,
        COUNT(*),
        CASE
            WHEN cohort_day + 1 <= :as_of THEN COUNT(*) FILTER (WHERE has_d1)
        END,
        CASE
            WHEN cohort_day + 7 <= :as_of THEN COUNT(*) FILTER (WHERE has_d7)
        END,
        :computed_at
    FROM per_member
    GROUP BY cohort_day
    ON CONFLICT (cohort_day) DO UPDATE SET
        size = EXCLUDED.size,
        d1_retained = EXCLUDED.d1_retained,
        d7_retained = EXCLUDED.d7_retained,
        computed_at = EXCLUDED.computed_at
    RETURNING 1
)
SELECT COUNT(*) FROM upserted
""".replace("__APPEAL__", _APPEAL)

_PURGE_BATCH = """
WITH doomed AS (
    SELECT ctid
    FROM usage_events
    WHERE occurred_at < (CAST(:now AS timestamptz) - interval '13 months')
    LIMIT :batch
)
DELETE FROM usage_events AS e
USING doomed
WHERE e.ctid = doomed.ctid
RETURNING 1
"""
