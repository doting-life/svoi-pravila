"""Unit tests for POST /api/v1/compose and /compose/choice."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.support.miniapp_bindings import build_test_miniapp_bindings
from tests.support.miniapp_decode import build_miniapp_decode_bundle
from tests.unit.api.test_miniapp_api import _auth_header
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, build_miniapp_router
from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    InvalidInlineResultRef,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    SafetyVerdict,
    SoftenResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.compose_generation import (
    ComposeGeneration,
    ComposeGenerationPorts,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.config import Environment
from svoi_pravila.domain.enums import (
    Firmness,
    RelationshipKind,
    UsageEventKind,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.text import ContactLabel


@dataclass
class _Boom:
    """Use-case stand-in that raises a configured exception."""

    exc: BaseException

    async def execute(self, _command: object) -> Any:
        raise self.exc


_TOKEN = "9:UNIT-MINIAPP"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 10_026
_TG_OTHER = 10_027
_CANARY = "COMPOSE_CANARY_DRAFT_0026_2_UNIQUE"


def _build_compose_app(
    world: AppWorld,
    *,
    generator: FakeTextGenerator | None = None,
    quota_gate: FakeQuotaGate | None = None,
    llm_budget: FakeLlmBudget | None = None,
) -> tuple[Any, FakeTextGenerator, Any]:
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    gen = generator or FakeTextGenerator()
    gate = quota_gate or FakeQuotaGate(limit=300)
    budget = llm_budget or FakeLlmBudget()
    decode_bundle = build_miniapp_decode_bundle(
        world,
        generator=gen,
        quota_gate=gate,
        llm_budget=budget,
    )
    compose = ComposeGeneration(
        ComposeGenerationPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=gen,
            quota_gate=gate,
            llm_budget=budget,
            sink=decode_bundle.sink,
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=decode_bundle.pseudonymizer,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=8.0,
            analytics_timezone="Europe/Moscow",
        )
    )
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=world,
        decode_bundle=decode_bundle,
        reuse=make_inline_reuse(world.clock),
        overrides={"compose_generation": compose},
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    return app, gen, decode_bundle


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
async def test_compose_soften_happy_path_and_choice(
    mini_world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    user = await mini_world.ensure_granted_user(_TG)
    contact = await CreateContact(
        mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
    ).execute(CreateContactCommand(user.id, ContactLabel("Аня"), RelationshipKind.PARTNER))
    gen = FakeTextGenerator(
        soften_result=SoftenResult(
            variants=(
                Variant(text="вариант мягко", firmness=Firmness.GENTLE),
                Variant(text="вариант баланс", firmness=Firmness.BALANCED),
                Variant(text="вариант твёрдо", firmness=Firmness.FIRM),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="soften@v4",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(input=2, output=2),
            ),
        )
    )
    app, wired, bundle = _build_compose_app(mini_world, generator=gen)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={
                "draft": _CANARY,
                "intent": "soften",
                "contact_id": str(contact.contact.id),
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["safety"] == "ok"
        assert len(body["variants"]) == 3
        assert body["applied_rules"] == []
        assert _CANARY not in response.text

        choice = await client.post(
            "/api/v1/compose/choice",
            headers=headers,
            json={"scenario": "soften", "firmness": "gentle"},
        )
        assert choice.status_code == 200
        assert choice.json() == {"outcome": "recorded"}

    assert wired.soften_calls
    assert wired.soften_calls[0].draft == _CANARY
    generations = [e for e in bundle.sink.events if e.event_kind is UsageEventKind.GENERATION]
    choices = [e for e in bundle.sink.events if e.event_kind is UsageEventKind.RESULT_CHOSEN]
    assert generations[0].surface is UsageSurface.MINIAPP
    assert choices[0].surface is UsageSurface.MINIAPP
    blob = str(capture_log_events())
    assert _CANARY not in blob
    assert "вариант мягко" not in blob


@pytest.mark.unit
async def test_compose_stranger_contact_not_found(mini_world: AppWorld) -> None:
    owner = await mini_world.ensure_granted_user(_TG)
    await mini_world.ensure_granted_user(_TG_OTHER)
    contact = await CreateContact(
        mini_world.uow_factory, mini_world.catalog, mini_world.ids, mini_world.clock
    ).execute(CreateContactCommand(owner.id, ContactLabel("Секрет"), RelationshipKind.FRIEND))
    app, _, _ = _build_compose_app(mini_world)
    headers = _auth_header(_TG_OTHER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={
                "draft": "достаточно длинный черновик",
                "intent": "soften",
                "contact_id": str(contact.contact.id),
            },
        )
    assert response.status_code == 404
    assert response.json()["code"] == MiniappErrorCode.NOT_FOUND


@pytest.mark.unit
async def test_compose_none_contact_and_help_say_intent(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app, gen, _ = _build_compose_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "нужно мягко отказать", "intent": "decline"},
        )
    assert response.status_code == 200
    assert gen.help_say_calls
    assert gen.help_say_calls[0].rules == ()
    assert response.json()["safety"] == "ok"


@pytest.mark.unit
async def test_compose_quota_exhausted(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app, _, _ = _build_compose_app(mini_world, quota_gate=FakeQuotaGate(limit=0))
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "длинный достаточно текст", "intent": "soften"},
        )
    assert response.status_code == 429
    assert response.json()["code"] == MiniappErrorCode.QUOTA_EXHAUSTED


@pytest.mark.unit
async def test_compose_choice_records_scenario(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app, _, bundle = _build_compose_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose/choice",
            headers=headers,
            json={"scenario": "help_say", "firmness": "firm"},
        )
    assert response.status_code == 200
    choices = [e for e in bundle.sink.events if e.event_kind is UsageEventKind.RESULT_CHOSEN]
    assert len(choices) == 1
    assert choices[0].scenario is UsageScenario.HELP_SAY
    assert choices[0].variant_firmness is Firmness.FIRM
    assert choices[0].surface is UsageSurface.MINIAPP


@pytest.mark.unit
async def test_compose_crisis_includes_lead_and_resources(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    app, gen, _ = _build_compose_app(mini_world)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "я не хочу жить больше", "intent": "soften"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["safety"] == "crisis"
    assert body["variants"] == []
    assert isinstance(body["lead"], str) and body["lead"]
    assert isinstance(body["resources"], list) and body["resources"]
    assert gen.soften_calls == []


@pytest.mark.unit
async def test_compose_maps_provider_errors(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    unavailable = FakeTextGenerator()
    unavailable.soften_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v4",
    )
    app, _, _ = _build_compose_app(mini_world, generator=unavailable)
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "длинный достаточно текст", "intent": "soften"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == MiniappErrorCode.GENERATION_UNAVAILABLE

    invalid = FakeTextGenerator()
    invalid.soften_error = InvalidGenerationOutput(
        (InvalidOutputReason.FIRMNESS_SET,),
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v4",
    )
    app, _, _ = _build_compose_app(mini_world, generator=invalid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose",
            headers=headers,
            json={"draft": "длинный достаточно текст", "intent": "soften"},
        )
    assert response.status_code == 502
    assert response.json()["code"] == MiniappErrorCode.INVALID_OUTPUT


@pytest.mark.unit
async def test_compose_maps_defensive_bound_errors(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), mini_world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(mini_world.uow_factory),
        get_onboarding_step=GetOnboardingStep(mini_world.uow_factory, mini_world.catalog),
    )
    decode_bundle = build_miniapp_decode_bundle(mini_world)
    headers = _auth_header(_TG)
    for exc, code, status in (
        (IncomingTextTooShort(), MiniappErrorCode.TEXT_TOO_SHORT, 422),
        (IncomingTextTooLong(), MiniappErrorCode.TEXT_TOO_LONG, 422),
    ):
        bindings = build_test_miniapp_bindings(
            auth=auth,
            world=mini_world,
            decode_bundle=decode_bundle,
            reuse=make_inline_reuse(mini_world.clock),
            overrides={"compose_generation": _Boom(exc)},
        )
        app = create_app(
            CheckReadiness(probes=(), timeout_seconds=1.0),
            Environment.TEST,
            AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/compose",
                headers=headers,
                json={"draft": "длинный достаточно текст", "intent": "soften"},
            )
        assert response.status_code == status
        assert response.json()["code"] == code


@pytest.mark.unit
async def test_compose_choice_maps_invalid_ref(mini_world: AppWorld) -> None:
    await mini_world.ensure_granted_user(_TG)
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), mini_world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(mini_world.uow_factory),
        get_onboarding_step=GetOnboardingStep(mini_world.uow_factory, mini_world.catalog),
    )
    decode_bundle = build_miniapp_decode_bundle(mini_world)
    bindings = build_test_miniapp_bindings(
        auth=auth,
        world=mini_world,
        decode_bundle=decode_bundle,
        reuse=make_inline_reuse(mini_world.clock),
        overrides={"record_choice": _Boom(InvalidInlineResultRef())},
    )
    app = create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        Environment.TEST,
        AppLifecycleHooks(extra_routers=(build_miniapp_router(bindings),)),
    )
    headers = _auth_header(_TG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/compose/choice",
            headers=headers,
            json={"scenario": "soften", "firmness": "gentle"},
        )
    assert response.status_code == 422
    assert response.json()["code"] == MiniappErrorCode.VALIDATION_ERROR
