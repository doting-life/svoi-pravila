"""Valkey export-download grants: hash-only keys, atomic GETDEL consume."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from redis.asyncio import Redis

from svoi_pravila.adapters.cache._redis_map import map_redis
from svoi_pravila.adapters.cache.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.application.ports.export_download import (
    EXPORT_DOWNLOAD_TTL_SECONDS,
    ExportDownloadGrant,
)
from svoi_pravila.domain.ids import UserId


class ValkeyExportDownloadStore:
    """Issue and consume one-time export download tokens (hash stored only)."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = EXPORT_DOWNLOAD_TTL_SECONDS,
        key_prefix: str = "export:dl",
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _key(self, token_hash: str) -> str:
        return f"{self._key_prefix}:{token_hash}"

    @staticmethod
    def _hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    async def issue(self, user_id: UserId, *, expires_at: datetime) -> ExportDownloadGrant:
        """Create a ≥128-bit token and store only its SHA-256 digest."""
        raw_token = secrets.token_urlsafe(32)
        key = self._key(self._hash(raw_token))

        async def _set() -> bool | None:
            return await self._client.set(
                key,
                str(user_id),
                nx=True,
                ex=self._ttl_seconds,
            )

        created = await map_redis(_set)
        if created is not True:
            raise CacheUnavailable(CacheErrorKind.SERVER)
        return ExportDownloadGrant(raw_token=raw_token, expires_at=expires_at)

    async def consume(self, raw_token: str) -> UserId | None:
        """GETDEL the grant; return the bound user id or None on a clean miss."""
        key = self._key(self._hash(raw_token))

        async def _getdel() -> object:
            return await self._client.getdel(key)

        raw = await map_redis(_getdel)
        if raw is None:
            return None
        text = raw if isinstance(raw, str) else raw.decode()
        try:
            return UserId(uuid.UUID(text))
        except ValueError as exc:
            raise CacheUnavailable(CacheErrorKind.SERVER) from exc
