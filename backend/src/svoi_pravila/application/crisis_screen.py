"""Load and apply the versioned deterministic crisis screen."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

from svoi_pravila.domain.safety import compile_crisis_patterns, crisis_hit, load_data_lines

_PATTERNS_PACKAGE = "svoi_pravila.domain.safety"


def load_crisis_pattern_sources(*, locale: str = "ru", version: str = "v1") -> tuple[str, ...]:
    """Load crisis regex sources from the versioned package resource."""
    resource = f"crisis_patterns/{locale}/{version}.txt"
    raw = resources.files(_PATTERNS_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    return load_data_lines(raw)


@dataclass(frozen=True, slots=True)
class CrisisScreen:
    """Deterministic pre-LLM screen over explicit high-precision crisis signals."""

    patterns: tuple[re.Pattern[str], ...]

    @classmethod
    def load_ru_v1(cls) -> CrisisScreen:
        """Load compiled ru/v1 patterns from the package resource."""
        sources = load_crisis_pattern_sources(locale="ru", version="v1")
        return cls(patterns=compile_crisis_patterns(sources))

    def hit(self, text: str) -> bool:
        """Return True when ``text`` contains an explicit crisis signal."""
        return crisis_hit(text, self.patterns)
