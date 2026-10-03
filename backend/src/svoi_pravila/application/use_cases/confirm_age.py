"""Confirm user age (18+)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import UserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class ConfirmAgeCommand:
    """Input for ConfirmAge."""

    user_id: UserId


@dataclass(frozen=True, slots=True)
class ConfirmAgeResult:
    """Result of ConfirmAge."""

    user: User


class ConfirmAge:
    """Record age confirmation; idempotent."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ConfirmAgeCommand) -> ConfirmAgeResult:
        """Confirm age for the user."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(command.user_id)
            if user is None:
                raise NotFound()
            updated = user.confirm_age(self._clock.now())
            if updated is not user:
                await uow.users.update(updated)
                await uow.commit()
            return ConfirmAgeResult(user=updated)
