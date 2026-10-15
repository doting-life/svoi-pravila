"""Shared synthetic event set and price table for program-metrics integration tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.domain.enums import (
    LimitKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.llm_pricing import LlmPrice
from svoi_pravila.domain.usage import UsageEvent

TZ = "Europe/Moscow"
COMPUTED = datetime(2026, 3, 20, 0, 30, tzinfo=UTC)
LLM_BUDGET = 100_000
LIGHT = "GigaChat-3-Lightning"
PRO = "GigaChat-2-Pro"
UNPRICED = "GigaChat-unpriced"

PRICES = (
    LlmPrice(LIGHT, date(2026, 3, 1), Decimal("0.0650")),
    LlmPrice(LIGHT, date(2026, 3, 16), Decimal("0.0800")),
    LlmPrice(PRO, date(2026, 3, 1), Decimal("0.5000")),
)

FIRST_DAY = date(2026, 3, 15)
LAST_DAY = date(2026, 3, 18)


def pseudo(n: int) -> str:
    """Synthetic 64-hex pseudonym."""
    return f"{n:064x}"


def call(
    event_id: int,
    when: datetime,
    user: str,
    *,
    model: str = LIGHT,
    scenario: UsageScenario = UsageScenario.DECODE,
    surface: UsageSurface = UsageSurface.DM,
    outcome: UsageOutcome = UsageOutcome.OK,
    attempts: int = 1,
    input_tokens: int = 10,
    output_tokens: int = 20,
    billable_tokens: int = 30,
) -> UsageEvent:
    """A generation event that made ``attempts`` provider calls."""
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=user,
        scenario=scenario,
        surface=surface,
        outcome=outcome,
        unavailable_kind=None,
        safety="ok",
        model=model,
        prompt_version="v1",
        latency_ms=10,
        ttfc_ms=2,
        attempts=attempts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billable_tokens=billable_tokens,
    )


def limited(event_id: int, when: datetime, user: str) -> UsageEvent:
    """A quota-limited generation event: no model, no calls."""
    return UsageEvent(
        id=UsageEventId(UUID(int=event_id)),
        occurred_at=when,
        user_pseudonym=user,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
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
        limit_kind=LimitKind.USER_QUOTA,
    )


def _utc(day: int, hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 3, day, hour, minute, second, tzinfo=UTC)


def dataset() -> tuple[UsageEvent, ...]:
    """Events covering retries, two-phase decode, an unpriced model, a price change,
    second/minute boundaries, a tied peak minute, a time-zone boundary, a day without
    calls and a day without events (Europe/Moscow, UTC+3).
    """
    u1, u2, u3 = pseudo(1), pseudo(2), pseudo(3)
    return (
        # Day 03-15 (Moscow): from 03-14T21:00Z to 03-15T21:00Z.
        call(1, _utc(14, 21, 0, 0), u1, billable_tokens=100, attempts=3),
        call(2, _utc(15, 12, 0, 59), u1, billable_tokens=40, attempts=2),
        call(3, _utc(15, 12, 1, 0), u2, billable_tokens=60, model=PRO, attempts=1),
        call(4, _utc(15, 12, 1, 0), u2, billable_tokens=25, model=PRO),
        call(5, _utc(15, 12, 1, 1), u2, billable_tokens=75, model=UNPRICED, attempts=2),
        call(
            6,
            _utc(15, 20, 59, 59),
            u3,
            surface=UsageSurface.INLINE,
            scenario=UsageScenario.REWRITE,
            billable_tokens=1_000,
            input_tokens=400,
            output_tokens=600,
        ),
        # Day 03-16 starts at 03-15T21:00:00Z: Lightning price changes to 0.0800.
        call(7, _utc(15, 21, 0, 0), u1, billable_tokens=500, attempts=1),
        call(8, _utc(16, 9, 30, 0), u3, billable_tokens=500, model=PRO),
        call(9, _utc(16, 9, 31, 0), u3, billable_tokens=500, model=PRO),
        limited(10, _utc(16, 9, 31, 30), pseudo(4)),
        # Day 03-17: only a limited event, no LLM calls.
        limited(11, _utc(17, 10, 0, 0), pseudo(4)),
        # Day 03-18: no events at all.
    )


async def insert_events(
    uow_factory: SqlAlchemyUnitOfWorkFactory, events: tuple[UsageEvent, ...]
) -> None:
    """Persist ``events`` through the usage-event repository."""
    async with uow_factory() as uow:
        for event in events:
            await uow.usage_events.add(event)
        await uow.commit()


async def set_prices(engine: AsyncEngine, prices: tuple[LlmPrice, ...] = PRICES) -> None:
    """Replace the price table with ``prices`` (the seed from the migration is cleared)."""
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM llm_prices"))
        for price in prices:
            await conn.execute(
                text(
                    "INSERT INTO llm_prices (model, valid_from, rub_per_1k_tokens) "
                    "VALUES (:model, :valid_from, :price)"
                ),
                {
                    "model": price.model,
                    "valid_from": price.valid_from,
                    "price": price.rub_per_1k_tokens,
                },
            )
