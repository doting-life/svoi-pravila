"""Composition root — the only place that builds Settings and wires adapters."""

from __future__ import annotations

import http.client
import sys
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import uvicorn
from aiogram import Bot
from aiogram.types import Update
from fastapi import APIRouter, FastAPI
from gigachat import GigaChat
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.confirmation_tokens import ValkeyConfirmationTokens
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.dialog_state import ValkeyDialogState
from svoi_pravila.adapters.cache.llm_budget import ValkeyLlmBudget, ValkeyLlmBudgetConfig
from svoi_pravila.adapters.cache.prepared_results import ValkeyPreparedResults
from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.cache.quota_gate import ValkeyQuotaGate
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.cache.rule_sources import ValkeyRuleSources
from svoi_pravila.adapters.channels.telegram import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.export_document import TelegramExportDelivery
from svoi_pravila.adapters.channels.telegram.init_data import AiogramInitDataVerifier
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    load_ru_strings,
)
from svoi_pravila.adapters.channels.telegram.pair_notifier import TelegramPairNotifier
from svoi_pravila.adapters.channels.telegram.sleeper import AsyncioSleeper
from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.adapters.persistence.billable_token_sum import UowBillableTokenSum
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.probe import DatabaseProbe
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.adapters.system.analytics_scheduler import (
    AnalyticsScheduler,
    AnalyticsSchedulerSettings,
)
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.adapters.system.inline_result_reuse import InProcessInlineResultReuse
from svoi_pravila.adapters.system.monotonic import SystemMonotonicClock
from svoi_pravila.adapters.system.tokens import SecretsInviteTokenGenerator
from svoi_pravila.adapters.system.tone_suggestion_catalog import StaticToneSuggestionCatalog
from svoi_pravila.api.app import AppLifecycleHooks, DisposeHook, create_app
from svoi_pravila.api.miniapp import (
    MiniappDeps,
    MiniappRouterBindings,
    build_miniapp_router,
)
from svoi_pravila.api.telegram_webhook import (
    TelegramWebhookBindings,
    build_telegram_webhook_router,
)
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.ports.inline_result_reuse import InlineResultReuse
from svoi_pravila.application.ports.pair_notifier import PairNotifier
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite
from svoi_pravila.application.use_cases.accept_suggestion import AcceptSuggestion
from svoi_pravila.application.use_cases.approve_rule import ApproveRule
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
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
from svoi_pravila.application.use_cases.request_my_data_export import RequestMyDataExport
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.run_daily_analytics import (
    RunDailyAnalytics,
    RunDailyAnalyticsPorts,
)
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecode,
    SuggestRuleFromDecodePorts,
)
from svoi_pravila.config import (
    DatabaseSettings,
    Environment,
    GrafanaDbPasswordMissingError,
    LlmDailyTokenBudgetMissingError,
    MigrateSettings,
    Settings,
    TelegramUpdatesMode,
    TestInfraSettings,
)
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.observability import configure_logging

_HEALTHCHECK_TIMEOUT_SECONDS = 2.0
_HTTP_OK = 200


@dataclass(frozen=True, slots=True)
class _CorePorts:
    """Shared composition-root ports used by mini-app and Telegram wiring."""

    uow_factory: SqlAlchemyUnitOfWorkFactory
    catalog: PackageConsentCatalog
    ids: Uuid7IdGenerator
    clock: SystemClock
    valkey: Redis
    pseudonymizer: HmacPseudonymizer


@dataclass(frozen=True, slots=True)
class _DecodeWire:
    """Shared decode stack for mini-app and Telegram."""

    decode_incoming: DecodeIncoming
    suggest_rule_from_decode: SuggestRuleFromDecode
    prepared_results: ValkeyPreparedResults
    rule_sources: ValkeyRuleSources


@dataclass(frozen=True, slots=True)
class _MiniappWire:
    """Bot-side collaborators for the mini-app mount."""

    bot: Bot
    inline_reuse: InlineResultReuse
    decode: _DecodeWire
    pair_notifier: PairNotifier
    bot_username: BotUsernameCache
    invite_tokens: SecretsInviteTokenGenerator


def _build_miniapp_mount(
    settings: Settings,
    ports: _CorePorts,
    wire: _MiniappWire,
) -> APIRouter:
    """Wire mini-app `/api/v1` (caller ensures a bot token is configured)."""
    bot_token = settings.telegram_bot_token
    if bot_token is None or not bot_token.get_secret_value():
        msg = "mini-app mount requires telegram_bot_token"
        raise RuntimeError(msg)
    strings = load_ru_strings()
    export_my_data = ExportMyData(ports.uow_factory, ports.clock)
    auth = MiniappDeps(
        init_data_verifier=AiogramInitDataVerifier(
            bot_token,
            ports.clock,
            max_age_seconds=settings.miniapp_initdata_max_age_seconds,
        ),
        rate_limiter=ValkeyRateLimiter(
            ports.valkey,
            limit=settings.miniapp_requests_per_minute,
            window_seconds=60,
            key_prefix="miniapp:rl",
        ),
        pseudonymizer=ports.pseudonymizer,
        get_user_by_telegram_id=GetUserByTelegramId(ports.uow_factory),
        get_onboarding_step=GetOnboardingStep(ports.uow_factory, ports.catalog),
    )
    return build_miniapp_router(
        MiniappRouterBindings(
            auth=auth,
            list_contacts=ListContacts(ports.uow_factory, ports.catalog),
            create_contact=CreateContact(ports.uow_factory, ports.catalog, ports.ids, ports.clock),
            rename_contact=RenameContact(ports.uow_factory, ports.catalog),
            set_active_contact=SetActiveContact(ports.uow_factory, ports.catalog),
            create_invite=CreateInvite(
                ports.uow_factory,
                ports.catalog,
                ports.ids,
                wire.invite_tokens,
                ports.clock,
            ),
            leave_pair=LeavePair(ports.uow_factory, ports.ids, ports.clock, wire.pair_notifier),
            list_rules=ListRules(ports.uow_factory, ports.catalog),
            propose_rule=ProposeRule(
                ports.uow_factory,
                ports.catalog,
                ports.ids,
                ports.clock,
                wire.pair_notifier,
            ),
            archive_rule=ArchiveRule(ports.uow_factory, ports.catalog, ports.clock),
            approve_rule=ApproveRule(
                ports.uow_factory, ports.catalog, ports.clock, wire.pair_notifier
            ),
            reject_pending_rule=RejectPendingRule(
                ports.uow_factory, ports.catalog, ports.clock, wire.pair_notifier
            ),
            list_suggestions=ListSuggestions(ports.uow_factory, ports.catalog),
            accept_suggestion=AcceptSuggestion(
                ports.uow_factory, ports.catalog, ports.ids, ports.clock
            ),
            dismiss_suggestion=DismissSuggestion(ports.uow_factory, ports.catalog, ports.clock),
            request_my_data_export=RequestMyDataExport(
                export_my_data,
                TelegramExportDelivery(
                    wire.bot,
                    clock=ports.clock,
                    caption=strings.rights_export_caption,
                ),
            ),
            revoke_all_consents=RevokeAllConsents(
                ports.uow_factory, ports.clock, wire.inline_reuse
            ),
            delete_my_account=DeleteMyAccount(
                DeleteMyAccountPorts(
                    ports.uow_factory,
                    ports.ids,
                    ports.pseudonymizer,
                    ports.clock,
                    wire.inline_reuse,
                    wire.pair_notifier,
                )
            ),
            export_rate_limiter=ValkeyRateLimiter(
                ports.valkey,
                limit=3,
                window_seconds=3600,
                key_prefix="miniapp:export",
            ),
            display_timezone=settings.display_timezone,
            decode_incoming=wire.decode.decode_incoming,
            suggest_rule_from_decode=wire.decode.suggest_rule_from_decode,
            prepared_results=wire.decode.prepared_results,
            rule_sources=wire.decode.rule_sources,
            pseudonymizer=ports.pseudonymizer,
            bot_username=wire.bot_username,
            enable_test_routes=settings.environment is Environment.TEST,
        )
    )


def load_database_settings() -> DatabaseSettings:
    """Load Alembic-only settings (database URL and log level)."""
    return DatabaseSettings()


def load_migrate_settings() -> MigrateSettings:
    """Load compose migrate settings including ``SP_GRAFANA_DB_PASSWORD``.

    Raises:
        GrafanaDbPasswordMissingError: when the password env var is unset or blank.
    """
    try:
        return MigrateSettings()
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "grafana_db_password":
                raise GrafanaDbPasswordMissingError() from exc
        raise


def load_test_infra_settings() -> TestInfraSettings:
    """Load test-fixture settings (database and Valkey URLs only)."""
    return TestInfraSettings()


def load_settings() -> Settings:
    """Load full API-process Settings from the process environment.

    Raises:
        LlmDailyTokenBudgetMissingError: when ``SP_LLM_DAILY_TOKEN_BUDGET`` is unset.
    """
    try:
        return Settings()
    except ValidationError as exc:
        for err in exc.errors():
            loc = err.get("loc", ())
            if loc and loc[0] == "llm_daily_token_budget":
                raise LlmDailyTokenBudgetMissingError() from exc
        raise


def _wire_quota_budget(
    settings: Settings,
    valkey: Redis,
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    clock: SystemClock,
) -> tuple[ValkeyConcurrencyGuard, ValkeyQuotaGate, ValkeyLlmBudget]:
    """Compose daily QuotaGate and LlmBudget; concurrency guard is for decode locks."""
    guard = ValkeyConcurrencyGuard(valkey)
    quota_gate = ValkeyQuotaGate(
        valkey,
        inline_limit=settings.quota_inline_per_day,
        decode_limit=settings.quota_decode_per_day,
        timezone=settings.analytics_timezone,
    )
    llm_budget = ValkeyLlmBudget(
        valkey,
        config=ValkeyLlmBudgetConfig(
            budget=settings.llm_daily_token_budget,
            timezone=settings.analytics_timezone,
            hourly_spike_share=settings.llm_hourly_spike_share,
        ),
        sums=UowBillableTokenSum(uow_factory, timezone=settings.analytics_timezone),
        clock=clock,
    )
    return guard, quota_gate, llm_budget


def create_application(settings: Settings) -> FastAPI:
    """Build the fully wired ASGI application from already-loaded settings."""
    configure_logging(settings, sys.stdout)

    engine = create_engine(settings)
    valkey = create_client(settings)
    gigachat = create_gigachat_client(settings)
    check_readiness = CheckReadiness(
        probes=(DatabaseProbe(engine), ValkeyProbe(valkey)),
        timeout_seconds=settings.readiness_timeout_seconds,
    )

    uow_factory = SqlAlchemyUnitOfWorkFactory(
        engine,
        kek=settings.data_kek_bytes(),
        kek_id=settings.data_kek_id,
    )
    clock, monotonic, ids = SystemClock(), SystemMonotonicClock(), Uuid7IdGenerator()
    catalog = PackageConsentCatalog()
    tone_catalog = StaticToneSuggestionCatalog()
    generator = GigaChatTextGenerator(gigachat, settings)
    sink = UnitOfWorkUsageEventSink(uow_factory)
    pseudonymizer = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    crisis_screen = CrisisScreen.load_ru_v2()
    inline_reuse = InProcessInlineResultReuse(
        monotonic,
        ttl_seconds=float(settings.inline_cache_seconds),
        max_entries=settings.inline_reuse_max_entries,
    )

    core = _CorePorts(
        uow_factory=uow_factory,
        catalog=catalog,
        ids=ids,
        clock=clock,
        valkey=valkey,
        pseudonymizer=pseudonymizer,
    )
    prepared_results = ValkeyPreparedResults(
        valkey,
        ttl_seconds=settings.prepared_result_ttl_seconds,
    )
    rule_sources = ValkeyRuleSources(
        valkey,
        ttl_seconds=settings.rule_source_ttl_seconds,
    )
    concurrency_guard, quota_gate, llm_budget = _wire_quota_budget(
        settings, valkey, uow_factory, clock
    )
    decode_wire = _DecodeWire(
        decode_incoming=DecodeIncoming(
            DecodeIncomingPorts(
                uow_factory=uow_factory,
                catalog=catalog,
                generator=generator,
                guard=concurrency_guard,
                quota_gate=quota_gate,
                llm_budget=llm_budget,
                sink=sink,
                clock=clock,
                monotonic=monotonic,
                ids=ids,
                pseudonymizer=pseudonymizer,
                crisis_screen=crisis_screen,
                deadline_seconds=settings.decode_deadline_seconds,
                analytics_timezone=settings.analytics_timezone,
            )
        ),
        suggest_rule_from_decode=SuggestRuleFromDecode(
            SuggestRuleFromDecodePorts(
                uow_factory=uow_factory,
                catalog=catalog,
                rule_sources=rule_sources,
                generator=generator,
                llm_budget=llm_budget,
                sink=sink,
                clock=clock,
                monotonic=monotonic,
                ids=ids,
                pseudonymizer=pseudonymizer,
                crisis_screen=crisis_screen,
                deadline_seconds=settings.decode_deadline_seconds,
                analytics_timezone=settings.analytics_timezone,
            )
        ),
        prepared_results=prepared_results,
        rule_sources=rule_sources,
    )
    lifecycle: TelegramLifecycle | None = None
    routers: list[APIRouter] = []
    shared_bot: Bot | None = None
    pair_notifier: TelegramPairNotifier | None = None
    bot_username = BotUsernameCache()
    invite_tokens = SecretsInviteTokenGenerator()
    bot_token = settings.telegram_bot_token
    if bot_token is not None and bot_token.get_secret_value():
        shared_bot = Bot(token=bot_token.get_secret_value())
        pair_notifier = TelegramPairNotifier(shared_bot, uow_factory, load_ru_strings())
        routers.append(
            _build_miniapp_mount(
                settings,
                core,
                _MiniappWire(
                    bot=shared_bot,
                    inline_reuse=inline_reuse,
                    decode=decode_wire,
                    pair_notifier=pair_notifier,
                    bot_username=bot_username,
                    invite_tokens=invite_tokens,
                ),
            )
        )
    if settings.telegram_updates_mode is not TelegramUpdatesMode.DISABLED:
        if shared_bot is None or pair_notifier is None:
            msg = "telegram updates require telegram_bot_token"
            raise RuntimeError(msg)
        strings = load_ru_strings()
        inline_compose = InlineCompose(
            InlineComposePorts(
                uow_factory=uow_factory,
                catalog=catalog,
                generator=generator,
                quota_gate=quota_gate,
                llm_budget=llm_budget,
                sink=sink,
                clock=clock,
                monotonic=monotonic,
                ids=ids,
                pseudonymizer=pseudonymizer,
                crisis_screen=crisis_screen,
                reuse=inline_reuse,
                min_chars=settings.inline_min_chars,
                deadline_seconds=settings.inline_deadline_seconds,
                intent_prefixes=help_say_intent_prefixes(strings),
                analytics_timezone=settings.analytics_timezone,
            )
        )
        deps = TelegramDeps(
            strings=strings,
            get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
            get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
            accept_age=AcceptAgeConfirmation(uow_factory, ids, clock),
            grant_consent=GrantConsent(uow_factory, catalog, ids, clock),
            get_consent_document=GetConsentDocument(catalog),
            decode_incoming=decode_wire.decode_incoming,
            inline_compose=inline_compose,
            record_inline_choice=RecordInlineChoice(
                RecordInlineChoicePorts(
                    sink=sink,
                    uow_factory=uow_factory,
                    catalog=catalog,
                    tone_catalog=tone_catalog,
                    clock=clock,
                    ids=ids,
                    pseudonymizer=pseudonymizer,
                )
            ),
            prepared_results=decode_wire.prepared_results,
            rule_sources=decode_wire.rule_sources,
            suggest_rule_from_decode=decode_wire.suggest_rule_from_decode,
            inline_queries=InlineQueryCoordinator(
                AsyncioSleeper(),
                debounce_seconds=settings.inline_debounce_ms / 1000.0,
            ),
            revoke_all_consents=RevokeAllConsents(uow_factory, clock, inline_reuse),
            delete_my_account=DeleteMyAccount(
                DeleteMyAccountPorts(
                    uow_factory, ids, pseudonymizer, clock, inline_reuse, pair_notifier
                )
            ),
            export_my_data=ExportMyData(uow_factory, clock),
            confirmation_tokens=ValkeyConfirmationTokens(valkey),
            create_contact=CreateContact(uow_factory, catalog, ids, clock),
            list_contacts=ListContacts(uow_factory, catalog),
            rename_contact=RenameContact(uow_factory, catalog),
            set_active_contact=SetActiveContact(uow_factory, catalog),
            create_invite=CreateInvite(uow_factory, catalog, ids, invite_tokens, clock),
            resolve_invite=ResolveInvite(uow_factory, catalog, clock),
            accept_invite=AcceptInvite(uow_factory, catalog, ids, clock, pair_notifier),
            leave_pair=LeavePair(uow_factory, ids, clock, pair_notifier),
            propose_rule=ProposeRule(uow_factory, catalog, ids, clock, pair_notifier),
            approve_rule=ApproveRule(uow_factory, catalog, clock, pair_notifier),
            reject_pending_rule=RejectPendingRule(uow_factory, catalog, clock, pair_notifier),
            list_rules=ListRules(uow_factory, catalog),
            archive_rule=ArchiveRule(uow_factory, catalog, clock),
            list_suggestions=ListSuggestions(uow_factory, catalog),
            accept_suggestion=AcceptSuggestion(uow_factory, catalog, ids, clock),
            dismiss_suggestion=DismissSuggestion(uow_factory, catalog, clock),
            dialog_state=ValkeyDialogState(
                valkey,
                ttl_seconds=settings.dialog_ttl_seconds,
            ),
            bot_username=bot_username,
            clock=clock,
            display_timezone=ZoneInfo(settings.display_timezone),
            deduplicator=ValkeyUpdateDeduplicator(
                valkey,
                ttl_seconds=settings.telegram_dedup_ttl_seconds,
            ),
            rate_limiter=ValkeyRateLimiter(
                valkey,
                limit=settings.telegram_rate_limit_per_minute,
                window_seconds=60,
                key_prefix="tg:rl",
            ),
            pseudonymizer=pseudonymizer,
            monotonic=monotonic,
            draft_min_interval_ms=settings.telegram_draft_min_interval_ms,
            inline_cache_seconds=settings.inline_cache_seconds,
        )
        lifecycle = build_telegram_lifecycle(
            settings,
            deps,
            bot=shared_bot,
            extra_tasks=lambda: set(inline_reuse.tasks),
        )
        if settings.telegram_updates_mode is TelegramUpdatesMode.WEBHOOK:
            path_secret = settings.telegram_webhook_path_secret
            secret_token = settings.telegram_webhook_secret_token
            if path_secret is None or secret_token is None:
                msg = "webhook secrets required in webhook mode"
                raise RuntimeError(msg)
            bound_lifecycle = lifecycle

            async def _feed(update: Update) -> None:
                await bound_lifecycle.dispatcher.feed_update(bound_lifecycle.bot, update)

            routers.append(
                build_telegram_webhook_router(
                    TelegramWebhookBindings(
                        is_accepting=lambda: bound_lifecycle.accepting,
                        path_secret=path_secret.get_secret_value(),
                        secret_token=secret_token.get_secret_value(),
                        parse_bot=bound_lifecycle.bot,
                        feed_update=_feed,
                        schedule=bound_lifecycle.schedule_update,
                    )
                )
            )

    return create_app(
        check_readiness,
        settings.environment,
        _app_lifecycle_hooks(
            lifecycle=lifecycle,
            io=_AppIoClients(gigachat=gigachat, valkey=valkey, engine=engine),
            routers=tuple(routers),
            extra_shutdown=_miniapp_bot_shutdown(lifecycle, shared_bot),
            analytics_scheduler=_analytics_scheduler(settings, engine, ids, clock),
        ),
        display_timezone=settings.display_timezone,
    )


@dataclass(frozen=True, slots=True)
class _AppIoClients:
    """I/O clients closed on application dispose."""

    gigachat: GigaChat
    valkey: Redis
    engine: AsyncEngine


def _analytics_scheduler(
    settings: Settings,
    engine: AsyncEngine,
    ids: Uuid7IdGenerator,
    clock: SystemClock,
) -> AnalyticsScheduler:
    store = SqlAlchemyAnalyticsStore(engine)
    return AnalyticsScheduler(
        RunDailyAnalytics(
            RunDailyAnalyticsPorts(
                store=store,
                ids=ids,
                clock=clock,
                timezone=settings.analytics_timezone,
                llm_budget_tokens=settings.llm_daily_token_budget,
            )
        ),
        clock,
        AnalyticsSchedulerSettings(
            timezone=settings.analytics_timezone,
            run_at=settings.analytics_run_at,
            enabled=settings.analytics_jobs_enabled,
        ),
    )


def _miniapp_bot_shutdown(
    lifecycle: TelegramLifecycle | None,
    shared_bot: Bot | None,
) -> DisposeHook | None:
    """Close the mini-app Bot session when Telegram lifecycle does not own it."""
    if lifecycle is not None or shared_bot is None:
        return None

    async def _close() -> None:
        await shared_bot.session.close()

    return _close


def _app_lifecycle_hooks(
    *,
    lifecycle: TelegramLifecycle | None,
    io: _AppIoClients,
    routers: tuple[APIRouter, ...],
    extra_shutdown: DisposeHook | None = None,
    analytics_scheduler: AnalyticsScheduler | None = None,
) -> AppLifecycleHooks:
    """Build FastAPI lifespan hooks for optional Telegram lifecycle and I/O clients."""

    async def on_startup() -> None:
        if lifecycle is not None:
            await lifecycle.start()
        if analytics_scheduler is not None:
            await analytics_scheduler.start()

    async def on_shutdown() -> None:
        if analytics_scheduler is not None:
            await analytics_scheduler.shutdown()
        if lifecycle is not None:
            await lifecycle.shutdown()
        if extra_shutdown is not None:
            await extra_shutdown()

    async def dispose() -> None:
        await close_gigachat_client(io.gigachat)
        await close_client(io.valkey)
        await dispose_engine(io.engine)

    return AppLifecycleHooks(
        dispose=dispose,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        extra_routers=routers,
    )


def serve() -> None:
    """Load settings, build the app, and run uvicorn programmatically."""
    settings = load_settings()
    app = create_application(settings)
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        access_log=False,
        log_config=None,
    )


def healthcheck() -> None:
    """GET local ``/readyz``; exit 0 on HTTP 200, otherwise 1.

    Intended as the container healthcheck entrypoint. Never prints response
    bodies or settings.
    """
    settings = load_settings()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        settings.http_port,
        timeout=_HEALTHCHECK_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/readyz")
        response = connection.getresponse()
        response.read()
        code = response.status
    except (OSError, TimeoutError, http.client.HTTPException):
        sys.exit(1)
    finally:
        connection.close()
    sys.exit(0 if code == _HTTP_OK else 1)
