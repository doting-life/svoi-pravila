"""One-shot migrate entrypoint: Alembic upgrade, then Grafana DB role provisioning."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.bootstrap import load_migrate_settings
from svoi_pravila.config import GrafanaDbPasswordMissingError, MigrateSettings
from svoi_pravila.observability import configure_logging

_GRAFANA_READER = "grafana_reader"
_CREATE_READER = "CREATE ROLE grafana_reader LOGIN IN ROLE svoi_analytics_read"
_GRANT_READER = "GRANT svoi_analytics_read TO grafana_reader"
# Postgres rejects bind parameters for ROLE PASSWORD; literal_execute lets the
# dialect emit a properly escaped literal (never client-side string concat).
_ALTER_ROLE_SECRET = "ALTER ROLE grafana_reader WITH LOGIN " + "PASSWORD :pwd"


def main() -> None:
    """Upgrade schema to head, then provision ``grafana_reader``."""
    try:
        settings = load_migrate_settings()
    except GrafanaDbPasswordMissingError:
        print("SP_GRAFANA_DB_PASSWORD is required and must be non-empty", file=sys.stderr)
        raise SystemExit(2) from None
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    configure_logging(settings, sys.stdout)
    _upgrade_head()
    asyncio.run(_provision_grafana_reader(settings))


def _upgrade_head() -> None:
    ini = Path("alembic.ini")
    if not ini.is_file():
        msg = f"alembic.ini not found in working directory {Path.cwd()}"
        raise FileNotFoundError(msg)
    cfg = Config(str(ini.resolve()))
    command.upgrade(cfg, "head")


async def _provision_grafana_reader(settings: MigrateSettings) -> None:
    password = settings.grafana_db_password.get_secret_value()
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        await provision_grafana_reader(engine, SecretStr(password))
    finally:
        await engine.dispose()


async def provision_grafana_reader(engine: AsyncEngine, password: SecretStr) -> None:
    """Create or update LOGIN role ``grafana_reader`` as member of ``svoi_analytics_read``.

    The password is set with a bound parameter so the secret is never concatenated
    into a SQL string on the client.
    """
    secret = password.get_secret_value()
    if not secret.strip():
        raise GrafanaDbPasswordMissingError()
    # Role DDL cannot run inside a transaction block.
    async with engine.connect() as base_conn:
        conn = await base_conn.execution_options(isolation_level="AUTOCOMMIT")
        exists = await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": _GRAFANA_READER},
        )
        if exists.scalar() is None:
            await conn.execute(text(_CREATE_READER))
        else:
            await conn.execute(text(_GRANT_READER))
        # Postgres rejects bind parameters for ROLE PASSWORD; the dialect must
        # emit an escaped literal. Mute engine logging so the secret is never logged.
        sa_loggers = (
            logging.getLogger("sqlalchemy.engine"),
            logging.getLogger("sqlalchemy.engine.Engine"),
        )
        previous_levels = [logger.level for logger in sa_loggers]
        for logger in sa_loggers:
            logger.setLevel(logging.WARNING)
        try:
            await conn.execute(
                text(_ALTER_ROLE_SECRET).bindparams(
                    bindparam("pwd", value=secret, literal_execute=True)
                )
            )
        finally:
            for logger, level in zip(sa_loggers, previous_levels, strict=True):
                logger.setLevel(level)


if __name__ == "__main__":
    main()
