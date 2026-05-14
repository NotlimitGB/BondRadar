"""ml dataset foundation

Revision ID: 202605140006
Revises: 202605140005
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140006"
down_revision: str | None = "202605140005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bond_market_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("clean_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("dirty_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("nkd", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column(
            "yield_to_maturity", sa.Numeric(precision=7, scale=3), nullable=True
        ),
        sa.Column("duration_years", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("volume", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
        sa.Column("spread_to_ofz", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "source",
            sa.String(length=64),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_market_snapshots_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_market_snapshots")),
        sa.UniqueConstraint(
            "bond_id",
            "trade_date",
            "source",
            name=op.f(
                "uq_bond_market_snapshots_bond_market_snapshots_bond_date_source_unique"
            ),
        ),
    )
    op.create_index(
        op.f("ix_bond_market_snapshots_bond_id"),
        "bond_market_snapshots",
        ["bond_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bond_market_snapshots_trade_date"),
        "bond_market_snapshots",
        ["trade_date"],
        unique=False,
    )

    op.create_table(
        "dataset_build_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("as_of_date_from", sa.Date(), nullable=False),
        sa.Column("as_of_date_to", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("features_created", sa.Integer(), nullable=False),
        sa.Column("labels_created", sa.Integer(), nullable=False),
        sa.Column("features_updated", sa.Integer(), nullable=False),
        sa.Column("labels_updated", sa.Integer(), nullable=False),
        sa.Column("errors_count", sa.Integer(), nullable=False),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'failed', 'completed_with_errors')",
            name=op.f("ck_dataset_build_runs_dataset_build_runs_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_build_runs")),
    )

    op.create_table(
        "bond_feature_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("bond_score_id", sa.Integer(), nullable=True),
        sa.Column("company_score_id", sa.Integer(), nullable=True),
        sa.Column("financial_report_id", sa.Integer(), nullable=True),
        sa.Column("bond_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("company_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "yield_to_maturity", sa.Numeric(precision=7, scale=3), nullable=True
        ),
        sa.Column("duration_years", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
        sa.Column("volume", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("spread_to_ofz", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "net_debt_to_ebitda", sa.Numeric(precision=12, scale=6), nullable=True
        ),
        sa.Column("debt_to_equity", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "interest_coverage", sa.Numeric(precision=12, scale=6), nullable=True
        ),
        sa.Column(
            "cash_to_short_term_debt",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
        sa.Column(
            "ocf_to_total_debt", sa.Numeric(precision=12, scale=6), nullable=True
        ),
        sa.Column("net_profit_margin", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("days_to_maturity", sa.Integer(), nullable=True),
        sa.Column("has_offer", sa.Boolean(), nullable=True),
        sa.Column("has_amortization", sa.Boolean(), nullable=True),
        sa.Column("missing_data_count", sa.Integer(), nullable=False),
        sa.Column(
            "features_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_feature_snapshots_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bond_score_id"],
            ["bond_scores.id"],
            name=op.f("fk_bond_feature_snapshots_bond_score_id_bond_scores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_bond_feature_snapshots_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_score_id"],
            ["company_scores.id"],
            name=op.f("fk_bond_feature_snapshots_company_score_id_company_scores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["financial_report_id"],
            ["financial_reports.id"],
            name=op.f(
                "fk_bond_feature_snapshots_financial_report_id_financial_reports"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["market_snapshot_id"],
            ["bond_market_snapshots.id"],
            name=op.f(
                "fk_bond_feature_snapshots_market_snapshot_id_bond_market_snapshots"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_feature_snapshots")),
        sa.UniqueConstraint(
            "bond_id",
            "as_of_date",
            name=op.f(
                "uq_bond_feature_snapshots_bond_feature_snapshots_bond_as_of_unique"
            ),
        ),
    )
    for column in (
        "bond_id",
        "company_id",
        "as_of_date",
        "market_snapshot_id",
        "bond_score_id",
        "company_score_id",
        "financial_report_id",
    ):
        op.create_index(
            op.f(f"ix_bond_feature_snapshots_{column}"),
            "bond_feature_snapshots",
            [column],
            unique=False,
        )

    op.create_table(
        "bond_return_labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("start_market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("end_market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("start_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("end_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("future_return", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("benchmark_return", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("excess_return", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("label_binary", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "label in ('outperform', 'underperform', 'positive_return', "
            "'negative_return', 'insufficient_data')",
            name=op.f("ck_bond_return_labels_bond_return_labels_label_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_return_labels_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["end_market_snapshot_id"],
            ["bond_market_snapshots.id"],
            name=op.f(
                "fk_bond_return_labels_end_market_snapshot_id_bond_market_snapshots"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["start_market_snapshot_id"],
            ["bond_market_snapshots.id"],
            name=op.f(
                "fk_bond_return_labels_start_market_snapshot_id_bond_market_snapshots"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_return_labels")),
        sa.UniqueConstraint(
            "bond_id",
            "as_of_date",
            "horizon_days",
            name=op.f(
                "uq_bond_return_labels_bond_return_labels_bond_as_of_horizon_unique"
            ),
        ),
    )
    for column in (
        "bond_id",
        "as_of_date",
        "horizon_days",
        "start_market_snapshot_id",
        "end_market_snapshot_id",
    ):
        op.create_index(
            op.f(f"ix_bond_return_labels_{column}"),
            "bond_return_labels",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("bond_return_labels")
    op.drop_table("bond_feature_snapshots")
    op.drop_table("dataset_build_runs")
    op.drop_table("bond_market_snapshots")
