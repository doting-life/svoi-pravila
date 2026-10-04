"""Grammar of the prepared-result inline reference (no conversation text)."""

from __future__ import annotations

import re

PREPARED_REF_PREFIX = "p_"
PREPARED_REF_BODY_LENGTH = 64
PREPARED_REF_LENGTH = 66
INLINE_QUERY_LIMIT = 256

_PREPARED_REF_RE = re.compile(
    rf"^{PREPARED_REF_PREFIX}[A-Za-z0-9_-]{{{PREPARED_REF_BODY_LENGTH}}}$"
)


def is_prepared_ref(query: str) -> bool:
    """Return True when ``query`` is the ``p_`` + 64-char base64url reference form."""
    return _PREPARED_REF_RE.fullmatch(query) is not None
