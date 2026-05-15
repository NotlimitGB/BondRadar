"""bond total return labels

Revision ID: 202605140009
Revises: 202605140008
Create Date: 2026-05-14 00:00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202605140009"
down_revision: str | None = "202605140008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_LABEL_UNIQUE = "uq_bond_return_labels_bond_date_horizon"
NEW_LABEL_UNIQUE = "uq_bond_return_labels_bond_date_horizon_method"
OLD_LABEL_UNIQUE_COLUMNS = ["bond_id", "as_of_date", "horizon_days"]
NEW_LABEL_UNIQUE_COLUMNS = ["bond_id", "as_of_date", "horizon_days", "return_method"]


def _drop_unique_for_columns(table_name: str, column_names: list[str]) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        inspector = sa.inspect(bind)
        for constraint in inspector.get_unique_constraints(table_name):
            if constraint.get("column_names") == column_names:
                op.drop_constraint(
                    constraint["name"],
                    table_name,
                    type_="unique",
                )
                return
        return

    constraint_name = bind.execute(
        sa.text(
            """
            select con.conname
            from pg_constraint con
            join pg_class rel on rel.oid = con.conrelid
            join pg_namespace nsp on nsp.oid = rel.relnamespace
            where con.contype = 'u'
              and rel.relname = :table_name
              and nsp.nspname = current_schema()
              and (
                select string_agg(att.attname, ',' order by cols.ordinality)
                from unnest(con.conkey) with ordinality as cols(attnum, ordinality)
                join pg_attribute att
                  on att.attrelid = rel.oid
                 and att.attnum = cols.attnum
              ) = :column_names
            limit 1
            """
        ),
        {
            "table_name": table_name,
            "column_names": ",".join(column_names),
        },
    ).scalar_one_or_none()
    if constraint_name is not None:
        op.execute(
            sa.text(
                f'alter table "{table_name}" '
                f'drop constraint "{_quote_identifier(constraint_name)}"'
            )
        )


def _quote_identifier(identifier: str) -> str:
    return identifier.replace('"', '""')


def upgrade() -> None:
    op.create_table(
        "bond_cashflow_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("amount_percent", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "source",
            sa.String(length=64),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type in ('coupon', 'amortization', 'redemption', "
            "'offer_redemption', 'other')",
            name="ck_bond_cashflow_events_type",
        ),
        sa.CheckConstraint(
            "amount is null or amount >= 0",
            name="ck_bond_cashflow_events_amount_nn",
        ),
        sa.CheckConstraint(
            "amount_percent is null or amount_percent >= 0",
            name="ck_bond_cashflow_events_amount_pct_nn",
        ),
        sa.ForeignKeyConstraint(
            ["bond_id"],
            ["bonds.id"],
            name=op.f("fk_bond_cashflow_events_bond_id_bonds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bond_cashflow_events")),
        sa.UniqueConstraint(
            "bond_id",
            "event_date",
            "event_type",
            "source",
            name="uq_bond_cashflow_events_bond_date_type_source",
        ),
    )
    for column in ("bond_id", "event_date", "event_type"):
        op.create_index(
            op.f(f"ix_bond_cashflow_events_{column}"),
            "bond_cashflow_events",
            [column],
            unique=False,
        )

    op.add_column(
        "bond_return_labels",
        sa.Column(
            "return_method",
            sa.String(length=32),
            server_default="price",
            nullable=False,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column("price_return", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column("coupon_return", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "amortization_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "redemption_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "gross_total_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "estimated_costs_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "net_total_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "risk_adjusted_excess_return",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "required_risk_premium",
            sa.Numeric(precision=12, scale=6),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "return_calculation_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "bond_return_labels",
        sa.Column(
            "return_calculation_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE bond_return_labels "
        "SET return_method = 'price' "
        "WHERE return_method IS NULL"
    )
    op.execute(
        "UPDATE bond_return_labels "
        "SET price_return = future_return "
        "WHERE price_return IS NULL"
    )
    _drop_unique_for_columns("bond_return_labels", OLD_LABEL_UNIQUE_COLUMNS)
    op.create_unique_constraint(
        NEW_LABEL_UNIQUE,
        "bond_return_labels",
        NEW_LABEL_UNIQUE_COLUMNS,
    )
    op.create_check_constraint(
        "ck_bond_return_labels_return_method",
        "bond_return_labels",
        "return_method in ('price', 'total_return', 'risk_adjusted')",
    )
    op.create_index(
        op.f("ix_bond_return_labels_return_method"),
        "bond_return_labels",
        ["return_method"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bond_return_labels_return_method"), table_name="bond_return_labels")
    op.drop_constraint(
        "ck_bond_return_labels_return_method",
        "bond_return_labels",
        type_="check",
    )
    _drop_unique_for_columns("bond_return_labels", NEW_LABEL_UNIQUE_COLUMNS)
    op.execute("DELETE FROM bond_return_labels WHERE return_method <> 'price'")
    op.create_unique_constraint(
        OLD_LABEL_UNIQUE,
        "bond_return_labels",
        OLD_LABEL_UNIQUE_COLUMNS,
    )
    for column in (
        "return_calculation_details",
        "return_calculation_warnings",
        "required_risk_premium",
        "risk_adjusted_excess_return",
        "net_total_return",
        "estimated_costs_return",
        "gross_total_return",
        "redemption_return",
        "amortization_return",
        "coupon_return",
        "price_return",
        "return_method",
    ):
        op.drop_column("bond_return_labels", column)
    op.drop_table("bond_cashflow_events")
