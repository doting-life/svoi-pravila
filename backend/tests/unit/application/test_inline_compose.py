"""InlineCompose use-case tests."""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

import pytest

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ApplicationError,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    InlineComposeFailed,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    ScenarioQuotaExceeded,
    UnavailableKind,
)
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
    GenerationMeta,
    HelpSayIntent,
    HelpSayResult,
    SafetyVerdict,
    SoftenResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineResultReuse,
    InlineReuseResolution,
    ProduceInlineReuse,
    ReuseFailed,
)
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
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
    RelationshipKind,
    RuleCategory,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel, RuleText
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
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
    quota: FakeRateLimiter | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    reuse: InlineResultReuse | None = None
    min_chars: int = 8


def _ports(
    world: AppWorld, fakes: _Fakes | None = None
) -> tuple[
    InlineCompose,
    RecordingUsageEventSink | FailingUsageEventSink,
    InlineResultReuse,
]:
    chosen = fakes or _Fakes()
    sink = chosen.sink if chosen.sink is not None else RecordingUsageEventSink()
    reuse: InlineResultReuse = (
        chosen.reuse if chosen.reuse is not None else make_inline_reuse(world.clock)
    )
    use_case = InlineCompose(
        InlineComposePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=chosen.generator or FakeTextGenerator(),
            quota=chosen.quota or FakeRateLimiter(limit=30),
            sink=sink,
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
            crisis_screen=CrisisScreen.load_ru_v2(),
            reuse=reuse,
            min_chars=chosen.min_chars,
            deadline_seconds=8.0,
            intent_prefixes=_PREFIXES,
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
        world, _Fakes(generator=generator, quota=FakeRateLimiter(limit=0))
    )
    with pytest.raises(InlineComposeFailed) as quota_info:
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
    assert isinstance(quota_info.value.cause, ScenarioQuotaExceeded)
    assert quota_info.value.reuse is InlineReuseStatus.MISS
    assert generator.soften_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


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
async def test_inline_compose_skips_blank_prefix_entries(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case = InlineCompose(
        InlineComposePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=generator,
            quota=FakeRateLimiter(limit=30),
            sink=RecordingUsageEventSink(),
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
            crisis_screen=CrisisScreen.load_ru_v2(),
            reuse=make_inline_reuse(world.clock),
            min_chars=8,
            deadline_seconds=8.0,
            intent_prefixes=(("", HelpSayIntent.OTHER), *_PREFIXES),
        )
    )
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough draft"))
    assert generator.soften_calls


@pytest.mark.unit
async def test_inline_compose_crisis_screen_skips_quota_and_generator(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    quota = FakeRateLimiter(limit=0)
    use_case, sink, _reuse = _ports(world, _Fakes(generator=generator, quota=quota))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "я не хочу жить больше")
    )
    assert result.safety is SafetyVerdict.CRISIS
    assert result.reuse is None
    assert result.applied_rules == ()
    assert result.variants == ()
    assert result.scenario is UsageScenario.SOFTEN
    assert generator.soften_calls == []
    assert quota.check_count() == 0
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
    await ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock).execute(
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
    await ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock).execute(
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


class _OddErrorReuse:
    """Reuse stub that returns an unexpected application error type."""

    async def resolve(
        self, key: str, user_key: str, produce: ProduceInlineReuse
    ) -> InlineReuseResolution:
        _ = key, user_key, produce
        return ReuseFailed(status=InlineReuseStatus.MISS, error=ApplicationError("unexpected"))

    def forget(self, user_key: str) -> None:
        _ = user_key


@pytest.mark.unit
async def test_inline_compose_rejects_unexpected_reuse_error(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    use_case, _sink, _ = _ports(world, _Fakes(reuse=_OddErrorReuse()))
    with pytest.raises(TypeError, match="unexpected reuse produce error"):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))


@pytest.mark.unit
async def test_inline_compose_forget_on_delete_and_revoke(world: AppWorld) -> None:
    await world.ensure_granted_user(150)
    generator = FakeTextGenerator()
    reuse = make_inline_reuse(world.clock)
    use_case, _sink, _ = _ports(world, _Fakes(generator=generator, reuse=reuse))
    await use_case.execute(InlineComposeCommand(TelegramUserId(150), "long enough draft"))
    assert generator.call_count == 1
    await DeleteMyAccount(
        world.uow_factory, world.ids, FakePseudonymizer(), world.clock, reuse
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
