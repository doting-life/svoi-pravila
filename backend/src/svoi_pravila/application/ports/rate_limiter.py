"""Rate limiter port for channel update throttling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a rate-limit check."""

    allowed: bool
    first_rejection: bool


class RateLimiter(Protocol):
    """Fixed-window rate limiter keyed by an opaque pseudonym."""

    async def check(self, pseudonym: str) -> RateLimitDecision:
        """Record one attempt for ``pseudonym`` and return the decision."""
        ...
