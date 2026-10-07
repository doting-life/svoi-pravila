"""Telegram adapter for PairNotifier: C0 catalog texts and a web_app button only."""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import ContactId, RuleId, UserId

logger = structlog.get_logger(__name__)


class TelegramPairNotifier:
    """Deliver pair lifecycle DMs without rule text, labels, or callback buttons."""

    def __init__(
        self,
        bot: Bot,
        uow_factory: UnitOfWorkFactory,
        strings: TelegramStrings,
        *,
        miniapp_url: str | None,
    ) -> None:
        self._bot = bot
        self._uow_factory = uow_factory
        self._strings = strings
        self._miniapp_url = miniapp_url

    async def invite_accepted(self, inviter_id: UserId, inviter_contact_id: ContactId) -> None:
        """Notify the inviter that the invite was accepted."""
        _ = inviter_contact_id
        telegram_id = await self._telegram_id(inviter_id)
        if telegram_id is None:
            return
        await self._send(
            telegram_id,
            self._strings.pair_invite_accepted,
            action="invite_accepted",
        )

    async def shared_rule_proposed(self, approver_id: UserId, rule_id: RuleId) -> None:
        """Notify the partner about a pending shared rule (decision in the mini-app)."""
        _ = rule_id
        telegram_id = await self._telegram_id(approver_id)
        if telegram_id is None:
            return
        await self._send(
            telegram_id,
            self._strings.pair_shared_rule_proposed,
            action="shared_rule_proposed",
        )

    async def shared_rule_decided(
        self, author_id: UserId, rule_id: RuleId, *, approved: bool
    ) -> None:
        """Notify the author of a shared-rule decision."""
        _ = rule_id
        telegram_id = await self._telegram_id(author_id)
        if telegram_id is None:
            return
        text = (
            self._strings.pair_shared_rule_approved
            if approved
            else self._strings.pair_shared_rule_rejected
        )
        await self._send(telegram_id, text, action="shared_rule_decided")

    async def partner_left(self, user_id: UserId, contact_id: ContactId) -> None:
        """Notify the remaining member that the shared rulebook ended."""
        _ = contact_id
        telegram_id = await self._telegram_id(user_id)
        if telegram_id is None:
            return
        await self._send(
            telegram_id,
            self._strings.pair_partner_left,
            action="partner_left",
        )

    async def _telegram_id(self, user_id: UserId) -> int | None:
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                return None
            return user.telegram_user_id.value

    def _web_app_markup(self) -> InlineKeyboardMarkup | None:
        if self._miniapp_url is None:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self._strings.dm_open_app,
                        web_app=WebAppInfo(url=self._miniapp_url),
                    )
                ]
            ]
        )

    async def _send(self, telegram_id: int, text: str, *, action: str) -> None:
        try:
            await self._bot.send_message(
                telegram_id,
                text,
                reply_markup=self._web_app_markup(),
            )
        except TelegramAPIError as exc:
            logger.info(
                "pair_notifier_failed",
                action=action,
                error_type=type(exc).__name__,
            )
