"""Composition root — the only place that builds Settings and wires adapters."""

from __future__ import annotations

import http.client
import sys

import uvicorn
from fastapi import FastAPI

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.probe import DatabaseProbe
from svoi_pravila.api.app import create_app
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.config import Settings
from svoi_pravila.observability import configure_logging

_HEALTHCHECK_TIMEOUT_SECONDS = 2.0
_HTTP_OK = 200


def load_settings() -> Settings:
    """Construct Settings from the process environment (sole call site in ``src/``)."""
    return Settings()


def create_application(settings: Settings) -> FastAPI:
    """Build the fully wired ASGI application from already-loaded settings."""
    configure_logging(settings, sys.stdout)

    engine = create_engine(settings)
    valkey = create_client(settings)
    gigachat = create_gigachat_client(settings)
    check_readiness = CheckReadiness(
        probes=(DatabaseProbe(engine), ValkeyProbe(valkey)),
        timeout_seconds=settings.readiness_timeout_seconds,
    )

    async def dispose() -> None:
        await close_gigachat_client(gigachat)
        await close_client(valkey)
        await dispose_engine(engine)

    return create_app(
        check_readiness=check_readiness,
        environment=settings.environment,
        dispose=dispose,
    )


def serve() -> None:
    """Load settings, build the app, and run uvicorn programmatically."""
    settings = load_settings()
    app = create_application(settings)
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        access_log=False,
        log_config=None,
    )


def healthcheck() -> None:
    """GET local ``/readyz``; exit 0 on HTTP 200, otherwise 1.

    Intended as the container healthcheck entrypoint. Never prints response
    bodies or settings.
    """
    settings = load_settings()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        settings.http_port,
        timeout=_HEALTHCHECK_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/readyz")
        response = connection.getresponse()
        response.read()
        code = response.status
    except (OSError, TimeoutError, http.client.HTTPException):
        sys.exit(1)
    finally:
        connection.close()
    sys.exit(0 if code == _HTTP_OK else 1)
