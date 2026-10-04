"""Render onboarding steps to Telegram messages."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from svoi_pravila.adapters.channels.telegram.keyboards import (
    age_keyboard,
    consent_keyboard,
    copy_text_markup,
)
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.generation import DecodeCompleted, SafetyVerdict
from svoi_pravila.application.use_cases.get_onboarding_step import (
    OnboardingStep,
    OnboardingStepKind,
)
from svoi_pravila.domain.consent_document import ConsentDocument

TELEGRAM_MESSAGE_MAX = 4096


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
) -> tuple[tuple[str, InlineKeyboardMarkup | None], ...]:
    """Final decode messages: analysis, hypotheses, then one message per variant."""
    result = completed.result
    if result.safety is SafetyVerdict.CRISIS:
        return ((strings.decode_crisis[:TELEGRAM_MESSAGE_MAX], None),)
    if result.safety is SafetyVerdict.REFUSE_MANIPULATION:
        return ((strings.decode_refuse_manipulation[:TELEGRAM_MESSAGE_MAX], None),)
    messages: list[tuple[str, InlineKeyboardMarkup | None]] = []
    if completed.analysis:
        messages.append((completed.analysis[:TELEGRAM_MESSAGE_MAX], None))
    hypo_parts = [item for item in (*result.hypotheses, result.underlying_request) if item]
    if hypo_parts:
        messages.append(("\n\n".join(hypo_parts)[:TELEGRAM_MESSAGE_MAX], None))
    for variant in result.variants:
        if not variant.text:
            continue
        messages.append(
            (
                variant.text[:TELEGRAM_MESSAGE_MAX],
                copy_text_markup(strings, variant.text, copy_max=copy_max),
            )
        )
    return tuple(messages)
