"""Shared Telegram handler helpers. Public names only."""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.presenters import render_step
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocumentQuery
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStepQuery,
    OnboardingStep,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.user import User

logger = structlog.get_logger(__name__)

# Feature callback prefixes that must reach their routers during onboarding.
FEATURE_CALLBACK_PREFIXES: tuple[str, ...] = ("ct", "ru", "sg", "sn")


def is_feature_callback(data: str | None) -> bool:
    """True when ``data`` starts with a registered feature prefix (``ct:``, ``ru:``, …)."""
    if data is None:
        return False
    return any(data.startswith(f"{prefix}:") for prefix in FEATURE_CALLBACK_PREFIXES)


def dialog_pseudonym(tg_deps: TelegramDeps, telegram_user_id: int) -> str:
    """HMAC dialog key for a Telegram user id."""
    return tg_deps.pseudonymizer.pseudonymize(DIALOG_PSEUDONYM_PURPOSE, str(telegram_user_id))


async def actor(tg_deps: TelegramDeps, telegram_user_id: int) -> User | None:
    """Resolve the onboarded user, if any."""
    result = await tg_deps.get_user_by_telegram_id.execute(
        GetUserByTelegramIdQuery(TelegramUserId(telegram_user_id))
    )
    return result.user


def callback_chat_id(callback: CallbackQuery) -> int | None:
    """Chat id when the callback still has an accessible message."""
    message = callback.message
    if isinstance(message, Message):
        return message.chat.id
    return None


async def reply_callback(bot: Bot, callback: CallbackQuery, text: str) -> None:
    """Send a private message in response to a callback when the chat is known."""
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, text)


async def clear_callback_keyboard(bot: Bot, callback: CallbackQuery) -> None:
    """Drop the inline keyboard; ignore Bot API failures on old messages."""
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


async def onboarding_payload(
    deps: TelegramDeps, telegram_user_id: int
) -> tuple[OnboardingStep, ConsentDocument | None]:
    """Current onboarding step plus consent document when needed."""
    step = await deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(telegram_user_id))
    )
    document = None
    if step.step.kind is OnboardingStepKind.CONSENT and step.step.consent_kind is not None:
        document = (
            await deps.get_consent_document.execute(GetConsentDocumentQuery(step.step.consent_kind))
        ).document
    return step.step, document


async def send_current_step(
    bot: Bot,
    callback: CallbackQuery,
    deps: TelegramDeps,
    telegram_user_id: int,
) -> None:
    """Push the current onboarding step into the callback's chat."""
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    step, document = await onboarding_payload(deps, telegram_user_id)
    text, keyboard = render_step(deps.strings, step, document)
    await bot.send_message(chat_id, text, reply_markup=keyboard)


async def render_current_step(
    message: Message,
    deps: TelegramDeps,
    telegram_user_id: int,
) -> None:
    """Reply with the current onboarding step."""
    step, document = await onboarding_payload(deps, telegram_user_id)
    text, keyboard = render_step(deps.strings, step, document)
    await message.answer(text, reply_markup=keyboard)


async def require_done(message: Message, tg_deps: TelegramDeps, telegram_user_id: int) -> bool:
    """True when onboarding is finished; otherwise send the current step."""
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(telegram_user_id))
    )
    if step.step.kind is OnboardingStepKind.DONE:
        return True
    await render_current_step(message, tg_deps, telegram_user_id)
    return False


async def require_done_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> bool:
    """True when onboarding is finished; otherwise send the current step."""
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
    )
    if step.step.kind is OnboardingStepKind.DONE:
        return True
    await send_current_step(bot, callback, tg_deps, callback.from_user.id)
    return False
