"""One-shot migrate entrypoint: Alembic upgrade, then Grafana DB role provisioning."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from svoi_pravila.adapters.persistence.scram_verifier import scram_sha256_verifier
from svoi_pravila.bootstrap import load_migrate_settings
from svoi_pravila.config import GrafanaDbPasswordMissingError, MigrateSettings
from svoi_pravila.observability import configure_logging

_DEFAULT_READER = "grafana_reader"
_ROLE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_GROUP_ROLE = "svoi_analytics_read"
_CONNECTION_LIMIT = 5
# Server-side limits on the Grafana reader (ADR-0017 §3): the database role, not the Grafana
# role, is the access boundary. Values are constants, never settings.
_ROLE_SETTINGS = (
    ("default_transaction_read_only", "on"),
    ("statement_timeout", "'10s'"),
    ("idle_in_transaction_session_timeout", "'30s'"),
    # Superuser-only parameter: the role cannot lift it, so a heavy query cannot fill the disk.
    ("temp_file_limit", "'256MB'"),
)


def main() -> None:
    """Upgrade schema to head, then provision the Grafana reader LOGIN role."""
    try:
        settings = load_migrate_settings()
    except GrafanaDbPasswordMissingError:
        print("SP_GRAFANA_DB_PASSWORD is required and must be non-empty", file=sys.stderr)
        raise SystemExit(2) from None
    except ValidationError as exc:
        print(_format_validation_errors(exc), file=sys.stderr)
        raise SystemExit(2) from None
    configure_logging(settings, sys.stdout)
    _upgrade_head()
    asyncio.run(_provision_from_settings(settings))


def _format_validation_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors(include_input=False, include_context=False):
        loc = ".".join(str(item) for item in err.get("loc", ()))
        typ = str(err.get("type", "error"))
        parts.append(f"{loc}: {typ}" if loc else typ)
    return "; ".join(parts) if parts else "validation error"


def _upgrade_head() -> None:
    ini = Path("alembic.ini")
    if not ini.is_file():
        msg = f"alembic.ini not found in working directory {Path.cwd()}"
        raise FileNotFoundError(msg)
    cfg = Config(str(ini.resolve()))
    command.upgrade(cfg, "head")


async def _provision_from_settings(settings: MigrateSettings) -> None:
    password = settings.grafana_db_password.get_secret_value()
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        await provision_grafana_reader(
            engine,
            SecretStr(password),
            role_name=settings.grafana_db_user,
        )
    finally:
        await engine.dispose()


def require_role_name(role_name: str) -> str:
    """Return ``role_name`` when it is a safe Postgres identifier."""
    if _ROLE_NAME_RE.fullmatch(role_name) is None:
        msg = "grafana_db_user must match ^[a-z_][a-z0-9_]{0,62}$"
        raise ValueError(msg)
    return role_name


def _quote_ident(identifier: str) -> str:
    """Quote a role name already validated by ``require_role_name``."""
    return f'"{identifier}"'


async def provision_grafana_reader(
    engine: AsyncEngine,
    password: SecretStr,
    *,
    role_name: str = _DEFAULT_READER,
) -> None:
    """Create or update a LOGIN role as inheriting member of ``svoi_analytics_read``.

    The password is never sent to Postgres in plaintext: a SCRAM-SHA-256 verifier
    is computed client-side (same format as ``psql \\password``) and embedded in
    ``ALTER ROLE … PASSWORD`` only after a strict format check. The role is also
    limited to ``CONNECTION LIMIT`` 5, read-only transactions, a 10 s statement timeout,
    a 30 s idle-in-transaction timeout and 256 MB of temporary files; every run reapplies
    them idempotently. The read-only and timeout defaults are user-settable: the privilege
    set of the group role is the boundary, the superuser-only temp-file limit is the cap.
    """
    secret = password.get_secret_value()
    if not secret.strip():
        raise GrafanaDbPasswordMissingError()
    name = require_role_name(role_name)
    quoted = _quote_ident(name)
    verifier = scram_sha256_verifier(secret)
    # Role DDL cannot run inside a transaction block.
    async with engine.connect() as base_conn:
        conn = await base_conn.execution_options(isolation_level="AUTOCOMMIT")
        exists = await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
            {"name": name},
        )
        if exists.scalar() is None:
            await conn.execute(text(f"CREATE ROLE {quoted} LOGIN"))
        # The group's privileges are inherited, but SET ROLE into the group is refused.
        await conn.execute(text(f"GRANT {_GROUP_ROLE} TO {quoted} WITH INHERIT TRUE, SET FALSE"))
        # Verifier is regex-validated (base64 + delimiters only). Use driver SQL so
        # SQLAlchemy does not treat `$…` fragments of the SCRAM string as binds.
        await conn.exec_driver_sql(f"ALTER ROLE {quoted} WITH LOGIN PASSWORD '{verifier}'")
        await conn.execute(text(f"ALTER ROLE {quoted} CONNECTION LIMIT {_CONNECTION_LIMIT}"))
        for setting, value in _ROLE_SETTINGS:
            await conn.execute(text(f"ALTER ROLE {quoted} SET {setting} = {value}"))


if __name__ == "__main__":
    main()
