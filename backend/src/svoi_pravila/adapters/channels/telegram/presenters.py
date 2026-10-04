"""Render onboarding steps to Telegram messages."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from svoi_pravila.adapters.channels.telegram.keyboards import age_keyboard, consent_keyboard
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.use_cases.get_onboarding_step import (
    OnboardingStep,
    OnboardingStepKind,
)
from svoi_pravila.domain.consent_document import ConsentDocument


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
