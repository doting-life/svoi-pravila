"""Pure normalization for inline query text."""

from __future__ import annotations

import re
import unicodedata

_HORIZONTAL = re.compile(r"[ \t\u00a0]+")
_TRAILING_HORIZONTAL = " \t\u00a0"


def normalize_inline_text(text: str) -> str:
    """Normalize inline query text for length checks, matching, and generation.

    Applies Unicode NFC, strips ends, collapses horizontal whitespace runs to a
    single space, removes trailing horizontal whitespace on each line, and keeps
    newlines.
    """
    normalized = unicodedata.normalize("NFC", text).strip()
    collapsed = _HORIZONTAL.sub(" ", normalized)
    lines = [line.rstrip(_TRAILING_HORIZONTAL) for line in collapsed.split("\n")]
    return "\n".join(lines)
