"""Add limited/budget columns to analytics daily aggregates.

Revision ID: b3c4d5e6f7a8
Revises: e9f0a1b2c3d4
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analytics_daily_scenario",
        sa.Column(
            "limited_user_quota",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "analytics_daily_scenario",
        sa.Column(
            "limited_global_budget",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "limited_user_quota_nonneg",
        "analytics_daily_scenario",
        "limited_user_quota >= 0",
    )
    op.create_check_constraint(
        "limited_global_budget_nonneg",
        "analytics_daily_scenario",
        "limited_global_budget >= 0",
    )

    op.add_column(
        "analytics_daily",
        sa.Column(
            "users_limited",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "analytics_daily",
        sa.Column(
            "billable_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "analytics_daily",
        sa.Column(
            "llm_budget_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "users_limited_nonneg",
        "analytics_daily",
        "users_limited >= 0",
    )
    op.create_check_constraint(
        "billable_tokens_nonneg",
        "analytics_daily",
        "billable_tokens >= 0",
    )
    op.create_check_constraint(
        "llm_budget_tokens_nonneg",
        "analytics_daily",
        "llm_budget_tokens >= 0",
    )

    op.alter_column("analytics_daily_scenario", "limited_user_quota", server_default=None)
    op.alter_column("analytics_daily_scenario", "limited_global_budget", server_default=None)
    op.alter_column("analytics_daily", "users_limited", server_default=None)
    op.alter_column("analytics_daily", "billable_tokens", server_default=None)
    op.alter_column("analytics_daily", "llm_budget_tokens", server_default=None)


def downgrade() -> None:
    op.drop_constraint("llm_budget_tokens_nonneg", "analytics_daily", type_="check")
    op.drop_constraint("billable_tokens_nonneg", "analytics_daily", type_="check")
    op.drop_constraint("users_limited_nonneg", "analytics_daily", type_="check")
    op.drop_column("analytics_daily", "llm_budget_tokens")
    op.drop_column("analytics_daily", "billable_tokens")
    op.drop_column("analytics_daily", "users_limited")

    op.drop_constraint("limited_global_budget_nonneg", "analytics_daily_scenario", type_="check")
    op.drop_constraint("limited_user_quota_nonneg", "analytics_daily_scenario", type_="check")
    op.drop_column("analytics_daily_scenario", "limited_global_budget")
    op.drop_column("analytics_daily_scenario", "limited_user_quota")
