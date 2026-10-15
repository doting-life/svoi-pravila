"""Variant policy v3: code-checked quality rules over the set of rewrite variants.

See ADR-0016 §5 and §10.

The policy never rewrites or blocks a text: it only reports which rules a variant broke.
Stop phrases and second-person forms are closed lists here. Obscene words and accusatory
generalizations are checked in the generation path (``domain.lexicon_guard``), which also reports
a surviving generalization as ``PolicyRule.GENERALIZATION``; insults without obscene words are
left to the prompt and the eval judge, and harsh wording and new hedges or apologies are not
checked by code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from svoi_pravila.domain.enums import RewriteGoal, RuleCategory, VariantKind

# Version of the rules and closed lists below; bump on every change.
POLICY_VERSION = 4

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
LICENSED_EXTRA_WORDS = 8
CHECK_MAX_EDIT_RATIO = 0.15
LADDER_MIN_DISTANCE = 0.10
_DISTANCE_DIGITS = 9

_WORD_RE = re.compile(r"[\u0430-\u044f\u0451a-z0-9]+")
_ORDER: tuple[VariantKind, ...] = (VariantKind.CHECK, VariantKind.SOFTER, VariantKind.FIRMER)


class PolicyRule(StrEnum):
    """Which policy rule a variant broke (C0 metric label)."""

    STOP_PHRASE = "stop_phrase"
    LENGTH = "length"
    EDIT_RATIO = "edit_ratio"
    REGISTER = "register"
    LADDER_LENGTH = "ladder_length"
    LADDER_SIMILAR = "ladder_similar"
    GENERALIZATION = "generalization"


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """What the request licenses: the goal and the categories of the supplied rules."""

    goal: RewriteGoal | None
    rule_categories: frozenset[RuleCategory]

    @property
    def addition_licensed(self) -> bool:
        """True when a goal or any rule may add a phrase to the variants."""
        return self.goal is not None or bool(self.rule_categories)


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """One broken rule of one variant; carries no text."""

    rule: PolicyRule
    kind: VariantKind


def check_variant_set(
    draft: str,
    texts: Mapping[VariantKind, str],
    context: PolicyContext,
) -> tuple[PolicyViolation, ...]:
    """Return every rule that the variants in ``texts`` break, ladder rules included."""
    violations: list[PolicyViolation] = []
    for kind in _ORDER:
        text = texts.get(kind)
        if text is not None:
            violations.extend(
                PolicyViolation(rule, kind) for rule in _variant_rules(draft, kind, text, context)
            )
    softer = texts.get(VariantKind.SOFTER)
    firmer = texts.get(VariantKind.FIRMER)
    if softer is not None and firmer is not None:
        violations.extend(
            PolicyViolation(rule, VariantKind.FIRMER) for rule in _ladder_rules(softer, firmer)
        )
    return tuple(violations)


def _variant_rules(
    draft: str,
    kind: VariantKind,
    text: str,
    context: PolicyContext,
) -> list[PolicyRule]:
    rules: list[PolicyRule] = []
    if _has_new_stop_phrase(draft, text):
        rules.append(PolicyRule.STOP_PHRASE)
    if _too_long(draft, kind, text, licensed=context.addition_licensed):
        rules.append(PolicyRule.LENGTH)
    if kind is VariantKind.CHECK and _edit_ratio(draft, text) > CHECK_MAX_EDIT_RATIO:
        rules.append(PolicyRule.EDIT_RATIO)
    if _register_changed(draft, text):
        rules.append(PolicyRule.REGISTER)
    return rules


def _ladder_rules(softer: str, firmer: str) -> list[PolicyRule]:
    rules: list[PolicyRule] = []
    if len(firmer.split()) > len(softer.split()):
        rules.append(PolicyRule.LADDER_LENGTH)
    softer_words = _words(softer)
    firmer_words = _words(firmer)
    distance = 1 - SequenceMatcher(None, softer_words, firmer_words).ratio()
    if round(distance, _DISTANCE_DIGITS) < LADDER_MIN_DISTANCE:
        rules.append(PolicyRule.LADDER_SIMILAR)
    return rules


def _words(value: str) -> tuple[str, ...]:
    """Words of ``value``, casefolded, with the yo letter written as ye."""
    return tuple(_WORD_RE.findall(value.casefold().replace("\u0451", "\u0435")))


def _contains_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    width = len(needle)
    return any(
        haystack[start : start + width] == needle for start in range(len(haystack) - width + 1)
    )


def _has_new_stop_phrase(draft: str, text: str) -> bool:
    draft_words = _words(draft)
    text_words = _words(text)
    for phrase in STOP_PHRASES:
        phrase_words = _words(phrase)
        if _contains_sequence(text_words, phrase_words) and not _contains_sequence(
            draft_words, phrase_words
        ):
            return True
    return False


def _too_long(draft: str, kind: VariantKind, text: str, *, licensed: bool) -> bool:
    draft_words = len(draft.split())
    text_words = len(text.split())
    scaled = math.ceil(LENGTH_FACTOR * draft_words)
    extra = LICENSED_EXTRA_WORDS if licensed else 0
    if kind is VariantKind.SOFTER:
        return text_words > max(scaled, draft_words + SOFTER_EXTRA_WORDS + extra)
    if kind is VariantKind.FIRMER:
        return text_words > scaled + extra
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
