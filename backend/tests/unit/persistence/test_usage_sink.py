"""Unit tests for usage-event sink failure mapping."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.application.errors import UsageEventWriteFailed
from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.usage import UsageEvent


class _FailingUowFactory:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self) -> Any:
        raise self._exc


@pytest.mark.unit
async def test_usage_sink_maps_sqlalchemy_error_and_logs_without_payload(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    marker = f"CANARY-{uuid.uuid4()}"
    event = UsageEvent(
        id=UsageEventId(UUID(int=81)),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        user_pseudonym="ab" * 32,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model="m",
        prompt_version="p",
        latency_ms=1,
        ttfc_ms=None,
        attempts=1,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
    )
    sink = UnitOfWorkUsageEventSink(
        _FailingUowFactory(IntegrityError("INSERT", {"payload": marker}, Exception(marker)))
    )
    with pytest.raises(UsageEventWriteFailed):
        await sink.record(event)
    events = [
        item for item in capture_log_events() if item.get("event") == "usage_event_write_failed"
    ]
    assert len(events) == 1
    assert events[0]["error_type"] == "IntegrityError"
    dumped = json.dumps(events)
    assert marker not in dumped
