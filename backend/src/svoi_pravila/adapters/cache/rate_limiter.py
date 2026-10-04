"""Valkey-backed fixed-window rate limiter keyed by opaque pseudonyms."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.application.ports.rate_limiter import RateLimitDecision

_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local count = redis.call('INCR', key)
if count == 1 then
  redis.call('EXPIRE', key, ttl)
end
if count <= limit then
  return {1, 0}
end
local first_rejection = 0
if count == limit + 1 then
  first_rejection = 1
end
return {0, first_rejection}
"""


class ValkeyRateLimiter:
    """Atomic fixed-window limiter; keys never contain Telegram ids."""

    def __init__(
        self,
        client: Redis,
        *,
        limit: int,
        window_seconds: int,
        key_prefix: str = "tg:rl",
    ) -> None:
        self._client = client
        self._limit = limit
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix
        self._script = client.register_script(_LUA)

    async def check(self, pseudonym: str) -> RateLimitDecision:
        """Increment the window counter for ``pseudonym`` and return the decision."""
        key = f"{self._key_prefix}:{pseudonym}"
        allowed, first_rejection = await self._script(
            keys=[key],
            args=[self._limit, self._window_seconds],
        )
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            first_rejection=bool(int(first_rejection)),
        )
