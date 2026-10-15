"""Load versioned lexicon resources: the one loader for the crisis screen and support data."""

from __future__ import annotations

from importlib import resources

from svoi_pravila.domain.lexicon import Lexicon, compile_lexicon, load_data_lines

SAFETY_PACKAGE = "svoi_pravila.domain.safety"


def load_lexicon(package: str, name: str, locale: str, version: str) -> Lexicon:
    """Load ``<name>/<locale>/<version>.txt`` from ``package`` and compile it."""
    resource = f"{name}/{locale}/{version}.txt"
    raw = resources.files(package).joinpath(resource).read_text(encoding="utf-8")
    return compile_lexicon(load_data_lines(raw))
