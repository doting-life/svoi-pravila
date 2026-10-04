"""Canonical digest keys for inline result reuse (no plaintext in stored keys)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from svoi_pravila.application.ports.generation import HelpSayIntent, RuleContext
from svoi_pravila.domain.enums import RelationshipKind, UsageScenario

_PERSON = b"inline-reuse"


@dataclass(frozen=True, slots=True)
class InlineReuseKeyMaterial:
    """Canonical fields hashed into an opaque reuse key."""

    user_key: str
    scenario: UsageScenario
    intent: HelpSayIntent | None
    draft: str
    relationship: RelationshipKind
    rules: tuple[RuleContext, ...]


def _field(value: str) -> bytes:
    data = value.encode("utf-8")
    return len(data).to_bytes(4, "big") + data


def inline_reuse_key(material: InlineReuseKeyMaterial) -> str:
    """Return a blake2b hex digest of the canonical reuse key material."""
    parts = [
        _field(material.user_key),
        _field(material.scenario.value),
        _field("" if material.intent is None else material.intent.value),
        _field(material.draft),
        _field(material.relationship.value),
    ]
    for rule in material.rules:
        parts.append(_field(rule.category.value))
        parts.append(_field(rule.text))
        parts.append(_field(rule.effective_since.isoformat()))
    digest = hashlib.blake2b(b"".join(parts), person=_PERSON, digest_size=32)
    return digest.hexdigest()
