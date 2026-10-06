"""E2 — analytics_read_role migration upgrade/downgrade as NOSUPERUSER CREATEROLE."""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlparse, urlunparse

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from svoi_pravila.adapters.persistence.scram_verifier import scram_sha256_verifier
from svoi_pravila.cli.migrate import provision_grafana_reader
from svoi_pravila.config import Settings
from tests.support.postgres import (
    create_temporary_database,
    downgrade_to,
    drop_temporary_database,
    upgrade_head,
)


def _login_url(admin_url: str, user: str, password: str) -> str:
    parsed = urlparse(admin_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    netloc = f"{user}:{quote(password, safe='')}@{host}:{port}"
    return urlunparse(parsed._replace(netloc=netloc))


async def _drop_role_if_exists(conn: AsyncConnection, role: str) -> None:
    exists = await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
        {"name": role},
    )
    if exists.scalar() is None:
        return
    await conn.execute(
        text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE usename = :name AND pid <> pg_backend_pid()"
        ),
        {"name": role},
    )
    member = await conn.execute(
        text(
            "SELECT 1 FROM pg_auth_members am "
            "JOIN pg_roles g ON g.oid = am.roleid "
            "JOIN pg_roles m ON m.oid = am.member "
            "WHERE g.rolname = 'svoi_analytics_read' AND m.rolname = :name"
        ),
        {"name": role},
    )
    if member.scalar() is not None:
        await conn.execute(text(f'REVOKE svoi_analytics_read FROM "{role}"'))
    # Drop any leftover LOGIN roles this CREATEROLE user created (test readers).
    created = await conn.execute(
        text(
            "SELECT m.rolname FROM pg_auth_members am "
            "JOIN pg_roles g ON g.oid = am.roleid "
            "JOIN pg_roles m ON m.oid = am.member "
            "WHERE g.rolname = :name"
        ),
        {"name": role},
    )
    for row in created:
        child = str(row[0])
        await conn.execute(text(f'REVOKE "{role}" FROM "{child}"'))
    await conn.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.integration
async def test_analytics_read_role_upgrade_downgrade_as_createrole(
    migrated_schema: Settings,
) -> None:
    temp_url, temp_name = await create_temporary_database(migrated_schema)
    mig_user = f"mig_createrole_{secrets.token_hex(6)}"
    mig_password = secrets.token_urlsafe(24)
    reader = f"grafana_reader_t_{secrets.token_hex(6)}"
    reader_password = secrets.token_urlsafe(24)
    mig_verifier = scram_sha256_verifier(mig_password)
    admin_url = migrated_schema.database_url.get_secret_value()
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    try:
        async with admin.connect() as conn:
            await conn.exec_driver_sql(
                f"CREATE ROLE {mig_user} LOGIN PASSWORD '{mig_verifier}' "
                "NOSUPERUSER CREATEROLE NOCREATEDB"
            )
            await conn.execute(text(f"GRANT ALL ON DATABASE {temp_name} TO {mig_user}"))
            await conn.execute(text(f"ALTER DATABASE {temp_name} OWNER TO {mig_user}"))

        mig_url = _login_url(temp_url, mig_user, mig_password)
        upgrade_head(sqlalchemy_url=mig_url)
        # Postgres 16+: granting a role requires ADMIN OPTION. The group role may
        # already exist cluster-wide from the shared test DB (duplicate_object on
        # upgrade), so the CREATEROLE user is not its owner — grant ADMIN for the test.
        async with admin.connect() as conn:
            await conn.execute(text(f"GRANT svoi_analytics_read TO {mig_user} WITH ADMIN OPTION"))

        mig_engine = create_async_engine(mig_url, hide_parameters=True)
        try:
            await provision_grafana_reader(mig_engine, SecretStr(reader_password), role_name=reader)
        finally:
            await mig_engine.dispose()

        downgrade_to("f1a2b3c4d5e6", sqlalchemy_url=mig_url)
        upgrade_head(sqlalchemy_url=mig_url)
    finally:
        await drop_temporary_database(migrated_schema, temp_name)
        async with admin.connect() as conn:
            await _drop_role_if_exists(conn, reader)
            await _drop_role_if_exists(conn, mig_user)
        await admin.dispose()
