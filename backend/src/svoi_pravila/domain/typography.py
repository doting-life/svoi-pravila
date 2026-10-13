"""Human typography for every text the user can send (ADR-0015 §5).

Pure function: the source text (the draft, or the incoming text for decode) decides
whether the author uses dashes. If it does, the generated text is returned untouched.
"""

from __future__ import annotations

import re

_DASHES = "\u2014\u2013"
_DIGIT_DASH_RE = re.compile(r"(?<=\d)\s*[\u2014\u2013]\s*(?=\d)")
_SPACED_DASH_RE = re.compile(r" [\u2014\u2013] ")
_ANY_DASH_RE = re.compile(r"[\u2014\u2013]")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_MULTI_COMMA_RE = re.compile(r",{2,}")
_COMMA_DOT_RE = re.compile(r",\.")


def humanize_typography(source: str, text: str) -> str:
    """Replace machine-style dashes and ellipsis characters unless ``source`` has a dash."""
    if any(dash in source for dash in _DASHES):
        return text
    result = _DIGIT_DASH_RE.sub("-", text)
    result = _SPACED_DASH_RE.sub(", ", result)
    result = _ANY_DASH_RE.sub("-", result)
    result = result.replace("…", "...")
    return _collapse(result)


def _collapse(text: str) -> str:
    """Collapse double spaces, repeated commas and ``,.`` until the text is stable."""
    current = text
    while True:
        collapsed = _MULTI_SPACE_RE.sub(" ", current)
        collapsed = _MULTI_COMMA_RE.sub(",", collapsed)
        collapsed = _COMMA_DOT_RE.sub(".", collapsed)
        if collapsed == current:
            return collapsed
        current = collapsed
