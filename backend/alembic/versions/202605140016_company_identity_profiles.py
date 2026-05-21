"""company identity profiles

Revision ID: 202605140016
Revises: 202605140015
Create Date: 2026-05-21 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140016"
down_revision: str | None = "202605140015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_identity_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("short_name", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("inn", sa.String(length=16), nullable=True),
        sa.Column("ogrn", sa.String(length=32), nullable=True),
        sa.Column("kpp", sa.String(length=16), nullable=True),
        sa.Column("okpo", sa.String(length=16), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("issuer_group_name", sa.String(length=255), nullable=True),
        sa.Column("issuer_group_inn", sa.String(length=16), nullable=True),
        sa.Column("issuer_role", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column(
            "identity_status",
            sa.String(length=32),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("identity_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "identity_source",
            sa.String(length=64),
            server_default="existing_company",
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
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
            "issuer_role in ('legal_issuer', 'spv', 'finance_subsidiary', "
            "'operating_company', 'parent_group', 'unknown')",
            name="ck_company_identity_issuer_role",
        ),
        sa.CheckConstraint(
            "identity_status in ('unknown', 'weak', 'matched', 'verified', 'conflict')",
            name="ck_company_identity_status",
        ),
        sa.CheckConstraint(
            "identity_source in ('moex_iss', 'operator_csv', 'operator_json', "
            "'manual_review', 'existing_company', 'mixed')",
            name="ck_company_identity_source",
        ),
        sa.CheckConstraint(
            "review_status in ('pending', 'reviewed', 'accepted', 'rejected')",
            name="ck_company_identity_review_status",
        ),
        sa.CheckConstraint(
            "identity_confidence is null or "
            "(identity_confidence >= 0 and identity_confidence <= 1)",
            name="ck_company_identity_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_company_identity_profiles_company_id_companies",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_identity_profiles"),
        sa.UniqueConstraint("company_id", name="uq_company_identity_profiles_company_id"),
    )
    op.create_index(
        "ix_company_identity_profiles_company_id",
        "company_identity_profiles",
        ["company_id"],
    )
    op.create_index(
        "ix_company_identity_profiles_inn",
        "company_identity_profiles",
        ["inn"],
    )
    op.create_index(
        "ix_company_identity_profiles_ogrn",
        "company_identity_profiles",
        ["ogrn"],
    )
    op.create_index(
        "ix_company_identity_profiles_identity_status",
        "company_identity_profiles",
        ["identity_status"],
    )
    op.create_index(
        "ix_company_identity_profiles_identity_source",
        "company_identity_profiles",
        ["identity_source"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_identity_profiles_identity_source",
        table_name="company_identity_profiles",
    )
    op.drop_index(
        "ix_company_identity_profiles_identity_status",
        table_name="company_identity_profiles",
    )
    op.drop_index("ix_company_identity_profiles_ogrn", table_name="company_identity_profiles")
    op.drop_index("ix_company_identity_profiles_inn", table_name="company_identity_profiles")
    op.drop_index(
        "ix_company_identity_profiles_company_id",
        table_name="company_identity_profiles",
    )
    op.drop_table("company_identity_profiles")
