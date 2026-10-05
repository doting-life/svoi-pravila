"""Render onboarding steps to Telegram messages."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup

from svoi_pravila.adapters.channels.telegram.dates import format_display_date
from svoi_pravila.adapters.channels.telegram.keyboards import (
    age_keyboard,
    consent_keyboard,
    contacts_keyboard,
    rules_keyboard,
    variant_reply_markup,
)
from svoi_pravila.adapters.channels.telegram.localization import (
    TelegramStrings,
    render_crisis_message,
    render_refuse_manipulation,
    suggestion_firmness_adjective,
)
from svoi_pravila.application.ports.generation import (
    AppliedRuleView,
    DecodeCompleted,
    SafetyVerdict,
)
from svoi_pravila.application.use_cases.get_onboarding_step import (
    OnboardingStep,
    OnboardingStepKind,
)
from svoi_pravila.domain.consent_document import ConsentDocument
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.enums import Firmness, RuleStatus
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.rules import Rule

TELEGRAM_MESSAGE_MAX = 4096
_MAX_CITED_RULES = 3


def render_age(strings: TelegramStrings) -> tuple[str, InlineKeyboardMarkup]:
    """Age confirmation prompt and keyboard."""
    return strings.age_prompt, age_keyboard(strings)


def render_consent(
    strings: TelegramStrings,
    document: ConsentDocument,
) -> tuple[str, InlineKeyboardMarkup]:
    """Full consent text plus agree/disagree buttons."""
    return document.text, consent_keyboard(
        strings,
        kind=document.kind,
        version=document.version,
    )


def render_done(strings: TelegramStrings) -> str:
    """Completion sequence for a fully onboarded user."""
    return "\n\n".join((strings.done_storage, strings.done_via_bot, strings.done_commands))


def render_step(
    strings: TelegramStrings,
    step: OnboardingStep,
    document: ConsentDocument | None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Map an onboarding step to message text and optional keyboard."""
    if step.kind is OnboardingStepKind.AGE:
        return render_age(strings)
    if step.kind is OnboardingStepKind.CONSENT:
        if document is None:
            msg = "consent document required for CONSENT step"
            raise ValueError(msg)
        return render_consent(strings, document)
    return render_done(strings), None


def render_decode_completed(
    strings: TelegramStrings,
    completed: DecodeCompleted,
    *,
    copy_max: int,
    insert_queries: tuple[str | None, ...] | None = None,
) -> tuple[tuple[str, InlineKeyboardMarkup | None], ...]:
    """Final decode messages: analysis, hypotheses, then one message per variant."""
    result = completed.result
    if result.safety is SafetyVerdict.CRISIS:
        return ((render_crisis_message(strings)[:TELEGRAM_MESSAGE_MAX], None),)
    if result.safety is SafetyVerdict.REFUSE_MANIPULATION:
        return ((render_refuse_manipulation(strings)[:TELEGRAM_MESSAGE_MAX], None),)
    messages: list[tuple[str, InlineKeyboardMarkup | None]] = []
    if completed.analysis:
        messages.append((completed.analysis[:TELEGRAM_MESSAGE_MAX], None))
    hypo_parts = [item for item in (*result.hypotheses, result.underlying_request) if item]
    if hypo_parts:
        messages.append(("\n\n".join(hypo_parts)[:TELEGRAM_MESSAGE_MAX], None))
    inserts = insert_queries
    if inserts is not None and len(inserts) != len(result.variants):
        msg = "insert_queries must match variants"
        raise ValueError(msg)
    for index, variant in enumerate(result.variants):
        if not variant.text:
            continue
        insert = None if inserts is None else inserts[index]
        messages.append(
            (
                variant.text[:TELEGRAM_MESSAGE_MAX],
                variant_reply_markup(
                    strings,
                    variant.text,
                    copy_max=copy_max,
                    insert_query=insert,
                ),
            )
        )
    return tuple(messages)


def render_applied_rule_citations(
    strings: TelegramStrings,
    views: tuple[AppliedRuleView, ...],
    *,
    now: datetime,
    tz: ZoneInfo,
) -> tuple[str, ...]:
    """At most three citation lines for an OK decode."""
    messages: list[str] = []
    for view in views[:_MAX_CITED_RULES]:
        date = format_display_date(view.effective_since, now, tz)
        text = strings.decode_rule_cited.format(date=date, text=view.text)
        messages.append(text[:TELEGRAM_MESSAGE_MAX])
    return tuple(messages)


def pack_message_lines(
    lines: tuple[str, ...], *, max_len: int = TELEGRAM_MESSAGE_MAX
) -> tuple[str, ...]:
    """Join lines into messages that each stay within the Telegram text limit."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        extra = len(line) if not current else len(line) + 1
        if current and current_len + extra > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n".join(current))
    return tuple(chunks)


def display_rule_text(rule: Rule) -> str:
    """Body text for list and archive confirm (never from callback data)."""
    if rule.status is RuleStatus.ACTIVE:
        return rule.require_effective_revision().text.value
    pending = rule.pending_revision
    if pending is not None:
        return pending.text.value
    return rule.require_effective_revision().text.value


def render_suggestion_dm(
    strings: TelegramStrings,
    *,
    firmness: Firmness,
    contact_label: str,
    rule_text: str,
) -> str:
    """One-time tone suggestion DM body (catalog template; no LLM)."""
    return strings.suggestion_dm.format(
        tone=suggestion_firmness_adjective(strings, firmness),
        label=contact_label,
        text=rule_text,
    )


def render_rules_list(
    strings: TelegramStrings,
    rules: tuple[Rule, ...],
    *,
    suggestions: tuple[RuleSuggestion, ...] = (),
    now: datetime,
    tz: ZoneInfo,
) -> tuple[tuple[str, ...], InlineKeyboardMarkup]:
    """Pending suggestions above ACTIVE/PROPOSED rules; ARCHIVED/REJECTED stay hidden."""
    lines: list[str] = []
    if suggestions:
        lines.append(strings.suggestion_header)
        lines.extend(f"• {suggestion.text.value}" for suggestion in suggestions)
    visible: list[Rule] = []
    lines.append(strings.rules_header)
    number = 0
    for rule in rules:
        if rule.status is RuleStatus.ACTIVE:
            revision = rule.require_effective_revision()
            date = format_display_date(revision.require_effective_since(), now, tz)
            number += 1
            lines.append(f"{number}. {revision.text.value} — {date}")
            visible.append(rule)
        elif rule.status is RuleStatus.PROPOSED:
            revision = rule.pending_revision or rule.revisions[-1]
            number += 1
            lines.append(f"{number}. {revision.text.value} — {strings.rules_proposed_mark}")
            visible.append(rule)
    if number == 0:
        lines.append(strings.rules_empty)
    keyboard = rules_keyboard(
        strings,
        tuple(rule.id for rule in visible),
        tuple(s.id for s in suggestions),
    )
    return pack_message_lines(tuple(lines)), keyboard


def render_contacts_list(
    strings: TelegramStrings,
    contacts: tuple[Contact, ...],
    active_contact_id: ContactId | None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Contact labels for display only, plus action keyboard (ids in callbacks)."""
    if not contacts:
        text = f"{strings.contacts_header}\n{strings.contacts_empty}"
    else:
        lines = [strings.contacts_header]
        for contact in contacts:
            if contact.id == active_contact_id:
                lines.append(f"{contact.label.value} — {strings.contacts_active_mark}")
            else:
                lines.append(contact.label.value)
        text = "\n".join(lines)
    keyboard = contacts_keyboard(strings, tuple(contact.id for contact in contacts))
    return text, keyboard
