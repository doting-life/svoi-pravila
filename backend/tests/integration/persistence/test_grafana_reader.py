"""A3 — ``grafana_reader`` may SELECT aggregates only; DML and other tables denied."""

from __future__ import annotations

from urllib.parse import quote, urlparse, urlunparse

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.cli.migrate import provision_grafana_reader
from svoi_pravila.config import Settings

_READER_PASSWORD = "grafana-reader-integration-test-only"

_SELECT_DAILY = "SELECT COUNT(*) FROM analytics_daily"
_SELECT_SCENARIO = "SELECT COUNT(*) FROM analytics_daily_scenario"
_SELECT_COHORTS = "SELECT COUNT(*) FROM analytics_cohorts"
_SELECT_USAGE = "SELECT COUNT(*) FROM usage_events"
_SELECT_JOB_RUNS = "SELECT COUNT(*) FROM job_runs"
_SELECT_USERS = "SELECT COUNT(*) FROM users"

_DML_STATEMENTS = (
    "INSERT INTO analytics_daily "
    "(day, active_users, appeals, new_users, generations, "
    "generation_errors, computed_at) "
    "VALUES (CURRENT_DATE, 0, 0, 0, 0, 0, NOW())",
    "UPDATE analytics_daily SET appeals = 1 WHERE FALSE",
    "DELETE FROM analytics_daily WHERE FALSE",
    "INSERT INTO analytics_daily_scenario "
    "(day, scenario, surface, appeals, users, ok, refused, screened, "
    "invalid_output, unavailable, chosen, latency_p50_ms, latency_p95_ms, "
    "ttfc_p50_ms, ttfc_p95_ms, input_tokens, output_tokens, billable_tokens) "
    "VALUES (CURRENT_DATE, 'decode', 'dm', 0, 0, 0, 0, 0, 0, 0, 0, "
    "NULL, NULL, NULL, NULL, 0, 0, 0)",
    "UPDATE analytics_daily_scenario SET appeals = 1 WHERE FALSE",
    "DELETE FROM analytics_daily_scenario WHERE FALSE",
    "INSERT INTO analytics_cohorts "
    "(cohort_day, size, d1_retained, d7_retained, computed_at) "
    "VALUES (CURRENT_DATE, 0, NULL, NULL, NOW())",
    "UPDATE analytics_cohorts SET size = 1 WHERE FALSE",
    "DELETE FROM analytics_cohorts WHERE FALSE",
)


def _reader_url(admin_url: str, password: str) -> str:
    parsed = urlparse(admin_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    db = parsed.path
    netloc = f"grafana_reader:{quote(password, safe='')}@{host}:{port}"
    return urlunparse(parsed._replace(netloc=netloc, path=db))


def _is_privilege_error(exc: BaseException) -> bool:
    text_repr = str(exc).lower()
    return "permission denied" in text_repr or "insufficientprivilege" in text_repr


@pytest.mark.integration
async def test_grafana_reader_select_aggregates_allowed(
    engine: AsyncEngine,
    migrated_schema: Settings,
) -> None:
    await provision_grafana_reader(engine, SecretStr(_READER_PASSWORD))
    reader = create_async_engine(
        _reader_url(migrated_schema.database_url.get_secret_value(), _READER_PASSWORD),
        hide_parameters=True,
    )
    try:
        async with reader.connect() as conn:
            for sql in (_SELECT_DAILY, _SELECT_SCENARIO, _SELECT_COHORTS):
                result = await conn.execute(text(sql))
                assert result.scalar() is not None
    finally:
        await reader.dispose()


@pytest.mark.integration
async def test_grafana_reader_select_sensitive_tables_denied(
    engine: AsyncEngine,
    migrated_schema: Settings,
) -> None:
    await provision_grafana_reader(engine, SecretStr(_READER_PASSWORD))
    reader = create_async_engine(
        _reader_url(migrated_schema.database_url.get_secret_value(), _READER_PASSWORD),
        hide_parameters=True,
    )
    try:
        for sql in (_SELECT_USAGE, _SELECT_JOB_RUNS, _SELECT_USERS):
            async with reader.connect() as conn:
                with pytest.raises((DBAPIError, ProgrammingError)) as caught:
                    await conn.execute(text(sql))
                assert _is_privilege_error(caught.value)
    finally:
        await reader.dispose()


@pytest.mark.integration
async def test_grafana_reader_dml_on_aggregates_denied(
    engine: AsyncEngine,
    migrated_schema: Settings,
) -> None:
    await provision_grafana_reader(engine, SecretStr(_READER_PASSWORD))
    reader = create_async_engine(
        _reader_url(migrated_schema.database_url.get_secret_value(), _READER_PASSWORD),
        hide_parameters=True,
    )
    try:
        for sql in _DML_STATEMENTS:
            async with reader.connect() as conn:
                with pytest.raises((DBAPIError, ProgrammingError)) as caught:
                    await conn.execute(text(sql))
                assert _is_privilege_error(caught.value)
    finally:
        await reader.dispose()
