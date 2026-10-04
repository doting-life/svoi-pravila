"""Usage-event sink persistence round-trip."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.adapters.persistence.usage_sink import UnitOfWorkUsageEventSink
from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface
from svoi_pravila.domain.ids import UsageEventId
from svoi_pravila.domain.usage import UsageEvent

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.integration
async def test_usage_event_sink_commits_c0_row(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    engine: AsyncEngine,
) -> None:
    event = UsageEvent(
        id=UsageEventId(UUID(int=80)),
        occurred_at=NOW,
        user_pseudonym="cd" * 32,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.OK,
        unavailable_kind=None,
        safety="ok",
        model="fake",
        prompt_version="decode@v1",
        latency_ms=8,
        ttfc_ms=1,
        attempts=2,
        input_tokens=3,
        output_tokens=4,
        billable_tokens=7,
    )
    await UnitOfWorkUsageEventSink(uow_factory).record(event)
    async with uow_factory() as uow:
        loaded = await uow.usage_events.get(event.id)
        assert loaded == event
    async with engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'usage_events'"
                    )
                )
            )
            .scalars()
            .all()
        )
        forbidden = {"text", "telegram_user_id", "contact_id", "rule_id"}
        assert forbidden.isdisjoint(set(columns))
        assert "event_kind" in columns
        assert "variant_firmness" in columns


@pytest.mark.integration
async def test_usage_event_sink_commits_screened_row(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    event = UsageEvent(
        id=UsageEventId(UUID(int=81)),
        occurred_at=NOW,
        user_pseudonym="ab" * 32,
        scenario=UsageScenario.DECODE,
        surface=UsageSurface.DM,
        outcome=UsageOutcome.SCREENED,
        unavailable_kind=None,
        safety="crisis",
        model=None,
        prompt_version=None,
        latency_ms=0,
        ttfc_ms=None,
        attempts=0,
        input_tokens=0,
        output_tokens=0,
        billable_tokens=0,
    )
    await UnitOfWorkUsageEventSink(uow_factory).record(event)
    async with uow_factory() as uow:
        loaded = await uow.usage_events.get(event.id)
        assert loaded == event
        assert loaded.outcome is UsageOutcome.SCREENED
