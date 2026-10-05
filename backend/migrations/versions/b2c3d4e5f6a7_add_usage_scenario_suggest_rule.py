"""add_usage_scenario_suggest_rule

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-05 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_usage_events_usage_scenario"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_scenario",
        "usage_events",
        "scenario IN ('decode', 'soften', 'help_say', 'suggest_rule')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM usage_events WHERE scenario = 'suggest_rule'")
    op.drop_constraint(op.f("ck_usage_events_usage_scenario"), "usage_events", type_="check")
    op.create_check_constraint(
        "usage_scenario",
        "usage_events",
        "scenario IN ('decode', 'soften', 'help_say')",
    )
