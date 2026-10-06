"""analytics_read_role

Revision ID: a7c8d9e0f1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-10-06 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.sql.compiler import IdentifierPreparer

revision: str = "a7c8d9e0f1b2"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_ROLE = """
DO $$ BEGIN
    CREATE ROLE svoi_analytics_read NOLOGIN;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
"""

# Roles are cluster-wide. Revoke members, then DROP the group role only when
# no members remain and pg_shdepend shows no dependencies in other databases.
_DOWNGRADE_CLEANUP = """
DO $$
DECLARE
    member_name text;
    member_count integer;
    other_db_deps integer;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svoi_analytics_read') THEN
        FOR member_name IN
            SELECT m.rolname
            FROM pg_auth_members am
            JOIN pg_roles g ON g.oid = am.roleid
            JOIN pg_roles m ON m.oid = am.member
            WHERE g.rolname = 'svoi_analytics_read'
        LOOP
            EXECUTE format('REVOKE svoi_analytics_read FROM %I', member_name);
        END LOOP;

        SELECT count(*) INTO member_count
        FROM pg_auth_members am
        JOIN pg_roles g ON g.oid = am.roleid
        WHERE g.rolname = 'svoi_analytics_read';

        -- pg_roles (not pg_authid): readable without superuser (CREATEROLE path).
        SELECT count(*) INTO other_db_deps
        FROM pg_shdepend sd
        JOIN pg_roles r ON r.oid = sd.refobjid
        WHERE r.rolname = 'svoi_analytics_read'
          AND sd.dbid <> 0
          AND sd.dbid <> (SELECT oid FROM pg_database WHERE datname = current_database());

        IF member_count = 0 AND other_db_deps = 0 THEN
            DROP ROLE svoi_analytics_read;
        END IF;
    END IF;
END $$;
"""


def _quote_ident(identifier: str) -> str:
    dialect_factory = PGDialect
    preparer: IdentifierPreparer = dialect_factory().identifier_preparer
    return preparer.quote(identifier)


def upgrade() -> None:
    # Roles are cluster-wide; the same role may already exist from another database.
    op.execute(sa.text(_CREATE_ROLE))
    bind = op.get_bind()
    db_name = bind.execute(sa.text("SELECT current_database()")).scalar_one()
    quoted_db = _quote_ident(db_name)
    op.execute(sa.text("GRANT CONNECT ON DATABASE " + quoted_db + " TO svoi_analytics_read"))
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO svoi_analytics_read"))
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_daily FROM PUBLIC"))
    op.execute(sa.text("GRANT SELECT ON TABLE analytics_daily TO svoi_analytics_read"))
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_daily_scenario FROM PUBLIC"))
    op.execute(sa.text("GRANT SELECT ON TABLE analytics_daily_scenario TO svoi_analytics_read"))
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_cohorts FROM PUBLIC"))
    op.execute(sa.text("GRANT SELECT ON TABLE analytics_cohorts TO svoi_analytics_read"))


def downgrade() -> None:
    bind = op.get_bind()
    db_name = bind.execute(sa.text("SELECT current_database()")).scalar_one()
    quoted_db = _quote_ident(db_name)
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_daily FROM svoi_analytics_read"))
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_daily_scenario FROM svoi_analytics_read"))
    op.execute(sa.text("REVOKE ALL ON TABLE analytics_cohorts FROM svoi_analytics_read"))
    op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM svoi_analytics_read"))
    op.execute(sa.text("REVOKE CONNECT ON DATABASE " + quoted_db + " FROM svoi_analytics_read"))
    op.execute(sa.text(_DOWNGRADE_CLEANUP))
