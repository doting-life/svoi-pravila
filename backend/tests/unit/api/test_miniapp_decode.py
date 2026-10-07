"""Unit tests for mini-app decode SSE and suggest-from-decode."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.fakes.clock import FakeClock
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.rule_sources import FakeRuleSources
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink
from tests.support.init_data import InitDataOptions, build_webapp_init_data
from tests.support.miniapp_decode import MiniappDecodeBundle, build_miniapp_decode_bundle
from tests.unit.application.conftest import AppWorld

from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.adapters.channels.telegram.localization import render_crisis_message
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.miniapp import MiniappDeps, MiniappRouterBindings, build_miniapp_router
from svoi_pravila.api.miniapp.decode_sse import _completed_payload, format_sse
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.decode_sealing import (
    DecodeSealPorts,
    DecodeSealRequest,
    seal_decode_outcome,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    DecodeResult,
    GenerationMeta,
    HelpSayRequest,
    HelpSayResult,
    SafetyVerdict,
    SoftenRequest,
    SoftenResult,
    SuggestRuleProposed,
    SuggestRuleRequest,
    SuggestRuleResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.rule_source import RuleSourcePayload
from svoi_pravila.application.support_resources import (
    load_applied_rule_template,
    load_crisis_lead,
    load_support_resources,
)
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.config import Environment
from svoi_pravila.domain.enums import Firmness, RelationshipKind, RuleCategory, UsageSurface
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.text import ContactLabel, RuleText

_TOKEN = "9:UNIT-DECODE"
_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_TG = 55_015
_CANARY = "CANARY-MINIAPP-DECODE-UNIQUE-MARKER-χ"


def _auth(telegram_id: int = _TG) -> dict[str, str]:
    raw = build_webapp_init_data(
        _TOKEN,
        user_id=telegram_id,
        auth_date=int(_NOW.timestamp()),
        options=InitDataOptions(extra={"query_id": "decode-qid"}),
    )
    return {"Authorization": f"tma {raw}"}


def _meta() -> GenerationMeta:
    return GenerationMeta(
        model="fake",
        prompt_version="decode@v1",
        latency_ms=1,
        attempts=1,
        usage=TokenUsage(input=1, output=1),
    )


def _ok_result() -> DecodeResult:
    return DecodeResult(
        safety=SafetyVerdict.OK,
        hypotheses=("h1",),
        underlying_request="u",
        variants=(
            Variant(text="мягко", firmness=Firmness.GENTLE),
            Variant(text="твёрдо", firmness=Firmness.FIRM),
        ),
        applied_rule_indexes=(),
        meta=_meta(),
    )


@dataclass
class _SlowGenerator:
    """Yields one chunk, waits on a gate, then completes."""

    started: asyncio.Event
    gate: asyncio.Event
    completed: bool = False
    max_billable_value: int = 1000

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        self.completed = False
        self.max_billable_value = 1000

    def max_billable(
        self,
        request: SoftenRequest | HelpSayRequest | DecodeRequest | SuggestRuleRequest,
    ) -> int:
        _ = request
        return self.max_billable_value

    async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        _ = request
        self.started.set()
        yield AnalysisChunk(text="часть")
        await self.gate.wait()
        yield DecodeCompleted(analysis="часть", result=_ok_result())
        self.completed = True

    async def soften(self, request: SoftenRequest) -> SoftenResult:
        raise RuntimeError(f"unused soften: {request!r}")

    async def help_say(self, request: HelpSayRequest) -> HelpSayResult:
        raise RuntimeError(f"unused help_say: {request!r}")

    async def suggest_rule(self, request: SuggestRuleRequest) -> SuggestRuleResult:
        raise RuntimeError(f"unused suggest_rule: {request!r}")


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
        elif line == "" and data_lines:
            raw = "\n".join(data_lines)
            events.append((event_name, json.loads(raw) if raw else {}))
            event_name = "message"
            data_lines = []
    if data_lines:
        raw = "\n".join(data_lines)
        events.append((event_name, json.loads(raw) if raw else {}))
    return events


def _bindings(
    world: AppWorld,
    bundle: MiniappDecodeBundle,
    *,
    enable_test_routes: bool = True,
) -> MiniappRouterBindings:
    from tests.support.miniapp_bindings import build_test_miniapp_bindings

    reuse = make_inline_reuse(world.clock)
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            SecretStr(_TOKEN), world.clock, max_age_seconds=3600
        ),
        rate_limiter=FakeRateLimiter(limit=120),
        pseudonymizer=FakePseudonymizer(),
        get_user_by_telegram_id=GetUserByTelegramId(world.uow_factory),
        get_onboarding_step=GetOnboardingStep(world.uow_factory, world.catalog),
    )
    return build_test_miniapp_bindings(
        auth=auth,
        world=world,
        decode_bundle=bundle,
        reuse=reuse,
        enable_test_routes=enable_test_routes,
    )


def _app(
    world: AppWorld,
    bundle: MiniappDecodeBundle,
    *,
    environment: Environment = Environment.TEST,
    enable_test_routes: bool | None = None,
) -> Any:
    routes_enabled = (
        enable_test_routes if enable_test_routes is not None else environment is Environment.TEST
    )
    return create_app(
        CheckReadiness(probes=(), timeout_seconds=1.0),
        environment,
        AppLifecycleHooks(
            extra_routers=(
                build_miniapp_router(
                    _bindings(world, bundle, enable_test_routes=routes_enabled),
                ),
            ),
        ),
    )


async def _activate_contact(world: AppWorld, telegram_id: int = _TG) -> ContactId:
    user = await world.ensure_granted_user(telegram_id)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(
                actor_id=user.id,
                label=ContactLabel("Аня"),
                relationship=RelationshipKind.PARTNER,
            )
        )
    ).contact
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(actor_id=user.id, contact_id=contact.id)
    )
    return contact.id


@pytest.fixture
def world() -> AppWorld:
    return AppWorld(
        uow_factory=InMemoryUnitOfWorkFactory(),
        clock=FakeClock(start=_NOW),
        ids=FakeIdGenerator(),
        tokens=FakeTokenGenerator(),
        catalog=FakeConsentCatalog(),
        notifier=FakePairNotifier(),
    )


@pytest.mark.unit
async def test_decode_completed_event_order_and_miniapp_surface(world: AppWorld) -> None:
    await _activate_contact(world)
    gen = FakeTextGenerator(stream_chunks=("a", "b"), decode_result=_ok_result())
    bundle = build_miniapp_decode_bundle(world, generator=gen)
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "привет, как дела?"},
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "text/event-stream" in response.headers["content-type"]
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert names == ["analysis", "analysis", "completed"]
    completed = events[-1][1]
    assert completed["safety"] == "ok"
    assert len(completed["variants"]) == 2
    assert all(v["insert_query"] for v in completed["variants"])
    assert completed["rule_source_token"] is not None
    assert completed["applied_rule_template"] == load_applied_rule_template()
    assert bundle.sink.events
    assert all(event.surface is UsageSurface.MINIAPP for event in bundle.sink.events)


@pytest.mark.unit
async def test_decode_crisis_terminal(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    crisis_text = "хочу покончить с собой"
    assert CrisisScreen.load_ru_v2().hit(crisis_text)
    bundle = build_miniapp_decode_bundle(world)
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": crisis_text},
        )
    assert _parse_sse(response.text) == [
        (
            "crisis",
            {
                "lead": load_crisis_lead(),
                "resources": list(load_support_resources()),
            },
        ),
    ]


@pytest.mark.unit
def test_crisis_copy_shared_between_bot_and_api() -> None:
    lead = load_crisis_lead()
    resources = list(load_support_resources())
    bot_message = render_crisis_message()
    assert bot_message == f"{lead}\n\n" + "\n".join(resources)
    assert lead
    assert resources
    assert all(line in bot_message for line in resources)


@pytest.mark.unit
async def test_decode_refused_terminal(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    gen = FakeTextGenerator(
        stream_chunks=(),
        decode_result=DecodeResult(
            safety=SafetyVerdict.REFUSE_MANIPULATION,
            hypotheses=(),
            underlying_request="",
            variants=(),
            applied_rule_indexes=(),
            meta=_meta(),
        ),
    )
    app = _app(world, build_miniapp_decode_bundle(world, generator=gen))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "обычный текст"},
        )
    assert [name for name, _ in _parse_sse(response.text)] == ["refused"]


@pytest.mark.unit
async def test_decode_error_events(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    unavailable = FakeTextGenerator(
        stream_error=GenerationUnavailable(
            kind=UnavailableKind.SERVER,
            model="fake",
            prompt_version="decode@v1",
            attempts=1,
            usage=TokenUsage(),
        )
    )
    app = _app(world, build_miniapp_decode_bundle(world, generator=unavailable))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "текст для ошибки"},
        )
    assert _parse_sse(response.text)[-1] == ("error", {"code": "generation_unavailable"})

    invalid = FakeTextGenerator(
        stream_error=InvalidGenerationOutput(
            reasons=(InvalidOutputReason.SCHEMA_VIOLATION,),
            model="fake",
            prompt_version="decode@v1",
            attempts=1,
            usage=TokenUsage(),
        )
    )
    app2 = _app(world, build_miniapp_decode_bundle(world, generator=invalid))
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
        response2 = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "текст для invalid"},
        )
    assert _parse_sse(response2.text)[-1] == ("error", {"code": "invalid_output"})


@pytest.mark.unit
async def test_decode_validation_before_stream(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    app = _app(world, build_miniapp_decode_bundle(world))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.post("/api/v1/decode", headers=_auth(), json={"text": ""})
        assert empty.status_code == 422
        assert empty.json()["code"] == "validation_error"
        too_long = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "x" * 4001},
        )
        assert too_long.status_code == 422
        unauth = await client.post("/api/v1/decode", json={"text": "hi"})
        assert unauth.status_code == 401


@pytest.mark.unit
async def test_decode_quota_exhausted_json_before_sse(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    app = _app(
        world,
        build_miniapp_decode_bundle(world, quota_gate=FakeQuotaGate(limit=0)),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "обычный текст для квоты"},
        )
    assert response.status_code == 429
    assert response.headers.get("content-type", "").startswith("application/json")
    body = response.json()
    assert body["code"] == "quota_exhausted"
    assert body["message"]
    assert "retry_at" in body
    assert "event:" not in response.text


@pytest.mark.unit
async def test_decode_service_budget_exhausted_json_before_sse(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    app = _app(
        world,
        build_miniapp_decode_bundle(world, llm_budget=FakeLlmBudget(exhausted=True)),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "обычный текст для бюджета"},
        )
    assert response.status_code == 503
    assert response.headers.get("Retry-After") is not None
    body = response.json()
    assert body["code"] == "service_budget_exhausted"
    assert body["message"]
    assert "retry_at" in body


@pytest.mark.unit
async def test_decode_crisis_with_quota_and_budget_exhausted(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    crisis_text = "хочу покончить с собой"
    app = _app(
        world,
        build_miniapp_decode_bundle(
            world,
            quota_gate=FakeQuotaGate(limit=0),
            llm_budget=FakeLlmBudget(exhausted=True),
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": crisis_text},
        )
    assert response.status_code == 200
    assert [name for name, _ in _parse_sse(response.text)] == ["crisis"]


@pytest.mark.unit
async def test_decode_empty_stream_returns_empty_sse(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)

    class _EmptyStream:
        async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
            del request
            for _ in ():
                yield AnalysisChunk(text="")

    app = _app(world, build_miniapp_decode_bundle(world, generator=cast(Any, _EmptyStream())))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "текст без событий"},
        )
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")
    assert response.text == ""


@pytest.mark.unit
async def test_decode_client_cancel_releases_without_completion(world: AppWorld) -> None:
    """Cancelling the SSE consumer must not leave DecodeIncoming hung on the lock."""
    await world.ensure_granted_user(_TG)

    slow = _SlowGenerator()
    sink = RecordingUsageEventSink()
    quota_gate = FakeQuotaGate(limit=20)
    budget = FakeLlmBudget()
    text = "текст для отмены потока"
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=slow,
            guard=FakeConcurrencyGuard(),
            quota_gate=quota_gate,
            llm_budget=budget,
            sink=sink,
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
            analytics_timezone="Europe/Moscow",
        )
    )
    from svoi_pravila.api.miniapp.decode_sse import DecodeStreamPorts, iter_decode_sse
    from svoi_pravila.domain.ids import TelegramUserId

    user = await world.ensure_granted_user(_TG)
    ports = DecodeStreamPorts(
        decode_incoming=decode,
        prepared_results=FakePreparedResults(),
        rule_sources=FakeRuleSources(),
        pseudonymizer=FakePseudonymizer(),
    )

    async def _consume() -> None:
        agen: AsyncGenerator[str] = iter_decode_sse(
            ports,
            actor=user,
            telegram_user_id=TelegramUserId(_TG),
            text=text,
        )
        first = await agen.__anext__()
        assert "analysis" in first
        async for _frame in agen:
            pass

    task = asyncio.create_task(_consume())
    await slow.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slow.completed is False
    assert quota_gate.refund_calls == []
    assert budget.spent == 1000


@pytest.mark.unit
async def test_decode_sse_cancelled_error_propagates(world: AppWorld) -> None:
    """CancelledError must not be mapped to an SSE error frame."""
    from svoi_pravila.api.miniapp.decode_sse import DecodeStreamPorts, iter_decode_sse
    from svoi_pravila.domain.ids import TelegramUserId

    class _CancelOnExecute:
        def execute(self, command: object) -> AsyncGenerator[object]:
            raise asyncio.CancelledError

    user = await world.ensure_granted_user(_TG)
    ports = DecodeStreamPorts(
        decode_incoming=_CancelOnExecute(),  # type: ignore[arg-type]
        prepared_results=FakePreparedResults(),
        rule_sources=FakeRuleSources(),
        pseudonymizer=FakePseudonymizer(),
    )
    agen = iter_decode_sse(
        ports,
        actor=user,
        telegram_user_id=TelegramUserId(_TG),
        text="cancel probe",
    )
    with pytest.raises(asyncio.CancelledError):
        await agen.__anext__()


@pytest.mark.unit
def test_sse_error_for_rejects_unmapped() -> None:
    from svoi_pravila.api.miniapp.decode_sse import _sse_error_for

    with pytest.raises(TypeError, match="unmapped"):
        _sse_error_for(RuntimeError("x"))


@pytest.mark.unit
async def test_decode_privacy_canary_absent_from_logs(
    world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    await _activate_contact(world)
    prepared = FakePreparedResults()
    sources = FakeRuleSources()
    gen = FakeTextGenerator(
        stream_chunks=(_CANARY,),
        decode_result=DecodeResult(
            safety=SafetyVerdict.OK,
            hypotheses=(),
            underlying_request="",
            variants=(Variant(text=_CANARY, firmness=Firmness.BALANCED),),
            applied_rule_indexes=(),
            meta=_meta(),
        ),
    )
    bundle = build_miniapp_decode_bundle(
        world,
        generator=gen,
        prepared_results=prepared,
        rule_sources=sources,
    )
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/decode", headers=_auth(), json={"text": _CANARY})
    blob = " ".join(str(event) for event in capture_log_events())
    assert _CANARY not in blob
    assert prepared.items
    for token, (_owner, variant) in prepared.items.items():
        assert _CANARY not in token
        assert variant.text == _CANARY
    assert sources.items
    for token, (_owner, payload) in sources.items.items():
        assert _CANARY not in token
        assert payload.incoming_text == _CANARY


@pytest.mark.unit
async def test_suggest_from_decode_ok_and_unavailable(world: AppWorld) -> None:
    contact_id = await _activate_contact(world)
    sources = FakeRuleSources()
    gen = FakeTextGenerator()
    gen.suggest_rule_result = SuggestRuleProposed(
        category=RuleCategory.OTHER,
        text=RuleText("не повышать голос"),
        meta=_meta(),
    )
    bundle = build_miniapp_decode_bundle(world, generator=gen, rule_sources=sources)
    token = await sources.store(
        FakePseudonymizer().pseudonymize("rule_source", str(_TG)),
        RuleSourcePayload(contact_id=contact_id, incoming_text="входящий"),
    )
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post(
            "/api/v1/suggestions/from-decode",
            headers=_auth(),
            json={"token": token},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["outcome"] == "ok"
        assert body["suggestion"]["text"] == "не повышать голос"
        missing = await client.post(
            "/api/v1/suggestions/from-decode",
            headers=_auth(),
            json={"token": "missing-token"},
        )
        assert missing.json()["outcome"] == "unavailable"


@pytest.mark.unit
async def test_suggest_from_decode_crisis_returns_support_copy(world: AppWorld) -> None:
    contact_id = await _activate_contact(world)
    sources = FakeRuleSources()
    bundle = build_miniapp_decode_bundle(world, rule_sources=sources)
    token = await sources.store(
        FakePseudonymizer().pseudonymize("rule_source", str(_TG)),
        RuleSourcePayload(contact_id=contact_id, incoming_text="хочу покончить с собой"),
    )
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suggestions/from-decode",
            headers=_auth(),
            json={"token": token},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "crisis"
    assert body["lead"] == load_crisis_lead()
    assert body["resources"] == list(load_support_resources())


@pytest.mark.unit
async def test_suggest_from_decode_service_budget_exhausted(world: AppWorld) -> None:
    contact_id = await _activate_contact(world)
    sources = FakeRuleSources()
    bundle = build_miniapp_decode_bundle(
        world,
        rule_sources=sources,
        suggest_llm_budget=FakeLlmBudget(exhausted=True),
    )
    token = await sources.store(
        FakePseudonymizer().pseudonymize("rule_source", str(_TG)),
        RuleSourcePayload(contact_id=contact_id, incoming_text="предложи правило"),
    )
    app = _app(world, bundle)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/suggestions/from-decode",
            headers=_auth(),
            json={"token": token},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "service_budget_exhausted"
    assert body["message"]
    assert "retry_at" in body


@pytest.mark.unit
async def test_seal_helper_parity_insert_and_rule_source_token() -> None:
    completed = DecodeCompleted(analysis="a", result=_ok_result())
    prepared = FakePreparedResults()
    sources = FakeRuleSources()
    sealed = await seal_decode_outcome(
        DecodeSealPorts(prepared, sources, FakePseudonymizer()),
        DecodeSealRequest(
            telegram_user_id=_TG,
            incoming_text="incoming",
            completed=completed,
            active_contact_id=ContactId(uuid4()),
        ),
    )
    assert all(query is not None and query.startswith("p_") for query in sealed.insert_queries)
    assert sealed.rule_source_token is not None
    assert len(sealed.rule_source_token) > 0


@pytest.mark.unit
async def test_seal_helper_skips_empty_variant_text() -> None:
    result = DecodeResult(
        safety=SafetyVerdict.OK,
        hypotheses=(),
        underlying_request="",
        variants=(
            Variant(text="", firmness=Firmness.GENTLE),
            Variant(text="есть текст", firmness=Firmness.FIRM),
        ),
        applied_rule_indexes=(),
        meta=_meta(),
    )
    sealed = await seal_decode_outcome(
        DecodeSealPorts(FakePreparedResults(), FakeRuleSources(), FakePseudonymizer()),
        DecodeSealRequest(
            telegram_user_id=_TG,
            incoming_text="incoming",
            completed=DecodeCompleted(analysis="a", result=result),
            active_contact_id=None,
        ),
    )
    assert sealed.insert_queries[0] is None
    assert sealed.insert_queries[1] is not None
    assert sealed.rule_source_token is None


@pytest.mark.unit
def test_format_sse() -> None:
    assert format_sse("analysis", {"chunk": "x"}) == 'event: analysis\ndata: {"chunk":"x"}\n\n'
    assert format_sse("refused") == "event: refused\ndata: {}\n\n"


@pytest.mark.unit
def test_completed_payload_pads_missing_insert_queries() -> None:
    payload = _completed_payload(
        DecodeCompleted(analysis="a", result=_ok_result()),
        insert_queries=(None,),
        rule_source_token=None,
    )
    assert payload["variants"][0]["insert_query"] is None
    assert payload["variants"][1]["insert_query"] is None


@pytest.mark.unit
async def test_decode_provider_refused_and_empty_stream(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)
    refused = FakeTextGenerator(
        stream_error=GenerationRefusedByProvider(
            usage=TokenUsage(),
            attempts=1,
            model="fake",
            prompt_version="decode@v1",
        )
    )
    app = _app(world, build_miniapp_decode_bundle(world, generator=refused))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "текст для refuse"},
        )
    assert [name for name, _ in _parse_sse(response.text)] == ["refused"]

    empty = FakeTextGenerator(stream_chunks=())
    empty.emit_completed = False
    app2 = _app(world, build_miniapp_decode_bundle(world, generator=empty))
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
        response2 = await client.post(
            "/api/v1/decode",
            headers=_auth(),
            json={"text": "текст без событий"},
        )
    assert response2.status_code == 200
    assert _parse_sse(response2.text) == []


@pytest.mark.unit
async def test_decode_unknown_stream_error_propagates(world: AppWorld) -> None:
    await world.ensure_granted_user(_TG)

    class _BoomGenerator:
        fail: bool = True

        def max_billable(
            self,
            request: SoftenRequest | HelpSayRequest | DecodeRequest | SuggestRuleRequest,
        ) -> int:
            _ = request
            return 1000

        async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
            _ = request
            if self.fail:
                raise RuntimeError("unexpected-decode-failure")
            yield AnalysisChunk(text="")

        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise RuntimeError(f"unused soften: {request!r}")

        async def help_say(self, request: HelpSayRequest) -> HelpSayResult:
            raise RuntimeError(f"unused help_say: {request!r}")

        async def suggest_rule(self, request: SuggestRuleRequest) -> SuggestRuleResult:
            raise RuntimeError(f"unused suggest_rule: {request!r}")

    sink = RecordingUsageEventSink()
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=_BoomGenerator(),
            guard=FakeConcurrencyGuard(),
            quota_gate=FakeQuotaGate(limit=20),
            llm_budget=FakeLlmBudget(),
            sink=sink,
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=45.0,
            analytics_timezone="Europe/Moscow",
        )
    )
    bundle = build_miniapp_decode_bundle(world)
    app = _app(
        world,
        MiniappDecodeBundle(
            decode_incoming=decode,
            suggest_rule_from_decode=bundle.suggest_rule_from_decode,
            prepared_results=bundle.prepared_results,
            rule_sources=bundle.rule_sources,
            pseudonymizer=bundle.pseudonymizer,
            sink=sink,
            generator=bundle.generator,
        ),
    )
    with pytest.raises(RuntimeError, match="unexpected-decode-failure"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/decode",
                headers=_auth(),
                json={"text": "текст для unexpected"},
            )


@pytest.mark.unit
async def test_sse_flush_probe_endpoint(world: AppWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)
    app = _app(world, build_miniapp_decode_bundle(world))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/_test/sse-flush")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert _parse_sse(response.text) == [
        ("probe", {"phase": "first"}),
        ("probe", {"phase": "done"}),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.PRODUCTION])
async def test_sse_flush_absent_outside_test(world: AppWorld, environment: Environment) -> None:
    """LOCAL/PRODUCTION use the same enable_test_routes flag as bootstrap."""
    assert (environment is Environment.TEST) is False
    app = _app(world, build_miniapp_decode_bundle(world), environment=environment)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/_test/sse-flush")
    assert response.status_code == 404
    paths = app.openapi().get("paths", {})
    assert "/api/v1/_test/sse-flush" not in paths
