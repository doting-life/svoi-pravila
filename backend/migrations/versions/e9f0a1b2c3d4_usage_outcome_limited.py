"""usage_outcome_limited

Revision ID: e9f0a1b2c3d4
Revises: a7c8d9e0f1b2
Create Date: 2026-10-07 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "a7c8d9e0f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHAPE = (
    "("
    "event_kind = 'generation' AND outcome = 'screened' AND safety = 'crisis' "
    "AND model IS NULL AND prompt_version IS NULL AND attempts = 0 "
    "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
    "AND ttfc_ms IS NULL AND unavailable_kind IS NULL AND variant_firmness IS NULL "
    "AND limit_kind IS NULL"
    ") OR ("
    "event_kind = 'generation' AND outcome = 'limited' "
    "AND limit_kind IN ('user_quota', 'global_budget') "
    "AND safety IS NULL AND model IS NULL AND prompt_version IS NULL "
    "AND attempts = 0 AND input_tokens = 0 AND output_tokens = 0 "
    "AND billable_tokens = 0 AND ttfc_ms IS NULL AND unavailable_kind IS NULL "
    "AND variant_firmness IS NULL"
    ") OR ("
    "event_kind = 'generation' AND outcome NOT IN ('screened', 'limited') "
    "AND model IS NOT NULL AND model <> '' "
    "AND prompt_version IS NOT NULL AND prompt_version <> '' "
    "AND attempts >= 1 AND variant_firmness IS NULL AND limit_kind IS NULL"
    ") OR ("
    "event_kind = 'result_chosen' AND model IS NULL AND prompt_version IS NULL "
    "AND attempts = 0 AND latency_ms = 0 AND ttfc_ms IS NULL "
    "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
    "AND safety IS NULL AND unavailable_kind IS NULL AND outcome = 'ok' "
    "AND variant_firmness IN ('gentle', 'balanced', 'firm') AND limit_kind IS NULL"
    ")"
)

_PREVIOUS_SHAPE = (
    "("
    "event_kind = 'generation' AND outcome = 'screened' AND safety = 'crisis' "
    "AND model IS NULL AND prompt_version IS NULL AND attempts = 0 "
    "AND input_tokens = 0 AND output_tokens = 0 AND billable_tokens = 0 "
    "AND ttfc_ms IS NULL AND unavailable_kind IS NULL AND variant_firmness IS NULL"
    ") OR ("
    "event_kind = 'generation' AND outcome <> 'screened' "
    "AND model IS NOT NULL AND model <> '' "
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
    op.add_column("usage_events", sa.Column("limit_kind", sa.Text(), nullable=True))
    op.create_check_constraint(
        "usage_limit_kind",
        "usage_events",
        "limit_kind IS NULL OR limit_kind IN ('user_quota', 'global_budget')",
    )
    op.create_check_constraint(
        "usage_limit_kind_outcome",
        "usage_events",
        "("
        "outcome = 'limited' AND limit_kind IS NOT NULL"
        ") OR ("
        "outcome <> 'limited' AND limit_kind IS NULL"
        ")",
    )
    op.drop_constraint(op.f("ck_usage_events_usage_outcome"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_outcome",
        "usage_events",
        "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable', 'screened', 'limited')",
    )
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.create_check_constraint("usage_event_kind_shape", "usage_events", _SHAPE)


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE outcome = 'limited'")
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.create_check_constraint("usage_event_kind_shape", "usage_events", _PREVIOUS_SHAPE)
    op.drop_constraint(op.f("ck_usage_events_usage_outcome"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_outcome",
        "usage_events",
        "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable', 'screened')",
    )
    op.drop_constraint(
        op.f("ck_usage_events_usage_limit_kind_outcome"), "usage_events", type_="check"
    )
    op.drop_constraint(op.f("ck_usage_events_usage_limit_kind"), "usage_events", type_="check")
    op.drop_column("usage_events", "limit_kind")
