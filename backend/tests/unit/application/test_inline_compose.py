"""InlineCompose use-case tests."""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

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
    HelpSayIntent,
    HelpSayResult,
    SafetyVerdict,
    SoftenRequest,
    SoftenResult,
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
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.enums import (
    Firmness,
    LimitKind,
    RelationshipKind,
    RuleCategory,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel, RuleText
from svoi_pravila.domain.usage import UsageEvent
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink
from tests.unit.application.conftest import AppWorld
from tests.unit.domain.test_crisis_screen import THREAT_AND_HYPERBOLE_NEGATIVES

_PREFIXES = (
    ("откажи:", HelpSayIntent.DECLINE),
    ("граница:", HelpSayIntent.SET_BOUNDARY),
    ("извинись:", HelpSayIntent.ADMIT_FAULT),
    ("мир:", HelpSayIntent.RECONNECT_AFTER_CONFLICT),
    ("помоги:", HelpSayIntent.OTHER),
)


@dataclass(frozen=True, slots=True)
class _Fakes:
    generator: FakeTextGenerator | None = None
    quota_gate: FakeQuotaGate | None = None
    llm_budget: FakeLlmBudget | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    reuse: InlineResultReuse | None = None
    min_chars: int = 8
    intent_prefixes: tuple[tuple[str, HelpSayIntent], ...] = _PREFIXES


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
            intent_prefixes=chosen.intent_prefixes,
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
    assert generator.soften_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


@pytest.mark.unit
async def test_inline_compose_softens_without_prefix(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "  Please leave me alone  ")
    )
    assert result.scenario is UsageScenario.SOFTEN
    assert result.reuse is InlineReuseStatus.MISS
    assert generator.soften_calls[0].draft == "Please leave me alone"
    assert generator.soften_calls[0].relationship is RelationshipKind.OTHER
    assert generator.help_say_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    event = sink.events[0]
    assert event.event_kind is UsageEventKind.GENERATION
    assert event.surface is UsageSurface.INLINE
    assert event.scenario is UsageScenario.SOFTEN
    assert event.outcome is UsageOutcome.OK
    assert event.ttfc_ms is None
    assert event.variant_firmness is None


@pytest.mark.unit
async def test_inline_compose_help_say_prefix_casefold(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, _sink, _reuse = _ports(world, _Fakes(generator=generator))
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "ОТКАЖИ:  I cannot come  "))
    assert generator.help_say_calls[0].intent is HelpSayIntent.DECLINE
    assert generator.help_say_calls[0].details == "I cannot come"
    assert generator.soften_calls == []


@pytest.mark.unit
async def test_inline_compose_prefix_remainder_too_short(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    with pytest.raises(InlineQueryTooShort):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "помоги: hi"))
    assert generator.help_say_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


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
    assert generator.soften_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert len(sink.events) == 1
    assert sink.events[0].outcome is UsageOutcome.LIMITED


@pytest.mark.unit
async def test_inline_compose_records_errors_and_swallows_sink_failure(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    refused = FakeTextGenerator()
    refused.soften_error = GenerationRefusedByProvider(
        usage=TokenUsage(input=1, output=0),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=refused))
    with pytest.raises(InlineComposeFailed) as refused_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(refused_info.value.cause, GenerationRefusedByProvider)
    assert refused_info.value.reuse is InlineReuseStatus.MISS
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.REFUSED

    unavailable = FakeTextGenerator()
    unavailable.soften_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=unavailable))
    with pytest.raises(InlineComposeFailed) as unavailable_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(unavailable_info.value.cause, GenerationUnavailable)
    assert unavailable_info.value.reuse is InlineReuseStatus.MISS
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.UNAVAILABLE

    invalid = FakeTextGenerator()
    invalid.soften_error = InvalidGenerationOutput(
        (InvalidOutputReason.VARIANT_COUNT,),
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
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
    unavailable.soften_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    quota_unavail = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=unavailable, quota_gate=quota_unavail))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert len(quota_unavail.refund_calls) == 1

    refused = FakeTextGenerator()
    refused.soften_error = GenerationRefusedByProvider(
        usage=TokenUsage(input=1, output=0),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    quota_refused = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=refused, quota_gate=quota_refused))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert quota_refused.refund_calls == []

    invalid = FakeTextGenerator()
    invalid.soften_error = InvalidGenerationOutput(
        (InvalidOutputReason.VARIANT_COUNT,),
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    quota_invalid = FakeQuotaGate(limit=30)
    use_case, _sink, _ = _ports(world, _Fakes(generator=invalid, quota_gate=quota_invalid))
    with pytest.raises(InlineComposeFailed):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert quota_invalid.refund_calls == []

    class _CancelSoft(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            self.soften_calls.append(request)
            raise asyncio.CancelledError

    quota_cancel = FakeQuotaGate(limit=30)
    budget_cancel = FakeLlmBudget()
    query = "long enough"
    use_case, _sink, _ = _ports(
        world, _Fakes(generator=_CancelSoft(), quota_gate=quota_cancel, llm_budget=budget_cancel)
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
async def test_inline_compose_skips_blank_prefix_entries(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, _sink, _ = _ports(
        world,
        _Fakes(
            generator=generator,
            intent_prefixes=(("", HelpSayIntent.OTHER), *_PREFIXES),
        ),
    )
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert generator.soften_calls


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
    assert generator.soften_calls == []
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
    assert result.applied_rules == ()
    assert result.variants == ()
    assert result.scenario is UsageScenario.SOFTEN
    assert generator.soften_calls == []
    assert quota_gate.reserve_count() == 0
    assert llm_budget.check_count() == 0
    assert isinstance(sink, RecordingUsageEventSink)
    event = sink.events[0]
    assert event.outcome is UsageOutcome.SCREENED
    assert event.safety == SafetyVerdict.CRISIS.value
    assert event.scenario is UsageScenario.SOFTEN
    assert event.billable_tokens == 0
    help_result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "граница: я не хочу жить")
    )
    assert help_result.scenario is UsageScenario.HELP_SAY
    assert help_result.safety is SafetyVerdict.CRISIS
    assert generator.help_say_calls == []


@pytest.mark.unit
async def test_inline_compose_ok_with_failing_sink(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    use_case, _sink, _reuse = _ports(
        world,
        _Fakes(
            generator=FakeTextGenerator(
                soften_result=SoftenResult(
                    variants=(Variant(text="a", firmness=Firmness.GENTLE),),
                    applied_rule_indexes=(),
                    safety=SafetyVerdict.OK,
                    meta=GenerationMeta(
                        model="fake",
                        prompt_version="soften@v1",
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
async def test_inline_compose_help_say_ok_event(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator(
        help_say_result=HelpSayResult(
            variants=(Variant(text="h", firmness=Firmness.FIRM),),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="help_say@v1",
                latency_ms=3,
                attempts=1,
                usage=TokenUsage(input=2, output=2),
            ),
        )
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "граница: keep the evening free")
    )
    assert result.scenario is UsageScenario.HELP_SAY
    assert generator.help_say_calls[0].intent is HelpSayIntent.SET_BOUNDARY
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].scenario is UsageScenario.HELP_SAY


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
    assert generator.soften_calls
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is not UsageOutcome.SCREENED


@pytest.mark.unit
async def test_inline_compose_maps_applied_rule_indexes(world: AppWorld) -> None:
    user = await world.ensure_granted_user(130)
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
    generator = FakeTextGenerator(
        soften_result=SoftenResult(
            variants=(Variant(text="a", firmness=Firmness.GENTLE),),
            applied_rule_indexes=(0, 4),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="soften@v1",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(input=1, output=1),
            ),
        )
    )
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(130), "long enough"))
    assert tuple(view.text for view in result.applied_rules) == ("не повышать голос",)
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
    generator.soften_block = gate
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    t1 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.soften_started.wait()
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
async def test_inline_compose_reuse_miss_on_context_changes(world: AppWorld) -> None:
    user = await world.ensure_granted_user(140)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    await use_case.execute(InlineComposeCommand(TelegramUserId(140), "long enough draft"))
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Pat"), RelationshipKind.PARTNER)
        )
    ).contact
    after_contact = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "long enough draft")
    )
    assert after_contact.reuse is InlineReuseStatus.MISS
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
    after_rule = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "long enough draft")
    )
    assert after_rule.reuse is InlineReuseStatus.MISS
    friend = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Sam"), RelationshipKind.FRIEND)
        )
    ).contact
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(user.id, friend.id)
    )
    after_relationship = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "long enough draft")
    )
    assert after_relationship.reuse is InlineReuseStatus.MISS
    help_say = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "помоги: long enough draft")
    )
    assert help_say.reuse is InlineReuseStatus.MISS
    assert help_say.scenario is UsageScenario.HELP_SAY
    decline = await use_case.execute(
        InlineComposeCommand(TelegramUserId(140), "откажи: long enough draft")
    )
    assert decline.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 6
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
    generator.soften_block = gate
    generator.soften_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(),
        attempts=1,
        model="fake",
        prompt_version="soften@v1",
    )
    reuse = make_inline_reuse(world.clock)
    use_case, sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    t1 = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.soften_started.wait()
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
    generator.soften_error = None
    generator.soften_block = None
    ok = await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert ok.reuse is InlineReuseStatus.MISS
    assert generator.call_count == 2
    assert isinstance(sink, RecordingUsageEventSink)


@pytest.mark.unit
async def test_inline_compose_waiter_cancel_keeps_shared_generation(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    gate = asyncio.Event()
    generator.soften_block = gate
    reuse = make_inline_reuse(world.clock)
    use_case, _sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    producer = asyncio.create_task(
        use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    )
    await generator.soften_started.wait()
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
                intent=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    with pytest.raises(IncomingTextTooLong):
        await use_case.execute(
            ComposeGenerationCommand(
                telegram_user_id=TelegramUserId(100),
                draft="x" * (BOUNDED_TEXT_MAX + 1),
                intent=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    with pytest.raises(NotFound):
        await use_case.execute(
            ComposeGenerationCommand(
                telegram_user_id=TelegramUserId(404),
                draft="long enough draft",
                intent=None,
                contact_id=None,
                surface=UsageSurface.MINIAPP,
            )
        )
    assert generator.soften_calls == []
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
            intent=None,
            contact_id=None,
            surface=UsageSurface.MINIAPP,
        )
    )
    assert result.safety is SafetyVerdict.CRISIS
    assert result.variants == ()
    assert result.applied_rules == ()
    assert generator.soften_calls == []
    assert quota_gate.reserve_count() == 0
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is UsageOutcome.SCREENED
    assert sink.events[0].surface is UsageSurface.MINIAPP
