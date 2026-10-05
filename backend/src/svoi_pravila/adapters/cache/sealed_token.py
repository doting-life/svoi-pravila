"""Shared AES-256-GCM sealed tokens: key in the token, ciphertext in Valkey."""

from __future__ import annotations

import base64
import secrets

from redis.asyncio import Redis

from svoi_pravila.crypto.cipher import FieldCipher
from svoi_pravila.crypto.errors import DecryptionError

KEY_LEN = 32
MIN_ID_LEN = 8


class SealedTokenCodec:
    """Encode/decode ``id || key`` into a URL-safe token body (optional prefix)."""

    def __init__(self, *, id_len: int, prefix: str = "") -> None:
        if id_len < MIN_ID_LEN:
            msg = "sealed token id_len must be at least 8"
            raise ValueError(msg)
        self.id_len = id_len
        self.prefix = prefix

    def mint(self) -> tuple[bytes, bytes, str]:
        """Return ``(token_id, key, token_string)``."""
        token_id = secrets.token_bytes(self.id_len)
        key = secrets.token_bytes(KEY_LEN)
        packed = base64.urlsafe_b64encode(token_id + key).decode("ascii").rstrip("=")
        return token_id, key, self.prefix + packed

    def parse(self, token: str) -> tuple[bytes, bytes]:
        """Split a token into ``(token_id, key)`` or raise ``ValueError``."""
        if self.prefix:
            if not token.startswith(self.prefix):
                msg = "token prefix mismatch"
                raise ValueError(msg)
            body = token[len(self.prefix) :]
        else:
            body = token
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if len(raw) != self.id_len + KEY_LEN:
            msg = "token length mismatch"
            raise ValueError(msg)
        return raw[: self.id_len], raw[self.id_len :]


def seal_aad(*, aad_version: str, token_id: bytes, user_pseudonym: str) -> bytes:
    """Build AAD binding purpose version, token id and user pseudonym."""
    return f"{aad_version}:{token_id.hex()}:{user_pseudonym}".encode()


def encrypt_plaintext(*, key: bytes, plaintext: bytes, aad: bytes) -> str:
    """Return ASCII base64 of AES-GCM ciphertext for Valkey storage."""
    blob = FieldCipher(key).encrypt(plaintext, aad=aad)
    return base64.b64encode(blob).decode("ascii")


def decrypt_stored(*, key: bytes, stored: str, aad: bytes) -> bytes:
    """Decrypt a Valkey base64 blob; raises ``ValueError`` / ``DecryptionError``."""
    blob = base64.b64decode(stored, validate=True)
    return FieldCipher(key).decrypt(blob, aad=aad)


class SealedValkeyStore:
    """Low-level Valkey ciphertext store keyed by token id hex."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str,
        aad_version: str,
        codec: SealedTokenCodec,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._aad_version = aad_version
        self._codec = codec

    def _redis_key(self, token_id: bytes) -> str:
        return f"{self._key_prefix}:{token_id.hex()}"

    async def store(self, user_pseudonym: str, plaintext: bytes) -> str:
        """Encrypt ``plaintext`` and return the sealed token string."""
        token_id, key, token = self._codec.mint()
        aad = seal_aad(
            aad_version=self._aad_version,
            token_id=token_id,
            user_pseudonym=user_pseudonym,
        )
        stored = encrypt_plaintext(key=key, plaintext=plaintext, aad=aad)
        await self._client.set(self._redis_key(token_id), stored, ex=self._ttl_seconds)
        return token

    async def get(self, user_pseudonym: str, token: str) -> bytes:
        """Decrypt without deleting; miss/tamper → ``LookupError``."""
        try:
            token_id, key = self._codec.parse(token)
        except (ValueError, TypeError) as exc:
            raise LookupError from exc
        stored = await self._client.get(self._redis_key(token_id))
        if stored is None:
            raise LookupError
        try:
            return decrypt_stored(
                key=key,
                stored=stored if isinstance(stored, str) else stored.decode("ascii"),
                aad=seal_aad(
                    aad_version=self._aad_version,
                    token_id=token_id,
                    user_pseudonym=user_pseudonym,
                ),
            )
        except (ValueError, TypeError, DecryptionError) as exc:
            raise LookupError from exc

    async def getdel(self, user_pseudonym: str, token: str) -> bytes:
        """Atomically fetch-and-delete ciphertext, then decrypt (single-use)."""
        try:
            token_id, key = self._codec.parse(token)
        except (ValueError, TypeError) as exc:
            raise LookupError from exc
        stored = await self._client.getdel(self._redis_key(token_id))
        if stored is None:
            raise LookupError
        try:
            return decrypt_stored(
                key=key,
                stored=stored if isinstance(stored, str) else stored.decode("ascii"),
                aad=seal_aad(
                    aad_version=self._aad_version,
                    token_id=token_id,
                    user_pseudonym=user_pseudonym,
                ),
            )
        except (ValueError, TypeError, DecryptionError) as exc:
            raise LookupError from exc

    async def delete(self, token: str) -> None:
        """Drop ciphertext by token id (after a successful same-user redeem)."""
        try:
            token_id, _key = self._codec.parse(token)
        except (ValueError, TypeError) as exc:
            raise LookupError from exc
        await self._client.delete(self._redis_key(token_id))
