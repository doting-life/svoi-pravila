"""Migration d1e2f3a4b5c6: ``users.active_contact_id`` is dropped and restorable."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from svoi_pravila.config import Settings
from tests.support.postgres import (
    create_temporary_database,
    downgrade_to,
    drop_temporary_database,
    upgrade_head,
    upgrade_to,
)

_BEFORE = "c7d8e9f0a1b2"
_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_USER = UUID(int=1)
_CONTACT = UUID(int=2)


async def _run(url: str, action: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await action(conn)
    finally:
        await engine.dispose()


async def _seed_user_with_active_contact(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "INSERT INTO users (id, telegram_user_id, created_at, version) VALUES (:id, 1, :now, 1)"
        ),
        {"id": _USER, "now": _NOW},
    )
    await conn.execute(
        text(
            "INSERT INTO contacts (id, owner_id, label_ciphertext, relationship, "
            "created_at, version) VALUES (:id, :owner, :label, 'friend', :now, 1)"
        ),
        {"id": _CONTACT, "owner": _USER, "label": b"x", "now": _NOW},
    )
    await conn.execute(
        text("UPDATE users SET active_contact_id = :contact WHERE id = :id"),
        {"contact": _CONTACT, "id": _USER},
    )


async def _user_columns(conn: AsyncConnection) -> list[str]:
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users' ORDER BY column_name"
        )
    )
    return list(result.scalars().all())


async def _user_count(conn: AsyncConnection) -> int:
    return int((await conn.execute(text("SELECT count(*) FROM users"))).scalar_one())


async def _restored_active_contact(conn: AsyncConnection) -> UUID | None:
    value = (await conn.execute(text("SELECT active_contact_id FROM users"))).scalar_one()
    return value if value is None else UUID(str(value))


@pytest.mark.integration
def test_upgrade_drops_column_and_downgrade_restores_it_empty(settings: Settings) -> None:
    url, name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_to(_BEFORE, sqlalchemy_url=url)
        asyncio.run(_run(url, _seed_user_with_active_contact))

        upgrade_head(sqlalchemy_url=url)
        assert "active_contact_id" not in asyncio.run(_run(url, _user_columns))
        assert asyncio.run(_run(url, _user_count)) == 1

        downgrade_to(_BEFORE, sqlalchemy_url=url)
        assert "active_contact_id" in asyncio.run(_run(url, _user_columns))
        assert asyncio.run(_run(url, _restored_active_contact)) is None
    finally:
        asyncio.run(drop_temporary_database(settings, name))
