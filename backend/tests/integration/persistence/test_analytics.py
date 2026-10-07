"""Oracle parity, cohorts, purge, lock, catch-up, and column allowlist."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.application.errors import (
    AnalyticsErrorKind,
    AnalyticsJobFailed,
    AnalyticsJobName,
    AnalyticsJobStatus,
)
from svoi_pravila.application.ports.analytics_store import JobRun
from svoi_pravila.application.use_cases.run_daily_analytics import (
    RunDailyAnalytics,
    RunDailyAnalyticsPorts,
)
from svoi_pravila.domain.analytics import aggregate, cohorts, event_day
from svoi_pravila.domain.enums import (
    Firmness,
    LimitKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.usage import UsageEvent
from tests.fakes.clock import FakeClock
from tests.fakes.ids import FakeIdGenerator

TZ = "Europe/Moscow"
COMPUTED = datetime(2026, 3, 17, 0, 30, tzinfo=UTC)


def _pseudo(n: int) -> str:
    return f"{n:064x}"


def _gen(
    event_id: int,
    when: datetime,
    user: str,
    *,
    scenario: UsageScenario = UsageScenario.DECODE,
    surface: UsageSurface = UsageSurface.DM,
    outcome: UsageOutcome = UsageOutcome.OK,
    latency_ms: int = 10,
    ttfc_ms: int | None = 2,
    tokens: int = 1,
) -> UsageEvent:
    if outcome is UsageOutcome.SCREENED:
        return UsageEvent(
            id=UsageEventId(UUID(int=event_id)),
            occurred_at=when,
            user_pseudonym=user,
            scenario=scenario,
            surface=surface,
            outcome=outcome,
            unavailable_kind=None,
            safety="crisis",
            model=None,
            prompt_version=None,
            latency_ms=latency_ms,
            ttfc_ms=None,
            attempts=0,
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
        )
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=user,
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        unavailable_kind=None,
        safety="ok",
        model="m",
        prompt_version="v1",
        latency_ms=latency_ms,
        ttfc_ms=ttfc_ms,
        attempts=1,
        input_tokens=tokens,
        output_tokens=tokens,
        billable_tokens=tokens * 2,
    )


def _chosen(event_id: int, when: datetime, user: str, scenario: UsageScenario) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=user,
        scenario=scenario,
        surface=UsageSurface.INLINE,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety=None,
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
        event_kind=UsageEventKind.RESULT_CHOSEN,
        variant_firmness=Firmness.BALANCED,
    )


def _limited(
    event_id: int,
    when: datetime,
    user: str,
    *,
    limit_kind: LimitKind,
    surface: UsageSurface = UsageSurface.DM,
) -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=user,
        scenario=UsageScenario.DECODE,
        surface=surface,
        outcome=UsageOutcome.LIMITED,
        unavailable_kind=None,
        safety=None,
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
        limit_kind=limit_kind,
    )


def _dataset() -> tuple[UsageEvent, ...]:
    before = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
    t_a = datetime(2026, 3, 15, 20, 59, 59, tzinfo=UTC)
    t_b = datetime(2026, 3, 15, 21, 0, 0, tzinfo=UTC)
    u1, u2, u3, u4, u5 = _pseudo(1), _pseudo(2), _pseudo(3), _pseudo(4), _pseudo(5)
    return (
        _gen(1, before, u1),
        _gen(2, t_a, u1),
        _gen(3, t_a, u2, surface=UsageSurface.MINIAPP, latency_ms=20, ttfc_ms=5, tokens=3),
        _gen(4, t_a, u3, surface=UsageSurface.INLINE, latency_ms=100, ttfc_ms=40),
        _chosen(5, t_a, u3, UsageScenario.SOFTEN),
        _gen(6, t_a, u4, outcome=UsageOutcome.REFUSED),
        _gen(7, t_a, u4, outcome=UsageOutcome.SCREENED, latency_ms=0),
        _gen(8, t_a, u4, outcome=UsageOutcome.INVALID_OUTPUT),
        _gen(9, t_a, u4, outcome=UsageOutcome.UNAVAILABLE),
        _gen(10, t_a, u4, scenario=UsageScenario.SUGGEST_RULE),
        _gen(11, t_b, u2, scenario=UsageScenario.HELP_SAY, latency_ms=30, ttfc_ms=8),
        _chosen(12, t_b, u2, UsageScenario.HELP_SAY),
        _limited(13, t_a, u5, limit_kind=LimitKind.USER_QUOTA),
        _limited(14, t_a, u5, limit_kind=LimitKind.GLOBAL_BUDGET, surface=UsageSurface.MINIAPP),
    )


async def _insert(uow_factory: SqlAlchemyUnitOfWorkFactory, events: tuple[UsageEvent, ...]) -> None:
    async with uow_factory() as uow:
        for event in events:
            await uow.usage_events.add(event)
        await uow.commit()


@pytest.mark.integration
async def test_aggregate_tables_column_allowlist(engine: AsyncEngine) -> None:
    expected = {
        "analytics_daily": {
            "day",
            "active_users",
            "appeals",
            "new_users",
            "generations",
            "generation_errors",
            "computed_at",
        },
        "analytics_daily_scenario": {
            "day",
            "scenario",
            "surface",
            "appeals",
            "users",
            "ok",
            "refused",
            "screened",
            "invalid_output",
            "unavailable",
            "chosen",
            "latency_p50_ms",
            "latency_p95_ms",
            "ttfc_p50_ms",
            "ttfc_p95_ms",
            "input_tokens",
            "output_tokens",
            "billable_tokens",
        },
        "analytics_cohorts": {
            "cohort_day",
            "size",
            "d1_retained",
            "d7_retained",
            "computed_at",
        },
        "job_runs": {
            "id",
            "job",
            "target_day",
            "started_at",
            "finished_at",
            "status",
            "rows_affected",
            "error_kind",
        },
    }
    async with engine.connect() as conn:
        for table, allow in expected.items():
            cols = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t AND table_schema = current_schema()"
                        ),
                        {"t": table},
                    )
                )
                .scalars()
                .all()
            )
            assert cols == allow
            assert "user_pseudonym" not in cols
            assert not any("telegram" in name or "pseudonym" in name for name in cols)


@pytest.mark.integration
async def test_oracle_parity_every_field(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    events = _dataset()
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    for day in (date(2026, 3, 15), date(2026, 3, 16)):
        await store.compute_day(day, TZ, COMPUTED)
        got = await store.fetch_day(day)
        want = aggregate(events, TZ, day, COMPUTED)
        assert got is not None
        assert got.daily.active_users == want.daily.active_users
        assert got.daily.appeals == want.daily.appeals
        assert got.daily.new_users == want.daily.new_users
        assert got.daily.generations == want.daily.generations
        assert got.daily.generation_errors == want.daily.generation_errors
        assert got.daily.day == want.daily.day
        assert len(got.scenarios) == len(want.scenarios)
        for left, right in zip(got.scenarios, want.scenarios, strict=True):
            assert left.scenario is right.scenario
            assert left.surface is right.surface
            assert left.appeals == right.appeals
            assert left.users == right.users
            assert left.ok == right.ok
            assert left.refused == right.refused
            assert left.screened == right.screened
            assert left.invalid_output == right.invalid_output
            assert left.unavailable == right.unavailable
            assert left.chosen == right.chosen
            assert left.input_tokens == right.input_tokens
            assert left.output_tokens == right.output_tokens
            assert left.billable_tokens == right.billable_tokens
            assert left.latency_p50_ms == right.latency_p50_ms
            assert left.latency_p95_ms == right.latency_p95_ms
            assert left.ttfc_p50_ms == right.ttfc_p50_ms
            assert left.ttfc_p95_ms == right.ttfc_p95_ms
        await store.compute_cohorts(day, day, TZ, date(2026, 3, 23), COMPUTED)
        cohort_rows = await store.fetch_cohorts(day, day)
        if got.daily.new_users == 0:
            assert cohort_rows == ()
        else:
            assert len(cohort_rows) == 1
            assert cohort_rows[0].size == got.daily.new_users


@pytest.mark.integration
async def test_compute_day_rolls_back_when_scenario_insert_fails(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import svoi_pravila.adapters.persistence.analytics_store as store_mod

    day = date(2026, 3, 16)
    events = (_gen(1, datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC), _pseudo(1)),)
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    await store.compute_day(day, TZ, COMPUTED)
    before = await store.fetch_day(day)
    assert before is not None
    monkeypatch.setattr(store_mod, "_SCENARIO_INSERT", "SELECT 1/0")
    with pytest.raises(AnalyticsJobFailed) as exc:
        await store.compute_day(day, TZ, COMPUTED)
    assert exc.value.kind is AnalyticsErrorKind.DATABASE
    after = await store.fetch_day(day)
    assert after == before


@pytest.mark.integration
async def test_compute_day_new_day_absent_after_mid_failure(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import svoi_pravila.adapters.persistence.analytics_store as store_mod

    day = date(2026, 3, 16)
    events = (_gen(1, datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC), _pseudo(1)),)
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    monkeypatch.setattr(
        store_mod,
        "_SCENARIO_INSERT",
        "INSERT INTO analytics_daily_scenario (day) VALUES (:day)",
    )
    with pytest.raises(AnalyticsJobFailed) as exc:
        await store.compute_day(day, TZ, COMPUTED)
    assert exc.value.kind is AnalyticsErrorKind.DATABASE
    assert await store.fetch_day(day) is None


@pytest.mark.integration
async def test_second_instance_closes_stale_running_then_proceeds(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    events = (_gen(1, datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC), _pseudo(1)),)
    await _insert(uow_factory, events)
    store_a = SqlAlchemyAnalyticsStore(engine)
    stale_id = UUID(int=90_001)
    await store_a.record_run(
        JobRun(
            id=stale_id,
            job=AnalyticsJobName.PURGE,
            target_day=None,
            started_at=COMPUTED,
            finished_at=None,
            status=AnalyticsJobStatus.RUNNING,
            rows_affected=0,
            error_kind=None,
        )
    )
    await store_a.record_run(
        JobRun(
            id=UUID(int=90_002),
            job=AnalyticsJobName.DAILY_AGGREGATES,
            target_day=date(2026, 3, 15),
            started_at=COMPUTED,
            finished_at=COMPUTED,
            status=AnalyticsJobStatus.SUCCEEDED,
            rows_affected=1,
            error_kind=None,
        )
    )
    store_b = SqlAlchemyAnalyticsStore(engine)
    job_b = RunDailyAnalytics(
        RunDailyAnalyticsPorts(
            store=store_b,
            ids=FakeIdGenerator(),
            clock=FakeClock(COMPUTED),
            timezone=TZ,
        )
    )
    await job_b.execute(COMPUTED)
    runs = {item.id: item for item in await store_b.fetch_job_runs()}
    assert runs[stale_id].status is AnalyticsJobStatus.FAILED
    assert runs[stale_id].error_kind is AnalyticsErrorKind.CANCELLED
    assert await store_b.fetch_day(date(2026, 3, 16)) is not None


@pytest.mark.integration
async def test_cohorts_null_until_complete_then_recompute(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    c = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    events = (
        _gen(1, c, _pseudo(1)),
        _gen(2, c + timedelta(days=1), _pseudo(1)),
        _gen(3, c + timedelta(days=7), _pseudo(1)),
        _gen(4, c, _pseudo(2)),
    )
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    await store.compute_cohorts(date(2026, 3, 1), date(2026, 3, 1), TZ, date(2026, 3, 1), COMPUTED)
    incomplete = await store.fetch_cohorts(date(2026, 3, 1), date(2026, 3, 1))
    assert incomplete == cohorts(
        events, TZ, date(2026, 3, 1), date(2026, 3, 1), date(2026, 3, 1), COMPUTED
    )
    await store.compute_cohorts(date(2026, 3, 1), date(2026, 3, 1), TZ, date(2026, 3, 8), COMPUTED)
    complete = await store.fetch_cohorts(date(2026, 3, 1), date(2026, 3, 1))
    assert complete[0].d1_retained == 1
    assert complete[0].d7_retained == 1


@pytest.mark.integration
async def test_purge_boundary_batches_leave_aggregates(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    store = SqlAlchemyAnalyticsStore(engine)
    async with engine.connect() as conn:
        cutoff = (
            await conn.execute(
                text("SELECT CAST(:now AS timestamptz) - interval '13 months'"),
                {"now": now},
            )
        ).scalar_one()
    kept_at = cutoff + timedelta(seconds=1)
    gone_at = cutoff - timedelta(seconds=1)
    await _insert(
        uow_factory,
        (
            _gen(1, kept_at, _pseudo(1)),
            _gen(2, gone_at, _pseudo(2)),
            _gen(3, gone_at - timedelta(seconds=1), _pseudo(3)),
        ),
    )
    kept_day = event_day(kept_at, TZ)
    await store.compute_day(kept_day, TZ, now)
    snapshot = await store.fetch_day(kept_day)
    assert snapshot is not None
    deleted = await store.purge_usage_events(now, 1)
    assert deleted == 2
    assert await store.count_usage_events_older_than(now) == 0
    after = await store.fetch_day(kept_day)
    assert after == snapshot
    async with uow_factory() as uow:
        assert await uow.usage_events.get(UsageEventId(UUID(int=1))) is not None
        assert await uow.usage_events.get(UsageEventId(UUID(int=2))) is None
        assert await uow.usage_events.get(UsageEventId(UUID(int=3))) is None


@pytest.mark.integration
async def test_idempotent_daily_job_two_runs(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    events = (_gen(1, datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC), _pseudo(1)),)
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    ids = FakeIdGenerator()
    await store.record_run(
        JobRun(
            id=ids.new_id(),
            job=AnalyticsJobName.DAILY_AGGREGATES,
            target_day=date(2026, 3, 15),
            started_at=COMPUTED,
            finished_at=COMPUTED,
            status=AnalyticsJobStatus.SUCCEEDED,
            rows_affected=1,
            error_kind=None,
        )
    )
    job = RunDailyAnalytics(
        RunDailyAnalyticsPorts(store=store, ids=ids, clock=FakeClock(COMPUTED), timezone=TZ)
    )
    await job.execute(COMPUTED)
    first = await store.fetch_day(date(2026, 3, 16))
    await job.execute(COMPUTED)
    second = await store.fetch_day(date(2026, 3, 16))
    assert first is not None
    assert first == second
    daily_ok = [
        item
        for item in await store.fetch_job_runs()
        if item.job is AnalyticsJobName.DAILY_AGGREGATES
        and item.target_day == date(2026, 3, 16)
        and item.status is AnalyticsJobStatus.SUCCEEDED
    ]
    assert len(daily_ok) == 2


@pytest.mark.integration
async def test_catch_up_three_days_and_cap(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    now = datetime(2026, 5, 10, 0, 30, tzinfo=UTC)
    yesterday = date(2026, 5, 9)
    events = tuple(
        _gen(i, datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC) + timedelta(days=i), _pseudo(i))
        for i in range(1, 5)
    )
    await _insert(uow_factory, events)
    store = SqlAlchemyAnalyticsStore(engine)
    ids = FakeIdGenerator()
    await store.record_run(
        JobRun(
            id=ids.new_id(),
            job=AnalyticsJobName.DAILY_AGGREGATES,
            target_day=yesterday - timedelta(days=3),
            started_at=now,
            finished_at=now,
            status=AnalyticsJobStatus.SUCCEEDED,
            rows_affected=1,
            error_kind=None,
        )
    )
    job = RunDailyAnalytics(
        RunDailyAnalyticsPorts(store=store, ids=ids, clock=FakeClock(now), timezone=TZ)
    )
    await job.execute(now)
    filled = [
        item.target_day
        for item in await store.fetch_job_runs()
        if item.job is AnalyticsJobName.DAILY_AGGREGATES
        and item.status is AnalyticsJobStatus.SUCCEEDED
        and item.target_day is not None
        and item.target_day > yesterday - timedelta(days=3)
    ]
    assert set(filled) == {
        yesterday - timedelta(days=2),
        yesterday - timedelta(days=1),
        yesterday,
    }

    store2 = SqlAlchemyAnalyticsStore(engine)
    ids2 = FakeIdGenerator()
    job2 = RunDailyAnalytics(
        RunDailyAnalyticsPorts(store=store2, ids=ids2, clock=FakeClock(now), timezone=TZ)
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM job_runs"))
    await job2.execute(now)
    capped = [
        item
        for item in await store2.fetch_job_runs()
        if item.error_kind is AnalyticsErrorKind.CATCH_UP_CAPPED
    ]
    assert capped
    daily_days = [
        item.target_day
        for item in await store2.fetch_job_runs()
        if item.job is AnalyticsJobName.DAILY_AGGREGATES
        and item.status is AnalyticsJobStatus.SUCCEEDED
        and item.target_day is not None
    ]
    assert len(daily_days) == 35
    assert min(daily_days) == yesterday - timedelta(days=34)
    assert max(daily_days) == yesterday


@pytest.mark.integration
async def test_unknown_timezone_typed(
    engine: AsyncEngine,
) -> None:
    store = SqlAlchemyAnalyticsStore(engine)
    with pytest.raises(AnalyticsJobFailed) as exc:
        await store.ensure_timezone("NotAZone")
    assert exc.value.kind is AnalyticsErrorKind.UNKNOWN_TIMEZONE


@pytest.mark.integration
@pytest.mark.usefixtures("uow_factory")
async def test_lock_second_run_skipped(
    engine: AsyncEngine,
) -> None:
    store = SqlAlchemyAnalyticsStore(engine)
    clock = FakeClock(COMPUTED)
    job = RunDailyAnalytics(
        RunDailyAnalyticsPorts(store=store, ids=FakeIdGenerator(), clock=clock, timezone=TZ)
    )
    held = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with store.hold_lock() as acquired:
            assert acquired is True
            held.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await held.wait()
    await job.execute(COMPUTED)
    release.set()
    await holder
    skipped = [
        item
        for item in await store.fetch_job_runs()
        if item.status is AnalyticsJobStatus.SKIPPED_LOCKED
    ]
    assert len(skipped) == 1


@pytest.mark.integration
@pytest.mark.usefixtures("uow_factory")
async def test_close_open_runs_marks_running_failed(
    engine: AsyncEngine,
) -> None:
    store = SqlAlchemyAnalyticsStore(engine)
    ids = FakeIdGenerator()
    await store.record_run(
        JobRun(
            id=ids.new_id(),
            job=AnalyticsJobName.PURGE,
            target_day=None,
            started_at=COMPUTED,
            finished_at=None,
            status=AnalyticsJobStatus.RUNNING,
            rows_affected=0,
            error_kind=None,
        )
    )
    closed = await store.close_open_runs(AnalyticsErrorKind.CANCELLED, COMPUTED)
    assert closed == 1
    rows = await store.fetch_job_runs()
    assert rows[0].status is AnalyticsJobStatus.FAILED
    assert rows[0].error_kind is AnalyticsErrorKind.CANCELLED
    assert rows[0].finished_at == COMPUTED
