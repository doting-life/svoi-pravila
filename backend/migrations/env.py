"""Alembic async environment — URL and logging from the composition root."""

from __future__ import annotations

import asyncio
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from svoi_pravila.adapters.persistence.models import Base
from svoi_pravila.bootstrap import load_settings
from svoi_pravila.observability import configure_logging

config = context.config
settings = load_settings()
configure_logging(settings, sys.stdout)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve the database URL from Alembic config attributes or Settings."""
    override = config.attributes.get("database_url")
    if override:
        return str(override)
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations given a sync connection from the async engine."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
