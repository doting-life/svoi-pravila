"""IssueComposeHandoff use-case tests."""

from __future__ import annotations

import pytest

from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.issue_compose_handoff import (
    IssueComposeHandoff,
    IssueComposeHandoffCommand,
    IssueComposeHandoffPorts,
)
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel
from tests.fakes.compose_handoffs import FakeComposeHandoffs
from tests.fakes.rate_limit import FakePseudonymizer
from tests.unit.application.conftest import AppWorld


def _uc(world: AppWorld, handoffs: FakeComposeHandoffs | None = None) -> IssueComposeHandoff:
    return IssueComposeHandoff(
        IssueComposeHandoffPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            compose_handoffs=handoffs or FakeComposeHandoffs(),
            pseudonymizer=FakePseudonymizer(),
        )
    )


@pytest.mark.unit
async def test_issue_compose_handoff_with_and_without_contact(world: AppWorld) -> None:
    handoffs = FakeComposeHandoffs()
    uc = _uc(world, handoffs)
    with pytest.raises(NotFound):
        await uc.execute(IssueComposeHandoffCommand(TelegramUserId(1), "draft"))
    user = await world.ensure_granted_user(50)
    none_label = await uc.execute(IssueComposeHandoffCommand(TelegramUserId(50), "hi"))
    assert none_label.contact_label is None
    assert none_label.token.startswith("h_")
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Лиза"), RelationshipKind.FRIEND)
        )
    ).contact
    _ = contact
    with_label = await uc.execute(IssueComposeHandoffCommand(TelegramUserId(50), "draft two"))
    assert with_label.contact_label == "Лиза"
    payload = await handoffs.redeem_once(
        FakePseudonymizer().pseudonymize("compose_handoff", "50"),
        with_label.token,
    )
    assert payload.draft == "draft two"
    assert payload.contact_id == contact.id


@pytest.mark.unit
async def test_issue_compose_handoff_requires_access(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
    from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand

    user = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(51))
        )
    ).user
    await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(user.id))
    with pytest.raises(AccessNotGranted):
        await _uc(world).execute(IssueComposeHandoffCommand(TelegramUserId(51), "x"))
