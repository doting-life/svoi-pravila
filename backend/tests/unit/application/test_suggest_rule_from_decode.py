"""Unit tests for SuggestRuleFromDecode outcomes."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, cast
from uuid import UUID

import pytest

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    CacheErrorKind,
    CacheUnavailable,
    ConflictError,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    NotFound,
    ServiceBudgetExhausted,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    SuggestRuleNothing,
    TokenUsage,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.application.rule_source import RuleSourcePayload
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.set_active_contact import (
    SetActiveContact,
    SetActiveContactCommand,
)
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    RULE_SOURCE_PURPOSE,
    SuggestRuleFromDecode,
    SuggestRuleFromDecodeCommand,
    SuggestRuleFromDecodeOutcome,
    SuggestRuleFromDecodePorts,
)
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import (
    RelationshipKind,
    RuleCategory,
    SuggestionSource,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import ContactId, RuleSuggestionId, TelegramUserId, UserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.text import ContactLabel, RuleText
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.quota_budget import FakeLlmBudget
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.rule_sources import FakeRuleSources
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink
from tests.unit.application.conftest import AppWorld


async def _active_contact(
    world: AppWorld, telegram_id: int = 901
) -> tuple[TelegramUserId, Contact]:
    user = await world.ensure_granted_user(telegram_id)
    contact = (
        await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
            CreateContactCommand(user.id, ContactLabel("Мама"), RelationshipKind.FAMILY)
        )
    ).contact
    await SetActiveContact(world.uow_factory, world.catalog).execute(
        SetActiveContactCommand(user.id, contact.id)
    )
    return TelegramUserId(telegram_id), contact


def _ports(
    world: AppWorld,
    *,
    sources: FakeRuleSources,
    generator: FakeTextGenerator,
    sink: RecordingUsageEventSink | None = None,
    llm_budget: FakeLlmBudget | None = None,
) -> SuggestRuleFromDecodePorts:
    return SuggestRuleFromDecodePorts(
        uow_factory=world.uow_factory,
        catalog=world.catalog,
        rule_sources=sources,
        generator=generator,
        llm_budget=llm_budget or FakeLlmBudget(),
        sink=sink or RecordingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )


@pytest.mark.unit
async def test_suggest_rule_ok_creates_decode_suggestion(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 901)
    sources = FakeRuleSources()
    sink = RecordingUsageEventSink()
    token = await sources.store(
        FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value)),
        RuleSourcePayload(contact_id=contact.id, incoming_text="Давай без сарказма"),
    )
    ports = _ports(world, sources=sources, generator=FakeTextGenerator(), sink=sink)
    result = await SuggestRuleFromDecode(ports).execute(
        SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
    )
    assert result.outcome is SuggestRuleFromDecodeOutcome.OK
    assert result.suggestion is not None
    assert result.suggestion.source is SuggestionSource.DECODE
    assert len(sink.events) == 1
    assert sink.events[0].scenario is UsageScenario.SUGGEST_RULE


@pytest.mark.unit
async def test_suggest_rule_none_unavailable_quota(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 902)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    token = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="ок")
    )
    generator = FakeTextGenerator()
    generator.suggest_rule_result = SuggestRuleNothing(
        meta=GenerationMeta(
            model="fake",
            prompt_version="suggest_rule@v1",
            latency_ms=1,
            attempts=1,
            usage=TokenUsage(1, 1, 0),
        ),
    )
    ports = _ports(world, sources=sources, generator=generator)
    uc = SuggestRuleFromDecode(ports)
    assert (
        await uc.execute(SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM))
    ).outcome is SuggestRuleFromDecodeOutcome.NONE
    assert (
        await uc.execute(SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM))
    ).outcome is SuggestRuleFromDecodeOutcome.UNAVAILABLE
    token2 = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="ещё")
    )
    ports_q = _ports(
        world,
        sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(exhausted=True),
    )
    with pytest.raises(ServiceBudgetExhausted):
        await SuggestRuleFromDecode(ports_q).execute(
            SuggestRuleFromDecodeCommand(tg, token2, surface=UsageSurface.DM)
        )


@pytest.mark.unit
async def test_suggest_rule_valkey_fail_closed(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 920)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    unavailable = CacheUnavailable(CacheErrorKind.SERVER)

    token_check = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="check fail")
    )
    ports_check = _ports(
        world,
        sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(check_unavailable=unavailable),
    )
    with pytest.raises(GenerationUnavailable) as check_info:
        await SuggestRuleFromDecode(ports_check).execute(
            SuggestRuleFromDecodeCommand(tg, token_check, surface=UsageSurface.DM)
        )
    assert check_info.value.kind is UnavailableKind.SERVER

    token_add = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="add fail")
    )
    ports_add = _ports(
        world,
        sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(add_unavailable=unavailable),
    )
    with pytest.raises(GenerationUnavailable) as add_info:
        await SuggestRuleFromDecode(ports_add).execute(
            SuggestRuleFromDecodeCommand(tg, token_add, surface=UsageSurface.DM)
        )
    assert add_info.value.kind is UnavailableKind.SERVER


@pytest.mark.unit
async def test_suggest_rule_pending_exists_and_crisis(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 903)
    user = await world.ensure_granted_user(903)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    async with world.uow_factory() as uow:
        await uow.rule_suggestions.add(
            RuleSuggestion.create_decode(
                suggestion_id=RuleSuggestionId(UUID(int=9)),
                user_id=user.id,
                contact_id=contact.id,
                category=RuleCategory.HOW_TO_ASK,
                text=RuleText("Мы говорим спокойно"),
                now=world.clock.now(),
            )
        )
        await uow.commit()
    generator = FakeTextGenerator()
    ports = _ports(world, sources=sources, generator=generator)
    token = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="повтор")
    )
    result = await SuggestRuleFromDecode(ports).execute(
        SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
    )
    assert result.outcome is SuggestRuleFromDecodeOutcome.PENDING_EXISTS
    assert generator.suggest_rule_calls == []

    token_c = await sources.store(
        pseudo,
        RuleSourcePayload(contact_id=contact.id, incoming_text="Я хочу покончить с собой"),
    )
    crisis = await SuggestRuleFromDecode(ports).execute(
        SuggestRuleFromDecodeCommand(tg, token_c, surface=UsageSurface.DM)
    )
    assert crisis.outcome is SuggestRuleFromDecodeOutcome.CRISIS


@pytest.mark.unit
async def test_suggest_rule_privacy_canary_absent_from_logs(
    world: AppWorld,
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    tg, contact = await _active_contact(world, 904)
    marker = "CANARY_SUGGEST_INCOMING_ZZZ"
    sources = FakeRuleSources()
    token = await sources.store(
        FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value)),
        RuleSourcePayload(contact_id=contact.id, incoming_text=marker),
    )
    ports = _ports(world, sources=sources, generator=FakeTextGenerator())
    await SuggestRuleFromDecode(ports).execute(
        SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
    )
    assert marker not in " ".join(str(event) for event in capture_log_events())


@pytest.mark.unit
async def test_suggest_rule_not_found_paths(world: AppWorld) -> None:
    sources = FakeRuleSources()
    ports = _ports(world, sources=sources, generator=FakeTextGenerator())
    with pytest.raises(NotFound):
        await SuggestRuleFromDecode(ports).execute(
            SuggestRuleFromDecodeCommand(TelegramUserId(999001), "missing", surface=UsageSurface.DM)
        )

    tg, contact = await _active_contact(world, 905)
    user = await world.ensure_granted_user(905)
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    foreign = ContactId(UUID(int=404))
    token = await sources.store(
        pseudo, RuleSourcePayload(contact_id=foreign, incoming_text="чужой контакт")
    )
    with pytest.raises(NotFound):
        await SuggestRuleFromDecode(ports).execute(
            SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
        )

    token_gone = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="user gone")
    )

    class _DeleteUserEnter:
        def __init__(self, inner: UnitOfWork, user_id: UserId) -> None:
            self._inner = inner
            self._user_id = user_id

        async def __aenter__(self) -> UnitOfWork:
            uow = await self._inner.__aenter__()
            await uow.users.delete(self._user_id)
            return uow

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            await self._inner.__aexit__(exc_type, exc, tb)

        async def commit(self) -> None:
            await self._inner.commit()

    class _ClearUserOnSecondOpen:
        def __init__(self) -> None:
            self._opens = 0

        def __call__(self) -> UnitOfWork:
            self._opens += 1
            inner = world.uow_factory()
            if self._opens < 2:
                return inner
            return cast(UnitOfWork, _DeleteUserEnter(inner, user.id))

    ports_gone = SuggestRuleFromDecodePorts(
        uow_factory=_ClearUserOnSecondOpen(),
        catalog=world.catalog,
        rule_sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(),
        sink=RecordingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )
    with pytest.raises(NotFound):
        await SuggestRuleFromDecode(ports_gone).execute(
            SuggestRuleFromDecodeCommand(tg, token_gone, surface=UsageSurface.DM)
        )


@pytest.mark.unit
async def test_suggest_rule_generation_errors_and_sink_fail(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 906)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))

    for error in (
        GenerationRefusedByProvider(
            usage=TokenUsage(input=2), attempts=2, model="m", prompt_version="p"
        ),
        InvalidGenerationOutput(
            (InvalidOutputReason.JSON_DECODE,),
            usage=TokenUsage(output=3),
            attempts=3,
            model="m",
            prompt_version="p",
        ),
        GenerationUnavailable(
            UnavailableKind.TIMEOUT,
            usage=TokenUsage(input=4),
            attempts=4,
            model="m",
            prompt_version="p",
        ),
    ):
        token = await sources.store(
            pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="ошибка")
        )
        generator = FakeTextGenerator()
        generator.suggest_rule_error = error
        sink = RecordingUsageEventSink()
        ports = SuggestRuleFromDecodePorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            rule_sources=sources,
            generator=generator,
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
        with pytest.raises(type(error)):
            await SuggestRuleFromDecode(ports).execute(
                SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
            )
        assert sink.events[-1].scenario is UsageScenario.SUGGEST_RULE
        if isinstance(error, GenerationUnavailable):
            assert sink.events[-1].outcome is UsageOutcome.UNAVAILABLE
        elif isinstance(error, GenerationRefusedByProvider):
            assert sink.events[-1].outcome is UsageOutcome.REFUSED
        else:
            assert sink.events[-1].outcome is UsageOutcome.INVALID_OUTPUT

    token_ok = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="sink fail ok")
    )
    ports_ok_fail = SuggestRuleFromDecodePorts(
        uow_factory=world.uow_factory,
        catalog=world.catalog,
        rule_sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(),
        sink=FailingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )
    ok_despite_sink = await SuggestRuleFromDecode(ports_ok_fail).execute(
        SuggestRuleFromDecodeCommand(tg, token_ok, surface=UsageSurface.DM)
    )
    assert ok_despite_sink.outcome is SuggestRuleFromDecodeOutcome.OK
    assert ok_despite_sink.suggestion is not None
    async with world.uow_factory() as uow:
        decided = ok_despite_sink.suggestion.dismiss(world.clock.now())
        await uow.rule_suggestions.update(decided)
        await uow.commit()

    token_err = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="sink fail err")
    )
    gen_err = FakeTextGenerator()
    gen_err.suggest_rule_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=TokenUsage(input=1),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    ports_err_fail = SuggestRuleFromDecodePorts(
        uow_factory=world.uow_factory,
        catalog=world.catalog,
        rule_sources=sources,
        generator=gen_err,
        llm_budget=FakeLlmBudget(),
        sink=FailingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )
    with pytest.raises(GenerationUnavailable):
        await SuggestRuleFromDecode(ports_err_fail).execute(
            SuggestRuleFromDecodeCommand(tg, token_err, surface=UsageSurface.DM)
        )


@pytest.mark.unit
async def test_suggest_rule_conflict_on_add_returns_pending_exists(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 907)
    user = await world.ensure_granted_user(907)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    token = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="гонка")
    )
    competing = RuleSuggestion.create_decode(
        suggestion_id=RuleSuggestionId(UUID(int=77)),
        user_id=user.id,
        contact_id=contact.id,
        category=RuleCategory.APOLOGY,
        text=RuleText("Мы извиняемся без оговорок"),
        now=world.clock.now(),
    )

    class _ConflictOnAdd:
        def __init__(self, inner: object, base_factory: object) -> None:
            self._inner = inner
            self._base_factory = base_factory

        async def add(self, suggestion: RuleSuggestion) -> None:
            _ = suggestion
            async with cast(Any, self._base_factory)() as uow:
                await uow.rule_suggestions.add(competing)
                await uow.commit()
            raise ConflictError()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    class _WrapUow:
        def __init__(self, inner: UnitOfWork, base_factory: object) -> None:
            self._inner = inner
            self._base_factory = base_factory

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        async def __aenter__(self) -> UnitOfWork:
            entered = await self._inner.__aenter__()
            wrapped = _ConflictOnAdd(entered.rule_suggestions, self._base_factory)
            object.__setattr__(self, "rule_suggestions", wrapped)
            for name in (
                "users",
                "consents",
                "contacts",
                "pairs",
                "rules",
                "invites",
                "usage_events",
                "tone_signals",
            ):
                object.__setattr__(self, name, getattr(entered, name))
            return cast(UnitOfWork, self)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            await self._inner.__aexit__(exc_type, exc, tb)

        async def commit(self) -> None:
            await self._inner.commit()

    class _Factory:
        def __init__(self) -> None:
            self._opens = 0

        def __call__(self) -> UnitOfWork:
            self._opens += 1
            inner = world.uow_factory()
            # Third open is the persist-suggestion transaction.
            if self._opens == 3:
                return cast(UnitOfWork, _WrapUow(inner, world.uow_factory))
            return inner

    ports = SuggestRuleFromDecodePorts(
        uow_factory=_Factory(),
        catalog=world.catalog,
        rule_sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(),
        sink=RecordingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )
    result = await SuggestRuleFromDecode(ports).execute(
        SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
    )
    assert result.outcome is SuggestRuleFromDecodeOutcome.PENDING_EXISTS
    assert result.suggestion is not None
    assert result.suggestion.id == competing.id


@pytest.mark.unit
async def test_suggest_rule_conflict_without_pending_raises_not_found(world: AppWorld) -> None:
    tg, contact = await _active_contact(world, 908)
    sources = FakeRuleSources()
    pseudo = FakePseudonymizer().pseudonymize(RULE_SOURCE_PURPOSE, str(tg.value))
    token = await sources.store(
        pseudo, RuleSourcePayload(contact_id=contact.id, incoming_text="гонка без pending")
    )

    class _RaiseConflict:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        async def add(self, suggestion: RuleSuggestion) -> None:
            _ = suggestion
            raise ConflictError()

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    class _WrapUow:
        def __init__(self, inner: UnitOfWork) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        async def __aenter__(self) -> UnitOfWork:
            entered = await self._inner.__aenter__()
            object.__setattr__(self, "rule_suggestions", _RaiseConflict(entered.rule_suggestions))
            for name in (
                "users",
                "consents",
                "contacts",
                "pairs",
                "rules",
                "invites",
                "usage_events",
                "tone_signals",
            ):
                object.__setattr__(self, name, getattr(entered, name))
            return cast(UnitOfWork, self)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            await self._inner.__aexit__(exc_type, exc, tb)

        async def commit(self) -> None:
            await self._inner.commit()

    class _Factory:
        def __init__(self) -> None:
            self._opens = 0

        def __call__(self) -> UnitOfWork:
            self._opens += 1
            inner = world.uow_factory()
            if self._opens == 3:
                return cast(UnitOfWork, _WrapUow(inner))
            return inner

    ports = SuggestRuleFromDecodePorts(
        uow_factory=_Factory(),
        catalog=world.catalog,
        rule_sources=sources,
        generator=FakeTextGenerator(),
        llm_budget=FakeLlmBudget(),
        sink=RecordingUsageEventSink(),
        clock=world.clock,
        monotonic=world.clock,
        ids=world.ids,
        pseudonymizer=FakePseudonymizer(),
        crisis_screen=CrisisScreen.load_ru_v2(),
        deadline_seconds=45.0,
        analytics_timezone="Europe/Moscow",
    )
    with pytest.raises(NotFound):
        await SuggestRuleFromDecode(ports).execute(
            SuggestRuleFromDecodeCommand(tg, token, surface=UsageSurface.DM)
        )
