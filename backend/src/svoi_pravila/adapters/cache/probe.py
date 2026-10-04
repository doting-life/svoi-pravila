"""Valkey readiness probe."""

from __future__ import annotations

import asyncio

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from svoi_pravila.application.ports.readiness import ProbeCheckResult

logger = structlog.get_logger(__name__)


class ValkeyProbe:
    """Readiness probe that issues ``PING`` against Valkey."""

    name = "valkey"

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self, timeout_seconds: float) -> ProbeCheckResult:
        """Return not-ready for expected driver failures; unexpected errors propagate."""
        try:
            await asyncio.wait_for(self._client.ping(), timeout=timeout_seconds)
        except TimeoutError as exc:
            return self._failed(exc, reason="timeout")
        except (OSError, RedisError) as exc:
            return self._failed(exc)
        return ProbeCheckResult(ready=True, reason="ok")

    def _failed(self, exc: BaseException, *, reason: str | None = None) -> ProbeCheckResult:
        error_type = type(exc).__name__
        logger.warning(
            "readiness_probe_failed",
            probe=self.name,
            error_type=error_type,
        )
        return ProbeCheckResult(ready=False, reason=reason or error_type)
