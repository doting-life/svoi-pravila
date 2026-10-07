"""Cancellation accounting: refund before provider start; charge after."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from svoi_pravila.application.errors import CacheUnavailable
from svoi_pravila.application.ports.llm_budget import LlmBudget
from svoi_pravila.application.ports.quota_gate import QuotaGate, QuotaReservation

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CancelledGeneration:
    """Inputs for cancel-time quota/budget accounting."""

    provider_started: bool
    reservation: QuotaReservation | None
    quota_gate: QuotaGate | None
    llm_budget: LlmBudget
    day: date
    billable_tokens: int


async def charge_cancelled_budget(
    llm_budget: LlmBudget,
    *,
    day: date,
    billable_tokens: int,
) -> None:
    """Add a precomputed worst-case charge; swallow cache errors after logging."""
    try:
        await llm_budget.add(day, billable_tokens)
    except CacheUnavailable as exc:
        logger.warning(
            "llm_budget_charge_on_cancel_failed",
            error_kind=exc.kind.value,
        )


async def handle_generation_cancelled(args: CancelledGeneration) -> None:
    """Refund a pre-start reservation or charge a post-start estimate."""
    if args.provider_started:
        await charge_cancelled_budget(
            args.llm_budget,
            day=args.day,
            billable_tokens=args.billable_tokens,
        )
        return
    if args.reservation is not None:
        if args.quota_gate is None:
            msg = "quota_gate is required to refund a reservation"
            raise ValueError(msg)
        await args.quota_gate.refund(args.reservation)
