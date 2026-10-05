"""Cover defensive AccessNotGranted / InvalidValueError branches in mini-app routers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.export_delivery import FakeExportDelivery
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.init_data import build_webapp_init_data
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings, build_miniapp_router
from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.api.miniapp.localization import load_ru_messages
from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.request_my_data_export import RequestMyDataExport
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import Environment
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.enums import ConsentKind, Firmness, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.text import RuleText

_TOKEN = "9:UNIT-BRANCHES"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 30_014
_DENIED = AccessNotGranted(
    AccessStatus(
        age_confirmed=True,
        missing_consents=frozenset({ConsentKind.PERSONAL_DATA}),
        granted=False,
    )
)


@dataclass
class _Boom:
    """Use-case stand-in that raises a configured exception."""

    exc: BaseException

    async def execute(self, _command: object) -> Any:
        raise self.exc


def _auth() -> dict[str, str]:
    raw = build_webapp_init_data(_TOKEN, user_id=_TG, auth_date=int(_NOW.timestamp()))
    return {"Authorization": f"tma {raw}"}


def _app_with_bindings(
    world: AppWorld,
    *,
    list_contacts: ListContacts | _Boom | None = None,
    create_contact: CreateContact | _Boom | None = None,
    rename_contact: RenameContact | _Boom | None = None,
    set_active_contact: SetActiveContact | _Boom | None = None,
    list_rules: ListRules | _Boom | None = None,
    propose_rule: ProposeRule | _Boom | None = None,
    archive_rule: ArchiveRule | _Boom | None = None,
    list_suggestions: ListSuggestions | _Boom | None = None,
    accept_suggestion: AcceptSuggestion | _Boom | None = None,
    dismiss_suggestion: DismissSuggestion | _Boom | None = None,
) -> Any:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    reuse = make_inline_reuse(world.clock)
    decode_bundle = build_miniapp_decode_bundle(world)
    bindings = MiniappRouterBindings(
        auth=auth,
        list_contacts=cast(
            ListContacts, list_contacts or ListContacts(world.uow_factory, world.catalog)
        ),
        create_contact=cast(
            CreateContact,
            create_contact
            or CreateContact(world.uow_factory, world.catalog, world.ids, world.clock),
        ),
        rename_contact=cast(
            RenameContact,
            rename_contact or RenameContact(world.uow_factory, world.catalog),
        ),
        set_active_contact=cast(
            SetActiveContact,
            set_active_contact or SetActiveContact(world.uow_factory, world.catalog),
        ),
        list_rules=cast(ListRules, list_rules or ListRules(world.uow_factory, world.catalog)),
        propose_rule=cast(
            ProposeRule,
            propose_rule or ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock),
        ),
        archive_rule=cast(
            ArchiveRule,
            archive_rule or ArchiveRule(world.uow_factory, world.catalog, world.clock),
        ),
        list_suggestions=cast(
            ListSuggestions,
            list_suggestions or ListSuggestions(world.uow_factory, world.catalog),
        ),
        accept_suggestion=cast(
            AcceptSuggestion,
            accept_suggestion
            or AcceptSuggestion(world.uow_factory, world.catalog, world.ids, world.clock),
        ),
        dismiss_suggestion=cast(
            DismissSuggestion,
            dismiss_suggestion or DismissSuggestion(world.uow_factory, world.catalog, world.clock),
        ),
        request_my_data_export=RequestMyDataExport(
            ExportMyData(world.uow_factory, world.clock),
            FakeExportDelivery(),
        ),
        revoke_all_consents=RevokeAllConsents(world.uow_factory, world.clock, reuse),
        delete_my_account=DeleteMyAccount(
            world.uow_factory,
            world.ids,
            FakePseudonymizer(),
            world.clock,
            reuse,
        ),
        export_rate_limiter=FakeRateLimiter(limit=3),
        display_timezone="Europe/Moscow",
        decode_incoming=decode_bundle.decode_incoming,
        suggest_rule_from_decode=decode_bundle.suggest_rule_from_decode,
        prepared_results=decode_bundle.prepared_results,
        rule_sources=decode_bundle.rule_sources,
        pseudonymizer=decode_bundle.pseudonymizer,
        enable_test_routes=True,
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )


@pytest.fixture
def world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
    )


@pytest.mark.unit
async def test_access_not_granted_mapped_on_each_mutating_route(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    boom = _Boom(_DENIED)
    app = _app_with_bindings(
        world,
        list_contacts=boom,
        create_contact=boom,
        rename_contact=boom,
        set_active_contact=boom,
        list_rules=boom,
        propose_rule=boom,
        archive_rule=boom,
        list_suggestions=boom,
        accept_suggestion=boom,
        dismiss_suggestion=boom,
    )
    cid = str(uuid4())
    rid = str(uuid4())
    sid = str(uuid4())
    cases = [
        ("GET", "/api/v1/contacts", None),
        ("POST", "/api/v1/contacts", {"label": "x", "relationship": "friend"}),
        ("PATCH", f"/api/v1/contacts/{cid}", {"label": "y"}),
        ("POST", f"/api/v1/contacts/{cid}/activate", None),
        ("GET", f"/api/v1/contacts/{cid}/rules", None),
        ("POST", f"/api/v1/contacts/{cid}/rules", {"category": "other", "text": "z"}),
        ("POST", f"/api/v1/rules/{rid}/archive", None),
        ("GET", f"/api/v1/contacts/{cid}/suggestions", None),
        ("POST", f"/api/v1/suggestions/{sid}/accept", None),
        ("POST", f"/api/v1/suggestions/{sid}/dismiss", None),
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path, body in cases:
            response = await client.request(method, path, headers=_auth(), json=body)
            assert response.status_code == 403, path
            assert response.json()["code"] == MiniappErrorCode.CONSENT_REQUIRED


@pytest.mark.unit
async def test_not_found_from_list_contacts(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    app = _app_with_bindings(world, list_contacts=_Boom(NotFound()))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/contacts", headers=_auth())
    assert response.status_code == 404


@pytest.mark.unit
async def test_invalid_label_and_rule_text_control_characters(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    app = _app_with_bindings(world)
    transport = ASGITransport(app=app)
    headers = _auth()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad_create = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "bad\x00label", "relationship": "friend"},
        )
        assert bad_create.status_code == 422
        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "ok", "relationship": "friend"},
        )
        contact_id = created.json()["id"]
        bad_rename = await client.patch(
            f"/api/v1/contacts/{contact_id}",
            headers=headers,
            json={"label": "bad\x00name"},
        )
        assert bad_rename.status_code == 422
        bad_rule = await client.post(
            f"/api/v1/contacts/{contact_id}/rules",
            headers=headers,
            json={"category": "other", "text": "bad\x00rule"},
        )
        assert bad_rule.status_code == 422


@pytest.mark.unit
async def test_require_actor_age_step_for_existing_user(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(_TG))
    )
    app = _app_with_bindings(world)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/contacts", headers=_auth())
    assert response.status_code == 403
    assert response.json()["code"] == MiniappErrorCode.ONBOARDING_REQUIRED


@pytest.mark.unit
async def test_tone_suggestion_firmness_in_list(world: AppWorld) -> None:
    user = await world.ensure_granted_user(_TG)
    app = _app_with_bindings(world)
    transport = ASGITransport(app=app)
    headers = _auth()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"label": "Tone", "relationship": "friend"},
        )
        contact_id = UUID(created.json()["id"])
        sid = world.ids.new_id()
        async with world.uow_factory() as uow:
            await uow.rule_suggestions.add(
                RuleSuggestion.create_tone(
                    suggestion_id=RuleSuggestionId(sid),
                    user_id=user.id,
                    contact_id=ContactId(contact_id),
                    category=RuleCategory.OTHER,
                    text=RuleText("tone rule"),
                    firmness=Firmness.FIRM,
                    now=world.clock.now(),
                )
            )
            await uow.commit()
        listed = await client.get(
            f"/api/v1/contacts/{contact_id}/suggestions",
            headers=headers,
        )
    assert listed.status_code == 200
    assert listed.json()["suggestions"][0]["firmness"] == "firm"


@pytest.mark.unit
def test_localization_rejects_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    load_ru_messages.cache_clear()

    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return "[1,2,3]"

    class _Files:
        def joinpath(self, _name: str) -> _Path:
            return _Path()

    monkeypatch.setattr(
        "svoi_pravila.api.miniapp.localization.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(TypeError, match="JSON object"):
        load_ru_messages()
    load_ru_messages.cache_clear()


@pytest.mark.unit
def test_localization_rejects_bad_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    load_ru_messages.cache_clear()

    class _Path:
        def read_text(self, encoding: str = "utf-8") -> str:
            _ = encoding
            return '{"unauthorized": ""}'

    class _Files:
        def joinpath(self, _name: str) -> _Path:
            return _Path()

    monkeypatch.setattr(
        "svoi_pravila.api.miniapp.localization.resources.files",
        lambda _pkg: _Files(),
    )
    with pytest.raises(TypeError, match="non-empty"):
        load_ru_messages()
    load_ru_messages.cache_clear()
