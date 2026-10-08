"""Build Bot, Dispatcher, and lifecycle for the Telegram channel."""

from __future__ import annotations

from aiogram import Bot, Dispatcher

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.errors import telegram_error_handler
from svoi_pravila.adapters.channels.telegram.handlers.inline import build_inline_router
from svoi_pravila.adapters.channels.telegram.handlers.membership import build_membership_router
from svoi_pravila.adapters.channels.telegram.handlers.welcome import build_welcome_router
from svoi_pravila.adapters.channels.telegram.lifecycle import (
    ExtraTasks,
    TelegramLifecycle,
    TelegramRuntimeConfig,
)
from svoi_pravila.adapters.channels.telegram.middlewares.dedup import DedupMiddleware
from svoi_pravila.adapters.channels.telegram.middlewares.metrics import TelegramMetricsMiddleware
from svoi_pravila.adapters.channels.telegram.middlewares.private_chat import (
    PrivateChatMiddleware,
)
from svoi_pravila.adapters.channels.telegram.middlewares.rate_limit import RateLimitMiddleware
from svoi_pravila.config import Settings, TelegramUpdatesMode


def build_telegram_lifecycle(
    settings: Settings,
    deps: TelegramDeps,
    *,
    bot: Bot | None = None,
    extra_tasks: ExtraTasks | None = None,
) -> TelegramLifecycle:
    """Wire the dispatcher, middlewares, handlers, and lifecycle controller."""
    if settings.telegram_updates_mode is TelegramUpdatesMode.DISABLED:
        msg = "build_telegram_lifecycle must not be called when updates mode is disabled"
        raise RuntimeError(msg)
    if settings.telegram_bot_token is None:
        msg = "telegram_bot_token is required"
        raise RuntimeError(msg)

    bot_instance = bot or Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher["tg_deps"] = deps
    dispatcher.update.outer_middleware(TelegramMetricsMiddleware())
    dispatcher.update.outer_middleware(PrivateChatMiddleware())
    dispatcher.update.outer_middleware(DedupMiddleware())
    dispatcher.update.outer_middleware(RateLimitMiddleware())
    dispatcher.include_router(build_membership_router())
    dispatcher.include_router(build_welcome_router())
    dispatcher.include_router(build_inline_router())
    dispatcher.errors.register(telegram_error_handler)

    webhook_url = None
    webhook_secret = None
    if settings.telegram_updates_mode is TelegramUpdatesMode.WEBHOOK:
        if (
            settings.telegram_webhook_base_url is None
            or settings.telegram_webhook_path_secret is None
            or settings.telegram_webhook_secret_token is None
        ):
            msg = "webhook settings incomplete"
            raise RuntimeError(msg)
        base = settings.telegram_webhook_base_url.rstrip("/")
        path_secret = settings.telegram_webhook_path_secret.get_secret_value()
        webhook_url = f"{base}/tg/{path_secret}"
        webhook_secret = settings.telegram_webhook_secret_token.get_secret_value()

    if settings.miniapp_url is None:
        msg = "miniapp_url is required for Telegram lifecycle"
        raise RuntimeError(msg)
    return TelegramLifecycle(
        bot=bot_instance,
        dispatcher=dispatcher,
        config=TelegramRuntimeConfig(
            mode=settings.telegram_updates_mode,
            strings=deps.strings,
            webhook_url=webhook_url,
            webhook_secret_token=webhook_secret,
            shutdown_grace_seconds=settings.telegram_shutdown_grace_seconds,
            inline_queries=deps.inline_queries,
            bot_username=deps.bot_username,
            miniapp_url=settings.miniapp_url,
            extra_tasks=extra_tasks,
        ),
    )
