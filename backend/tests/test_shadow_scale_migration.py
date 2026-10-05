"""Disposable SQLite migration and compatibility gates."""
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine, inspect, text
from alembic.operations import Operations
from alembic.migration import MigrationContext
from app.db.base import Base


def migration():
    path=Path(__file__).resolve().parents[1]/'alembic'/'versions'/'202610040001_shadow_scale_experiment_persistence.py'
    spec=importlib.util.spec_from_file_location('scale_migration',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


@pytest.mark.parametrize('precreated',[False,True])
def test_upgrade_roundtrip_exact_schema(precreated):
    e=create_engine('sqlite://');m=migration();tables=set(m.TASK305_TABLES)
    Base.metadata.create_all(e,tables=[t for n,t in Base.metadata.tables.items() if precreated or n not in tables])
    with e.begin() as c,Operations.context(MigrationContext.configure(c)):
        m.upgrade();m._validate_precreated_sqlite_schema(inspect(c))
        assert (m.revision,m.down_revision)==('202610040001','202610030002')
        for n in tables:
            assert inspect(c).get_pk_constraint(n)['constrained_columns']==['id']
            assert inspect(c).get_unique_constraints(n) and inspect(c).get_check_constraints(n)
            assert all(f['options']['ondelete']=='RESTRICT' for f in inspect(c).get_foreign_keys(n))
        m.downgrade();assert not tables.intersection(inspect(c).get_table_names())
        m.upgrade();m._validate_precreated_sqlite_schema(inspect(c))
    e.dispose()


@pytest.mark.parametrize('count',[1,2,3])
def test_partial_refused_before_ddl(count):
    m=migration();e=create_engine('sqlite://')
    Base.metadata.create_all(e,tables=[Base.metadata.tables[n] for n in m.TASK305_TABLES[:count]])
    with e.begin() as c,Operations.context(MigrationContext.configure(c)):
        before=inspect(c).get_table_names()
        with pytest.raises(RuntimeError,match='PARTIAL_TASK305_SQLITE_SCHEMA'):m.upgrade()
        assert inspect(c).get_table_names()==before
    e.dispose()


@pytest.mark.parametrize('alteration',['column','index','check','nullability','type','fk'])
def test_incompatible_complete_schema_rejected(alteration):
    m=migration();e=create_engine('sqlite://');Base.metadata.create_all(e)
    with e.begin() as c,Operations.context(MigrationContext.configure(c)):
        if alteration=='column':c.execute(text('ALTER TABLE shadow_experiment_groups ADD COLUMN unexpected TEXT'))
        elif alteration=='index':c.execute(text('DROP INDEX ix_shadow_experiment_cases_shadow_run_id'))
        else:
            sql=c.execute(text("SELECT sql FROM sqlite_master WHERE name='shadow_experiment_cases'")).scalar_one()
            if alteration=='check':sql=sql.replace('ordinal BETWEEN 1 AND 7','ordinal BETWEEN 1 AND 8')
            elif alteration=='nullability':sql=sql.replace('ordinal INTEGER NOT NULL','ordinal INTEGER')
            elif alteration=='type':sql=sql.replace('ordinal INTEGER','ordinal TEXT')
            else:sql=sql.replace('ON DELETE RESTRICT','ON DELETE CASCADE')
            c.execute(text('PRAGMA writable_schema=ON'))
            c.execute(text("UPDATE sqlite_master SET sql=:sql WHERE name='shadow_experiment_cases'"),{'sql':sql})
            c.execute(text('PRAGMA writable_schema=OFF'))
            version=c.execute(text('PRAGMA schema_version')).scalar_one()
            c.execute(text(f'PRAGMA schema_version={version+1}'))
        with pytest.raises(RuntimeError,match='INCOMPATIBLE_TASK305_SQLITE_SCHEMA'):m.upgrade()
    e.dispose()


def test_downgrade_refuses_history():
    m=migration();e=create_engine('sqlite://');Base.metadata.create_all(e)
    values=dict(group_key_sha256='a'*64,status='ACTIVE',genesis_date='2026-10-01',planned_end_date='2026-12-30',
        horizon_days=90,common_market_trade_date='2026-10-01',source_code_sha='a'*40,
        source_universe_sha256='a'*64,investment_batch_sha256='a'*64,experiment_policy_sha256='a'*64,
        scale_policy_sha256='a'*64,scale_genesis_sha256='a'*64,capital_case_count=7)
    with e.begin() as c,Operations.context(MigrationContext.configure(c)):
        cols=','.join(values);params=','.join(':'+n for n in values)
        c.execute(text(f'INSERT INTO shadow_experiment_groups ({cols}) VALUES ({params})'),values)
        with pytest.raises(RuntimeError,match='SCALE_HISTORY_DOWNGRADE_FORBIDDEN'):m.downgrade()
        assert set(m.TASK305_TABLES)<=set(inspect(c).get_table_names())
    e.dispose()
