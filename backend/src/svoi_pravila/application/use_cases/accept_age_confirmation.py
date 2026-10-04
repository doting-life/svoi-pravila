"""Accept 18+ confirmation and create the user in one unit of work."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import ConflictError, NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import TelegramUserId, UserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class AcceptAgeConfirmationCommand:
    """Input for AcceptAgeConfirmation."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class AcceptAgeConfirmationResult:
    """Result of AcceptAgeConfirmation."""

    user: User


class AcceptAgeConfirmation:
    """Create the user (if needed) and confirm age in a single unit of work.

    Declining 18+ must not call this use case — nothing is persisted on decline.
    ``EnsureUser`` and ``ConfirmAge`` remain available for other callers; each of
    those opens its own unit of work, so onboarding cannot compose them directly.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock

    async def execute(self, command: AcceptAgeConfirmationCommand) -> AcceptAgeConfirmationResult:
        """Ensure the user exists and has ``age_confirmed_at`` set."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if existing is not None:
                updated = existing.confirm_age(now)
                if updated is not existing:
                    await uow.users.update(updated)
                    await uow.commit()
                return AcceptAgeConfirmationResult(user=updated)

            user = User(
                id=UserId(self._ids.new_id()),
                telegram_user_id=command.telegram_user_id,
                created_at=now,
                age_confirmed_at=None,
                active_contact_id=None,
            ).confirm_age(now)
            try:
                await uow.users.add(user)
                await uow.commit()
            except ConflictError:
                pass
            else:
                return AcceptAgeConfirmationResult(user=user)

        async with self._uow_factory() as uow:
            raced = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if raced is None:
                raise NotFound()
            updated = raced.confirm_age(self._clock.now())
            if updated is not raced:
                await uow.users.update(updated)
                await uow.commit()
            return AcceptAgeConfirmationResult(user=updated)
