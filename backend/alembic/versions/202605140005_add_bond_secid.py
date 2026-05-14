"""add bond secid

Revision ID: 202605140005
Revises: 202605140004
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "202605140005"
down_revision: str | None = "202605140004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bonds", sa.Column("secid", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_bonds_secid"), "bonds", ["secid"], unique=True)
    op.alter_column(
        "bonds",
        "isin",
        existing_type=sa.String(length=12),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE bonds "
            "SET isin = 'SYN' || lpad(id::text, 9, '0') "
            "WHERE isin IS NULL OR isin = ''"
        )
    )
    op.alter_column(
        "bonds",
        "isin",
        existing_type=sa.String(length=12),
        nullable=False,
    )
    op.drop_index(op.f("ix_bonds_secid"), table_name="bonds")
    op.drop_column("bonds", "secid")
