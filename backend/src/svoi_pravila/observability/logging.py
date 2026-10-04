"""Structlog configuration with JSON output and privacy redaction."""

from __future__ import annotations

import logging
from typing import IO, Protocol

import structlog
from structlog.processors import ExceptionRenderer
from structlog.stdlib import ProcessorFormatter
from structlog.tracebacks import ExceptionDictTransformer
from structlog.types import Processor

from svoi_pravila.config import LogLevel
from svoi_pravila.observability.redaction import redact_log_processor


class SupportsLogLevel(Protocol):
    """Minimal settings surface required to configure logging."""

    log_level: LogLevel


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        ExceptionRenderer(
            exception_formatter=ExceptionDictTransformer(show_locals=False),
        ),
        redact_log_processor,
    ]


def configure_logging(settings: SupportsLogLevel, stream: IO[str]) -> None:
    """Configure structlog and route all stdlib loggers through JSON processors.

    ``stream`` is the sink for the root handler. The composition root passes
    ``sys.stdout``; tests pass an in-memory stream and restore state afterward.
    """
    shared = _shared_processors()
    renderer: Processor = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.value)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "asyncio", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(settings.log_level.value)

    logging.getLogger("uvicorn.access").disabled = True
