"""Unit of Work port."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from svoi_pravila.application.ports.repositories import (
    ConsentRepository,
    ContactRepository,
    InviteRepository,
    PairRepository,
    RuleRepository,
    UsageEventRepository,
    UserRepository,
)


class UnitOfWork(Protocol):
    """Transactional boundary exposing repositories."""

    users: UserRepository
    consents: ConsentRepository
    contacts: ContactRepository
    pairs: PairRepository
    rules: RuleRepository
    invites: InviteRepository
    usage_events: UsageEventRepository

    async def __aenter__(self) -> UnitOfWork:
        """Enter the unit of work."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit; roll back if ``commit`` was not called."""
        ...

    async def commit(self) -> None:
        """Persist all changes made in this unit of work."""
        ...


class UnitOfWorkFactory(Protocol):
    """Creates new units of work."""

    def __call__(self) -> UnitOfWork:
        """Return a new unit of work instance."""
        ...
