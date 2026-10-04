"""Telegram bot lifecycle: polling/webhook startup and graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.config import TelegramUpdatesMode

logger = structlog.get_logger(__name__)

ALLOWED_UPDATES = ("message", "callback_query")


@dataclass(frozen=True, slots=True)
class TelegramRuntimeConfig:
    """Mode-specific lifecycle settings bundled to keep the constructor small."""

    mode: TelegramUpdatesMode
    strings: TelegramStrings
    webhook_url: str | None
    webhook_secret_token: str | None
    shutdown_grace_seconds: float


class TelegramLifecycle:
    """Own bot session, update tasks, and mode-specific start/stop."""

    def __init__(
        self,
        *,
        bot: Bot,
        dispatcher: Dispatcher,
        config: TelegramRuntimeConfig,
    ) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._mode = config.mode
        self._strings = config.strings
        self._webhook_url = config.webhook_url
        self._webhook_secret_token = config.webhook_secret_token
        self._shutdown_grace_seconds = config.shutdown_grace_seconds
        self._polling_task: asyncio.Task[None] | None = None
        self._update_tasks: set[asyncio.Task[None]] = set()
        self._accepting = True

    @property
    def accepting(self) -> bool:
        """Whether webhook updates are still accepted."""
        return self._accepting

    def stop_accepting(self) -> None:
        """Reject further webhook updates (used on shutdown before drain)."""
        self._accepting = False

    @property
    def bot(self) -> Bot:
        """Configured bot instance."""
        return self._bot

    @property
    def dispatcher(self) -> Dispatcher:
        """Configured dispatcher."""
        return self._dispatcher

    async def start(self) -> None:
        """Install commands and start polling or set the webhook."""
        await self._bot.set_my_commands(
            [
                BotCommand(command="start", description=self._strings.commands_start),
                BotCommand(command="help", description=self._strings.commands_help),
                BotCommand(command="export", description=self._strings.commands_export),
                BotCommand(command="revoke", description=self._strings.commands_revoke),
                BotCommand(command="delete", description=self._strings.commands_delete),
            ]
        )
        if self._mode is TelegramUpdatesMode.POLLING:
            await self._bot.delete_webhook(drop_pending_updates=False)
            self._polling_task = asyncio.create_task(
                self._dispatcher.start_polling(
                    self._bot,
                    handle_signals=False,
                    close_bot_session=False,
                    allowed_updates=list(ALLOWED_UPDATES),
                ),
                name="telegram-polling",
            )
            return
        if self._mode is TelegramUpdatesMode.WEBHOOK:
            if self._webhook_url is None or self._webhook_secret_token is None:
                msg = "webhook url and secret token are required"
                raise RuntimeError(msg)
            await self._bot.set_webhook(
                url=self._webhook_url,
                secret_token=self._webhook_secret_token,
                allowed_updates=list(ALLOWED_UPDATES),
            )

    def schedule_update(self, coro: Coroutine[Any, Any, None]) -> None:
        """Track a background update task for webhook mode."""
        task: asyncio.Task[None] = asyncio.create_task(coro, name="telegram-update")
        self._update_tasks.add(task)
        task.add_done_callback(self._update_tasks.discard)

    async def shutdown(self) -> None:
        """Stop accepting updates, drain or cancel in-flight work, close the session."""
        self.stop_accepting()
        if self._mode is TelegramUpdatesMode.POLLING and self._polling_task is not None:
            try:
                await self._dispatcher.stop_polling()
            except RuntimeError:
                self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await self._polling_task
            self._polling_task = None

        pending = list(self._update_tasks)
        if pending:
            done, still_pending = await asyncio.wait(
                pending,
                timeout=self._shutdown_grace_seconds,
            )
            cancelled = 0
            for task in still_pending:
                task.cancel()
                cancelled += 1
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)
            if cancelled:
                logger.info("telegram_shutdown_cancelled_tasks", count=cancelled)
            _ = done
        await self._bot.session.close()
