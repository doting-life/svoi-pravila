"""Valkey dialog-state store: C0/C1 JSON, no labels or message text."""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis

from svoi_pravila.application.ports.dialog_state import DialogRecord, DialogStep
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId


class ValkeyDialogState:
    """GET/SET/DEL of a compact dialog record under ``tg:dialog:{pseudonym}``."""

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int,
        key_prefix: str = "tg:dialog",
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _key(self, pseudonym: str) -> str:
        return f"{self._key_prefix}:{pseudonym}"

    async def get(self, pseudonym: str) -> DialogRecord | None:
        """Return the parsed record, or None when the key is missing."""
        raw = await self._client.get(self._key(pseudonym))
        if raw is None:
            return None
        text = raw if isinstance(raw, str) else raw.decode()
        return parse_dialog_record(text)

    async def set(self, pseudonym: str, record: DialogRecord) -> None:
        """Store the record with the configured TTL."""
        await self._client.set(
            self._key(pseudonym),
            serialize_dialog_record(record),
            ex=self._ttl_seconds,
        )

    async def clear(self, pseudonym: str) -> None:
        """Drop the key."""
        await self._client.delete(self._key(pseudonym))


def serialize_dialog_record(record: DialogRecord) -> str:
    """Encode C0/C1 fields only."""
    payload: dict[str, str] = {"step": record.step}
    if record.contact_id is not None:
        payload["contact_id"] = str(record.contact_id)
    if record.relationship is not None:
        payload["relationship"] = record.relationship.value
    return json.dumps(payload, separators=(",", ":"))


def parse_dialog_record(raw: str) -> DialogRecord:
    """Decode a stored dialog value; reject unexpected types."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = "dialog record is not JSON"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = "dialog record must be an object"
        raise TypeError(msg)
    step = _parse_step(payload.get("step"))
    contact_raw = payload.get("contact_id")
    contact_id: ContactId | None = None
    if contact_raw is not None:
        try:
            contact_id = ContactId(uuid.UUID(str(contact_raw)))
        except ValueError as exc:
            msg = "dialog contact_id must be a UUID"
            raise ValueError(msg) from exc
    relationship_raw = payload.get("relationship")
    relationship: RelationshipKind | None = None
    if relationship_raw is not None:
        try:
            relationship = RelationshipKind(str(relationship_raw))
        except ValueError as exc:
            msg = "dialog relationship is invalid"
            raise ValueError(msg) from exc
    return DialogRecord(step=step, contact_id=contact_id, relationship=relationship)


def _parse_step(raw: object) -> DialogStep:
    if raw == "awaiting_label":
        return "awaiting_label"
    if raw == "awaiting_rename":
        return "awaiting_rename"
    msg = f"unknown dialog step {raw!r}"
    raise ValueError(msg)
