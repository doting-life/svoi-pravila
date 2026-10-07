"""Valkey fixtures for cache adapter integration tests (no PostgreSQL required)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from tests.factories import make_settings
from tests.support.postgres import test_valkey_url

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.bootstrap import load_test_infra_settings
from svoi_pravila.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings with Valkey pointed at the dedicated test DB index."""
    infra = load_test_infra_settings()
    return make_settings(
        database_url=infra.database_url.get_secret_value(),
        valkey_url=test_valkey_url(infra.valkey_url.get_secret_value()),
    )


@pytest.fixture
async def valkey_db15(settings: Settings) -> AsyncIterator[Redis]:
    client = create_client(settings)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await close_client(client)
