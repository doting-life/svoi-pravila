"""Migration c7d8e9f0a1b2 against rows written under the old firmness vocabulary."""

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

_BEFORE = "b3c4d5e6f7a8"
_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_USER = UUID(int=1)
_CONTACT = UUID(int=2)
_PSEUDONYM = "a" * 64


async def _run(url: str, action: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            return await action(conn)
    finally:
        await engine.dispose()


async def _seed_old_rows(conn: AsyncConnection) -> None:
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
        text(
            'INSERT INTO tone_signals (user_id, contact_id, "values") '
            "VALUES (:user, :contact, ARRAY['gentle', 'balanced', 'firm'])"
        ),
        {"user": _USER, "contact": _CONTACT},
    )
    for index, firmness in enumerate(("gentle", "balanced", "firm"), start=10):
        await conn.execute(
            text(
                "INSERT INTO rule_suggestions (id, user_id, contact_id, source, category, "
                "text_ciphertext, firmness, status, created_at, decided_at, version) "
                "VALUES (:id, :user, :contact, 'tone', 'other', :cipher, :firmness, "
                "'accepted', :now, :now, 1)"
            ),
            {
                "id": UUID(int=index),
                "user": _USER,
                "contact": _CONTACT,
                "cipher": b"x",
                "firmness": firmness,
                "now": _NOW,
            },
        )
    scenarios: tuple[tuple[str, str | None], ...] = (
        ("soften", None),
        ("help_say", None),
        ("decode", None),
        ("soften", "gentle"),
        ("help_say", "balanced"),
        ("soften", "firm"),
    )
    for index, (scenario, old_value) in enumerate(scenarios, start=20):
        chosen = old_value is not None
        await conn.execute(
            text(
                "INSERT INTO usage_events (id, occurred_at, user_pseudonym, scenario, "
                "surface, outcome, safety, model, prompt_version, latency_ms, attempts, "
                "input_tokens, output_tokens, billable_tokens, event_kind, variant_firmness) "
                "VALUES (:id, :now, :pseudonym, :scenario, 'inline', 'ok', :safety, "
                ":model, :prompt, :latency, :attempts, 0, 0, 0, :kind, :firmness)"
            ),
            {
                "id": UUID(int=index),
                "now": _NOW,
                "pseudonym": _PSEUDONYM,
                "scenario": scenario,
                "safety": None if chosen else "ok",
                "model": None if chosen else "m",
                "prompt": None if chosen else "p@v1",
                "latency": 0 if chosen else 5,
                "attempts": 0 if chosen else 1,
                "kind": "result_chosen" if chosen else "generation",
                "firmness": old_value,
            },
        )
    await conn.execute(
        text(
            "INSERT INTO analytics_daily_scenario (day, scenario, surface, appeals, users, ok, "
            "refused, screened, invalid_output, unavailable, chosen, limited_user_quota, "
            "limited_global_budget, input_tokens, output_tokens, billable_tokens) "
            "VALUES (:day, :scenario, 'inline', 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)"
        ),
        [
            {"day": _NOW.date(), "scenario": "soften"},
            {"day": _NOW.date(), "scenario": "decode"},
        ],
    )


async def _read_new(conn: AsyncConnection) -> dict[str, Any]:
    tone = (await conn.execute(text('SELECT "values" FROM tone_signals'))).scalar_one()
    suggestions = (
        (
            await conn.execute(
                text("SELECT variant_kind FROM rule_suggestions ORDER BY variant_kind")
            )
        )
        .scalars()
        .all()
    )
    usage = (
        await conn.execute(
            text(
                "SELECT scenario, variant_kind FROM usage_events "
                "ORDER BY scenario, variant_kind NULLS FIRST"
            )
        )
    ).all()
    analytics = (
        (await conn.execute(text("SELECT scenario FROM analytics_daily_scenario"))).scalars().all()
    )
    return {
        "tone": tone,
        "suggestions": list(suggestions),
        "usage": [tuple(row) for row in usage],
        "analytics": list(analytics),
    }


@pytest.mark.integration
def test_upgrade_maps_old_rows_and_downgrade_restores_vocabulary(settings: Settings) -> None:
    url, name = asyncio.run(create_temporary_database(settings))
    try:
        upgrade_to(_BEFORE, sqlalchemy_url=url)
        asyncio.run(_run(url, _seed_old_rows))

        upgrade_head(sqlalchemy_url=url)
        migrated = asyncio.run(_run(url, _read_new))
        assert migrated["tone"] == ["softer", "firmer"]
        assert migrated["suggestions"] == ["firmer", "softer"]
        assert migrated["usage"] == [
            ("decode", None),
            ("rewrite", None),
            ("rewrite", None),
            ("rewrite", "firmer"),
            ("rewrite", "softer"),
        ]
        assert migrated["analytics"] == ["decode"]

        downgrade_to(_BEFORE, sqlalchemy_url=url)
        restored = asyncio.run(_run(url, _read_old))
        assert restored["tone"] == ["gentle", "firm"]
        assert restored["suggestions"] == ["firm", "gentle"]
        assert restored["scenarios"] == ["decode", "soften", "soften", "soften", "soften"]
    finally:
        asyncio.run(drop_temporary_database(settings, name))


async def _read_old(conn: AsyncConnection) -> dict[str, Any]:
    tone = (await conn.execute(text('SELECT "values" FROM tone_signals'))).scalar_one()
    suggestions = (
        (await conn.execute(text("SELECT firmness FROM rule_suggestions ORDER BY firmness")))
        .scalars()
        .all()
    )
    scenarios = (
        (await conn.execute(text("SELECT scenario FROM usage_events ORDER BY scenario")))
        .scalars()
        .all()
    )
    return {"tone": tone, "suggestions": list(suggestions), "scenarios": list(scenarios)}
