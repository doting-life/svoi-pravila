"""Pair aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.domain.errors import InvalidValueError, NotAMemberError
from svoi_pravila.domain.ids import PairId, UserId
from svoi_pravila.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class Pair:
    """Exactly two distinct users sharing rules."""

    id: PairId
    members: frozenset[UserId]
    created_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        if len(self.members) != 2:
            msg = "pair must have exactly two members"
            raise InvalidValueError(msg)

    def is_member(self, user_id: UserId) -> bool:
        """Return whether ``user_id`` is a member."""
        return user_id in self.members

    def other_member(self, user_id: UserId) -> UserId:
        """Return the other member; error if ``user_id`` is not a member."""
        if user_id not in self.members:
            msg = "user is not a pair member"
            raise NotAMemberError(msg)
        return next(iter(self.members - {user_id}))
