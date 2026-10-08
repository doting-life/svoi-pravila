"""Unit tests for stdlib logger levels set by configure_logging."""

from __future__ import annotations

import io
import json
import logging

import pytest

from svoi_pravila.config import LogLevel
from svoi_pravila.observability.logging import configure_logging
from tests.factories import make_settings


@pytest.mark.unit
def test_configure_logging_keeps_sqlalchemy_at_warning_when_app_is_info() -> None:
    stream = io.StringIO()
    configure_logging(make_settings(log_level=LogLevel.INFO), stream)

    engine_logger = logging.getLogger("sqlalchemy.engine")
    assert logging.getLogger("sqlalchemy").level == logging.WARNING
    assert not engine_logger.isEnabledFor(logging.INFO)
    assert engine_logger.isEnabledFor(logging.WARNING)

    engine_logger.info("sqlalchemy-info-must-not-appear")
    engine_logger.warning("sqlalchemy-warning-must-appear")

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    messages = [event.get("event") for event in events]
    assert "sqlalchemy-info-must-not-appear" not in messages
    assert "sqlalchemy-warning-must-appear" in messages
    assert logging.getLogger("alembic").level == logging.INFO
