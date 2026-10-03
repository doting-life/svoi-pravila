"""Consent aggregate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ConsentId, UserId
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class Consent:
    """Record of a granted (and optionally revoked) consent."""

    id: ConsentId
    user_id: UserId
    kind: ConsentKind
    text_version: str
    text_sha256: Sha256Hex
    granted_at: datetime
    revoked_at: datetime | None

    def __post_init__(self) -> None:
        if not self.text_version:
            msg = "text_version must be non-empty"
            raise InvalidValueError(msg)
        require_utc(self.granted_at)
        if self.revoked_at is not None:
            require_utc(self.revoked_at)
            if self.revoked_at < self.granted_at:
                msg = "revoked_at must be at or after granted_at"
                raise InvalidValueError(msg)

    def revoke(self, now: datetime) -> Consent:
        """Revoke once; ``now`` must be at or after ``granted_at``."""
        require_utc(now)
        if self.revoked_at is not None:
            msg = "consent already revoked"
            raise InvalidTransitionError(msg)
        if now < self.granted_at:
            msg = "revoke time must be at or after granted_at"
            raise InvalidTransitionError(msg)
        return replace(self, revoked_at=now)

    def is_valid_for(self, version: str, sha256: str) -> bool:
        """Return True when not revoked and version/hash match."""
        return (
            self.revoked_at is None
            and self.text_version == version
            and self.text_sha256.value == sha256
        )
