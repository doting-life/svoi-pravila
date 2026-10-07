"""Unit tests for mini-app privacy endpoints: export download, revoke, delete."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.export_download import FakeExportDownloadStore
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
from svoi_pravila.application.use_cases.export_my_data import ExportMyData, ExportMyDataCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.privacy import load_privacy_catalog

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 10_015
_TG_OTHER = 10_016
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
async def test_export_issue_and_download_once(
    mini_world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    await CreateContact(
        mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
    ).execute(
        CreateContactCommand(user.id, ContactLabel(_EXPORT_SENTINEL), RelationshipKind.FRIEND)
    )
    store = FakeExportDownloadStore()
    app = _build_app(mini_world, export_store=store)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/me/export", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert "download_url" in body
        assert "expires_at" in body
        assert _EXPORT_SENTINEL not in response.text
        token = urlparse(body["download_url"]).path.rsplit("/", 1)[-1]
        assert body["download_url"] == f"https://miniapp.test/api/v1/downloads/{token}"

        downloaded = await client.get(f"/api/v1/downloads/{token}")
        assert downloaded.status_code == 200
        assert downloaded.headers["cache-control"] == "no-store"
        assert 'filename="svoi-pravila-export.json"' in downloaded.headers["content-disposition"]
        payload = downloaded.json()
        assert _EXPORT_SENTINEL in str(payload)

        expected = await ExportMyData(mini_world.uow_factory, mini_world.clock).execute(
            ExportMyDataCommand(telegram_user_id=TelegramUserId(_TG))
        )
        assert expected.found and expected.payload is not None
        assert payload == expected.payload

        reuse = await client.get(f"/api/v1/downloads/{token}")
        assert reuse.status_code == 404
        assert reuse.json()["code"] == MiniappErrorCode.NOT_FOUND

    blob = str(capture_log_events())
    assert _EXPORT_SENTINEL not in blob


@pytest.mark.unit
async def test_export_download_serves_only_token_owner_payload(mini_world: AppWorld) -> None:
    user_a = await mini_world.ensure_granted_user(_TG)
    await mini_world.ensure_granted_user(_TG_OTHER)
    await CreateContact(
        mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
    ).execute(
        CreateContactCommand(user_a.id, ContactLabel(_EXPORT_SENTINEL), RelationshipKind.FRIEND)
    )
    store = FakeExportDownloadStore()
    app = _build_app(mini_world, export_store=store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        issued_a = await client.post("/api/v1/me/export", headers=_auth_header(_TG))
        issued_b = await client.post("/api/v1/me/export", headers=_auth_header(_TG_OTHER))
        token_a = urlparse(issued_a.json()["download_url"]).path.rsplit("/", 1)[-1]
        token_b = urlparse(issued_b.json()["download_url"]).path.rsplit("/", 1)[-1]
        assert token_a != token_b
        missing = await client.get("/api/v1/downloads/not-a-real-token")
        assert missing.status_code == 404
        assert missing.json()["code"] == MiniappErrorCode.NOT_FOUND
        body_b = (await client.get(f"/api/v1/downloads/{token_b}")).json()
        body_a = (await client.get(f"/api/v1/downloads/{token_a}")).json()
        assert _EXPORT_SENTINEL in str(body_a)
        assert _EXPORT_SENTINEL not in str(body_b)


@pytest.mark.unit
async def test_export_without_miniapp_url_unavailable(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world, miniapp_url=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/me/export", headers=_auth_header(_TG))
    assert response.status_code == 503
    assert response.json()["code"] == MiniappErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.unit
async def test_export_fourth_call_rate_limited(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app = _build_app(mini_world, export_limit=3)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(3):
            ok = await client.post("/api/v1/me/export", headers=headers)
            assert ok.status_code == 200
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
    app = _build_app(mini_world)
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
        assert exported.status_code == 200
        assert "download_url" in exported.json()
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
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["onboarding_step"] == "consent"
        assert me.json()["account_exists"] is True
        exported = await client.post("/api/v1/me/export", headers=headers)
    assert exported.status_code == 200
    assert "download_url" in exported.json()


@pytest.mark.unit
async def test_export_unknown_user_not_found(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
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


@pytest.mark.unit
async def test_onboarding_age_and_consents_via_api(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me0 = await client.get("/api/v1/me", headers=headers)
        assert me0.json()["onboarding_step"] == "age"
        assert me0.json()["account_exists"] is False

        age = await client.post("/api/v1/me/age-confirmation", headers=headers)
        assert age.status_code == 204

        me1 = await client.get("/api/v1/me", headers=headers)
        assert me1.json()["onboarding_step"] == "consent"
        assert me1.json()["account_exists"] is True
        kind = me1.json()["consent_kind"]
        version = me1.json()["consent_version"]
        assert kind in {"personal_data", "special_category"}
        assert version

        doc = await client.get(f"/api/v1/consents/{kind}/document", headers=headers)
        assert doc.status_code == 200
        assert doc.json()["kind"] == kind
        assert doc.json()["version"] == version
        assert doc.json()["text"]

        stale = await client.post(
            "/api/v1/me/consents",
            headers=headers,
            json={"kind": kind, "text_version": "not-current"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == MiniappErrorCode.CONSENT_STALE

        while True:
            me = await client.get("/api/v1/me", headers=headers)
            step = me.json()["onboarding_step"]
            if step == "done":
                break
            assert step == "consent"
            grant = await client.post(
                "/api/v1/me/consents",
                headers=headers,
                json={
                    "kind": me.json()["consent_kind"],
                    "text_version": me.json()["consent_version"],
                },
            )
            assert grant.status_code == 204

        contacts = await client.get("/api/v1/contacts", headers=headers)
        assert contacts.status_code == 200


@pytest.mark.unit
async def test_onboarding_consent_document_and_grant_without_user(mini_world: AppWorld) -> None:
    app = _build_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown_kind = await client.get("/api/v1/consents/not_a_kind/document", headers=headers)
        assert unknown_kind.status_code == 404
        assert unknown_kind.json()["code"] == MiniappErrorCode.NOT_FOUND

        grant_before_age = await client.post(
            "/api/v1/me/consents",
            headers=headers,
            json={"kind": "personal_data", "text_version": "v1"},
        )
        assert grant_before_age.status_code == 403
        assert grant_before_age.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED
