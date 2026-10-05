"""DecodeIncoming use-case tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    AccessNotGranted,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    ScenarioBusy,
    ScenarioQuotaExceeded,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    DecodeResult,
    GenerationMeta,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.decode_incoming import (
    DecodeIncoming,
    DecodeIncomingCommand,
    DecodeIncomingPorts,
)
from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
from svoi_pravila.application.use_cases.get_effective_rules import (
    GetEffectiveRules,
    GetEffectiveRulesCommand,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.domain.enums import (
    Firmness,
    RelationshipKind,
    RuleCategory,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.rules import RuleRevision
from svoi_pravila.domain.text import ContactLabel, RuleText
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink
from tests.unit.application.conftest import AppWorld
from tests.unit.domain.test_crisis_screen import THREAT_AND_HYPERBOLE_NEGATIVES


@dataclass(frozen=True, slots=True)
class _DecodeFakes:
    generator: FakeTextGenerator | None = None
    guard: FakeConcurrencyGuard | None = None
    quota: FakeRateLimiter | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    deadline_seconds: float = 45.0


def _ports(
    world: AppWorld,
    fakes: _DecodeFakes | None = None,
) -> tuple[DecodeIncoming, RecordingUsageEventSink | FailingUsageEventSink, FakeConcurrencyGuard]:
    chosen = fakes or _DecodeFakes()
    recording = chosen.sink if chosen.sink is not None else RecordingUsageEventSink()
    concurrency = chosen.guard or FakeConcurrencyGuard()
    use_case = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            generator=chosen.generator or FakeTextGenerator(stream_chunks=("Hi", " there")),
            guard=concurrency,
            quota=chosen.quota or FakeRateLimiter(limit=20),
            sink=recording,
            clock=world.clock,
            monotonic=world.clock,
            ids=world.ids,
            pseudonymizer=FakePseudonymizer(),
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=chosen.deadline_seconds,
        )
    )
    return use_case, recording, concurrency


async def _drain(use_case: DecodeIncoming, telegram_id: int, text: str) -> list[object]:
    return [
        event
        async for event in use_case.execute(
            DecodeIncomingCommand(TelegramUserId(telegram_id), text, surface=UsageSurface.DM)
        )
    ]


@pytest.mark.unit
async def test_decode_rejects_empty_and_too_long_before_lock(world: AppWorld) -> None:
    use_case, _sink, guard = _ports(world)
    with pytest.raises(IncomingTextTooShort):
        await _drain(use_case, 100, "")
    with pytest.raises(IncomingTextTooLong):
        await _drain(use_case, 100, "x" * 4001)
    assert guard.acquire_calls == []


@pytest.mark.unit
async def test_decode_unknown_user(world: AppWorld) -> None:
    use_case, _sink, _guard = _ports(world)
    with pytest.raises(NotFound):
        await _drain(use_case, 999, "hello")


@pytest.mark.unit
async def test_decode_requires_access(world: AppWorld) -> None:
    await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
        EnsureUserCommand(TelegramUserId(100))
    )
    use_case, _sink, _guard = _ports(world)
    with pytest.raises(AccessNotGranted):
        await _drain(use_case, 100, "hello")


@pytest.mark.unit
async def test_decode_without_active_contact_uses_other_and_empty_rules(
    world: AppWorld,
) -> None:
    user = await world.ensure_granted_user(100)
    generator = FakeTextGenerator(stream_chunks=("A",))
    use_case, sink, guard = _ports(world, _DecodeFakes(generator=generator))
    events = await _drain(use_case, 100, "incoming")
    assert isinstance(events[0], AnalysisChunk)
    assert isinstance(events[-1], DecodeCompleted)
    assert generator.decode_stream_calls[0].relationship is RelationshipKind.OTHER
    assert generator.decode_stream_calls[0].rules == ()
    assert isinstance(sink, RecordingUsageEventSink)
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.scenario is UsageScenario.DECODE
    assert event.surface is UsageSurface.DM
    assert event.outcome is UsageOutcome.OK
    assert event.safety == SafetyVerdict.OK.value
    assert event.ttfc_ms is not None
    assert event.user_pseudonym != str(user.telegram_user_id.value)
    assert all(ch in "0123456789abcdef" for ch in event.user_pseudonym)
    assert len(guard.release_calls) == 1
    assert guard.acquire_calls[0][1] == 50


@pytest.mark.unit
async def test_decode_busy_and_quota(world: AppWorld) -> None:
    await world.ensure_granted_user(100)

    class AlwaysBusy(FakeConcurrencyGuard):
        async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
            self.acquire_calls.append((key, ttl_seconds))
            return None

    use_case, sink, _g = _ports(world, _DecodeFakes(guard=AlwaysBusy()))
    with pytest.raises(ScenarioBusy):
        await _drain(use_case, 100, "hello")
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []

    await world.ensure_granted_user(101)
    quota_uc, quota_sink, quota_guard = _ports(world, _DecodeFakes(quota=FakeRateLimiter(limit=0)))
    with pytest.raises(ScenarioQuotaExceeded):
        await _drain(quota_uc, 101, "hello")
    assert isinstance(quota_sink, RecordingUsageEventSink)
    assert quota_sink.events == []
    assert quota_guard.release_calls


@pytest.mark.unit
async def test_decode_active_rules_and_error_outcomes(world: AppWorld) -> None:
    user = await world.ensure_granted_user(102)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Sam"), RelationshipKind.FRIEND)
        )
    ).contact
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(user.id, contact.id)
    )
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            user.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("do not mention work"),
            shared=False,
        )
    )
    generator = FakeTextGenerator(stream_chunks=())
    use_case, sink, _g = _ports(world, _DecodeFakes(generator=generator))
    events = await _drain(use_case, 102, "please decode")
    assert isinstance(events[0], DecodeCompleted)
    request = generator.decode_stream_calls[0]
    assert request.relationship is RelationshipKind.FRIEND
    assert len(request.rules) == 1
    assert request.rules[0].text == "do not mention work"
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].ttfc_ms is None

    refused = FakeTextGenerator(
        stream_error=GenerationRefusedByProvider(
            usage=TokenUsage(input=2),
            attempts=2,
            model="m",
            prompt_version="p",
        )
    )
    refused_uc, refused_sink, _ = _ports(world, _DecodeFakes(generator=refused))
    with pytest.raises(GenerationRefusedByProvider):
        await _drain(refused_uc, 102, "please decode")
    assert isinstance(refused_sink, RecordingUsageEventSink)
    assert refused_sink.events[0].outcome is UsageOutcome.REFUSED

    invalid = FakeTextGenerator(
        stream_error=InvalidGenerationOutput(
            (InvalidOutputReason.JSON_DECODE,),
            usage=TokenUsage(output=3),
            attempts=3,
            model="m",
            prompt_version="p",
        )
    )
    invalid_uc, invalid_sink, _ = _ports(world, _DecodeFakes(generator=invalid))
    with pytest.raises(InvalidGenerationOutput):
        await _drain(invalid_uc, 102, "please decode")
    assert isinstance(invalid_sink, RecordingUsageEventSink)
    assert invalid_sink.events[0].outcome is UsageOutcome.INVALID_OUTPUT

    unavailable = FakeTextGenerator(
        stream_error=GenerationUnavailable(
            UnavailableKind.TIMEOUT,
            usage=TokenUsage(input=4),
            attempts=4,
            model="m",
            prompt_version="p",
        )
    )
    un_uc, un_sink, _ = _ports(world, _DecodeFakes(generator=unavailable))
    with pytest.raises(GenerationUnavailable):
        await _drain(un_uc, 102, "please decode")
    assert isinstance(un_sink, RecordingUsageEventSink)
    recorded = un_sink.events[0]
    assert recorded.outcome is UsageOutcome.UNAVAILABLE
    assert recorded.unavailable_kind == UnavailableKind.TIMEOUT.value
    assert recorded.model == "m"
    assert recorded.prompt_version == "p"


@pytest.mark.unit
async def test_decode_sink_failure_does_not_fail_stream(world: AppWorld) -> None:
    await world.ensure_granted_user(103)
    generator = FakeTextGenerator(
        decode_result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(
                Variant(text="g", firmness=Firmness.GENTLE),
                Variant(text="b", firmness=Firmness.BALANCED),
                Variant(text="f", firmness=Firmness.FIRM),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="m",
                prompt_version="p",
                latency_ms=9,
                attempts=2,
                usage=TokenUsage(input=1, output=1),
            ),
        )
    )
    use_case, _sink, guard = _ports(
        world, _DecodeFakes(generator=generator, sink=FailingUsageEventSink())
    )
    events = await _drain(use_case, 103, "incoming")
    assert isinstance(events[-1], DecodeCompleted)
    assert events[-1].result.meta.latency_ms == 9
    assert guard.release_calls


@pytest.mark.unit
async def test_decode_clock_truncation(world: AppWorld) -> None:
    await world.ensure_granted_user(104)
    world.clock.advance(timedelta(microseconds=500_000))
    use_case, sink, _g = _ports(world)
    await _drain(use_case, 104, "incoming")
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].occurred_at.microsecond == 0


@pytest.mark.unit
async def test_decode_pair_scope_and_skipped_rules(
    world: AppWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    inviter = await world.ensure_granted_user(200)
    invitee = await world.ensure_granted_user(201)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            invite.invite.id,
            ContactLabel("Inviter"),
            RelationshipKind.PARTNER,
        )
    )
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(inviter.id, contact.id)
    )
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("shared pending"),
            shared=True,
        )
    )
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("private active"),
            shared=False,
        )
    )
    generator = FakeTextGenerator()
    use_case, _sink, _g = _ports(world, _DecodeFakes(generator=generator))
    await _drain(use_case, 200, "incoming")
    texts = [item.text for item in generator.decode_stream_calls[0].rules]
    assert texts == ["private active"]

    monkeypatch.setattr(
        "svoi_pravila.domain.rules.Rule.effective_revision",
        property(lambda self: None),
    )
    await _drain(use_case, 200, "incoming")
    assert generator.decode_stream_calls[-1].rules == ()

    pending = RuleRevision(
        number=1,
        text=RuleText("pending shape"),
        author_id=inviter.id,
        proposed_at=world.clock.now(),
        approved_by=frozenset({inviter.id}),
        effective_since=None,
    )
    monkeypatch.setattr(
        "svoi_pravila.domain.rules.Rule.effective_revision",
        property(lambda self: pending),
    )
    await _drain(use_case, 200, "incoming")
    assert generator.decode_stream_calls[-1].rules == ()


@pytest.mark.unit
async def test_decode_and_get_effective_rules_share_rule_set(world: AppWorld) -> None:
    inviter = await world.ensure_granted_user(210)
    invitee = await world.ensure_granted_user(211)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("Partner"), RelationshipKind.PARTNER)
        )
    ).contact
    invite = await CreateInvite(
        world.uow_factory, world.catalog, world.ids, world.tokens, world.clock
    ).execute(CreateInviteCommand(inviter.id, contact.id))
    accepted = await AcceptInvite(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        AcceptInviteCommand(
            invitee.id,
            invite.invite.id,
            ContactLabel("Inviter"),
            RelationshipKind.PARTNER,
        )
    )
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(inviter.id, contact.id)
    )
    owned = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("owner visible"),
            shared=False,
        )
    )
    hidden = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            invitee.id,
            accepted.invitee_contact.id,
            RuleCategory.OTHER,
            RuleText("partner hidden"),
            shared=False,
        )
    )
    generator = FakeTextGenerator()
    use_case, _sink, _g = _ports(world, _DecodeFakes(generator=generator))
    await _drain(use_case, 210, "incoming")
    effective = await GetEffectiveRules(world.uow_factory, world.catalog).execute(
        GetEffectiveRulesCommand(inviter.id, contact.id)
    )
    decode_texts = {item.text for item in generator.decode_stream_calls[0].rules}
    view_texts = {view.text.value for view in effective.rules}
    view_ids = {view.rule_id for view in effective.rules}
    assert decode_texts == view_texts
    assert owned.rule.id in view_ids
    assert hidden.rule.id not in view_ids
    assert "partner hidden" not in decode_texts


@pytest.mark.unit
async def test_decode_ok_event_recorded_after_completed_yield(world: AppWorld) -> None:
    await world.ensure_granted_user(106)
    use_case, sink, _g = _ports(world)
    agen: AsyncGenerator[DecodeEvent] = use_case.execute(
        DecodeIncomingCommand(TelegramUserId(106), "incoming", surface=UsageSurface.DM)
    )
    completed: DecodeCompleted | None = None
    async for event in agen:
        if isinstance(event, DecodeCompleted):
            completed = event
            break
    assert completed is not None
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []
    await agen.aclose()
    assert sink.events == []

    drained, drain_sink, _ = _ports(world)
    events = await _drain(drained, 106, "incoming")
    assert isinstance(events[-1], DecodeCompleted)
    assert isinstance(drain_sink, RecordingUsageEventSink)
    assert len(drain_sink.events) == 1
    assert drain_sink.events[0].outcome is UsageOutcome.OK


class _CancelStream(FakeTextGenerator):
    def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        self.decode_stream_calls.append(request)
        return _RaisingGen(asyncio.CancelledError())


class _RaisingGen:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __aiter__(self) -> _RaisingGen:
        return self

    async def __anext__(self) -> DecodeEvent:
        raise self._exc


@pytest.mark.unit
async def test_decode_releases_lock_on_cancellation(world: AppWorld) -> None:
    await world.ensure_granted_user(105)
    use_case, sink, guard = _ports(world, _DecodeFakes(generator=_CancelStream()))
    with pytest.raises(asyncio.CancelledError):
        await _drain(use_case, 105, "incoming")
    assert guard.release_calls
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events == []


@pytest.mark.unit
async def test_decode_crisis_screen_skips_quota_lock_and_generator(world: AppWorld) -> None:
    await world.ensure_granted_user(108)

    class AlwaysBusy(FakeConcurrencyGuard):
        async def acquire(self, key: str, *, ttl_seconds: int) -> str | None:
            self.acquire_calls.append((key, ttl_seconds))
            return None

    generator = FakeTextGenerator()
    quota = FakeRateLimiter(limit=0)
    use_case, sink, guard = _ports(
        world, _DecodeFakes(generator=generator, guard=AlwaysBusy(), quota=quota)
    )
    events = await _drain(use_case, 108, "он сказал, что не хочет жить")
    assert len(events) == 1
    assert isinstance(events[0], DecodeCompleted)
    assert events[0].result.safety is SafetyVerdict.CRISIS
    assert events[0].result.variants == ()
    assert events[0].applied_rules == ()
    assert generator.decode_stream_calls == []
    assert quota.check_count() == 0
    assert guard.acquire_calls == []
    assert isinstance(sink, RecordingUsageEventSink)
    event = sink.events[0]
    assert event.outcome is UsageOutcome.SCREENED
    assert event.safety == SafetyVerdict.CRISIS.value
    assert event.model is None
    assert event.attempts == 0
    assert event.billable_tokens == 0


@pytest.mark.unit
@pytest.mark.parametrize("phrase", THREAT_AND_HYPERBOLE_NEGATIVES)
async def test_decode_threat_and_hyperbole_still_calls_generator(
    world: AppWorld, phrase: str
) -> None:
    await world.ensure_granted_user(109)
    generator = FakeTextGenerator()
    use_case, sink, _guard = _ports(world, _DecodeFakes(generator=generator))
    events = await _drain(use_case, 109, phrase)
    assert any(isinstance(event, DecodeCompleted) for event in events)
    assert generator.decode_stream_calls
    assert isinstance(sink, RecordingUsageEventSink)
    assert sink.events[0].outcome is not UsageOutcome.SCREENED


@pytest.mark.unit
async def test_decode_maps_applied_rule_indexes(world: AppWorld) -> None:
    user = await world.ensure_granted_user(220)
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
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            user.id,
            contact.id,
            RuleCategory.TABOO_TOPIC,
            RuleText("не шутить про работу"),
            shared=False,
        )
    )
    generator = FakeTextGenerator(
        decode_result=DecodeResult(
            hypotheses=("h",),
            underlying_request="u",
            variants=(Variant(text="g", firmness=Firmness.GENTLE),),
            applied_rule_indexes=(0, 9, 1),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="m",
                prompt_version="p",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(),
            ),
        )
    )
    use_case, sink, _g = _ports(world, _DecodeFakes(generator=generator))
    events = await _drain(use_case, 220, "incoming please")
    completed = events[-1]
    assert isinstance(completed, DecodeCompleted)
    assert tuple(view.text for view in completed.applied_rules) == (
        "не повышать голос",
        "не шутить про работу",
    )
    assert isinstance(sink, RecordingUsageEventSink)
    blob = " ".join(str(event) for event in sink.events)
    assert "не повышать голос" not in blob
    assert "не шутить про работу" not in blob
