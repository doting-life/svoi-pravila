"""Grammar of the prepared-result inline token (no conversation text)."""

from __future__ import annotations

import re

TOKEN_PREFIX = "p" + "_"
TOKEN_BODY_LENGTH = 64
TOKEN_LENGTH = 66
INLINE_QUERY_LIMIT = 256

_TOKEN_RE = re.compile(rf"^{TOKEN_PREFIX}[A-Za-z0-9_-]{{{TOKEN_BODY_LENGTH}}}$")


def is_prepared_token(query: str) -> bool:
    """Return True when ``query`` is the ``p_`` + 64-char base64url token form."""
    return _TOKEN_RE.fullmatch(query) is not None
