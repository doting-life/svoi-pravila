"""Port for in-flight scenario concurrency (one decode per user)."""

from __future__ import annotations

from typing import Protocol


class ConcurrencyGuard(Protocol):
    """Exclusive lock keyed by an opaque pseudonym."""

    async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
        """Acquire the lock; return an owner token, or None if already held."""
        ...

    async def release(self, key: str, token: str) -> None:
        """Release the lock only if ``token`` still owns it."""
        ...
