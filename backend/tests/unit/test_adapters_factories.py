"""Adapter factory unit tests (no live network I/O required)."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from tests.factories import make_settings


@pytest.mark.unit
async def test_engine_factory_and_dispose() -> None:
    engine = create_engine(make_settings())
    assert engine.sync_engine.hide_parameters is True
    await dispose_engine(engine)


@pytest.mark.unit
async def test_valkey_client_factory_close() -> None:
    client = create_client(make_settings())
    await close_client(client)
