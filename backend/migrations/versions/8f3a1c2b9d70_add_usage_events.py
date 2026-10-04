"""add_usage_events

Revision ID: 8f3a1c2b9d70
Revises: 1cdb5910d70e
Create Date: 2026-10-04 14:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a1c2b9d70"
down_revision: str | Sequence[str] | None = "1cdb5910d70e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_pseudonym", sa.String(length=64), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("unavailable_kind", sa.Text(), nullable=True),
        sa.Column("safety", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ttfc_ms", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("billable_tokens", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "user_pseudonym ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_usage_events_user_pseudonym_hex"),
        ),
        sa.CheckConstraint(
            "scenario IN ('decode', 'soften', 'help_say')",
            name=op.f("ck_usage_events_usage_scenario"),
        ),
        sa.CheckConstraint(
            "surface IN ('dm', 'inline', 'miniapp')",
            name=op.f("ck_usage_events_usage_surface"),
        ),
        sa.CheckConstraint(
            "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable')",
            name=op.f("ck_usage_events_usage_outcome"),
        ),
        sa.CheckConstraint("attempts >= 1", name=op.f("ck_usage_events_usage_attempts")),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_usage_events_usage_latency")),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name=op.f("ck_usage_events_usage_input_tokens"),
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name=op.f("ck_usage_events_usage_output_tokens"),
        ),
        sa.CheckConstraint(
            "billable_tokens >= 0",
            name=op.f("ck_usage_events_usage_billable_tokens"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index("ix_usage_events_occurred_at", "usage_events", ["occurred_at"], unique=False)
    op.create_index(
        "ix_usage_events_user_pseudonym_occurred_at",
        "usage_events",
        ["user_pseudonym", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_pseudonym_occurred_at", table_name="usage_events")
    op.drop_index("ix_usage_events_occurred_at", table_name="usage_events")
    op.drop_table("usage_events")
