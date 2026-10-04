"""Valkey dialog-state adapter: serialize/parse and TTL."""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis
from tests.fakes.dialog import FakeDialogState

from svoi_pravila.adapters.cache.dialog_state import (
    ValkeyDialogState,
    parse_dialog_record,
    serialize_dialog_record,
)
from svoi_pravila.application.ports.dialog_state import DialogRecord
from svoi_pravila.domain.enums import RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import ContactId


class _MemClient:
    def __init__(self) -> None:
        self.store: dict[str, str | bytes] = {}
        self.ex: int | None = None

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value
        self.ex = ex

    async def get(self, key: str) -> str | bytes | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.mark.unit
def test_serialize_parse_round_trip() -> None:
    record = DialogRecord(
        step="awaiting_rename",
        contact_id=ContactId(UUID(int=7)),
        relationship=RelationshipKind.FRIEND,
    )
    parsed = parse_dialog_record(serialize_dialog_record(record))
    assert parsed == record
    label_only = DialogRecord(step="awaiting_label", relationship=RelationshipKind.PARTNER)
    assert parse_dialog_record(serialize_dialog_record(label_only)) == label_only
    assert "label" not in serialize_dialog_record(record)
    with_category = DialogRecord(
        step="awaiting_rule_text",
        contact_id=ContactId(UUID(int=8)),
        category=RuleCategory.TABOO_TOPIC,
    )
    encoded = serialize_dialog_record(with_category)
    assert parse_dialog_record(encoded) == with_category
    assert set(json.loads(encoded)) <= {"step", "contact_id", "relationship", "category"}
    assert "label" not in json.loads(encoded)


@pytest.mark.unit
def test_parse_dialog_record_rejects_bad_payloads() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        parse_dialog_record("{")
    with pytest.raises(TypeError, match="object"):
        parse_dialog_record("[]")
    with pytest.raises(ValueError, match="unknown dialog step"):
        parse_dialog_record('{"step":"nope"}')
    with pytest.raises(ValueError, match="UUID"):
        parse_dialog_record('{"step":"awaiting_rename","contact_id":"nope"}')
    with pytest.raises(ValueError, match="relationship"):
        parse_dialog_record('{"step":"awaiting_label","relationship":"nope"}')
    with pytest.raises(ValueError, match="category"):
        parse_dialog_record('{"step":"awaiting_rule_text","category":"nope"}')


@pytest.mark.unit
async def test_valkey_dialog_state_get_set_clear_and_bytes() -> None:
    client = _MemClient()
    store = ValkeyDialogState(cast(Redis, client), ttl_seconds=600)
    missing = await store.get("p")
    assert missing is None
    record = DialogRecord(step="awaiting_label", relationship=RelationshipKind.WORK)
    await store.set("p", record)
    assert client.ex == 600
    assert await store.get("p") == record
    client.store["tg:dialog:p"] = serialize_dialog_record(record).encode()
    assert await store.get("p") == record
    await store.clear("p")
    assert await store.get("p") is None


@pytest.mark.unit
async def test_fake_dialog_state_get_set_clear() -> None:
    store = FakeDialogState()
    assert await store.get("p") is None
    record = DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=3)))
    await store.set("p", record)
    assert await store.get("p") == record
    await store.clear("p")
    assert await store.get("p") is None
