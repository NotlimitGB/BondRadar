"""bond scoring

Revision ID: 202605140004
Revises: 202605140003
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140004"
down_revision: str | None = "202605140003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_SIGNAL_CHECK = (
    "signal in ('interesting_for_analysis', 'neutral', "
    "'elevated_risk', 'insufficient_data')"
)
NEW_SIGNAL_CHECK = (
    "signal in ('interesting_for_analysis', 'neutral', 'elevated_risk', "
    "'increased_risk', 'high_risk', 'insufficient_data')"
)
SIGNAL_CONSTRAINTS = [
    (
        "companies",
        "ck_companies_companies_signal_allowed",
        "companies_signal_allowed",
    ),
    (
        "bonds",
        "ck_bonds_bonds_signal_allowed",
        "bonds_signal_allowed",
    ),
    (
        "financial_reports",
        "ck_financial_reports_financial_reports_signal_allowed",
        "financial_reports_signal_allowed",
    ),
    (
        "company_scores",
        "ck_company_scores_company_scores_signal_allowed",
        "company_scores_signal_allowed",
    ),
    (
        "bond_scores",
        "ck_bond_scores_bond_scores_signal_allowed",
        "bond_scores_signal_allowed",
    ),
]


def _replace_signal_constraints(check_sql: str) -> None:
    for table_name, canonical_name, legacy_name in SIGNAL_CONSTRAINTS:
        for constraint_name in (canonical_name, legacy_name):
            op.execute(
                sa.text(
                    f"ALTER TABLE {table_name} "
                    f"DROP CONSTRAINT IF EXISTS {constraint_name}"
                )
            )
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} ADD CONSTRAINT {canonical_name} "
                f"CHECK ({check_sql})"
            )
        )


def _downgrade_signal_values() -> None:
    for table_name, _, _ in SIGNAL_CONSTRAINTS:
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET signal = 'elevated_risk' "
                "WHERE signal IN ('increased_risk', 'high_risk')"
            )
        )


def upgrade() -> None:
    _replace_signal_constraints(NEW_SIGNAL_CHECK)

    op.add_column(
        "bonds",
        sa.Column("volume", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.add_column(
        "bonds",
        sa.Column("amortization", sa.Boolean(), nullable=True),
    )

    op.add_column(
        "bond_scores",
        sa.Column("company_score_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("yield_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("duration_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("liquidity_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("spread_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("risk_penalty", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column("final_bond_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bond_scores",
        sa.Column(
            "explanation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_bond_scores_company_score_id"),
        "bond_scores",
        ["company_score_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_bond_scores_company_score_id_company_scores"),
        "bond_scores",
        "company_scores",
        ["company_score_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    _downgrade_signal_values()
    _replace_signal_constraints(OLD_SIGNAL_CHECK)

    op.drop_constraint(
        op.f("fk_bond_scores_company_score_id_company_scores"),
        "bond_scores",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_bond_scores_company_score_id"), table_name="bond_scores")
    op.drop_column("bond_scores", "explanation")
    op.drop_column("bond_scores", "final_bond_score")
    op.drop_column("bond_scores", "risk_penalty")
    op.drop_column("bond_scores", "spread_score")
    op.drop_column("bond_scores", "liquidity_score")
    op.drop_column("bond_scores", "duration_score")
    op.drop_column("bond_scores", "yield_score")
    op.drop_column("bond_scores", "company_score_id")

    op.drop_column("bonds", "amortization")
    op.drop_column("bonds", "volume")
