"""Database readiness probe."""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)


class DatabaseProbe:
    """Readiness probe that executes ``SELECT 1`` against PostgreSQL."""

    name = "database"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        """Raise if the database does not answer ``SELECT 1``."""
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning(
                "readiness_probe_failed",
                probe=self.name,
                error_type=type(exc).__name__,
            )
            raise
