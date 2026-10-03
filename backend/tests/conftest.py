"""Shared pytest fixtures."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import structlog

from svoi_pravila.observability.logging import configure_logging
from tests.factories import make_settings

pytest_plugins = ["tests.support.postgres"]

_NAMED_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "sqlalchemy",
    "asyncio",
    "alembic",
)


@pytest.fixture
def capture_log_events() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """Configure logging into memory; yield JSON-event reader; restore globals."""
    stream = io.StringIO()
    root = logging.getLogger()
    previous_root_handlers = root.handlers[:]
    previous_root_level = root.level
    previous_named: dict[str, tuple[list[logging.Handler], int, bool, bool]] = {}
    for name in _NAMED_LOGGERS:
        logger = logging.getLogger(name)
        previous_named[name] = (
            logger.handlers[:],
            logger.level,
            logger.propagate,
            logger.disabled,
        )

    configure_logging(make_settings(), stream)

    def events() -> list[dict[str, Any]]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    yield events

    root.handlers[:] = previous_root_handlers
    root.setLevel(previous_root_level)
    for name, (handlers, level, propagate, disabled) in previous_named.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled
    structlog.reset_defaults()
