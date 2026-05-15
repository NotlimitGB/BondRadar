"""financial report ingestion foundation

Revision ID: 202605140010
Revises: 202605140009
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140010"
down_revision: str | None = "202605140009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_report_import_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("input_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'completed_with_errors', 'failed')",
            name="ck_fin_report_import_status",
        ),
        sa.CheckConstraint(
            "input_type in ('json', 'csv', 'manual')",
            name="ck_fin_report_import_input_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fin_report_import_runs"),
    )
    op.create_index(
        "ix_fin_report_import_runs_source",
        "financial_report_import_runs",
        ["source"],
        unique=False,
    )
    op.create_index(
        "ix_fin_report_import_runs_status",
        "financial_report_import_runs",
        ["status"],
        unique=False,
    )

    op.add_column(
        "financial_reports",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column("period_start_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column("period_end_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )
    op.create_index(
        "ix_financial_reports_published_at",
        "financial_reports",
        ["published_at"],
        unique=False,
    )

    op.create_table(
        "financial_report_source_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("financial_report_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_file_name", sa.String(length=255), nullable=True),
        sa.Column("report_type", sa.String(length=64), nullable=True),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_quarter", sa.Integer(), nullable=True),
        sa.Column("period_start_date", sa.Date(), nullable=True),
        sa.Column("period_end_date", sa.Date(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "parse_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "parse_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
            "status in ('imported', 'linked', 'failed', 'skipped')",
            name="ck_fin_report_source_status",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_fin_report_source_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["financial_report_id"],
            ["financial_reports.id"],
            name="fk_fin_report_source_report",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fin_report_source_documents"),
    )
    for index_name, column in (
        ("ix_fin_report_source_company", "company_id"),
        ("ix_fin_report_source_report", "financial_report_id"),
        ("ix_fin_report_source_source", "source"),
        ("ix_fin_report_source_period_year", "period_year"),
        ("ix_fin_report_source_period_quarter", "period_quarter"),
        ("ix_fin_report_source_published", "published_at"),
        ("ix_fin_report_source_status", "status"),
    ):
        op.create_index(
            index_name,
            "financial_report_source_documents",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for index_name in (
        "ix_fin_report_source_status",
        "ix_fin_report_source_published",
        "ix_fin_report_source_period_quarter",
        "ix_fin_report_source_period_year",
        "ix_fin_report_source_source",
        "ix_fin_report_source_report",
        "ix_fin_report_source_company",
    ):
        op.drop_index(index_name, table_name="financial_report_source_documents")
    op.drop_table("financial_report_source_documents")
    op.drop_index("ix_financial_reports_published_at", table_name="financial_reports")
    op.drop_column("financial_reports", "currency")
    op.drop_column("financial_reports", "period_end_date")
    op.drop_column("financial_reports", "period_start_date")
    op.drop_column("financial_reports", "published_at")
    op.drop_index("ix_fin_report_import_runs_status", table_name="financial_report_import_runs")
    op.drop_index("ix_fin_report_import_runs_source", table_name="financial_report_import_runs")
    op.drop_table("financial_report_import_runs")
