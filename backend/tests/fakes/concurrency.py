"""In-memory concurrency guard for tests."""

from __future__ import annotations


class FakeConcurrencyGuard:
    """Exclusive lock in process memory."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self.acquire_calls: list[tuple[str, int]] = []
        self.release_calls: list[tuple[str, str]] = []

    async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
        self.acquire_calls.append((key, ttl_seconds))
        if key in self._owners:
            return None
        token = f"token:{len(self.acquire_calls)}"
        self._owners[key] = token
        return token

    async def release(self, key: str, token: str) -> None:
        self.release_calls.append((key, token))
        if self._owners.get(key) == token:
            del self._owners[key]
