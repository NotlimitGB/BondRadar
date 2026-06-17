"""controlled financial statement values

Revision ID: 202606170001
Revises: 202605140017
Create Date: 2026-06-17 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202606170001"
down_revision: str | None = "202605140017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "controlled_financial_statement_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("report_year", sa.Integer(), nullable=False),
        sa.Column("report_standard", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=128), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("metric_role", sa.String(length=32), nullable=False),
        sa.Column("metric_name_ru", sa.String(length=255), nullable=False),
        sa.Column("metric_name_en", sa.String(length=255), nullable=False),
        sa.Column("statement_page", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("value_2025", sa.Numeric(24, 2), nullable=False),
        sa.Column("value_2024", sa.Numeric(24, 2), nullable=False),
        sa.Column("raw_value_2025", sa.String(length=64), nullable=False),
        sa.Column("raw_value_2024", sa.String(length=64), nullable=False),
        sa.Column("raw_line", sa.Text(), nullable=False),
        sa.Column("note_reference", sa.String(length=64), server_default="", nullable=False),
        sa.Column("source_pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("plan_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("plan_rows_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("natural_key", sa.Text(), nullable=False),
        sa.Column("natural_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_checksum_sha256", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_controlled_financial_statement_values"),
        sa.UniqueConstraint(
            "natural_key_sha256",
            name="uq_controlled_financial_statement_values_natural_key_sha256",
        ),
    )
    op.create_index(
        "ix_controlled_financial_statement_values_company_year",
        "controlled_financial_statement_values",
        ["company_id", "report_year", "report_standard"],
        unique=False,
    )
    op.create_index(
        "ix_controlled_financial_statement_values_target_metric",
        "controlled_financial_statement_values",
        ["target_type", "metric_key", "metric_role"],
        unique=False,
    )
    op.create_index(
        "ix_controlled_financial_statement_values_plan_checksum",
        "controlled_financial_statement_values",
        ["plan_checksum_sha256"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_controlled_financial_statement_values_plan_checksum",
        table_name="controlled_financial_statement_values",
    )
    op.drop_index(
        "ix_controlled_financial_statement_values_target_metric",
        table_name="controlled_financial_statement_values",
    )
    op.drop_index(
        "ix_controlled_financial_statement_values_company_year",
        table_name="controlled_financial_statement_values",
    )
    op.drop_table("controlled_financial_statement_values")
