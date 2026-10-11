"""GET /contacts/{id}/partner-username — ownership and request-scoped lookup."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.telegram_public_username import FakeTelegramPublicUsername
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.miniapp_bindings import build_test_miniapp_bindings
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.api.test_miniapp_api import _TOKEN, _auth_header
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, build_miniapp_router
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_partner_username import (
    GetPartnerUsername,
    GetPartnerUsernamePorts,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.config import Environment
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.text import ContactLabel

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG_A = 30_026
_TG_B = 30_027
_TG_STRANGER = 30_028


@pytest.fixture
def mini_world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
        notifier=FakePairNotifier(),
    )


def _app(world: AppWorld, usernames: FakeTelegramPublicUsername) -> FastAPI:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    decode_bundle = build_miniapp_decode_bundle(world)
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=world,
        decode_bundle=decode_bundle,
        reuse=make_inline_reuse(world.clock),
        overrides={
            "get_partner_username": GetPartnerUsername(
                GetPartnerUsernamePorts(
                    uow_factory=world.uow_factory,
                    catalog=world.catalog,
                    usernames=usernames,
                )
            )
        },
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )


async def _pair_contacts(world: AppWorld) -> tuple[str, str]:
    owner = await world.ensure_granted_user(_TG_A)
    invitee = await world.ensure_granted_user(_TG_B)
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
    accepted = await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("Аня"),
            RelationshipKind.PARTNER,
        )
    )
    return str(contact.id), str(accepted.invitee_contact.id)


@pytest.mark.unit
async def test_partner_username_owner_and_idor(mini_world: AppWorld) -> None:
    usernames = FakeTelegramPublicUsername(by_id={_TG_B: "partner_user", _TG_A: "owner_user"})
    owner_contact_id, invitee_contact_id = await _pair_contacts(mini_world)
    app = _app(mini_world, usernames)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(
            f"/api/v1/contacts/{owner_contact_id}/partner-username",
            headers=_auth_header(_TG_A),
        )
        assert ok.status_code == 200
        assert ok.json() == {"username": "partner_user"}
        assert usernames.calls == [_TG_B]

        partner_view = await client.get(
            f"/api/v1/contacts/{invitee_contact_id}/partner-username",
            headers=_auth_header(_TG_B),
        )
        assert partner_view.status_code == 200
        assert partner_view.json() == {"username": "owner_user"}

        await mini_world.ensure_granted_user(_TG_STRANGER)
        stranger = await client.get(
            f"/api/v1/contacts/{owner_contact_id}/partner-username",
            headers=_auth_header(_TG_STRANGER),
        )
        assert stranger.status_code == 404


@pytest.mark.unit
async def test_partner_username_null_when_unpaired_or_lookup_fails(mini_world: AppWorld) -> None:
    user = await mini_world.ensure_granted_user(_TG_A)
    contact = (
        await CreateContact(
            mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
        ).execute(CreateContactCommand(user.id, ContactLabel("Solo"), RelationshipKind.FRIEND))
    ).contact
    usernames = FakeTelegramPublicUsername(by_id={_TG_B: "hidden"})
    app = _app(mini_world, usernames)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unpaired = await client.get(
            f"/api/v1/contacts/{contact.id}/partner-username",
            headers=_auth_header(_TG_A),
        )
        assert unpaired.status_code == 200
        assert unpaired.json() == {"username": None}
        assert usernames.calls == []

    owner_contact_id, _invitee = await _pair_contacts(mini_world)
    usernames_fail = FakeTelegramPublicUsername(fail_ids={_TG_B})
    app2 = _app(mini_world, usernames_fail)
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
        missing = await client.get(
            f"/api/v1/contacts/{owner_contact_id}/partner-username",
            headers=_auth_header(_TG_A),
        )
        assert missing.status_code == 200
        assert missing.json() == {"username": None}
