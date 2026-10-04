"""Onboarding use-case tests."""

from __future__ import annotations

from types import TracebackType

import pytest

from svoi_pravila.application.errors import ConflictError, NotFound
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
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.get_consent_document import (
    GetConsentDocument,
    GetConsentDocumentQuery,
)
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStep,
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.grant_consent import (
    GrantConsent,
    GrantConsentCommand,
    GrantConsentOutcome,
)
from svoi_pravila.domain.access import AccessRequirement, ConsentText
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import TelegramUserId, UserId
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.user import User
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_unknown_user_is_at_age(world: AppWorld) -> None:
    result = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(TelegramUserId(1))
    )
    assert result.step.kind is OnboardingStepKind.AGE


@pytest.mark.unit
async def test_accept_age_creates_user_once(world: AppWorld) -> None:
    uc = AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock)
    first = await uc.execute(AcceptAgeConfirmationCommand(TelegramUserId(2)))
    second = await uc.execute(AcceptAgeConfirmationCommand(TelegramUserId(2)))
    assert first.user.id == second.user.id
    assert first.user.age_confirmed_at is not None
    step = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(TelegramUserId(2))
    )
    assert step.step.kind is OnboardingStepKind.CONSENT
    assert step.step.consent_kind is ConsentKind.PERSONAL_DATA


@pytest.mark.unit
async def test_accept_age_updates_existing_unconfirmed_user(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(22))
    )
    result = await AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(22))
    )
    assert result.user.age_confirmed_at is not None


@pytest.mark.unit
async def test_decline_age_stores_nothing(world: AppWorld) -> None:
    async with world.uow_factory() as uow:
        assert await uow.users.get_by_telegram_id(TelegramUserId(3)) is None


@pytest.mark.unit
async def test_full_onboarding_to_done(world: AppWorld) -> None:
    await AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(4))
    )
    step = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(TelegramUserId(4))
    )
    assert step.step.consent_kind is ConsentKind.PERSONAL_DATA
    user_id = (
        await AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock).execute(
            AcceptAgeConfirmationCommand(TelegramUserId(4))
        )
    ).user.id
    for kind in (ConsentKind.PERSONAL_DATA, ConsentKind.SPECIAL_CATEGORY):
        version = world.catalog.current_requirement().for_kind(kind).version
        await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            GrantConsentCommand(user_id, kind, version)
        )
    done = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(TelegramUserId(4))
    )
    assert done.step.kind is OnboardingStepKind.DONE


@pytest.mark.unit
async def test_outdated_consent_returns_that_step(world: AppWorld) -> None:
    user = await world.ensure_granted_user(5)
    world.catalog.set_requirement(
        AccessRequirement.from_kinds(
            {
                ConsentKind.PERSONAL_DATA: ConsentText("2", Sha256Hex("d" * 64)),
                ConsentKind.SPECIAL_CATEGORY: ConsentText("1", Sha256Hex("b" * 64)),
            }
        )
    )
    step = await GetOnboardingStep(world.uow_factory, world.catalog).execute(
        GetOnboardingStepQuery(user.telegram_user_id)
    )
    assert step.step.kind is OnboardingStepKind.CONSENT
    assert step.step.consent_kind is ConsentKind.PERSONAL_DATA
    assert step.step.consent_version == "2"


@pytest.mark.unit
async def test_double_grant_idempotent(world: AppWorld) -> None:
    user = (
        await AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock).execute(
            AcceptAgeConfirmationCommand(TelegramUserId(6))
        )
    ).user
    version = world.catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    first = await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        GrantConsentCommand(user.id, ConsentKind.PERSONAL_DATA, version)
    )
    second = await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        GrantConsentCommand(user.id, ConsentKind.PERSONAL_DATA, version)
    )
    assert first.outcome is GrantConsentOutcome.GRANTED
    assert second.outcome is GrantConsentOutcome.IDEMPOTENT
    assert first.consent is not None
    assert second.consent is not None
    assert first.consent.id == second.consent.id
    async with world.uow_factory() as uow:
        rows = await uow.consents.list_for_user(user.id)
    assert len(rows) == 1


@pytest.mark.unit
async def test_get_consent_document(world: AppWorld) -> None:
    result = await GetConsentDocument(world.catalog).execute(
        GetConsentDocumentQuery(ConsentKind.PERSONAL_DATA)
    )
    assert result.document.kind is ConsentKind.PERSONAL_DATA
    assert result.document.text


@pytest.mark.unit
async def test_accept_age_conflict_then_found(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(90))
    )

    class _ConflictFactory:
        def __init__(self, inner: InMemoryUnitOfWorkFactory) -> None:
            self._inner = inner
            self.hide_once = True

        def __call__(self) -> UnitOfWork:
            return _ConflictUow(self._inner(), self)

    class _ConflictUow:
        def __init__(self, inner: UnitOfWork, factory: _ConflictFactory) -> None:
            self._inner = inner
            self._factory = factory
            self.users: UserRepository
            self.consents: ConsentRepository
            self.contacts: ContactRepository
            self.pairs: PairRepository
            self.rules: RuleRepository
            self.invites: InviteRepository
            self.usage_events: UsageEventRepository

        async def __aenter__(self) -> _ConflictUow:
            await self._inner.__aenter__()
            self.users = _ConflictUsers(self._inner.users, self._factory)
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

    class _ConflictUsers:
        def __init__(self, inner: UserRepository, factory: _ConflictFactory) -> None:
            self._inner = inner
            self._factory = factory

        async def get(self, user_id: UserId) -> User | None:
            return await self._inner.get(user_id)

        async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
            if self._factory.hide_once:
                return None
            return await self._inner.get_by_telegram_id(telegram_user_id)

        async def add(self, user: User) -> None:
            self._factory.hide_once = False
            raise ConflictError()

        async def update(self, user: User) -> None:
            await self._inner.update(user)

    racing = _ConflictFactory(world.uow_factory)
    result = await AcceptAgeConfirmation(racing, world.ids, world.clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(90))
    )
    assert result.user.age_confirmed_at is not None

    # Second conflict against an already age-confirmed user covers the no-op update branch.
    racing2 = _ConflictFactory(world.uow_factory)
    again = await AcceptAgeConfirmation(racing2, world.ids, world.clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(90))
    )
    assert again.user.age_confirmed_at is not None


@pytest.mark.unit
async def test_accept_age_conflict_without_winner(world: AppWorld) -> None:
    class _EmptyFactory:
        def __call__(self) -> UnitOfWork:
            return _EmptyUow()

    class _EmptyUow:
        def __init__(self) -> None:
            self.users: UserRepository = _EmptyUsers()
            self.consents: ConsentRepository
            self.contacts: ContactRepository
            self.pairs: PairRepository
            self.rules: RuleRepository
            self.invites: InviteRepository
            self.usage_events: UsageEventRepository

        async def __aenter__(self) -> _EmptyUow:
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

    class _EmptyUsers:
        async def get(self, user_id: UserId) -> User | None:
            return None

        async def get_by_telegram_id(self, telegram_user_id: TelegramUserId) -> User | None:
            return None

        async def add(self, user: User) -> None:
            raise ConflictError()

        async def update(self, user: User) -> None:
            return None

    with pytest.raises(NotFound):
        await AcceptAgeConfirmation(_EmptyFactory(), world.ids, world.clock).execute(
            AcceptAgeConfirmationCommand(TelegramUserId(91))
        )


@pytest.mark.unit
def test_consent_document_rejects_empty_fields() -> None:
    with pytest.raises(InvalidValueError):
        ConsentDocument(
            kind=ConsentKind.PERSONAL_DATA,
            version="",
            sha256=Sha256Hex("a" * 64),
            text="body",
        )
    with pytest.raises(InvalidValueError):
        ConsentDocument(
            kind=ConsentKind.PERSONAL_DATA,
            version="1",
            sha256=Sha256Hex("a" * 64),
            text="",
        )
