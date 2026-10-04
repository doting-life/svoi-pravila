"""Inline keyboards for onboarding."""

from __future__ import annotations

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    SwitchInlineQueryChosenChat,
)

from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.domain.enums import ConsentKind

_CALLBACK_DATA_MAX_BYTES = 64


def age_keyboard(strings: TelegramStrings) -> InlineKeyboardMarkup:
    """Buttons for 18+ confirmation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=strings.age_button_yes, callback_data="age:y"),
                InlineKeyboardButton(text=strings.age_button_no, callback_data="age:n"),
            ]
        ]
    )


def consent_keyboard(
    strings: TelegramStrings,
    *,
    kind: ConsentKind,
    version: str,
) -> InlineKeyboardMarkup:
    """Buttons for a versioned consent grant/decline."""
    yes = f"cg:{kind.value}:{version}:y"
    no = f"cg:{kind.value}:{version}:n"
    if (
        len(yes.encode("utf-8")) > _CALLBACK_DATA_MAX_BYTES
        or len(no.encode("utf-8")) > _CALLBACK_DATA_MAX_BYTES
    ):
        msg = "consent callback_data exceeds 64 bytes"
        raise ValueError(msg)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=strings.consent_button_yes, callback_data=yes),
                InlineKeyboardButton(text=strings.consent_button_no, callback_data=no),
            ]
        ]
    )


def confirm_keyboard(
    strings: TelegramStrings,
    *,
    action: str,
    token: str,
) -> InlineKeyboardMarkup:
    """Confirm / cancel buttons; callback_data stays within 64 bytes."""
    yes = f"cf:{action}:{token}"
    no = f"cx:{action}"
    if (
        len(yes.encode("utf-8")) > _CALLBACK_DATA_MAX_BYTES
        or len(no.encode("utf-8")) > _CALLBACK_DATA_MAX_BYTES
    ):
        msg = "rights callback_data exceeds 64 bytes"
        raise ValueError(msg)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=strings.rights_confirm, callback_data=yes),
                InlineKeyboardButton(text=strings.rights_cancel, callback_data=no),
            ]
        ]
    )


def copy_text_markup(
    strings: TelegramStrings,
    text: str,
    *,
    copy_max: int,
) -> InlineKeyboardMarkup | None:
    """«Копировать» when ``text`` is within the Bot API copy_text length."""
    return variant_reply_markup(strings, text, copy_max=copy_max, insert_query=None)


def variant_reply_markup(
    strings: TelegramStrings,
    text: str,
    *,
    copy_max: int,
    insert_query: str | None,
) -> InlineKeyboardMarkup | None:
    """Copy and/or «Вставить в чат» (switch_inline_query_chosen_chat)."""
    buttons: list[InlineKeyboardButton] = []
    if text and len(text) <= copy_max:
        buttons.append(
            InlineKeyboardButton(
                text=strings.decode_copy,
                copy_text=CopyTextButton(text=text),
            )
        )
    if insert_query:
        buttons.append(
            InlineKeyboardButton(
                text=strings.decode_insert,
                switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
                    query=insert_query,
                    allow_user_chats=True,
                    allow_bot_chats=False,
                    allow_group_chats=True,
                    allow_channel_chats=True,
                ),
            )
        )
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
