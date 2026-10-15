"""LLM price table: validation, effective price lookup, exact cost and rounding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.llm_pricing import (
    PRICE_QUANTUM,
    LlmPrice,
    effective_price,
    llm_cost,
    round_half_up,
)

_M = "model-a"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "price"),
    [
        ("", Decimal("1.0000")),
        (_M, Decimal(0)),
        (_M, Decimal("-0.0001")),
        (_M, Decimal("0.00001")),
        (_M, Decimal("1000000.0000")),
        (_M, Decimal("NaN")),
        (_M, Decimal("Infinity")),
    ],
)
def test_price_rejects_invalid_values(model: str, price: Decimal) -> None:
    with pytest.raises(InvalidValueError):
        LlmPrice(model, date(2026, 1, 1), price)


@pytest.mark.unit
def test_price_accepts_bounds() -> None:
    assert LlmPrice(_M, date(2026, 1, 1), Decimal("0.0001")).rub_per_1k_tokens == Decimal("0.0001")
    assert LlmPrice(_M, date(2026, 1, 1), Decimal("999999.9999")).model == _M
    assert LlmPrice(_M, date(2026, 1, 1), Decimal(2)).rub_per_1k_tokens == Decimal(2)


@pytest.mark.unit
def test_effective_price_picks_latest_valid_from_not_after_day() -> None:
    prices = (
        LlmPrice(_M, date(2026, 3, 1), Decimal("0.0650")),
        LlmPrice(_M, date(2026, 3, 16), Decimal("0.0800")),
        LlmPrice("other", date(2026, 3, 10), Decimal("9.0000")),
    )
    assert effective_price(prices, _M, date(2026, 2, 28)) is None
    assert effective_price(prices, _M, date(2026, 3, 1)) == Decimal("0.0650")
    assert effective_price(prices, _M, date(2026, 3, 15)) == Decimal("0.0650")
    assert effective_price(prices, _M, date(2026, 3, 16)) == Decimal("0.0800")
    assert effective_price(prices, "missing", date(2026, 3, 16)) is None
    assert effective_price((), _M, date(2026, 3, 16)) is None


@pytest.mark.unit
def test_llm_cost_is_exact() -> None:
    assert llm_cost(1500, Decimal("0.0650")) == Decimal("0.0975")
    assert llm_cost(0, Decimal("0.5000")) == Decimal(0)
    assert llm_cost(1, Decimal("0.0001")) == Decimal("0.0000001")


@pytest.mark.unit
def test_round_half_up_goes_away_from_zero() -> None:
    assert round_half_up(Decimal("0.00005"), PRICE_QUANTUM) == Decimal("0.0001")
    assert round_half_up(Decimal("0.00004"), PRICE_QUANTUM) == Decimal("0.0000")
    assert round_half_up(Decimal("2.5"), Decimal(1)) == Decimal(3)
    assert round_half_up(Decimal("-2.5"), Decimal(1)) == Decimal(-3)
