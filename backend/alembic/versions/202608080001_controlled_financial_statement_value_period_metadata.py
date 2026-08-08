"""Add period-specific value metadata.

Revision ID: 202608080001
Revises: 202606170001
"""

from alembic import op
import sqlalchemy as sa


revision = "202608080001"
down_revision = "202606170001"
branch_labels = None
depends_on = None

TABLE_NAME = "controlled_financial_statement_values"


def upgrade() -> None:
    # Previous controlled staging contract admitted only
    # reported RUB million values at scale 1,000,000.
    # Temporary defaults backfill those legacy rows.
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "currency_2025",
            sa.String(length=16),
            nullable=False,
            server_default="RUB",
        ),
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "unit_2025",
            sa.String(length=64),
            nullable=False,
            server_default="RUB million",
        ),
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "scale_2025",
            sa.String(length=32),
            nullable=False,
            server_default="1000000",
        ),
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "currency_2024",
            sa.String(length=16),
            nullable=False,
            server_default="RUB",
        ),
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "unit_2024",
            sa.String(length=64),
            nullable=False,
            server_default="RUB million",
        ),
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "scale_2024",
            sa.String(length=32),
            nullable=False,
            server_default="1000000",
        ),
    )

    for field in (
        "currency_2025",
        "unit_2025",
        "scale_2025",
        "currency_2024",
        "unit_2024",
        "scale_2024",
    ):
        op.alter_column(
            TABLE_NAME,
            field,
            server_default=None,
        )


def downgrade() -> None:
    for field in (
        "scale_2024",
        "unit_2024",
        "currency_2024",
        "scale_2025",
        "unit_2025",
        "currency_2025",
    ):
        op.drop_column(TABLE_NAME, field)
