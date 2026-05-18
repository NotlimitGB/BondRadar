"""paper live scheduler

Revision ID: 202605140014
Revises: 202605140013
Create Date: 2026-05-18 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140014"
down_revision: str | None = "202605140013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_live_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="manual_cycle", nullable=False),
        sa.Column("cycle_request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cycle_run_id", sa.Integer(), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=True),
        sa.Column("run_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("use_current_date_as_of_date", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_token", sa.String(length=64), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            "status in ('active', 'paused', 'archived')",
            name="ck_paper_live_schedule_status",
        ),
        sa.CheckConstraint(
            "mode in ('manual_cycle')",
            name="ck_paper_live_schedule_mode",
        ),
        sa.CheckConstraint(
            "interval_days >= 1",
            name="ck_paper_live_schedule_interval",
        ),
        sa.CheckConstraint(
            "run_count >= 0",
            name="ck_paper_live_schedule_run_count",
        ),
        sa.CheckConstraint(
            "max_runs is null or max_runs >= 1",
            name="ck_paper_live_schedule_max_runs",
        ),
        sa.ForeignKeyConstraint(
            ["last_cycle_run_id"],
            ["paper_live_cycle_runs.id"],
            name="fk_paper_live_schedule_last_cycle",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_live_schedules"),
    )
    op.create_index("ix_paper_live_schedules_status", "paper_live_schedules", ["status"])
    op.create_index("ix_paper_live_schedules_next_run_at", "paper_live_schedules", ["next_run_at"])
    op.create_index("ix_paper_live_schedules_last_cycle_run_id", "paper_live_schedules", ["last_cycle_run_id"])
    op.create_index("ix_paper_live_schedules_lock_expires_at", "paper_live_schedules", ["lock_expires_at"])

    op.add_column(
        "paper_live_cycle_runs",
        sa.Column("schedule_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "paper_live_cycle_runs",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_paper_live_cycle_schedule",
        "paper_live_cycle_runs",
        "paper_live_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_paper_live_cycles_schedule_id", "paper_live_cycle_runs", ["schedule_id"])
    op.create_index("ix_paper_live_cycles_scheduled_for", "paper_live_cycle_runs", ["scheduled_for"])


def downgrade() -> None:
    op.drop_index("ix_paper_live_cycles_scheduled_for", table_name="paper_live_cycle_runs")
    op.drop_index("ix_paper_live_cycles_schedule_id", table_name="paper_live_cycle_runs")
    op.drop_constraint("fk_paper_live_cycle_schedule", "paper_live_cycle_runs", type_="foreignkey")
    op.drop_column("paper_live_cycle_runs", "scheduled_for")
    op.drop_column("paper_live_cycle_runs", "schedule_id")

    op.drop_index("ix_paper_live_schedules_lock_expires_at", table_name="paper_live_schedules")
    op.drop_index("ix_paper_live_schedules_last_cycle_run_id", table_name="paper_live_schedules")
    op.drop_index("ix_paper_live_schedules_next_run_at", table_name="paper_live_schedules")
    op.drop_index("ix_paper_live_schedules_status", table_name="paper_live_schedules")
    op.drop_table("paper_live_schedules")
