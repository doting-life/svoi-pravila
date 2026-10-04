"""UsageEventSink that commits through its own unit of work."""

from __future__ import annotations

import structlog
from sqlalchemy.exc import SQLAlchemyError

from svoi_pravila.application.errors import UsageEventWriteFailed
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.usage import UsageEvent

logger = structlog.get_logger(__name__)


class UnitOfWorkUsageEventSink:
    """Persist a usage event in a dedicated transaction."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record(self, event: UsageEvent) -> None:
        """Insert and commit ``event``."""
        try:
            async with self._uow_factory() as uow:
                await uow.usage_events.add(event)
                await uow.commit()
        except (OSError, TimeoutError, SQLAlchemyError) as exc:
            logger.info(
                "usage_event_write_failed",
                error_type=type(exc).__name__,
            )
            raise UsageEventWriteFailed() from exc
