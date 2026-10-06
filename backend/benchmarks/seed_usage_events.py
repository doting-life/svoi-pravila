#!/usr/bin/env python3
"""Seed 1_000_000 usage_events and time compute_day / compute_cohorts / purge.

Not imported by pytest. Talks only to the dedicated ``*_test`` database:

    cd backend && uv run python benchmarks/seed_usage_events.py
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

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.bootstrap import load_test_infra_settings
from svoi_pravila.domain.enums import UsageOutcome, UsageScenario, UsageSurface

DAYS = 35
PSEUDONYMS_PER_DAY = 1_000
EVENTS = 1_000_000
TZ = "Europe/Moscow"


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


async def _seed(engine: AsyncEngine, now: datetime) -> None:
    start = now - timedelta(days=DAYS)
    batch: list[dict[str, object]] = []
    remaining = EVENTS
    event_id = 1
    while remaining > 0:
        day_index = (EVENTS - remaining) % DAYS
        user_index = (EVENTS - remaining) % (PSEUDONYMS_PER_DAY * DAYS)
        occurred = start + timedelta(days=day_index, seconds=(EVENTS - remaining) % 86_400)
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


async def _explain(engine: AsyncEngine, sql: str, params: dict[str, object]) -> str:
    async with engine.connect() as conn:
        result = await conn.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql), params)
        return "\n".join(row[0] for row in result.all())


async def _run(purge_expired: int) -> None:
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
        print(f"seeding {EVENTS} events...")
        t0 = time.perf_counter()
        await _seed(engine, now)
        print(f"seed_seconds={time.perf_counter() - t0:.2f}")
        store = SqlAlchemyAnalyticsStore(engine)
        yesterday = now.astimezone(ZoneInfo(TZ)).date() - timedelta(days=1)
        t1 = time.perf_counter()
        await store.compute_day(yesterday, TZ, now)
        print(f"compute_day_seconds={time.perf_counter() - t1:.3f}")
        t2 = time.perf_counter()
        await store.compute_cohorts(
            yesterday - timedelta(days=8), yesterday - timedelta(days=1), TZ, yesterday, now
        )
        print(f"compute_cohorts_seconds={time.perf_counter() - t2:.3f}")
        day_sql = (
            "WITH bounds AS ("
            "SELECT timezone(:tz, CAST(:day AS timestamp without time zone)) AS start_ts, "
            "timezone(:tz, CAST(:day AS timestamp without time zone) + interval '1 day') "
            "AS end_ts) "
            "SELECT COUNT(*) FROM usage_events e, bounds b "
            "WHERE e.occurred_at >= b.start_ts AND e.occurred_at < b.end_ts"
        )
        print("explain_compute_day:")
        print(await _explain(engine, day_sql, {"day": yesterday, "tz": TZ}))
        cohort_sql = (
            "SELECT COUNT(DISTINCT user_pseudonym) FROM usage_events "
            "WHERE scenario <> 'suggest_rule' AND ("
            "event_kind = 'result_chosen' OR ("
            "event_kind = 'generation' AND surface IN ('dm', 'miniapp') "
            "AND outcome IN ('ok', 'refused', 'screened')))"
        )
        print("explain_compute_cohorts:")
        print(await _explain(engine, cohort_sql, {}))
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
    parser.add_argument("--purge-expired", type=int, default=100_000)
    args = parser.parse_args()
    asyncio.run(_run(args.purge_expired))


if __name__ == "__main__":
    main()
