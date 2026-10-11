"""RedeemComposeHandoff use-case tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ComposeHandoffUnavailable,
    RateLimited,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.redeem_compose_handoff import (
    RedeemComposeHandoff,
    RedeemComposeHandoffCommand,
    RedeemComposeHandoffPorts,
)
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId, UserId
from svoi_pravila.domain.text import ContactLabel
from tests.fakes.compose_handoffs import FakeComposeHandoffs
from tests.fakes.compose_sessions import FakeComposeSessions
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.telegram_public_username import FakeTelegramPublicUsername
from tests.unit.application.conftest import AppWorld


def _uc(
    world: AppWorld,
    *,
    handoffs: FakeComposeHandoffs | None = None,
    sessions: FakeComposeSessions | None = None,
    usernames: FakeTelegramPublicUsername | None = None,
    rate_limiter: FakeRateLimiter | None = None,
) -> RedeemComposeHandoff:
    return RedeemComposeHandoff(
        RedeemComposeHandoffPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            compose_handoffs=handoffs or FakeComposeHandoffs(),
            compose_sessions=sessions or FakeComposeSessions(),
            usernames=usernames or FakeTelegramPublicUsername(),
            rate_limiter=rate_limiter or FakeRateLimiter(limit=120),
            pseudonymizer=FakePseudonymizer(),
            clock=world.clock,
        )
    )


async def _pair(world: AppWorld, owner_tg: int, invitee_tg: int) -> tuple[ContactId, int, UserId]:
    owner = await world.ensure_granted_user(owner_tg)
    invitee = await world.ensure_granted_user(invitee_tg)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(owner.id, ContactLabel("Партнёр"), RelationshipKind.PARTNER)
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
            ContactLabel("Я"),
            RelationshipKind.PARTNER,
        )
    )
    return contact.id, invitee_tg, invitee.id


@pytest.mark.unit
async def test_redeem_issues_session_with_partner_username(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(200)
    contact_id, invitee_tg, _invitee_id = await _pair(world, 200, 201)
    handoffs = FakeComposeHandoffs()
    sessions = FakeComposeSessions()
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=owner.id, draft="draft-a", contact_id=contact_id)
    )
    result = await _uc(
        world,
        handoffs=handoffs,
        sessions=sessions,
        usernames=FakeTelegramPublicUsername({invitee_tg: "partner_u"}),
    ).execute(RedeemComposeHandoffCommand(token))
    assert result.draft == "draft-a"
    assert result.partner is not None
    assert result.partner.contact_id == contact_id
    assert result.partner.label == "Партнёр"
    assert result.partner.username == "partner_u"
    assert result.session_token.startswith("cs_")
    assert result.session_token in sessions.items


@pytest.mark.unit
async def test_redeem_without_contact_returns_null_partner(world: AppWorld) -> None:
    user = await world.ensure_granted_user(202)
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="solo", contact_id=None)
    )
    result = await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))
    assert result.draft == "solo"
    assert result.partner is None


@pytest.mark.unit
async def test_redeem_missing_contact_returns_null_partner(world: AppWorld) -> None:
    user = await world.ensure_granted_user(203)
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(
        ComposeHandoffPayload(
            user_id=user.id,
            draft="gone-contact",
            contact_id=ContactId(UUID("01900000-0000-7000-8000-0000000000bb")),
        )
    )
    result = await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))
    assert result.partner is None


@pytest.mark.unit
async def test_redeem_unpaired_contact_returns_null_partner(world: AppWorld) -> None:
    user = await world.ensure_granted_user(204)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Solo"), RelationshipKind.FRIEND)
        )
    ).contact
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="unpaired", contact_id=contact.id)
    )
    result = await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))
    assert result.partner is None


@pytest.mark.unit
async def test_redeem_missing_partner_user_omits_username(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(205)
    contact_id, _invitee_tg, invitee_id = await _pair(world, 205, 206)
    async with world.uow_factory() as uow:
        await uow.users.delete(invitee_id)
        await uow.commit()
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=owner.id, draft="no-user", contact_id=contact_id)
    )
    result = await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))
    assert result.partner is not None
    assert result.partner.label == "Партнёр"
    assert result.partner.username is None


@pytest.mark.unit
async def test_redeem_rate_limited(world: AppWorld) -> None:
    user = await world.ensure_granted_user(207)
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="rl", contact_id=None)
    )
    with pytest.raises(RateLimited):
        await _uc(world, handoffs=handoffs, rate_limiter=FakeRateLimiter(limit=0)).execute(
            RedeemComposeHandoffCommand(token)
        )


@pytest.mark.unit
async def test_redeem_unavailable_and_access(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
    from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
    from svoi_pravila.domain.ids import TelegramUserId

    handoffs = FakeComposeHandoffs()
    with pytest.raises(ComposeHandoffUnavailable):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand("h_" + ("z" * 64)))

    user = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(208))
        )
    ).user
    await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(user.id))
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="no-consent", contact_id=None)
    )
    with pytest.raises(AccessNotGranted):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))

    granted = await world.ensure_granted_user(209)
    gone_token = await handoffs.store(
        ComposeHandoffPayload(user_id=granted.id, draft="gone", contact_id=None)
    )
    async with world.uow_factory() as uow:
        await uow.users.delete(granted.id)
        await uow.commit()
    with pytest.raises(ComposeHandoffUnavailable):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(gone_token))
