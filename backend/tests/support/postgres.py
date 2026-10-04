"""Shared PostgreSQL fixtures: schema upgrade, truncate, engine, UoW factory."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql.compiler import IdentifierPreparer

from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.models import Base
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import Settings
from tests.factories import make_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
_TEST_VALKEY_DB = "15"
_TEST_DB_SUFFIX = "_test"


def _postgres_identifier_preparer() -> IdentifierPreparer:
    """Build a PostgreSQL identifier preparer (PGDialect is untyped at stubs)."""
    dialect_factory = cast(Callable[[], PGDialect], PGDialect)
    return dialect_factory().identifier_preparer


_PG_PREPARER = _postgres_identifier_preparer()


def quote_pg_identifier(identifier: str) -> str:
    """Quote a PostgreSQL identifier via SQLAlchemy's dialect preparer."""
    return _PG_PREPARER.quote(identifier)


def alembic_config(*, sqlalchemy_url: str | None = None) -> Config:
    """Build an Alembic Config rooted at ``backend/``."""
    cfg = Config(str(ALEMBIC_INI))
    if sqlalchemy_url is not None:
        cfg.attributes["database_url"] = sqlalchemy_url
    return cfg


def _run_alembic_in_isolated_loop(action: Callable[[], None]) -> None:
    """Run Alembic away from pytest-asyncio's loop.

    ``migrations/env.py`` uses ``asyncio.run``. When a sync session fixture is
    resolved while an async fixture's loop is already running, in-process
    ``asyncio.run`` fails. A worker thread always has a free loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(action).result()


def require_test_database_url(database_url: str) -> str:
    """Refuse URLs whose database name does not end with ``_test``."""
    name = database_name_from_url(database_url)
    if not name.endswith(_TEST_DB_SUFFIX):
        msg = (
            f"test support refuses non-test database {name!r}; "
            f"name must end with {_TEST_DB_SUFFIX!r}"
        )
        raise ValueError(msg)
    return database_url


def upgrade_head(*, sqlalchemy_url: str | None = None) -> None:
    """Apply Alembic migrations to head on a ``*_test`` database only."""
    if sqlalchemy_url is None:
        sqlalchemy_url = load_settings().database_url.get_secret_value()
    require_test_database_url(sqlalchemy_url)
    cfg = alembic_config(sqlalchemy_url=sqlalchemy_url)
    _run_alembic_in_isolated_loop(lambda: command.upgrade(cfg, "head"))


def downgrade_base(*, sqlalchemy_url: str | None = None) -> None:
    """Downgrade Alembic migrations to base on a ``*_test`` database only."""
    if sqlalchemy_url is None:
        sqlalchemy_url = load_settings().database_url.get_secret_value()
    require_test_database_url(sqlalchemy_url)
    cfg = alembic_config(sqlalchemy_url=sqlalchemy_url)
    _run_alembic_in_isolated_loop(lambda: command.downgrade(cfg, "base"))


def replace_database_name(database_url: str, database_name: str) -> str:
    """Return ``database_url`` with the path replaced by ``/database_name``."""
    parsed = urlparse(database_url)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def database_name_from_url(database_url: str) -> str:
    """Return the database name from a SQLAlchemy URL path."""
    path = urlparse(database_url).path.lstrip("/")
    return path.split("/", maxsplit=1)[0]


def test_database_url(database_url: str) -> str:
    """Derive the dedicated ``<database>_test`` URL from a settings URL."""
    name = database_name_from_url(database_url)
    return replace_database_name(database_url, f"{name}{_TEST_DB_SUFFIX}")


def test_valkey_url(valkey_url: str) -> str:
    """Rewrite a Valkey URL to logical database 15 for tests."""
    parsed = urlparse(valkey_url)
    return urlunparse(parsed._replace(path=f"/{_TEST_VALKEY_DB}"))


async def ensure_test_database_exists(settings: Settings) -> str:
    """Create ``<database>_test`` if missing; return its URL."""
    base = settings.database_url.get_secret_value()
    url = test_database_url(base)
    name = database_name_from_url(url)
    require_test_database_url(url)
    admin_url = replace_database_name(base, "postgres")
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name},
            )
            if exists.scalar() is None:
                quoted = quote_pg_identifier(name)
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await admin.dispose()
    return url


def isolated_settings() -> Settings:
    """Settings aimed at the dedicated test database and Valkey DB 15."""
    # Prefer full Settings when the process env is valid; otherwise assemble
    # from the DB/Valkey/KEK/pepper variables required by integration tests.
    try:
        base = load_settings()
        database_url = base.database_url.get_secret_value()
        valkey_url = base.valkey_url.get_secret_value()
        data_kek = base.data_kek.get_secret_value()
        data_kek_id = base.data_kek_id
        pepper = base.pseudonym_pepper.get_secret_value()
    except ValidationError:
        database_url = os.environ["SP_DATABASE_URL"]
        valkey_url = os.environ["SP_VALKEY_URL"]
        data_kek = os.environ["SP_DATA_KEK"]
        data_kek_id = os.environ["SP_DATA_KEK_ID"]
        pepper = os.environ["SP_PSEUDONYM_PEPPER"]

    settings = make_settings(
        database_url=database_url,
        valkey_url=valkey_url,
        data_kek=data_kek,
        data_kek_id=data_kek_id,
        pseudonym_pepper=pepper,
    )
    # Run ensure off the pytest-asyncio loop (same reason as Alembic helpers).
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        database_url = pool.submit(
            lambda: asyncio.run(ensure_test_database_exists(settings))
        ).result()
    return settings.model_copy(
        update={
            "database_url": SecretStr(database_url),
            "valkey_url": SecretStr(test_valkey_url(settings.valkey_url.get_secret_value())),
        }
    )


def _engine_database_url(engine: AsyncEngine) -> str:
    """Render the engine URL including the password for guard checks."""
    return engine.url.render_as_string(hide_password=False)


async def truncate_all_tables(engine: AsyncEngine) -> None:
    """Truncate every mapped table using ``Base.metadata.sorted_tables``."""
    require_test_database_url(_engine_database_url(engine))
    tables = list(reversed(Base.metadata.sorted_tables))
    if not tables:
        return
    names = ", ".join(table.name for table in tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def migrated_schema() -> Settings:
    """Ensure the dedicated test database schema is at Alembic head."""
    settings = isolated_settings()
    upgrade_head(sqlalchemy_url=settings.database_url.get_secret_value())
    return settings


@pytest.fixture
async def engine(migrated_schema: Settings) -> AsyncIterator[AsyncEngine]:
    """Async engine against the dedicated migrated test database."""
    require_test_database_url(migrated_schema.database_url.get_secret_value())
    eng = create_engine(migrated_schema)
    yield eng
    await dispose_engine(eng)


@pytest.fixture
async def uow_factory_postgres(
    engine: AsyncEngine, migrated_schema: Settings
) -> AsyncIterator[SqlAlchemyUnitOfWorkFactory]:
    """SQLAlchemy UoW factory with truncate before and after each test."""
    factory = SqlAlchemyUnitOfWorkFactory(
        engine,
        kek=migrated_schema.data_kek_bytes(),
        kek_id=migrated_schema.data_kek_id,
    )
    await truncate_all_tables(engine)
    yield factory
    await truncate_all_tables(engine)


async def create_temporary_database(settings: Settings) -> tuple[str, str]:
    """Create an empty temporary ``*_test`` database; return (temp_url, temp_name)."""
    base = settings.database_url.get_secret_value()
    temp_name = f"svoi_mig_{secrets.token_hex(6)}{_TEST_DB_SUFFIX}"
    admin_url = replace_database_name(base, "postgres")
    temp_url = replace_database_name(base, temp_name)
    require_test_database_url(temp_url)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            quoted = quote_pg_identifier(temp_name)
            await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await admin.dispose()
    return temp_url, temp_name


async def drop_temporary_database(settings: Settings, database_name: str) -> None:
    """Drop a temporary database created for migration tests."""
    admin_url = replace_database_name(settings.database_url.get_secret_value(), "postgres")
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            quoted = quote_pg_identifier(database_name)
            await conn.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
    finally:
        await admin.dispose()
