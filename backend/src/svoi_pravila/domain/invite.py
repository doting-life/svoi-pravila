"""Invite aggregate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from svoi_pravila.domain.errors import (
    InvalidValueError,
    InviteAlreadyAcceptedError,
    InviteExpiredError,
    SelfInviteAcceptError,
)
from svoi_pravila.domain.ids import ContactId, InviteId, UserId
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.time import require_utc

INVITE_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class InviteTokenHash:
    """SHA-256 hex digest of a raw invite token."""

    value: Sha256Hex

    @classmethod
    def from_raw_token(cls, raw_token: str) -> InviteTokenHash:
        """Hash a raw token with SHA-256."""
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        return cls(Sha256Hex(digest))

    @classmethod
    def from_hex(cls, hex_digest: str) -> InviteTokenHash:
        """Wrap an already-computed hex digest."""
        return cls(Sha256Hex(hex_digest))

    @property
    def hex(self) -> str:
        """Return the hex digest string."""
        return self.value.value


@dataclass(frozen=True, slots=True)
class Invite:
    """One-time invite to link a contact into a pair."""

    id: InviteId
    inviter_id: UserId
    contact_id: ContactId
    token_hash: InviteTokenHash
    created_at: datetime
    expires_at: datetime
    accepted_by: UserId | None
    accepted_at: datetime | None

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        require_utc(self.expires_at)
        if self.expires_at != self.created_at + INVITE_TTL:
            msg = "expires_at must equal created_at + INVITE_TTL"
            raise InvalidValueError(msg)
        accepted_pair = (self.accepted_by is None, self.accepted_at is None)
        if accepted_pair not in {(True, True), (False, False)}:
            msg = "accepted_by and accepted_at must both be set or both unset"
            raise InvalidValueError(msg)
        if self.accepted_at is not None:
            require_utc(self.accepted_at)
            if not (self.created_at <= self.accepted_at < self.expires_at):
                msg = "accepted_at must satisfy created_at <= accepted_at < expires_at"
                raise InvalidValueError(msg)
        if self.accepted_by is not None and self.accepted_by == self.inviter_id:
            msg = "accepted_by must not be the inviter"
            raise InvalidValueError(msg)

    @classmethod
    def create(
        cls,
        *,
        invite_id: InviteId,
        inviter_id: UserId,
        contact_id: ContactId,
        token_hash: InviteTokenHash,
        created_at: datetime,
    ) -> Invite:
        """Create an invite with TTL-derived expiry."""
        require_utc(created_at)
        return cls(
            id=invite_id,
            inviter_id=inviter_id,
            contact_id=contact_id,
            token_hash=token_hash,
            created_at=created_at,
            expires_at=created_at + INVITE_TTL,
            accepted_by=None,
            accepted_at=None,
        )

    def accept(self, user_id: UserId, now: datetime) -> Invite:
        """Accept the invite if valid."""
        require_utc(now)
        if self.accepted_at is not None:
            msg = "invite already accepted"
            raise InviteAlreadyAcceptedError(msg)
        if now >= self.expires_at:
            msg = "invite expired"
            raise InviteExpiredError(msg)
        if user_id == self.inviter_id:
            msg = "inviter cannot accept own invite"
            raise SelfInviteAcceptError(msg)
        return replace(self, accepted_by=user_id, accepted_at=now)
