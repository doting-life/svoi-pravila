"""Typed localization catalog for the Telegram channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from svoi_pravila.application.ports.generation import HelpSayIntent
from svoi_pravila.domain.enums import Firmness
from svoi_pravila.domain.safety import load_data_lines


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
    help_inline: str
    inline_prefix_decline: str
    inline_prefix_set_boundary: str
    inline_prefix_admit_fault: str
    inline_prefix_reconnect: str
    inline_prefix_other: str
    inline_firmness_gentle: str
    inline_firmness_balanced: str
    inline_firmness_firm: str
    inline_button_how_to: str
    inline_button_finish_setup: str
    inline_button_need_support: str
    inline_button_why_no_variants: str
    rate_limited: str
    error_generic: str
    decode_copy: str
    decode_insert: str
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
    commands_export: str
    commands_revoke: str
    commands_delete: str
    rights_revoke_explain: str
    rights_delete_explain: str
    rights_confirm: str
    rights_cancel: str
    rights_cancelled: str
    rights_confirm_rejected: str
    rights_deleted: str
    rights_export_empty: str
    rights_export_caption: str


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
    "help.inline": "help_inline",
    "inline.prefix.decline": "inline_prefix_decline",
    "inline.prefix.set_boundary": "inline_prefix_set_boundary",
    "inline.prefix.admit_fault": "inline_prefix_admit_fault",
    "inline.prefix.reconnect": "inline_prefix_reconnect",
    "inline.prefix.other": "inline_prefix_other",
    "inline.firmness.gentle": "inline_firmness_gentle",
    "inline.firmness.balanced": "inline_firmness_balanced",
    "inline.firmness.firm": "inline_firmness_firm",
    "inline.button.how_to": "inline_button_how_to",
    "inline.button.finish_setup": "inline_button_finish_setup",
    "inline.button.need_support": "inline_button_need_support",
    "inline.button.why_no_variants": "inline_button_why_no_variants",
    "rate_limited": "rate_limited",
    "error.generic": "error_generic",
    "decode.copy": "decode_copy",
    "decode.insert": "decode_insert",
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
    "commands.export": "commands_export",
    "commands.revoke": "commands_revoke",
    "commands.delete": "commands_delete",
    "rights.revoke_explain": "rights_revoke_explain",
    "rights.delete_explain": "rights_delete_explain",
    "rights.confirm": "rights_confirm",
    "rights.cancel": "rights_cancel",
    "rights.cancelled": "rights_cancelled",
    "rights.confirm_rejected": "rights_confirm_rejected",
    "rights.deleted": "rights_deleted",
    "rights.export_empty": "rights_export_empty",
    "rights.export_caption": "rights_export_caption",
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


def help_say_intent_prefixes(
    strings: TelegramStrings,
) -> tuple[tuple[str, HelpSayIntent], ...]:
    """Intent prefixes from the catalog (not literals in use-case code)."""
    return (
        (strings.inline_prefix_decline, HelpSayIntent.DECLINE),
        (strings.inline_prefix_set_boundary, HelpSayIntent.SET_BOUNDARY),
        (strings.inline_prefix_admit_fault, HelpSayIntent.ADMIT_FAULT),
        (strings.inline_prefix_reconnect, HelpSayIntent.RECONNECT_AFTER_CONFLICT),
        (strings.inline_prefix_other, HelpSayIntent.OTHER),
    )


def render_help(strings: TelegramStrings) -> str:
    """DM decode help plus inline prefixes taken from the catalog."""
    prefixes = "\n".join(prefix for prefix, _intent in help_say_intent_prefixes(strings))
    return f"{strings.help_body}\n\n{strings.help_inline}\n{prefixes}"


def firmness_label(strings: TelegramStrings, firmness: Firmness) -> str:
    """Catalog title for a variant's firmness."""
    if firmness is Firmness.GENTLE:
        return strings.inline_firmness_gentle
    if firmness is Firmness.BALANCED:
        return strings.inline_firmness_balanced
    return strings.inline_firmness_firm


def load_support_resources() -> tuple[str, ...]:
    """Load versioned support-resource lines, skipping comments."""
    raw = (
        resources.files("svoi_pravila.domain.safety")
        .joinpath("support_resources/ru/v1.txt")
        .read_text(encoding="utf-8")
    )
    return load_data_lines(raw)


def render_crisis_message(strings: TelegramStrings) -> str:
    """Careful crisis copy plus versioned help contacts. No diagnosis or advice."""
    resources_block = "\n".join(load_support_resources())
    return f"{strings.decode_crisis}\n\n{resources_block}"


def render_refuse_manipulation(strings: TelegramStrings) -> str:
    """Careful refusal plus an offer to help say it respectfully."""
    return strings.decode_refuse_manipulation
