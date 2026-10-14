"""Shared lexicon mechanism: normalization, compilation and matching of versioned regex lists."""

from __future__ import annotations

import re
import string
from collections.abc import Sequence
from dataclasses import dataclass

_EDGE_PUNCT = string.punctuation + "".join(
    chr(code)
    for code in (
        0x00AB,
        0x00BB,
        0x201E,
        0x201C,
        0x201D,
        0x2018,
        0x2019,
        0x2014,
        0x2013,
        0x2026,
        0x2022,
        0x00B7,
        0x2039,
        0x203A,
    )
)
_PATTERN_COMMENT = "#"


@dataclass(frozen=True, slots=True)
class Lexicon:
    """Compiled patterns of one versioned lexicon, applied to normalized text."""

    patterns: tuple[re.Pattern[str], ...]


def load_data_lines(text: str) -> tuple[str, ...]:
    """Return non-empty, non-comment lines from a versioned data resource."""
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(_PATTERN_COMMENT):
            continue
        lines.append(stripped)
    return tuple(lines)


def normalize_lexicon_text(text: str) -> str:
    """Casefold, map yo to ye, collapse whitespace, strip punctuation at word edges.

    An asterisk inside a word is kept: masked forms stay matchable.
    """
    folded = text.casefold().replace("\u0451", "\u0435")
    words: list[str] = []
    for token in folded.split():
        stripped = token.strip(_EDGE_PUNCT)
        if stripped:
            words.append(stripped)
    return " ".join(words)


def compile_lexicon(pattern_sources: Sequence[str]) -> Lexicon:
    """Compile versioned regex sources (Unicode word boundaries) into a lexicon."""
    return Lexicon(patterns=tuple(re.compile(source) for source in pattern_sources))


def lexicon_hit(text: str, lexicon: Lexicon) -> bool:
    """Return True when the normalized text matches any pattern of ``lexicon``."""
    return lexicon_count(text, lexicon) > 0


def lexicon_count(text: str, lexicon: Lexicon) -> int:
    """Return the number of matches in the normalized text.

    Overlapping matches of different patterns count once.
    """
    normalized = normalize_lexicon_text(text)
    spans = sorted(
        match.span() for pattern in lexicon.patterns for match in pattern.finditer(normalized)
    )
    count = 0
    covered_until = -1
    for start, end in spans:
        if start >= covered_until:
            count += 1
            covered_until = end
        else:
            covered_until = max(covered_until, end)
    return count
