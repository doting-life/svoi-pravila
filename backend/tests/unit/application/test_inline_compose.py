"""InlineCompose use-case tests."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    AccessNotGranted,
    CacheErrorKind,
    CacheUnavailable,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InlineComposeFailed,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    ServiceBudgetExhausted,
    UnavailableKind,
    UserQuotaExhausted,
)
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
    BOUNDED_TEXT_MIN,
    GenerationMeta,
    RewriteRequest,
    RewriteResult,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.inline_result_reuse import InlineResultReuse
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, BudgetOk
from svoi_pravila.application.use_cases.compose_generation import (
    ComposeGeneration,
    ComposeGenerationCommand,
    ComposeGenerationPorts,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
    DeleteMyAccountPorts,
)
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.inline_compose import (
    InlineCompose,
    InlineComposeCommand,
    InlineComposePorts,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.revoke_all_consents import (
    RevokeAllConsents,
    RevokeAllConsentsCommand,
)
from svoi_pravila.domain.enums import (
    LimitKind,
    RelationshipKind,
    RuleCategory,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
    VariantKind,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.usage import UsageEvent
from svoi_pravila.domain.variant_policy import PolicyRule
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink
from tests.fakes.variant_policy import RecordingVariantPolicyMonitor
from tests.unit.application.conftest import AppWorld
from tests.unit.domain.test_crisis_screen import THREAT_AND_HYPERBOLE_NEGATIVES


@dataclass(frozen=True, slots=True)
class _Fakes:
    generator: FakeTextGenerator | None = None
    quota_gate: FakeQuotaGate | None = None
    llm_budget: FakeLlmBudget | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    reuse: InlineResultReuse | None = None
    min_chars: int = 8
    policy_monitor: RecordingVariantPolicyMonitor | None = None


def _compose_generation(
    world: AppWorld, fakes: _Fakes | None = None
) -> tuple[ComposeGeneration, RecordingUsageEventSink | FailingUsageEventSink]:
    chosen = fakes or _Fakes()
    sink = chosen.sink if chosen.sink is not None else RecordingUsageEventSink()
    return (
        ComposeGeneration(
            ComposeGenerationPorts(
                uow_factory=world.uow_factory,
                catalog=world.catalog,
                generator=chosen.generator or FakeTextGenerator(),
                quota_gate=chosen.quota_gate or FakeQuotaGate(limit=30),
                llm_budget=chosen.llm_budget or FakeLlmBudget(),
                sink=sink,
                policy_monitor=chosen.policy_monitor or RecordingVariantPolicyMonitor(),
                clock=world.clock,
                monotonic=world.clock,
                ids=world.ids,
                pseudonymizer=FakePseudonymizer(),
                crisis_screen=CrisisScreen.load_ru_v2(),
                deadline_seconds=8.0,
                analytics_timezone="Europe/Moscow",
            )
        ),
        sink,
    )


def _ports(
    world: AppWorld, fakes: _Fakes | None = None
) -> tuple[
    InlineCompose,
    RecordingUsageEventSink | FailingUsageEventSink,
    InlineResultReuse,
]:
    chosen = fakes or _Fakes()
    reuse: InlineResultReuse = (
        chosen.reuse if chosen.reuse is not None else make_inline_reuse(world.clock)
    )
    compose_generation, sink = _compose_generation(world, chosen)
    use_case = InlineCompose(
        InlineComposePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            compose=compose_generation,
            reuse=reuse,
            min_chars=chosen.min_chars,
        )
    )
    return use_case, sink, reuse


@pytest.mark.unit
async def test_inline_compose_unknown_user(world: AppWorld) -> None:
    use_case, _sink, _reuse = _ports(world)
    with pytest.raises(NotFound):
        await use_case.execute(InlineComposeCommand(TelegramUserId(1), "long enough"))


@pytest.mark.unit
async def test_inline_compose_requires_access(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(100))
    )
    use_case, _sink, _reuse = _ports(world)
    with pytest.raises(AccessNotGranted):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))


@pytest.mark.unit
async def test_inline_compose_too_short_and_too_long(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    with pytest.raises(InlineQueryTooShort):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "short"))
    with pytest.raises(IncomingTextTooLong):
        await use_case.execute(
            InlineComposeCommand(TelegramUserId(100), "x" * (BOUNDED_TEXT_MAX + 1))
        )
    assert generator.rewrite_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


@pytest.mark.unit
async def test_inline_compose_rewrites_without_goal(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "  Please leave me alone  ")
    )
    assert result.scenario is UsageScenario.REWRITE
    assert result.reuse is InlineReuseStatus.MISS
    assert generator.rewrite_calls[0].draft == "Please leave me alone"
    assert generator.rewrite_calls[0].relationship is RelationshipKind.OTHER
    assert isinstance(sink, RecordingUsageEventSink)
    event = sink.events[0]
    assert event.event_kind is UsageEventKind.GENERATION
    assert event.surface is UsageSurface.INLINE
    assert event.scenario is UsageScenario.REWRITE
    assert event.outcome is UsageOutcome.OK
    assert event.ttfc_ms is None
    assert event.variant_kind is None


@pytest.mark.unit
async def test_inline_compose_quota_before_generation(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(
        world, _Fakes(generator=generator, quota_gate=FakeQuotaGate(limit=0))
    )
    with pytest.raises(InlineComposeFailed) as quota_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(quota_info.value.cause, UserQuotaExhausted)
    assert quota_info.value.reuse is InlineReuseStatus.MISS
    assert generator.rewrite_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert len(sink.events) == 1
    assert sink.events[0].outcome is UsageOutcome.LIMITED


@pytest.mark.unit
async def test_inline_compose_records_errors_and_swallows_sink_failure(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    refused = FakeTextGenerator()
    refused.rewrite_error = GenerationRefusedByProvider(
        usage=TokenUsage(input=1, output=0),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=refused))
    with pytest.raises(InlineComposeFailed) as refused_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(refused_info.value.cause, GenerationRefusedByProvider)
    assert refused_info.value.reuse is InlineReuseStatus.MISS
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.REFUSED

    unavailable = FakeTextGenerator()
    unavailable.rewrite_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=unavailable))
    with pytest.raises(InlineComposeFailed) as unavailable_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(unavailable_info.value.cause, GenerationUnavailable)
    assert unavailable_info.value.reuse is InlineReuseStatus.MISS
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.UNAVAILABLE

    invalid = FakeTextGenerator()
    invalid.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.VARIANT_COUNT,),
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=invalid, sink=FailingUsageEventSink()))
    with pytest.raises(InlineComposeFailed) as invalid_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(invalid_info.value.cause, InvalidGenerationOutput)
    assert invalid_info.value.reuse is InlineReuseStatus.MISS


@pytest.mark.unit
async def test_inline_compose_refund_matrix(world: AppWorld) -> None:
    await world.ensure_granted_user(100)

    unavailable = FakeTextGenerator()
    unavailable.rewrite_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    quota_unavail = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=unavailable, quota_gate=quota_unavail))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert len(quota_unavail.refund_calls) == 1

    refused = FakeTextGenerator()
    refused.rewrite_error = GenerationRefusedByProvider(
        usage=TokenUsage(input=1, output=0),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    quota_refused = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=refused, quota_gate=quota_refused))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert quota_refused.refund_calls == []

    invalid = FakeTextGenerator()
    invalid.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.VARIANT_COUNT,),
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    quota_invalid = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=invalid, quota_gate=quota_invalid))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert quota_invalid.refund_calls == []

    class _CancelRewrite(FakeTextGenerator):
        async def rewrite(self, request: RewriteRequest) -> RewriteResult:
            self.rewrite_calls.append(request)
            raise asyncio.CancelledError

    quota_cancel = FakeQuotaGate(limit=30)
    budget_cancel = FakeLlmBudget()
    query = "long enough"
    use_case, _sink, _ = _ports(
        world, _Fakes(generator=_CancelRewrite(), quota_gate=quota_cancel, llm_budget=budget_cancel)
    )
    with pytest.raises(asyncio.CancelledError):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), query))
    assert quota_cancel.refund_calls == []
    assert budget_cancel.spent == 1000


@pytest.mark.unit
async def test_inline_compose_cancel_before_provider_refunds(world: AppWorld) -> None:
    await world.ensure_granted_user(101)

    class _CancelOnCheck(FakeLlmBudget):
        async def check(self, day: date) -> BudgetOk | BudgetExhausted:
            raise asyncio.CancelledError

    quota = FakeQuotaGate(limit=30)
    budget = _CancelOnCheck()
    use_case, _sink, _ = _ports(world, _Fakes(quota_gate=quota, llm_budget=budget))
    with pytest.raises(asyncio.CancelledError):
        await use_case.execute(InlineComposeCommand(TelegramUserId(101), "long enough"))
    assert quota.refund_calls == []
    assert quota.reserve_count() == 0
    assert budget.add_calls == []


@pytest.mark.unit
async def test_inline_compose_persist_before_budget_add(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    order: list[str] = []

    class _OrderSink(RecordingUsageEventSink):
        async def record(self, event: UsageEvent) -> None:
            order.append("persist")
            self.events.append(event)

    class _OrderBudget(FakeLlmBudget):
        async def add(self, day: date, billable_tokens: int) -> None:
            order.append("add")
            await super().add(day, billable_tokens)

    use_case, _sink, _ = _ports(
        world,
        _Fakes(sink=_OrderSink(), llm_budget=_OrderBudget()),
    )
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert order == ["persist", "add"]


@pytest.mark.unit
async def test_inline_compose_service_budget_exhausted(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(
        world,
        _Fakes(generator=generator, llm_budget=FakeLlmBudget(exhausted=True)),
    )
    with pytest.raises(InlineComposeFailed) as info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(info.value.cause, ServiceBudgetExhausted)
    assert generator.rewrite_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.LIMITED
    assert sink.events[0].limit_kind is LimitKind.GLOBAL_BUDGET


@pytest.mark.unit
async def test_inline_compose_valkey_fail_closed(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    unavailable = CacheUnavailable(CacheErrorKind.TIMEOUT)

    check_uc, _, _ = _ports(
        world,
        _Fakes(llm_budget=FakeLlmBudget(check_unavailable=unavailable)),
    )
    with pytest.raises(InlineComposeFailed) as check_info:
        await check_uc.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(check_info.value.cause, GenerationUnavailable)
    assert check_info.value.cause.kind is UnavailableKind.TIMEOUT

    reserve_uc, _, _ = _ports(
        world,
        _Fakes(quota_gate=FakeQuotaGate(cache_unavailable=unavailable)),
    )
    with pytest.raises(InlineComposeFailed) as reserve_info:
        await reserve_uc.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(reserve_info.value.cause, GenerationUnavailable)
    assert reserve_info.value.cause.kind is UnavailableKind.TIMEOUT

    add_uc, _, _ = _ports(
        world,
        _Fakes(llm_budget=FakeLlmBudget(add_unavailable=unavailable)),
    )
    with pytest.raises(GenerationUnavailable) as add_info:
        await add_uc.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert add_info.value.kind is UnavailableKind.TIMEOUT


@pytest.mark.unit
async def test_inline_compose_crisis_screen_skips_quota_and_generator(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    quota_gate = FakeQuotaGate(limit=0)
    llm_budget = FakeLlmBudget(exhausted=True)
    use_case, sink, _reuse = _ports(
        world,
        _Fakes(generator=generator, quota_gate=quota_gate, llm_budget=llm_budget),
    )
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "я не хочу жить больше")
    )
    assert result.safety is SafetyVerdict.CRISIS
    assert result.reuse is None
    assert result.variants == ()
    assert result.scenario is UsageScenario.REWRITE
    assert generator.rewrite_calls == []
    assert quota_gate.reserve_count() == 0
    assert llm_budget.check_count() == 0
    assert isinstance(sink, RecordingUsageEventSink)
    event = sink.events[0]
    assert event.outcome is UsageOutcome.SCREENED
    assert event.safety == SafetyVerdict.CRISIS.value
    assert event.scenario is UsageScenario.REWRITE
    assert event.billable_tokens == 0


@pytest.mark.unit
async def test_inline_compose_ok_with_failing_sink(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    use_case, _sink, _reuse = _ports(
        world,
        _Fakes(
            generator=FakeTextGenerator(
                rewrite_result=RewriteResult(
                    variants=(Variant(text="a", kind=VariantKind.SOFTER),),
                    applied_rule_indexes=(),
                    safety=SafetyVerdict.OK,
                    meta=GenerationMeta(
                        model="fake",
                        prompt_version="rewrite@v1",
                        latency_ms=2,
                        attempts=1,
                        usage=TokenUsage(input=1, output=1),
                    ),
                )
            ),
            sink=FailingUsageEventSink(),
        ),
    )
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert result.variants[0].text == "a"


@pytest.mark.unit
@pytest.mark.parametrize("phrase", THREAT_AND_HYPERBOLE_NEGATIVES)
async def test_inline_threat_and_hyperbole_still_calls_generator(
    world: AppWorld, phrase: str
) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), phrase))
    assert result.safety is not SafetyVerdict.CRISIS
    assert generator.rewrite_calls
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is not UsageOutcome.SCREENED


async def _user_with_contact_and_rule(world: AppWorld, telegram_id: int) -> None:
    user = await world.ensure_granted_user(telegram_id)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Sam"), RelationshipKind.FRIEND)
        )
    ).contact
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            user.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("не повышать голос"),
            shared=False,
        )
    )


@pytest.mark.unit
async def test_inline_compose_ignores_contacts_and_rules(world: AppWorld) -> None:
    await _user_with_contact_and_rule(world, 130)
    generator = FakeTextGenerator(
        rewrite_result=RewriteResult(
            variants=(Variant(text="a", kind=VariantKind.SOFTER),),
            applied_rule_indexes=(0,),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="rewrite@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(input=1, output=1),
            ),
        )
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    await use_case.execute(InlineComposeCommand(TelegramUserId(130), "long enough"))
    request = generator.rewrite_calls[0]
    assert request.relationship is RelationshipKind.OTHER
    assert request.rules == ()
    assert isinstance(sink, RecordingUsageEventSink)
    assert "не повышать голос" not in str(sink.events[0])


@pytest.mark.unit
async def test_inline_compose_reuse_hit_on_whitespace_and_nfc_variants(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    first = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "  please leave me alone  ")
    )
    nfd = unicodedata.normalize("NFD", "please leave me alone")
    second = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "please\tleave\u00a0me alone")
    )
    third = await use_case.execute(InlineComposeCommand(TelegramUserId(100), nfd))
    assert first.reuse is InlineReuseStatus.MISS
    assert second.reuse is InlineReuseStatus.HIT
    assert third.reuse is InlineReuseStatus.HIT
    assert generator.call_count == 1
    assert isinstance(sink, RecordingUsageEventSink)
    assert len(sink.events) == 1


@pytest.mark.unit
async def test_inline_compose_reuse_join_one_generator_call(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    gate = asyncio.Event()
    generator.rewrite_block = gate
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    t1 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.rewrite_started.wait()
    t2 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await asyncio.sleep(0)
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert generator.call_count == 1
    assert {r1.reuse, r2.reuse} == {InlineReuseStatus.MISS, InlineReuseStatus.JOIN}
    assert r1.variants == r2.variants
    assert isinstance(sink, RecordingUsageEventSink)
    assert len(sink.events) == 1


@pytest.mark.unit
async def test_inline_compose_reuse_key_ignores_contact_state(world: AppWorld) -> None:
    user = await world.ensure_granted_user(140)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    first = await use_case.execute(InlineComposeCommand(TelegramUserId(140), "long enough draft"))
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Pat"), RelationshipKind.PARTNER)
        )
    ).contact
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            user.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("не повышать голос"),
            shared=False,
        )
    )
    after_contact = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "long enough draft")
    )
    assert first.reuse is InlineReuseStatus.MISS
    assert after_contact.reuse is InlineReuseStatus.HIT
    assert generator.call_count == 1
    assert isinstance(sink, RecordingUsageEventSink)


@pytest.mark.unit
async def test_inline_compose_reuse_ttl_expiry_is_miss(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock, ttl_seconds=30.0)
    use_case, _sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    world.clock.advance(timedelta(seconds=31))
    again = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert again.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 2


@pytest.mark.unit
async def test_inline_compose_reuse_error_to_joiners_not_stored(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    gate = asyncio.Event()
    generator.rewrite_block = gate
    generator.rewrite_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="rewrite@v1",
    )
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    t1 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.rewrite_started.wait()
    t2 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await asyncio.sleep(0)
    gate.set()
    with pytest.raises(InlineComposeFailed) as first_info:
        await t1
    with pytest.raises(InlineComposeFailed) as second_info:
        await t2
    assert isinstance(first_info.value.cause, GenerationUnavailable)
    assert isinstance(second_info.value.cause, GenerationUnavailable)
    assert first_info.value.cause is second_info.value.cause
    assert {first_info.value.reuse, second_info.value.reuse} == {
        InlineReuseStatus.MISS,
        InlineReuseStatus.JOIN,
    }
    assert generator.call_count == 1
    generator.rewrite_error = None
    generator.rewrite_block = None
    ok = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert ok.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 2
    assert isinstance(sink, RecordingUsageEventSink)


@pytest.mark.unit
async def test_inline_compose_waiter_cancel_keeps_shared_generation(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    gate = asyncio.Event()
    generator.rewrite_block = gate
    reuse = make_inline_reuse(world.clock)
    use_case, _sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    producer = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.rewrite_started.wait()
    joiner = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await asyncio.sleep(0)
    joiner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await joiner
    gate.set()
    produced = await producer
    assert produced.reuse is InlineReuseStatus.MISS
    hit = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert hit.reuse is InlineReuseStatus.HIT
    assert generator.call_count == 1


@pytest.mark.unit
async def test_inline_compose_forget_on_delete_and_revoke(world: AppWorld) -> None:
    await world.ensure_granted_user(150)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock)
    use_case, _sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    await use_case.execute(InlineComposeCommand(TelegramUserId(150), "long enough draft"))
    assert generator.call_count == 1
    await DeleteMyAccount(
        DeleteMyAccountPorts(
            world.uow_factory,
            world.ids,
            FakePseudonymizer(),
            world.clock,
            reuse,
            world.notifier,
        )
    ).execute(DeleteMyAccountCommand(TelegramUserId(150)))
    await world.ensure_granted_user(150)
    after_delete = await use_case.execute(
        InlineComposeCommand(TelegramUserId(150), "long enough draft")
    )
    assert after_delete.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 2
    await use_case.execute(InlineComposeCommand(TelegramUserId(150), "long enough draft"))
    assert generator.call_count == 2  # hit
    await RevokeAllConsents(world.uow_factory, world.clock, reuse).execute(
        RevokeAllConsentsCommand(TelegramUserId(150))
    )
    await world.ensure_granted_user(150)
    # consents revoked then re-granted via ensure_granted_user — reuse must still miss
    after_revoke = await use_case.execute(
        InlineComposeCommand(TelegramUserId(150), "long enough draft")
    )
    assert after_revoke.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 3


@pytest.mark.unit
async def test_compose_generation_execute_bounds_and_unknown_user(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink = _compose_generation(world, _Fakes(generator=generator))
    assert BOUNDED_TEXT_MIN >= 1
    with pytest.raises(IncomingTextTooShort):
        await use_case.execute(
            ComposeGenerationCommand(
                telegram_user_id=TelegramUserId(100),
                draft="",
                goal=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    with pytest.raises(IncomingTextTooLong):
        await use_case.execute(
            ComposeGenerationCommand(
                telegram_user_id=TelegramUserId(100),
                draft="x" * (BOUNDED_TEXT_MAX + 1),
                goal=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    with pytest.raises(NotFound):
        await use_case.execute(
            ComposeGenerationCommand(
                telegram_user_id=TelegramUserId(404),
                draft="long enough draft",
                goal=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    assert generator.rewrite_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


@pytest.mark.unit
async def test_compose_generation_execute_crisis_before_quota(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    quota_gate = FakeQuotaGate(limit=0)
    use_case, sink = _compose_generation(world, _Fakes(generator=generator, quota_gate=quota_gate))
    result = await use_case.execute(
        ComposeGenerationCommand(
            telegram_user_id=TelegramUserId(100),
            draft="я не хочу жить больше",
            goal=None,
            contact_id=None,
            surface=UsageSurface.MINIAPP,
        )
    )
    assert result.safety is SafetyVerdict.CRISIS
    assert result.variants == ()
    assert generator.rewrite_calls == []
    assert quota_gate.reserve_count() == 0
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.SCREENED
    assert sink.events[0].surface is UsageSurface.MINIAPP


def _rewrite_result(*texts: str) -> RewriteResult:
    kinds = (VariantKind.CHECK, VariantKind.SOFTER, VariantKind.FIRMER)
    return RewriteResult(
        variants=tuple(
            Variant(text=text, kind=kind) for text, kind in zip(texts, kinds, strict=True)
        ),
        applied_rule_indexes=(),
        safety=SafetyVerdict.OK,
        meta=GenerationMeta(
            model="fake",
            prompt_version="rewrite@v1",
            latency_ms=1,
            attempts=1,
            usage=TokenUsage(input=1, output=1),
        ),
    )


@pytest.mark.unit
async def test_inline_compose_humanizes_typography_from_draft(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result(
            "Нужно поговорить \u2014 сегодня\u2026",
            "Давай поговорим \u2014 сегодня",
            "Говорим сегодня",
        )
    )
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "нужно поговорить"))
    assert [v.text for v in result.variants] == [
        "Нужно поговорить, сегодня...",
        "Давай поговорим, сегодня",
        "Говорим сегодня",
    ]


@pytest.mark.unit
async def test_inline_compose_keeps_dashes_when_draft_has_a_dash(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result(
            "нужно \u2014 поговорить", "давай \u2014 поговорим", "говорим"
        )
    )
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "нужно \u2014 поговорить")
    )
    assert result.variants[0].text == "нужно \u2014 поговорить"
    assert result.variants[1].text == "давай \u2014 поговорим"


@pytest.mark.unit
async def test_inline_compose_reports_policy_violations_without_blocking(
    world: AppWorld,
) -> None:
    await world.ensure_granted_user(100)
    monitor = RecordingVariantPolicyMonitor()
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result(
            "я ценю, что мы поговорим сегодня вечером",
            "давай обсудим спокойно",
            "говорим сегодня",
        )
    )
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator, policy_monitor=monitor))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "нужно поговорить"))
    assert len(result.variants) == 3
    assert (VariantKind.CHECK, PolicyRule.STOP_PHRASE) in monitor.records
    assert (VariantKind.SOFTER, PolicyRule.STOP_PHRASE) in monitor.records
    assert all(rule is not PolicyRule.REGISTER for _kind, rule in monitor.records)


async def _compose_with_context(
    world: AppWorld,
    texts: tuple[str, str, str],
) -> list[tuple[VariantKind, PolicyRule]]:
    """Run ComposeGeneration on a fixed draft and return the recorded policy violations."""
    await world.ensure_granted_user(100)
    monitor = RecordingVariantPolicyMonitor()
    generator = FakeTextGenerator(rewrite_result=_rewrite_result(*texts))
    use_case, _sink = _compose_generation(
        world, _Fakes(generator=generator, policy_monitor=monitor)
    )
    await use_case.execute(
        ComposeGenerationCommand(
            telegram_user_id=TelegramUserId(100),
            draft="Я опоздал на встречу",
            goal=None,
            contact_id=None,
            surface=UsageSurface.MINIAPP,
        )
    )
    return monitor.records


@pytest.mark.unit
async def test_compose_generation_reports_the_ladder(world: AppWorld) -> None:
    records = await _compose_with_context(
        world, ("Я опоздал на встречу", "Я опоздал, прости", "Я опоздал, прости")
    )
    assert (VariantKind.FIRMER, PolicyRule.LADDER_SIMILAR) in records


@pytest.mark.unit
async def test_compose_generation_reports_no_rule_for_a_clean_ladder(world: AppWorld) -> None:
    records = await _compose_with_context(
        world, ("Я опоздал на встречу", "Я пришёл поздно, мне жаль", "Опоздал, больше не буду")
    )
    assert records == []


@pytest.mark.unit
async def test_inline_compose_substitutes_the_draft_for_an_empty_check(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    monitor = RecordingVariantPolicyMonitor()
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result("", "давай обсудим спокойно", "говорим сегодня")
    )
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator, policy_monitor=monitor))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "нужно поговорить"))
    by_kind = {variant.kind: variant.text for variant in result.variants}
    assert by_kind[VariantKind.CHECK] == "нужно поговорить"
    assert by_kind[VariantKind.SOFTER] == "давай обсудим спокойно"
    assert monitor.check_empty == 1
    assert all(kind is not VariantKind.CHECK for kind, _rule in monitor.records)


@pytest.mark.unit
async def test_inline_compose_does_not_count_a_filled_check_as_empty(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    monitor = RecordingVariantPolicyMonitor()
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result(
            "Нужно поговорить", "давай обсудим спокойно", "говорим сегодня"
        )
    )
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator, policy_monitor=monitor))
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "нужно поговорить"))
    assert monitor.check_empty == 0


@pytest.mark.unit
async def test_invalid_output_is_reported_per_attempt_with_slot_and_finish_reason(
    world: AppWorld,
) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    generator.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.LENGTH),
        usage=TokenUsage(),
        attempts=2,
        model="fake",
        prompt_version="rewrite@v1",
    ).with_diagnostics((VariantKind.FIRMER, None), ("stop", "length"))
    monitor = RecordingVariantPolicyMonitor()
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator, policy_monitor=monitor))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert monitor.invalid == [
        (InvalidOutputReason.EMPTY_TEXT, VariantKind.FIRMER, "stop"),
        (InvalidOutputReason.LENGTH, None, "length"),
    ]


@pytest.mark.unit
async def test_policy_path_does_not_log_conversation_text(
    world: AppWorld, capture_log_events: Callable[[], list[dict[str, Any]]]
) -> None:
    await world.ensure_granted_user(100)
    marker = "CANARY-7f3a9c"
    generator = FakeTextGenerator(
        rewrite_result=_rewrite_result(
            f"я ценю {marker} \u2014 {marker}",
            f"давай обсудим спокойно {marker}",
            f"говорим {marker}",
        )
    )
    monitor = RecordingVariantPolicyMonitor()
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator, policy_monitor=monitor))
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), f"нужно поговорить {marker}"))
    assert monitor.records
    assert marker not in json.dumps(capture_log_events())
