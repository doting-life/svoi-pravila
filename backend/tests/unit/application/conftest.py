"""Fixtures for application use-case tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.user import User
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory


@dataclass(slots=True)
class AppWorld:
    """Shared fakes for application tests."""

    uow_factory: InMemoryUnitOfWorkFactory
    clock: FakeClock
    ids: FakeIdGenerator
    tokens: FakeTokenGenerator
    catalog: FakeConsentCatalog
    notifier: FakePairNotifier

    async def ensure_granted_user(self, telegram_id: int = 100) -> User:
        """Create a user with age and both consents granted."""
        user = (
            await EnsureUser(self.uow_factory, self.ids, self.clock).execute(
                EnsureUserCommand(TelegramUserId(telegram_id))
            )
        ).user
        user = (
            await ConfirmAge(self.uow_factory, self.clock).execute(ConfirmAgeCommand(user.id))
        ).user
        for kind in ConsentKind:
            version = self.catalog.current_requirement().for_kind(kind).version
            await GrantConsent(self.uow_factory, self.catalog, self.ids, self.clock).execute(
                GrantConsentCommand(user.id, kind, version)
            )
        return user


@pytest.fixture
def world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
        notifier=FakePairNotifier(),
    )
