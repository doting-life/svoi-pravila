"""Shared TelegramDeps construction for tests."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    load_ru_strings,
)
from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.approve_rule import ApproveRule
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.create_invite import CreateInvite
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountPorts,
)
from svoi_pravila.application.use_cases.dismiss_suggestion import DismissSuggestion
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.inline_compose import InlineCompose, InlineComposePorts
from svoi_pravila.application.use_cases.leave_pair import LeavePair
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestions
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoicePorts,
)
from svoi_pravila.application.use_cases.reject_pending_rule import RejectPendingRule
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecode,
    SuggestRuleFromDecodePorts,
)
from tests.fakes.clock import FakeClock
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.confirmation import FakeConfirmationTokens
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.dialog import FakeDialogState
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.rule_sources import FakeRuleSources
from tests.fakes.sleeper import GateSleeper, ImmediateSleeper
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink


@dataclass(frozen=True, slots=True)
class TelegramTestDeps:
    """Optional fakes for ``make_telegram_deps``."""

    uow: InMemoryUnitOfWorkFactory | None = None
    catalog: FakeConsentCatalog | None = None
    clock: FakeClock | None = None
    ids: FakeIdGenerator | None = None
    rate_limit: int = 30
    quota_limit: int = 20
    inline_quota_limit: int = 30
    generator: FakeTextGenerator | None = None
    guard: FakeConcurrencyGuard | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    confirmation: FakeConfirmationTokens | None = None
    prepared: FakePreparedResults | None = None
    rule_sources: FakeRuleSources | None = None
    dialog: FakeDialogState | None = None
    sleeper: ImmediateSleeper | GateSleeper | None = None
    draft_min_interval_ms: int = 50
    deadline_seconds: float = 45.0
    inline_min_chars: int = 8
    inline_deadline_seconds: float = 8.0
    debounce_seconds: float = 0.0
    inline_cache_seconds: int = 30
    suggest_quota_limit: int = 10
    pair_notifier: FakePairNotifier | None = None
    tokens: FakeTokenGenerator | None = None
    bot_username: str | None = "test_bot"


def make_telegram_deps(spec: TelegramTestDeps | None = None) -> TelegramDeps:
    """Wire onboarding, decode, and inline use cases over in-memory fakes."""
    chosen = spec or TelegramTestDeps()
    uow = chosen.uow or InMemoryUnitOfWorkFactory()
    catalog = chosen.catalog or FakeConsentCatalog()
    clock = chosen.clock or FakeClock()
    ids = chosen.ids or FakeIdGenerator()
    strings = load_ru_strings()
    pseudonymizer = FakePseudonymizer()
    sink = chosen.sink or RecordingUsageEventSink()
    generator = chosen.generator or FakeTextGenerator()
    tone_catalog = StaticToneSuggestionCatalog()
    notifier = chosen.pair_notifier or FakePairNotifier()
    tokens = chosen.tokens or FakeTokenGenerator()
    decode = DecodeIncoming(
        DecodeIncomingPorts(
            uow_factory=uow,
            catalog=catalog,
            generator=generator,
            guard=chosen.guard or FakeConcurrencyGuard(),
            quota=FakeRateLimiter(limit=chosen.quota_limit),
            sink=sink,
            clock=clock,
            monotonic=clock,
            ids=ids,
            pseudonymizer=pseudonymizer,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=chosen.deadline_seconds,
        )
    )
    reuse = make_inline_reuse(clock, ttl_seconds=float(chosen.inline_cache_seconds))
    compose = InlineCompose(
        InlineComposePorts(
            uow_factory=uow,
            catalog=catalog,
            generator=generator,
            quota=FakeRateLimiter(limit=chosen.inline_quota_limit),
            sink=sink,
            clock=clock,
            monotonic=clock,
            ids=ids,
            pseudonymizer=pseudonymizer,
            crisis_screen=CrisisScreen.load_ru_v2(),
            reuse=reuse,
            min_chars=chosen.inline_min_chars,
            deadline_seconds=chosen.inline_deadline_seconds,
            intent_prefixes=help_say_intent_prefixes(strings),
        )
    )
    rule_sources = chosen.rule_sources or FakeRuleSources()
    suggest = SuggestRuleFromDecode(
        SuggestRuleFromDecodePorts(
            uow_factory=uow,
            catalog=catalog,
            rule_sources=rule_sources,
            generator=generator,
            quota=FakeRateLimiter(limit=chosen.suggest_quota_limit),
            sink=sink,
            clock=clock,
            monotonic=clock,
            ids=ids,
            pseudonymizer=pseudonymizer,
            crisis_screen=CrisisScreen.load_ru_v2(),
            deadline_seconds=chosen.deadline_seconds,
        )
    )
    return TelegramDeps(
        strings=strings,
        get_onboarding_step=GetOnboardingStep(uow, catalog),
        get_user_by_telegram_id=GetUserByTelegramId(uow),
        accept_age=AcceptAgeConfirmation(uow, ids, clock),
        grant_consent=GrantConsent(uow, catalog, ids, clock),
        get_consent_document=GetConsentDocument(catalog),
        decode_incoming=decode,
        inline_compose=compose,
        record_inline_choice=RecordInlineChoice(
            RecordInlineChoicePorts(
                sink=sink,
                uow_factory=uow,
                catalog=catalog,
                tone_catalog=tone_catalog,
                clock=clock,
                ids=ids,
                pseudonymizer=pseudonymizer,
            )
        ),
        prepared_results=chosen.prepared or FakePreparedResults(),
        rule_sources=rule_sources,
        suggest_rule_from_decode=suggest,
        inline_queries=InlineQueryCoordinator(
            chosen.sleeper or ImmediateSleeper(),
            debounce_seconds=chosen.debounce_seconds,
        ),
        revoke_all_consents=RevokeAllConsents(uow, clock, reuse),
        delete_my_account=DeleteMyAccount(
            DeleteMyAccountPorts(uow, ids, pseudonymizer, clock, reuse, notifier)
        ),
        export_my_data=ExportMyData(uow, clock),
        confirmation_tokens=chosen.confirmation or FakeConfirmationTokens(),
        create_contact=CreateContact(uow, catalog, ids, clock),
        list_contacts=ListContacts(uow, catalog),
        rename_contact=RenameContact(uow, catalog),
        set_active_contact=SetActiveContact(uow, catalog),
        create_invite=CreateInvite(uow, catalog, ids, tokens, clock),
        resolve_invite=ResolveInvite(uow, catalog, clock),
        accept_invite=AcceptInvite(uow, catalog, ids, clock, notifier),
        leave_pair=LeavePair(uow, ids, clock, notifier),
        propose_rule=ProposeRule(uow, catalog, ids, clock, notifier),
        approve_rule=ApproveRule(uow, catalog, clock, notifier),
        reject_pending_rule=RejectPendingRule(uow, catalog, clock, notifier),
        list_rules=ListRules(uow, catalog),
        archive_rule=ArchiveRule(uow, catalog, clock),
        list_suggestions=ListSuggestions(uow, catalog),
        accept_suggestion=AcceptSuggestion(uow, catalog, ids, clock),
        dismiss_suggestion=DismissSuggestion(uow, catalog, clock),
        dialog_state=chosen.dialog or FakeDialogState(),
        bot_username=BotUsernameCache(username=chosen.bot_username),
        clock=clock,
        display_timezone=ZoneInfo("Europe/Moscow"),
        deduplicator=FakeUpdateDeduplicator(),
        rate_limiter=FakeRateLimiter(limit=chosen.rate_limit),
        pseudonymizer=pseudonymizer,
        monotonic=clock,
        draft_min_interval_ms=chosen.draft_min_interval_ms,
        inline_cache_seconds=chosen.inline_cache_seconds,
    )
