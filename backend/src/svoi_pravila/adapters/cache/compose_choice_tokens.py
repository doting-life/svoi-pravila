"""Valkey single-use compose choice tokens (HMAC-bound; scenario and contact id only)."""

from __future__ import annotations

import base64
import hmac
import secrets
from uuid import UUID

from redis.asyncio import Redis

from svoi_pravila.application.errors import ComposeChoiceTokenInvalid
from svoi_pravila.application.ports.compose_choice_tokens import (
    COMPOSE_CHOICE_KIND_SET,
    COMPOSE_CHOICE_TTL_SECONDS,
    ComposeChoiceBinding,
)
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import ContactId

_PURPOSE = "compose_choice"
_PREFIX = "c_"
_ID_LEN = 16
_MAC_LEN = 32
_FIELD_SEPARATOR = "|"


class ValkeyComposeChoiceTokens:
    """Opaque ``c_`` tokens: HMAC over user, scenario, contact, variant-kind set; Valkey GETDEL."""

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

    def _mac_hex(
        self,
        token_id: bytes,
        user_pseudonym: str,
        binding: ComposeChoiceBinding,
    ) -> str:
        contact_raw = _contact_raw(binding.contact_id)
        value = (
            f"{token_id.hex()}\0{user_pseudonym}\0{binding.scenario.value}\0{contact_raw}"
            f"\0{COMPOSE_CHOICE_KIND_SET}"
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

    async def issue(
        self,
        user_pseudonym: str,
        scenario: UsageScenario,
        contact_id: ContactId | None,
    ) -> str:
        """Store scenario and contact under a fresh id and return the opaque signed token."""
        token_id = secrets.token_bytes(_ID_LEN)
        binding = ComposeChoiceBinding(scenario=scenario, contact_id=contact_id)
        mac_hex = self._mac_hex(token_id, user_pseudonym, binding)
        await self._client.set(
            self._redis_key(token_id),
            f"{scenario.value}{_FIELD_SEPARATOR}{_contact_raw(contact_id)}",
            ex=self._ttl_seconds,
        )
        return self._encode(token_id, mac_hex)

    async def redeem_once(self, user_pseudonym: str, token: str) -> ComposeChoiceBinding:
        """Consume the token for ``user_pseudonym`` and return its scenario and contact."""
        token_id, mac_bytes = self._decode(token)
        key = self._redis_key(token_id)
        stored = await self._client.get(key)
        if stored is None:
            raise ComposeChoiceTokenInvalid()
        raw = stored if isinstance(stored, str) else stored.decode("ascii")
        binding = _parse_binding(raw)
        expected = bytes.fromhex(self._mac_hex(token_id, user_pseudonym, binding))
        if not hmac.compare_digest(mac_bytes, expected):
            raise ComposeChoiceTokenInvalid()
        deleted = await self._client.delete(key)
        if deleted == 0:
            raise ComposeChoiceTokenInvalid()
        return binding


def _contact_raw(contact_id: ContactId | None) -> str:
    return "" if contact_id is None else str(contact_id)


def _parse_binding(raw: str) -> ComposeChoiceBinding:
    scenario_raw, sep, contact_raw = raw.partition(_FIELD_SEPARATOR)
    if not sep:
        raise ComposeChoiceTokenInvalid()
    try:
        scenario = UsageScenario(scenario_raw)
        contact_id = ContactId(UUID(contact_raw)) if contact_raw else None
    except ValueError as exc:
        raise ComposeChoiceTokenInvalid() from exc
    return ComposeChoiceBinding(scenario=scenario, contact_id=contact_id)
