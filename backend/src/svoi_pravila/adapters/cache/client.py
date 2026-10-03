"""Valkey client factory (redis.asyncio)."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.config import Settings

SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
SOCKET_TIMEOUT_SECONDS = 2.0


def create_client(settings: Settings) -> Redis:
    """Build an asyncio Redis client pointed at Valkey."""
    return Redis.from_url(
        settings.valkey_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
    )


async def close_client(client: Redis) -> None:
    """Close the Valkey client and its connection pool."""
    await client.aclose()
