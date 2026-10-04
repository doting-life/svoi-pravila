"""HMAC-SHA256 pseudonymizer (domain-separated by purpose label)."""

from __future__ import annotations

import hmac
from hashlib import sha256

_MIN_PEPPER_BYTES = 32


class HmacPseudonymizer:
    """Deterministic hex pseudonyms from a pepper and purpose label."""

    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < _MIN_PEPPER_BYTES:
            msg = f"pepper must be at least {_MIN_PEPPER_BYTES} bytes"
            raise ValueError(msg)
        self._pepper = pepper

    def pseudonymize(self, purpose: str, value: str) -> str:
        """Return hex HMAC-SHA256 of ``purpose`` and ``value`` under the pepper."""
        if not purpose:
            msg = "purpose must be non-empty"
            raise ValueError(msg)
        message = purpose.encode("utf-8") + b"\0" + value.encode("utf-8")
        return hmac.new(self._pepper, message, sha256).hexdigest()
