"""ASGI middleware that records HTTP request duration histograms."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import (
    http_method_label,
    route_label,
    status_class_label,
)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class HttpMetricsMiddleware:
    """Observe ``sp_http_request_duration_seconds`` per matched route template."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if not response_started:
                status_code = 500
            route = scope.get("route")
            template = getattr(route, "path", None)
            families.HTTP_REQUEST_DURATION.labels(
                route=route_label(template if isinstance(template, str) else None),
                method=http_method_label(
                    scope.get("method") if isinstance(scope.get("method"), str) else None
                ),
                status_class=status_class_label(status_code),
            ).observe(time.perf_counter() - started)
