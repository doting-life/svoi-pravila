"""Shared lexicon mechanism: normalization, loading, matching and counting."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from svoi_pravila.domain.lexicon import (
    Lexicon,
    compile_lexicon,
    lexicon_count,
    lexicon_hit,
    load_data_lines,
    normalize_lexicon_text,
)


@pytest.mark.unit
def test_normalize_folds_case_maps_yo_and_strips_edge_punctuation() -> None:
    assert normalize_lexicon_text("  Ёлка, «Жить!!!»  ") == "елка жить"


@pytest.mark.unit
def test_normalize_keeps_asterisk_inside_a_word_only() -> None:
    assert normalize_lexicon_text("Х*й, **бл*дь**") == "х*й бл*дь"


@pytest.mark.unit
def test_normalize_drops_tokens_made_of_punctuation() -> None:
    assert normalize_lexicon_text("... — !!!") == ""


@pytest.mark.unit
def test_load_data_lines_skips_blank_and_comment_lines() -> None:
    assert load_data_lines("# c\n\nабв\n  # x\n  гд  ") == ("абв", "гд")


@pytest.mark.unit
def test_compile_lexicon_keeps_pattern_sources_in_order() -> None:
    lexicon = compile_lexicon([r"\bа", r"\bб"])
    assert [pattern.pattern for pattern in lexicon.patterns] == [r"\bа", r"\bб"]


@pytest.mark.unit
def test_hit_matches_on_normalized_text() -> None:
    lexicon = compile_lexicon([r"\bелка\b"])
    assert lexicon_hit("«Ёлка!»", lexicon)
    assert not lexicon_hit("ёлки", lexicon)


@pytest.mark.unit
def test_empty_lexicon_and_empty_text_never_match() -> None:
    assert lexicon_count("слово", Lexicon(patterns=())) == 0
    assert lexicon_count("!!!", compile_lexicon([r"\bа"])) == 0


@pytest.mark.unit
def test_count_counts_every_separate_match() -> None:
    lexicon = compile_lexicon([r"\bда\b", r"\bнет\b"])
    assert lexicon_count("да нет да", lexicon) == 3


@pytest.mark.unit
def test_count_counts_overlapping_matches_once() -> None:
    lexicon = compile_lexicon([r"\bмог бы\b", r"\bне мог бы\b", r"\bбы\b"])
    assert lexicon_count("он не мог бы", lexicon) == 1


@pytest.mark.unit
def test_count_counts_adjacent_matches_separately() -> None:
    lexicon = compile_lexicon([r"\bда\b"])
    assert lexicon_count("да да", lexicon) == 2


@given(st.text(max_size=80))
@pytest.mark.unit
def test_normalization_is_idempotent(text: str) -> None:
    once = normalize_lexicon_text(text)
    assert normalize_lexicon_text(once) == once
