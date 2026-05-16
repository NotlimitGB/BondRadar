"""paper trading virtual portfolio

Revision ID: 202605140012
Revises: 202605140011
Create Date: 2026-05-16 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140012"
down_revision: str | None = "202605140011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_portfolios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("base_currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("initial_capital", sa.Numeric(20, 6), nullable=False),
        sa.Column("cash_balance", sa.Numeric(20, 6), nullable=False),
        sa.Column("current_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("model_run_id", sa.Integer(), nullable=True),
        sa.Column("return_method", sa.String(length=32), nullable=True),
        sa.Column("horizon_days", sa.Integer(), nullable=True),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_rebalanced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rebalance_as_of_date", sa.Date(), nullable=True),
        sa.Column("last_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('active', 'archived')",
            name="ck_paper_portfolio_status",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml_model_runs.id"],
            name="fk_paper_portfolio_model_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_portfolios"),
    )
    op.create_index("ix_paper_portfolios_status", "paper_portfolios", ["status"])
    op.create_index("ix_paper_portfolios_model_run_id", "paper_portfolios", ["model_run_id"])

    op.create_table(
        "paper_portfolio_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("allocation_weight", sa.Numeric(12, 10), nullable=False),
        sa.Column("allocation_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("current_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("probability_positive", sa.Numeric(12, 10), nullable=True),
        sa.Column("predicted_label", sa.String(length=64), nullable=True),
        sa.Column("yield_to_maturity", sa.Numeric(7, 3), nullable=True),
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
        sa.Column("decision_status", sa.String(length=32), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("source_model_run_id", sa.Integer(), nullable=True),
        sa.Column("source_prediction_id", sa.Integer(), nullable=True),
        sa.Column("source_details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["paper_portfolios.id"],
            name="fk_paper_position_portfolio",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name="fk_paper_position_bond",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_paper_position_company",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_model_run_id"],
            ["ml_model_runs.id"],
            name="fk_paper_position_model_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_prediction_id"],
            ["ml_predictions.id"],
            name="fk_paper_position_prediction",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_positions"),
        sa.UniqueConstraint("portfolio_id", "bond_id", name="uq_paper_position_bond"),
    )
    op.create_index("ix_paper_positions_portfolio_id", "paper_portfolio_positions", ["portfolio_id"])
    op.create_index("ix_paper_positions_bond_id", "paper_portfolio_positions", ["bond_id"])
    op.create_index("ix_paper_positions_is_active", "paper_portfolio_positions", ["is_active"])

    op.create_table(
        "paper_portfolio_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=True),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("amount_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("weight_delta", sa.Numeric(12, 10), nullable=True),
        sa.Column("fee_amount", sa.Numeric(20, 6), nullable=True),
        sa.Column("portfolio_value_before", sa.Numeric(20, 6), nullable=True),
        sa.Column("portfolio_value_after", sa.Numeric(20, 6), nullable=True),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "transaction_type in ('portfolio_created', 'allocation_increase', "
            "'allocation_decrease', 'allocation_removed', 'rebalance_fee', "
            "'period_return', 'cash_adjustment', 'snapshot')",
            name="ck_paper_tx_type",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["paper_portfolios.id"],
            name="fk_paper_tx_portfolio",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name="fk_paper_tx_bond",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_transactions"),
    )
    op.create_index("ix_paper_tx_portfolio_id", "paper_portfolio_transactions", ["portfolio_id"])
    op.create_index("ix_paper_tx_as_of_date", "paper_portfolio_transactions", ["as_of_date"])
    op.create_index("ix_paper_tx_type", "paper_portfolio_transactions", ["transaction_type"])

    op.create_table(
        "paper_portfolio_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("portfolio_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("cash_balance", sa.Numeric(20, 6), nullable=False),
        sa.Column("allocated_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("allocated_weight", sa.Numeric(12, 10), nullable=False),
        sa.Column("unallocated_weight", sa.Numeric(12, 10), nullable=False),
        sa.Column("positions_count", sa.Integer(), nullable=False),
        sa.Column("active_positions_count", sa.Integer(), nullable=False),
        sa.Column("cumulative_return", sa.Numeric(12, 10), nullable=False),
        sa.Column("period_return", sa.Numeric(12, 10), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["paper_portfolios.id"],
            name="fk_paper_snapshot_portfolio",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_snapshots"),
        sa.UniqueConstraint("portfolio_id", "as_of_date", name="uq_paper_snapshot_date"),
    )
    op.create_index("ix_paper_snapshots_portfolio_id", "paper_portfolio_snapshots", ["portfolio_id"])
    op.create_index("ix_paper_snapshots_as_of_date", "paper_portfolio_snapshots", ["as_of_date"])


def downgrade() -> None:
    op.drop_index("ix_paper_snapshots_as_of_date", table_name="paper_portfolio_snapshots")
    op.drop_index("ix_paper_snapshots_portfolio_id", table_name="paper_portfolio_snapshots")
    op.drop_table("paper_portfolio_snapshots")
    op.drop_index("ix_paper_tx_type", table_name="paper_portfolio_transactions")
    op.drop_index("ix_paper_tx_as_of_date", table_name="paper_portfolio_transactions")
    op.drop_index("ix_paper_tx_portfolio_id", table_name="paper_portfolio_transactions")
    op.drop_table("paper_portfolio_transactions")
    op.drop_index("ix_paper_positions_is_active", table_name="paper_portfolio_positions")
    op.drop_index("ix_paper_positions_bond_id", table_name="paper_portfolio_positions")
    op.drop_index("ix_paper_positions_portfolio_id", table_name="paper_portfolio_positions")
    op.drop_table("paper_portfolio_positions")
    op.drop_index("ix_paper_portfolios_model_run_id", table_name="paper_portfolios")
    op.drop_index("ix_paper_portfolios_status", table_name="paper_portfolios")
    op.drop_table("paper_portfolios")
