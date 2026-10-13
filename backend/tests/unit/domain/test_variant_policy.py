"""Variant policy rules (ADR-0015 §6)."""

from __future__ import annotations

import pytest

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import (
    STOP_PHRASES,
    PolicyRule,
    PolicyViolation,
    check_variant_policy,
)


def _rules(draft: str, kind: VariantKind, text: str) -> tuple[PolicyRule, ...]:
    return tuple(v.rule for v in check_variant_policy(draft, kind, text))


@pytest.mark.unit
@pytest.mark.parametrize("phrase", STOP_PHRASES)
@pytest.mark.parametrize("kind", list(VariantKind))
def test_stop_phrase_is_reported_case_insensitively(phrase: str, kind: VariantKind) -> None:
    draft = "Ты опять опоздал"
    text = f"Ты опять опоздал. {phrase.upper()}"
    assert PolicyRule.STOP_PHRASE in _rules(draft, kind, text)


@pytest.mark.unit
def test_stop_phrase_already_in_draft_is_allowed() -> None:
    draft = "Я ценю твою помощь, но так нельзя"
    assert _rules(draft, VariantKind.CHECK, "Я ценю твою помощь, но так нельзя.") == ()


@pytest.mark.unit
def test_stop_phrase_matches_ye_spelling_of_yo() -> None:
    draft = "Ты опять опоздал"
    text = "Ты опять опоздал. Давай вместе разберемся."
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_matches_without_its_comma() -> None:
    draft = "Ты опять опоздал"
    text = "Я понимаю что ты занят."
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_split_by_punctuation_is_reported() -> None:
    draft = "Ты опять опоздал"
    text = "Давай вместе... разберёмся!"
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_words_must_be_contiguous() -> None:
    draft = "Ты опять опоздал"
    text = "Давай вместе всё разберёмся."
    assert PolicyRule.STOP_PHRASE not in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_in_draft_with_other_spelling_is_allowed() -> None:
    draft = "Давай вместе разберёмся с этим"
    text = "Давай вместе разберемся с этим."
    assert _rules(draft, VariantKind.CHECK, text) == ()


@pytest.mark.unit
def test_violation_is_a_value_without_text() -> None:
    assert PolicyViolation(PolicyRule.LENGTH) == PolicyViolation(PolicyRule.LENGTH)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("draft_words", "text_words", "kind", "violates"),
    [
        (10, 12, VariantKind.FIRMER, False),
        (10, 13, VariantKind.FIRMER, True),
        (3, 4, VariantKind.FIRMER, False),
        (3, 5, VariantKind.FIRMER, True),
        (10, 16, VariantKind.SOFTER, False),
        (10, 17, VariantKind.SOFTER, True),
        (100, 120, VariantKind.SOFTER, False),
        (100, 121, VariantKind.SOFTER, True),
        (2, 8, VariantKind.SOFTER, False),
        (2, 9, VariantKind.SOFTER, True),
        (2, 50, VariantKind.CHECK, False),
    ],
)
def test_length_limits(
    draft_words: int, text_words: int, kind: VariantKind, *, violates: bool
) -> None:
    draft = " ".join(["слово"] * draft_words)
    text = " ".join(["слово"] * text_words)
    assert (PolicyRule.LENGTH in _rules(draft, kind, text)) is violates


@pytest.mark.unit
def test_check_edit_ratio_boundary() -> None:
    draft = "а" * 100
    assert _rules(draft, VariantKind.CHECK, draft) == ()
    assert PolicyRule.EDIT_RATIO in _rules(draft, VariantKind.CHECK, "б" * 100)
    assert PolicyRule.EDIT_RATIO not in _rules(draft, VariantKind.SOFTER, "б" * 10)


@pytest.mark.unit
def test_check_with_small_correction_passes() -> None:
    draft = "Ты опять не сделал то что обещал. Сколько можно?"
    text = "Ты опять не сделал то, что обещал. Сколько можно?"
    assert _rules(draft, VariantKind.CHECK, text) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("draft", "text", "violates"),
    [
        ("Ты опять не сделал", "Вы снова не сделали", True),
        ("Ты опять не сделал", "Ты снова не сделал", False),
        ("Вы опять не сделали", "Ты снова не сделал", True),
        ("Вам стоит подумать", "Вам стоит подумать", False),
        ("Ты и вы оба тут", "Ты и вы оба тут", False),
        ("Ты и вы оба тут", "Вы оба тут", False),
        ("Сиди дома и жди", "Вы сидите дома", False),
        ("Сиди дома и жди", "Ты сиди дома", False),
    ],
)
def test_register_rule(draft: str, text: str, *, violates: bool) -> None:
    assert (PolicyRule.REGISTER in _rules(draft, VariantKind.SOFTER, text)) is violates
