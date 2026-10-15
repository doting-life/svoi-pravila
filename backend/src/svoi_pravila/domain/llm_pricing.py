"""LLM price table: roubles per 1000 billable tokens, effective by calendar day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from svoi_pravila.domain.errors import InvalidValueError

PRICE_QUANTUM = Decimal("0.0001")
MAX_PRICE = Decimal("999999.9999")
_TOKENS_PER_PRICE_UNIT = Decimal(1000)


@dataclass(frozen=True, slots=True)
class LlmPrice:
    """Price of ``model`` from ``valid_from`` (inclusive) until a later row for the model."""

    model: str
    valid_from: date
    rub_per_1k_tokens: Decimal

    def __post_init__(self) -> None:
        """Require a named model and a positive price with at most four decimals."""
        if not self.model:
            msg = "model must be non-empty"
            raise InvalidValueError(msg)
        price = self.rub_per_1k_tokens
        if (
            not price.is_finite()
            or price <= 0
            or price > MAX_PRICE
            or price != price.quantize(PRICE_QUANTUM)
        ):
            msg = "price must be positive, at most 999999.9999, with at most 4 decimals"
            raise InvalidValueError(msg)


def effective_price(prices: tuple[LlmPrice, ...], model: str, day: date) -> Decimal | None:
    """Price of ``model`` on ``day``: the row with the greatest ``valid_from`` <= ``day``."""
    candidates = [item for item in prices if item.model == model and item.valid_from <= day]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.valid_from).rub_per_1k_tokens


def llm_cost(billable_tokens: int, price_rub_per_1k: Decimal) -> Decimal:
    """Exact cost in roubles of ``billable_tokens`` at ``price_rub_per_1k`` (no rounding)."""
    return Decimal(billable_tokens) * price_rub_per_1k / _TOKENS_PER_PRICE_UNIT


def round_half_up(value: Decimal, quantum: Decimal) -> Decimal:
    """Round ``value`` to ``quantum`` with halves away from zero (Postgres ``numeric``)."""
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
