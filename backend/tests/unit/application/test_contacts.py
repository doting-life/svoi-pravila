"""Contact use-case tests."""

from __future__ import annotations

from types import TracebackType
from uuid import UUID

import pytest

from svoi_pravila.application.errors import ContactLimitReached, NotFound
from svoi_pravila.application.ports.repositories import (
    ConsentRepository,
    ContactRepository,
    InviteRepository,
    PairRepository,
    RuleRepository,
    UsageEventRepository,
    UserRepository,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
)
from svoi_pravila.application.use_cases.list_contacts import ListContacts, ListContactsCommand
from svoi_pravila.application.use_cases.rename_contact import (
    RenameContact,
    RenameContactCommand,
)
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId, TelegramUserId, UserId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.domain.user import User
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_contact_crud_owner_only(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(10)
    stranger = await world.ensure_granted_user(11)
    created = await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        CreateContactCommand(owner.id, ContactLabel("Alex"), RelationshipKind.FRIEND)
    )
    renamed = await RenameContact(world.uow_factory, world.catalog).execute(
        RenameContactCommand(owner.id, created.contact.id, ContactLabel("Sam"))
    )
    assert renamed.contact.label.value == "Sam"
    activated = await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(owner.id, created.contact.id)
    )
    assert activated.user.active_contact_id == created.contact.id
    listed = await ListContacts(world.uow_factory, world.catalog).execute(
        ListContactsCommand(owner.id)
    )
    assert len(listed.contacts) == 1

    with pytest.raises(NotFound):
        await RenameContact(world.uow_factory, world.catalog).execute(
            RenameContactCommand(stranger.id, created.contact.id, ContactLabel("X"))
        )
    with pytest.raises(NotFound):
        await SetActiveContact(world.uow_factory, world.catalog).execute(
            SetActiveContactCommand(stranger.id, created.contact.id)
        )
    with pytest.raises(NotFound):
        await RenameContact(world.uow_factory, world.catalog).execute(
            RenameContactCommand(owner.id, ContactId(UUID(int=9999)), ContactLabel("X"))
        )


@pytest.mark.unit
async def test_contact_limit(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(12)
    uc = CreateContact(world.uow_factory, world.catalog, world.ids, world.clock)
    for i in range(MAX_CONTACTS_PER_USER):
        await uc.execute(
            CreateContactCommand(owner.id, ContactLabel(f"c{i}"), RelationshipKind.OTHER)
        )
    with pytest.raises(ContactLimitReached):
        await uc.execute(
            CreateContactCommand(owner.id, ContactLabel("overflow"), RelationshipKind.OTHER)
        )


@pytest.mark.unit
async def test_set_active_contact_missing_user_after_ownership_check(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(13)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Alex"), RelationshipKind.FRIEND)
        )
    ).contact
    hiding = _HideUserFactory(world.uow_factory, owner.id)
    with pytest.raises(NotFound):
        await SetActiveContact(hiding, world.catalog).execute(
            SetActiveContactCommand(owner.id, contact.id)
        )


class _HideUserFactory:
    def __init__(self, inner: InMemoryUnitOfWorkFactory, hidden_user_id: UserId) -> None:
        self._inner = inner
        self._hidden_user_id = hidden_user_id

    def __call__(self) -> UnitOfWork:
        return _HideUserUow(self._inner(), self._hidden_user_id)


class _HideUserUow:
    def __init__(self, inner: UnitOfWork, hidden_user_id: UserId) -> None:
        self._inner = inner
        self._hidden_user_id = hidden_user_id
        self.users: UserRepository
        self.consents: ConsentRepository
        self.contacts: ContactRepository
        self.pairs: PairRepository
        self.rules: RuleRepository
        self.invites: InviteRepository
        self.usage_events: UsageEventRepository

    async def __aenter__(self) -> _HideUserUow:
        await self._inner.__aenter__()
        self.users = _HideUserUsers(self._inner.users, self._hidden_user_id)
        self.consents = self._inner.consents
        self.contacts = self._inner.contacts
        self.pairs = self._inner.pairs
        self.rules = self._inner.rules
        self.invites = self._inner.invites
        self.usage_events = self._inner.usage_events
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        await self._inner.commit()


class _HideUserUsers:
    def __init__(self, inner: UserRepository, hidden_user_id: UserId) -> None:
        self._inner = inner
        self._hidden_user_id = hidden_user_id
        self._gets = 0

    async def get(self, user_id: UserId) -> User | None:
        if user_id == self._hidden_user_id:
            self._gets += 1
            if self._gets >= 2:
                return None
        return await self._inner.get(user_id)

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        return await self._inner.get_by_telegram_id(telegram_user_id)

    async def add(self, user: User) -> None:
        await self._inner.add(user)

    async def update(self, user: User) -> None:
        await self._inner.update(user)

    async def delete(self, user_id: UserId) -> None:
        await self._inner.delete(user_id)
