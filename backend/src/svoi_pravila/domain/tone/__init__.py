"""Versioned tone-marker lexicons (harsh, hedge, apology) next to the profanity lexicon."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.domain.lexicon import Lexicon


@dataclass(frozen=True, slots=True)
class ToneLexicons:
    """The four lexicons the variant policy measures a rewrite with."""

    profanity: Lexicon
    harsh: Lexicon
    hedge: Lexicon
    apology: Lexicon
