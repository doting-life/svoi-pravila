"""Integration tests for Valkey rate limiter and update deduplicator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from redis.asyncio import Redis

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.export_download import ValkeyExportDownloadStore
from svoi_pravila.adapters.cache.prepared_results import ValkeyPreparedResults
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.cache.welcome_throttle import ValkeyWelcomeThrottle
from svoi_pravila.application.errors import PreparedResultUnavailable
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.prepared_ref import PREPARED_REF_LENGTH
from svoi_pravila.config import Settings
from svoi_pravila.domain.enums import Firmness
from svoi_pravila.domain.ids import UserId
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


@pytest.mark.integration
async def test_prepared_token_wrong_user_and_tamper_rejected(valkey_db15: Redis) -> None:
    store = ValkeyPreparedResults(valkey_db15, ttl_seconds=60)
    owner = "ab" * 32
    token = await store.store(owner, PreparedVariant(Firmness.GENTLE, "secret-live"))
    keys = [key async for key in valkey_db15.scan_iter(match="tg:prepared:*")]
    assert keys
    assert "secret-live" not in "".join(keys)
    value = await valkey_db15.get(keys[0])
    assert value is not None
    rendered = value if isinstance(value, str) else value.decode()
    assert "secret-live" not in rendered
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem("cd" * 32, token)
    flipped = ("A" if rendered[0] != "A" else "B") + rendered[1:]
    await valkey_db15.set(keys[0], flipped)
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, token)
    with pytest.raises(PreparedResultUnavailable):
        await store.redeem(owner, "p_" + "?" * 64)


@pytest.mark.integration
async def test_prepared_token_length_and_ttl_key_has_no_telegram_id(valkey_db15: Redis) -> None:
    store = ValkeyPreparedResults(valkey_db15, ttl_seconds=60)
    token = await store.store("ff" * 32, PreparedVariant(Firmness.FIRM, "ok"))
    assert len(token) == PREPARED_REF_LENGTH
    keys = [key async for key in valkey_db15.scan_iter(match="tg:prepared:*")]
    assert all("telegram" not in key for key in keys)


@pytest.mark.integration
async def test_welcome_throttle_claim_once_and_ttl(valkey_db15: Redis) -> None:
    throttle = ValkeyWelcomeThrottle(valkey_db15, ttl_seconds=2)
    assert await throttle.claim("pseudo-welcome") is True
    assert await throttle.claim("pseudo-welcome") is False
    keys = [key async for key in valkey_db15.scan_iter(match="tg:welcome:*")]
    assert keys == ["tg:welcome:pseudo-welcome"]
    ttl = await valkey_db15.ttl(keys[0])
    assert 1 <= ttl <= 2
    await asyncio.sleep(2.1)
    assert await throttle.claim("pseudo-welcome") is True


@pytest.mark.integration
async def test_export_download_issue_consume_round_trip(valkey_db15: Redis) -> None:
    store = ValkeyExportDownloadStore(valkey_db15, ttl_seconds=60)
    user_id = UserId(UUID(int=42))
    expires_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=60)
    grant = await store.issue(user_id, expires_at=expires_at)
    keys = [key async for key in valkey_db15.scan_iter(match="export:dl:*")]
    assert len(keys) == 1
    assert grant.raw_token not in keys[0]
    assert await store.consume(grant.raw_token) == user_id
    assert await store.consume(grant.raw_token) is None
    assert await store.consume("unknown-token") is None
