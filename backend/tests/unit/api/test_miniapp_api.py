"""Unit tests for mini-app `/api/v1` auth, errors, and thin routers (in-memory)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.export_download import FakeExportDownloadStore
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.init_data import InitDataOptions, build_webapp_init_data
from tests.support.miniapp_bindings import build_test_miniapp_bindings
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, build_miniapp_router
from svoi_pravila.api.miniapp.body_limit import MAX_BODY_BYTES
from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body
from svoi_pravila.api.miniapp.http import map_access_error, register_miniapp_exception_handlers
from svoi_pravila.application.errors import AccessNotGranted
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
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


def _auth_header(
    telegram_id: int,
    *,
    auth_date: datetime | None = None,
    start_param: str | None = None,
) -> dict[str, str]:
    when = auth_date if auth_date is not None else _NOW
    extra: dict[str, str] = {"query_id": "unit-qid"}
    if start_param is not None:
        extra["start_param"] = start_param
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=telegram_id,
        auth_date=int(when.timestamp()),
        options=InitDataOptions(extra=extra),
    )
    return {"Authorization": f"tma {raw}"}


def _build_app(
    world: AppWorld,
    *,
    rate_limit: int = 120,
    export_limit: int = 3,
    export_store: FakeExportDownloadStore | None = None,
    miniapp_url: str | None = "https://miniapp.test",
) -> Any:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=rate_limit),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    reuse = make_inline_reuse(world.clock)
    decode_bundle = build_miniapp_decode_bundle(world)
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=world,
        decode_bundle=decode_bundle,
        reuse=reuse,
        export_store=export_store,
        export_rate_limiter=FakeRateLimiter(limit=export_limit),
        miniapp_url=miniapp_url,
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )


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


@pytest.mark.unit
async def test_two_apps_keep_isolated_auth_deps(mini_world: AppWorld) -> None:
    """Two routers in one process must not share rate-limiter state via globals."""
    app_loose = _build_app(mini_world, rate_limit=120)
    app_strict = _build_app(mini_world, rate_limit=1)
    headers = _auth_header(_TG_A)
    async with AsyncClient(
        transport=ASGITransport(app=app_loose), base_url="http://loose"
    ) as loose:
        first = await loose.get("/api/v1/me", headers=headers)
        second = await loose.get("/api/v1/me", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    async with AsyncClient(
        transport=ASGITransport(app=app_strict), base_url="http://strict"
    ) as strict:
        ok = await strict.get("/api/v1/me", headers=headers)
        limited = await strict.get("/api/v1/me", headers=headers)
    assert ok.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["code"] == MiniappErrorCode.RATE_LIMITED
    async with AsyncClient(
        transport=ASGITransport(app=app_loose), base_url="http://loose"
    ) as loose_again:
        still_ok = await loose_again.get("/api/v1/me", headers=headers)
    assert still_ok.status_code == 200


@pytest.mark.unit
async def test_me_unknown_user_age_step(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["onboarding_step"] == "age"
    assert body["account_exists"] is False
    assert body["display_timezone"] == "Europe/Moscow"
    assert body["bot_username"] == "test_bot"
    assert body["decode_remaining"] == 40


@pytest.mark.unit
async def test_me_without_bot_username_unavailable(mini_world: AppWorld) -> None:
    from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache

    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), mini_world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(mini_world.uow_factory),
        get_onboarding_step=GetOnboardingStep(mini_world.uow_factory, mini_world.catalog),
    )
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=mini_world,
        decode_bundle=build_miniapp_decode_bundle(mini_world),
        reuse=make_inline_reuse(mini_world.clock),
        overrides={"bot_username": BotUsernameCache(username=None)},
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    assert response.status_code == 503
    assert response.json()["code"] == MiniappErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.unit
async def test_me_cache_unavailable_maps_to_service_unavailable(mini_world: AppWorld) -> None:
    from tests.fakes.quota_budget import FakeQuotaGate

    from svoi_pravila.application.errors import CacheErrorKind, CacheUnavailable

    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), mini_world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(mini_world.uow_factory),
        get_onboarding_step=GetOnboardingStep(mini_world.uow_factory, mini_world.catalog),
    )
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=mini_world,
        decode_bundle=build_miniapp_decode_bundle(mini_world),
        reuse=make_inline_reuse(mini_world.clock),
        overrides={
            "quota_gate": FakeQuotaGate(cache_unavailable=CacheUnavailable(CacheErrorKind.SERVER))
        },
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    assert response.status_code == 503
    assert response.json()["code"] == MiniappErrorCode.SERVICE_UNAVAILABLE


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
        assert me.json()["account_exists"] is True
        assert me.json()["display_timezone"] == "Europe/Moscow"

        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": label_sentinel, "relationship": "friend"},
        )
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        contact_id = created.json()["id"]
        assert created.json()["paired"] is False

        listed = await client.get("/api/v1/contacts", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["contacts"]) == 1
        assert listed.json()["contacts"][0]["paired"] is False

        privacy = await client.get("/api/v1/privacy/texts", headers=headers)
        assert privacy.status_code == 200
        assert "confirm" in privacy.json()["leave_pair"]

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
        assert rules.json()["rules"][0]["has_pending_edit"] is False
        assert rules.json()["rules"][0]["text"] == rule_sentinel

        archived = await client.post(f"/api/v1/rules/{rule_id}/archive", headers=headers)
        assert archived.json()["status"] == "archived"
        listed_after = await client.get(f"/api/v1/contacts/{contact_id}/rules", headers=headers)
        assert listed_after.json()["rules"] == []

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
            ("POST", f"/api/v1/contacts/{contact_id}/invite", None),
            ("POST", f"/api/v1/contacts/{contact_id}/leave", {"confirm": True}),
            ("POST", f"/api/v1/contacts/{contact_id}/rules", {"category": "other", "text": "x"}),
            ("POST", f"/api/v1/rules/{rule_id}/archive", None),
            ("POST", f"/api/v1/rules/{rule_id}/approve", None),
            ("POST", f"/api/v1/rules/{rule_id}/reject", None),
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
def test_rule_item_fallback_for_rejected_without_effective() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from svoi_pravila.api.miniapp.router import _rule_item
    from svoi_pravila.domain.enums import RuleCategory, RuleStatus
    from svoi_pravila.domain.ids import PairId, RuleId, UserId
    from svoi_pravila.domain.rules import PairScope, Rule
    from svoi_pravila.domain.text import RuleText

    owner = UserId(UUID(int=1))
    partner = UserId(UUID(int=2))
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    proposed = Rule.propose(
        rule_id=RuleId(UUID(int=99)),
        scope=PairScope(pair_id=PairId(UUID(int=21))),
        category=RuleCategory.OTHER,
        approvers=frozenset({owner, partner}),
        author_id=owner,
        text=RuleText("pending only"),
        now=now,
    )
    rejected = proposed.reject_pending(partner, now)
    assert rejected.status is RuleStatus.REJECTED
    item = _rule_item(rejected, owner)
    assert item.status == "rejected"
    assert item.text == "pending only"
    assert item.has_pending_edit is False
    assert item.needs_my_approval is False


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
async def test_auth_logs_c0_reason_only(
    mini_world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    app = _build_app(mini_world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/me")
        await client.get(
            "/api/v1/me",
            headers={"Authorization": "tma auth_date=1&hash=00"},
        )
        await client.get(
            "/api/v1/me",
            headers=_auth_header(_TG_A, auth_date=_NOW - timedelta(seconds=4000)),
        )
    app_rl = _build_app(mini_world, rate_limit=1)
    transport_rl = ASGITransport(app=app_rl)
    async with AsyncClient(transport=transport_rl, base_url="http://test") as client:
        await client.get("/api/v1/me", headers=_auth_header(_TG_A))
        await client.get("/api/v1/me", headers=_auth_header(_TG_A))
    events = capture_log_events()
    rejected = [e for e in events if e.get("event") == "miniapp_auth_rejected"]
    assert any(e.get("reason") == "missing_or_malformed_header" for e in rejected)
    assert any(e.get("reason") == "init_data_invalid" for e in rejected)
    assert any(e.get("reason") == "init_data_expired" for e in rejected)
    assert any(e.get("event") == "miniapp_rate_limited" for e in events)
    blob = str(events)
    assert "tma " not in blob
    assert "unit-qid" not in blob


@pytest.mark.unit
async def test_non_miniapp_validation_keeps_detail() -> None:
    """RequestValidationError outside /api/v1 must not use mini-app envelope."""
    app = FastAPI()
    register_miniapp_exception_handlers(app, display_timezone="Europe/Moscow")

    @app.post("/other")
    async def other(payload: dict[str, str]) -> dict[str, str]:
        return payload

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/other", json=[1, 2, 3])
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.unit
async def test_invite_resolve_accept_via_start_param(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG_A)
    await mini_world.ensure_granted_user(_TG_B)
    app = _build_app(mini_world)
    headers_a = _auth_header(_TG_A)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": "Partner", "relationship": "friend"},
        )
        contact_id = created.json()["id"]
        invite = await client.post(f"/api/v1/contacts/{contact_id}/invite", headers=headers_a)
        assert invite.status_code == 201
        link = invite.json()["link"]
        assert link.startswith("https://t.me/test_bot?startapp=inv_")
        raw_token = link.rsplit("inv_", 1)[1]

        missing = await client.post("/api/v1/invites/resolve", headers=_auth_header(_TG_B))
        assert missing.status_code == 404
        forged = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param="forged_token"),
        )
        assert forged.status_code == 404
        wrong_prefix = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param="x_not_inv"),
        )
        assert wrong_prefix.status_code == 404

        resolved = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param=f"inv_{raw_token}"),
        )
        assert resolved.status_code == 200
        assert "expires_at" in resolved.json()

        own = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_A, start_param=f"inv_{raw_token}"),
        )
        assert own.status_code == 409
        assert own.json()["code"] == MiniappErrorCode.INVITE_OWN

        accepted = await client.post(
            "/api/v1/invites/accept",
            headers=_auth_header(_TG_B, start_param=f"inv_{raw_token}"),
            json={"label": "Inviter", "relationship": "friend"},
        )
        assert accepted.status_code == 204
        assert mini_world.notifier.invite_accepted_calls

        contacts_b = await client.get("/api/v1/contacts", headers=_auth_header(_TG_B))
        assert contacts_b.json()["contacts"][0]["paired"] is True

        reuse = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param=f"inv_{raw_token}"),
        )
        assert reuse.status_code == 409
        assert reuse.json()["code"] == MiniappErrorCode.INVITE_INVALID

        empty_prefix = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param="inv_"),
        )
        assert empty_prefix.status_code == 404


@pytest.mark.unit
async def test_invite_resolve_requires_existing_user_and_expiry(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG_A)
    app = _build_app(mini_world)
    headers_a = _auth_header(_TG_A)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": "Partner", "relationship": "friend"},
        )
        contact_id = created.json()["id"]
        invite = await client.post(f"/api/v1/contacts/{contact_id}/invite", headers=headers_a)
        raw_token = invite.json()["link"].rsplit("inv_", 1)[1]

        before_user = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(_TG_B, start_param=f"inv_{raw_token}"),
        )
        assert before_user.status_code == 403
        assert before_user.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED

        await mini_world.ensure_granted_user(_TG_B)
        mini_world.clock.advance(timedelta(days=40))
        expired = await client.post(
            "/api/v1/invites/resolve",
            headers=_auth_header(
                _TG_B,
                auth_date=mini_world.clock.now(),
                start_param=f"inv_{raw_token}",
            ),
        )
        assert expired.status_code == 409
        assert expired.json()["code"] == MiniappErrorCode.INVITE_EXPIRED


@pytest.mark.unit
async def test_pair_invite_shared_rule_approve_reject_leave(mini_world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
    from svoi_pravila.application.use_cases.resolve_invite import (
        ResolveInvite,
        ResolveInviteCommand,
    )
    from svoi_pravila.domain.enums import RelationshipKind
    from svoi_pravila.domain.text import ContactLabel

    await mini_world.ensure_granted_user(_TG_A)
    invitee = await mini_world.ensure_granted_user(_TG_B)
    app = _build_app(mini_world)
    headers_a = _auth_header(_TG_A)
    headers_b = _auth_header(_TG_B)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": "Partner", "relationship": "friend"},
        )
        contact_id = created.json()["id"]
        shared_fail = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "other", "text": "shared fails", "shared": True},
        )
        assert shared_fail.status_code == 409
        assert shared_fail.json()["code"] == MiniappErrorCode.CONTACT_NOT_PAIRED

        leave_fail = await client.post(
            f"/api/v1/contacts/{contact_id}/leave",
            headers=headers_a,
            json={"confirm": True},
        )
        assert leave_fail.status_code == 409
        assert leave_fail.json()["code"] == MiniappErrorCode.CONTACT_NOT_PAIRED

        invite = await client.post(
            f"/api/v1/contacts/{contact_id}/invite",
            headers=headers_a,
        )
        assert invite.status_code == 201
        link = invite.json()["link"]
        assert link.startswith("https://t.me/test_bot?startapp=inv_")
        assert "expires_at" in invite.json()

        again = await client.post(
            f"/api/v1/contacts/{contact_id}/invite",
            headers=headers_a,
        )
        assert again.status_code == 201
        raw_token = again.json()["link"].rsplit("inv_", 1)[1]

        resolved = await ResolveInvite(
            mini_world.uow_factory, mini_world.catalog, mini_world.clock
        ).execute(ResolveInviteCommand(invitee.id, raw_token))
        await AcceptInvite(
            mini_world.uow_factory,
            mini_world.catalog,
            mini_world.ids,
            mini_world.clock,
            mini_world.notifier,
        ).execute(
            AcceptInviteCommand(
                invitee.id,
                resolved.invite_id,
                ContactLabel("Inviter"),
                RelationshipKind.FRIEND,
            )
        )
        assert mini_world.notifier.invite_accepted_calls

        contacts_a = await client.get("/api/v1/contacts", headers=headers_a)
        assert contacts_a.json()["contacts"][0]["paired"] is True

        linked = await client.post(
            f"/api/v1/contacts/{contact_id}/invite",
            headers=headers_a,
        )
        assert linked.status_code == 409
        assert linked.json()["code"] == MiniappErrorCode.CONTACT_ALREADY_LINKED

        shared = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "other", "text": "shared pending", "shared": True},
        )
        assert shared.status_code == 201
        assert shared.json()["shared"] is True
        assert shared.json()["status"] == "proposed"
        assert shared.json()["needs_my_approval"] is False
        rule_id = shared.json()["id"]
        assert mini_world.notifier.shared_rule_proposed_calls

        contacts_b = await client.get("/api/v1/contacts", headers=headers_b)
        b_contact = contacts_b.json()["contacts"][0]["id"]
        rules_b = await client.get(f"/api/v1/contacts/{b_contact}/rules", headers=headers_b)
        pending = next(r for r in rules_b.json()["rules"] if r["id"] == rule_id)
        assert pending["needs_my_approval"] is True

        approved = await client.post(f"/api/v1/rules/{rule_id}/approve", headers=headers_b)
        assert approved.status_code == 200
        assert approved.json()["status"] == "active"
        assert mini_world.notifier.shared_rule_decided_calls

        shared2 = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "other", "text": "to reject", "shared": True},
        )
        reject_id = shared2.json()["id"]
        rejected = await client.post(f"/api/v1/rules/{reject_id}/reject", headers=headers_b)
        assert rejected.status_code == 200

        self_approve = await client.post(f"/api/v1/rules/{reject_id}/approve", headers=headers_a)
        assert self_approve.status_code in {404, 409}

        mini_world.notifier.partner_left_calls.clear()
        left = await client.post(
            f"/api/v1/contacts/{contact_id}/leave",
            headers=headers_a,
            json={"confirm": True},
        )
        assert left.status_code == 204
        assert mini_world.notifier.partner_left_calls
        after = await client.get("/api/v1/contacts", headers=headers_a)
        assert after.json()["contacts"][0]["paired"] is False


@pytest.mark.unit
def test_needs_my_approval_false_when_pending_missing() -> None:
    from types import SimpleNamespace
    from typing import cast
    from uuid import UUID

    from svoi_pravila.api.miniapp.router import _needs_my_approval
    from svoi_pravila.domain.enums import RuleStatus
    from svoi_pravila.domain.ids import PairId, UserId
    from svoi_pravila.domain.rules import PairScope, Rule

    actor = UserId(UUID(int=1))
    rule = SimpleNamespace(
        scope=PairScope(pair_id=PairId(UUID(int=2))),
        status=RuleStatus.PROPOSED,
        pending_revision=None,
        approvers=frozenset({actor}),
    )
    assert _needs_my_approval(cast(Rule, rule), actor) is False
