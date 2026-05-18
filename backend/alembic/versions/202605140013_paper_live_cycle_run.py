"""paper live cycle run log

Revision ID: 202605140013
Revises: 202605140012
Create Date: 2026-05-18 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140013"
down_revision: str | None = "202605140012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_live_cycle_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=True),
        sa.Column("client_cycle_key", sa.String(length=255), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("readiness_status", sa.String(length=32), nullable=True),
        sa.Column("selected_model_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "selected_model_run_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("readiness_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "mark_period_result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "rebalance_result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'blocked', 'failed')",
            name="ck_paper_live_cycle_status",
        ),
        sa.CheckConstraint(
            "mode in ('manual')",
            name="ck_paper_live_cycle_mode",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["paper_portfolios.id"],
            name="fk_paper_live_cycle_portfolio",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_live_cycle_runs"),
        sa.UniqueConstraint("client_cycle_key", name="uq_paper_live_cycle_key"),
    )
    op.create_index("ix_paper_live_cycles_status", "paper_live_cycle_runs", ["status"])
    op.create_index("ix_paper_live_cycles_portfolio_id", "paper_live_cycle_runs", ["portfolio_id"])
    op.create_index("ix_paper_live_cycles_client_cycle_key", "paper_live_cycle_runs", ["client_cycle_key"])
    op.create_index("ix_paper_live_cycles_as_of_date", "paper_live_cycle_runs", ["as_of_date"])
    op.create_index("ix_paper_live_cycles_model_run_id", "paper_live_cycle_runs", ["selected_model_run_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_live_cycles_model_run_id", table_name="paper_live_cycle_runs")
    op.drop_index("ix_paper_live_cycles_as_of_date", table_name="paper_live_cycle_runs")
    op.drop_index("ix_paper_live_cycles_client_cycle_key", table_name="paper_live_cycle_runs")
    op.drop_index("ix_paper_live_cycles_portfolio_id", table_name="paper_live_cycle_runs")
    op.drop_index("ix_paper_live_cycles_status", table_name="paper_live_cycle_runs")
    op.drop_table("paper_live_cycle_runs")
