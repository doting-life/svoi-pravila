"""Valkey single-use compose choice tokens (HMAC-bound; C0 payload only)."""

from __future__ import annotations

import base64
import hmac
import secrets

from redis.asyncio import Redis

from svoi_pravila.application.errors import ComposeChoiceTokenInvalid
from svoi_pravila.application.ports.compose_choice_tokens import (
    COMPOSE_CHOICE_FIRMNESS_SET,
    COMPOSE_CHOICE_TTL_SECONDS,
)
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.domain.enums import UsageScenario

_PURPOSE = "compose_choice"
_PREFIX = "c_"
_ID_LEN = 16
_MAC_LEN = 32


class ValkeyComposeChoiceTokens:
    """Opaque ``c_`` tokens: HMAC over user, scenario, firmness set; Valkey GETDEL."""

    def __init__(
        self,
        client: Redis,
        pseudonymizer: Pseudonymizer,
        *,
        ttl_seconds: int = COMPOSE_CHOICE_TTL_SECONDS,
        key_prefix: str = "compose_choice",
    ) -> None:
        if ttl_seconds < 1 or ttl_seconds > COMPOSE_CHOICE_TTL_SECONDS:
            msg = f"ttl_seconds must be in 1..{COMPOSE_CHOICE_TTL_SECONDS}"
            raise ValueError(msg)
        self._client = client
        self._pseudonymizer = pseudonymizer
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _redis_key(self, token_id: bytes) -> str:
        return f"{self._key_prefix}:{token_id.hex()}"

    def _mac_hex(self, token_id: bytes, user_pseudonym: str, scenario: UsageScenario) -> str:
        value = (
            f"{token_id.hex()}\0{user_pseudonym}\0{scenario.value}\0{COMPOSE_CHOICE_FIRMNESS_SET}"
        )
        return self._pseudonymizer.pseudonymize(_PURPOSE, value)

    def _encode(self, token_id: bytes, mac_hex: str) -> str:
        packed = token_id + bytes.fromhex(mac_hex)
        body = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
        return _PREFIX + body

    def _decode(self, token: str) -> tuple[bytes, bytes]:
        if not token.startswith(_PREFIX):
            raise ComposeChoiceTokenInvalid()
        body = token[len(_PREFIX) :]
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        except (ValueError, TypeError) as exc:
            raise ComposeChoiceTokenInvalid() from exc
        if len(raw) != _ID_LEN + _MAC_LEN:
            raise ComposeChoiceTokenInvalid()
        return raw[:_ID_LEN], raw[_ID_LEN:]

    async def issue(self, user_pseudonym: str, scenario: UsageScenario) -> str:
        """Store scenario under a fresh id and return the opaque signed token."""
        token_id = secrets.token_bytes(_ID_LEN)
        mac_hex = self._mac_hex(token_id, user_pseudonym, scenario)
        await self._client.set(
            self._redis_key(token_id),
            scenario.value,
            ex=self._ttl_seconds,
        )
        return self._encode(token_id, mac_hex)

    async def redeem_once(self, user_pseudonym: str, token: str) -> UsageScenario:
        """Consume the token for ``user_pseudonym`` and return its scenario."""
        token_id, mac_bytes = self._decode(token)
        key = self._redis_key(token_id)
        stored = await self._client.get(key)
        if stored is None:
            raise ComposeChoiceTokenInvalid()
        raw = stored if isinstance(stored, str) else stored.decode("ascii")
        try:
            scenario = UsageScenario(raw)
        except ValueError as exc:
            raise ComposeChoiceTokenInvalid() from exc
        expected = bytes.fromhex(self._mac_hex(token_id, user_pseudonym, scenario))
        if not hmac.compare_digest(mac_bytes, expected):
            raise ComposeChoiceTokenInvalid()
        deleted = await self._client.delete(key)
        if deleted == 0:
            raise ComposeChoiceTokenInvalid()
        return scenario
