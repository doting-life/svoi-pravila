"""add_usage_event_kind_and_variant_firmness

Revision ID: c4e7b2a91f03
Revises: 8f3a1c2b9d70
Create Date: 2026-10-04 17:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7b2a91f03"
down_revision: str | Sequence[str] | None = "8f3a1c2b9d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHAPE = (
    "("
    "event_kind = 'generation' AND model IS NOT NULL AND model <> '' "
    "AND prompt_version IS NOT NULL AND prompt_version <> '' "
    "AND attempts >= 1 AND variant_firmness IS NULL"
    ") OR ("
    "event_kind = 'result_chosen' AND model IS NULL AND prompt_version IS NULL "
    "AND attempts = 0 AND latency_ms = 0 AND ttfc_ms IS NULL "
    "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
    "AND safety IS NULL AND unavailable_kind IS NULL AND outcome = 'ok' "
    "AND variant_firmness IN ('gentle', 'balanced', 'firm')"
    ")"
)


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column(
            "event_kind",
            sa.Text(),
            nullable=False,
            server_default="generation",
        ),
    )
    op.add_column("usage_events", sa.Column("variant_firmness", sa.Text(), nullable=True))
    op.alter_column("usage_events", "model", existing_type=sa.Text(), nullable=True)
    op.alter_column("usage_events", "prompt_version", existing_type=sa.Text(), nullable=True)
    op.drop_constraint(op.f("ck_usage_events_usage_attempts"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_event_kind",
        "usage_events",
        "event_kind IN ('generation', 'result_chosen')",
    )
    op.create_check_constraint(
        "usage_variant_firmness",
        "usage_events",
        "variant_firmness IS NULL OR variant_firmness IN ('gentle', 'balanced', 'firm')",
    )
    op.create_check_constraint("usage_event_kind_shape", "usage_events", _SHAPE)
    op.alter_column("usage_events", "event_kind", server_default=None)


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE event_kind = 'result_chosen'")
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_usage_events_usage_variant_firmness"), "usage_events", type_="check"
    )
    op.drop_constraint(op.f("ck_usage_events_usage_event_kind"), "usage_events", type_="check")
    op.create_check_constraint("usage_attempts", "usage_events", "attempts >= 1")
    op.alter_column("usage_events", "prompt_version", existing_type=sa.Text(), nullable=False)
    op.alter_column("usage_events", "model", existing_type=sa.Text(), nullable=False)
    op.drop_column("usage_events", "variant_firmness")
    op.drop_column("usage_events", "event_kind")
