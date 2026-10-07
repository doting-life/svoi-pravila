"""One-time export download grants stored by token hash (C0/C1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from svoi_pravila.domain.ids import UserId

EXPORT_DOWNLOAD_TTL_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ExportDownloadGrant:
    """Capability issued for a single download (raw token returned once)."""

    raw_token: str
    expires_at: datetime


class ExportDownloadStore(Protocol):
    """Store hashed download tokens bound to a user; consume once."""

    async def issue(self, user_id: UserId, *, expires_at: datetime) -> ExportDownloadGrant:
        """Persist ``SHA-256(token) → user_id`` until ``expires_at``; return raw token."""
        ...

    async def consume(self, raw_token: str) -> UserId | None:
        """Atomically delete the grant and return the bound user, or None."""
        ...
