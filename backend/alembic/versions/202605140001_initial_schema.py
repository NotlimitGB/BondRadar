"""initial schema

Revision ID: 202605140001
Revises:
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202605140001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SIGNAL_CHECK = (
    "signal in ('interesting_for_analysis', 'neutral', "
    "'elevated_risk', 'insufficient_data')"
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("inn", sa.String(length=16), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=False),
        sa.Column("credit_rating", sa.String(length=32), nullable=True),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(SIGNAL_CHECK, name=op.f("ck_companies_companies_signal_allowed")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_index(op.f("ix_companies_inn"), "companies", ["inn"], unique=True)
    op.create_index(op.f("ix_companies_name"), "companies", ["name"], unique=False)
    op.create_index(op.f("ix_companies_ticker"), "companies", ["ticker"], unique=True)

    op.create_table(
        "bonds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("nominal_value", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("current_price", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("coupon_rate", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("yield_to_maturity", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("duration_years", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("offer_date", sa.Date(), nullable=True),
        sa.Column("is_floating_coupon", sa.Boolean(), nullable=False),
        sa.Column("is_subordinated", sa.Boolean(), nullable=False),
        sa.Column("is_perpetual", sa.Boolean(), nullable=False),
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("risk_notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(SIGNAL_CHECK, name=op.f("ck_bonds_bonds_signal_allowed")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_bonds_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bonds")),
    )
    op.create_index(op.f("ix_bonds_company_id"), "bonds", ["company_id"], unique=False)
    op.create_index(op.f("ix_bonds_isin"), "bonds", ["isin"], unique=True)
    op.create_index(op.f("ix_bonds_name"), "bonds", ["name"], unique=False)

    op.create_table(
        "financial_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_quarter", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("ebitda", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("net_debt", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("total_debt", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("cash", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("interest_expense", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("debt_to_ebitda", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("interest_coverage", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("signal", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint(
            "period_quarter between 0 and 4",
            name=op.f("ck_financial_reports_period_quarter_range"),
        ),
        sa.CheckConstraint(
            SIGNAL_CHECK,
            name=op.f("ck_financial_reports_financial_reports_signal_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_financial_reports_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_reports")),
        sa.UniqueConstraint(
            "company_id",
            "period_year",
            "period_quarter",
            name="financial_reports_company_period_unique",
        ),
    )
    op.create_index(
        op.f("ix_financial_reports_company_id"),
        "financial_reports",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_reports_period_year"),
        "financial_reports",
        ["period_year"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_reports_period_year"), table_name="financial_reports")
    op.drop_index(op.f("ix_financial_reports_company_id"), table_name="financial_reports")
    op.drop_table("financial_reports")
    op.drop_index(op.f("ix_bonds_name"), table_name="bonds")
    op.drop_index(op.f("ix_bonds_isin"), table_name="bonds")
    op.drop_index(op.f("ix_bonds_company_id"), table_name="bonds")
    op.drop_table("bonds")
    op.drop_index(op.f("ix_companies_ticker"), table_name="companies")
    op.drop_index(op.f("ix_companies_name"), table_name="companies")
    op.drop_index(op.f("ix_companies_inn"), table_name="companies")
    op.drop_table("companies")
