"""Private-chat rules: list, add, archive for the active contact."""

from __future__ import annotations

import uuid

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.helpers import (
    actor,
    callback_chat_id,
    clear_callback_keyboard,
    dialog_pseudonym,
    render_current_step,
    reply_callback,
    require_done,
    require_done_callback,
    send_current_step,
)
from svoi_pravila.adapters.channels.telegram.keyboards import (
    archive_rule_confirm_keyboard,
    rule_category_keyboard,
    suggestion_decision_keyboard,
)
from svoi_pravila.adapters.channels.telegram.presenters import (
    display_rule_text,
    render_rules_list,
)
from svoi_pravila.application.errors import AccessNotGranted, NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.dialog_state import DialogRecord
from svoi_pravila.application.rule_source import RULE_SOURCE_CALLBACK_PREFIX
from svoi_pravila.application.use_cases.accept_suggestion import (
    AcceptSuggestionCommand,
    AcceptSuggestionOutcome,
)
from svoi_pravila.application.use_cases.archive_rule import ArchiveRuleCommand
from svoi_pravila.application.use_cases.dismiss_suggestion import (
    DismissSuggestionCommand,
    DismissSuggestionOutcome,
)
from svoi_pravila.application.use_cases.list_rules import ListRulesCommand
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestionsCommand
from svoi_pravila.application.use_cases.propose_rule import ProposeRuleCommand
from svoi_pravila.application.use_cases.suggest_rule_from_decode import (
    SuggestRuleFromDecodeCommand,
    SuggestRuleFromDecodeOutcome,
    SuggestRuleFromDecodeResult,
)
from svoi_pravila.domain.enums import RuleCategory, UsageSurface
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import RuleId, RuleSuggestionId, TelegramUserId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.rules import Rule
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
        record = await tg_deps.dialog_state.get(dialog_pseudonym(tg_deps, message.from_user.id))
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
    router.callback_query.register(accept_suggestion, F.data.startswith("sg:a:"))
    router.callback_query.register(edit_suggestion, F.data.startswith("sg:e:"))
    router.callback_query.register(dismiss_suggestion, F.data.startswith("sg:d:"))
    router.callback_query.register(
        suggest_from_decode,
        F.data.startswith(RULE_SOURCE_CALLBACK_PREFIX),
    )
    router.message.register(dialog_text, AwaitingRuleText())
    return router


async def rules_command(message: Message, tg_deps: TelegramDeps) -> None:
    """List rules for the active contact."""
    if message.from_user is None:
        return
    if not await require_done(message, tg_deps, message.from_user.id):
        return
    await _send_rules_list(message, tg_deps, message.from_user.id)


async def add_rule(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Offer category buttons for a new private rule."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
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
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    kind = _parse_category(callback.data)
    if kind is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    if user.active_contact_id is None:
        await reply_callback(bot, callback, tg_deps.strings.rules_no_active_contact)
        return
    await tg_deps.dialog_state.set(
        dialog_pseudonym(tg_deps, callback.from_user.id),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=user.active_contact_id,
            category=kind,
        ),
    )
    logger.info("telegram_dialog_set", step="awaiting_rule_text", category=kind.value)
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.rules_text_prompt)


async def ask_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Ask for archive confirmation with the rule text loaded by id."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    rule_id = _parse_rule_id(callback.data, "ar")
    if rule_id is None:
        return
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    match = await _rule_for_archive_confirm(callback, tg_deps, bot, chat_id, rule_id)
    if match is None:
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.rules_archive_confirm.format(text=display_rule_text(match)),
        reply_markup=archive_rule_confirm_keyboard(tg_deps.strings, rule_id),
    )


async def confirm_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Archive through the use case after confirmation."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    rule_id = _parse_rule_id(callback.data, "ay")
    if rule_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        await tg_deps.archive_rule.execute(ArchiveRuleCommand(user.id, rule_id))
    except InvalidTransitionError:
        await reply_callback(bot, callback, tg_deps.strings.rules_already_archived)
        await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)
        return
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    logger.info("telegram_rule_archived")
    await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def cancel_archive(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Drop the confirm keyboard and refresh the list."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def dialog_text(message: Message, tg_deps: TelegramDeps) -> None:
    """Consume the next private text as a rule body, never as decode."""
    if message.from_user is None or message.text is None:
        return
    telegram_user_id = message.from_user.id
    record = await tg_deps.dialog_state.get(dialog_pseudonym(tg_deps, telegram_user_id))
    if record is None or record.step != "awaiting_rule_text":
        return
    if not await require_done(message, tg_deps, telegram_user_id):
        return
    user = await actor(tg_deps, telegram_user_id)
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
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
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
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.rules_limit)
        return
    except (NotFound, AccessNotGranted):
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.rules_contact_unavailable)
        return
    await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
    await _send_rules_list(message, tg_deps, telegram_user_id)


async def _rule_for_archive_confirm(
    callback: CallbackQuery,
    tg_deps: TelegramDeps,
    bot: Bot,
    chat_id: int,
    rule_id: RuleId,
) -> Rule | None:
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return None
    if user.active_contact_id is None:
        await bot.send_message(chat_id, tg_deps.strings.rules_no_active_contact)
        return None
    try:
        listed = await tg_deps.list_rules.execute(ListRulesCommand(user.id, user.active_contact_id))
    except (NotFound, AccessNotGranted):
        await bot.send_message(chat_id, tg_deps.strings.error_generic)
        return None
    match = next((rule for rule in listed.rules if rule.id == rule_id), None)
    if match is None:
        await bot.send_message(chat_id, tg_deps.strings.error_generic)
        return None
    return match


async def accept_suggestion(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Accept a pending suggestion and refresh the rules list."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    suggestion_id = _parse_suggestion_id(callback.data, "a")
    if suggestion_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        result = await tg_deps.accept_suggestion.execute(
            AcceptSuggestionCommand(user.id, suggestion_id)
        )
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    if result.outcome is AcceptSuggestionOutcome.ALREADY_DECIDED:
        await reply_callback(bot, callback, tg_deps.strings.suggestion_already_decided)
        return
    if result.outcome is AcceptSuggestionOutcome.OPEN_RULE_LIMIT:
        await reply_callback(bot, callback, tg_deps.strings.rules_limit)
        return
    await _send_rules_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def dismiss_suggestion(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Dismiss a pending suggestion with a short confirmation."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    suggestion_id = _parse_suggestion_id(callback.data, "d")
    if suggestion_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        result = await tg_deps.dismiss_suggestion.execute(
            DismissSuggestionCommand(user.id, suggestion_id)
        )
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    if result.outcome is DismissSuggestionOutcome.ALREADY_DECIDED:
        await reply_callback(bot, callback, tg_deps.strings.suggestion_already_decided)
        return
    await reply_callback(bot, callback, tg_deps.strings.suggestion_dismissed)


async def edit_suggestion(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Dismiss a suggestion and start awaiting_rule_text with its category."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    suggestion_id = _parse_suggestion_id(callback.data, "e")
    if suggestion_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        result = await tg_deps.dismiss_suggestion.execute(
            DismissSuggestionCommand(user.id, suggestion_id)
        )
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    if result.outcome is DismissSuggestionOutcome.ALREADY_DECIDED:
        await reply_callback(bot, callback, tg_deps.strings.suggestion_already_decided)
        return
    suggestion = result.suggestion
    if suggestion is None:
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    await tg_deps.dialog_state.set(
        dialog_pseudonym(tg_deps, callback.from_user.id),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=suggestion.contact_id,
            category=suggestion.category,
        ),
    )
    await reply_callback(bot, callback, tg_deps.strings.suggestion_decode_edit_prompt)


async def suggest_from_decode(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Redeem sn: token and show a decode-sourced suggestion outcome."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    data = callback.data or ""
    if not data.startswith(RULE_SOURCE_CALLBACK_PREFIX):
        return
    token = data[len(RULE_SOURCE_CALLBACK_PREFIX) :]
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    await bot.send_chat_action(chat_id, action="typing")
    try:
        result = await tg_deps.suggest_rule_from_decode.execute(
            SuggestRuleFromDecodeCommand(
                telegram_user_id=TelegramUserId(callback.from_user.id),
                token=token,
                surface=UsageSurface.DM,
            )
        )
    except (NotFound, AccessNotGranted):
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    await _reply_suggest_from_decode(bot, callback, tg_deps, chat_id, result)


async def _reply_suggest_from_decode(
    bot: Bot,
    callback: CallbackQuery,
    tg_deps: TelegramDeps,
    chat_id: int,
    result: SuggestRuleFromDecodeResult,
) -> None:
    simple = {
        SuggestRuleFromDecodeOutcome.UNAVAILABLE: tg_deps.strings.suggestion_decode_expired,
        SuggestRuleFromDecodeOutcome.CRISIS: tg_deps.strings.suggestion_decode_crisis,
        SuggestRuleFromDecodeOutcome.QUOTA_EXCEEDED: tg_deps.strings.suggestion_decode_quota,
        SuggestRuleFromDecodeOutcome.NONE: tg_deps.strings.suggestion_decode_none,
    }
    text = simple.get(result.outcome)
    if text is not None:
        await reply_callback(bot, callback, text)
        return
    suggestion = result.suggestion
    if suggestion is None:
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.suggestion_decode_ok.format(text=suggestion.text.value),
        reply_markup=suggestion_decision_keyboard(
            tg_deps.strings, suggestion.id, include_edit=True
        ),
    )


async def _pending_suggestions(tg_deps: TelegramDeps, user: User) -> tuple[RuleSuggestion, ...]:
    if user.active_contact_id is None:
        return ()
    listed = await tg_deps.list_suggestions.execute(
        ListSuggestionsCommand(user.id, user.active_contact_id)
    )
    return listed.suggestions


async def _send_rules_list(message: Message, tg_deps: TelegramDeps, telegram_user_id: int) -> None:
    user = await actor(tg_deps, telegram_user_id)
    if user is None:
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    if user.active_contact_id is None:
        await message.answer(tg_deps.strings.rules_no_active_contact)
        return
    listed = await tg_deps.list_rules.execute(ListRulesCommand(user.id, user.active_contact_id))
    suggestions = await _pending_suggestions(tg_deps, user)
    chunks, keyboard = render_rules_list(
        tg_deps.strings,
        listed.rules,
        suggestions=suggestions,
        now=tg_deps.clock.now(),
        tz=tg_deps.display_timezone,
    )
    await _answer_chunks(message, chunks, keyboard)


async def _send_rules_list_callback(
    callback: CallbackQuery,
    tg_deps: TelegramDeps,
    bot: Bot,
    telegram_user_id: int,
) -> None:
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    user = await actor(tg_deps, telegram_user_id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    if user.active_contact_id is None:
        await bot.send_message(chat_id, tg_deps.strings.rules_no_active_contact)
        return
    listed = await tg_deps.list_rules.execute(ListRulesCommand(user.id, user.active_contact_id))
    suggestions = await _pending_suggestions(tg_deps, user)
    chunks, keyboard = render_rules_list(
        tg_deps.strings,
        listed.rules,
        suggestions=suggestions,
        now=tg_deps.clock.now(),
        tz=tg_deps.display_timezone,
    )
    for text in chunks[:-1]:
        await bot.send_message(chat_id, text)
    await bot.send_message(chat_id, chunks[-1], reply_markup=keyboard)


async def _answer_chunks(
    message: Message, chunks: tuple[str, ...], keyboard: InlineKeyboardMarkup
) -> None:
    for text in chunks[:-1]:
        await message.answer(text)
    await message.answer(chunks[-1], reply_markup=keyboard)


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


def _parse_suggestion_id(data: str | None, action: str) -> RuleSuggestionId | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "sg" or parts[1] != action:
        return None
    try:
        return RuleSuggestionId(uuid.UUID(parts[2]))
    except ValueError:
        return None
