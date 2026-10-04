"""Composition root — the only place that builds Settings and wires adapters."""

from __future__ import annotations

import http.client
import sys
from zoneinfo import ZoneInfo

import uvicorn
from aiogram.types import Update
from fastapi import APIRouter, FastAPI

from svoi_pravila.adapters.cache.client import close_client, create_client
from svoi_pravila.adapters.cache.concurrency import ValkeyConcurrencyGuard
from svoi_pravila.adapters.cache.confirmation_tokens import ValkeyConfirmationTokens
from svoi_pravila.adapters.cache.deduplicator import ValkeyUpdateDeduplicator
from svoi_pravila.adapters.cache.dialog_state import ValkeyDialogState
from svoi_pravila.adapters.cache.prepared_results import ValkeyPreparedResults
from svoi_pravila.adapters.cache.probe import ValkeyProbe
from svoi_pravila.adapters.cache.rate_limiter import ValkeyRateLimiter
from svoi_pravila.adapters.channels.telegram import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.localization import (
    help_say_intent_prefixes,
    load_ru_strings,
)
from svoi_pravila.adapters.channels.telegram.sleeper import AsyncioSleeper
from svoi_pravila.adapters.consents import PackageConsentCatalog
from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import close_gigachat_client, create_gigachat_client
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.probe import DatabaseProbe
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.adapters.system.monotonic import SystemMonotonicClock
from svoi_pravila.api.app import AppLifecycleHooks, create_app
from svoi_pravila.api.telegram_webhook import (
    TelegramWebhookBindings,
    build_telegram_webhook_router,
)
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.archive_rule import ArchiveRule
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.application.use_cases.create_contact import CreateContact
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncoming, DecodeIncomingPorts
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccount
from svoi_pravila.application.use_cases.export_my_data import ExportMyData
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent
from svoi_pravila.application.use_cases.inline_compose import InlineCompose, InlineComposePorts
from svoi_pravila.application.use_cases.list_contacts import ListContacts
from svoi_pravila.application.use_cases.list_rules import ListRules
from svoi_pravila.application.use_cases.propose_rule import ProposeRule
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoice
from svoi_pravila.application.use_cases.rename_contact import RenameContact
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsents
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContact
from svoi_pravila.config import (
    DatabaseSettings,
    Settings,
    TelegramUpdatesMode,
    TestInfraSettings,
)
from svoi_pravila.crypto import HmacPseudonymizer
from svoi_pravila.observability import configure_logging

_HEALTHCHECK_TIMEOUT_SECONDS = 2.0
_HTTP_OK = 200


def load_database_settings() -> DatabaseSettings:
    """Load migrate-process settings (database URL and log level only)."""
    return DatabaseSettings()


def load_test_infra_settings() -> TestInfraSettings:
    """Load test-fixture settings (database and Valkey URLs only)."""
    return TestInfraSettings()


def load_settings() -> Settings:
    """Load full API-process Settings from the process environment."""
    return Settings()


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
    clock = SystemClock()
    monotonic = SystemMonotonicClock()
    ids = Uuid7IdGenerator()
    catalog = PackageConsentCatalog()
    generator = GigaChatTextGenerator(gigachat, settings)
    sink = UnitOfWorkUsageEventSink(uow_factory)
    pseudonymizer = HmacPseudonymizer(settings.pseudonym_pepper_bytes())
    crisis_screen = CrisisScreen.load_ru_v2()

    lifecycle = None
    extra_routers: tuple[APIRouter, ...] = ()
    if settings.telegram_updates_mode is not TelegramUpdatesMode.DISABLED:
        strings = load_ru_strings()
        decode_incoming = DecodeIncoming(
            DecodeIncomingPorts(
                uow_factory=uow_factory,
                catalog=catalog,
                generator=generator,
                guard=ValkeyConcurrencyGuard(valkey),
                quota=ValkeyRateLimiter(
                    valkey,
                    limit=settings.decode_per_hour,
                    window_seconds=3600,
                    key_prefix="tg:decode:quota",
                ),
                sink=sink,
                clock=clock,
                monotonic=monotonic,
                ids=ids,
                pseudonymizer=pseudonymizer,
                crisis_screen=crisis_screen,
                deadline_seconds=settings.decode_deadline_seconds,
            )
        )
        inline_compose = InlineCompose(
            InlineComposePorts(
                uow_factory=uow_factory,
                catalog=catalog,
                generator=generator,
                quota=ValkeyRateLimiter(
                    valkey,
                    limit=settings.inline_per_hour,
                    window_seconds=3600,
                    key_prefix="tg:inline:quota",
                ),
                sink=sink,
                clock=clock,
                monotonic=monotonic,
                ids=ids,
                pseudonymizer=pseudonymizer,
                crisis_screen=crisis_screen,
                min_chars=settings.inline_min_chars,
                deadline_seconds=settings.inline_deadline_seconds,
                intent_prefixes=help_say_intent_prefixes(strings),
            )
        )
        deps = TelegramDeps(
            strings=strings,
            get_onboarding_step=GetOnboardingStep(uow_factory, catalog),
            get_user_by_telegram_id=GetUserByTelegramId(uow_factory),
            accept_age=AcceptAgeConfirmation(uow_factory, ids, clock),
            grant_consent=GrantConsent(uow_factory, catalog, ids, clock),
            get_consent_document=GetConsentDocument(catalog),
            decode_incoming=decode_incoming,
            inline_compose=inline_compose,
            record_inline_choice=RecordInlineChoice(sink, clock, ids, pseudonymizer),
            prepared_results=ValkeyPreparedResults(
                valkey,
                ttl_seconds=settings.prepared_result_ttl_seconds,
            ),
            inline_queries=InlineQueryCoordinator(
                AsyncioSleeper(),
                debounce_seconds=settings.inline_debounce_ms / 1000.0,
            ),
            revoke_all_consents=RevokeAllConsents(uow_factory, clock),
            delete_my_account=DeleteMyAccount(uow_factory, ids, pseudonymizer, clock),
            export_my_data=ExportMyData(uow_factory, clock),
            confirmation_tokens=ValkeyConfirmationTokens(valkey),
            create_contact=CreateContact(uow_factory, catalog, ids, clock),
            list_contacts=ListContacts(uow_factory, catalog),
            rename_contact=RenameContact(uow_factory, catalog),
            set_active_contact=SetActiveContact(uow_factory, catalog),
            propose_rule=ProposeRule(uow_factory, catalog, ids, clock),
            list_rules=ListRules(uow_factory, catalog),
            archive_rule=ArchiveRule(uow_factory, catalog, clock),
            dialog_state=ValkeyDialogState(
                valkey,
                ttl_seconds=settings.dialog_ttl_seconds,
            ),
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
        lifecycle = build_telegram_lifecycle(settings, deps)
        if settings.telegram_updates_mode is TelegramUpdatesMode.WEBHOOK:
            path_secret = settings.telegram_webhook_path_secret
            secret_token = settings.telegram_webhook_secret_token
            if path_secret is None or secret_token is None:
                msg = "webhook secrets required in webhook mode"
                raise RuntimeError(msg)
            bound_lifecycle = lifecycle

            async def _feed(update: Update) -> None:
                await bound_lifecycle.dispatcher.feed_update(bound_lifecycle.bot, update)

            extra_routers = (
                build_telegram_webhook_router(
                    TelegramWebhookBindings(
                        is_accepting=lambda: bound_lifecycle.accepting,
                        path_secret=path_secret.get_secret_value(),
                        secret_token=secret_token.get_secret_value(),
                        parse_bot=bound_lifecycle.bot,
                        feed_update=_feed,
                        schedule=bound_lifecycle.schedule_update,
                    )
                ),
            )

    async def on_startup() -> None:
        if lifecycle is not None:
            await lifecycle.start()

    async def on_shutdown() -> None:
        if lifecycle is not None:
            await lifecycle.shutdown()

    async def dispose() -> None:
        await close_gigachat_client(gigachat)
        await close_client(valkey)
        await dispose_engine(engine)

    return create_app(
        check_readiness,
        settings.environment,
        AppLifecycleHooks(
            dispose=dispose,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            extra_routers=extra_routers,
        ),
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
