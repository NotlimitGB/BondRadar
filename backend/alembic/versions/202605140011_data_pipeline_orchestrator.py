"""data pipeline orchestrator

Revision ID: 202605140011
Revises: 202605140010
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140011"
down_revision: str | None = "202605140010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_pipeline_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("bond_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("company_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "return_methods_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            name="ck_data_pipeline_run_status",
        ),
        sa.CheckConstraint(
            "mode in ('manual', 'scheduled', 'demo', 'test')",
            name="ck_data_pipeline_mode",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_pipeline_runs"),
    )
    op.create_index(
        "ix_data_pipeline_runs_status",
        "data_pipeline_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_data_pipeline_runs_mode",
        "data_pipeline_runs",
        ["mode"],
        unique=False,
    )

    op.create_table(
        "data_pipeline_step_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'completed', 'skipped', 'failed')",
            name="ck_data_pipeline_step_status",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["data_pipeline_runs.id"],
            name="fk_data_pipeline_step_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_pipeline_step_runs"),
    )
    op.create_index(
        "ix_data_pipeline_step_runs_pipeline_run_id",
        "data_pipeline_step_runs",
        ["pipeline_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_pipeline_step_runs_step_name",
        "data_pipeline_step_runs",
        ["step_name"],
        unique=False,
    )
    op.create_index(
        "ix_data_pipeline_step_runs_status",
        "data_pipeline_step_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_data_pipeline_step_runs_status", table_name="data_pipeline_step_runs")
    op.drop_index("ix_data_pipeline_step_runs_step_name", table_name="data_pipeline_step_runs")
    op.drop_index(
        "ix_data_pipeline_step_runs_pipeline_run_id",
        table_name="data_pipeline_step_runs",
    )
    op.drop_table("data_pipeline_step_runs")
    op.drop_index("ix_data_pipeline_runs_mode", table_name="data_pipeline_runs")
    op.drop_index("ix_data_pipeline_runs_status", table_name="data_pipeline_runs")
    op.drop_table("data_pipeline_runs")
