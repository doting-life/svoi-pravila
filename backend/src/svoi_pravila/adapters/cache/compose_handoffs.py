"""Valkey one-time compose handoff store (bearer sealed token)."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import (
    BearerBinding,
    SealedTokenCodec,
    SealedTokenUnavailableError,
    SealedValkeyStore,
)
from svoi_pravila.application.compose_handoff import (
    COMPOSE_HANDOFF_ID_LEN,
    COMPOSE_HANDOFF_REF_PREFIX,
    ComposeHandoffPayload,
    decode_compose_handoff_payload,
    encode_compose_handoff_payload,
    is_compose_handoff_ref,
)
from svoi_pravila.application.errors import ComposeHandoffUnavailable

_AAD_VERSION = "compose_handoff:v2"
_CODEC = SealedTokenCodec(id_len=COMPOSE_HANDOFF_ID_LEN, prefix=COMPOSE_HANDOFF_REF_PREFIX)
_BEARER = BearerBinding()


class ValkeyComposeHandoffs:
    """AES-256-GCM compose handoffs; redeem_once uses GETDEL before decrypt."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "tg:compose_handoff",
    ) -> None:
        self._store = SealedValkeyStore(
            client,
            ttl_seconds=ttl_seconds,
            key_prefix=key_prefix,
            aad_version=_AAD_VERSION,
            codec=_CODEC,
        )

    async def store(self, payload: ComposeHandoffPayload) -> str:
        """Encrypt ``payload`` and return a compact ``h_`` redeem token."""
        return await self._store.store(_BEARER, encode_compose_handoff_payload(payload))

    async def redeem_once(self, token: str) -> ComposeHandoffPayload:
        """Delete the Valkey record atomically, then decrypt."""
        if not is_compose_handoff_ref(token):
            raise ComposeHandoffUnavailable()
        try:
            plaintext = await self._store.getdel(_BEARER, token)
        except SealedTokenUnavailableError as exc:
            raise ComposeHandoffUnavailable() from exc
        try:
            return decode_compose_handoff_payload(plaintext)
        except ValueError as exc:
            raise ComposeHandoffUnavailable() from exc
