"""Migration c5d6e7f8a9b0: prices seed, new aggregates, reader grants, working downgrade."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

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

_BEFORE = "d1e2f3a4b5c6"
_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_READER = "svoi_analytics_read"
_NEW_TABLES = ("analytics_daily_llm", "analytics_daily_load", "llm_prices")
_NEW_DAILY_COLUMNS = {"llm_calls", "target_users_total", "llm_cost_rub", "unpriced_models"}


async def _run(url: str, action: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await action(conn)
    finally:
        await engine.dispose()


async def _seed_old_daily_row(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "INSERT INTO analytics_daily (day, active_users, appeals, new_users, generations, "
            "generation_errors, users_limited, billable_tokens, llm_budget_tokens, computed_at) "
            "VALUES (DATE '2026-09-30', 2, 2, 1, 3, 0, 0, 90, 1000, :now)"
        ),
        {"now": _NOW},
    )


async def _tables(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    )
    return set(result.scalars().all())


async def _daily_columns(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'analytics_daily' AND table_schema = current_schema()"
        )
    )
    return set(result.scalars().all())


async def _seeded_prices(conn: AsyncConnection) -> dict[str, tuple[date, Decimal]]:
    result = await conn.execute(text("SELECT model, valid_from, rub_per_1k_tokens FROM llm_prices"))
    return {row[0]: (row[1], row[2]) for row in result.all()}


async def _backfilled_row(conn: AsyncConnection) -> tuple[Any, ...]:
    row = (
        await conn.execute(
            text(
                "SELECT llm_calls, target_users_total, llm_cost_rub, unpriced_models "
                "FROM analytics_daily WHERE day = DATE '2026-09-30'"
            )
        )
    ).one()
    return tuple(row)


async def _reader_privileges(conn: AsyncConnection) -> dict[str, bool]:
    names = ("analytics_daily", *_NEW_TABLES, "usage_events")
    return {
        name: bool(
            (
                await conn.execute(
                    text("SELECT has_table_privilege(:role, :tbl, 'SELECT')"),
                    {"role": _READER, "tbl": name},
                )
            ).scalar_one()
        )
        for name in names
    }


async def _reader_can_write_new_tables(conn: AsyncConnection) -> list[str]:
    writable = []
    for name in _NEW_TABLES:
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            allowed = (
                await conn.execute(
                    text("SELECT has_table_privilege(:role, :tbl, :priv)"),
                    {"role": _READER, "tbl": name, "priv": privilege},
                )
            ).scalar_one()
            if allowed:
                writable.append(f"{name}:{privilege}")
    return writable


@pytest.mark.integration
def test_upgrade_adds_tables_seeds_prices_and_keeps_old_rows_valid(settings: Settings) -> None:
    url, name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_to(_BEFORE, sqlalchemy_url=url)
        assert not set(_NEW_TABLES) & asyncio.run(_run(url, _tables))
        asyncio.run(_run(url, _seed_old_daily_row))

        upgrade_head(sqlalchemy_url=url)

        assert set(_NEW_TABLES) <= asyncio.run(_run(url, _tables))
        assert asyncio.run(_run(url, _daily_columns)) >= _NEW_DAILY_COLUMNS
        assert asyncio.run(_run(url, _backfilled_row)) == (0, 0, Decimal("0.0000"), 0)
        assert asyncio.run(_run(url, _seeded_prices)) == {
            "GigaChat-3-Lightning": (date(2026, 9, 29), Decimal("0.0650")),
            "GigaChat-2-Pro": (date(2026, 9, 29), Decimal("0.5000")),
            "GigaChat-3-Pro": (date(2026, 9, 29), Decimal("0.5000")),
            "GigaChat-2-Max": (date(2026, 9, 29), Decimal("0.6500")),
        }
    finally:
        asyncio.run(drop_temporary_database(settings, name))


@pytest.mark.integration
def test_reader_role_selects_the_aggregates_only(settings: Settings) -> None:
    url, name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_head(sqlalchemy_url=url)
        assert asyncio.run(_run(url, _reader_privileges)) == {
            "analytics_daily": True,
            "analytics_daily_llm": True,
            "analytics_daily_load": True,
            "llm_prices": True,
            "usage_events": False,
        }
        assert asyncio.run(_run(url, _reader_can_write_new_tables)) == []
    finally:
        asyncio.run(drop_temporary_database(settings, name))


@pytest.mark.integration
def test_downgrade_removes_tables_and_columns_and_upgrade_works_again(settings: Settings) -> None:
    url, name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_head(sqlalchemy_url=url)
        downgrade_to(_BEFORE, sqlalchemy_url=url)
        assert not set(_NEW_TABLES) & asyncio.run(_run(url, _tables))
        assert not _NEW_DAILY_COLUMNS & asyncio.run(_run(url, _daily_columns))
        upgrade_head(sqlalchemy_url=url)
        assert set(_NEW_TABLES) <= asyncio.run(_run(url, _tables))
    finally:
        asyncio.run(drop_temporary_database(settings, name))
