"""API factory tests."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.middleware import RequestLoggingMiddleware
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.config import Environment
from tests.fakes.probes import FailingProbe, OkProbe


@pytest.mark.unit
async def test_healthz() -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.TEST)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.unit
async def test_readyz_ok() -> None:
    app = create_app(CheckReadiness([OkProbe("db")], 1.0), Environment.TEST)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["probes"] == [{"name": "db", "status": "ok"}]


@pytest.mark.unit
async def test_readyz_unavailable() -> None:
    app = create_app(CheckReadiness([FailingProbe("db")], 1.0), Environment.TEST)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["probes"][0]["status"] == "failed"


@pytest.mark.unit
async def test_docs_disabled_in_production() -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.PRODUCTION)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")
    assert docs.status_code == 404
    assert openapi.status_code == 404


@pytest.mark.unit
async def test_request_log_uses_route_template_not_raw_path(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.TEST)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/healthz?token=should-not-appear")
        await client.get("/no-such-route")

    events = [event for event in capture_log_events() if event.get("event") == "http_request"]
    assert any(event.get("route") == "/healthz" for event in events)
    assert any(event.get("route") == "<unmatched>" for event in events)
    serialized = str(events)
    assert "should-not-appear" not in serialized
    assert "token=" not in serialized


@pytest.mark.unit
async def test_dispose_hook_runs_on_shutdown() -> None:
    calls: list[str] = []

    async def dispose() -> None:
        calls.append("disposed")

    app = create_app(
        CheckReadiness([], 1.0),
        Environment.TEST,
        AppLifecycleHooks(dispose=dispose),
    )
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
            assert response.status_code == 200
    assert calls == ["disposed"]


@pytest.mark.unit
async def test_lifespan_without_optional_hooks() -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.TEST)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
            assert response.status_code == 200


@pytest.mark.unit
async def test_middleware_passes_through_non_http_scope() -> None:
    calls: list[str] = []

    async def inner(
        scope: MutableMapping[str, Any],
        _receive: Any,
        _send: Any,
    ) -> None:
        calls.append(str(scope["type"]))

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    middleware = RequestLoggingMiddleware(inner)
    await middleware({"type": "lifespan"}, receive, send)
    assert calls == ["lifespan"]


@pytest.mark.unit
async def test_request_log_emitted_when_handler_raises(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.TEST)

    @app.get("/boom")
    async def boom() -> None:
        msg = "explode"
        raise RuntimeError(msg)

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="explode"):
            await client.get("/boom")

    events = [event for event in capture_log_events() if event.get("event") == "http_request"]
    assert len(events) == 1
    assert events[0]["route"] == "/boom"
    assert events[0]["status_code"] == 500
