"""Private-chat contacts: list, add, rename, set active, invite, leave pair."""

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
from svoi_pravila.adapters.channels.telegram.keyboards import (
    invite_share_keyboard,
    leave_confirm_keyboard,
    relationship_keyboard,
)
from svoi_pravila.adapters.channels.telegram.presenters import render_contacts_list
from svoi_pravila.application.errors import (
    AccessNotGranted,
    AlreadyPaired,
    ContactAlreadyLinked,
    ContactLimitReached,
    NotFound,
)
from svoi_pravila.application.ports.dialog_state import DialogRecord
from svoi_pravila.application.use_cases.accept_invite import AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInviteCommand
from svoi_pravila.application.use_cases.leave_pair import LeavePairCommand
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand
from svoi_pravila.application.use_cases.rename_contact import RenameContactCommand
from svoi_pravila.application.use_cases.set_active_contact import SetActiveContactCommand
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.errors import (
    InvalidValueError,
    InviteAlreadyAcceptedError,
    InviteExpiredError,
    SelfInviteAcceptError,
)
from svoi_pravila.domain.ids import ContactId, InviteId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.domain.user import User

_CALLBACK_PARTS = 3
_START_PARAM_MAX = 64
logger = structlog.get_logger(__name__)


class AwaitingDialogText(Filter):
    """Match private text only while a dialog step is waiting for a label."""

    async def __call__(self, message: Message, tg_deps: TelegramDeps) -> bool:
        if message.from_user is None or message.text is None:
            return False
        if message.text.startswith("/"):
            return False
        record = await tg_deps.dialog_state.get(dialog_pseudonym(tg_deps, message.from_user.id))
        return record is not None and record.step in {
            "awaiting_label",
            "awaiting_rename",
            "awaiting_invite_label",
        }


def build_contacts_router() -> Router:
    """Create the private-chat router for contacts after onboarding."""
    router = Router(name="telegram_contacts")
    router.message.register(contacts_command, Command("contacts"))
    router.message.register(cancel_command, Command("cancel"))
    router.callback_query.register(add_contact, F.data == "ct:n")
    router.callback_query.register(choose_relationship, F.data.startswith("ct:rel:"))
    router.callback_query.register(set_active, F.data.startswith("ct:a:"))
    router.callback_query.register(start_rename, F.data.startswith("ct:r:"))
    router.callback_query.register(start_invite, F.data.startswith("ct:i:"))
    router.callback_query.register(confirm_leave, F.data.startswith("ct:ly:"))
    router.callback_query.register(cancel_leave, F.data == "ct:lx")
    router.callback_query.register(ask_leave, F.data.startswith("ct:l:"))
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
    contact_id = _parse_contact_id(callback.data, "a")
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
    contact_id = _parse_contact_id(callback.data, "r")
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


async def start_invite(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Create an invite and send the deep-link share message."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    contact_id = _parse_contact_id(callback.data, "i")
    if contact_id is None:
        return
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    try:
        created = await tg_deps.create_invite.execute(CreateInviteCommand(user.id, contact_id))
    except ContactAlreadyLinked:
        await bot.send_message(chat_id, tg_deps.strings.contacts_unavailable)
        return
    except (NotFound, AccessNotGranted):
        await bot.send_message(chat_id, tg_deps.strings.error_generic)
        return
    username = tg_deps.bot_username.username or "test_bot"
    start_param = f"inv_{created.raw_token}"
    if len(start_param) > _START_PARAM_MAX:
        msg = "invite start parameter exceeds 64 characters"
        raise ValueError(msg)
    deep_link = f"https://t.me/{username}?start={start_param}"
    text = f"{deep_link}\n{tg_deps.strings.contacts_invite_explain}"
    await bot.send_message(
        chat_id,
        text,
        reply_markup=invite_share_keyboard(deep_link, tg_deps.strings),
    )


async def ask_leave(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Ask for leave-pair confirmation."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    contact_id = _parse_contact_id(callback.data, "l")
    if contact_id is None:
        return
    chat_id = callback_chat_id(callback)
    if chat_id is None:
        return
    await bot.send_message(
        chat_id,
        tg_deps.strings.contacts_leave_confirm,
        reply_markup=leave_confirm_keyboard(contact_id, tg_deps.strings),
    )


async def confirm_leave(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Leave the pair through the use case after confirmation."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    contact_id = _parse_contact_id(callback.data, "ly")
    if contact_id is None:
        return
    user = await actor(tg_deps, callback.from_user.id)
    if user is None:
        await send_current_step(bot, callback, tg_deps, callback.from_user.id)
        return
    listed = await tg_deps.list_contacts.execute(ListContactsCommand(user.id))
    contact = next((item for item in listed.contacts if item.id == contact_id), None)
    if contact is None or contact.pair_id is None:
        await reply_callback(bot, callback, tg_deps.strings.contacts_unavailable)
        return
    try:
        await tg_deps.leave_pair.execute(LeavePairCommand(user.id, contact.pair_id))
    except (NotFound, AccessNotGranted):
        await reply_callback(bot, callback, tg_deps.strings.error_generic)
        return
    await _send_contacts_list_callback(callback, tg_deps, bot, callback.from_user.id)


async def cancel_leave(callback: CallbackQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
    """Drop the leave confirm keyboard and refresh the list."""
    await clear_callback_keyboard(bot, callback)
    await callback.answer()
    if not await require_done_callback(callback, tg_deps, bot):
        return
    await _send_contacts_list_callback(callback, tg_deps, bot, callback.from_user.id)


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
        return
    if record.step == "awaiting_invite_label":
        await _finish_invite_label(message, tg_deps, user, record, telegram_user_id)


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


async def _finish_invite_label(
    message: Message,
    tg_deps: TelegramDeps,
    user: User,
    record: DialogRecord,
    telegram_user_id: int,
) -> None:
    if record.invite_id is None or record.relationship is None:
        await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
        await message.answer(tg_deps.strings.error_generic)
        return
    try:
        label = ContactLabel(message.text or "")
    except InvalidValueError:
        await message.answer(tg_deps.strings.contacts_invalid_label)
        return
    error_text = await _try_accept_invite(
        tg_deps,
        user,
        invite_id=record.invite_id,
        relationship=record.relationship,
        label=label,
    )
    await tg_deps.dialog_state.clear(dialog_pseudonym(tg_deps, telegram_user_id))
    if error_text is not None:
        await message.answer(error_text)
        return
    await message.answer(tg_deps.strings.invite_accepted_invitee)


async def _try_accept_invite(
    tg_deps: TelegramDeps,
    user: User,
    *,
    invite_id: InviteId,
    relationship: RelationshipKind,
    label: ContactLabel,
) -> str | None:
    try:
        await tg_deps.accept_invite.execute(
            AcceptInviteCommand(
                user.id,
                invite_id,
                label,
                relationship,
            )
        )
    except ContactLimitReached:
        return tg_deps.strings.contacts_limit
    except (NotFound, InviteAlreadyAcceptedError):
        return tg_deps.strings.invite_invalid
    except InviteExpiredError:
        return tg_deps.strings.invite_expired
    except SelfInviteAcceptError:
        return tg_deps.strings.invite_own
    except (AlreadyPaired, AccessNotGranted):
        return tg_deps.strings.error_generic
    return None


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


def _parse_contact_id(data: str | None, action: str) -> ContactId | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != _CALLBACK_PARTS or parts[0] != "ct" or parts[1] != action:
        return None
    try:
        return ContactId(uuid.UUID(parts[2]))
    except ValueError:
        return None
