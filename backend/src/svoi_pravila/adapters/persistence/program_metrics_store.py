"""Postgres reader for stored program-metrics aggregates and the LLM price table."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobFailed
from svoi_pravila.domain.analytics import (
    CohortTotals,
    DailyTotals,
    LlmModelTotals,
    LoadPeaks,
    ScenarioTotals,
)
from svoi_pravila.domain.enums import UsageScenario, UsageSurface
from svoi_pravila.domain.llm_pricing import LlmPrice

_DAILY = "SELECT * FROM analytics_daily WHERE day BETWEEN :from_day AND :to_day ORDER BY day"
_SCENARIOS = (
    "SELECT * FROM analytics_daily_scenario WHERE day BETWEEN :from_day AND :to_day "
    "ORDER BY day, scenario, surface"
)
_LLM = (
    "SELECT * FROM analytics_daily_llm WHERE day BETWEEN :from_day AND :to_day "
    "ORDER BY day, scenario, surface, model"
)
_LOAD = "SELECT * FROM analytics_daily_load WHERE day BETWEEN :from_day AND :to_day ORDER BY day"
_COHORTS = (
    "SELECT * FROM analytics_cohorts WHERE cohort_day BETWEEN :from_day AND :to_day "
    "ORDER BY cohort_day"
)


class SqlAlchemyProgramMetricsStore:
    """``ProgramMetricsReader`` and ``LlmPriceStore`` over a shared async engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def fetch_daily(self, from_day: date, to_day: date) -> tuple[DailyTotals, ...]:
        """``analytics_daily`` rows ordered by day."""
        rows = await self._range(_DAILY, from_day, to_day)
        return tuple(
            DailyTotals(
                day=item.day,
                active_users=item.active_users,
                appeals=item.appeals,
                new_users=item.new_users,
                generations=item.generations,
                generation_errors=item.generation_errors,
                users_limited=item.users_limited,
                billable_tokens=int(item.billable_tokens),
                llm_budget_tokens=int(item.llm_budget_tokens),
                llm_calls=int(item.llm_calls),
                target_users_total=item.target_users_total,
                llm_cost_rub=_decimal(item.llm_cost_rub),
                unpriced_models=item.unpriced_models,
                computed_at=item.computed_at,
            )
            for item in rows
        )

    async def fetch_scenarios(self, from_day: date, to_day: date) -> tuple[ScenarioTotals, ...]:
        """``analytics_daily_scenario`` rows ordered by day, scenario, surface."""
        rows = await self._range(_SCENARIOS, from_day, to_day)
        return tuple(
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
                limited_user_quota=item.limited_user_quota,
                limited_global_budget=item.limited_global_budget,
                latency_p50_ms=item.latency_p50_ms,
                latency_p95_ms=item.latency_p95_ms,
                ttfc_p50_ms=item.ttfc_p50_ms,
                ttfc_p95_ms=item.ttfc_p95_ms,
                input_tokens=int(item.input_tokens),
                output_tokens=int(item.output_tokens),
                billable_tokens=int(item.billable_tokens),
            )
            for item in rows
        )

    async def fetch_llm(self, from_day: date, to_day: date) -> tuple[LlmModelTotals, ...]:
        """``analytics_daily_llm`` rows ordered by day, scenario, surface, model."""
        rows = await self._range(_LLM, from_day, to_day)
        return tuple(
            LlmModelTotals(
                day=item.day,
                scenario=UsageScenario(item.scenario),
                surface=UsageSurface(item.surface),
                model=item.model,
                generations=item.generations,
                llm_calls=int(item.llm_calls),
                input_tokens=int(item.input_tokens),
                output_tokens=int(item.output_tokens),
                billable_tokens=int(item.billable_tokens),
                price_rub_per_1k=_decimal_or_none(item.price_rub_per_1k),
                cost_rub=_decimal_or_none(item.cost_rub),
            )
            for item in rows
        )

    async def fetch_load(self, from_day: date, to_day: date) -> tuple[LoadPeaks, ...]:
        """``analytics_daily_load`` rows ordered by day."""
        rows = await self._range(_LOAD, from_day, to_day)
        return tuple(
            LoadPeaks(
                day=item.day,
                peak_tpm=int(item.peak_tpm),
                peak_tps=int(item.peak_tps),
                peak_llm_calls_per_minute=int(item.peak_llm_calls_per_minute),
                peak_llm_calls_per_second=int(item.peak_llm_calls_per_second),
                peak_tpm_minute=item.peak_tpm_minute,
            )
            for item in rows
        )

    async def fetch_cohorts(self, from_day: date, to_day: date) -> tuple[CohortTotals, ...]:
        """``analytics_cohorts`` rows whose cohort day is in the range, ordered by day."""
        rows = await self._range(_COHORTS, from_day, to_day)
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

    async def list_prices(self) -> tuple[LlmPrice, ...]:
        """All prices ordered by model, then ``valid_from``."""
        rows = await self._query(
            "SELECT model, valid_from, rub_per_1k_tokens FROM llm_prices "
            "ORDER BY model, valid_from",
            {},
        )
        return tuple(
            LlmPrice(
                model=item.model,
                valid_from=item.valid_from,
                rub_per_1k_tokens=_decimal(item.rub_per_1k_tokens),
            )
            for item in rows
        )

    async def upsert_price(self, price: LlmPrice) -> None:
        """Insert or replace the row keyed by ``(model, valid_from)``."""
        await self._query(
            "INSERT INTO llm_prices (model, valid_from, rub_per_1k_tokens) "
            "VALUES (:model, :valid_from, :price) "
            "ON CONFLICT (model, valid_from) DO UPDATE SET "
            "rub_per_1k_tokens = EXCLUDED.rub_per_1k_tokens",
            {
                "model": price.model,
                "valid_from": price.valid_from,
                "price": price.rub_per_1k_tokens,
            },
            write=True,
        )

    async def _range(self, sql: str, from_day: date, to_day: date) -> list[Any]:
        return await self._query(sql, {"from_day": from_day, "to_day": to_day})

    async def _query(
        self, sql: str, params: dict[str, object], *, write: bool = False
    ) -> list[Any]:
        try:
            if write:
                async with self._engine.begin() as conn:
                    await conn.execute(text(sql), params)
                return []
            async with self._engine.connect() as conn:
                return list((await conn.execute(text(sql), params)).all())
        except OSError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.NETWORK) from exc
        except SQLAlchemyError as exc:
            raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE) from exc


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    raise AnalyticsJobFailed(AnalyticsErrorKind.DATABASE)


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)
