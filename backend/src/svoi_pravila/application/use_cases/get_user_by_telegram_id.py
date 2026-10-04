"""Look up a user by Telegram id without creating records."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class GetUserByTelegramIdQuery:
    """Input for GetUserByTelegramId."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class GetUserByTelegramIdResult:
    """Result of GetUserByTelegramId."""

    user: User | None


class GetUserByTelegramId:
    """Return the user for a Telegram id, or None if absent."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
        """Load the user without writes."""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(query.telegram_user_id)
        return GetUserByTelegramIdResult(user=user)
