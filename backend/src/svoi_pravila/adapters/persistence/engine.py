"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.config import Settings

POOL_SIZE = 5
MAX_OVERFLOW = 10
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 1800


def create_engine(settings: Settings) -> AsyncEngine:
    """Build an async engine from settings with explicit pool parameters."""
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
        hide_parameters=True,
    )


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose the engine connection pool."""
    await engine.dispose()
