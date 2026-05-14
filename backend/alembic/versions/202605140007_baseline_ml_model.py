"""baseline ml model

Revision ID: 202605140007
Revises: 202605140006
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140007"
down_revision: str | None = "202605140006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ml_model_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_type", sa.String(length=64), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("as_of_date_from", sa.Date(), nullable=True),
        sa.Column("as_of_date_to", sa.Date(), nullable=True),
        sa.Column("train_rows", sa.Integer(), nullable=False),
        sa.Column("test_rows", sa.Integer(), nullable=False),
        sa.Column("positive_rows", sa.Integer(), nullable=False),
        sa.Column("negative_rows", sa.Integer(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "feature_importance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'failed')",
            name=op.f("ck_ml_model_runs_ml_model_runs_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ml_model_runs")),
    )
    op.create_index(
        op.f("ix_ml_model_runs_horizon_days"),
        "ml_model_runs",
        ["horizon_days"],
        unique=False,
    )

    op.create_table(
        "ml_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_run_id", sa.Integer(), nullable=False),
        sa.Column("feature_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column(
            "probability_positive",
            sa.Numeric(precision=12, scale=10),
            nullable=False,
        ),
        sa.Column("predicted_label", sa.String(length=64), nullable=False),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "predicted_label in ('predicted_positive_return', "
            "'predicted_negative_return')",
            name=op.f("ck_ml_predictions_ml_predictions_predicted_label_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_ml_predictions_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_ml_predictions_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["feature_snapshot_id"],
            ["bond_feature_snapshots.id"],
            name=op.f(
                "fk_ml_predictions_feature_snapshot_id_bond_feature_snapshots"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml_model_runs.id"],
            name=op.f("fk_ml_predictions_model_run_id_ml_model_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ml_predictions")),
        sa.UniqueConstraint(
            "model_run_id",
            "feature_snapshot_id",
            name=op.f("uq_ml_predictions_ml_predictions_run_feature_unique"),
        ),
    )
    for column in (
        "model_run_id",
        "feature_snapshot_id",
        "bond_id",
        "company_id",
        "as_of_date",
        "horizon_days",
    ):
        op.create_index(
            op.f(f"ix_ml_predictions_{column}"),
            "ml_predictions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("ml_predictions")
    op.drop_table("ml_model_runs")
