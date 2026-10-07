#!/usr/bin/env python3
"""Seed usage_events and time compute_day / compute_cohorts / purge.

Not imported by pytest. Talks only to the dedicated ``*_test`` database:

    cd backend && uv run python benchmarks/seed_usage_events.py --scale 1m
    cd backend && uv run python benchmarks/seed_usage_events.py --scale 4m
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.adapters.persistence.analytics_store import (
    _COHORT_UPSERT,
    _DAILY_UPSERT,
    _SCENARIO_INSERT,
    SqlAlchemyAnalyticsStore,
)
from svoi_pravila.bootstrap import load_test_infra_settings
from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface

PSEUDONYMS_PER_DAY = 1_000
TZ = "Europe/Moscow"

_SCALES = {
    "1m": (1_000_000, 35),
    "4m": (4_000_000, 140),
}


def _pseudo(index: int) -> str:
    return f"{index:064x}"


def _test_database_url(url: str) -> str:
    parsed = urlparse(url)
    name = parsed.path.lstrip("/").split("/", maxsplit=1)[0]
    if not name:
        raise SystemExit("database URL has no database name")
    if not name.endswith("_test"):
        name = f"{name}_test"
    return urlunparse(parsed._replace(path=f"/{name}"))


async def _seed(engine: AsyncEngine, now: datetime, events: int, days: int) -> None:
    start = now - timedelta(days=days)
    batch: list[dict[str, object]] = []
    remaining = events
    event_id = 1
    while remaining > 0:
        day_index = (events - remaining) % days
        user_index = (events - remaining) % (PSEUDONYMS_PER_DAY * days)
        occurred = start + timedelta(days=day_index, seconds=(events - remaining) % 86_400)
        occurred = occurred.replace(microsecond=0)
        batch.append(
            {
                "id": UUID(int=event_id),
                "occurred_at": occurred,
                "user_pseudonym": _pseudo(user_index + 1),
                "scenario": UsageScenario.DECODE.value,
                "surface": UsageSurface.DM.value,
                "outcome": UsageOutcome.OK.value,
                "unavailable_kind": None,
                "safety": "ok",
                "model": "m",
                "prompt_version": "v1",
                "latency_ms": 10 + (event_id % 50),
                "ttfc_ms": 2,
                "attempts": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "billable_tokens": 2,
                "event_kind": "generation",
                "variant_firmness": None,
            }
        )
        event_id += 1
        remaining -= 1
        if len(batch) >= 5_000 or remaining == 0:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO usage_events ("
                        "id, occurred_at, user_pseudonym, scenario, surface, outcome, "
                        "unavailable_kind, safety, model, prompt_version, latency_ms, ttfc_ms, "
                        "attempts, input_tokens, output_tokens, billable_tokens, event_kind, "
                        "variant_firmness"
                        ") VALUES ("
                        ":id, :occurred_at, :user_pseudonym, :scenario, :surface, :outcome, "
                        ":unavailable_kind, :safety, :model, :prompt_version, :latency_ms, "
                        ":ttfc_ms, :attempts, :input_tokens, :output_tokens, :billable_tokens, "
                        ":event_kind, :variant_firmness"
                        ")"
                    ),
                    batch,
                )
            batch = []


async def _explain(
    engine: AsyncEngine,
    sql: str,
    params: dict[str, object],
    *,
    prelude: str | None = None,
    prelude_params: dict[str, object] | None = None,
) -> str:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            if prelude is not None:
                await conn.execute(text(prelude), prelude_params or {})
            result = await conn.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql), params)
            return "\n".join(row[0] for row in result.all())
        finally:
            await transaction.rollback()


async def _run(scale: str, purge_expired: int) -> None:
    events, days = _SCALES[scale]
    url = _test_database_url(load_test_infra_settings().database_url.get_secret_value())
    engine = create_async_engine(url, pool_pre_ping=True)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE usage_events, analytics_daily, "
                    "analytics_daily_scenario, analytics_cohorts, job_runs"
                )
            )
        print(f"seeding scale={scale} events={events} days={days}...")
        t0 = time.perf_counter()
        await _seed(engine, now, events, days)
        print(f"seed_seconds={time.perf_counter() - t0:.2f}")
        store = SqlAlchemyAnalyticsStore(engine)
        yesterday = now.astimezone(ZoneInfo(TZ)).date() - timedelta(days=1)
        from_day = yesterday - timedelta(days=8)
        to_day = yesterday - timedelta(days=1)
        t1 = time.perf_counter()
        await store.compute_day(yesterday, TZ, now, 100_000)
        print(f"compute_day_seconds={time.perf_counter() - t1:.3f}")
        t2 = time.perf_counter()
        await store.compute_cohorts(from_day, to_day, TZ, yesterday, now)
        print(f"compute_cohorts_seconds={time.perf_counter() - t2:.3f}")
        day_params = {"day": yesterday, "tz": TZ, "computed_at": now, "llm_budget_tokens": 100_000}
        print("explain_compute_day:")
        print(await _explain(engine, _DAILY_UPSERT, day_params))
        print("explain_scenario_insert:")
        print(
            await _explain(
                engine,
                _SCENARIO_INSERT,
                day_params,
                prelude="DELETE FROM analytics_daily_scenario WHERE day = :day",
                prelude_params={"day": yesterday},
            )
        )
        print("explain_compute_cohorts:")
        print(
            await _explain(
                engine,
                _COHORT_UPSERT,
                {
                    "from_day": from_day,
                    "to_day": to_day,
                    "tz": TZ,
                    "as_of": yesterday,
                    "computed_at": now,
                },
            )
        )
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE usage_events SET occurred_at = :old "
                    "WHERE ctid IN (SELECT ctid FROM usage_events LIMIT :n)"
                ),
                {"old": now - timedelta(days=400), "n": purge_expired},
            )
        t3 = time.perf_counter()
        deleted = await store.purge_usage_events(now, 5_000)
        print(f"purge_deleted={deleted} purge_seconds={time.perf_counter() - t3:.3f}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=sorted(_SCALES), default="1m")
    parser.add_argument("--purge-expired", type=int, default=100_000)
    args = parser.parse_args()
    asyncio.run(_run(args.scale, args.purge_expired))


if __name__ == "__main__":
    main()
