"""Account rights: /revoke, /delete, /export."""

from __future__ import annotations

import json

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.onboarding import (
    _clear_callback_keyboard,
    _send_current_step,
)
from svoi_pravila.adapters.channels.telegram.keyboards import confirm_keyboard
from svoi_pravila.application.errors import OpenRuleLimitReached
from svoi_pravila.application.use_cases.delete_my_account import DeleteMyAccountCommand
from svoi_pravila.application.use_cases.export_my_data import ExportMyDataCommand
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsentsCommand
from svoi_pravila.domain.ids import TelegramUserId

_RATE_LIMIT_PURPOSE = "rate_limit"
_ACTION_REVOKE = "rv"
_ACTION_DELETE = "dl"
_CONFIRM_PARTS = 3
_TOKEN_HEX_LEN = 32
_HEX_ALPHABET = "0123456789abcdef"


def build_rights_router() -> Router:
    """Commands and confirmation callbacks for user rights."""
    router = Router(name="telegram_rights")

    @router.message(Command("revoke"))
    async def revoke_command(message: Message, tg_deps: TelegramDeps) -> None:
        if message.from_user is None:
            return
        await _prompt_confirm(
            message, tg_deps, _ACTION_REVOKE, tg_deps.strings.rights_revoke_explain
        )

    @router.message(Command("delete"))
    async def delete_command(message: Message, tg_deps: TelegramDeps) -> None:
        if message.from_user is None:
            return
        await _prompt_confirm(
            message, tg_deps, _ACTION_DELETE, tg_deps.strings.rights_delete_explain
        )

    @router.message(Command("export"))
    async def export_command(message: Message, tg_deps: TelegramDeps, bot: Bot) -> None:
        if message.from_user is None:
            return
        result = await tg_deps.export_my_data.execute(
            ExportMyDataCommand(TelegramUserId(message.from_user.id))
        )
        if not result.found or result.payload is None:
            await message.answer(tg_deps.strings.rights_export_empty)
            return
        body = json.dumps(result.payload, ensure_ascii=False, indent=2)
        stamp = tg_deps.clock.now().strftime("%Y%m%d")
        document = BufferedInputFile(
            body.encode("utf-8"),
            filename=f"svoi-pravila-export-{stamp}.json",
        )
        await bot.send_document(
            chat_id=message.chat.id,
            document=document,
            caption=tg_deps.strings.rights_export_caption,
        )

    @router.callback_query(F.data.startswith("cf:"))
    async def confirm_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        await _clear_callback_keyboard(bot, callback)
        await callback.answer()
        parsed = _parse_confirm(callback.data)
        chat_id = _chat_id(callback)
        if parsed is None:
            if chat_id is not None:
                await bot.send_message(chat_id, tg_deps.strings.rights_confirm_rejected)
            return
        action, token = parsed
        pseudonym = tg_deps.pseudonymizer.pseudonymize(
            _RATE_LIMIT_PURPOSE, str(callback.from_user.id)
        )
        ok = await tg_deps.confirmation_tokens.consume(
            pseudonym=pseudonym, action=action, token=token
        )
        if not ok:
            if chat_id is not None:
                await bot.send_message(chat_id, tg_deps.strings.rights_confirm_rejected)
            return
        telegram_id = TelegramUserId(callback.from_user.id)
        if action == _ACTION_REVOKE:
            await tg_deps.revoke_all_consents.execute(RevokeAllConsentsCommand(telegram_id))
            await _send_current_step(bot, callback, tg_deps, callback.from_user.id)
            return
        if action == _ACTION_DELETE:
            try:
                await tg_deps.delete_my_account.execute(DeleteMyAccountCommand(telegram_id))
            except OpenRuleLimitReached:
                if chat_id is not None:
                    await bot.send_message(chat_id, tg_deps.strings.error_generic)
                return
            if chat_id is not None:
                await bot.send_message(chat_id, tg_deps.strings.rights_deleted)

    @router.callback_query(F.data.startswith("cx:"))
    async def cancel_callback(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        await _clear_callback_keyboard(bot, callback)
        await callback.answer()
        chat_id = _chat_id(callback)
        if chat_id is not None:
            await bot.send_message(chat_id, tg_deps.strings.rights_cancelled)

    return router


async def _prompt_confirm(
    message: Message,
    deps: TelegramDeps,
    action: str,
    explanation: str,
) -> None:
    if message.from_user is None:
        return
    pseudonym = deps.pseudonymizer.pseudonymize(_RATE_LIMIT_PURPOSE, str(message.from_user.id))
    token = await deps.confirmation_tokens.issue(pseudonym=pseudonym, action=action)
    await message.answer(
        explanation,
        reply_markup=confirm_keyboard(deps.strings, action=action, token=token),
    )


def _parse_confirm(data: str | None) -> tuple[str, str] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CONFIRM_PARTS or parts[0] != "cf":
        return None
    action, token = parts[1], parts[2]
    if action not in {_ACTION_REVOKE, _ACTION_DELETE}:
        return None
    if len(token) != _TOKEN_HEX_LEN or any(ch not in _HEX_ALPHABET for ch in token):
        return None
    return action, token


def _chat_id(callback: CallbackQuery) -> int | None:
    message = callback.message
    if isinstance(message, Message):
        return message.chat.id
    return None
