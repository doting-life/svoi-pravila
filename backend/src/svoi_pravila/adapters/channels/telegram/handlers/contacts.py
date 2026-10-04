"""Private-chat contacts: list, add, rename, set active."""

from __future__ import annotations

import uuid

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message

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
from svoi_pravila.adapters.channels.telegram.keyboards import relationship_keyboard
from svoi_pravila.adapters.channels.telegram.presenters import render_contacts_list
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ContactLimitReached,
    NotFound,
)
from svoi_pravila.application.ports.dialog_state import DialogRecord
from svoi_pravila.application.use_cases.create_contact import CreateContactCommand
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand
from svoi_pravila.application.use_cases.rename_contact import RenameContactCommand
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContactCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.domain.user import User

_CALLBACK_PARTS = 3
logger = structlog.get_logger(__name__)


class AwaitingDialogText(Filter):
    """Match private text only while a dialog step is waiting for a label."""

    async def __call__(self, message: Message, tg_deps: TelegramDeps) -> bool:
        if message.from_user is None or message.text is None:
            return False
        if message.text.startswith("/"):
            return False
        record = await tg_deps.dialog_state.get(dialog_pseudonym(tg_deps, message.from_user.id))
        return record is not None and record.step in {"awaiting_label", "awaiting_rename"}


def build_contacts_router() -> Router:
    """Create the private-chat router for contacts after onboarding."""
    router = Router(name="telegram_contacts")
    router.message.register(contacts_command, Command("contacts"))
    router.message.register(cancel_command, Command("cancel"))
    router.callback_query.register(add_contact, F.data == "ct:n")
    router.callback_query.register(choose_relationship, F.data.startswith("ct:rel:"))
    router.callback_query.register(set_active, F.data.startswith("ct:a:"))
    router.callback_query.register(start_rename, F.data.startswith("ct:r:"))
    router.message.register(dialog_text, AwaitingDialogText())
    return router


async def contacts_command(message: Message, tg_deps: TelegramDeps) -> None:
    """List contacts for an onboarded user."""
    if message.from_user is None:
        return
    if not await require_done(message, tg_deps, message.from_user.id):
        return
    await _send_contacts_list(message, tg_deps, message.from_user.id)


async def cancel_command(message: Message, tg_deps: TelegramDeps) -> None:
    """Confirm that the dialog was cleared."""
    await message.answer(tg_deps.strings.contacts_cancelled)


async def add_contact(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Offer relationship buttons for a new contact."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.contacts_header,
        reply_markup=relationship_keyboard(tg_deps.strings),
    )


async def choose_relationship(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Store awaiting_label with the chosen relationship."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    kind = _parse_relationship(callback.data)
    if kind is None:
        return
    pseudonym = dialog_pseudonym(tg_deps, callback.from_user.id)
    await tg_deps.dialog_state.set(
        pseudonym, DialogRecord(step="awaiting_label", relationship=kind)
    )
    logger.info("telegram_dialog_set", step="awaiting_label", relationship=kind.value)
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.contacts_label_prompt)


async def set_active(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Set the active contact through the use case."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    contact_id = _parse_contact_id(callback.data)
    if contact_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        await tg_deps.set_active_contact.execute(SetActiveContactCommand(user.id, contact_id))
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    logger.info("telegram_contact_active_set")
    await _send_contacts_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def start_rename(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Store awaiting_rename for the chosen contact id."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    contact_id = _parse_contact_id(callback.data)
    if contact_id is None:
        return
    pseudonym = dialog_pseudonym(tg_deps, callback.from_user.id)
    await tg_deps.dialog_state.set(
        pseudonym, DialogRecord(step="awaiting_rename", contact_id=contact_id)
    )
    logger.info("telegram_dialog_set", step="awaiting_rename")
    chat_id = callback_chat_id(callback)
    if chat_id is not None:
        await bot.send_message(chat_id, tg_deps.strings.contacts_rename_prompt)


async def dialog_text(message: Message, tg_deps: TelegramDeps) -> None:
    """Consume the next private text as a label, never as decode."""
    if message.from_user is None or message.text is None:
        return
    telegram_user_id = message.from_user.id
    record = await tg_deps.dialog_state.get(dialog_pseudonym(tg_deps, telegram_user_id))
    if record is None:
        return
    if not await require_done(message, tg_deps, telegram_user_id):
        return
    user = await actor(tg_deps, telegram_user_id)
    if user is None:
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    if record.step == "awaiting_label":
        await _finish_label(message, tg_deps, user, record, telegram_user_id)
        return
    if record.step == "awaiting_rename":
        await _finish_rename(message, tg_deps, user, record, telegram_user_id)


async def _finish_label(
    message: Message,
    tg_deps: TelegramDeps,
    user: User,
    record: DialogRecord,
    telegram_user_id: int,
) -> None:
    if record.relationship is None:
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.error_generic)
        return
    try:
        label = ContactLabel(message.text or "")
    except InvalidValueError:
        await message.answer(tg_deps.strings.contacts_invalid_label)
        return
    try:
        await tg_deps.create_contact.execute(
            CreateContactCommand(user.id, label, record.relationship)
        )
    except ContactLimitReached:
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.contacts_limit)
        return
    except (NotFound, AccessNotGranted):
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.contacts_unavailable)
        return
    await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
    await _send_contacts_list(message, tg_deps, telegram_user_id)


async def _finish_rename(
    message: Message,
    tg_deps: TelegramDeps,
    user: User,
    record: DialogRecord,
    telegram_user_id: int,
) -> None:
    if record.contact_id is None:
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.error_generic)
        return
    try:
        label = ContactLabel(message.text or "")
    except InvalidValueError:
        await message.answer(tg_deps.strings.contacts_invalid_label)
        return
    try:
        await tg_deps.rename_contact.execute(
            RenameContactCommand(user.id, record.contact_id, label)
        )
    except (NotFound, AccessNotGranted):
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.contacts_unavailable)
        return
    await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
    await _send_contacts_list(message, tg_deps, telegram_user_id)


async def _send_contacts_list(
    message: Message, tg_deps: TelegramDeps, telegram_user_id: int
) -> None:
    user = await actor(tg_deps, telegram_user_id)
    if user is None:
        await render_current_step(message, tg_deps, telegram_user_id)
        return
    listed = await tg_deps.list_contacts.execute(ListContactsCommand(user.id))
    text, keyboard = render_contacts_list(tg_deps.strings, listed.contacts, user.active_contact_id)
    await message.answer(text, reply_markup=keyboard)


async def _send_contacts_list_callback(
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
    listed = await tg_deps.list_contacts.execute(ListContactsCommand(user.id))
    text, keyboard = render_contacts_list(tg_deps.strings, listed.contacts, user.active_contact_id)
    await bot.send_message(chat_id, text, reply_markup=keyboard)


def _parse_relationship(data: str | None) -> RelationshipKind | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "ct" or parts[1] != "rel":
        return None
    try:
        return RelationshipKind(parts[2])
    except ValueError:
        return None


def _parse_contact_id(data: str | None) -> ContactId | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "ct" or parts[1] not in {"a", "r"}:
        return None
    try:
        return ContactId(uuid.UUID(parts[2]))
    except ValueError:
        return None
