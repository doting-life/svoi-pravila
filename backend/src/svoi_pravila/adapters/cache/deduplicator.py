"""Valkey-backed Telegram update_id deduplicator."""

from __future__ import annotations

from redis.asyncio import Redis


class ValkeyUpdateDeduplicator:
    """Claim update ids with atomic ``SET NX EX``."""

    def __init__(self, client: Redis, *, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def claim(self, update_id: int) -> bool:
        """Return True when ``update_id`` was not seen within the TTL window."""
        key = f"tg:upd:{update_id}"
        created = await self._client.set(key, "1", nx=True, ex=self._ttl_seconds)
        return created is True
