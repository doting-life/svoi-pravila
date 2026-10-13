"""Variant policy: code-checked quality rules for rewrite variants (ADR-0015 §6).

The policy never rewrites or blocks a text: it only reports which rules a variant broke.
Word lists are closed and versioned here, in the domain.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from svoi_pravila.domain.enums import VariantKind

# Version of the stop-phrase and register lists below; bump on every list change.
POLICY_VERSION = 1

STOP_PHRASES: tuple[str, ...] = (
    "давай обсудим спокойно",
    "чтобы нам обоим было комфортно",
    "я ценю",
    "без давления",
    "я понимаю, что",
    "давай вместе разберёмся",
)

# Closed lists of second-person forms, lower-cased; both the yo and ye spellings are listed.
TY_FORMS: frozenset[str] = frozenset(
    {
        "ты",
        "тебя",
        "тебе",
        "тобой",
        "тобою",
        "твой",
        "твоя",
        "твоё",
        "твое",
        "твои",
        "твоего",
        "твоей",
        "твоему",
        "твоим",
        "твоих",
        "твоими",
        "твою",
    }
)
VY_FORMS: frozenset[str] = frozenset(
    {
        "вы",
        "вас",
        "вам",
        "вами",
        "ваш",
        "ваша",
        "ваше",
        "ваши",
        "вашего",
        "вашей",
        "вашему",
        "вашим",
        "ваших",
        "вашими",
        "вашу",
    }
)

SOFTER_EXTRA_WORDS = 6
LENGTH_FACTOR = 1.2
CHECK_MAX_EDIT_RATIO = 0.15

_WORD_RE = re.compile(r"[\u0430-\u044f\u0451a-z0-9]+")


class PolicyRule(StrEnum):
    """Which policy rule a variant broke (C0 metric label)."""

    STOP_PHRASE = "stop_phrase"
    LENGTH = "length"
    EDIT_RATIO = "edit_ratio"
    REGISTER = "register"


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """One broken rule; carries no text."""

    rule: PolicyRule


def check_variant_policy(draft: str, kind: VariantKind, text: str) -> tuple[PolicyViolation, ...]:
    """Return every rule that ``text`` (a ``kind`` variant of ``draft``) breaks."""
    violations: list[PolicyViolation] = []
    if _has_new_stop_phrase(draft, text):
        violations.append(PolicyViolation(PolicyRule.STOP_PHRASE))
    if _too_long(draft, kind, text):
        violations.append(PolicyViolation(PolicyRule.LENGTH))
    if kind is VariantKind.CHECK and _edit_ratio(draft, text) > CHECK_MAX_EDIT_RATIO:
        violations.append(PolicyViolation(PolicyRule.EDIT_RATIO))
    if _register_changed(draft, text):
        violations.append(PolicyViolation(PolicyRule.REGISTER))
    return tuple(violations)


def _has_new_stop_phrase(draft: str, text: str) -> bool:
    draft_folded = draft.casefold()
    text_folded = text.casefold()
    return any(phrase in text_folded and phrase not in draft_folded for phrase in STOP_PHRASES)


def _too_long(draft: str, kind: VariantKind, text: str) -> bool:
    draft_words = len(draft.split())
    text_words = len(text.split())
    scaled = math.ceil(LENGTH_FACTOR * draft_words)
    if kind is VariantKind.SOFTER:
        return text_words > max(scaled, draft_words + SOFTER_EXTRA_WORDS)
    if kind is VariantKind.FIRMER:
        return text_words > scaled
    return False


def _edit_ratio(draft: str, text: str) -> float:
    return 1 - SequenceMatcher(None, draft.casefold(), text.casefold()).ratio()


def _register_changed(draft: str, text: str) -> bool:
    draft_words = set(_WORD_RE.findall(draft.casefold()))
    text_words = set(_WORD_RE.findall(text.casefold()))
    draft_ty = bool(draft_words & TY_FORMS)
    draft_vy = bool(draft_words & VY_FORMS)
    if draft_ty and not draft_vy:
        return bool(text_words & VY_FORMS)
    if draft_vy and not draft_ty:
        return bool(text_words & TY_FORMS)
    return False
