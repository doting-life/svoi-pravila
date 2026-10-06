"""add_analytics_aggregates

Revision ID: f1a2b3c4d5e6
Revises: b2c3d4e5f6a7
Create Date: 2026-10-06 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCENARIO = "scenario IN ('decode', 'soften', 'help_say', 'suggest_rule')"
_SURFACE = "surface IN ('dm', 'inline', 'miniapp')"
_JOB = "job IN ('daily_aggregates', 'cohorts', 'purge')"
_STATUS = "status IN ('running', 'succeeded', 'failed', 'skipped_locked')"
_ERROR = (
    "error_kind IS NULL OR error_kind IN "
    "('unknown_timezone', 'catch_up_capped', 'database', 'network', 'cancelled')"
)


def upgrade() -> None:
    op.create_table(
        "analytics_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.Column("appeals", sa.Integer(), nullable=False),
        sa.Column("new_users", sa.Integer(), nullable=False),
        sa.Column("generations", sa.Integer(), nullable=False),
        sa.Column("generation_errors", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "active_users >= 0",
            name=op.f("ck_analytics_daily_active_users_nonneg"),
        ),
        sa.CheckConstraint("appeals >= 0", name=op.f("ck_analytics_daily_appeals_nonneg")),
        sa.CheckConstraint("new_users >= 0", name=op.f("ck_analytics_daily_new_users_nonneg")),
        sa.CheckConstraint("generations >= 0", name=op.f("ck_analytics_daily_generations_nonneg")),
        sa.CheckConstraint(
            "generation_errors >= 0",
            name=op.f("ck_analytics_daily_generation_errors_nonneg"),
        ),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_analytics_daily")),
    )
    op.create_table(
        "analytics_daily_scenario",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("appeals", sa.Integer(), nullable=False),
        sa.Column("users", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=False),
        sa.Column("refused", sa.Integer(), nullable=False),
        sa.Column("screened", sa.Integer(), nullable=False),
        sa.Column("invalid_output", sa.Integer(), nullable=False),
        sa.Column("unavailable", sa.Integer(), nullable=False),
        sa.Column("chosen", sa.Integer(), nullable=False),
        sa.Column("latency_p50_ms", sa.Double(), nullable=True),
        sa.Column("latency_p95_ms", sa.Double(), nullable=True),
        sa.Column("ttfc_p50_ms", sa.Double(), nullable=True),
        sa.Column("ttfc_p95_ms", sa.Double(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("billable_tokens", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(_SCENARIO, name=op.f("ck_analytics_daily_scenario_usage_scenario")),
        sa.CheckConstraint(_SURFACE, name=op.f("ck_analytics_daily_scenario_usage_surface")),
        sa.CheckConstraint("appeals >= 0", name=op.f("ck_analytics_daily_scenario_appeals_nonneg")),
        sa.CheckConstraint("users >= 0", name=op.f("ck_analytics_daily_scenario_users_nonneg")),
        sa.CheckConstraint("ok >= 0", name=op.f("ck_analytics_daily_scenario_ok_nonneg")),
        sa.CheckConstraint("refused >= 0", name=op.f("ck_analytics_daily_scenario_refused_nonneg")),
        sa.CheckConstraint(
            "screened >= 0",
            name=op.f("ck_analytics_daily_scenario_screened_nonneg"),
        ),
        sa.CheckConstraint(
            "invalid_output >= 0",
            name=op.f("ck_analytics_daily_scenario_invalid_output_nonneg"),
        ),
        sa.CheckConstraint(
            "unavailable >= 0",
            name=op.f("ck_analytics_daily_scenario_unavailable_nonneg"),
        ),
        sa.CheckConstraint("chosen >= 0", name=op.f("ck_analytics_daily_scenario_chosen_nonneg")),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name=op.f("ck_analytics_daily_scenario_input_tokens_nonneg"),
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name=op.f("ck_analytics_daily_scenario_output_tokens_nonneg"),
        ),
        sa.CheckConstraint(
            "billable_tokens >= 0",
            name=op.f("ck_analytics_daily_scenario_billable_tokens_nonneg"),
        ),
        sa.PrimaryKeyConstraint(
            "day",
            "scenario",
            "surface",
            name=op.f("pk_analytics_daily_scenario"),
        ),
    )
    op.create_table(
        "analytics_cohorts",
        sa.Column("cohort_day", sa.Date(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("d1_retained", sa.Integer(), nullable=True),
        sa.Column("d7_retained", sa.Integer(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size >= 0", name=op.f("ck_analytics_cohorts_size_nonneg")),
        sa.CheckConstraint(
            "d1_retained IS NULL OR d1_retained >= 0",
            name=op.f("ck_analytics_cohorts_d1_nonneg"),
        ),
        sa.CheckConstraint(
            "d7_retained IS NULL OR d7_retained >= 0",
            name=op.f("ck_analytics_cohorts_d7_nonneg"),
        ),
        sa.PrimaryKeyConstraint("cohort_day", name=op.f("pk_analytics_cohorts")),
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("target_day", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_affected", sa.Integer(), nullable=False),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.CheckConstraint(_JOB, name=op.f("ck_job_runs_job_name")),
        sa.CheckConstraint(_STATUS, name=op.f("ck_job_runs_job_status")),
        sa.CheckConstraint(_ERROR, name=op.f("ck_job_runs_job_error_kind")),
        sa.CheckConstraint("rows_affected >= 0", name=op.f("ck_job_runs_rows_affected_nonneg")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    op.create_index(
        "ix_job_runs_job_status_target_day",
        "job_runs",
        ["job", "status", "target_day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_runs_job_status_target_day", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("analytics_cohorts")
    op.drop_table("analytics_daily_scenario")
    op.drop_table("analytics_daily")
