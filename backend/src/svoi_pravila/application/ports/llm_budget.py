"""Global daily LLM billable-token budget port (ADR-0009)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BudgetOk:
    """Spent tokens are still below the daily budget."""


@dataclass(frozen=True, slots=True)
class BudgetExhausted:
    """Daily global token budget is already spent."""

    resets_at: datetime


class LlmBudget(Protocol):
    """Atomic global daily token counter with DB rebuild on key loss."""

    async def check(self, day: date) -> BudgetOk | BudgetExhausted:
        """Return ok when spent < budget; rebuild the key from usage_events if missing."""
        ...

    async def add(self, day: date, billable_tokens: int) -> None:
        """INCR spent when the key exists; rebuild from DB then INCR if missing."""
        ...
