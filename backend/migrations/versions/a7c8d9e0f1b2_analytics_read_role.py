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

# Roles are cluster-wide. Drop grants in this database, then drop the role when
# nothing else (other databases / login members) still depends on it.
_DOWNGRADE_CLEANUP = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        EXECUTE 'REVOKE svoi_analytics_read FROM grafana_reader';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svoi_analytics_read') THEN
        EXECUTE 'DROP OWNED BY svoi_analytics_read';
    END IF;
    BEGIN
        DROP ROLE IF EXISTS svoi_analytics_read;
    EXCEPTION
        WHEN dependent_objects_still_exist THEN NULL;
    END;
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
