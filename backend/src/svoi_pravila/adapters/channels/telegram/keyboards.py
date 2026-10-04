"""Inline keyboards for onboarding."""

from __future__ import annotations

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    SwitchInlineQueryChosenChat,
)

from svoi_pravila.adapters.channels.telegram.localization import (
    TelegramStrings,
    relationship_label,
    rule_category_label,
)
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId

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


def _require_callback_bytes(data: str) -> str:
    if len(data.encode("utf-8")) > _CALLBACK_DATA_MAX_BYTES:
        msg = "contacts callback_data exceeds 64 bytes"
        raise ValueError(msg)
    return data


def contacts_keyboard(
    strings: TelegramStrings,
    contact_ids: tuple[ContactId, ...],
) -> InlineKeyboardMarkup:
    """Per-contact actions plus add. Callback data carries ids only."""
    rows: list[list[InlineKeyboardButton]] = []
    for contact_id in contact_ids:
        ident = str(contact_id)
        active = _require_callback_bytes(f"ct:a:{ident}")
        rename = _require_callback_bytes(f"ct:r:{ident}")
        rows.append(
            [
                InlineKeyboardButton(text=strings.contacts_make_active, callback_data=active),
                InlineKeyboardButton(text=strings.contacts_rename, callback_data=rename),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=strings.contacts_add,
                callback_data=_require_callback_bytes("ct:n"),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def relationship_keyboard(strings: TelegramStrings) -> InlineKeyboardMarkup:
    """Relationship kinds for a new contact."""
    rows: list[list[InlineKeyboardButton]] = []
    for kind in RelationshipKind:
        data = _require_callback_bytes(f"ct:rel:{kind.value}")
        rows.append(
            [InlineKeyboardButton(text=relationship_label(strings, kind), callback_data=data)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rule_category_keyboard(strings: TelegramStrings) -> InlineKeyboardMarkup:
    """Category buttons for a new private rule."""
    rows: list[list[InlineKeyboardButton]] = []
    for kind in RuleCategory:
        data = _require_callback_bytes(f"ru:cat:{kind.value}")
        rows.append(
            [InlineKeyboardButton(text=rule_category_label(strings, kind), callback_data=data)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_keyboard(
    strings: TelegramStrings,
    rule_ids: tuple[RuleId, ...],
) -> InlineKeyboardMarkup:
    """Per-rule archive plus add. Callback data carries ids only."""
    rows: list[list[InlineKeyboardButton]] = []
    for index, rule_id in enumerate(rule_ids, start=1):
        archive = _require_callback_bytes(f"ru:ar:{rule_id}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=strings.rules_archive.format(n=index),
                    callback_data=archive,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=strings.rules_add,
                callback_data=_require_callback_bytes("ru:n"),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_rule_confirm_keyboard(
    strings: TelegramStrings,
    rule_id: RuleId,
) -> InlineKeyboardMarkup:
    """Yes/no archive confirmation. Callback data carries the id only."""
    yes = _require_callback_bytes(f"ru:ay:{rule_id}")
    no = _require_callback_bytes("ru:ax")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=strings.rights_confirm, callback_data=yes),
                InlineKeyboardButton(text=strings.rights_cancel, callback_data=no),
            ]
        ]
    )
