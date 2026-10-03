"""Shared PostgreSQL fixtures: schema upgrade, truncate, engine, UoW factory."""

from __future__ import annotations

import concurrent.futures
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.adapters.persistence.engine import create_engine, dispose_engine
from svoi_pravila.adapters.persistence.models import Base
from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory
from svoi_pravila.bootstrap import load_settings
from svoi_pravila.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def alembic_config(*, sqlalchemy_url: str | None = None) -> Config:
    """Build an Alembic Config rooted at ``backend/``."""
    cfg = Config(str(ALEMBIC_INI))
    if sqlalchemy_url is not None:
        cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)
    return cfg


def _run_alembic_in_isolated_loop(action: Callable[[], None]) -> None:
    """Run Alembic away from pytest-asyncio's loop.

    ``migrations/env.py`` uses ``asyncio.run``. When a sync session fixture is
    resolved while an async fixture's loop is already running, in-process
    ``asyncio.run`` fails. A worker thread always has a free loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(action).result()


def upgrade_head(*, sqlalchemy_url: str | None = None) -> None:
    """Apply Alembic migrations to head."""
    cfg = alembic_config(sqlalchemy_url=sqlalchemy_url)
    _run_alembic_in_isolated_loop(lambda: command.upgrade(cfg, "head"))


def downgrade_base(*, sqlalchemy_url: str | None = None) -> None:
    """Downgrade Alembic migrations to base."""
    cfg = alembic_config(sqlalchemy_url=sqlalchemy_url)
    _run_alembic_in_isolated_loop(lambda: command.downgrade(cfg, "base"))


def replace_database_name(database_url: str, database_name: str) -> str:
    """Return ``database_url`` with the path replaced by ``/database_name``."""
    parsed = urlparse(database_url)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


async def truncate_all_tables(engine: AsyncEngine) -> None:
    """Truncate every mapped table using ``Base.metadata.sorted_tables``."""
    tables = list(reversed(Base.metadata.sorted_tables))
    if not tables:
        return
    names = ", ".join(table.name for table in tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def migrated_schema() -> Settings:
    """Ensure the shared test database schema is at Alembic head; return settings."""
    settings = load_settings()
    upgrade_head(sqlalchemy_url=settings.database_url.get_secret_value())
    return settings


@pytest.fixture
async def engine(migrated_schema: Settings) -> AsyncIterator[AsyncEngine]:
    """Async engine against the shared migrated database."""
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
    """Create an empty temporary database; return (temp_url, temp_name)."""
    base = settings.database_url.get_secret_value()
    temp_name = f"svoi_mig_{secrets.token_hex(6)}"
    admin_url = replace_database_name(base, "postgres")
    temp_url = replace_database_name(base, temp_name)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{temp_name}"'))
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
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        await admin.dispose()
