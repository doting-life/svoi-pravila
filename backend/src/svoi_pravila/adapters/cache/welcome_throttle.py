"""Valkey welcome-throttle: SET NX EX on a per-pseudonym key."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.application.ports.welcome_throttle import WELCOME_THROTTLE_TTL_SECONDS


class ValkeyWelcomeThrottle:
    """Claim welcome sends with atomic ``SET NX EX``."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = WELCOME_THROTTLE_TTL_SECONDS,
        key_prefix: str = "tg:welcome",
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    async def claim(self, pseudonym: str) -> bool:
        """Return True when the key was created for ``pseudonym``."""
        key = f"{self._key_prefix}:{pseudonym}"
        created = await self._client.set(key, "1", nx=True, ex=self._ttl_seconds)
        return created is True
