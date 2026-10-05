"""Valkey one-time rule-source store for the decode «Сделать правилом» button."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import SealedTokenCodec, SealedValkeyStore
from svoi_pravila.application.errors import RuleSourceUnavailable
from svoi_pravila.application.rule_source import (
    RULE_SOURCE_ID_LEN,
    RuleSourcePayload,
    decode_rule_source_payload,
    encode_rule_source_payload,
)

_AAD_VERSION = "rule_source:v1"
_CODEC = SealedTokenCodec(id_len=RULE_SOURCE_ID_LEN, prefix="")


class ValkeyRuleSources:
    """AES-256-GCM rule sources; redeem_once uses GETDEL before decrypt."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "tg:rule_source",
    ) -> None:
        self._store = SealedValkeyStore(
            client,
            ttl_seconds=ttl_seconds,
            key_prefix=key_prefix,
            aad_version=_AAD_VERSION,
            codec=_CODEC,
        )

    async def store(self, user_pseudonym: str, payload: RuleSourcePayload) -> str:
        """Encrypt ``payload`` and return a compact token (fits ``sn:`` ≤ 64 bytes)."""
        return await self._store.store(user_pseudonym, encode_rule_source_payload(payload))

    async def redeem_once(self, user_pseudonym: str, token: str) -> RuleSourcePayload:
        """Delete the Valkey record atomically, then decrypt for the owning user."""
        try:
            plaintext = await self._store.getdel(user_pseudonym, token)
        except LookupError as exc:
            raise RuleSourceUnavailable() from exc
        try:
            return decode_rule_source_payload(plaintext)
        except ValueError as exc:
            raise RuleSourceUnavailable() from exc
