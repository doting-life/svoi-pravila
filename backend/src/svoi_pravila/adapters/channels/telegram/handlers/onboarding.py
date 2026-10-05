"""Onboarding and command handlers."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.types import CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.helpers import (
    callback_chat_id,
    clear_callback_keyboard,
    is_feature_callback,
    render_current_step,
    send_current_step,
)
from svoi_pravila.adapters.channels.telegram.localization import (
    render_crisis_message,
    render_help,
    render_refuse_manipulation,
)
from svoi_pravila.application.use_cases.accept_age_confirmation import (
    AcceptAgeConfirmationCommand,
)
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
        await clear_callback_keyboard(bot, callback)
        await tg_deps.accept_age.execute(
            AcceptAgeConfirmationCommand(TelegramUserId(callback.from_user.id))
        )
        await callback.answer()
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)

    @router.callback_query(F.data == "age:n")
    async def age_no(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        await clear_callback_keyboard(bot, callback)
        await callback.answer()
        chat_id = callback_chat_id(callback)
        if chat_id is not None:
            await bot.send_message(chat_id, tg_deps.strings.age_declined)

    @router.callback_query(F.data.startswith("cg:"))
    async def consent_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        if callback.data is None:
            return
        parsed = _parse_consent_callback(callback.data)
        await clear_callback_keyboard(bot, callback)
        await callback.answer()
        if parsed is None:
            return
        kind, version, accepted = parsed
        chat_id = callback_chat_id(callback)
        if not accepted:
            if chat_id is not None:
                await bot.send_message(chat_id, tg_deps.strings.consent_declined)
            return

        lookup = await tg_deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(callback.from_user.id))
        )
        if lookup.user is None or lookup.user.age_confirmed_at is None:
            await send_current_step(bot, callback, tg_deps, callback.from_user.id)
            return
        await tg_deps.grant_consent.execute(GrantConsentCommand(lookup.user.id, kind, version))
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)

    @router.callback_query(NonFeatureCallbackFilter())
    async def any_callback_while_onboarding(
        callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot
    ) -> None:
        await callback.answer()
        step = await tg_deps.get_onboarding_step.execute(
            GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
        )
        if step.step.kind is not OnboardingStepKind.DONE:
            await send_current_step(bot, callback, tg_deps, callback.from_user.id)

    return router


class NonFeatureCallbackFilter(Filter):
    """Match callbacks outside the feature-prefix registry (``ct``, ``ru``, ``sg``)."""

    async def __call__(self, callback: CallbackQuery) -> bool:
        return not is_feature_callback(callback.data)


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
