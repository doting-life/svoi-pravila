"""Lexicon findings of the tone slots: slots, bounds and text hygiene."""

from __future__ import annotations

import pytest

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.lexicon import compile_lexicon
from svoi_pravila.domain.lexicon_guard import (
    MAX_FINDING_WORD_CHARS,
    MAX_FINDING_WORDS,
    FindingKind,
    GuardLexicons,
    SlotFinding,
    find_in_slots,
)

_LEXICONS = GuardLexicons(
    profanity=compile_lexicon([r"\bзлоб\w*"]),
    generalization=compile_lexicon([r"\bты всегда\b"]),
)


@pytest.mark.unit
def test_findings_cover_softer_and_firmer_in_slot_order() -> None:
    findings = find_in_slots(
        {
            VariantKind.CHECK: "злоба",
            VariantKind.FIRMER: "ты всегда злобный",
            VariantKind.SOFTER: "злобно",
        },
        _LEXICONS,
    )
    assert [(item.kind, item.slot) for item in findings] == [
        (FindingKind.PROFANITY, VariantKind.SOFTER),
        (FindingKind.PROFANITY, VariantKind.FIRMER),
        (FindingKind.GENERALIZATION, VariantKind.FIRMER),
    ]
    assert findings[2].words == ("ты всегда",)


@pytest.mark.unit
def test_a_text_only_in_check_has_no_finding() -> None:
    assert find_in_slots({VariantKind.CHECK: "злоба, ты всегда"}, _LEXICONS) == ()


@pytest.mark.unit
def test_a_missing_slot_and_clean_texts_give_no_finding() -> None:
    assert find_in_slots({VariantKind.SOFTER: "спокойно"}, _LEXICONS) == ()


@pytest.mark.unit
def test_words_are_bounded_in_count_and_length() -> None:
    long_word = "злоб" + "а" * 60
    many = " ".join(f"злоб{index}" for index in range(MAX_FINDING_WORDS + 3))
    [long_finding] = find_in_slots({VariantKind.SOFTER: long_word}, _LEXICONS)
    [many_finding] = find_in_slots({VariantKind.FIRMER: many}, _LEXICONS)
    assert [len(word) for word in long_finding.words] == [MAX_FINDING_WORD_CHARS]
    assert len(many_finding.words) == MAX_FINDING_WORDS


@pytest.mark.unit
def test_repr_and_label_never_contain_the_words() -> None:
    finding = SlotFinding(FindingKind.PROFANITY, VariantKind.FIRMER, ("секретноеслово",))
    assert "секретноеслово" not in repr(finding)
    assert finding.label == "profanity:firmer"
