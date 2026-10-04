"""Database readiness probe."""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.application.ports.readiness import ProbeCheckResult

logger = structlog.get_logger(__name__)


class DatabaseProbe:
    """Readiness probe that executes ``SELECT 1`` against PostgreSQL."""

    name = "database"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        """Return not-ready for expected driver failures; unexpected errors propagate."""
        try:
            await asyncio.wait_for(self._select_one(), timeout=timeout_seconds)
        except TimeoutError as exc:
            return self._failed(exc, reason="timeout")
        except (OSError, SQLAlchemyError) as exc:
            return self._failed(exc)
        return ProbeCheckResult(ready=True, reason="ok")

    async def _select_one(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    def _failed(self, exc: BaseException, *, reason: str | None = None) -> ProbeCheckResult:
        error_type = type(exc).__name__
        logger.warning(
            "readiness_probe_failed",
            probe=self.name,
            error_type=error_type,
        )
        return ProbeCheckResult(ready=False, reason=reason or error_type)
