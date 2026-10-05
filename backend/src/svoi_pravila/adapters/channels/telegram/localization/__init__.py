"""Typed localization catalog for the Telegram channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from svoi_pravila.application.ports.generation import HelpSayIntent
from svoi_pravila.application.support_resources import load_support_resources
from svoi_pravila.domain.enums import Firmness, RelationshipKind, RuleCategory
from svoi_pravila.privacy import load_privacy_catalog


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
    decode_make_rule: str
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
    commands_contacts: str
    commands_rules: str
    commands_cancel: str
    contacts_header: str
    contacts_empty: str
    contacts_active_mark: str
    contacts_make_active: str
    contacts_rename: str
    contacts_add: str
    contacts_label_prompt: str
    contacts_rename_prompt: str
    contacts_cancelled: str
    contacts_invalid_label: str
    contacts_limit: str
    contacts_unavailable: str
    contacts_relationship_partner: str
    contacts_relationship_family: str
    contacts_relationship_friend: str
    contacts_relationship_work: str
    contacts_relationship_other: str
    rules_header: str
    rules_empty: str
    rules_no_active_contact: str
    rules_add: str
    rules_archive: str
    rules_archive_confirm: str
    rules_proposed_mark: str
    rules_text_prompt: str
    rules_invalid_text: str
    rules_limit: str
    rules_contact_unavailable: str
    rules_already_archived: str
    rules_category_taboo_topic: str
    rules_category_how_to_ask: str
    rules_category_apology: str
    rules_category_conflict_protocol: str
    rules_category_other: str
    suggestion_dm: str
    suggestion_accept: str
    suggestion_edit: str
    suggestion_dismiss: str
    suggestion_dismissed: str
    suggestion_already_decided: str
    suggestion_header: str
    suggestion_firmness_gentle: str
    suggestion_firmness_balanced: str
    suggestion_firmness_firm: str
    suggestion_decode_ok: str
    suggestion_decode_none: str
    suggestion_decode_expired: str
    suggestion_decode_quota: str
    suggestion_decode_crisis: str
    suggestion_decode_edit_prompt: str
    decode_rule_cited: str
    inline_rule_cited_prefix: str
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
    "decode.make_rule": "decode_make_rule",
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
    "commands.contacts": "commands_contacts",
    "commands.rules": "commands_rules",
    "commands.cancel": "commands_cancel",
    "contacts.header": "contacts_header",
    "contacts.empty": "contacts_empty",
    "contacts.active_mark": "contacts_active_mark",
    "contacts.make_active": "contacts_make_active",
    "contacts.rename": "contacts_rename",
    "contacts.add": "contacts_add",
    "contacts.label_prompt": "contacts_label_prompt",
    "contacts.rename_prompt": "contacts_rename_prompt",
    "contacts.cancelled": "contacts_cancelled",
    "contacts.invalid_label": "contacts_invalid_label",
    "contacts.limit": "contacts_limit",
    "contacts.unavailable": "contacts_unavailable",
    "contacts.relationship.partner": "contacts_relationship_partner",
    "contacts.relationship.family": "contacts_relationship_family",
    "contacts.relationship.friend": "contacts_relationship_friend",
    "contacts.relationship.work": "contacts_relationship_work",
    "contacts.relationship.other": "contacts_relationship_other",
    "rules.header": "rules_header",
    "rules.empty": "rules_empty",
    "rules.no_active_contact": "rules_no_active_contact",
    "rules.add": "rules_add",
    "rules.archive": "rules_archive",
    "rules.archive_confirm": "rules_archive_confirm",
    "rules.proposed_mark": "rules_proposed_mark",
    "rules.text_prompt": "rules_text_prompt",
    "rules.invalid_text": "rules_invalid_text",
    "rules.limit": "rules_limit",
    "rules.contact_unavailable": "rules_contact_unavailable",
    "rules.already_archived": "rules_already_archived",
    "rules.category.taboo_topic": "rules_category_taboo_topic",
    "rules.category.how_to_ask": "rules_category_how_to_ask",
    "rules.category.apology": "rules_category_apology",
    "rules.category.conflict_protocol": "rules_category_conflict_protocol",
    "rules.category.other": "rules_category_other",
    "suggestion.dm": "suggestion_dm",
    "suggestion.accept": "suggestion_accept",
    "suggestion.edit": "suggestion_edit",
    "suggestion.dismiss": "suggestion_dismiss",
    "suggestion.dismissed": "suggestion_dismissed",
    "suggestion.already_decided": "suggestion_already_decided",
    "suggestion.header": "suggestion_header",
    "suggestion.firmness.gentle": "suggestion_firmness_gentle",
    "suggestion.firmness.balanced": "suggestion_firmness_balanced",
    "suggestion.firmness.firm": "suggestion_firmness_firm",
    "suggestion.decode_ok": "suggestion_decode_ok",
    "suggestion.decode_none": "suggestion_decode_none",
    "suggestion.decode_expired": "suggestion_decode_expired",
    "suggestion.decode_quota": "suggestion_decode_quota",
    "suggestion.decode_crisis": "suggestion_decode_crisis",
    "suggestion.decode_edit_prompt": "suggestion_decode_edit_prompt",
    "decode.rule_cited": "decode_rule_cited",
    "inline.rule_cited_prefix": "inline_rule_cited_prefix",
    "rights.confirm": "rights_confirm",
    "rights.cancel": "rights_cancel",
    "rights.cancelled": "rights_cancelled",
    "rights.confirm_rejected": "rights_confirm_rejected",
    "rights.deleted": "rights_deleted",
    "rights.export_empty": "rights_export_empty",
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
    privacy = load_privacy_catalog()
    values["rights_export_caption"] = privacy.export.description
    values["rights_revoke_explain"] = privacy.revoke.confirm
    values["rights_delete_explain"] = privacy.delete.description
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


def relationship_label(strings: TelegramStrings, kind: RelationshipKind) -> str:
    """Catalog label for a relationship kind."""
    if kind is RelationshipKind.PARTNER:
        return strings.contacts_relationship_partner
    if kind is RelationshipKind.FAMILY:
        return strings.contacts_relationship_family
    if kind is RelationshipKind.FRIEND:
        return strings.contacts_relationship_friend
    if kind is RelationshipKind.WORK:
        return strings.contacts_relationship_work
    return strings.contacts_relationship_other


def rule_category_label(strings: TelegramStrings, kind: RuleCategory) -> str:
    """Catalog label for a rule category."""
    if kind is RuleCategory.TABOO_TOPIC:
        return strings.rules_category_taboo_topic
    if kind is RuleCategory.HOW_TO_ASK:
        return strings.rules_category_how_to_ask
    if kind is RuleCategory.APOLOGY:
        return strings.rules_category_apology
    if kind is RuleCategory.CONFLICT_PROTOCOL:
        return strings.rules_category_conflict_protocol
    return strings.rules_category_other


def firmness_label(strings: TelegramStrings, firmness: Firmness) -> str:
    """Catalog title for a variant's firmness."""
    if firmness is Firmness.GENTLE:
        return strings.inline_firmness_gentle
    if firmness is Firmness.BALANCED:
        return strings.inline_firmness_balanced
    return strings.inline_firmness_firm


def suggestion_firmness_adjective(strings: TelegramStrings, firmness: Firmness) -> str:
    """Adjective used in the tone-suggestion DM («мягкий вариант»)."""
    if firmness is Firmness.GENTLE:
        return strings.suggestion_firmness_gentle
    if firmness is Firmness.BALANCED:
        return strings.suggestion_firmness_balanced
    return strings.suggestion_firmness_firm


def render_crisis_message(strings: TelegramStrings) -> str:
    """Careful crisis copy plus versioned help contacts. No diagnosis or advice."""
    resources_block = "\n".join(load_support_resources())
    return f"{strings.decode_crisis}\n\n{resources_block}"


def render_refuse_manipulation(strings: TelegramStrings) -> str:
    """Careful refusal plus an offer to help say it respectfully."""
    return strings.decode_refuse_manipulation
