"""add_usage_outcome_screened

Revision ID: d8f1c3a04b21
Revises: c4e7b2a91f03
Create Date: 2026-10-04 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d8f1c3a04b21"
down_revision: str | Sequence[str] | None = "c4e7b2a91f03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHAPE = (
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

_PREVIOUS_SHAPE = (
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
    op.drop_constraint(op.f("ck_usage_events_usage_outcome"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_outcome",
        "usage_events",
        "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable', 'screened')",
    )
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.create_check_constraint("usage_event_kind_shape", "usage_events", _SHAPE)


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE outcome = 'screened'")
    op.drop_constraint(
        op.f("ck_usage_events_usage_event_kind_shape"), "usage_events", type_="check"
    )
    op.create_check_constraint("usage_event_kind_shape", "usage_events", _PREVIOUS_SHAPE)
    op.drop_constraint(op.f("ck_usage_events_usage_outcome"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_outcome",
        "usage_events",
        "outcome IN ('ok', 'invalid_output', 'refused', 'unavailable')",
    )
