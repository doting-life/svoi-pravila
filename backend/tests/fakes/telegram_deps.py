"""Shared TelegramDeps construction for tests."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    load_ru_strings,
)
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.inline_compose import InlineCompose, InlineComposePorts
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoice
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from tests.fakes.clock import FakeClock
from tests.fakes.concurrency import FakeConcurrencyGuard
from tests.fakes.confirmation import FakeConfirmationTokens
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.sleeper import GateSleeper, ImmediateSleeper
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
    sleeper: ImmediateSleeper | GateSleeper | None = None
    draft_min_interval_ms: int = 50
    deadline_seconds: float = 45.0
    inline_min_chars: int = 8
    inline_deadline_seconds: float = 8.0
    debounce_seconds: float = 0.0
    inline_cache_seconds: int = 30


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
            crisis_screen=CrisisScreen.load_ru_v1(),
            deadline_seconds=chosen.deadline_seconds,
        )
    )
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
            crisis_screen=CrisisScreen.load_ru_v1(),
            min_chars=chosen.inline_min_chars,
            deadline_seconds=chosen.inline_deadline_seconds,
            intent_prefixes=help_say_intent_prefixes(strings),
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
        record_inline_choice=RecordInlineChoice(sink, clock, ids, pseudonymizer),
        prepared_results=chosen.prepared or FakePreparedResults(),
        inline_queries=InlineQueryCoordinator(
            chosen.sleeper or ImmediateSleeper(),
            debounce_seconds=chosen.debounce_seconds,
        ),
        revoke_all_consents=RevokeAllConsents(uow, clock),
        delete_my_account=DeleteMyAccount(uow, ids, pseudonymizer, clock),
        export_my_data=ExportMyData(uow, clock),
        confirmation_tokens=chosen.confirmation or FakeConfirmationTokens(),
        clock=clock,
        deduplicator=FakeUpdateDeduplicator(),
        rate_limiter=FakeRateLimiter(limit=chosen.rate_limit),
        pseudonymizer=pseudonymizer,
        monotonic=clock,
        draft_min_interval_ms=chosen.draft_min_interval_ms,
        inline_cache_seconds=chosen.inline_cache_seconds,
    )
