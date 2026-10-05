"""Unit tests for mini-app `/api/v1` auth, errors, and thin routers (in-memory)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.requests import Request
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.init_data import InitDataOptions, build_webapp_init_data
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings, build_miniapp_router
from svoi_pravila.api.miniapp.deps import (
    MAX_BODY_BYTES,
    enforce_body_limit,
    get_miniapp_deps,
    map_access_error,
)
from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body
from svoi_pravila.api.miniapp.http import MiniappHttpError, register_miniapp_exception_handlers
from svoi_pravila.application.errors import AccessNotGranted
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import Environment
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER
from svoi_pravila.domain.enums import ConsentKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE
from svoi_pravila.domain.text import RuleText

_TOKEN = "9:UNIT-MINIAPP"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG_A = 10_014
_TG_B = 20_014


def _auth_header(telegram_id: int, *, auth_date: datetime | None = None) -> dict[str, str]:
    when = auth_date if auth_date is not None else _NOW
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=telegram_id,
        auth_date=int(when.timestamp()),
        options=InitDataOptions(extra={"query_id": "unit-qid"}),
    )
    return {"Authorization": f"tma {raw}"}


def _build_app(world: AppWorld, *, rate_limit: int = 120) -> Any:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=rate_limit),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    bindings = MiniappRouterBindings(
        auth=auth,
        list_contacts=ListContacts(world.uow_factory, world.catalog),
        create_contact=CreateContact(world.uow_factory, world.catalog, world.ids, world.clock),
        rename_contact=RenameContact(world.uow_factory, world.catalog),
        set_active_contact=SetActiveContact(world.uow_factory, world.catalog),
        list_rules=ListRules(world.uow_factory, world.catalog),
        propose_rule=ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock),
        archive_rule=ArchiveRule(world.uow_factory, world.catalog, world.clock),
        list_suggestions=ListSuggestions(world.uow_factory, world.catalog),
        accept_suggestion=AcceptSuggestion(
            world.uow_factory, world.catalog, world.ids, world.clock
        ),
        dismiss_suggestion=DismissSuggestion(world.uow_factory, world.catalog, world.clock),
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    app.state.miniapp_deps = auth
    return app


@pytest.fixture
def mini_world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
    )


@pytest.mark.unit
async def test_me_unknown_user_age_step(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["onboarding_step"] == "age"


@pytest.mark.unit
async def test_unauthorized_missing_header(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == MiniappErrorCode.UNAUTHORIZED
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.unit
async def test_init_data_invalid_and_expired(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad = await client.get(
            "/api/v1/me",
            headers={"Authorization": "tma auth_date=1&hash=00"},
        )
        assert bad.status_code == 401
        assert bad.json()["code"] == MiniappErrorCode.INIT_DATA_INVALID

        expired = await client.get(
            "/api/v1/me",
            headers=_auth_header(_TG_A, auth_date=_NOW - timedelta(seconds=4000)),
        )
        assert expired.status_code == 401
        assert expired.json()["code"] == MiniappErrorCode.INIT_DATA_EXPIRED


@pytest.mark.unit
async def test_rate_limited(mini_world: AppWorld) -> None:
    app = _build_app(mini_world, rate_limit=1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
        second = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == MiniappErrorCode.RATE_LIMITED


@pytest.mark.unit
async def test_contacts_require_onboarding_and_consent(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/v1/contacts", headers=_auth_header(_TG_A))
        assert missing.status_code == 403
        assert missing.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED

    user = (
        await EnsureUser(mini_world.uow_factory, mini_world.ids, mini_world.clock).execute(
            EnsureUserCommand(TelegramUserId(_TG_A))
        )
    ).user
    await ConfirmAge(mini_world.uow_factory, mini_world.clock).execute(ConfirmAgeCommand(user.id))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        consent = await client.get("/api/v1/contacts", headers=_auth_header(_TG_A))
    assert consent.status_code == 403
    assert consent.json()["code"] == MiniappErrorCode.CONSENT_REQUIRED


@pytest.mark.unit
async def test_happy_path_contacts_rules_suggestions(
    mini_world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    label_sentinel = "LABEL_SENTINEL_0014_UNIT"
    rule_sentinel = "RULE_SENTINEL_0014_UNIT"
    user = await mini_world.ensure_granted_user(_TG_A)
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    headers = _auth_header(_TG_A)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "done"

        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": label_sentinel, "relationship": "friend"},
        )
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        contact_id = created.json()["id"]

        listed = await client.get("/api/v1/contacts", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["contacts"]) == 1

        renamed = await client.patch(
            f"/api/v1/contacts/{contact_id}",
            headers=headers,
            json={"label": "Renamed"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["label"] == "Renamed"

        activated = await client.post(
            f"/api/v1/contacts/{contact_id}/activate",
            headers=headers,
        )
        assert activated.status_code == 204
        assert activated.headers["cache-control"] == "no-store"

        me2 = await client.get("/api/v1/me", headers=headers)
        assert me2.json()["active_contact_id"] == contact_id

        rule = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={"category": "apology", "text": rule_sentinel},
        )
        assert rule.status_code == 201
        assert rule.json()["text"] == rule_sentinel
        assert rule.json()["shared"] is False
        rule_id = rule.json()["id"]

        rules = await client.get(f"/api/v1/contacts/{contact_id}/rules", headers=headers)
        assert len(rules.json()["rules"]) == 1

        archived = await client.post(f"/api/v1/rules/{rule_id}/archive", headers=headers)
        assert archived.json()["status"] == "archived"

        again = await client.post(f"/api/v1/rules/{rule_id}/archive", headers=headers)
        assert again.status_code == 409
        assert again.json()["code"] == MiniappErrorCode.INVALID_TRANSITION

        suggestion_id = mini_world.ids.new_id()
        async with mini_world.uow_factory() as uow:
            await uow.rule_suggestions.add(
                RuleSuggestion.create_decode(
                    suggestion_id=RuleSuggestionId(suggestion_id),
                    user_id=user.id,
                    contact_id=ContactId(UUID(contact_id)),
                    category=RuleCategory.HOW_TO_ASK,
                    text=RuleText("suggest text"),
                    now=mini_world.clock.now(),
                )
            )
            await uow.commit()

        suggestions = await client.get(
            f"/api/v1/contacts/{contact_id}/suggestions",
            headers=headers,
        )
        assert len(suggestions.json()["suggestions"]) == 1

        accepted = await client.post(
            f"/api/v1/suggestions/{suggestion_id}/accept",
            headers=headers,
        )
        assert accepted.json()["outcome"] == "accepted"
        assert accepted.json()["rule_id"] is not None

        suggestion_id_2 = mini_world.ids.new_id()
        async with mini_world.uow_factory() as uow:
            await uow.rule_suggestions.add(
                RuleSuggestion.create_decode(
                    suggestion_id=RuleSuggestionId(suggestion_id_2),
                    user_id=user.id,
                    contact_id=ContactId(UUID(contact_id)),
                    category=RuleCategory.OTHER,
                    text=RuleText("dismiss me"),
                    now=mini_world.clock.now(),
                )
            )
            await uow.commit()
        dismissed = await client.post(
            f"/api/v1/suggestions/{suggestion_id_2}/dismiss",
            headers=headers,
        )
        assert dismissed.json()["outcome"] == "dismissed"

        invalid_label = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "", "relationship": "friend"},
        )
        assert invalid_label.status_code == 422
        assert invalid_label.json()["code"] == MiniappErrorCode.VALIDATION_ERROR

        assert (
            await client.get(f"/api/v1/contacts/{uuid4()}/rules", headers=headers)
        ).status_code == 404
        assert (
            await client.get("/api/v1/contacts/not-a-uuid/rules", headers=headers)
        ).status_code == 404

        huge = await client.post(
            "/api/v1/contacts",
            headers={**headers, "Content-Length": str(MAX_BODY_BYTES + 1)},
            content=b"x" * (MAX_BODY_BYTES + 1),
        )
        assert huge.status_code == 413
        assert huge.json()["code"] == MiniappErrorCode.BODY_TOO_LARGE

    blob = str(capture_log_events())
    assert label_sentinel not in blob
    assert rule_sentinel not in blob
    assert "unit-qid" not in blob
    assert "test_user" not in blob


@pytest.mark.unit
async def test_idor_matrix_returns_404(mini_world: AppWorld) -> None:
    user_a = await mini_world.ensure_granted_user(_TG_A)
    await mini_world.ensure_granted_user(_TG_B)
    app = _build_app(mini_world)
    headers_a = _auth_header(_TG_A)
    headers_b = _auth_header(_TG_B)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": "OwnerOnly", "relationship": "partner"},
        )
        contact_id = created.json()["id"]
        rule = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "other", "text": "private rule"},
        )
        rule_id = rule.json()["id"]
        suggestion_id = mini_world.ids.new_id()
        async with mini_world.uow_factory() as uow:
            await uow.rule_suggestions.add(
                RuleSuggestion.create_decode(
                    suggestion_id=RuleSuggestionId(suggestion_id),
                    user_id=user_a.id,
                    contact_id=ContactId(UUID(contact_id)),
                    category=RuleCategory.OTHER,
                    text=RuleText("private suggestion"),
                    now=mini_world.clock.now(),
                )
            )
            await uow.commit()

        cases: list[tuple[str, str, dict[str, object] | None]] = [
            ("GET", f"/api/v1/contacts/{contact_id}/rules", None),
            ("GET", f"/api/v1/contacts/{contact_id}/suggestions", None),
            ("PATCH", f"/api/v1/contacts/{contact_id}", {"label": "Hacked"}),
            ("POST", f"/api/v1/contacts/{contact_id}/activate", None),
            ("POST", f"/api/v1/contacts/{contact_id}/rules", {"category": "other", "text": "x"}),
            ("POST", f"/api/v1/rules/{rule_id}/archive", None),
            ("POST", f"/api/v1/suggestions/{suggestion_id}/accept", None),
            ("POST", f"/api/v1/suggestions/{suggestion_id}/dismiss", None),
        ]
        for method, path, json_body in cases:
            response = await client.request(method, path, headers=headers_b, json=json_body)
            assert response.status_code == 404, path
            assert response.json()["code"] == MiniappErrorCode.NOT_FOUND

        still = await client.get("/api/v1/contacts", headers=headers_a)
        assert still.json()["contacts"][0]["label"] == "OwnerOnly"
        rules = await client.get(f"/api/v1/contacts/{contact_id}/rules", headers=headers_a)
        assert rules.json()["rules"][0]["status"] == "active"
        assert rules.json()["rules"][0]["text"] == "private rule"


@pytest.mark.unit
async def test_contact_and_open_rule_limits(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG_A)
    app = _build_app(mini_world)
    headers = _auth_header(_TG_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        contact_id = ""
        for index in range(MAX_CONTACTS_PER_USER):
            response = await client.post(
                "/api/v1/contacts",
                headers=headers,
                json={"label": f"c{index}", "relationship": "other"},
            )
            assert response.status_code == 201
            contact_id = response.json()["id"]
        overflow = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "overflow", "relationship": "other"},
        )
        assert overflow.status_code == 409
        assert overflow.json()["code"] == MiniappErrorCode.CONTACT_LIMIT

        for index in range(MAX_OPEN_RULES_PER_SCOPE):
            response = await client.post(
                f"/api/v1/contacts/{contact_id}/rules",
                headers=headers,
                json={"category": "other", "text": f"rule {index}"},
            )
            assert response.status_code == 201
        rule_overflow = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={"category": "other", "text": "one too many"},
        )
        assert rule_overflow.status_code == 409
        assert rule_overflow.json()["code"] == MiniappErrorCode.OPEN_RULE_LIMIT


@pytest.mark.unit
async def test_enforce_body_limit_helpers() -> None:
    bad_cl = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/contacts",
            "raw_path": b"/api/v1/contacts",
            "query_string": b"",
            "headers": [(b"content-length", b"abc")],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
    )
    with pytest.raises(MiniappHttpError) as exc:
        await enforce_body_limit(bad_cl)
    assert exc.value.code is MiniappErrorCode.VALIDATION_ERROR

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"x" * (MAX_BODY_BYTES + 1), "more_body": False}

    oversized = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/contacts",
            "raw_path": b"/api/v1/contacts",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        },
        receive,
    )
    with pytest.raises(MiniappHttpError) as exc2:
        await enforce_body_limit(oversized)
    assert exc2.value.code is MiniappErrorCode.BODY_TOO_LARGE


@pytest.mark.unit
def test_map_access_error_and_catalog() -> None:
    assert (
        map_access_error(
            AccessNotGranted(
                AccessStatus(age_confirmed=False, missing_consents=frozenset(), granted=False)
            )
        ).code
        is MiniappErrorCode.ONBOARDING_REQUIRED
    )
    assert (
        map_access_error(
            AccessNotGranted(
                AccessStatus(
                    age_confirmed=True,
                    missing_consents=frozenset({ConsentKind.PERSONAL_DATA}),
                    granted=False,
                )
            )
        ).code
        is MiniappErrorCode.CONSENT_REQUIRED
    )
    assert (
        map_access_error(
            AccessNotGranted(
                AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
            )
        ).code
        is MiniappErrorCode.ONBOARDING_REQUIRED
    )
    body = error_body(MiniappErrorCode.NOT_FOUND)
    assert body.message == "Не найдено."


@pytest.mark.unit
def test_get_miniapp_deps_requires_state() -> None:
    empty = type("App", (), {"state": type("State", (), {})()})()
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/me",
            "raw_path": b"/api/v1/me",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
            "app": empty,
        }
    )
    with pytest.raises(RuntimeError, match="miniapp_deps"):
        get_miniapp_deps(request)
    wrong = type("App", (), {"state": type("State", (), {"miniapp_deps": object()})()})()
    request.scope["app"] = wrong
    with pytest.raises(TypeError, match="unexpected type"):
        get_miniapp_deps(request)


@pytest.mark.unit
async def test_auth_logs_c0_reason_only(
    mini_world: AppWorld,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/me")
    joined = "\n".join(caplog.messages)
    assert "missing_or_malformed_header" in joined
    assert "tma " not in joined


@pytest.mark.unit
async def test_non_miniapp_validation_keeps_detail() -> None:
    """RequestValidationError outside /api/v1 must not use mini-app envelope."""
    app = FastAPI()
    register_miniapp_exception_handlers(app)

    @app.post("/other")
    async def other(payload: dict[str, str]) -> dict[str, str]:
        return payload

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/other", json=[1, 2, 3])
    assert response.status_code == 422
    assert "detail" in response.json()
