"""Unit tests for readiness probe failure logging via the real pipeline."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, cast

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.persistence.probe import DatabaseProbe


class _FailingConnect:
    def __init__(self, message: str) -> None:
        self._message = message

    async def __aenter__(self) -> Any:
        raise ConnectionError(self._message)

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingEngine:
    def __init__(self, message: str) -> None:
        self._message = message

    def connect(self) -> _FailingConnect:
        return _FailingConnect(self._message)


class _FailingValkey:
    def __init__(self, message: str) -> None:
        self._message = message

    async def ping(self) -> None:
        raise ConnectionError(self._message)


@pytest.mark.unit
async def test_database_probe_logs_error_type_without_exception_message(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    marker = f"CANARY-{uuid.uuid4()}"
    probe = DatabaseProbe(cast(AsyncEngine, _FailingEngine(f"dsn contains {marker}")))
    result = await probe.check(1.0)
    assert result.ready is False
    assert result.reason == "ConnectionError"

    events = [
        event for event in capture_log_events() if event.get("event") == "readiness_probe_failed"
    ]
    assert len(events) == 1
    assert events[0]["probe"] == "database"
    assert events[0]["error_type"] == "ConnectionError"
    assert marker not in json.dumps(events)


@pytest.mark.unit
async def test_valkey_probe_logs_error_type_without_exception_message(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    marker = f"CANARY-{uuid.uuid4()}"
    probe = ValkeyProbe(cast(Redis, _FailingValkey(f"url contains {marker}")))
    result = await probe.check(1.0)
    assert result.ready is False
    assert result.reason == "ConnectionError"

    events = [
        event for event in capture_log_events() if event.get("event") == "readiness_probe_failed"
    ]
    assert len(events) == 1
    assert events[0]["probe"] == "valkey"
    assert events[0]["error_type"] == "ConnectionError"
    assert marker not in json.dumps(events)
