"""InlineCompose use-case tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    AccessNotGranted,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    ScenarioQuotaExceeded,
    UnavailableKind,
)
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
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.inline_compose import (
    InlineCompose,
    InlineComposeCommand,
    InlineComposePorts,
)
from svoi_pravila.domain.enums import (
    Firmness,
    RelationshipKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId
from tests.fakes.generation import FakeTextGenerator
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
    min_chars: int = 8


def _ports(
    world: AppWorld, fakes: _Fakes | None = None
) -> tuple[InlineCompose, RecordingUsageEventSink | FailingUsageEventSink]:
    chosen = fakes or _Fakes()
    sink = chosen.sink if chosen.sink is not None else RecordingUsageEventSink()
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
            min_chars=chosen.min_chars,
            deadline_seconds=8.0,
            intent_prefixes=_PREFIXES,
        )
    )
    return use_case, sink


@pytest.mark.unit
async def test_inline_compose_unknown_user(world: AppWorld) -> None:
    use_case, _sink = _ports(world)
    with pytest.raises(NotFound):
        await use_case.execute(InlineComposeCommand(TelegramUserId(1), "long enough"))


@pytest.mark.unit
async def test_inline_compose_requires_access(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(100))
    )
    use_case, _sink = _ports(world)
    with pytest.raises(AccessNotGranted):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))


@pytest.mark.unit
async def test_inline_compose_too_short_and_too_long(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink = _ports(world, _Fakes(generator=generator))
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
    use_case, sink = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "  Please leave me alone  ")
    )
    assert result.scenario is UsageScenario.SOFTEN
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
    use_case, _sink = _ports(world, _Fakes(generator=generator))
    await use_case.execute(InlineComposeCommand(TelegramUserId(100), "ОТКАЖИ:  I cannot come  "))
    assert generator.help_say_calls[0].intent is HelpSayIntent.DECLINE
    assert generator.help_say_calls[0].details == "I cannot come"
    assert generator.soften_calls == []


@pytest.mark.unit
async def test_inline_compose_prefix_remainder_too_short(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink = _ports(world, _Fakes(generator=generator))
    with pytest.raises(InlineQueryTooShort):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "помоги: hi"))
    assert generator.help_say_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


@pytest.mark.unit
async def test_inline_compose_quota_before_generation(world: AppWorld) -> None:
    await world.ensure_granted_user(100)
    generator = FakeTextGenerator()
    use_case, sink = _ports(world, _Fakes(generator=generator, quota=FakeRateLimiter(limit=0)))
    with pytest.raises(ScenarioQuotaExceeded):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
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
    use_case, sink = _ports(world, _Fakes(generator=refused))
    with pytest.raises(GenerationRefusedByProvider):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
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
    use_case, sink = _ports(world, _Fakes(generator=unavailable))
    with pytest.raises(GenerationUnavailable):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))
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
    use_case, sink = _ports(world, _Fakes(generator=invalid, sink=FailingUsageEventSink()))
    with pytest.raises(InvalidGenerationOutput):
        await use_case.execute(InlineComposeCommand(TelegramUserId(100), "long enough"))


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
    use_case, sink = _ports(world, _Fakes(generator=generator, quota=quota))
    result = await use_case.execute(
        InlineComposeCommand(TelegramUserId(100), "я не хочу жить больше")
    )
    assert result.safety is SafetyVerdict.CRISIS
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
    use_case, _sink = _ports(
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
    use_case, sink = _ports(world, _Fakes(generator=generator))
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
    use_case, sink = _ports(world, _Fakes(generator=generator))
    result = await use_case.execute(InlineComposeCommand(TelegramUserId(100), phrase))
    assert result.safety is not SafetyVerdict.CRISIS
    assert generator.soften_calls
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is not UsageOutcome.SCREENED
