"""Lexicon resources: loader, anchoring and the match/clean fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from svoi_pravila.application.lexicons import load_guard_lexicons_ru_v1, load_lexicon
from svoi_pravila.domain.lexicon import Lexicon, lexicon_hit, load_data_lines

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lexicon"
_MINIMUM_LINES: dict[str, int] = {"profanity": 60, "generalization": 9}
_REQUIRED_CLEAN: dict[str, tuple[str, ...]] = {
    "profanity": (
        "употреблять",
        "оскорблять",
        "корабля",
        "рубля",
        "небо",
        "небольшой",
        "ребята",
        "хлебать",
        "колебаться",
        "требуй",
        "психуй",
        "страхуйте",
        "мудрый",
        "издеваться",
        "сукно",
        "команда",
        "хлебушек",
        "учеба",
        "бляха",
    ),
    "generalization": ("всегда рад", "никогда не поздно"),
}
_GUARD = load_guard_lexicons_ru_v1()
_LEXICONS: dict[str, Lexicon] = {
    "profanity": _GUARD.profanity,
    "generalization": _GUARD.generalization,
}


def _lines(name: str, kind: str) -> tuple[str, ...]:
    raw = (_FIXTURES / f"{name}_{kind}.txt").read_text(encoding="utf-8")
    return load_data_lines(raw)


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_LEXICONS))
def test_every_match_line_hits(name: str) -> None:
    lines = _lines(name, "match")
    assert len(lines) >= _MINIMUM_LINES[name]
    assert [line for line in lines if not lexicon_hit(line, _LEXICONS[name])] == []


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_LEXICONS))
def test_no_clean_line_hits(name: str) -> None:
    lines = _lines(name, "clean")
    assert lines
    assert [line for line in lines if lexicon_hit(line, _LEXICONS[name])] == []


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_REQUIRED_CLEAN))
def test_clean_lists_include_the_required_words(name: str) -> None:
    lines = set(_lines(name, "clean"))
    assert set(_REQUIRED_CLEAN[name]) <= lines


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_LEXICONS))
def test_every_pattern_is_anchored_at_a_word_start(name: str) -> None:
    patterns = _LEXICONS[name].patterns
    assert patterns
    assert all(pattern.pattern.startswith("\\b") for pattern in patterns)


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "ты всегда",
        "ты никогда",
        "ты постоянно",
        "вы всегда",
        "вы никогда",
        "вы постоянно",
        "вечно ты",
        "вечно вы",
        "сколько можно",
    ],
)
def test_each_generalization_line_matches_its_phrase(line: str) -> None:
    assert lexicon_hit(f"Ну {line} так", _GUARD.generalization)


@pytest.mark.unit
def test_generalization_does_not_list_kak_vsegda() -> None:
    assert not lexicon_hit("Как всегда, отлично", _GUARD.generalization)


@pytest.mark.unit
def test_load_lexicon_reads_the_named_versioned_resource() -> None:
    lexicon = load_lexicon("svoi_pravila.domain.safety", "crisis_patterns", "ru", "v2")
    assert lexicon_hit("не хочу жить", lexicon)
    assert all(pattern.pattern.startswith("\\b") for pattern in lexicon.patterns)


@pytest.mark.unit
def test_load_lexicon_fails_on_a_missing_version() -> None:
    with pytest.raises(FileNotFoundError):
        load_lexicon("svoi_pravila.domain.safety", "profanity_patterns", "ru", "v0")


@pytest.mark.unit
def test_bare_substring_roots_do_not_match_inside_words() -> None:
    assert not lexicon_hit("психуй, страхуйте и требуй", _GUARD.profanity)
