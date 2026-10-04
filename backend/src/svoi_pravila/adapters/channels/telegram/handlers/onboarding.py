"""Onboarding and command handlers."""

from __future__ import annotations

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.localization import (
    render_crisis_message,
    render_help,
    render_refuse_manipulation,
)
from svoi_pravila.adapters.channels.telegram.presenters import render_step
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmationCommand,
)
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocumentQuery
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramIdQuery,
)
from svoi_pravila.application.use_cases.grant_consent import (
    GrantConsentCommand,
)
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import TelegramUserId

logger = structlog.get_logger(__name__)


def build_router() -> Router:
    """Create the private-chat router for onboarding and help."""
    router = Router(name="telegram_onboarding")

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject, tg_deps: TelegramDeps) -> None:
        if message.from_user is None:
            return
        if command.args == "help":
            await message.answer(render_help(tg_deps.strings))
            return
        if command.args == "support":
            await message.answer(render_crisis_message(tg_deps.strings))
            return
        if command.args == "why":
            await message.answer(render_refuse_manipulation(tg_deps.strings))
            return
        await render_current_step(message, tg_deps, message.from_user.id)

    @router.message(Command("help"))
    async def help_command(message: Message, tg_deps: TelegramDeps) -> None:
        await message.answer(render_help(tg_deps.strings))

    @router.callback_query(F.data == "age:y")
    async def age_yes(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        await _clear_callback_keyboard(bot, callback)
        await tg_deps.accept_age.execute(
            AcceptAgeConfirmationCommand(TelegramUserId(callback.from_user.id))
        )
        await callback.answer()
        await _send_current_step(bot, callback, tg_deps, callback.from_user.id)

    @router.callback_query(F.data == "age:n")
    async def age_no(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        await _clear_callback_keyboard(bot, callback)
        await callback.answer()
        chat_id = _callback_chat_id(callback)
        if chat_id is not None:
            await bot.send_message(chat_id, tg_deps.strings.age_declined)

    @router.callback_query(F.data.startswith("cg:"))
    async def consent_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        if callback.data is None:
            return
        parsed = _parse_consent_callback(callback.data)
        await _clear_callback_keyboard(bot, callback)
        await callback.answer()
        if parsed is None:
            return
        kind, version, accepted = parsed
        chat_id = _callback_chat_id(callback)
        if not accepted:
            if chat_id is not None:
                await bot.send_message(chat_id, tg_deps.strings.consent_declined)
            return

        lookup = await tg_deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(callback.from_user.id))
        )
        if lookup.user is None or lookup.user.age_confirmed_at is None:
            await _send_current_step(bot, callback, tg_deps, callback.from_user.id)
            return
        await tg_deps.grant_consent.execute(GrantConsentCommand(lookup.user.id, kind, version))
        await _send_current_step(bot, callback, tg_deps, callback.from_user.id)

    @router.callback_query(~F.data.startswith("ct:") & ~F.data.startswith("ru:"))
    async def any_callback_while_onboarding(
        callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot
    ) -> None:
        await callback.answer()
        step = await tg_deps.get_onboarding_step.execute(
            GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
        )
        if step.step.kind is not OnboardingStepKind.DONE:
            await _send_current_step(bot, callback, tg_deps, callback.from_user.id)

    return router


async def _clear_callback_keyboard(bot: Bot, callback: CallbackQuery) -> None:
    message = callback.message
    if not isinstance(message, Message):
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=None,
        )
    except TelegramAPIError as exc:
        logger.info(
            "telegram_keyboard_clear_failed",
            exception_class=type(exc).__name__,
        )


async def _send_current_step(
    bot: Bot,
    callback: CallbackQuery,
    deps: TelegramDeps,
    telegram_user_id: int,
) -> None:
    chat_id = _callback_chat_id(callback)
    if chat_id is None:
        return
    step = await deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(telegram_user_id))
    )
    document = None
    if step.step.kind is OnboardingStepKind.CONSENT and step.step.consent_kind is not None:
        document = (
            await deps.get_consent_document.execute(GetConsentDocumentQuery(step.step.consent_kind))
        ).document
    text, keyboard = render_step(deps.strings, step.step, document)
    await bot.send_message(chat_id, text, reply_markup=keyboard)


async def render_current_step(
    message: Message,
    deps: TelegramDeps,
    telegram_user_id: int,
) -> None:
    step = await deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(telegram_user_id))
    )
    document = None
    if step.step.kind is OnboardingStepKind.CONSENT and step.step.consent_kind is not None:
        document = (
            await deps.get_consent_document.execute(GetConsentDocumentQuery(step.step.consent_kind))
        ).document
    text, keyboard = render_step(deps.strings, step.step, document)
    await message.answer(text, reply_markup=keyboard)


def _callback_chat_id(callback: CallbackQuery) -> int | None:
    message = callback.message
    if isinstance(message, Message):
        return message.chat.id
    return None


_CONSENT_CALLBACK_PARTS = 4


def _parse_consent_callback(data: str) -> tuple[ConsentKind, str, bool] | None:
    parts = data.split(":")
    if len(parts) != _CONSENT_CALLBACK_PARTS or parts[0] != "cg":
        return None
    try:
        kind = ConsentKind(parts[1])
    except ValueError:
        return None
    version = parts[2]
    if parts[3] == "y":
        return kind, version, True
    if parts[3] == "n":
        return kind, version, False
    return None
