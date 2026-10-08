"""ASGI middleware for request logging without query strings or raw paths."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from http import HTTPStatus
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_PROBE_ROUTES = frozenset({"/healthz", "/readyz"})


class RequestLoggingMiddleware:
    """Log one structured event per HTTP request using the route template."""

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
            route_template = getattr(route, "path", None) or "<unmatched>"
            successful_probe = (
                route_template in _PROBE_ROUTES
                and HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES
            )
            if not successful_probe:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "http_request",
                    method=scope.get("method"),
                    route=route_template,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
