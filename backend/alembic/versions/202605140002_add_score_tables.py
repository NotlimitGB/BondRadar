"""add score tables

Revision ID: 202605140002
Revises: 202605140001
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140002"
down_revision: str | None = "202605140001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SIGNAL_CHECK = (
    "signal in ('interesting_for_analysis', 'neutral', "
    "'elevated_risk', 'insufficient_data')"
)


def upgrade() -> None:
    op.create_table(
        "company_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column(
            "factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
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
            "score between 0 and 100",
            name=op.f("ck_company_scores_company_score_range"),
        ),
        sa.CheckConstraint(
            SIGNAL_CHECK,
            name=op.f("ck_company_scores_company_scores_signal_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_scores_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_scores")),
        sa.UniqueConstraint(
            "company_id",
            "as_of_date",
            "source",
            name="company_scores_company_snapshot_unique",
        ),
    )
    op.create_index(
        op.f("ix_company_scores_as_of_date"),
        "company_scores",
        ["as_of_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_scores_company_id"),
        "company_scores",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "bond_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column(
            "factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
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
            "score between 0 and 100",
            name=op.f("ck_bond_scores_bond_score_range"),
        ),
        sa.CheckConstraint(
            SIGNAL_CHECK,
            name=op.f("ck_bond_scores_bond_scores_signal_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_scores_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_scores")),
        sa.UniqueConstraint(
            "bond_id",
            "as_of_date",
            "source",
            name="bond_scores_bond_snapshot_unique",
        ),
    )
    op.create_index(
        op.f("ix_bond_scores_as_of_date"),
        "bond_scores",
        ["as_of_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bond_scores_bond_id"),
        "bond_scores",
        ["bond_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bond_scores_bond_id"), table_name="bond_scores")
    op.drop_index(op.f("ix_bond_scores_as_of_date"), table_name="bond_scores")
    op.drop_table("bond_scores")
    op.drop_index(op.f("ix_company_scores_company_id"), table_name="company_scores")
    op.drop_index(op.f("ix_company_scores_as_of_date"), table_name="company_scores")
    op.drop_table("company_scores")

