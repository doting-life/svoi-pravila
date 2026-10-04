"""Typed localization catalog for the Telegram channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True, slots=True)
class TelegramStrings:
    """All user-facing Telegram strings for one locale."""

    commands_start: str
    commands_help: str
    age_prompt: str
    age_button_yes: str
    age_button_no: str
    age_declined: str
    consent_button_yes: str
    consent_button_no: str
    consent_declined: str
    done_storage: str
    done_via_bot: str
    done_commands: str
    help_body: str
    rate_limited: str
    error_generic: str
    decode_copy: str
    decode_need_text: str
    decode_busy: str
    decode_quota: str
    decode_too_short: str
    decode_too_long: str
    decode_crisis: str
    decode_refuse_manipulation: str
    decode_refused: str
    decode_invalid: str
    decode_unavailable: str


_KEYS: dict[str, str] = {
    "commands.start": "commands_start",
    "commands.help": "commands_help",
    "age.prompt": "age_prompt",
    "age.button_yes": "age_button_yes",
    "age.button_no": "age_button_no",
    "age.declined": "age_declined",
    "consent.button_yes": "consent_button_yes",
    "consent.button_no": "consent_button_no",
    "consent.declined": "consent_declined",
    "done.storage": "done_storage",
    "done.via_bot": "done_via_bot",
    "done.commands": "done_commands",
    "help.body": "help_body",
    "rate_limited": "rate_limited",
    "error.generic": "error_generic",
    "decode.copy": "decode_copy",
    "decode.need_text": "decode_need_text",
    "decode.busy": "decode_busy",
    "decode.quota": "decode_quota",
    "decode.too_short": "decode_too_short",
    "decode.too_long": "decode_too_long",
    "decode.crisis": "decode_crisis",
    "decode.refuse_manipulation": "decode_refuse_manipulation",
    "decode.refused": "decode_refused",
    "decode.invalid": "decode_invalid",
    "decode.unavailable": "decode_unavailable",
}


def load_ru_strings() -> TelegramStrings:
    """Load the Russian catalog; missing keys fail at startup."""
    raw = resources.files(__package__).joinpath("ru.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "localization catalog must be a JSON object"
        raise TypeError(msg)
    missing = [key for key in _KEYS if key not in data]
    if missing:
        msg = f"missing localization keys: {', '.join(sorted(missing))}"
        raise KeyError(msg)
    values = {attr: str(data[key]) for key, attr in _KEYS.items()}
    return TelegramStrings(**values)
