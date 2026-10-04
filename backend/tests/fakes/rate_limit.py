"""In-memory rate limiter and update deduplicator fakes."""

from __future__ import annotations

from hashlib import sha256

from svoi_pravila.application.ports.rate_limiter import RateLimitDecision


class FakeUpdateDeduplicator:
    """Claim each update_id once."""

    def __init__(self) -> None:
        self._seen: set[int] = set()

    async def claim(self, update_id: int) -> bool:
        if update_id in self._seen:
            return False
        self._seen.add(update_id)
        return True


class FakeRateLimiter:
    """Fixed-window limiter in memory."""

    def __init__(self, limit: int = 30) -> None:
        self._limit = limit
        self._counts: dict[str, int] = {}

    async def check(self, pseudonym: str) -> RateLimitDecision:
        count = self._counts.get(pseudonym, 0) + 1
        self._counts[pseudonym] = count
        if count <= self._limit:
            return RateLimitDecision(allowed=True, first_rejection=False)
        return RateLimitDecision(
            allowed=False,
            first_rejection=count == self._limit + 1,
        )

    def check_count(self) -> int:
        """How many times ``check`` was called."""
        return sum(self._counts.values())


class FakePseudonymizer:
    """Deterministic test pseudonymizer."""

    def pseudonymize(self, purpose: str, value: str) -> str:
        return sha256(f"{purpose}\0{value}".encode()).hexdigest()
