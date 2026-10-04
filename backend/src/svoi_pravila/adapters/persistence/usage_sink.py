"""UsageEventSink that commits through its own unit of work."""

from __future__ import annotations

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.usage import UsageEvent


class UnitOfWorkUsageEventSink:
    """Persist a usage event in a dedicated transaction."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record(self, event: UsageEvent) -> None:
        """Insert and commit ``event``."""
        async with self._uow_factory() as uow:
            await uow.usage_events.add(event)
            await uow.commit()
