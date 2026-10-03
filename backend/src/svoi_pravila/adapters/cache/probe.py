"""Valkey readiness probe."""

from __future__ import annotations

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class ValkeyProbe:
    """Readiness probe that issues ``PING`` against Valkey."""

    name = "valkey"

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self) -> None:
        """Raise if Valkey does not answer ``PING``."""
        try:
            await self._client.ping()
        except Exception as exc:
            logger.warning(
                "readiness_probe_failed",
                probe=self.name,
                error_type=type(exc).__name__,
            )
            raise
