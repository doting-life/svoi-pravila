"""ListLlmPrices and SetLlmPrice (backfill range)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from svoi_pravila.application.use_cases.list_llm_prices import ListLlmPrices
from svoi_pravila.application.use_cases.set_llm_price import (
    SetLlmPrice,
    SetLlmPricePorts,
    SetLlmPriceResult,
)
from svoi_pravila.domain.llm_pricing import LlmPrice
from tests.fakes.clock import FakeClock
from tests.fakes.program_metrics import FakeLlmPriceStore

_M = "model-a"
# 00:30 on 2026-04-10 in Moscow is still 2026-04-09 21:30 UTC.
_NOW = datetime(2026, 4, 9, 21, 30, tzinfo=UTC)


def _set_price(store: FakeLlmPriceStore) -> SetLlmPrice:
    return SetLlmPrice(
        SetLlmPricePorts(store=store, clock=FakeClock(_NOW), timezone="Europe/Moscow")
    )


@pytest.mark.unit
async def test_list_returns_every_row_in_store_order() -> None:
    older = LlmPrice(_M, date(2026, 1, 1), Decimal("1.0000"))
    newer = LlmPrice(_M, date(2026, 2, 1), Decimal("2.0000"))
    store = FakeLlmPriceStore((newer, older))
    assert await ListLlmPrices(store).execute() == (older, newer)


@pytest.mark.unit
async def test_set_past_price_asks_for_backfill_until_yesterday_in_local_day() -> None:
    store = FakeLlmPriceStore()
    price = LlmPrice(_M, date(2026, 4, 1), Decimal("0.0700"))
    result = await _set_price(store).execute(price)
    assert result == SetLlmPriceResult(date(2026, 4, 1), date(2026, 4, 9))
    assert store.rows[(_M, date(2026, 4, 1))] == price


@pytest.mark.unit
async def test_set_price_replaces_the_row_with_the_same_key() -> None:
    store = FakeLlmPriceStore((LlmPrice(_M, date(2026, 4, 1), Decimal("0.0700")),))
    updated = LlmPrice(_M, date(2026, 4, 1), Decimal("0.0900"))
    await _set_price(store).execute(updated)
    assert await store.list_prices() == (updated,)


@pytest.mark.unit
async def test_set_price_backfill_stops_before_the_next_price_of_the_same_model() -> None:
    store = FakeLlmPriceStore(
        (
            LlmPrice(_M, date(2026, 4, 5), Decimal("0.0900")),
            LlmPrice("other", date(2026, 4, 3), Decimal("0.0100")),
        )
    )
    result = await _set_price(store).execute(LlmPrice(_M, date(2026, 4, 1), Decimal("0.0700")))
    assert result == SetLlmPriceResult(date(2026, 4, 1), date(2026, 4, 4))


@pytest.mark.unit
async def test_set_price_next_price_after_yesterday_does_not_shorten_the_range() -> None:
    store = FakeLlmPriceStore((LlmPrice(_M, date(2026, 5, 1), Decimal("0.0900")),))
    result = await _set_price(store).execute(LlmPrice(_M, date(2026, 4, 1), Decimal("0.0700")))
    assert result == SetLlmPriceResult(date(2026, 4, 1), date(2026, 4, 9))


@pytest.mark.unit
@pytest.mark.parametrize("valid_from", [date(2026, 4, 10), date(2026, 6, 1)])
async def test_set_future_or_today_price_needs_no_backfill(valid_from: date) -> None:
    result = await _set_price(FakeLlmPriceStore()).execute(
        LlmPrice(_M, valid_from, Decimal("0.0700"))
    )
    assert result == SetLlmPriceResult(None, None)
