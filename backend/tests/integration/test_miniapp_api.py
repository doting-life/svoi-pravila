"""Integration tests for mini-app API against PostgreSQL and Valkey."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings, build_miniapp_router
from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmation,
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent, GrantConsentCommand
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.request_my_data_export import RequestMyDataExport
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import Environment, Settings
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.domain.enums import ConsentKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.text import RuleText
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.export_delivery import FakeExportDelivery
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.integration.test_user_rights_delete import _scan_has_uuid
from tests.support.init_data import InitDataOptions, build_webapp_init_data

_TOKEN = "14:INTEGRATION-MINIAPP"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG_A = 11_014
_TG_B = 22_014
_LABEL_SENTINEL = "LABEL_SENTINEL_0014_INT"
_RULE_SENTINEL = "RULE_SENTINEL_0014_INT"


@pytest.fixture
async def miniapp_valkey(settings: Settings) -> AsyncIterator[Redis]:
    parts = urlsplit(settings.valkey_url.get_secret_value())
    db15 = urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))
    client = create_client(
        make_settings(
            database_url=settings.database_url.get_secret_value(),
            valkey_url=db15,
        )
    )
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await close_client(client)


def _auth(telegram_id: int) -> dict[str, str]:
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=telegram_id,
        auth_date=int(_NOW.timestamp()),
        options=InitDataOptions(extra={"query_id": "int-qid", "username": "pii_user"}),
    )
    return {"Authorization": f"tma {raw}"}


@dataclass(frozen=True, slots=True)
class _MiniappWorld:
    uow_factory: SqlAlchemyUnitOfWorkFactory
    valkey: Redis
    settings: Settings
    clock: FakeClock
    ids: FakeIdGenerator
    catalog: PackageConsentCatalog


async def _grant_user(world: _MiniappWorld, telegram_id: int) -> None:
    accepted = await AcceptAgeConfirmation(world.uow_factory, world.ids, world.clock).execute(
        AcceptAgeConfirmationCommand(TelegramUserId(telegram_id))
    )
    for kind in ConsentKind:
        version = world.catalog.current_requirement().for_kind(kind).version
        await GrantConsent(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            GrantConsentCommand(accepted.user.id, kind, version)
        )


def _build_app(world: _MiniappWorld) -> Any:
    uow_factory = world.uow_factory
    valkey = world.valkey
    settings = world.settings
    clock = world.clock
    ids = world.ids
    catalog = world.catalog
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(SecretStr(_TOKEN), clock, max_age_seconds=3600),
        rate_limiter=ValkeyRateLimiter(
            valkey, limit=120, window_seconds=60, key_prefix="miniapp:rl"
        ),
        pseudonymizer=HmacPseudonymizer(settings.pseudonym_pepper_bytes()),
        get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
        get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
    )
    pepper = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    reuse = make_inline_reuse(clock)
    bindings = MiniappRouterBindings(
        auth=auth,
        list_contacts=ListContacts(uow_factory, catalog),
        create_contact=CreateContact(uow_factory, catalog, ids, clock),
        rename_contact=RenameContact(uow_factory, catalog),
        set_active_contact=SetActiveContact(uow_factory, catalog),
        list_rules=ListRules(uow_factory, catalog),
        propose_rule=ProposeRule(uow_factory, catalog, ids, clock),
        archive_rule=ArchiveRule(uow_factory, catalog, clock),
        list_suggestions=ListSuggestions(uow_factory, catalog),
        accept_suggestion=AcceptSuggestion(uow_factory, catalog, ids, clock),
        dismiss_suggestion=DismissSuggestion(uow_factory, catalog, clock),
        request_my_data_export=RequestMyDataExport(
            ExportMyData(uow_factory, clock),
            FakeExportDelivery(),
        ),
        revoke_all_consents=RevokeAllConsents(uow_factory, clock, reuse),
        delete_my_account=DeleteMyAccount(uow_factory, ids, pepper, clock, reuse),
        export_rate_limiter=ValkeyRateLimiter(
            valkey, limit=3, window_seconds=3600, key_prefix="miniapp:export"
        ),
        display_timezone=settings.display_timezone,
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )


@pytest.mark.integration
async def test_miniapp_api_happy_path_idor_privacy(
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
    miniapp_valkey: Redis,
    settings: Settings,
    engine: AsyncEngine,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    world = _MiniappWorld(
        uow_factory=uow_factory_postgres,
        valkey=miniapp_valkey,
        settings=settings,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        catalog=PackageConsentCatalog(),
    )
    await _grant_user(world, _TG_A)
    await _grant_user(world, _TG_B)
    app = _build_app(world)
    clock = world.clock
    ids = world.ids
    headers_a = _auth(_TG_A)
    headers_b = _auth(_TG_B)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers=headers_a)
        assert me.status_code == 200
        assert me.headers["cache-control"] == "no-store"
        assert me.json()["onboarding_step"] == "done"

        created = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": _LABEL_SENTINEL, "relationship": "family"},
        )
        assert created.status_code == 201
        contact_id = created.json()["id"]
        assert created.json()["label"] == _LABEL_SENTINEL

        rule = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "taboo_topic", "text": _RULE_SENTINEL},
        )
        assert rule.status_code == 201
        rule_id = rule.json()["id"]
        assert rule.json()["text"] == _RULE_SENTINEL

        suggestion_id = ids.new_id()
        async with uow_factory_postgres() as uow:
            user = await uow.users.get_by_telegram_id(TelegramUserId(_TG_A))
            assert user is not None
            await uow.rule_suggestions.add(
                RuleSuggestion.create_decode(
                    suggestion_id=RuleSuggestionId(suggestion_id),
                    user_id=user.id,
                    contact_id=ContactId(UUID(contact_id)),
                    category=RuleCategory.OTHER,
                    text=RuleText("integration suggestion"),
                    now=clock.now(),
                )
            )
            await uow.commit()

        suggestions = await client.get(
            f"/api/v1/contacts/{contact_id}/suggestions",
            headers=headers_a,
        )
        assert suggestions.status_code == 200
        assert len(suggestions.json()["suggestions"]) == 1

        idor_cases: list[tuple[str, str, dict[str, object] | None]] = [
            ("GET", f"/api/v1/contacts/{contact_id}/rules", None),
            ("GET", f"/api/v1/contacts/{contact_id}/suggestions", None),
            ("PATCH", f"/api/v1/contacts/{contact_id}", {"label": "Stolen"}),
            ("POST", f"/api/v1/contacts/{contact_id}/activate", None),
            ("POST", f"/api/v1/contacts/{contact_id}/rules", {"category": "other", "text": "x"}),
            ("POST", f"/api/v1/rules/{rule_id}/archive", None),
            ("POST", f"/api/v1/suggestions/{suggestion_id}/accept", None),
            ("POST", f"/api/v1/suggestions/{suggestion_id}/dismiss", None),
        ]
        for method, path, body in idor_cases:
            response = await client.request(method, path, headers=headers_b, json=body)
            assert response.status_code == 404, path
            assert response.json()["code"] == MiniappErrorCode.NOT_FOUND

        still = await client.get("/api/v1/contacts", headers=headers_a)
        assert still.json()["contacts"][0]["label"] == _LABEL_SENTINEL
        rules = await client.get(f"/api/v1/contacts/{contact_id}/rules", headers=headers_a)
        assert rules.json()["rules"][0]["status"] == "active"
        assert rules.json()["rules"][0]["text"] == _RULE_SENTINEL

        accepted = await client.post(
            f"/api/v1/suggestions/{suggestion_id}/accept",
            headers=headers_a,
        )
        assert accepted.status_code == 200
        assert accepted.json()["outcome"] == "accepted"

    async with engine.connect() as conn:
        label_rows = (await conn.execute(text("SELECT label_ciphertext FROM contacts"))).fetchall()
        rule_rows = (
            await conn.execute(text("SELECT text_ciphertext FROM rule_revisions"))
        ).fetchall()
    for row in label_rows:
        assert _LABEL_SENTINEL not in str(row[0])
    for row in rule_rows:
        assert _RULE_SENTINEL not in str(row[0])

    keys = [key async for key in miniapp_valkey.scan_iter(match="*")]
    for key in keys:
        assert _LABEL_SENTINEL not in str(key)
        value = await miniapp_valkey.get(key)
        if value is not None:
            assert _LABEL_SENTINEL not in str(value)
            assert _RULE_SENTINEL not in str(value)

    blob = str(capture_log_events())
    assert _LABEL_SENTINEL not in blob
    assert _RULE_SENTINEL not in blob
    assert "pii_user" not in blob
    assert "int-qid" not in blob
    assert str(_TG_A) not in blob


@pytest.mark.integration
async def test_miniapp_delete_shreds_user_and_keys(
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
    miniapp_valkey: Redis,
    settings: Settings,
    engine: AsyncEngine,
) -> None:
    world = _MiniappWorld(
        uow_factory=uow_factory_postgres,
        valkey=miniapp_valkey,
        settings=settings,
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        catalog=PackageConsentCatalog(),
    )
    await _grant_user(world, _TG_A)
    app = _build_app(world)
    headers = _auth(_TG_A)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": _LABEL_SENTINEL, "relationship": "friend"},
        )
        assert created.status_code == 201
        async with uow_factory_postgres() as uow:
            user = await uow.users.get_by_telegram_id(TelegramUserId(_TG_A))
            assert user is not None
            user_id = user.id
        deleted = await client.post("/api/v1/me/delete", headers=headers, json={"confirm": True})
        assert deleted.status_code == 204
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "age"
        contacts = await client.get("/api/v1/contacts", headers=headers)
        assert contacts.status_code == 403
        assert contacts.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED

    assert await _scan_has_uuid(engine, user_id) is False
    async with engine.connect() as conn:
        key_count = (
            await conn.execute(
                text("SELECT count(*) FROM user_keys WHERE user_id = :id"), {"id": user_id}
            )
        ).scalar_one()
    assert key_count == 0
    remaining = [key async for key in miniapp_valkey.scan_iter(match="*")]
    for key in remaining:
        assert str(user_id) not in str(key)
        assert _LABEL_SENTINEL not in str(key)
        value = await miniapp_valkey.get(key)
        if value is not None:
            assert str(user_id) not in str(value)
            assert _LABEL_SENTINEL not in str(value)
