"""Integration tests for Valkey rate limiter and update deduplicator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.config import Settings
from tests.factories import make_settings


def _db15_url(valkey_url: str) -> str:
    parts = urlsplit(valkey_url)
    return urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))


@pytest.fixture
async def valkey_db15(settings: Settings) -> AsyncIterator[Redis]:
    client = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await close_client(client)


@pytest.mark.integration
async def test_deduplicator_claims_once(valkey_db15: Redis) -> None:
    dedup = ValkeyUpdateDeduplicator(valkey_db15, ttl_seconds=2)
    assert await dedup.claim(1001) is True
    assert await dedup.claim(1001) is False
    assert await dedup.claim(1002) is True


@pytest.mark.integration
async def test_deduplicator_expires(valkey_db15: Redis) -> None:
    dedup = ValkeyUpdateDeduplicator(valkey_db15, ttl_seconds=1)
    assert await dedup.claim(2001) is True
    await asyncio.sleep(1.1)
    assert await dedup.claim(2001) is True


@pytest.mark.integration
async def test_rate_limiter_first_rejection_flag(valkey_db15: Redis) -> None:
    limiter = ValkeyRateLimiter(valkey_db15, limit=2, window_seconds=60)
    first = await limiter.check("pseudo-a")
    second = await limiter.check("pseudo-a")
    third = await limiter.check("pseudo-a")
    fourth = await limiter.check("pseudo-a")
    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.first_rejection is True
    assert fourth.allowed is False
    assert fourth.first_rejection is False


@pytest.mark.integration
async def test_rate_limiter_keys_use_pseudonym_only(valkey_db15: Redis) -> None:
    limiter = ValkeyRateLimiter(valkey_db15, limit=5, window_seconds=60)
    await limiter.check("abc123")
    keys = [key async for key in valkey_db15.scan_iter(match="tg:rl:*")]
    assert keys == ["tg:rl:abc123"]


@pytest.mark.integration
async def test_concurrency_guard_exclusive_and_owner_release(valkey_db15: Redis) -> None:
    guard = ValkeyConcurrencyGuard(valkey_db15)
    first = await guard.acquire("tg:decode:lock:abc", ttl_seconds=5)
    second = await guard.acquire("tg:decode:lock:abc", ttl_seconds=5)
    assert first is not None
    assert second is None
    await guard.release("tg:decode:lock:abc", "wrong")
    still = await guard.acquire("tg:decode:lock:abc", ttl_seconds=5)
    assert still is None
    await guard.release("tg:decode:lock:abc", first)
    third = await guard.acquire("tg:decode:lock:abc", ttl_seconds=5)
    assert third is not None
