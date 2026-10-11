"""Inline compose handoff redeem, compose session auth, and closed-by-default scope."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from tests.fakes.clock import FakeClock
from tests.fakes.compose_handoffs import FakeComposeHandoffs
from tests.fakes.compose_sessions import FakeComposeSessions
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.telegram_public_username import FakeTelegramPublicUsername
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.init_data import build_webapp_init_data
from tests.support.miniapp_bindings import build_test_miniapp_bindings, make_miniapp_auth
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.application.conftest import AppWorld

from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import build_miniapp_router
from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.compose_session import (
    COMPOSE_SESSION_TTL_SECONDS,
    ComposeSessionPayload,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.redeem_compose_handoff import (
    RedeemComposeHandoff,
    RedeemComposeHandoffPorts,
)
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.config import Environment
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId, TelegramUserId
from svoi_pravila.domain.text import INCOMING_TEXT_MAX_CHARS, ContactLabel

_TOKEN = "9:UNIT-MINIAPP"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 40_026
_TG_B = 40_027
_TG_OTHER = 40_028

# Route auth classes (print membership in the matrix test for the report).
PUBLIC_BY_DESIGN = frozenset(
    {
        "/api/v1/compose/handoff/redeem",
        "/api/v1/downloads/{token}",
        "/healthz",
        "/readyz",
    }
)
COMPOSE_ALLOWED = frozenset(
    {
        "/api/v1/compose",
        "/api/v1/compose/choice",
    }
)
_AUTH_DEP_NAMES = frozenset({"authenticate", "require_actor", "require_compose_actor"})


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


def _build_app(
    world: AppWorld,
    *,
    handoffs: FakeComposeHandoffs | None = None,
    sessions: FakeComposeSessions | None = None,
    usernames: FakeTelegramPublicUsername | None = None,
    enable_test_routes: bool = False,
    redeem_rate_limiter: FakeRateLimiter | None = None,
) -> tuple[Any, FakeComposeHandoffs, FakeComposeSessions]:
    handoffs = handoffs or FakeComposeHandoffs()
    sessions = sessions or FakeComposeSessions()
    names = usernames or FakeTelegramPublicUsername()
    auth = make_miniapp_auth(world, bot_token=_TOKEN, compose_sessions=sessions)
    decode_bundle = build_miniapp_decode_bundle(world)
    redeem = RedeemComposeHandoff(
        RedeemComposeHandoffPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            compose_handoffs=handoffs,
            compose_sessions=sessions,
            usernames=names,
            rate_limiter=redeem_rate_limiter or FakeRateLimiter(limit=120),
            pseudonymizer=FakePseudonymizer(),
            clock=world.clock,
        )
    )
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=world,
        decode_bundle=decode_bundle,
        reuse=make_inline_reuse(world.clock),
        enable_test_routes=enable_test_routes,
        overrides={"redeem_compose_handoff": redeem},
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    return app, handoffs, sessions


async def _pair_contact(world: AppWorld) -> ContactId:
    owner = await world.ensure_granted_user(_TG)
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
    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            resolved.invite_id,
            ContactLabel("Аня"),
            RelationshipKind.PARTNER,
        )
    )
    return contact.id


@pytest.mark.unit
async def test_compose_handoff_redeem_issues_session_single_use(mini_world: AppWorld) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    contact_id = await _pair_contact(mini_world)
    app, handoffs, sessions = _build_app(
        mini_world, usernames=FakeTelegramPublicUsername({_TG_B: "partner_b"})
    )
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="handoff-draft", contact_id=contact_id)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {token}"},
        )
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        body = first.json()
        assert body["draft"] == "handoff-draft"
        assert body["partner"] == {
            "contact_id": str(contact_id),
            "label": "Партнёр",
            "username": "partner_b",
        }
        assert body["limits"] == {"incoming_text_max_chars": INCOMING_TEXT_MAX_CHARS}
        assert body["session"]["token"].startswith("cs_")
        expires = datetime.fromisoformat(body["session"]["expires_at"].replace("Z", "+00:00"))
        assert expires == _NOW + timedelta(seconds=COMPOSE_SESSION_TTL_SECONDS)
        assert body["session"]["token"] in sessions.items

        second = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {token}"},
        )
        assert second.status_code == 401
        assert second.json()["code"] == MiniappErrorCode.HANDOFF_EXPIRED


@pytest.mark.unit
async def test_compose_handoff_redeem_errors_and_partner_null(
    mini_world: AppWorld,
) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    app, handoffs, _sessions = _build_app(mini_world)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {'h_' + ('a' * 64)}"},
        )
        assert missing.status_code == 401
        assert missing.json()["code"] == MiniappErrorCode.HANDOFF_EXPIRED

        deleted_user_token = await handoffs.store(
            ComposeHandoffPayload(user_id=user.id, draft="x", contact_id=None)
        )
        async with mini_world.uow_factory() as uow:
            stored = await uow.users.get(user.id)
            assert stored is not None
            await uow.users.delete(stored.id)
            await uow.commit()
        gone = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {deleted_user_token}"},
        )
        assert gone.status_code == 401
        assert gone.json()["code"] == MiniappErrorCode.HANDOFF_EXPIRED

    user2 = await mini_world.ensure_granted_user(_TG)
    contact = (
        await CreateContact(
            mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
        ).execute(CreateContactCommand(user2.id, ContactLabel("Solo"), RelationshipKind.FRIEND))
    ).contact
    unpaired_token = await handoffs.store(
        ComposeHandoffPayload(user_id=user2.id, draft="solo", contact_id=contact.id)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unpaired = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {unpaired_token}"},
        )
        assert unpaired.status_code == 200
        assert unpaired.json()["partner"] is None
        assert unpaired.json()["draft"] == "solo"

    reuse = make_inline_reuse(mini_world.clock)
    await RevokeAllConsents(mini_world.uow_factory, mini_world.clock, reuse).execute(
        RevokeAllConsentsCommand(TelegramUserId(_TG))
    )
    consent_token = await handoffs.store(
        ComposeHandoffPayload(user_id=user2.id, draft="need-consent", contact_id=None)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        revoked = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {consent_token}"},
        )
        assert revoked.status_code == 403
        assert revoked.json()["code"] == MiniappErrorCode.CONSENT_REQUIRED


@pytest.mark.unit
async def test_compose_session_auth_ttl_tamper_and_contact_bound(
    mini_world: AppWorld,
) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    other = await mini_world.ensure_granted_user(_TG_OTHER)
    contact_id = await _pair_contact(mini_world)
    foreign = (
        await CreateContact(
            mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
        ).execute(CreateContactCommand(other.id, ContactLabel("Чужой"), RelationshipKind.FRIEND))
    ).contact
    sessions = FakeComposeSessions()
    app, _handoffs, _ = _build_app(mini_world, sessions=sessions)
    session_token = await sessions.store(
        ComposeSessionPayload(user_id=user.id, contact_id=contact_id)
    )
    headers = {"Authorization": f"compose {session_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={
                "draft": "пожалуйста смягчи это сообщение немного",
                "intent": "soften",
                "contact_id": str(contact_id),
            },
        )
        assert ok.status_code == 200

        foreign_contact = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={
                "draft": "пожалуйста смягчи это сообщение немного",
                "intent": "soften",
                "contact_id": str(foreign.id),
            },
        )
        assert foreign_contact.status_code == 404
        assert foreign_contact.json()["code"] == MiniappErrorCode.NOT_FOUND

        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 403
        assert me.json()["code"] == MiniappErrorCode.SCOPE_INSUFFICIENT

        bad = await client.post(
            "/api/v1/compose",
            headers={"Authorization": "compose cs_" + ("x" * 64)},
            json={"draft": "пожалуйста смягчи это сообщение немного", "intent": "soften"},
        )
        assert bad.status_code == 401
        assert bad.json()["code"] == MiniappErrorCode.COMPOSE_SESSION_EXPIRED

    async with mini_world.uow_factory() as uow:
        stored = await uow.users.get(user.id)
        assert stored is not None
        await uow.users.delete(stored.id)
        await uow.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "пожалуйста смягчи это сообщение немного", "intent": "soften"},
        )
        assert deleted.status_code == 401
        assert deleted.json()["code"] == MiniappErrorCode.COMPOSE_SESSION_EXPIRED


def _dep_callable_names(dependant: Any) -> set[str]:
    names: set[str] = set()
    stack = [dependant]
    while stack:
        current = stack.pop()
        call = getattr(current, "call", None)
        if call is not None and hasattr(call, "__name__"):
            names.add(call.__name__)
        stack.extend(getattr(current, "dependencies", []) or [])
    return names


@pytest.mark.unit
async def test_compose_scope_closed_by_default_openapi_matrix(mini_world: AppWorld) -> None:
    """Classify every OpenAPI route; FULL rejects COMPOSE; non-public declare auth deps."""
    user = await mini_world.ensure_granted_user(_TG)
    sessions = FakeComposeSessions()
    app, _handoffs, _ = _build_app(mini_world, sessions=sessions, enable_test_routes=False)
    session_token = await sessions.store(ComposeSessionPayload(user_id=user.id, contact_id=None))
    headers = {"Authorization": f"compose {session_token}"}
    schema = app.openapi()
    paths: dict[str, Any] = schema["paths"]
    openapi_paths = frozenset(path for path in paths if not path.startswith("/api/v1/_test/"))
    full_routes = openapi_paths - PUBLIC_BY_DESIGN - COMPOSE_ALLOWED
    assert PUBLIC_BY_DESIGN | COMPOSE_ALLOWED | full_routes == openapi_paths
    assert len(PUBLIC_BY_DESIGN & COMPOSE_ALLOWED) == 0
    assert len(PUBLIC_BY_DESIGN & full_routes) == 0
    assert len(COMPOSE_ALLOWED & full_routes) == 0
    # Task report requires these three class memberships on stdout.
    sys.stdout.write(f"PUBLIC_BY_DESIGN= {sorted(PUBLIC_BY_DESIGN)}\n")
    sys.stdout.write(f"COMPOSE_ALLOWED= {sorted(COMPOSE_ALLOWED)}\n")
    sys.stdout.write(f"FULL= {sorted(full_routes)}\n")

    api_routes: dict[str, list[APIRoute]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            api_routes.setdefault(route.path, []).append(route)
        original = getattr(route, "original_router", None)
        if original is not None:
            for sub in original.routes:
                if isinstance(sub, APIRoute):
                    api_routes.setdefault(sub.path, []).append(sub)
    for path_template in openapi_paths - PUBLIC_BY_DESIGN:
        matching = api_routes.get(path_template, [])
        assert matching, f"no APIRoute for {path_template}"
        for route in matching:
            names = _dep_callable_names(route.dependant)
            assert names & _AUTH_DEP_NAMES, (
                f"{path_template} missing auth dependency; deps={sorted(names)}"
            )

    uuid = "01900000-0000-7000-8000-000000000001"
    sample = {
        "str": "x",
        "token": "t",
        "contact_id": uuid,
        "suggestion_id": uuid,
        "rule_id": uuid,
        "kind": "personal_data",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path_template in sorted(full_routes):
            methods = paths[path_template]
            concrete = path_template
            for name, value in sample.items():
                concrete = concrete.replace("{" + name + "}", value)
            concrete = re.sub(r"\{[^}]+\}", uuid, concrete)
            for method in methods:
                verb = method.lower()
                if verb not in {"get", "post", "put", "patch", "delete"}:
                    continue
                kwargs: dict[str, Any] = {"headers": headers}
                if verb in {"post", "put", "patch"}:
                    kwargs["json"] = {}
                response = await getattr(client, verb)(concrete, **kwargs)
                assert response.status_code == 403, (
                    f"{method.upper()} {path_template} → {response.status_code} {response.text}"
                )
                assert response.json()["code"] == MiniappErrorCode.SCOPE_INSUFFICIENT


@pytest.mark.unit
async def test_compose_auth_schemes_and_handoff_header_edges(mini_world: AppWorld) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    sessions = FakeComposeSessions()
    handoffs = FakeComposeHandoffs()
    app, _, _ = _build_app(mini_world, sessions=sessions, handoffs=handoffs)
    session_token = await sessions.store(ComposeSessionPayload(user_id=user.id, contact_id=None))
    tma_raw = build_webapp_init_data(_TOKEN, user_id=_TG, auth_date=int(_NOW.timestamp()))
    unknown_tma = build_webapp_init_data(_TOKEN, user_id=40_099, auth_date=int(_NOW.timestamp()))
    draft_body = {
        "draft": "пожалуйста смягчи это сообщение немного",
        "intent": "soften",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me_bad_scheme = await client.get("/api/v1/me", headers={"Authorization": "Bearer x"})
        assert me_bad_scheme.status_code == 401
        assert me_bad_scheme.json()["code"] == MiniappErrorCode.UNAUTHORIZED

        contacts_bad = await client.get("/api/v1/contacts", headers={"Authorization": "Bearer x"})
        assert contacts_bad.status_code == 401
        assert contacts_bad.json()["code"] == MiniappErrorCode.UNAUTHORIZED

        compose_missing = await client.post("/api/v1/compose", json=draft_body)
        assert compose_missing.status_code == 401
        assert compose_missing.json()["code"] == MiniappErrorCode.UNAUTHORIZED

        compose_unknown = await client.post(
            "/api/v1/compose",
            headers={"Authorization": "Bearer x"},
            json=draft_body,
        )
        assert compose_unknown.status_code == 401
        assert compose_unknown.json()["code"] == MiniappErrorCode.UNAUTHORIZED

        compose_new_user = await client.post(
            "/api/v1/compose",
            headers={"Authorization": f"tma {unknown_tma}"},
            json=draft_body,
        )
        assert compose_new_user.status_code == 403
        assert compose_new_user.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED

        compose_null_contact = await client.post(
            "/api/v1/compose",
            headers={"Authorization": f"compose {session_token}"},
            json=draft_body,
        )
        assert compose_null_contact.status_code == 200

        redeem_wrong = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"tma {tma_raw}"},
        )
        assert redeem_wrong.status_code == 401
        assert redeem_wrong.json()["code"] == MiniappErrorCode.HANDOFF_EXPIRED

        redeem_empty = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": "handoff "},
        )
        assert redeem_empty.status_code == 401
        assert redeem_empty.json()["code"] == MiniappErrorCode.HANDOFF_EXPIRED


@pytest.mark.unit
async def test_compose_handoff_redeem_rate_limited(mini_world: AppWorld) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    handoffs = FakeComposeHandoffs()
    app, _, _ = _build_app(
        mini_world, handoffs=handoffs, redeem_rate_limiter=FakeRateLimiter(limit=0)
    )
    token = await handoffs.store(
        ComposeHandoffPayload(user_id=user.id, draft="rl", contact_id=None)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        limited = await client.post(
            "/api/v1/compose/handoff/redeem",
            headers={"Authorization": f"handoff {token}"},
        )
    assert limited.status_code == 429
    assert limited.json()["code"] == MiniappErrorCode.RATE_LIMITED
