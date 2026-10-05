"""Telegram adapter for PairNotifier: DM partners without revealing labels across sides."""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from svoi_pravila.adapters.channels.telegram.keyboards import require_callback_bytes
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import PairScope

logger = structlog.get_logger(__name__)


class TelegramPairNotifier:
    """Deliver pair lifecycle DMs using each recipient's own contact label / rule text."""

    def __init__(
        self,
        bot: Bot,
        uow_factory: UnitOfWorkFactory,
        strings: TelegramStrings,
    ) -> None:
        self._bot = bot
        self._uow_factory = uow_factory
        self._strings = strings

    async def invite_accepted(self, inviter_id: UserId, inviter_contact_id: ContactId) -> None:
        """Notify the inviter that the invite was accepted."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(inviter_id)
            contact = await uow.contacts.get(inviter_contact_id)
            if user is None or contact is None or contact.owner_id != inviter_id:
                return
            telegram_id = user.telegram_user_id.value
            label = contact.label.value
        text = self._strings.pair_invite_accepted.format(label=label)
        await self._send(telegram_id, text, action="invite_accepted")

    async def shared_rule_proposed(self, approver_id: UserId, rule_id: RuleId) -> None:
        """Notify the partner about a pending shared rule with approve/reject buttons."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(approver_id)
            rule = await uow.rules.get(rule_id)
            if user is None or rule is None or not isinstance(rule.scope, PairScope):
                return
            pending = rule.pending_revision
            if pending is None:
                return
            telegram_id = user.telegram_user_id.value
            rule_text = pending.text.value
        yes = require_callback_bytes(f"pr:y:{rule_id}")
        no = require_callback_bytes(f"pr:n:{rule_id}")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=self._strings.pair_rule_approve, callback_data=yes),
                    InlineKeyboardButton(text=self._strings.pair_rule_reject, callback_data=no),
                ]
            ]
        )
        text = self._strings.pair_shared_rule_proposed.format(text=rule_text)
        await self._send(telegram_id, text, reply_markup=keyboard, action="shared_rule_proposed")

    async def shared_rule_decided(
        self, author_id: UserId, rule_id: RuleId, *, approved: bool
    ) -> None:
        """Notify the author of a shared-rule decision."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(author_id)
            rule = await uow.rules.get(rule_id)
            if user is None or rule is None:
                return
            telegram_id = user.telegram_user_id.value
        template = (
            self._strings.pair_shared_rule_approved
            if approved
            else self._strings.pair_shared_rule_rejected
        )
        await self._send(telegram_id, template, action="shared_rule_decided")

    async def partner_left(self, user_id: UserId, contact_id: ContactId) -> None:
        """Notify the remaining member that the shared rulebook ended."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            contact = await uow.contacts.get(contact_id)
            if user is None or contact is None or contact.owner_id != user_id:
                return
            telegram_id = user.telegram_user_id.value
            label = contact.label.value
        text = self._strings.pair_partner_left.format(label=label)
        await self._send(telegram_id, text, action="partner_left")

    async def _send(
        self,
        telegram_id: int,
        text: str,
        *,
        action: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await self._bot.send_message(telegram_id, text, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            logger.info(
                "pair_notifier_failed",
                action=action,
                error_type=type(exc).__name__,
            )
