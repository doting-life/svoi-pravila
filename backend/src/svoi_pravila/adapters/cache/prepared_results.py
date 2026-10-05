"""Valkey prepared-result store: ciphertext only, key lives in the token."""

from __future__ import annotations

from redis.asyncio import Redis

from svoi_pravila.adapters.cache.sealed_token import SealedTokenCodec, SealedValkeyStore
from svoi_pravila.application.errors import PreparedResultUnavailable
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.prepared_ref import PREPARED_REF_PREFIX, is_prepared_ref
from svoi_pravila.domain.enums import Firmness

_ID_LEN = 16
_AAD_VERSION = "prepared:v1"
_CODEC = SealedTokenCodec(id_len=_ID_LEN, prefix=PREPARED_REF_PREFIX)


class ValkeyPreparedResults:
    """AES-256-GCM prepared variants; Valkey never sees plaintext or the key."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "tg:prepared",
    ) -> None:
        self._store = SealedValkeyStore(
            client,
            ttl_seconds=ttl_seconds,
            key_prefix=key_prefix,
            aad_version=_AAD_VERSION,
            codec=_CODEC,
        )

    async def store(self, user_pseudonym: str, variant: PreparedVariant) -> str:
        """Encrypt ``variant`` under a random key and return a ``p_`` token."""
        plaintext = f"{variant.firmness.value}\n{variant.text}".encode()
        return await self._store.store(user_pseudonym, plaintext)

    async def redeem(self, user_pseudonym: str, token: str) -> PreparedVariant:
        """Decrypt for the owning user; leave the ciphertext until delete/TTL."""
        if not is_prepared_ref(token):
            raise PreparedResultUnavailable()
        try:
            plaintext = await self._store.get(user_pseudonym, token)
        except LookupError as exc:
            raise PreparedResultUnavailable() from exc
        return _parse_plaintext(plaintext)

    async def delete(self, user_pseudonym: str, token: str) -> None:
        """Drop the ciphertext after a successful same-user decrypt."""
        await self.redeem(user_pseudonym, token)
        await self._store.delete(token)


def _parse_plaintext(plaintext: bytes) -> PreparedVariant:
    try:
        decoded = plaintext.decode()
    except UnicodeDecodeError as exc:
        raise PreparedResultUnavailable() from exc
    firmness_raw, separator, text = decoded.partition("\n")
    if not separator:
        raise PreparedResultUnavailable()
    try:
        firmness = Firmness(firmness_raw)
    except ValueError as exc:
        raise PreparedResultUnavailable() from exc
    return PreparedVariant(firmness=firmness, text=text)
