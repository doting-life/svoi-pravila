"""In-memory ExportDownloadStore fake."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from svoi_pravila.application.ports.export_download import ExportDownloadGrant
from svoi_pravila.domain.ids import UserId


class FakeExportDownloadStore:
    """Hash-only in-memory grants with optional forced expiry."""

    def __init__(self) -> None:
        self._by_hash: dict[str, UserId] = {}
        self.expired: set[str] = set()

    async def issue(self, user_id: UserId, *, expires_at: datetime) -> ExportDownloadGrant:
        raw_token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        self._by_hash[digest] = user_id
        return ExportDownloadGrant(raw_token=raw_token, expires_at=expires_at)

    async def consume(self, raw_token: str) -> UserId | None:
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        if digest in self.expired:
            self._by_hash.pop(digest, None)
            return None
        return self._by_hash.pop(digest, None)

    def mark_expired(self, raw_token: str) -> None:
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        self.expired.add(digest)
