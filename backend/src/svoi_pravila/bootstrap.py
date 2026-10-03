"""Composition root — the only place that builds Settings and wires adapters."""

from __future__ import annotations

import sys

import uvicorn
from fastapi import FastAPI

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.probe import DatabaseProbe
from svoi_pravila.api.app import create_app
from svoi_pravila.application.check_readiness import CheckReadiness
from svoi_pravila.config import Settings
from svoi_pravila.observability import configure_logging


def load_settings() -> Settings:
    """Construct Settings from the process environment (sole call site in ``src/``)."""
    return Settings()


def create_application(settings: Settings) -> FastAPI:
    """Build the fully wired ASGI application from already-loaded settings."""
    configure_logging(settings, sys.stdout)

    engine = create_engine(settings)
    valkey = create_client(settings)
    check_readiness = CheckReadiness(
        probes=(DatabaseProbe(engine), ValkeyProbe(valkey)),
        timeout_seconds=settings.readiness_timeout_seconds,
    )

    async def dispose() -> None:
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
