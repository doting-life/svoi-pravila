"""Valkey exclusive lock with owner-checked release."""

from __future__ import annotations

import secrets

from redis.asyncio import Redis

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class ValkeyConcurrencyGuard:
    """SET NX EX acquire; Lua release only if the token still owns the key."""

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._release = client.register_script(_RELEASE)

    async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
        """Acquire ``key`` for ``ttl_seconds``; return owner token or None."""
        token = secrets.token_hex(16)
        acquired = await self._client.set(key, token, nx=True, ex=ttl_seconds)
        if acquired:
            return token
        return None

    async def release(self, key: str, token: str) -> None:
        """Delete ``key`` only when ``token`` matches."""
        await self._release(keys=[key], args=[token])
