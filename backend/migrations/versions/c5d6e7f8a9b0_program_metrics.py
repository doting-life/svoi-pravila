"""Program metrics: LLM prices, daily LLM and load aggregates, new daily columns.

Revision ID: c5d6e7f8a9b0
Revises: d1e2f3a4b5c6
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCENARIO = "scenario IN ('decode', 'rewrite', 'suggest_rule')"
_SURFACE = "surface IN ('dm', 'inline', 'miniapp')"
_PRICE_VALID_FROM = date(2026, 9, 29)
_SEED_PRICES: tuple[tuple[str, Decimal], ...] = (
    ("GigaChat-3-Lightning", Decimal("0.0650")),
    ("GigaChat-2-Pro", Decimal("0.5000")),
    ("GigaChat-3-Pro", Decimal("0.5000")),
    ("GigaChat-2-Max", Decimal("0.6500")),
)
_READER = "svoi_analytics_read"
_NEW_TABLES = ("analytics_daily_llm", "analytics_daily_load", "llm_prices")
_LOAD_NONNEG = (
    "peak_tpm",
    "peak_tps",
    "peak_llm_calls_per_minute",
    "peak_llm_calls_per_second",
)
_LLM_NONNEG = ("generations", "llm_calls", "input_tokens", "output_tokens", "billable_tokens")


def upgrade() -> None:
    op.create_table(
        "llm_prices",
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("rub_per_1k_tokens", sa.Numeric(10, 4), nullable=False),
        sa.CheckConstraint("rub_per_1k_tokens > 0", name=op.f("ck_llm_prices_rub_per_1k_positive")),
        sa.CheckConstraint("model <> ''", name=op.f("ck_llm_prices_model_nonempty")),
        sa.PrimaryKeyConstraint("model", "valid_from", name=op.f("pk_llm_prices")),
    )
    prices = sa.table(
        "llm_prices",
        sa.column("model", sa.Text()),
        sa.column("valid_from", sa.Date()),
        sa.column("rub_per_1k_tokens", sa.Numeric(10, 4)),
    )
    op.bulk_insert(
        prices,
        [
            {
                "model": model,
                "valid_from": _PRICE_VALID_FROM,
                "rub_per_1k_tokens": price,
            }
            for model, price in _SEED_PRICES
        ],
    )

    op.create_table(
        "analytics_daily_llm",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("generations", sa.Integer(), nullable=False),
        sa.Column("llm_calls", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("billable_tokens", sa.BigInteger(), nullable=False),
        sa.Column("price_rub_per_1k", sa.Numeric(10, 4), nullable=True),
        sa.Column("cost_rub", sa.Numeric(14, 4), nullable=True),
        sa.CheckConstraint(_SCENARIO, name=op.f("ck_analytics_daily_llm_usage_scenario")),
        sa.CheckConstraint(_SURFACE, name=op.f("ck_analytics_daily_llm_usage_surface")),
        *(
            sa.CheckConstraint(
                f"{column} >= 0", name=op.f(f"ck_analytics_daily_llm_{column}_nonneg")
            )
            for column in _LLM_NONNEG
        ),
        sa.PrimaryKeyConstraint(
            "day", "scenario", "surface", "model", name=op.f("pk_analytics_daily_llm")
        ),
    )
    op.create_table(
        "analytics_daily_load",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("peak_tpm", sa.BigInteger(), nullable=False),
        sa.Column("peak_tps", sa.BigInteger(), nullable=False),
        sa.Column("peak_llm_calls_per_minute", sa.BigInteger(), nullable=False),
        sa.Column("peak_llm_calls_per_second", sa.BigInteger(), nullable=False),
        sa.Column("peak_tpm_minute", sa.DateTime(timezone=True), nullable=True),
        *(
            sa.CheckConstraint(
                f"{column} >= 0", name=op.f(f"ck_analytics_daily_load_{column}_nonneg")
            )
            for column in _LOAD_NONNEG
        ),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_analytics_daily_load")),
    )

    op.add_column(
        "analytics_daily",
        sa.Column("llm_calls", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analytics_daily",
        sa.Column("target_users_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analytics_daily",
        sa.Column("llm_cost_rub", sa.Numeric(14, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "analytics_daily",
        sa.Column("unpriced_models", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint("llm_calls_nonneg", "analytics_daily", "llm_calls >= 0")
    op.create_check_constraint(
        "target_users_total_nonneg", "analytics_daily", "target_users_total >= 0"
    )
    op.create_check_constraint("llm_cost_rub_nonneg", "analytics_daily", "llm_cost_rub >= 0")
    op.create_check_constraint("unpriced_models_nonneg", "analytics_daily", "unpriced_models >= 0")
    for column in ("llm_calls", "target_users_total", "llm_cost_rub", "unpriced_models"):
        op.alter_column("analytics_daily", column, server_default=None)

    for table in _NEW_TABLES:
        op.execute(sa.text(f"REVOKE ALL ON TABLE {table} FROM PUBLIC"))
        op.execute(sa.text(f"GRANT SELECT ON TABLE {table} TO {_READER}"))


def downgrade() -> None:
    op.drop_constraint("unpriced_models_nonneg", "analytics_daily", type_="check")
    op.drop_constraint("llm_cost_rub_nonneg", "analytics_daily", type_="check")
    op.drop_constraint("target_users_total_nonneg", "analytics_daily", type_="check")
    op.drop_constraint("llm_calls_nonneg", "analytics_daily", type_="check")
    op.drop_column("analytics_daily", "unpriced_models")
    op.drop_column("analytics_daily", "llm_cost_rub")
    op.drop_column("analytics_daily", "target_users_total")
    op.drop_column("analytics_daily", "llm_calls")
    op.drop_table("analytics_daily_load")
    op.drop_table("analytics_daily_llm")
    op.drop_table("llm_prices")
