"""Ensure a user exists for a Telegram identity."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import ConflictError, NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import TelegramUserId, UserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class EnsureUserCommand:
    """Input for EnsureUser."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class EnsureUserResult:
    """Result of EnsureUser."""

    user: User


class EnsureUser:
    """Create the user if absent; otherwise return the existing one."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock

    async def execute(self, command: EnsureUserCommand) -> EnsureUserResult:
        """Return the user for the Telegram id, creating if needed (race-safe)."""
        async with self._uow_factory() as uow:
            existing = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if existing is not None:
                return EnsureUserResult(user=existing)
            user = User(
                id=UserId(self._ids.new_id()),
                telegram_user_id=command.telegram_user_id,
                created_at=self._clock.now(),
                age_confirmed_at=None,
                active_contact_id=None,
            )
            try:
                await uow.users.add(user)
                await uow.commit()
            except ConflictError:
                pass
            else:
                return EnsureUserResult(user=user)

        async with self._uow_factory() as uow:
            raced = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if raced is None:
                raise NotFound()
            return EnsureUserResult(user=raced)
