"""Valkey reusable compose session store (bearer sealed token)."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import (
    BearerBinding,
    SealedTokenCodec,
    SealedTokenUnavailableError,
    SealedValkeyStore,
)
from svoi_pravila.application.compose_session import (
    COMPOSE_SESSION_ID_LEN,
    COMPOSE_SESSION_REF_PREFIX,
    COMPOSE_SESSION_TTL_SECONDS,
    ComposeSessionPayload,
    decode_compose_session_payload,
    encode_compose_session_payload,
    is_compose_session_ref,
)
from svoi_pravila.application.errors import ComposeSessionUnavailable

_AAD_VERSION = "compose_session:v1"
_CODEC = SealedTokenCodec(id_len=COMPOSE_SESSION_ID_LEN, prefix=COMPOSE_SESSION_REF_PREFIX)
_BEARER = BearerBinding()


class ValkeyComposeSessions:
    """AES-256-GCM compose sessions; load uses GET (reusable within TTL)."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = COMPOSE_SESSION_TTL_SECONDS,
        key_prefix: str = "tg:compose_session",
    ) -> None:
        self._store = SealedValkeyStore(
            client,
            ttl_seconds=ttl_seconds,
            key_prefix=key_prefix,
            aad_version=_AAD_VERSION,
            codec=_CODEC,
        )

    async def store(self, payload: ComposeSessionPayload) -> str:
        """Encrypt ``payload`` and return a compact ``cs_`` session token."""
        return await self._store.store(_BEARER, encode_compose_session_payload(payload))

    async def load(self, token: str) -> ComposeSessionPayload:
        """Decrypt without deleting; miss/tamper → ComposeSessionUnavailable."""
        if not is_compose_session_ref(token):
            raise ComposeSessionUnavailable()
        try:
            plaintext = await self._store.get(_BEARER, token)
        except SealedTokenUnavailableError as exc:
            raise ComposeSessionUnavailable() from exc
        try:
            return decode_compose_session_payload(plaintext)
        except ValueError as exc:
            raise ComposeSessionUnavailable() from exc
