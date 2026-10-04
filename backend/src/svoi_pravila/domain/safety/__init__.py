"""Pure safety policies and versioned data resources."""

from __future__ import annotations

import re
import string
from collections.abc import Sequence

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


def load_data_lines(text: str) -> tuple[str, ...]:
    """Return non-empty, non-comment lines from a versioned data resource."""
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(_PATTERN_COMMENT):
            continue
        lines.append(stripped)
    return tuple(lines)


def normalize_crisis_text(text: str) -> str:
    """Lowercase, map yo to ye, collapse whitespace, strip punctuation at word edges."""
    folded = text.casefold().replace("\u0451", "\u0435")
    words: list[str] = []
    for token in folded.split():
        stripped = token.strip(_EDGE_PUNCT)
        if stripped:
            words.append(stripped)
    return " ".join(words)


def compile_crisis_patterns(pattern_sources: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """Compile versioned crisis regexes (Unicode word boundaries)."""
    return tuple(re.compile(source) for source in pattern_sources)


def crisis_hit(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    """Return True when normalized text matches an explicit crisis pattern."""
    normalized = normalize_crisis_text(text)
    if not normalized:
        return False
    return any(pattern.search(normalized) is not None for pattern in patterns)
