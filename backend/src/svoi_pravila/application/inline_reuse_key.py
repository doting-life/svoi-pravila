"""Canonical digest keys for inline result reuse (no plaintext in stored keys)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from svoi_pravila.domain.enums import UsageScenario

_PERSON = b"inline-reuse"


@dataclass(frozen=True, slots=True)
class InlineReuseKeyMaterial:
    """Canonical fields hashed into an opaque reuse key."""

    user_key: str
    scenario: UsageScenario
    draft: str


def _field(value: str) -> bytes:
    data = value.encode("utf-8")
    return len(data).to_bytes(4, "big") + data


def inline_reuse_key(material: InlineReuseKeyMaterial) -> str:
    """Return a blake2b hex digest of the canonical reuse key material."""
    parts = [
        _field(material.user_key),
        _field(material.scenario.value),
        _field(material.draft),
    ]
    digest = hashlib.blake2b(b"".join(parts), person=_PERSON, digest_size=32)
    return digest.hexdigest()
