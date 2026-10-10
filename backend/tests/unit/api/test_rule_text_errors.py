"""Typed rule-text error codes and pending rules listing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.api.test_miniapp_api import _TG_A, _TG_B, _auth_header, _build_app
from tests.unit.application.conftest import AppWorld

from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS, ContactLabel

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG_S = 303


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
async def test_create_rule_typed_text_errors(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG_A)
    app = _build_app(mini_world)
    headers = _auth_header(_TG_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "Аня", "relationship": "partner"},
        )
        contact_id = created.json()["id"]

        empty = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={"category": "other", "text": "   ", "shared": False},
        )
        assert empty.status_code == 422
        assert empty.json()["code"] == "rule_text_empty"

        bad = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={"category": "other", "text": "a\tb", "shared": False},
        )
        assert bad.status_code == 422
        assert bad.json()["code"] == "rule_text_invalid_chars"

        too_long = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={
                "category": "other",
                "text": "x" * (RULE_TEXT_MAX_CHARS + 1),
                "shared": False,
            },
        )
        assert too_long.status_code == 422
        body = too_long.json()
        assert body["code"] == "rule_text_too_long"
        assert body["max"] == RULE_TEXT_MAX_CHARS
        assert body["actual"] == RULE_TEXT_MAX_CHARS + 1


@pytest.mark.unit
async def test_pending_rules_endpoint_and_idor(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG_A)
    invitee = await mini_world.ensure_granted_user(_TG_B)
    await mini_world.ensure_granted_user(_TG_S)
    app = _build_app(mini_world)
    headers_a = _auth_header(_TG_A)
    headers_b = _auth_header(_TG_B)
    headers_s = _auth_header(_TG_S)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        contact = await client.post(
            "/api/v1/contacts",
            headers=headers_a,
            json={"label": "Partner", "relationship": "partner"},
        )
        contact_id = contact.json()["id"]
        invite = await client.post(f"/api/v1/contacts/{contact_id}/invite", headers=headers_a)
        raw_token = invite.json()["link"].rsplit("inv_", 1)[1]

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
                RelationshipKind.PARTNER,
            )
        )

        created = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers_a,
            json={"category": "other", "text": "общее\nдля двоих", "shared": True},
        )
        assert created.status_code == 201
        rule_id = created.json()["id"]

        pending_b = await client.get("/api/v1/rules/pending", headers=headers_b)
        assert pending_b.status_code == 200
        items = pending_b.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == rule_id
        assert "общее" in items[0]["text"]

        pending_a = await client.get("/api/v1/rules/pending", headers=headers_a)
        assert pending_a.json()["items"] == []

        pending_s = await client.get("/api/v1/rules/pending", headers=headers_s)
        assert pending_s.json()["items"] == []

        forbidden = await client.post(f"/api/v1/rules/{rule_id}/approve", headers=headers_s)
        assert forbidden.status_code == 404
