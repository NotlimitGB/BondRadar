"""company financial scoring

Revision ID: 202605140003
Revises: 202605140002
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140003"
down_revision: str | None = "202605140002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_reports",
        sa.Column("equity", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column("short_term_debt", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column(
            "operating_cash_flow", sa.Numeric(precision=18, scale=2), nullable=True
        ),
    )
    op.add_column(
        "financial_reports",
        sa.Column("net_profit", sa.Numeric(precision=18, scale=2), nullable=True),
    )

    op.add_column(
        "company_scores",
        sa.Column("report_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("debt_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("profitability_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("cashflow_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("stability_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("final_company_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column("risk_level", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "company_scores",
        sa.Column(
            "explanation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_company_scores_report_id"),
        "company_scores",
        ["report_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_company_scores_report_id_financial_reports"),
        "company_scores",
        "financial_reports",
        ["report_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_company_scores_report_id_financial_reports"),
        "company_scores",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_company_scores_report_id"), table_name="company_scores")
    op.drop_column("company_scores", "explanation")
    op.drop_column("company_scores", "risk_level")
    op.drop_column("company_scores", "final_company_score")
    op.drop_column("company_scores", "stability_score")
    op.drop_column("company_scores", "cashflow_score")
    op.drop_column("company_scores", "liquidity_score")
    op.drop_column("company_scores", "profitability_score")
    op.drop_column("company_scores", "debt_score")
    op.drop_column("company_scores", "report_id")

    op.drop_column("financial_reports", "net_profit")
    op.drop_column("financial_reports", "operating_cash_flow")
    op.drop_column("financial_reports", "short_term_debt")
    op.drop_column("financial_reports", "equity")

