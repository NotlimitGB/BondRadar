"""Dedicated immutable Shadow accounting; refuse destructive downgrade."""
from alembic import op
import sqlalchemy as sa

revision = "202610030002"
down_revision = "202610030001"
branch_labels = None
depends_on = None


def decimal_type():
    # Frozen migration definition: no dependency on mutable application models.
    return sa.Numeric().with_variant(sa.String(), "sqlite")


def fk(name, target, nullable=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def amount(name, nullable=False):
    return sa.Column(name, decimal_type(), nullable=nullable)


def string(name, length=64, nullable=False):
    return sa.Column(name, sa.String(length), nullable=nullable)


def upgrade():
    horizon = "planned_end_date = date(genesis_date, '+90 days')" if op.get_bind().dialect.name == "sqlite" else "planned_end_date = genesis_date + 90"
    op.create_table("shadow_test_runs",
        sa.Column("id",sa.Integer(),primary_key=True), string("run_key_sha256"), string("status",32),
        sa.Column("genesis_date",sa.Date(),nullable=False), sa.Column("planned_end_date",sa.Date(),nullable=False),
        sa.Column("horizon_days",sa.Integer(),nullable=False), amount("initial_capital_rub"),
        string("base_currency",3), string("market_source",8), string("source_code_sha",40),
        string("source_universe_sha256"), string("genesis_shadow_plan_sha256"),
        string("shadow_execution_contract_version"), string("strategy_contract_version"), string("strategy_policy_version"),
        string("risk_policy_version"), string("execution_policy_version"),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column("activated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column("observation_completed_at",sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_key_sha256"),
        sa.CheckConstraint("status IN ('ACTIVE','OBSERVATION_COMPLETE','ABORTED')", name="shadow_303_check_1"),
        sa.CheckConstraint("horizon_days = 90", name="shadow_303_check_2"),sa.CheckConstraint(horizon, name="shadow_303_check_3"),
        sa.CheckConstraint("CAST(initial_capital_rub AS NUMERIC) > 0", name="shadow_303_check_4"),
        sa.CheckConstraint("base_currency = 'RUB' AND market_source = 'moex'", name="shadow_303_check_5"))
    op.create_table("shadow_ledger_entries",
        sa.Column("id",sa.Integer(),primary_key=True),fk("shadow_run_id","shadow_test_runs.id"),
        string("event_key_sha256"),sa.Column("event_date",sa.Date(),nullable=False),
        sa.Column("sequence_number",sa.Integer(),nullable=False),string("event_type",32),fk("bond_id","bonds.id",True),
        sa.Column("quantity_delta",sa.Integer(),nullable=False),amount("cash_delta_rub"),amount("unit_amount_rub",True),
        fk("source_market_snapshot_id","bond_market_snapshots.id",True),fk("source_cashflow_event_id","bond_cashflow_events.id",True),
        string("source_contract_version"),string("source_fingerprint_sha256"),sa.Column("details_json",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint("event_key_sha256"),sa.UniqueConstraint("shadow_run_id","sequence_number"),
        sa.CheckConstraint("sequence_number > 0", name="shadow_303_check_6"),
        sa.CheckConstraint("event_type IN ('INITIAL_CAPITAL','GENESIS_PURCHASE','COUPON','AMORTIZATION','REDEMPTION')", name="shadow_303_check_7"),
        sa.CheckConstraint("(event_type = 'INITIAL_CAPITAL' AND bond_id IS NULL AND quantity_delta = 0) OR (event_type != 'INITIAL_CAPITAL' AND bond_id IS NOT NULL)", name="shadow_303_check_8"),
        sa.CheckConstraint("(event_type = 'INITIAL_CAPITAL' AND CAST(cash_delta_rub AS NUMERIC) > 0) OR (event_type = 'GENESIS_PURCHASE' AND quantity_delta > 0 AND CAST(cash_delta_rub AS NUMERIC) < 0) OR (event_type IN ('COUPON','AMORTIZATION') AND quantity_delta = 0 AND CAST(cash_delta_rub AS NUMERIC) >= 0) OR (event_type = 'REDEMPTION' AND quantity_delta < 0 AND CAST(cash_delta_rub AS NUMERIC) >= 0)", name="shadow_event_signs"))
    op.create_table("shadow_daily_snapshots",
        sa.Column("id",sa.Integer(),primary_key=True),fk("shadow_run_id","shadow_test_runs.id"),
        sa.Column("as_of_date",sa.Date(),nullable=False),fk("previous_snapshot_id","shadow_daily_snapshots.id",True),
        amount("cash_rub"),amount("market_value_rub"),amount("nav_rub"),amount("daily_return"),amount("cumulative_return"),
        *[sa.Column(n,sa.Integer(),nullable=False) for n in ("active_position_count","redeemed_position_count","ledger_entry_count_to_date","applied_cashflow_count_for_day")],
        string("input_state_sha256"),string("snapshot_sha256"),sa.Column("details_json",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint("shadow_run_id","as_of_date"),
        sa.CheckConstraint("active_position_count >= 0 AND redeemed_position_count >= 0 AND ledger_entry_count_to_date >= 0 AND applied_cashflow_count_for_day >= 0", name="shadow_303_check_9"),
        sa.CheckConstraint("CAST(cash_rub AS NUMERIC) >= 0 AND CAST(market_value_rub AS NUMERIC) >= 0 AND CAST(nav_rub AS NUMERIC) >= 0", name="shadow_snapshot_nonnegative"))
    op.create_table("shadow_daily_position_snapshots",
        sa.Column("id",sa.Integer(),primary_key=True),fk("shadow_daily_snapshot_id","shadow_daily_snapshots.id"),
        fk("shadow_run_id","shadow_test_runs.id"),fk("bond_id","bonds.id"),string("position_status",16),
        sa.Column("quantity",sa.Integer(),nullable=False),fk("market_snapshot_id","bond_market_snapshots.id",True),
        sa.Column("market_trade_date",sa.Date()),sa.Column("market_age_days",sa.Integer()),string("price_basis",32,True),
        amount("dirty_value_rub_per_bond",True),amount("market_value_rub"),amount("cashflow_rub_on_date"),
        string("source_dv01_contract_version",64,True),fk("source_security_master_profile_id","bond_security_master_profiles.id",True),
        string("position_state_sha256"),sa.UniqueConstraint("shadow_daily_snapshot_id","bond_id"),
        sa.CheckConstraint("(position_status = 'ACTIVE' AND quantity > 0 AND market_snapshot_id IS NOT NULL AND market_trade_date IS NOT NULL AND market_age_days >= 0 AND CAST(dirty_value_rub_per_bond AS NUMERIC) > 0) OR (position_status = 'REDEEMED' AND quantity = 0 AND CAST(market_value_rub AS NUMERIC) = 0)", name="shadow_303_check_10"))
    for table, columns in (
        ("shadow_ledger_entries",("shadow_run_id","event_date")),
        ("shadow_daily_snapshots",("shadow_run_id",)),
        ("shadow_daily_position_snapshots",("shadow_run_id","shadow_daily_snapshot_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}",table,[column])


def downgrade():
    tables = ("shadow_daily_position_snapshots","shadow_daily_snapshots","shadow_ledger_entries","shadow_test_runs")
    for table in tables:
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("SHADOW_HISTORY_DOWNGRADE_FORBIDDEN")
    for table in tables:
        op.drop_table(table)
