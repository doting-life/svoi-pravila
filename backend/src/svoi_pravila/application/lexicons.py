"""Load versioned lexicon resources: the one loader for crisis, profanity and tone markers."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.domain.lexicon import Lexicon, compile_lexicon, load_data_lines
from svoi_pravila.domain.tone import ToneLexicons

_SAFETY_PACKAGE = "svoi_pravila.domain.safety"
_TONE_PACKAGE = "svoi_pravila.domain.tone"


def load_lexicon(package: str, name: str, locale: str, version: str) -> Lexicon:
    """Load ``<name>/<locale>/<version>.txt`` from ``package`` and compile it."""
    resource = f"{name}/{locale}/{version}.txt"
    raw = resources.files(package).joinpath(resource).read_text(encoding="utf-8")
    return compile_lexicon(load_data_lines(raw))


def load_tone_lexicons_ru_v1() -> ToneLexicons:
    """Load the ru/v1 profanity lexicon and the harsh, hedge and apology tone lexicons."""
    return ToneLexicons(
        profanity=load_lexicon(_SAFETY_PACKAGE, "profanity_patterns", "ru", "v1"),
        harsh=load_lexicon(_TONE_PACKAGE, "harsh_patterns", "ru", "v1"),
        hedge=load_lexicon(_TONE_PACKAGE, "hedge_patterns", "ru", "v1"),
        apology=load_lexicon(_TONE_PACKAGE, "apology_patterns", "ru", "v1"),
    )
