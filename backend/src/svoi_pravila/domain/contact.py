"""Contact aggregate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.errors import AlreadyLinkedError, InvalidTransitionError
from svoi_pravila.domain.ids import ContactId, PairId, UserId
from svoi_pravila.domain.text import ContactLabel
from svoi_pravila.domain.time import require_utc

MAX_CONTACTS_PER_USER = 20


@dataclass(frozen=True, slots=True)
class Contact:
    """Owner-local contact, optionally linked to a shared pair."""

    id: ContactId
    owner_id: UserId
    label: ContactLabel
    relationship: RelationshipKind
    pair_id: PairId | None
    created_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at)

    def rename(self, label: ContactLabel) -> Contact:
        """Return a copy with a new label."""
        return replace(self, label=label)

    def link_pair(self, pair_id: PairId) -> Contact:
        """Link to a pair; error if already linked."""
        if self.pair_id is not None:
            msg = "contact already linked to a pair"
            raise AlreadyLinkedError(msg)
        return replace(self, pair_id=pair_id)

    def unlink_pair(self) -> Contact:
        """Clear the pair link."""
        if self.pair_id is None:
            msg = "contact is not linked to a pair"
            raise InvalidTransitionError(msg)
        return replace(self, pair_id=None)
