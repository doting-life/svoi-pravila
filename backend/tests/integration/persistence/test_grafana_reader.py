"""A3/D — Grafana reader SELECT/DML isolation; unique roles; SCRAM provisioning."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from urllib.parse import quote, urlparse, urlunparse

import pytest
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.cli.migrate import provision_grafana_reader
from svoi_pravila.config import Settings

_READER_PASSWORD = "grafana-reader-integration-test-only"
_WRONG_PASSWORD = "definitely-not-the-reader-password"
_DEFAULT_ROLE = "grafana_reader"

_SELECT_DAILY = "SELECT COUNT(*) FROM analytics_daily"
_SELECT_SCENARIO = "SELECT COUNT(*) FROM analytics_daily_scenario"
_SELECT_COHORTS = "SELECT COUNT(*) FROM analytics_cohorts"
_SELECT_NEW_DAILY_COLS = (
    "SELECT users_limited, billable_tokens, llm_budget_tokens FROM analytics_daily LIMIT 1"
)
_SELECT_NEW_SCENARIO_COLS = (
    "SELECT limited_user_quota, limited_global_budget FROM analytics_daily_scenario LIMIT 1"
)
_SELECT_USAGE = "SELECT COUNT(*) FROM usage_events"
_SELECT_JOB_RUNS = "SELECT COUNT(*) FROM job_runs"
_SELECT_USERS = "SELECT COUNT(*) FROM users"

_DML_STATEMENTS = (
    "INSERT INTO analytics_daily "
    "(day, active_users, appeals, new_users, generations, "
    "generation_errors, users_limited, billable_tokens, llm_budget_tokens, computed_at) "
    "VALUES (CURRENT_DATE, 0, 0, 0, 0, 0, 0, 0, 0, NOW())",
    "UPDATE analytics_daily SET appeals = 1 WHERE FALSE",
    "DELETE FROM analytics_daily WHERE FALSE",
    "INSERT INTO analytics_daily_scenario "
    "(day, scenario, surface, appeals, users, ok, refused, screened, "
    "invalid_output, unavailable, chosen, limited_user_quota, "
    "limited_global_budget, latency_p50_ms, latency_p95_ms, "
    "ttfc_p50_ms, ttfc_p95_ms, input_tokens, output_tokens, billable_tokens) "
    "VALUES (CURRENT_DATE, 'decode', 'dm', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "
    "NULL, NULL, NULL, NULL, 0, 0, 0)",
    "UPDATE analytics_daily_scenario SET appeals = 1 WHERE FALSE",
    "DELETE FROM analytics_daily_scenario WHERE FALSE",
    "INSERT INTO analytics_cohorts "
    "(cohort_day, size, d1_retained, d7_retained, computed_at) "
    "VALUES (CURRENT_DATE, 0, NULL, NULL, NOW())",
    "UPDATE analytics_cohorts SET size = 1 WHERE FALSE",
    "DELETE FROM analytics_cohorts WHERE FALSE",
)


def _reader_url(admin_url: str, role_name: str, password: str) -> str:
    parsed = urlparse(admin_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    db = parsed.path
    netloc = f"{role_name}:{quote(password, safe='')}@{host}:{port}"
    return urlunparse(parsed._replace(netloc=netloc, path=db))


def _is_privilege_error(exc: BaseException) -> bool:
    text_repr = str(exc).lower()
    return "permission denied" in text_repr or "insufficientprivilege" in text_repr


def _is_auth_error(exc: BaseException) -> bool:
    text_repr = str(exc).lower()
    return (
        "password authentication failed" in text_repr
        or "invalidpassword" in text_repr
        or "authentication failed" in text_repr
    )


async def _rolpassword(engine: AsyncEngine, role_name: str) -> str | None:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT rolpassword FROM pg_authid WHERE rolname = :name"),
            {"name": role_name},
        )
        value = result.scalar()
        return None if value is None else str(value)


async def _drop_role(engine: AsyncEngine, role_name: str) -> None:
    async with engine.connect() as base_conn:
        conn = await base_conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename = :name AND pid <> pg_backend_pid()"
            ),
            {"name": role_name},
        )
        exists = await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": role_name},
        )
        if exists.scalar() is not None:
            await conn.execute(text(f'REVOKE svoi_analytics_read FROM "{role_name}"'))
            await conn.execute(text(f'DROP ROLE "{role_name}"'))


@pytest.fixture
async def grafana_test_reader(
    engine: AsyncEngine,
) -> AsyncIterator[str]:
    """Unique LOGIN role for this test; never touches ``grafana_reader``."""
    role_name = f"grafana_reader_t_{secrets.token_hex(8)}"
    yield role_name
    await _drop_role(engine, role_name)


@pytest.mark.integration
async def test_grafana_reader_select_aggregates_allowed(
    engine: AsyncEngine,
    migrated_schema: Settings,
    grafana_test_reader: str,
) -> None:
    await provision_grafana_reader(
        engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
    )
    reader = create_async_engine(
        _reader_url(
            migrated_schema.database_url.get_secret_value(),
            grafana_test_reader,
            _READER_PASSWORD,
        ),
        hide_parameters=True,
    )
    try:
        async with reader.connect() as conn:
            for sql in (_SELECT_DAILY, _SELECT_SCENARIO, _SELECT_COHORTS):
                result = await conn.execute(text(sql))
                assert result.scalar() is not None
            for sql in (_SELECT_NEW_DAILY_COLS, _SELECT_NEW_SCENARIO_COLS):
                await conn.execute(text(sql))
    finally:
        await reader.dispose()


@pytest.mark.integration
async def test_grafana_reader_select_sensitive_tables_denied(
    engine: AsyncEngine,
    migrated_schema: Settings,
    grafana_test_reader: str,
) -> None:
    await provision_grafana_reader(
        engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
    )
    reader = create_async_engine(
        _reader_url(
            migrated_schema.database_url.get_secret_value(),
            grafana_test_reader,
            _READER_PASSWORD,
        ),
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
    grafana_test_reader: str,
) -> None:
    await provision_grafana_reader(
        engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
    )
    reader = create_async_engine(
        _reader_url(
            migrated_schema.database_url.get_secret_value(),
            grafana_test_reader,
            _READER_PASSWORD,
        ),
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


@pytest.mark.integration
async def test_grafana_reader_auth_with_plaintext_and_wrong_password(
    engine: AsyncEngine,
    migrated_schema: Settings,
    grafana_test_reader: str,
) -> None:
    await provision_grafana_reader(
        engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
    )
    admin_url = migrated_schema.database_url.get_secret_value()
    ok = create_async_engine(
        _reader_url(admin_url, grafana_test_reader, _READER_PASSWORD),
        hide_parameters=True,
    )
    try:
        async with ok.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await ok.dispose()

    bad = create_async_engine(
        _reader_url(admin_url, grafana_test_reader, _WRONG_PASSWORD),
        hide_parameters=True,
    )
    try:
        with pytest.raises(DBAPIError) as caught:
            async with bad.connect() as conn:
                await conn.execute(text("SELECT 1"))
        assert _is_auth_error(caught.value)
    finally:
        await bad.dispose()


@pytest.mark.integration
async def test_provision_statement_never_contains_plaintext_password(
    engine: AsyncEngine,
    grafana_test_reader: str,
) -> None:
    statements: list[str] = []

    def _capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await provision_grafana_reader(
            engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    joined = "\n".join(statements)
    assert _READER_PASSWORD not in joined
    assert "SCRAM-SHA-256$4096:" in joined


@pytest.mark.integration
async def test_default_grafana_reader_password_unchanged_after_suite(
    engine: AsyncEngine,
    grafana_test_reader: str,
) -> None:
    before = await _rolpassword(engine, _DEFAULT_ROLE)
    await provision_grafana_reader(
        engine, SecretStr(_READER_PASSWORD), role_name=grafana_test_reader
    )
    after = await _rolpassword(engine, _DEFAULT_ROLE)
    assert before == after
