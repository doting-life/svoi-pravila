"""Typed localization catalog for the Telegram channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from svoi_pravila.application.support_resources import (
    load_applied_rule_template,
    load_crisis_lead,
    load_support_resources,
)
from svoi_pravila.domain.enums import VariantKind


@dataclass(frozen=True, slots=True)
class TelegramStrings:
    """All user-facing Telegram strings for one locale (closed DM + inline + pair)."""

    dm_welcome: str
    dm_open_app: str
    help_inline: str
    inline_variant_check: str
    inline_variant_softer: str
    inline_variant_firmer: str
    inline_button_how_to: str
    inline_button_finish_setup: str
    inline_button_need_support: str
    inline_button_why_no_variants: str
    inline_button_compose: str
    rate_limited: str
    error_generic: str
    decode_refuse_manipulation: str
    pair_invite_accepted: str
    pair_shared_rule_proposed: str
    pair_shared_rule_approved: str
    pair_shared_rule_rejected: str
    pair_partner_left: str


_KEYS: dict[str, str] = {
    "dm.welcome": "dm_welcome",
    "dm.open_app": "dm_open_app",
    "help.inline": "help_inline",
    "inline.variant.check": "inline_variant_check",
    "inline.variant.softer": "inline_variant_softer",
    "inline.variant.firmer": "inline_variant_firmer",
    "inline.button.how_to": "inline_button_how_to",
    "inline.button.finish_setup": "inline_button_finish_setup",
    "inline.button.need_support": "inline_button_need_support",
    "inline.button.why_no_variants": "inline_button_why_no_variants",
    "inline.button.compose": "inline_button_compose",
    "rate_limited": "rate_limited",
    "error.generic": "error_generic",
    "decode.refuse_manipulation": "decode_refuse_manipulation",
    "pair.invite_accepted": "pair_invite_accepted",
    "pair.shared_rule_proposed": "pair_shared_rule_proposed",
    "pair.shared_rule_approved": "pair_shared_rule_approved",
    "pair.shared_rule_rejected": "pair_shared_rule_rejected",
    "pair.partner_left": "pair_partner_left",
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


def variant_label(strings: TelegramStrings, kind: VariantKind) -> str:
    """Catalog title for a variant kind."""
    if kind is VariantKind.CHECK:
        return strings.inline_variant_check
    if kind is VariantKind.SOFTER:
        return strings.inline_variant_softer
    return strings.inline_variant_firmer


def render_crisis_message() -> str:
    """Careful crisis lead plus versioned help contacts. No diagnosis or advice."""
    resources_block = "\n".join(load_support_resources())
    return f"{load_crisis_lead()}\n\n{resources_block}"


def format_applied_rule_citation(*, date: str, text: str) -> str:
    """Format one decode citation line from the shared C0 template."""
    return load_applied_rule_template().format(date=date, text=text)


def render_refuse_manipulation(strings: TelegramStrings) -> str:
    """Careful refusal plus an offer to help say it respectfully."""
    return strings.decode_refuse_manipulation
