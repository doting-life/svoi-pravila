"""Access and identity use-case tests."""

from __future__ import annotations

from types import TracebackType
from uuid import UUID

import pytest

from svoi_pravila.application.errors import AccessNotGranted, ConflictError, NotFound
from svoi_pravila.application.ports.repositories import (
    ConsentRepository,
    ContactRepository,
    InviteRepository,
    PairRepository,
    RuleRepository,
    UserRepository,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.get_access_status import (
    GetAccessStatus,
    GetAccessStatusCommand,
)
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.application.use_cases.list_contacts import ListContacts, ListContactsCommand
from svoi_pravila.application.use_cases.revoke_consent import RevokeConsent, RevokeConsentCommand
from svoi_pravila.domain.access import AccessRequirement, ConsentText
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import ConsentId, TelegramUserId, UserId
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.user import User
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_ensure_user_idempotent(world: AppWorld) -> None:
    uc = EnsureUser(world.uow_factory, world.ids, world.clock)
    first = await uc.execute(EnsureUserCommand(TelegramUserId(1)))
    second = await uc.execute(EnsureUserCommand(TelegramUserId(1)))
    assert first.user.id == second.user.id


@pytest.mark.unit
async def test_ensure_user_conflict_returns_existing(world: AppWorld) -> None:
    seeded = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(77))
        )
    ).user
    racing = _ConflictOnCreateFactory(world.uow_factory)
    raced = await EnsureUser(racing, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(77))
    )
    assert raced.user.id == seeded.id


@pytest.mark.unit
async def test_access_flow(world: AppWorld) -> None:
    user = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(2))
        )
    ).user
    status = (
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(user.id)
        )
    ).status
    assert status.granted is False
    assert status.age_confirmed is False

    await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(user.id))
    await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(user.id))

    for kind in ConsentKind:
        first = await GrantConsent(
            world.uow_factory, world.catalog, world.ids, world.clock
        ).execute(GrantConsentCommand(user.id, kind))
        second = await GrantConsent(
            world.uow_factory, world.catalog, world.ids, world.clock
        ).execute(GrantConsentCommand(user.id, kind))
        assert first.consent.id == second.consent.id

    granted = (
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(user.id)
        )
    ).status
    assert granted.granted is True

    revoked = await RevokeConsent(world.uow_factory, world.clock).execute(
        RevokeConsentCommand(user.id, ConsentKind.PERSONAL_DATA)
    )
    assert len(revoked.consents) == 1
    after = (
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(user.id)
        )
    ).status
    assert after.granted is False
    assert ConsentKind.PERSONAL_DATA in after.missing_consents


@pytest.mark.unit
async def test_revoke_consent_all_versions_of_kind(world: AppWorld) -> None:
    user = await world.ensure_granted_user(4)
    old_text = ConsentText("pd-old", Sha256Hex("c" * 64))
    async with world.uow_factory() as uow:
        await uow.consents.add(
            Consent(
                id=ConsentId(world.ids.new_id()),
                user_id=user.id,
                kind=ConsentKind.PERSONAL_DATA,
                text_version=old_text.version,
                text_sha256=old_text.sha256,
                granted_at=world.clock.now(),
                revoked_at=None,
            )
        )
        await uow.commit()

    result = await RevokeConsent(world.uow_factory, world.clock).execute(
        RevokeConsentCommand(user.id, ConsentKind.PERSONAL_DATA)
    )
    assert len(result.consents) == 2
    assert all(c.revoked_at is not None for c in result.consents)

    empty = await RevokeConsent(world.uow_factory, world.clock).execute(
        RevokeConsentCommand(user.id, ConsentKind.PERSONAL_DATA)
    )
    assert empty.consents == ()


@pytest.mark.unit
async def test_protected_use_case_requires_access(world: AppWorld) -> None:
    user = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(3))
        )
    ).user
    with pytest.raises(AccessNotGranted) as exc:
        await ListContacts(world.uow_factory, world.catalog).execute(ListContactsCommand(user.id))
    assert exc.value.status.granted is False


@pytest.mark.unit
async def test_missing_user_not_found(world: AppWorld) -> None:
    with pytest.raises(NotFound):
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(UserId(UUID(int=999)))
        )
    with pytest.raises(NotFound):
        await RevokeConsent(world.uow_factory, world.clock).execute(
            RevokeConsentCommand(UserId(UUID(int=999)), ConsentKind.PERSONAL_DATA)
        )


@pytest.mark.unit
async def test_confirm_age_and_grant_consent_missing_user(world: AppWorld) -> None:
    missing = UserId(UUID(int=999))
    with pytest.raises(NotFound):
        await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(missing))
    with pytest.raises(NotFound):
        await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            GrantConsentCommand(missing, ConsentKind.PERSONAL_DATA)
        )


@pytest.mark.unit
async def test_ensure_user_conflict_without_winner_is_not_found(world: AppWorld) -> None:
    racing = _ConflictWithoutExistingFactory()
    with pytest.raises(NotFound):
        await EnsureUser(racing, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(88))
        )


@pytest.mark.unit
async def test_grant_consent_after_requirement_bump(world: AppWorld) -> None:
    user = await world.ensure_granted_user(5)
    world.catalog.set_requirement(
        AccessRequirement.from_kinds(
            {
                ConsentKind.PERSONAL_DATA: ConsentText("pd-v2", Sha256Hex("d" * 64)),
                ConsentKind.SPECIAL_CATEGORY: ConsentText("sc-v1", Sha256Hex("b" * 64)),
            }
        )
    )
    status = (
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(user.id)
        )
    ).status
    assert status.granted is False
    await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        GrantConsentCommand(user.id, ConsentKind.PERSONAL_DATA)
    )
    after = (
        await GetAccessStatus(world.uow_factory, world.catalog).execute(
            GetAccessStatusCommand(user.id)
        )
    ).status
    assert after.granted is True


class _ConflictOnCreateFactory:
    """Hide existing user once, raise ConflictError on add, then resolve normally."""

    def __init__(self, inner: InMemoryUnitOfWorkFactory) -> None:
        self._inner = inner
        self.hide_existing = True

    def __call__(self) -> UnitOfWork:
        return _ConflictOnCreateUow(self._inner(), self)


class _ConflictWithoutExistingFactory:
    """Always conflict on add and never find the user afterwards."""

    def __call__(self) -> UnitOfWork:
        return _ConflictWithoutExistingUow()


class _ConflictOnCreateUow:
    def __init__(self, inner: UnitOfWork, factory: _ConflictOnCreateFactory) -> None:
        self._inner = inner
        self._factory = factory
        self.users: UserRepository
        self.consents: ConsentRepository
        self.contacts: ContactRepository
        self.pairs: PairRepository
        self.rules: RuleRepository
        self.invites: InviteRepository

    async def __aenter__(self) -> _ConflictOnCreateUow:
        await self._inner.__aenter__()
        self.users = _ConflictOnCreateUsers(self._inner.users, self._factory)
        self.consents = self._inner.consents
        self.contacts = self._inner.contacts
        self.pairs = self._inner.pairs
        self.rules = self._inner.rules
        self.invites = self._inner.invites
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


class _ConflictOnCreateUsers:
    def __init__(self, inner: UserRepository, factory: _ConflictOnCreateFactory) -> None:
        self._inner = inner
        self._factory = factory

    async def get(self, user_id: UserId) -> User | None:
        return await self._inner.get(user_id)

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        if self._factory.hide_existing:
            return None
        return await self._inner.get_by_telegram_id(telegram_user_id)

    async def add(self, user: User) -> None:
        if self._factory.hide_existing:
            self._factory.hide_existing = False
            raise ConflictError()
        await self._inner.add(user)

    async def update(self, user: User) -> None:
        await self._inner.update(user)


class _ConflictWithoutExistingUow:
    def __init__(self) -> None:
        self.users: UserRepository = _EmptyConflictUsers()
        self.consents: ConsentRepository
        self.contacts: ContactRepository
        self.pairs: PairRepository
        self.rules: RuleRepository
        self.invites: InviteRepository

    async def __aenter__(self) -> _ConflictWithoutExistingUow:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class _EmptyConflictUsers:
    async def get(self, user_id: UserId) -> User | None:
        return None

    async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
        return None

    async def add(self, user: User) -> None:
        raise ConflictError()

    async def update(self, user: User) -> None:
        return None
