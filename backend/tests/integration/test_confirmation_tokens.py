"""Valkey confirmation token issue/consume semantics."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import pytest

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.confirmation_tokens import ValkeyConfirmationTokens
from svoi_pravila.config import Settings
from tests.factories import make_settings


def _db15_url(valkey_url: str) -> str:
    parts = urlsplit(valkey_url)
    return urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))


@pytest.mark.integration
async def test_confirmation_tokens_nx_and_consume(settings: Settings) -> None:
    client = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=_db15_url(settings.valkey_url.get_secret_value()),
        )
    )
    await client.flushdb()
    try:
        tokens = ValkeyConfirmationTokens(client)
        first = await tokens.issue(pseudonym="abc", action="rv")
        again = await tokens.issue(pseudonym="abc", action="rv")
        assert first == again
        assert len(first) == 32
        assert await tokens.consume(pseudonym="abc", action="rv", token="0" * 32) is False
        assert await tokens.consume(pseudonym="abc", action="rv", token=first) is True
        assert await tokens.consume(pseudonym="abc", action="rv", token=first) is False
        other = await tokens.issue(pseudonym="def", action="rv")
        assert await tokens.consume(pseudonym="abc", action="rv", token=other) is False
    finally:
        await close_client(client)
