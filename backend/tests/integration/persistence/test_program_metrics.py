"""Program metrics: SQL aggregates against the domain reference, prices, grants."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.adapters.persistence.program_metrics_store import SqlAlchemyProgramMetricsStore
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.system.csv_metrics_package import CsvMetricsPackageWriter
from svoi_pravila.application.use_cases.export_program_metrics import (
    ExportProgramMetrics,
    ExportProgramMetricsInput,
    ExportProgramMetricsPorts,
)
from svoi_pravila.domain.analytics import aggregate
from svoi_pravila.domain.llm_pricing import LlmPrice
from tests.fakes.clock import FakeClock
from tests.integration.persistence.program_metrics_seed import (
    COMPUTED,
    FIRST_DAY,
    LAST_DAY,
    LIGHT,
    LLM_BUDGET,
    PRICES,
    PRO,
    TZ,
    UNPRICED,
    dataset,
    insert_events,
    set_prices,
)

EXPECTED_DAILY_CSV = """\
day,dau,new_users,target_users_total,target_actions,target_actions_per_dau,generations,\
generation_errors,error_rate,users_limited,llm_calls,llm_calls_per_active_user,billable_tokens,\
llm_cost_rub,llm_cost_per_target_action_rub
2026-03-15,2,2,2,5,2.50,6,0,0.00,0,10,5.00,1300,0.12,0.02
2026-03-16,2,1,3,3,1.50,4,0,0.00,1,3,1.50,1500,0.54,0.18
2026-03-17,0,0,3,0,,1,0,0.00,1,0,,0,0.00,
2026-03-18,0,0,3,0,,0,0,,0,0,,0,0.00,
"""
EXPECTED_LOAD_CSV = """\
day,peak_tpm,peak_tps,peak_llm_calls_per_minute,peak_llm_calls_per_second,peak_tpm_minute,avg_tpm
2026-03-15,1000,1000,4,3,2026-03-15T20:59:00+00:00,0.90
2026-03-16,500,500,1,1,2026-03-15T21:00:00+00:00,1.04
2026-03-17,0,0,0,0,,0.00
2026-03-18,0,0,0,0,,0.00
"""


def _days() -> list[date]:
    return [FIRST_DAY + timedelta(days=offset) for offset in range((LAST_DAY - FIRST_DAY).days + 1)]


@pytest.mark.integration
async def test_sql_matches_domain_reference_every_day(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    events = dataset()
    await insert_events(uow_factory, events)
    await set_prices(engine)
    store = SqlAlchemyAnalyticsStore(engine)
    for day in _days():
        await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
        got = await store.fetch_day(day)
        want = aggregate(events, TZ, day, COMPUTED, LLM_BUDGET, PRICES)
        assert got is not None
        assert got.daily == want.daily
        assert got.scenarios == want.scenarios
        assert got.llm == want.llm
        assert got.load == want.load


@pytest.mark.integration
async def test_known_values_for_hand_checked_days(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    await insert_events(uow_factory, dataset())
    await set_prices(engine)
    store = SqlAlchemyAnalyticsStore(engine)
    for day in _days():
        await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    first = await store.fetch_day(date(2026, 3, 15))
    second = await store.fetch_day(date(2026, 3, 16))
    quiet = await store.fetch_day(date(2026, 3, 17))
    empty = await store.fetch_day(date(2026, 3, 18))
    assert first is not None
    assert second is not None
    assert quiet is not None
    assert empty is not None
    # Day 15: Lightning 100+40+1000 tokens at 0.0650, Pro 60+25 at 0.5, one unpriced model.
    assert first.daily.llm_calls == 3 + 2 + 1 + 1 + 2 + 1
    assert first.daily.llm_cost_rub == Decimal("0.0650") * Decimal("1.140") + Decimal(
        "0.5"
    ) * Decimal("0.085")
    assert first.daily.unpriced_models == 1
    assert first.load.peak_tpm == 1000
    assert first.load.peak_tps == 1000
    assert first.load.peak_llm_calls_per_minute == 4
    assert first.load.peak_llm_calls_per_second == 3
    # Day 16: the Lightning price changed to 0.0800; the tied peak minute is the earliest.
    lightning = [row for row in second.llm if row.model == LIGHT]
    assert [row.price_rub_per_1k for row in lightning] == [Decimal("0.0800")]
    assert second.load.peak_tpm == 500
    assert second.load.peak_tpm_minute is not None
    assert second.load.peak_tpm_minute.isoformat() == "2026-03-15T21:00:00+00:00"
    assert {row.model for row in second.llm} == {LIGHT, PRO}
    # No calls: zero peaks, no minute, no LLM rows, zero cost.
    assert quiet.llm == ()
    assert quiet.load.peak_tpm == 0
    assert quiet.load.peak_tpm_minute is None
    assert quiet.daily.llm_cost_rub == Decimal(0)
    assert empty.daily.active_users == 0
    assert empty.load.peak_tpm_minute is None
    assert UNPRICED in {row.model for row in first.llm}
    unpriced = [row for row in first.llm if row.model == UNPRICED]
    assert unpriced[0].cost_rub is None
    assert unpriced[0].price_rub_per_1k is None


@pytest.mark.integration
async def test_target_users_total_is_cumulative_first_appeal(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    await insert_events(uow_factory, dataset())
    await set_prices(engine)
    store = SqlAlchemyAnalyticsStore(engine)
    totals = []
    for day in _days():
        await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
        fetched = await store.fetch_day(day)
        assert fetched is not None
        totals.append(fetched.daily.target_users_total)
    assert totals == [2, 3, 3, 3]


@pytest.mark.integration
async def test_recompute_is_idempotent_and_follows_price_change(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    await insert_events(uow_factory, dataset())
    await set_prices(engine)
    store = SqlAlchemyAnalyticsStore(engine)
    day = date(2026, 3, 16)
    await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    before = await store.fetch_day(day)
    await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    assert await store.fetch_day(day) == before
    prices = SqlAlchemyProgramMetricsStore(engine)
    await prices.upsert_price(LlmPrice(LIGHT, date(2026, 3, 16), Decimal("0.1000")))
    await store.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    after = await store.fetch_day(day)
    assert before is not None
    assert after is not None
    assert after.daily.llm_cost_rub > before.daily.llm_cost_rub
    assert [row.price_rub_per_1k for row in after.llm if row.model == LIGHT] == [Decimal("0.1000")]


@pytest.mark.integration
async def test_prices_upsert_and_list(engine: AsyncEngine) -> None:
    await set_prices(engine, ())
    store = SqlAlchemyProgramMetricsStore(engine)
    first = LlmPrice(PRO, date(2026, 3, 1), Decimal("0.5000"))
    await store.upsert_price(first)
    await store.upsert_price(LlmPrice(PRO, date(2026, 3, 1), Decimal("0.7500")))
    await store.upsert_price(LlmPrice(LIGHT, date(2026, 3, 1), Decimal("0.0650")))
    assert await store.list_prices() == (
        LlmPrice(PRO, date(2026, 3, 1), Decimal("0.7500")),
        LlmPrice(LIGHT, date(2026, 3, 1), Decimal("0.0650")),
    )


@pytest.mark.integration
async def test_range_readers_return_stored_rows(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    await insert_events(uow_factory, dataset())
    await set_prices(engine)
    analytics = SqlAlchemyAnalyticsStore(engine)
    for day in _days():
        await analytics.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    await analytics.compute_cohorts(FIRST_DAY, LAST_DAY, TZ, date(2026, 3, 30), COMPUTED)
    reader = SqlAlchemyProgramMetricsStore(engine)
    assert [row.day for row in await reader.fetch_daily(FIRST_DAY, LAST_DAY)] == _days()
    assert [row.day for row in await reader.fetch_load(FIRST_DAY, LAST_DAY)] == _days()
    assert await reader.fetch_daily(date(2026, 4, 1), date(2026, 4, 2)) == ()
    assert len(await reader.fetch_llm(FIRST_DAY, LAST_DAY)) == 6
    assert len(await reader.fetch_scenarios(FIRST_DAY, LAST_DAY)) > 0
    assert len(await reader.fetch_cohorts(FIRST_DAY, LAST_DAY)) == 2


@pytest.mark.integration
async def test_aggregates_carry_no_pseudonyms(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name IN "
                        "('analytics_daily_llm', 'analytics_daily_load', 'llm_prices')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert columns
    assert not any("pseudonym" in name or "user" in name for name in columns)


@pytest.mark.integration
async def test_export_of_the_seeded_range_matches_hand_checked_csv(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    await insert_events(uow_factory, dataset())
    await set_prices(engine)
    analytics = SqlAlchemyAnalyticsStore(engine)
    for day in _days():
        await analytics.compute_day(day, TZ, COMPUTED, LLM_BUDGET)
    await analytics.compute_cohorts(FIRST_DAY, LAST_DAY, TZ, date(2026, 3, 30), COMPUTED)
    store = SqlAlchemyProgramMetricsStore(engine)
    exported = await ExportProgramMetrics(
        ExportProgramMetricsPorts(
            reader=store,
            prices=store,
            writer=CsvMetricsPackageWriter(),
            clock=FakeClock(COMPUTED),
        )
    ).execute(
        ExportProgramMetricsInput(
            from_day=FIRST_DAY, to_day=LAST_DAY, out_dir=tmp_path, force=False
        )
    )
    assert [item.name for item in exported] == [
        "00_definitions.md",
        "01_summary.csv",
        "02_daily.csv",
        "03_scenarios.csv",
        "04_llm_models.csv",
        "05_retention.csv",
        "06_load_peaks.csv",
    ]
    assert (tmp_path / "02_daily.csv").read_text(encoding="utf-8") == EXPECTED_DAILY_CSV
    assert (tmp_path / "06_load_peaks.csv").read_text(encoding="utf-8") == EXPECTED_LOAD_CSV
