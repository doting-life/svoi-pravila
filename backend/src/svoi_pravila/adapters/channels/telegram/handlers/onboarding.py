"""Onboarding and command handlers."""

from __future__ import annotations

import uuid

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.types import CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.helpers import (
    actor,
    callback_chat_id,
    clear_callback_keyboard,
    dialog_pseudonym,
    is_feature_callback,
    render_current_step,
    send_current_step,
)
from svoi_pravila.adapters.channels.telegram.keyboards import invite_relationship_keyboard
from svoi_pravila.adapters.channels.telegram.localization import (
    render_crisis_message,
    render_help,
    render_refuse_manipulation,
)
from svoi_pravila.application.errors import AccessNotGranted, NotFound
from svoi_pravila.application.ports.dialog_state import DialogRecord
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
from svoi_pravila.application.use_cases.resolve_invite import ResolveInviteCommand
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind
from svoi_pravila.domain.errors import (
    InviteAlreadyAcceptedError,
    InviteExpiredError,
    SelfInviteAcceptError,
)
from svoi_pravila.domain.ids import InviteId, TelegramUserId

_INVITE_PREFIX = "inv_"
_INVITE_REL_PARTS = 4
_CONSENT_CALLBACK_PARTS = 4


def build_router() -> Router:
    """Create the private-chat router for onboarding and help."""
    router = Router(name="telegram_onboarding")
    router.message.register(start, CommandStart())
    router.message.register(help_command, Command("help"))
    router.callback_query.register(age_yes, F.data == "age:y")
    router.callback_query.register(age_no, F.data == "age:n")
    router.callback_query.register(consent_callback, F.data.startswith("cg:"))
    router.callback_query.register(invite_relationship, F.data.startswith("iv:rel:"))
    router.callback_query.register(any_callback_while_onboarding, NonFeatureCallbackFilter())
    return router


async def start(message: Message, command: CommandObject, tg_deps: TelegramDeps) -> None:
    """Handle /start, deep-link shortcuts, and invite tokens."""
    if message.from_user is None:
        return
    if command.args == "help":
        await message.answer(render_help(tg_deps.strings))
        return
    if command.args == "support":
        await message.answer(render_crisis_message())
        return
    if command.args == "why":
        await message.answer(render_refuse_manipulation(tg_deps.strings))
        return
    if command.args is not None and command.args.startswith(_INVITE_PREFIX):
        await _handle_invite_start(message, tg_deps, command.args[len(_INVITE_PREFIX) :])
        return
    await render_current_step(message, tg_deps, message.from_user.id)


async def help_command(message: Message, tg_deps: TelegramDeps) -> None:
    """Reply with the help catalog."""
    await message.answer(render_help(tg_deps.strings))


async def age_yes(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Accept age confirmation and advance onboarding."""
    await clear_callback_keyboard(bot, callback)
    await tg_deps.accept_age.execute(
        AcceptAgeConfirmationCommand(TelegramUserId(callback.from_user.id))
    )
    await callback.answer()
    await send_current_step(bot, callback, tg_deps, callback.from_user.id)


async def age_no(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Decline age confirmation."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.age_declined)


async def consent_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Grant or decline a versioned consent."""
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


async def invite_relationship(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Store awaiting_invite_label after the invitee picks a relationship."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
    )
    if step.step.kind is not OnboardingStepKind.DONE:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    parsed = _parse_invite_relationship(callback.data)
    if parsed is None:
        return
    invite_id, kind = parsed
    await tg_deps.dialog_state.set(
        dialog_pseudonym(tg_deps, callback.from_user.id),
        DialogRecord(
            step="awaiting_invite_label",
            invite_id=invite_id,
            relationship=kind,
        ),
    )
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.invite_label_prompt)


async def any_callback_while_onboarding(
    callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot
) -> None:
    """Re-show the current onboarding step for unknown non-feature callbacks."""
    await callback.answer()
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(callback.from_user.id))
    )
    if step.step.kind is not OnboardingStepKind.DONE:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)


async def _handle_invite_start(
    message: Message,
    tg_deps: TelegramDeps,
    raw_token: str,
) -> None:
    if message.from_user is None:
        return
    step = await tg_deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(TelegramUserId(message.from_user.id))
    )
    if step.step.kind is not OnboardingStepKind.DONE:
        await render_current_step(message, tg_deps, message.from_user.id)
        await message.answer(tg_deps.strings.invite_reopen_link)
        return
    user = await actor(tg_deps, message.from_user.id)
    if user is None:
        await render_current_step(message, tg_deps, message.from_user.id)
        return
    try:
        resolved = await tg_deps.resolve_invite.execute(ResolveInviteCommand(user.id, raw_token))
    except (NotFound, InviteAlreadyAcceptedError, AccessNotGranted):
        await message.answer(tg_deps.strings.invite_invalid)
        return
    except InviteExpiredError:
        await message.answer(tg_deps.strings.invite_expired)
        return
    except SelfInviteAcceptError:
        await message.answer(tg_deps.strings.invite_own)
        return
    await message.answer(
        tg_deps.strings.invite_relationship_prompt,
        reply_markup=invite_relationship_keyboard(resolved.invite_id, tg_deps.strings),
    )


class NonFeatureCallbackFilter(Filter):
    """Match callbacks outside the feature-prefix registry (``ct``, ``ru``, ``sg``)."""

    async def __call__(self, callback: CallbackQuery) -> bool:
        return not is_feature_callback(callback.data)


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


def _parse_invite_relationship(
    data: str | None,
) -> tuple[InviteId, RelationshipKind] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _INVITE_REL_PARTS or parts[0] != "iv" or parts[1] != "rel":
        return None
    try:
        invite_id = InviteId(uuid.UUID(parts[2]))
        kind = RelationshipKind(parts[3])
    except ValueError:
        return None
    return invite_id, kind
