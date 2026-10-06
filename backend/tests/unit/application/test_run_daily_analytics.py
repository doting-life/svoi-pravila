"""RunDailyAnalytics catch-up, cap, lock skip, and step failure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobFailed,
    AnalyticsJobName,
    AnalyticsJobStatus,
)
from svoi_pravila.application.use_cases.run_daily_analytics import (
    CATCH_UP_MAX_DAYS,
    RunDailyAnalytics,
    RunDailyAnalyticsPorts,
)
from tests.fakes.analytics_store import FakeAnalyticsStore
from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIdGenerator

NOW = datetime(2026, 4, 10, 0, 30, tzinfo=UTC)
YESTERDAY = datetime(2026, 4, 9, tzinfo=UTC).date()


def _job() -> tuple[RunDailyAnalytics, FakeAnalyticsStore]:
    store = FakeAnalyticsStore()
    use_case = RunDailyAnalytics(
        RunDailyAnalyticsPorts(
            store=store,
            ids=FakeIdGenerator(),
            clock=FakeClock(NOW),
            timezone="Europe/Moscow",
        )
    )
    return use_case, store


@pytest.mark.unit
async def test_skipped_locked_records_and_does_nothing() -> None:
    use_case, store = _job()
    store.acquired = False
    await use_case.execute(NOW)
    assert store.compute_days == []
    assert store.purge_calls == []
    assert store.runs[0].status is AnalyticsJobStatus.SKIPPED_LOCKED


@pytest.mark.unit
async def test_unknown_timezone_raises_before_compute() -> None:
    use_case, store = _job()
    store.timezone_ok = False
    with pytest.raises(AnalyticsJobFailed) as exc:
        await use_case.execute(NOW)
    assert exc.value.kind is AnalyticsErrorKind.UNKNOWN_TIMEZONE
    assert store.compute_days == []


@pytest.mark.unit
async def test_empty_store_skips_catch_up_but_runs_cohorts_and_purge() -> None:
    use_case, store = _job()
    await use_case.execute(NOW)
    assert store.compute_days == []
    assert len(store.cohort_calls) == 1
    assert store.purge_calls[0][0] == NOW


@pytest.mark.unit
async def test_catch_up_fills_three_day_gap() -> None:
    use_case, store = _job()
    store.last_day = YESTERDAY - timedelta(days=3)
    await use_case.execute(NOW)
    assert store.compute_days == [
        YESTERDAY - timedelta(days=2),
        YESTERDAY - timedelta(days=1),
        YESTERDAY,
    ]
    from_day, to_day, as_of = store.cohort_calls[0]
    assert as_of == YESTERDAY
    assert (YESTERDAY - from_day).days == 8
    assert (YESTERDAY - to_day).days == 1
    daily = [item for item in store.runs if item.job is AnalyticsJobName.DAILY_AGGREGATES]
    assert all(item.status is AnalyticsJobStatus.SUCCEEDED for item in daily)


@pytest.mark.unit
async def test_catch_up_from_earliest_when_no_last_completed() -> None:
    use_case, store = _job()
    store.earliest = YESTERDAY - timedelta(days=1)
    await use_case.execute(NOW)
    assert store.compute_days == [YESTERDAY - timedelta(days=1), YESTERDAY]


@pytest.mark.unit
async def test_catch_up_caps_at_35_days_and_records_kind() -> None:
    use_case, store = _job()
    store.earliest = YESTERDAY - timedelta(days=40)
    await use_case.execute(NOW)
    assert len(store.compute_days) == CATCH_UP_MAX_DAYS
    assert store.compute_days[0] == YESTERDAY - timedelta(days=CATCH_UP_MAX_DAYS - 1)
    assert store.compute_days[-1] == YESTERDAY
    first = next(
        item
        for item in store.runs
        if item.job is AnalyticsJobName.DAILY_AGGREGATES
        and item.status is AnalyticsJobStatus.SUCCEEDED
        and item.target_day == store.compute_days[0]
    )
    assert first.error_kind is AnalyticsErrorKind.CATCH_UP_CAPPED


@pytest.mark.unit
async def test_completed_through_yesterday_still_recomputes_yesterday() -> None:
    use_case, store = _job()
    store.last_day = YESTERDAY
    await use_case.execute(NOW)
    assert store.compute_days == [YESTERDAY]


@pytest.mark.unit
async def test_compute_failure_marks_running_row_failed() -> None:
    use_case, store = _job()
    store.earliest = YESTERDAY
    store.fail_compute = AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc:
        await use_case.execute(NOW)
    assert exc.value.kind is AnalyticsErrorKind.DATABASE
    assert exc.value.job is AnalyticsJobName.DAILY_AGGREGATES
    failed = [item for item in store.runs if item.status is AnalyticsJobStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].error_kind is AnalyticsErrorKind.DATABASE
