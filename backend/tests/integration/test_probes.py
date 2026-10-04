"""Integration tests for DatabaseProbe and ValkeyProbe."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy.engine.url import make_url

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.probe import DatabaseProbe
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness, ProbeOutcome
from svoi_pravila.config import Settings
from tests.factories import make_settings


def _unreachable_valkey_url(valkey_url: str) -> str:
    parts = urlsplit(valkey_url)
    host = parts.hostname or "127.0.0.1"
    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo = f"{userinfo}:{parts.password}"
        userinfo = f"{userinfo}@"
    netloc = f"{userinfo}{host}:1"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.mark.integration
async def test_database_probe_ok(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        await DatabaseProbe(engine).check(2.0)
    finally:
        await dispose_engine(engine)


@pytest.mark.integration
async def test_valkey_probe_ok(settings: Settings) -> None:
    client = create_client(settings)
    try:
        await ValkeyProbe(client).check(2.0)
    finally:
        await close_client(client)


@pytest.mark.integration
async def test_unreachable_database_probe_fails(settings: Settings) -> None:
    unreachable = (
        make_url(settings.database_url.get_secret_value())
        .set(port=1)
        .render_as_string(hide_password=False)
    )
    probe_settings = make_settings(
        database_url=unreachable,
        valkey_url=settings.valkey_url.get_secret_value(),
        readiness_timeout_seconds=2.0,
    )
    engine = create_engine(probe_settings)
    try:
        result = await CheckReadiness(
            probes=[DatabaseProbe(engine)],
            timeout_seconds=2.0,
        ).execute()
    finally:
        await dispose_engine(engine)
    assert result.ready is False
    assert result.probes[0].status is ProbeOutcome.FAILED


@pytest.mark.integration
async def test_unreachable_valkey_probe_fails(settings: Settings) -> None:
    probe_settings = make_settings(
        database_url=settings.database_url.get_secret_value(),
        valkey_url=_unreachable_valkey_url(settings.valkey_url.get_secret_value()),
        readiness_timeout_seconds=2.0,
    )
    client = create_client(probe_settings)
    try:
        result = await CheckReadiness(
            probes=[ValkeyProbe(client)],
            timeout_seconds=2.0,
        ).execute()
    finally:
        await close_client(client)
    assert result.ready is False
    assert result.probes[0].status is ProbeOutcome.FAILED
