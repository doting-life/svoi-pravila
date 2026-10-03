"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse

from svoi_pravila.api.middleware import RequestLoggingMiddleware
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.config import Environment

DisposeHook = Callable[[], Awaitable[None]]


def create_app(
    check_readiness: CheckReadiness,
    environment: Environment,
    dispose: DisposeHook | None = None,
) -> FastAPI:
    """Build the ASGI app with already-constructed collaborators (no globals)."""
    docs_enabled = environment in {Environment.LOCAL, Environment.TEST}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if dispose is not None:
            await dispose()

    app = FastAPI(
        title="Svoi Pravila",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        result = await check_readiness.execute()
        body: dict[str, Any] = {
            "ready": result.ready,
            "probes": [
                {"name": probe.name, "status": probe.status.value} for probe in result.probes
            ],
        }
        code = status.HTTP_200_OK if result.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(content=body, status_code=code)

    return app
