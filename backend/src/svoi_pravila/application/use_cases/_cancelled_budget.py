"""Charge a conservative budget estimate when generation is cancelled post-start."""

from __future__ import annotations

import logging
from datetime import date

from svoi_pravila.application.errors import CacheUnavailable
from svoi_pravila.application.ports.llm_budget import LlmBudget
from svoi_pravila.domain.cancelled_billable import estimate_cancelled_billable

logger = logging.getLogger(__name__)


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
