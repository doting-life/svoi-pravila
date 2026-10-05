"""Associated authenticated data strings for field encryption."""

from __future__ import annotations

from uuid import UUID


def contact_label_aad(contact_id: UUID) -> bytes:
    """AAD for ``contacts.label_ciphertext``."""
    return f"contacts:label:{contact_id}:v1".encode()


def rule_revision_text_aad(rule_id: UUID, number: int) -> bytes:
    """AAD for ``rule_revisions.text_ciphertext``."""
    return f"rule_revisions:text:{rule_id}:{number}:v1".encode()


def rule_suggestion_text_aad(suggestion_id: UUID) -> bytes:
    """AAD for ``rule_suggestions.text_ciphertext``."""
    return f"rule_suggestions:text:{suggestion_id}:v1".encode()
