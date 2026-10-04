"""Composition root wiring tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from svoi_pravila.bootstrap import create_application
from tests.factories import make_settings
from tests.fakes.probes import FailingProbe, OkProbe


class _FakeEngine:
    """Stand-in engine for bootstrap wiring tests."""


class _FakeValkey:
    """Stand-in Valkey client for bootstrap wiring tests."""


@pytest.mark.unit
async def test_create_application_wires_probes_into_readyz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    engine = _FakeEngine()
    valkey = _FakeValkey()
    closed: list[str] = []

    monkeypatch.setattr(
        "svoi_pravila.bootstrap.configure_logging",
        lambda _settings, _stream: None,
    )
    monkeypatch.setattr("svoi_pravila.bootstrap.create_engine", lambda _settings: engine)
    monkeypatch.setattr("svoi_pravila.bootstrap.create_client", lambda _settings: valkey)
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.create_gigachat_client",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.DatabaseProbe",
        lambda _engine: OkProbe("database"),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyProbe",
        lambda _client: OkProbe("valkey"),
    )

    async def close_client(_client: object) -> None:
        closed.append("valkey")

    async def dispose_engine(_engine: object) -> None:
        closed.append("engine")

    async def close_gigachat(_client: object) -> None:
        closed.append("gigachat")

    monkeypatch.setattr("svoi_pravila.bootstrap.close_client", close_client)
    monkeypatch.setattr("svoi_pravila.bootstrap.dispose_engine", dispose_engine)
    monkeypatch.setattr("svoi_pravila.bootstrap.close_gigachat_client", close_gigachat)

    app = create_application(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")
            assert response.status_code == 200
            body = response.json()
            assert body["ready"] is True
            assert {p["name"] for p in body["probes"]} == {"database", "valkey"}
    assert closed == ["gigachat", "valkey", "engine"]


@pytest.mark.unit
async def test_create_application_readyz_failed_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.configure_logging",
        lambda _settings, _stream: None,
    )
    monkeypatch.setattr("svoi_pravila.bootstrap.create_engine", lambda _settings: object())
    monkeypatch.setattr("svoi_pravila.bootstrap.create_client", lambda _settings: object())
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.create_gigachat_client",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.DatabaseProbe",
        lambda _engine: FailingProbe("database"),
    )
    monkeypatch.setattr(
        "svoi_pravila.bootstrap.ValkeyProbe",
        lambda _client: OkProbe("valkey"),
    )

    async def _noop(_obj: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.bootstrap.close_client", _noop)
    monkeypatch.setattr("svoi_pravila.bootstrap.dispose_engine", _noop)
    monkeypatch.setattr("svoi_pravila.bootstrap.close_gigachat_client", _noop)

    app = create_application(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["probes"][0]["status"] == "failed"
