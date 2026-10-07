"""Cancellation accounting: refund before provider start; charge after."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from svoi_pravila.application.errors import CacheUnavailable
from svoi_pravila.application.ports.llm_budget import LlmBudget
from svoi_pravila.application.ports.quota_gate import QuotaGate, QuotaReservation
from svoi_pravila.domain.cancelled_billable import estimate_cancelled_billable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CancelledGeneration:
    """Inputs for cancel-time quota/budget accounting."""

    provider_started: bool
    reservation: QuotaReservation | None
    quota_gate: QuotaGate | None
    llm_budget: LlmBudget
    day: date
    input_chars: int
    max_output_tokens: int


async def charge_cancelled_budget(
    llm_budget: LlmBudget,
    *,
    day: date,
    input_chars: int,
    max_output_tokens: int,
) -> None:
    """Add ``estimate_cancelled_billable``; swallow cache errors after logging."""
    estimate = estimate_cancelled_billable(input_chars, max_output_tokens)
    try:
        await llm_budget.add(day, estimate)
    except CacheUnavailable as exc:
        logger.info(
            "llm_budget_charge_on_cancel_failed error_kind=%s",
            exc.kind.value,
        )


async def handle_generation_cancelled(args: CancelledGeneration) -> None:
    """Refund a pre-start reservation or charge a post-start estimate."""
    if args.provider_started:
        await charge_cancelled_budget(
            args.llm_budget,
            day=args.day,
            input_chars=args.input_chars,
            max_output_tokens=args.max_output_tokens,
        )
        return
    if args.reservation is not None:
        if args.quota_gate is None:
            msg = "quota_gate is required to refund a reservation"
            raise ValueError(msg)
        await args.quota_gate.refund(args.reservation)
