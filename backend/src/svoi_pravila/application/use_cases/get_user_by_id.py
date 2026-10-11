"""Look up a user by internal id without creating records."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import UserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class GetUserByIdQuery:
    """Input for GetUserById."""

    user_id: UserId


@dataclass(frozen=True, slots=True)
class GetUserByIdResult:
    """Result of GetUserById."""

    user: User | None


class GetUserById:
    """Return the user for an internal id, or None if absent."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: GetUserByIdQuery) -> GetUserByIdResult:
        """Load the user without writes."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(query.user_id)
        return GetUserByIdResult(user=user)
