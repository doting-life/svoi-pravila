"""add_rule_suggestions_and_tone_signals

Revision ID: a1b2c3d4e5f6
Revises: d8f1c3a04b21
Create Date: 2026-10-05 07:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d8f1c3a04b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("text_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("firmness", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "source IN ('tone', 'decode')",
            name=op.f("ck_rule_suggestions_source"),
        ),
        sa.CheckConstraint(
            "category IN ('taboo_topic', 'how_to_ask', 'apology', 'conflict_protocol', 'other')",
            name=op.f("ck_rule_suggestions_category"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name=op.f("ck_rule_suggestions_status"),
        ),
        sa.CheckConstraint(
            "firmness IS NULL OR firmness IN ('gentle', 'balanced', 'firm')",
            name=op.f("ck_rule_suggestions_firmness"),
        ),
        sa.CheckConstraint(
            "(source = 'tone' AND firmness IS NOT NULL) OR "
            "(source = 'decode' AND firmness IS NULL)",
            name=op.f("ck_rule_suggestions_source_firmness"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL) OR "
            "(status <> 'pending' AND decided_at IS NOT NULL)",
            name=op.f("ck_rule_suggestions_decided_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_rule_suggestions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_rule_suggestions_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_suggestions")),
    )
    op.create_index(
        "ix_rule_suggestions_user_id",
        "rule_suggestions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_rule_suggestions_contact_id",
        "rule_suggestions",
        ["contact_id"],
        unique=False,
    )
    op.create_index(
        "uq_rule_suggestions_tone_user_contact_firmness",
        "rule_suggestions",
        ["user_id", "contact_id", "firmness"],
        unique=True,
        postgresql_where=sa.text("source = 'tone'"),
    )
    op.create_index(
        "uq_rule_suggestions_pending_user_contact_source",
        "rule_suggestions",
        ["user_id", "contact_id", "source"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "tone_signals",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("values", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.CheckConstraint(
            "cardinality(values) <= 10",
            name=op.f("ck_tone_signals_window"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tone_signals_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_tone_signals_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "contact_id", name=op.f("pk_tone_signals")),
    )


def downgrade() -> None:
    op.drop_table("tone_signals")
    op.drop_index(
        "uq_rule_suggestions_pending_user_contact_source",
        table_name="rule_suggestions",
    )
    op.drop_index(
        "uq_rule_suggestions_tone_user_contact_firmness",
        table_name="rule_suggestions",
    )
    op.drop_index("ix_rule_suggestions_contact_id", table_name="rule_suggestions")
    op.drop_index("ix_rule_suggestions_user_id", table_name="rule_suggestions")
    op.drop_table("rule_suggestions")
