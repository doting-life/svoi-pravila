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
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.inline_compose import InlineCompose, InlineComposePorts
from svoi_pravila.application.use_cases.record_inline_choice import (
    RecordInlineChoice,
    RecordInlineChoicePorts,
)
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.prepared import FakePreparedResults
from tests.fakes.quota_budget import FakeLlmBudget, FakeQuotaGate
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter, FakeUpdateDeduplicator
from tests.fakes.sleeper import GateSleeper, ImmediateSleeper
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import FailingUsageEventSink, RecordingUsageEventSink
from tests.fakes.welcome_throttle import FakeWelcomeThrottle


@dataclass(frozen=True, slots=True)
class TelegramTestDeps:
    """Optional fakes for ``make_telegram_deps``."""

    uow: InMemoryUnitOfWorkFactory | None = None
    catalog: FakeConsentCatalog | None = None
    clock: FakeClock | None = None
    ids: FakeIdGenerator | None = None
    rate_limit: int = 30
    inline_quota_limit: int = 30
    budget_exhausted: bool = False
    generator: FakeTextGenerator | None = None
    sink: RecordingUsageEventSink | FailingUsageEventSink | None = None
    prepared: FakePreparedResults | None = None
    sleeper: ImmediateSleeper | GateSleeper | None = None
    inline_min_chars: int = 8
    inline_deadline_seconds: float = 8.0
    debounce_seconds: float = 0.0
    inline_cache_seconds: int = 30
    bot_username: str | None = "test_bot"
    miniapp_url: str | None = "https://miniapp.example"
    welcome: FakeWelcomeThrottle | None = None


def make_telegram_deps(spec: TelegramTestDeps | None = None) -> TelegramDeps:
    """Wire inline use cases over in-memory fakes."""
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
    llm_budget = FakeLlmBudget(exhausted=chosen.budget_exhausted)
    reuse = make_inline_reuse(clock, ttl_seconds=float(chosen.inline_cache_seconds))
    compose = InlineCompose(
        InlineComposePorts(
            uow_factory=uow,
            catalog=catalog,
            generator=generator,
            quota_gate=FakeQuotaGate(limit=chosen.inline_quota_limit),
            llm_budget=llm_budget,
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
            analytics_timezone="Europe/Moscow",
        )
    )
    return TelegramDeps(
        strings=strings,
        get_user_by_telegram_id=GetUserByTelegramId(uow),
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
        inline_queries=InlineQueryCoordinator(
            chosen.sleeper or ImmediateSleeper(),
            debounce_seconds=chosen.debounce_seconds,
        ),
        welcome_throttle=chosen.welcome or FakeWelcomeThrottle(),
        bot_username=BotUsernameCache(username=chosen.bot_username),
        clock=clock,
        display_timezone=ZoneInfo("Europe/Moscow"),
        deduplicator=FakeUpdateDeduplicator(),
        rate_limiter=FakeRateLimiter(limit=chosen.rate_limit),
        pseudonymizer=pseudonymizer,
        monotonic=clock,
        inline_cache_seconds=chosen.inline_cache_seconds,
        miniapp_url=chosen.miniapp_url,
    )
