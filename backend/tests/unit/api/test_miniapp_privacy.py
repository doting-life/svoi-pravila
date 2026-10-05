"""Unit tests for mini-app privacy endpoints: export, revoke, delete."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.export_delivery import FakeExportDelivery
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.api.test_miniapp_api import _auth_header, _build_app
from tests.unit.application.conftest import AppWorld

from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.privacy import load_privacy_catalog

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 10_015
_EXPORT_SENTINEL = "SENTINEL_EXPORT_MINIAPP_0015_2"


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
async def test_export_happy_path_never_returns_payload(
    mini_world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    await CreateContact(
        mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
    ).execute(
        CreateContactCommand(user.id, ContactLabel(_EXPORT_SENTINEL), RelationshipKind.FRIEND)
    )
    delivery = FakeExportDelivery()
    app = _build_app(mini_world, delivery=delivery)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/me/export", headers=headers)
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"delivered_to": "bot_chat"}
    assert _EXPORT_SENTINEL not in response.text
    assert len(delivery.deliveries) == 1
    assert _EXPORT_SENTINEL in str(delivery.deliveries[0][1])
    blob = str(capture_log_events())
    assert _EXPORT_SENTINEL not in blob


@pytest.mark.unit
async def test_export_bot_chat_unavailable(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world, delivery=FakeExportDelivery(unavailable=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/me/export", headers=_auth_header(_TG))
    assert response.status_code == 409
    assert response.json()["code"] == MiniappErrorCode.BOT_CHAT_UNAVAILABLE


@pytest.mark.unit
async def test_export_fourth_call_rate_limited(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world, export_limit=3)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            ok = await client.post("/api/v1/me/export", headers=headers)
            assert ok.status_code == 202
        limited = await client.post("/api/v1/me/export", headers=headers)
    assert limited.status_code == 429
    assert limited.json()["code"] == MiniappErrorCode.RATE_LIMITED


@pytest.mark.unit
async def test_revoke_confirm_validation(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/api/v1/me/consents/revoke", headers=headers, json={})
        false_body = await client.post(
            "/api/v1/me/consents/revoke", headers=headers, json={"confirm": False}
        )
        extra = await client.post(
            "/api/v1/me/consents/revoke",
            headers=headers,
            json={"confirm": True, "user_id": "other"},
        )
    assert missing.status_code == 422
    assert missing.json()["code"] == MiniappErrorCode.VALIDATION_ERROR
    assert false_body.status_code == 422
    assert extra.status_code == 422


@pytest.mark.unit
async def test_revoke_then_me_consent_and_other_forbidden(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    delivery = FakeExportDelivery()
    app = _build_app(mini_world, delivery=delivery)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        revoked = await client.post(
            "/api/v1/me/consents/revoke", headers=headers, json={"confirm": True}
        )
        assert revoked.status_code == 204
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "consent"
        assert me.json()["account_exists"] is True
        contacts = await client.get("/api/v1/contacts", headers=headers)
        assert contacts.status_code == 403
        assert contacts.json()["code"] == MiniappErrorCode.CONSENT_REQUIRED
        exported = await client.post("/api/v1/me/export", headers=headers)
        assert exported.status_code == 202
        assert exported.json() == {"delivered_to": "bot_chat"}
        assert len(delivery.deliveries) == 1
        deleted = await client.post("/api/v1/me/delete", headers=headers, json={"confirm": True})
        assert deleted.status_code == 204


@pytest.mark.unit
async def test_export_after_age_without_consents(mini_world: AppWorld) -> None:
    user = (
        await EnsureUser(mini_world.uow_factory, mini_world.ids, mini_world.clock).execute(
            EnsureUserCommand(TelegramUserId(_TG))
        )
    ).user
    await ConfirmAge(mini_world.uow_factory, mini_world.clock).execute(ConfirmAgeCommand(user.id))
    delivery = FakeExportDelivery()
    app = _build_app(mini_world, delivery=delivery)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "consent"
        assert me.json()["account_exists"] is True
        exported = await client.post("/api/v1/me/export", headers=headers)
    assert exported.status_code == 202
    assert exported.json() == {"delivered_to": "bot_chat"}
    assert len(delivery.deliveries) == 1


@pytest.mark.unit
async def test_export_unknown_user_not_found(mini_world: AppWorld) -> None:
    app = _build_app(mini_world, delivery=FakeExportDelivery())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/me/export", headers=_auth_header(_TG))
    assert response.status_code == 404
    assert response.json()["code"] == MiniappErrorCode.NOT_FOUND


@pytest.mark.unit
async def test_privacy_texts_match_catalog(mini_world: AppWorld) -> None:
    catalog = load_privacy_catalog()
    app = _build_app(mini_world)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/privacy/texts", headers=_auth_header(_TG))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["export"]["description"] == catalog.export.description
    assert body["export"]["sections"] == catalog.export.sections
    assert body["revoke"]["description"] == catalog.revoke.description
    assert body["revoke"]["confirm"] == catalog.revoke.confirm
    assert body["delete"]["description"] == catalog.delete.description
    assert body["delete"]["confirm"] == catalog.delete.confirm


@pytest.mark.unit
async def test_delete_confirm_validation(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/api/v1/me/delete", headers=headers, json={})
        false_body = await client.post(
            "/api/v1/me/delete", headers=headers, json={"confirm": False}
        )
        extra = await client.post(
            "/api/v1/me/delete",
            headers=headers,
            json={"confirm": True, "user_id": "other"},
        )
    assert missing.status_code == 422
    assert false_body.status_code == 422
    assert extra.status_code == 422


@pytest.mark.unit
async def test_delete_idempotent_and_onboarding_reset(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.post("/api/v1/me/delete", headers=headers, json={"confirm": True})
        assert deleted.status_code == 204
        again = await client.post("/api/v1/me/delete", headers=headers, json={"confirm": True})
        assert again.status_code == 204
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "age"
        assert me.json()["account_exists"] is False
        contacts = await client.get("/api/v1/contacts", headers=headers)
        assert contacts.status_code == 403
        assert contacts.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED
