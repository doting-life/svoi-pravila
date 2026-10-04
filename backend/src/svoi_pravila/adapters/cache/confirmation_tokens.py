"""Valkey confirmation tokens: SET NX EX 300 and atomic compare-and-delete."""

from __future__ import annotations

from secrets import token_hex

from redis.asyncio import Redis

_TTL_SECONDS = 300
_LUA_CONSUME = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""


class ValkeyConfirmationTokens:
    """Confirmation nonces keyed by rate-limit pseudonym and action (C0)."""

    def __init__(self, client: Redis, *, key_prefix: str = "tg:confirm") -> None:
        self._client = client
        self._key_prefix = key_prefix
        self._consume = client.register_script(_LUA_CONSUME)

    def _key(self, pseudonym: str, action: str) -> str:
        return f"{self._key_prefix}:{pseudonym}:{action}"

    async def issue(self, *, pseudonym: str, action: str) -> str:
        """Store a 128-bit nonce with NX+TTL; reuse the live value if present."""
        key = self._key(pseudonym, action)
        token = token_hex(16)
        created = await self._client.set(key, token, nx=True, ex=_TTL_SECONDS)
        if created:
            return token
        existing = await self._client.get(key)
        if existing is None:
            await self._client.set(key, token, ex=_TTL_SECONDS)
            return token
        return str(existing)

    async def consume(self, *, pseudonym: str, action: str, token: str) -> bool:
        """Return True only when GET-and-DEL matched ``token``."""
        result = await self._consume(keys=[self._key(pseudonym, action)], args=[token])
        return int(result) == 1
