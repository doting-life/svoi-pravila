"""CLI for daily analytics catch-up, bounded backfill, and purge dry-run."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.application.errors import AnalyticsJobName, AnalyticsJobStatus
from svoi_pravila.application.ports.analytics_store import JobRun
from svoi_pravila.application.use_cases.run_daily_analytics import (
    PURGE_BATCH_SIZE,
    RunDailyAnalytics,
    RunDailyAnalyticsPorts,
)
from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import Settings

BACKFILL_MAX_DAYS = 400


def main(argv: list[str] | None = None) -> None:
    """Parse argv and run the selected analytics command."""
    parser = _parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace, Settings], Coroutine[object, object, int]] = args.handler
    settings = load_settings()
    raise SystemExit(asyncio.run(handler(args, settings)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="svoi-pravila-analytics")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="same as the daily in-process job")
    run_cmd.set_defaults(handler=_cmd_run)
    backfill = sub.add_parser("backfill", help="idempotent compute_day over a date range")
    backfill.add_argument("--from", dest="from_day", required=True, type=date.fromisoformat)
    backfill.add_argument("--to", dest="to_day", required=True, type=date.fromisoformat)
    backfill.set_defaults(handler=_cmd_backfill)
    purge = sub.add_parser("purge", help="delete usage_events older than 13 months")
    purge.add_argument("--dry-run", action="store_true", dest="dry_run")
    purge.set_defaults(handler=_cmd_purge)
    return parser


async def _cmd_run(_args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        store = SqlAlchemyAnalyticsStore(engine)
        clock = SystemClock()
        await RunDailyAnalytics(
            RunDailyAnalyticsPorts(
                store=store,
                ids=Uuid7IdGenerator(),
                clock=clock,
                timezone=settings.analytics_timezone,
                llm_budget_tokens=settings.llm_daily_token_budget,
            )
        ).execute(clock.now())
    finally:
        await dispose_engine(engine)
    return 0


async def _cmd_backfill(args: argparse.Namespace, settings: Settings) -> int:
    from_day: date = args.from_day
    to_day: date = args.to_day
    if to_day < from_day:
        print("backfill: --to must be on or after --from", file=sys.stderr)
        return 2
    span = (to_day - from_day).days + 1
    if span > BACKFILL_MAX_DAYS:
        print(f"backfill: range exceeds {BACKFILL_MAX_DAYS} days", file=sys.stderr)
        return 2
    engine = create_engine(settings)
    try:
        store = SqlAlchemyAnalyticsStore(engine)
        clock = SystemClock()
        ids = Uuid7IdGenerator()
        now = clock.now()
        async with store.hold_lock() as acquired:
            if not acquired:
                await store.record_run(
                    JobRun(
                        id=ids.new_id(),
                        job=AnalyticsJobName.DAILY_AGGREGATES,
                        target_day=None,
                        started_at=now,
                        finished_at=now,
                        status=AnalyticsJobStatus.SKIPPED_LOCKED,
                        rows_affected=0,
                        error_kind=None,
                    )
                )
                return 0
            await store.ensure_timezone(settings.analytics_timezone)
            day = from_day
            while day <= to_day:
                rows = await store.compute_day(
                    day, settings.analytics_timezone, now, settings.llm_daily_token_budget
                )
                await store.record_run(
                    JobRun(
                        id=ids.new_id(),
                        job=AnalyticsJobName.DAILY_AGGREGATES,
                        target_day=day,
                        started_at=now,
                        finished_at=now,
                        status=AnalyticsJobStatus.SUCCEEDED,
                        rows_affected=rows,
                        error_kind=None,
                    )
                )
                day = day + timedelta(days=1)
            yesterday = now.astimezone(ZoneInfo(settings.analytics_timezone)).date() - timedelta(
                days=1
            )
            cohort_rows = await store.compute_cohorts(
                from_day,
                to_day,
                settings.analytics_timezone,
                yesterday,
                now,
            )
            await store.record_run(
                JobRun(
                    id=ids.new_id(),
                    job=AnalyticsJobName.COHORTS,
                    target_day=None,
                    started_at=now,
                    finished_at=now,
                    status=AnalyticsJobStatus.SUCCEEDED,
                    rows_affected=cohort_rows,
                    error_kind=None,
                )
            )
    finally:
        await dispose_engine(engine)
    return 0


async def _cmd_purge(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        store = SqlAlchemyAnalyticsStore(engine)
        now = SystemClock().now()
        if args.dry_run:
            print(await store.count_usage_events_older_than(now))
            return 0
        print(await store.purge_usage_events(now, PURGE_BATCH_SIZE))
    finally:
        await dispose_engine(engine)
    return 0
