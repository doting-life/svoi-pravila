"""Log canary — denylisted values must never appear in log output."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import structlog

from svoi_pravila.observability.redaction import REDACTED_PLACEHOLDER


def _has_exc_type(event: dict[str, Any], exc_type: str) -> bool:
    exception = event.get("exception")
    if not isinstance(exception, list):
        return False
    return any(isinstance(stack, dict) and stack.get("exc_type") == exc_type for stack in exception)


@pytest.mark.unit
def test_log_canary_redacts_marker_from_structlog_and_stdlib(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    marker = f"CANARY-{uuid.uuid4()}"
    log = structlog.get_logger("canary")
    log.info("structlog_event", text=marker, safe="ok")
    logging.getLogger("uvicorn.error").error("stdlib_event", extra={"draft": marker})

    def blow_up() -> None:
        local_secret = marker
        _ = local_secret
        msg = "boom"
        raise RuntimeError(msg)

    try:
        blow_up()
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("exc_event")

    events = capture_log_events()
    serialized = json.dumps(events)
    assert marker not in serialized
    assert REDACTED_PLACEHOLDER in serialized


@pytest.mark.unit
def test_log_canary_redacts_exception_message_for_structlog_and_stdlib(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    marker = f"CANARY-{uuid.uuid4()}"

    def blow_up() -> None:
        raise RuntimeError(marker)

    try:
        blow_up()
    except RuntimeError:
        structlog.get_logger("canary").exception("structlog_exc_event")
        logging.getLogger("uvicorn.error").exception("stdlib_exc_event")

    events = capture_log_events()
    serialized = json.dumps(events)
    assert marker not in serialized
    structlog_events = [event for event in events if event.get("event") == "structlog_exc_event"]
    stdlib_events = [event for event in events if event.get("event") == "stdlib_exc_event"]
    assert len(structlog_events) == 1
    assert len(stdlib_events) == 1
    assert _has_exc_type(structlog_events[0], "RuntimeError")
    assert _has_exc_type(stdlib_events[0], "RuntimeError")
