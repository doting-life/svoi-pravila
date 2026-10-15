"""CLI for daily analytics catch-up, backfill, purge, LLM prices and the metrics export."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.program_metrics_store import SqlAlchemyProgramMetricsStore
from svoi_pravila.adapters.system.clock import SystemClock
from svoi_pravila.adapters.system.csv_metrics_package import CsvMetricsPackageWriter
from svoi_pravila.adapters.system.ids import Uuid7IdGenerator
from svoi_pravila.application.errors import (
    AnalyticsJobName,
    AnalyticsJobStatus,
    ProgramExportErrorKind,
    ProgramExportFailed,
)
from svoi_pravila.application.ports.analytics_store import JobRun
from svoi_pravila.application.use_cases.export_program_metrics import (
    ExportProgramMetrics,
    ExportProgramMetricsInput,
    ExportProgramMetricsPorts,
)
from svoi_pravila.application.use_cases.list_llm_prices import ListLlmPrices
from svoi_pravila.application.use_cases.run_daily_analytics import (
    PURGE_BATCH_SIZE,
    RunDailyAnalytics,
    RunDailyAnalyticsPorts,
)
from svoi_pravila.application.use_cases.set_llm_price import SetLlmPrice, SetLlmPricePorts
from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import Settings
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.llm_pricing import LlmPrice

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
    _add_prices_parser(sub)
    export = sub.add_parser("export", help="write the program-metrics CSV package")
    export.add_argument("--from", dest="from_day", required=True, type=date.fromisoformat)
    export.add_argument("--to", dest="to_day", required=True, type=date.fromisoformat)
    export.add_argument("--out-dir", dest="out_dir", required=True, type=Path)
    export.add_argument("--force", action="store_true", help="overwrite existing files")
    export.set_defaults(handler=_cmd_export)
    return parser


def _add_prices_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    prices = sub.add_parser("prices", help="LLM price table")
    prices_sub = prices.add_subparsers(dest="prices_command", required=True)
    prices_sub.add_parser("list", help="print every price row").set_defaults(
        handler=_cmd_prices_list
    )
    price_set = prices_sub.add_parser("set", help="upsert one price row")
    price_set.add_argument("--model", required=True)
    price_set.add_argument("--rub-per-1k", dest="rub_per_1k", required=True, type=_decimal_arg)
    price_set.add_argument("--from", dest="from_day", required=True, type=date.fromisoformat)
    price_set.set_defaults(handler=_cmd_prices_set)


def _decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        msg = f"not a decimal number: {value!r}"
        raise argparse.ArgumentTypeError(msg) from exc


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


async def _cmd_prices_list(_args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        prices = await ListLlmPrices(SqlAlchemyProgramMetricsStore(engine)).execute()
    finally:
        await dispose_engine(engine)
    print("model\tvalid_from\trub_per_1k_tokens")
    for price in prices:
        print(f"{price.model}\t{price.valid_from.isoformat()}\t{price.rub_per_1k_tokens}")
    return 0


async def _cmd_prices_set(args: argparse.Namespace, settings: Settings) -> int:
    try:
        price = LlmPrice(
            model=args.model, valid_from=args.from_day, rub_per_1k_tokens=args.rub_per_1k
        )
    except InvalidValueError as exc:
        print(f"prices set: {exc}", file=sys.stderr)
        return 2
    engine = create_engine(settings)
    try:
        result = await SetLlmPrice(
            SetLlmPricePorts(
                store=SqlAlchemyProgramMetricsStore(engine),
                clock=SystemClock(),
                timezone=settings.analytics_timezone,
            )
        ).execute(price)
    finally:
        await dispose_engine(engine)
    print(f"price set: {price.model} {price.rub_per_1k_tokens} from {price.valid_from.isoformat()}")
    if result.backfill_from is None or result.backfill_to is None:
        print("backfill: not needed, the price applies to days not yet computed")
    else:
        print(
            "backfill: svoi-pravila-analytics backfill "
            f"--from {result.backfill_from.isoformat()} --to {result.backfill_to.isoformat()}"
        )
    return 0


_EXPORT_MESSAGES = {
    ProgramExportErrorKind.INVALID_RANGE: "--to must be on or after --from",
    ProgramExportErrorKind.RANGE_TOO_LONG: "range exceeds 400 days",
    ProgramExportErrorKind.TARGET_EXISTS: "files already exist in --out-dir, use --force",
    ProgramExportErrorKind.WRITE_FAILED: "could not write to --out-dir",
}


async def _cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    try:
        store = SqlAlchemyProgramMetricsStore(engine)
        exported = await ExportProgramMetrics(
            ExportProgramMetricsPorts(
                reader=store,
                prices=store,
                writer=CsvMetricsPackageWriter(),
                clock=SystemClock(),
            )
        ).execute(
            ExportProgramMetricsInput(
                from_day=args.from_day,
                to_day=args.to_day,
                out_dir=args.out_dir,
                force=args.force,
            )
        )
    except ProgramExportFailed as exc:
        print(f"export: {_EXPORT_MESSAGES[exc.kind]}", file=sys.stderr)
        return 1 if exc.kind is ProgramExportErrorKind.WRITE_FAILED else 2
    finally:
        await dispose_engine(engine)
    for item in exported:
        print(f"{item.name}: {item.rows} rows")
    return 0
