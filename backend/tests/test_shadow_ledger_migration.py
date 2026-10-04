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
                market_source,source_code_sha,source_universe_sha256,shadow_execution_sha256,genesis_plan_sha256,
                shadow_execution_contract_version,strategy_contract_version,strategy_policy_version,risk_policy_version,execution_policy_version)
                VALUES (:key,'ACTIVE','2026-10-01','2026-12-30',90,'100','RUB','moex', :code,:key,:key,:key,'v','v','v','v','v')"""),
                {"key":"a"*64,"code":"a"*40})
            with pytest.raises(RuntimeError,match="SHADOW_HISTORY_DOWNGRADE_FORBIDDEN"): module.downgrade()
            assert TABLES<=set(inspect(conn).get_table_names())
    engine.dispose()


def test_current_metadata_stamp_upgrade_cycle(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.core.config import settings
    root = Path(__file__).resolve().parents[1]
    url = "sqlite:///" + (tmp_path / "precreated.sqlite").as_posix()
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    assert TABLES <= set(inspect(engine).get_table_names())
    command.stamp(config, "202610030001")
    command.upgrade(config, "head")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration()._validate_precreated_sqlite_schema(inspect(conn))
    command.downgrade(config, "202610030001")
    assert not TABLES.intersection(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration()._validate_precreated_sqlite_schema(inspect(conn))
    columns = {column["name"] for column in inspect(engine).get_columns("shadow_test_runs")}
    assert {"shadow_execution_sha256", "genesis_plan_sha256"} <= columns
    assert "genesis_shadow_plan_sha256" not in columns
    engine.dispose()


@pytest.mark.parametrize("count", [1, 2, 3])
def test_partial_schema_is_rejected_without_creating_missing_tables(count):
    engine = create_engine("sqlite://")
    module = migration()
    tables = [Base.metadata.tables[name] for name in module.TASK303_TABLES[:count]]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        before = set(inspect(conn).get_table_names())
        with pytest.raises(RuntimeError, match="PARTIAL_TASK303_SQLITE_SCHEMA"):
            module.upgrade()
        assert set(inspect(conn).get_table_names()) == before
    engine.dispose()


@pytest.mark.parametrize("damage", ["column", "type", "nullable", "pk", "unique", "fk", "restrict", "index", "partial_index", "check", "literal"])
def test_incompatible_complete_schema_fails_closed(damage):
    import sqlalchemy as sa
    module = migration()
    metadata = module._expected_sqlite_schema()
    run = metadata.tables["shadow_test_runs"]
    ledger = metadata.tables["shadow_ledger_entries"]
    if damage == "column": run._columns.remove(run.c.genesis_plan_sha256)
    if damage == "type": run.c.genesis_plan_sha256.type = sa.Integer()
    if damage == "nullable": run.c.genesis_plan_sha256.nullable = True
    if damage == "pk": run.append_constraint(sa.PrimaryKeyConstraint("id", "run_key_sha256"))
    if damage == "unique": run.constraints.remove(next(c for c in run.constraints if isinstance(c, sa.UniqueConstraint)))
    if damage in ("fk", "restrict"):
        fk = next(c for c in ledger.constraints if isinstance(c, sa.ForeignKeyConstraint))
        if damage == "fk": ledger.constraints.remove(fk)
        else:
            fk.ondelete = "CASCADE"
            for element in fk.elements: element.ondelete = "CASCADE"
    if damage == "index": ledger.indexes.clear()
    if damage == "partial_index":
        index = next(i for i in ledger.indexes if tuple(c.name for c in i.columns) == ("shadow_run_id",))
        ledger.indexes.remove(index)
        sa.Index(index.name, ledger.c.shadow_run_id, sqlite_where=ledger.c.shadow_run_id > 1)
    if damage in ("check", "literal"):
        check = next(c for c in run.constraints if isinstance(c, sa.CheckConstraint) and "base_currency" in str(c.sqltext))
        run.constraints.remove(check)
        if damage == "literal": run.append_constraint(sa.CheckConstraint("base_currency = 'rub' AND market_source = 'moex'"))
    for name, table in Base.metadata.tables.items():
        if name not in TABLES: table.to_metadata(metadata)
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        with pytest.raises(RuntimeError, match="INCOMPATIBLE_TASK303_SQLITE_SCHEMA"):
            module.upgrade()
        assert TABLES <= set(inspect(conn).get_table_names())
    engine.dispose()


def test_check_comparison_preserves_literals_and_operators():
    tokens = migration()._sql_tokens
    assert tokens("A >= 0 AND b = 'RUB'") == tokens("a>=0 and B='RUB'")
    assert tokens("b = 'RUB'") != tokens("b = 'rub'")
    assert tokens("a >= 0") != tokens("a > 0")
