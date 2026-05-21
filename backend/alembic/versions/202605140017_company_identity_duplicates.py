"""company identity duplicate candidates

Revision ID: 202605140017
Revises: 202605140016
Create Date: 2026-05-21 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140017"
down_revision: str | None = "202605140016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_identity_duplicate_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_company_id", sa.Integer(), nullable=False),
        sa.Column("candidate_company_id", sa.Integer(), nullable=False),
        sa.Column("group_key", sa.String(length=255), nullable=False),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("match_score", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "match_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), server_default="diagnostics", nullable=False),
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
            "canonical_company_id <> candidate_company_id",
            name="ck_company_identity_duplicate_distinct",
        ),
        sa.CheckConstraint(
            "match_type in ('exact_inn', 'exact_ogrn', 'exact_legal_name', "
            "'normalized_name', 'bond_name_phrase', 'same_group_name', "
            "'manual_review', 'mixed')",
            name="ck_company_identity_duplicate_match_type",
        ),
        sa.CheckConstraint(
            "status in ('candidate', 'accepted', 'rejected', 'needs_review', 'conflict')",
            name="ck_company_identity_duplicate_status",
        ),
        sa.CheckConstraint(
            "review_status in ('pending', 'reviewed', 'accepted', 'rejected')",
            name="ck_company_identity_duplicate_review_status",
        ),
        sa.CheckConstraint(
            "match_score >= 0 and match_score <= 1",
            name="ck_company_identity_duplicate_score",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_company_id"],
            ["companies.id"],
            name="fk_company_identity_duplicate_canonical_companies",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_company_id"],
            ["companies.id"],
            name="fk_company_identity_duplicate_candidate_companies",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_identity_duplicate_candidates"),
        sa.UniqueConstraint(
            "canonical_company_id",
            "candidate_company_id",
            name="uq_company_identity_duplicate_pair",
        ),
    )
    op.create_index(
        "ix_company_identity_duplicate_canonical_company_id",
        "company_identity_duplicate_candidates",
        ["canonical_company_id"],
    )
    op.create_index(
        "ix_company_identity_duplicate_candidate_company_id",
        "company_identity_duplicate_candidates",
        ["candidate_company_id"],
    )
    op.create_index(
        "ix_company_identity_duplicate_status",
        "company_identity_duplicate_candidates",
        ["status"],
    )
    op.create_index(
        "ix_company_identity_duplicate_match_type",
        "company_identity_duplicate_candidates",
        ["match_type"],
    )
    op.create_index(
        "ix_company_identity_duplicate_group_key",
        "company_identity_duplicate_candidates",
        ["group_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_identity_duplicate_group_key",
        table_name="company_identity_duplicate_candidates",
    )
    op.drop_index(
        "ix_company_identity_duplicate_match_type",
        table_name="company_identity_duplicate_candidates",
    )
    op.drop_index(
        "ix_company_identity_duplicate_status",
        table_name="company_identity_duplicate_candidates",
    )
    op.drop_index(
        "ix_company_identity_duplicate_candidate_company_id",
        table_name="company_identity_duplicate_candidates",
    )
    op.drop_index(
        "ix_company_identity_duplicate_canonical_company_id",
        table_name="company_identity_duplicate_candidates",
    )
    op.drop_table("company_identity_duplicate_candidates")
