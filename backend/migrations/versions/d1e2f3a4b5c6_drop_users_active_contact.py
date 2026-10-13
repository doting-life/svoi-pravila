"""drop_users_active_contact

The contact is explicit per request (ADR-0015 section 3); the user profile no longer
stores an active contact.

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-10-13 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_users_active_contact_id_contacts"


def upgrade() -> None:
    op.drop_constraint(_FK, "users", type_="foreignkey")
    op.drop_column("users", "active_contact_id")


def downgrade() -> None:
    op.add_column("users", sa.Column("active_contact_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        _FK,
        "users",
        "contacts",
        ["active_contact_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
