"""Load versioned lexicon resources: crisis screen, obscene words, generalizations."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.domain.lexicon import Lexicon, compile_lexicon, load_data_lines
from svoi_pravila.domain.lexicon_guard import GuardLexicons

SAFETY_PACKAGE = "svoi_pravila.domain.safety"
_TONE_PACKAGE = "svoi_pravila.domain.tone"


def load_lexicon(package: str, name: str, locale: str, version: str) -> Lexicon:
    """Load ``<name>/<locale>/<version>.txt`` from ``package`` and compile it."""
    resource = f"{name}/{locale}/{version}.txt"
    raw = resources.files(package).joinpath(resource).read_text(encoding="utf-8")
    return compile_lexicon(load_data_lines(raw))


def load_guard_lexicons_ru_v1() -> GuardLexicons:
    """Load the ru/v1 profanity lexicon and the ru/v1 generalization lexicon."""
    return GuardLexicons(
        profanity=load_lexicon(SAFETY_PACKAGE, "profanity_patterns", "ru", "v1"),
        generalization=load_lexicon(_TONE_PACKAGE, "generalization_patterns", "ru", "v1"),
    )
