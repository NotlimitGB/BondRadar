"""external risk regime

Revision ID: 202605140015
Revises: 202605140014
Create Date: 2026-05-19 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202605140015"
down_revision: str | None = "202605140014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_risk_regimes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=64), server_default="manual", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "mode in ('normal', 'elevated', 'severe')",
            name="ck_external_risk_regime_mode",
        ),
        sa.CheckConstraint(
            "length(source) > 0",
            name="ck_external_risk_regime_source",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_risk_regimes"),
    )
    op.create_index(
        "ix_external_risk_regimes_mode",
        "external_risk_regimes",
        ["mode"],
    )
    op.create_index(
        "ix_external_risk_regimes_is_active",
        "external_risk_regimes",
        ["is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_risk_regimes_is_active", table_name="external_risk_regimes")
    op.drop_index("ix_external_risk_regimes_mode", table_name="external_risk_regimes")
    op.drop_table("external_risk_regimes")
