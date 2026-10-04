"""Disposable migration and fail-closed downgrade; no configured DB access."""
from pathlib import Path
import importlib.util
import pytest
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.base import Base

TABLES={"shadow_test_runs","shadow_ledger_entries","shadow_daily_snapshots","shadow_daily_position_snapshots"}


def migration():
    path=Path(__file__).resolve().parents[1]/"alembic"/"versions"/"202610030002_shadow_ledger_and_daily_marking.py"
    spec=importlib.util.spec_from_file_location("shadow_migration",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_upgrade_constraints_empty_downgrade_and_legacy_unchanged():
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine,tables=[t for n,t in Base.metadata.tables.items() if n not in TABLES])
    with engine.begin() as conn:
        before=inspect(conn).get_table_names()
        legacy=conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table' AND name LIKE 'paper_%' ORDER BY name")).all()
        module=migration()
        assert (module.revision,module.down_revision)==("202610030002","202610030001")
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            assert set(inspect(conn).get_table_names())-set(before)==TABLES
            for n in TABLES:
                assert inspect(conn).get_pk_constraint(n)["constrained_columns"]==["id"]
                assert inspect(conn).get_unique_constraints(n)
                assert inspect(conn).get_check_constraints(n)
            assert conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table' AND name LIKE 'paper_%' ORDER BY name")).all()==legacy
            module.downgrade()
            assert inspect(conn).get_table_names()==before
    engine.dispose()


def test_downgrade_refuses_any_existing_run_before_ddl():
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine,tables=[t for n,t in Base.metadata.tables.items() if n not in TABLES])
    with engine.begin() as conn:
        module=migration()
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            conn.execute(text("""INSERT INTO shadow_test_runs
                (run_key_sha256,status,genesis_date,planned_end_date,horizon_days,initial_capital_rub,base_currency,
                market_source,source_code_sha,source_universe_sha256,genesis_shadow_plan_sha256,
                shadow_execution_contract_version,strategy_contract_version,strategy_policy_version,risk_policy_version,execution_policy_version)
                VALUES (:key,'ACTIVE','2026-10-01','2026-12-30',90,'100','RUB','moex',:code,:key,:key,'v','v','v','v','v')"""),
                {"key":"a"*64,"code":"a"*40})
            with pytest.raises(RuntimeError,match="SHADOW_HISTORY_DOWNGRADE_FORBIDDEN"): module.downgrade()
            assert TABLES<=set(inspect(conn).get_table_names())
    engine.dispose()
