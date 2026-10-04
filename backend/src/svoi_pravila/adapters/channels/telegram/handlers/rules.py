"""Private-chat rules: list, add, archive for the active contact."""

from __future__ import annotations

import uuid

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.onboarding import (
    _clear_callback_keyboard,
    _send_current_step,
    render_current_step,
)
from svoi_pravila.adapters.channels.telegram.keyboards import (
    archive_rule_confirm_keyboard,
    rule_category_keyboard,
)
from svoi_pravila.adapters.channels.telegram.presenters import render_rules_list
from svoi_pravila.application.errors import AccessNotGranted, NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE, DialogRecord
from svoi_pravila.application.use_cases.archive_rule import ArchiveRuleCommand
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.application.use_cases.list_rules import ListRulesCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRuleCommand
from svoi_pravila.domain.enums import RuleCategory
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import RuleId, TelegramUserId
from svoi_pravila.domain.text import RuleText
from svoi_pravila.domain.user import User

_CALLBACK_PARTS = 3
logger = structlog.get_logger(__name__)


class AwaitingRuleText(Filter):
    """Match private text only while a rule dialog is waiting for body text."""

    async def __call__(self, message: Message, tg_deps: TelegramDeps) -> bool:
        if message.from_user is None or message.text is None:
            return False
        if message.text.startswith("/"):
            return False
        record = await tg_deps.dialog_state.get(_dialog_pseudonym(tg_deps, message.from_user.id))
        return record is not None and record.step == "awaiting_rule_text"


def build_rules_router() -> Router:
    """Create the private-chat router for rules after contacts."""
    router = Router(name="telegram_rules")
    router.message.register(rules_command, Command("rules"))
    router.callback_query.register(add_rule, F.data == "ru:n")
    router.callback_query.register(choose_category, F.data.startswith("ru:cat:"))
    router.callback_query.register(ask_archive, F.data.startswith("ru:ar:"))
    router.callback_query.register(confirm_archive, F.data.startswith("ru:ay:"))
    router.callback_query.register(cancel_archive, F.data == "ru:ax")
    router.message.register(dialog_text, AwaitingRuleText())
    return router


async def rules_command(message: Message, tg_deps: TelegramDeps) -> None:
    """List rules for the active contact."""
    if message.from_user is None:
        return
    if not await _require_done(message, tg_deps, message.from_user.id):
        return
    await _send_rules_list(message, tg_deps, message.from_user.id)


async def add_rule(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Offer category buttons for a new private rule."""
    await _clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await _require_done_callback(callback, tg_deps, bot):
        return
    chat_id = _callback_chat_id(callback)
    if chat_id is None:
        return
    user = await _actor(tg_deps, callback.from_user.id)
    if user is None:
        await _send_onboarding_from_callback(callback, tg_deps, bot)
        return
    if user.active_contact_id is None:
        await bot.send_message(chat_id, tg_deps.strings.rules_no_active_contact)
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.rules_header,
        reply_markup=rule_category_keyboard(tg_deps.strings),
    )


async def choose_category(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Store awaiting_rule_text with the active contact and category."""
    await _clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await _require_done_callback(callback, tg_deps, bot):
        return
    kind = _parse_category(callback.data)
    if kind is None:
        return
    user = await _actor(tg_deps, callback.from_user.id)
    if user is None:
        await _send_onboarding_from_callback(callback, tg_deps, bot)
        return
    if user.active_contact_id is None:
        await _reply_callback(bot, callback, tg_deps.strings.rules_no_active_contact)
        return
    await tg_deps.dialog_state.set(
        _dialog_pseudonym(tg_deps, callback.from_user.id),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=user.active_contact_id,
            category=kind,
        ),
    )
    logger.info("telegram_dialog_set", step="awaiting_rule_text", category=kind.value)
    chat_id = _callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.rules_text_prompt)


async def ask_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Ask for archive confirmation."""
    await _clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await _require_done_callback(callback, tg_deps, bot):
        return
    rule_id = _parse_rule_id(callback.data, "ar")
    if rule_id is None:
        return
    chat_id = _callback_chat_id(callback)
    if chat_id is None:
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.rules_archive_confirm,
        reply_markup=archive_rule_confirm_keyboard(tg_deps.strings, rule_id),
    )


async def confirm_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Archive through the use case after confirmation."""
    await _clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await _require_done_callback(callback, tg_deps, bot):
        return
    rule_id = _parse_rule_id(callback.data, "ay")
    if rule_id is None:
        return
    user = await _actor(tg_deps, callback.from_user.id)
    if user is None:
        await _send_onboarding_from_callback(callback, tg_deps, bot)
        return
    try:
        await tg_deps.archive_rule.execute(ArchiveRuleCommand(user.id, rule_id))
    except (NotFound, AccessNotGranted):
        await _reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    logger.info("telegram_rule_archived")
    await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def cancel_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Drop the confirm keyboard and refresh the list."""
    await _clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await _require_done_callback(callback, tg_deps, bot):
        return
    await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def dialog_text(message: Message, tg_deps: TelegramDeps) -> None:
    """Consume the next private text as a rule body, never as decode."""
    if message.from_user is None or message.text is None:
        return
    telegram_user_id = message.from_user.id
    record = await tg_deps.dialog_state.get(_dialog_pseudonym(tg_deps, telegram_user_id))
    if record is None or record.step != "awaiting_rule_text":
        return
    if not await _require_done(message, tg_deps, telegram_user_id):
        return
    user = await _actor(tg_deps, telegram_user_id)
    if user is None:
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    await _finish_rule_text(message, tg_deps, user, record, telegram_user_id)


async def _finish_rule_text(
    message: Message,
    tg_deps: TelegramDeps,
    user: User,
    record: DialogRecord,
    telegram_user_id: int,
) -> None:
    if record.contact_id is None or record.category is None:
        await tg_deps.dialog_state.clear(_dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.error_generic)
        return
    try:
        body = RuleText(message.text or "")
    except InvalidValueError:
        await message.answer(tg_deps.strings.rules_invalid_text)
        return
    try:
        await tg_deps.propose_rule.execute(
            ProposeRuleCommand(
                user.id,
                record.contact_id,
                record.category,
                body,
                shared=False,
            )
        )
    except OpenRuleLimitReached:
        await message.answer(tg_deps.strings.rules_limit)
        return
    except (NotFound, AccessNotGranted):
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    await tg_deps.dialog_state.clear(_dialog_pseudonym(tg_deps, telegram_user_id))
    await _send_rules_list(message, tg_deps, telegram_user_id)


def _dialog_pseudonym(tg_deps: TelegramDeps, telegram_user_id: int) -> str:
    return tg_deps.pseudonymizer.pseudonymize(DIALOG_PSEUDONYM_PURPOSE, str(telegram_user_id))


async def _actor(tg_deps: TelegramDeps, telegram_user_id: int) -> User | None:
    result = await tg_deps.get_user_by_telegram_id.execute(
        GetUserByTelegramIdQuery(TelegramUserId(telegram_user_id))
    )
    return result.user


async def _require_done(message: Message, tg_deps: TelegramDeps, telegram_user_id: int) -> bool:
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(telegram_user_id))
    )
    if step.step.kind is OnboardingStepKind.DONE:
        return True
    await render_current_step(message, tg_deps, telegram_user_id)
    return False


async def _require_done_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> bool:
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
    )
    if step.step.kind is OnboardingStepKind.DONE:
        return True
    await _send_onboarding_from_callback(callback, tg_deps, bot)
    return False


async def _send_onboarding_from_callback(
    callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot
) -> None:
    await _send_current_step(bot, callback, tg_deps, callback.from_user.id)


async def _send_rules_list(message: Message, tg_deps: TelegramDeps, telegram_user_id: int) -> None:
    user = await _actor(tg_deps, telegram_user_id)
    if user is None:
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    if user.active_contact_id is None:
        await message.answer(tg_deps.strings.rules_no_active_contact)
        return
    listed = await tg_deps.list_rules.execute(ListRulesCommand(user.id, user.active_contact_id))
    text, keyboard = render_rules_list(
        tg_deps.strings,
        listed.rules,
        now=tg_deps.clock.now(),
        tz=tg_deps.display_timezone,
    )
    await message.answer(text, reply_markup=keyboard)


async def _send_rules_list_callback(
    callback: CallbackQuery,
    tg_deps: TelegramDeps,
    bot: Bot,
    telegram_user_id: int,
) -> None:
    chat_id = _callback_chat_id(callback)
    if chat_id is None:
        return
    user = await _actor(tg_deps, telegram_user_id)
    if user is None:
        await _send_onboarding_from_callback(callback, tg_deps, bot)
        return
    if user.active_contact_id is None:
        await bot.send_message(chat_id, tg_deps.strings.rules_no_active_contact)
        return
    listed = await tg_deps.list_rules.execute(ListRulesCommand(user.id, user.active_contact_id))
    text, keyboard = render_rules_list(
        tg_deps.strings,
        listed.rules,
        now=tg_deps.clock.now(),
        tz=tg_deps.display_timezone,
    )
    await bot.send_message(chat_id, text, reply_markup=keyboard)


async def _reply_callback(bot: Bot, callback: CallbackQuery, text: str) -> None:
    chat_id = _callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, text)


def _callback_chat_id(callback: CallbackQuery) -> int | None:
    message = callback.message
    if isinstance(message, Message):
        return message.chat.id
    return None


def _parse_category(data: str | None) -> RuleCategory | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "ru" or parts[1] != "cat":
        return None
    try:
        return RuleCategory(parts[2])
    except ValueError:
        return None


def _parse_rule_id(data: str | None, action: str) -> RuleId | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "ru" or parts[1] != action:
        return None
    try:
        return RuleId(uuid.UUID(parts[2]))
    except ValueError:
        return None
