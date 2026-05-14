"""credit health risk gates

Revision ID: 202605140008
Revises: 202605140007
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140008"
down_revision: str | None = "202605140007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_credit_health_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("financial_report_id", sa.Integer(), nullable=True),
        sa.Column("company_score_id", sa.Integer(), nullable=True),
        sa.Column("credit_health_score", sa.Integer(), nullable=False),
        sa.Column("credit_status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("data_quality_level", sa.String(length=32), nullable=False),
        sa.Column("debt_to_ebitda", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("interest_coverage", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "cash_to_short_term_debt",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
        sa.Column("ocf_to_total_debt", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("net_profit_margin", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("revenue", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("ebitda", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("net_debt", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("total_debt", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("cash", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("equity", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("short_term_debt", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column(
            "operating_cash_flow",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
        ),
        sa.Column("net_profit", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("interest_expense", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("risk_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "positive_factors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("missing_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "credit_health_score between 0 and 100",
            name=op.f(
                "ck_company_credit_health_snapshots_company_credit_health_score_range"
            ),
        ),
        sa.CheckConstraint(
            "credit_status in ('credit_stable', 'credit_watchlist', "
            "'credit_stressed', 'credit_distressed', 'insufficient_data')",
            name=op.f(
                "ck_company_credit_health_snapshots_company_credit_health_status_allowed"
            ),
        ),
        sa.CheckConstraint(
            "risk_level in ('low', 'medium', 'high', 'critical', 'unknown')",
            name=op.f(
                "ck_company_credit_health_snapshots_company_credit_health_risk_allowed"
            ),
        ),
        sa.CheckConstraint(
            "data_quality_level in ('high', 'medium', 'low', 'insufficient')",
            name=op.f(
                "ck_company_credit_health_snapshots_company_credit_health_data_quality_allowed"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_credit_health_snapshots_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_score_id"],
            ["company_scores.id"],
            name=op.f(
                "fk_company_credit_health_snapshots_company_score_id_company_scores"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["financial_report_id"],
            ["financial_reports.id"],
            name=op.f(
                "fk_company_credit_health_snapshots_financial_report_id_financial_reports"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_company_credit_health_snapshots"),
        ),
        sa.UniqueConstraint(
            "company_id",
            "as_of_date",
            name=op.f(
                "uq_company_credit_health_snapshots_company_credit_health_company_as_of_unique"
            ),
        ),
    )
    for column in (
        "company_id",
        "as_of_date",
        "financial_report_id",
        "company_score_id",
        "credit_status",
    ):
        op.create_index(
            op.f(f"ix_company_credit_health_snapshots_{column}"),
            "company_credit_health_snapshots",
            [column],
            unique=False,
        )

    op.create_table(
        "bond_risk_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("company_credit_health_id", sa.Integer(), nullable=True),
        sa.Column("bond_score_id", sa.Integer(), nullable=True),
        sa.Column("market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("assessment_score", sa.Integer(), nullable=False),
        sa.Column("decision_status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column(
            "required_risk_premium",
            sa.Numeric(precision=12, scale=6),
            nullable=False,
        ),
        sa.Column("yield_to_maturity", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("coupon_rate", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("duration_years", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
        sa.Column("volume", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("company_credit_status", sa.String(length=32), nullable=True),
        sa.Column("company_credit_health_score", sa.Integer(), nullable=True),
        sa.Column("company_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("bond_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("gates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "blocking_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "positive_factors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "negative_factors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("missing_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assessment_score between 0 and 100",
            name=op.f("ck_bond_risk_assessments_bond_risk_assessment_score_range"),
        ),
        sa.CheckConstraint(
            "decision_status in ('eligible_for_analysis', 'watchlist', "
            "'blocked_by_risk', 'insufficient_data')",
            name=op.f("ck_bond_risk_assessments_bond_risk_decision_status_allowed"),
        ),
        sa.CheckConstraint(
            "risk_level in ('low', 'medium', 'high', 'critical', 'unknown')",
            name=op.f("ck_bond_risk_assessments_bond_risk_level_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_risk_assessments_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bond_score_id"],
            ["bond_scores.id"],
            name=op.f("fk_bond_risk_assessments_bond_score_id_bond_scores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_credit_health_id"],
            ["company_credit_health_snapshots.id"],
            name=op.f(
                "fk_bond_risk_assessments_company_credit_health_id_company_credit_health_snapshots"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_bond_risk_assessments_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["market_snapshot_id"],
            ["bond_market_snapshots.id"],
            name=op.f(
                "fk_bond_risk_assessments_market_snapshot_id_bond_market_snapshots"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_risk_assessments")),
        sa.UniqueConstraint(
            "bond_id",
            "as_of_date",
            name=op.f(
                "uq_bond_risk_assessments_bond_risk_assessments_bond_as_of_unique"
            ),
        ),
    )
    for column in (
        "bond_id",
        "company_id",
        "as_of_date",
        "company_credit_health_id",
        "bond_score_id",
        "market_snapshot_id",
        "decision_status",
    ):
        op.create_index(
            op.f(f"ix_bond_risk_assessments_{column}"),
            "bond_risk_assessments",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("bond_risk_assessments")
    op.drop_table("company_credit_health_snapshots")
