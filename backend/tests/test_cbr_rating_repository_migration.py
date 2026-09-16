"""Task265 schema extension proofs; all database writes are disposable SQLite."""
from dataclasses import replace
import importlib.util
import json

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, event
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.base import Base
from app.models.credit_risk_evidence import CreditRiskSourceArtifact, CreditRatingEvent, CreditDefaultEvent
from app.services.credit_risk_evidence import CreditRiskEvidenceStore, SourceProvider, SourceKind
from test_credit_risk_evidence import _seed_issuer, _seed_bond, _artifact_input, _rating_input, _default_input
from test_credit_risk_evidence_migration import ROOT, TABLES, _config


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path/'migration.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(engine)
    config = _config(url)
    command.stamp(config, "202609150002")
    command.upgrade(config, "202609150003")
    with Session(engine, autoflush=False) as session:
        _seed_issuer(session); _seed_bond(session)
        store = CreditRiskEvidenceStore(session)
        artifact = store.persist_source_artifact(_artifact_input()).row
        store.persist_rating_event(artifact, _rating_input())
        default_artifact = store.persist_source_artifact(_artifact_input(SourceProvider.MOEX)).row
        store.persist_default_event(default_artifact, _default_input())
        session.commit()
    yield engine, config
    engine.dispose()


def projection(engine):
    with engine.connect() as connection:
        return {table: tuple(tuple(row) for row in connection.execute(
            select(Base.metadata.tables[table]).order_by(Base.metadata.tables[table].c.id))) for table in TABLES}


def objects(engine):
    reflection = inspect(engine)
    def canonical(items):
        return sorted(json.dumps(item, sort_keys=True) for item in items)
    return {table: (canonical(reflection.get_foreign_keys(table)), canonical(reflection.get_indexes(table)),
                   canonical(reflection.get_unique_constraints(table))) for table in TABLES}


def test_upgrade_downgrade_reupgrade_preserves_all_evidence(database):
    engine, config = database
    before, schema = projection(engine), objects(engine)
    command.upgrade(config, "202609160001")
    assert projection(engine) == before and objects(engine) == schema
    assert next(c for c in inspect(engine).get_columns(TABLES[1]) if c['name'] == 'rating_scale_raw')['nullable']
    command.downgrade(config, "202609150003")
    assert projection(engine) == before and objects(engine) == schema
    assert not next(c for c in inspect(engine).get_columns(TABLES[1]) if c['name'] == 'rating_scale_raw')['nullable']
    command.upgrade(config, "202609160001")
    assert projection(engine) == before and objects(engine) == schema


def test_null_scale_acceptance_empty_rejection_and_downgrade_before_ddl(database):
    engine, config = database
    command.upgrade(config, "202609160001")
    with Session(engine) as session:
        store = CreditRiskEvidenceStore(session)
        artifact = store.persist_source_artifact(replace(_artifact_input(), provider=SourceProvider.CBR_RATINGS,
            kind=SourceKind.RATING_REPOSITORY_RESPONSE, content_bytes=b'{"data": []}')).row
        stored = store.persist_rating_event(artifact, _rating_input(rating_scale_raw=None)).row
        assert stored.rating_scale_raw is None
        stored_id = stored.id
        session.commit()
    before = projection(engine)
    statements = []
    def record(c,u,s,p,x,m):
        statements.append(s)
    event.listen(Engine, "before_cursor_execute", record)
    try:
        with pytest.raises(RuntimeError, match="prevents downgrade"):
            command.downgrade(config, "202609150003")
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert not any(s.lstrip().upper().startswith(('DROP ', 'CREATE ', 'ALTER ')) for s in statements)
    assert projection(engine) == before
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            connection.execute(CreditRatingEvent.__table__.update().where(CreditRatingEvent.id == stored_id)
                               .values(rating_scale_raw=""))


def test_reflected_postgresql_names_missing_and_ambiguous():
    path = ROOT/'backend/alembic/versions/202609160001_cbr_rating_repository_support.py'
    spec = importlib.util.spec_from_file_location("task265_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    constraint = {'name': 'ck_credit_rating_events_raw_rat_9a01',
                  'sqltext': 'RATING_SCALE_RAW IS NULL OR rating_value_raw IS NOT NULL OR rating_action_raw IS NOT NULL'}
    keys = ('rating_scale_raw', 'rating_value_raw', 'rating_action_raw')
    assert migration._select_constraint([constraint], keys) == constraint['name']
    for invalid in ([], [constraint, dict(constraint, name='another')]):
        with pytest.raises(RuntimeError):
            migration._select_constraint(invalid, keys)
    source = path.read_text()
    assert 'op.f(name)' in source
    assert not any(s in source.upper() for s in ('INSERT INTO', 'UPDATE ', 'DELETE FROM'))
