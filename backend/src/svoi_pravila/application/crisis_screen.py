"""Apply the versioned deterministic crisis screen."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.lexicons import load_lexicon
from svoi_pravila.domain.lexicon import Lexicon, lexicon_hit


@dataclass(frozen=True, slots=True)
class CrisisScreen:
    """Deterministic pre-LLM screen over explicit high-precision crisis signals."""

    lexicon: Lexicon

    @classmethod
    def load_ru_v2(cls) -> CrisisScreen:
        """Load the compiled crisis ru/v2 lexicon from the package resource."""
        return cls(
            lexicon=load_lexicon("svoi_pravila.domain.safety", "crisis_patterns", "ru", "v2")
        )

    def hit(self, text: str) -> bool:
        """Return True when ``text`` contains an explicit crisis signal."""
        return lexicon_hit(text, self.lexicon)
