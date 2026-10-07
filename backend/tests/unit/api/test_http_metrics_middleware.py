"""HTTP metrics middleware uses route templates and unmatched."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from svoi_pravila.api.app import create_app
from svoi_pravila.api.metrics_middleware import HttpMetricsMiddleware
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.config import Environment

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def _sample(metric_name: str, labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return float(sample.value)
    return 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_route_label_uses_template_and_unmatched() -> None:
    app = FastAPI()
    app.add_middleware(HttpMetricsMiddleware)

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        matched_before = _sample(
            "sp_http_request_duration_seconds_count",
            {"route": "/items/{item_id}", "method": "GET", "status_class": "2xx"},
        )
        response = await client.get("/items/42")
        assert response.status_code == 200
        assert (
            _sample(
                "sp_http_request_duration_seconds_count",
                {"route": "/items/{item_id}", "method": "GET", "status_class": "2xx"},
            )
            == matched_before + 1.0
        )

        unmatched_before = _sample(
            "sp_http_request_duration_seconds_count",
            {"route": "unmatched", "method": "GET", "status_class": "4xx"},
        )
        missing = await client.get("/no-such-path")
        assert missing.status_code == 404
        assert (
            _sample(
                "sp_http_request_duration_seconds_count",
                {"route": "unmatched", "method": "GET", "status_class": "4xx"},
            )
            == unmatched_before + 1.0
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_main_api_does_not_serve_metrics() -> None:
    app = create_app(CheckReadiness([], 1.0), Environment.TEST)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
        assert response.status_code == 404
        health = await client.get("/healthz")
        assert health.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_metrics_middleware_passes_non_http_scopes() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        del receive, send
        seen.append(str(scope["type"]))

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    middleware = HttpMetricsMiddleware(inner)
    await middleware({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_metrics_middleware_defaults_to_5xx_without_response_start() -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, receive, send

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    before = _sample(
        "sp_http_request_duration_seconds_count",
        {"route": "unmatched", "method": "GET", "status_class": "5xx"},
    )
    middleware = HttpMetricsMiddleware(inner)
    await middleware({"type": "http", "method": "GET"}, receive, send)
    assert (
        _sample(
            "sp_http_request_duration_seconds_count",
            {"route": "unmatched", "method": "GET", "status_class": "5xx"},
        )
        == before + 1.0
    )
