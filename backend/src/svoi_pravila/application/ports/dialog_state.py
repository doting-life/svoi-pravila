"""Ephemeral per-user dialog step (C0/C1 only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId, InviteId

DIALOG_PSEUDONYM_PURPOSE = "dialog"
DialogStep = Literal[
    "awaiting_label",
    "awaiting_rename",
    "awaiting_rule_text",
    "awaiting_invite_label",
]


@dataclass(frozen=True, slots=True)
class DialogRecord:
    """Typed dialog value: step plus optional C0/C1 fields.

    Classifications: ``step`` C0, ``relationship`` C0, ``category`` C0,
    ``contact_id`` C1, ``invite_id`` C1, ``shared`` C0.
    Never includes a label, rule text, or message text.
    """

    step: DialogStep
    contact_id: ContactId | None = None
    relationship: RelationshipKind | None = None
    category: RuleCategory | None = None
    invite_id: InviteId | None = None
    shared: bool | None = None


class DialogState(Protocol):
    """Short-TTL dialog record keyed by dialog-purpose pseudonym."""

    async def get(self, pseudonym: str) -> DialogRecord | None:
        """Return the live record, or None when missing."""
        ...

    async def set(self, pseudonym: str, record: DialogRecord) -> None:
        """Replace the record and refresh TTL."""
        ...

    async def clear(self, pseudonym: str) -> None:
        """Delete the record if present."""
        ...
