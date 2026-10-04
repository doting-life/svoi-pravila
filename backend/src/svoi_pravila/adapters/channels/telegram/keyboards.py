"""Inline keyboards for onboarding."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
