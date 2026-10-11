"""GetPartnerUsername use-case tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.get_partner_username import (
    GetPartnerUsername,
    GetPartnerUsernameCommand,
    GetPartnerUsernamePorts,
)
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.text import ContactLabel
from tests.fakes.telegram_public_username import FakeTelegramPublicUsername
from tests.unit.application.conftest import AppWorld


def _uc(world: AppWorld, usernames: FakeTelegramPublicUsername) -> GetPartnerUsername:
    return GetPartnerUsername(
        GetPartnerUsernamePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            usernames=usernames,
        )
    )


@pytest.mark.unit
async def test_get_partner_username_paired_and_idor(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(80)
    invitee = await world.ensure_granted_user(81)
    stranger = await world.ensure_granted_user(82)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("П"), RelationshipKind.PARTNER)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(owner.id, contact.id))
    resolved = await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
        ResolveInviteCommand(invitee.id, created.raw_token)
    )
    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("О"),
            RelationshipKind.PARTNER,
        )
    )
    usernames = FakeTelegramPublicUsername(by_id={81: "invitee_tg"})
    result = await _uc(world, usernames).execute(
        GetPartnerUsernameCommand(actor_id=owner.id, contact_id=contact.id)
    )
    assert result.username == "invitee_tg"
    with pytest.raises(NotFound):
        await _uc(world, usernames).execute(
            GetPartnerUsernameCommand(actor_id=stranger.id, contact_id=contact.id)
        )


@pytest.mark.unit
async def test_get_partner_username_unpaired_returns_none(world: AppWorld) -> None:
    user = await world.ensure_granted_user(83)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Solo"), RelationshipKind.FRIEND)
        )
    ).contact
    usernames = FakeTelegramPublicUsername(by_id={99: "x"})
    result = await _uc(world, usernames).execute(
        GetPartnerUsernameCommand(actor_id=user.id, contact_id=contact.id)
    )
    assert result.username is None
    assert usernames.calls == []
    with pytest.raises(NotFound):
        await _uc(world, usernames).execute(
            GetPartnerUsernameCommand(
                actor_id=user.id,
                contact_id=ContactId(UUID("01900000-0000-7000-8000-0000000000aa")),
            )
        )


@pytest.mark.unit
async def test_get_partner_username_missing_partner_user(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(84)
    invitee = await world.ensure_granted_user(85)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("П"), RelationshipKind.PARTNER)
        )
    ).contact
    created = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(owner.id, contact.id))
    resolved = await ResolveInvite(world.uow_factory, world.catalog, world.clock).execute(
        ResolveInviteCommand(invitee.id, created.raw_token)
    )
    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("О"),
            RelationshipKind.PARTNER,
        )
    )
    async with world.uow_factory() as uow:
        await uow.users.delete(invitee.id)
        await uow.commit()
    usernames = FakeTelegramPublicUsername(by_id={85: "gone"})
    with pytest.raises(NotFound):
        await _uc(world, usernames).execute(
            GetPartnerUsernameCommand(actor_id=owner.id, contact_id=contact.id)
        )
    assert usernames.calls == []
