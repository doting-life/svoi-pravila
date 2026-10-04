"""Valkey prepared-result store: ciphertext only, key lives in the token."""

from __future__ import annotations

import base64
import secrets

from redis.asyncio import Redis

from svoi_pravila.application.errors import PreparedResultUnavailable
from svoi_pravila.application.ports.prepared_results import PreparedVariant
from svoi_pravila.application.prepared_token import (
    TOKEN_PREFIX,
    is_prepared_token,
)
from svoi_pravila.crypto.cipher import FieldCipher
from svoi_pravila.crypto.errors import DecryptionError
from svoi_pravila.domain.enums import Firmness

_ID_LEN = 16
_KEY_LEN = 32
_AAD_VERSION = "prepared:v1"


class ValkeyPreparedResults:
    """AES-256-GCM prepared variants; Valkey never sees plaintext or the key."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "tg:prepared",
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    async def store(self, user_pseudonym: str, variant: PreparedVariant) -> str:
        """Encrypt ``variant`` under a random key and return a ``p_`` token."""
        token_id = secrets.token_bytes(_ID_LEN)
        key = secrets.token_bytes(_KEY_LEN)
        token = _encode_token(token_id, key)
        plaintext = f"{variant.firmness.value}\n{variant.text}".encode()
        blob = FieldCipher(key).encrypt(plaintext, aad=_aad(token_id, user_pseudonym))
        stored = base64.b64encode(blob).decode("ascii")
        await self._client.set(self._redis_key(token_id), stored, ex=self._ttl_seconds)
        return token

    async def redeem(self, user_pseudonym: str, token: str) -> PreparedVariant:
        """Decrypt for the owning user; leave the ciphertext until delete/TTL."""
        token_id, key = _decode_token(token)
        stored = await self._client.get(self._redis_key(token_id))
        if stored is None:
            raise PreparedResultUnavailable()
        try:
            blob = base64.b64decode(stored, validate=True)
        except (ValueError, TypeError) as exc:
            raise PreparedResultUnavailable() from exc
        try:
            plaintext = FieldCipher(key).decrypt(blob, aad=_aad(token_id, user_pseudonym))
        except (ValueError, DecryptionError) as exc:
            raise PreparedResultUnavailable() from exc
        return _parse_plaintext(plaintext)

    async def delete(self, user_pseudonym: str, token: str) -> None:
        """Drop the ciphertext after a successful same-user decrypt."""
        token_id, _key = _decode_token(token)
        await self.redeem(user_pseudonym, token)
        await self._client.delete(self._redis_key(token_id))

    def _redis_key(self, token_id: bytes) -> str:
        return f"{self._key_prefix}:{token_id.hex()}"


def _aad(token_id: bytes, user_pseudonym: str) -> bytes:
    return f"{_AAD_VERSION}:{token_id.hex()}:{user_pseudonym}".encode()


def _encode_token(token_id: bytes, key: bytes) -> str:
    return TOKEN_PREFIX + base64.urlsafe_b64encode(token_id + key).decode("ascii").rstrip("=")


def _decode_token(token: str) -> tuple[bytes, bytes]:
    if not is_prepared_token(token):
        raise PreparedResultUnavailable()
    body = token[len(TOKEN_PREFIX) :]
    raw = base64.urlsafe_b64decode(body)
    return raw[:_ID_LEN], raw[_ID_LEN:]


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
