"""Valkey one-time compose handoff store (same sealed-token mechanism as prepared results)."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import (
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

_AAD_VERSION = "compose_handoff:v1"
_CODEC = SealedTokenCodec(id_len=COMPOSE_HANDOFF_ID_LEN, prefix=COMPOSE_HANDOFF_REF_PREFIX)


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

    async def store(self, user_pseudonym: str, payload: ComposeHandoffPayload) -> str:
        """Encrypt ``payload`` and return a compact ``h_`` redeem token."""
        return await self._store.store(user_pseudonym, encode_compose_handoff_payload(payload))

    async def redeem_once(self, user_pseudonym: str, token: str) -> ComposeHandoffPayload:
        """Delete the Valkey record atomically, then decrypt for the owning user."""
        if not is_compose_handoff_ref(token):
            raise ComposeHandoffUnavailable()
        try:
            plaintext = await self._store.getdel(user_pseudonym, token)
        except SealedTokenUnavailableError as exc:
            raise ComposeHandoffUnavailable() from exc
        try:
            return decode_compose_handoff_payload(plaintext)
        except ValueError as exc:
            raise ComposeHandoffUnavailable() from exc
