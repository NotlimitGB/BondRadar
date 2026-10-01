"""Task298 request forwarding and read-only ownership boundary."""

import ast
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.models.company import Company
from app.services.unified_candidate_snapshot_service import UnifiedCandidateSnapshotService
import app.services.unified_candidate_snapshot_service as module
from app.services.unified_candidate_reducer import UnifiedCandidateReducer
from test_unified_candidate_reducer import DAY, composite, snapshot


def test_delegation_pending_state_select_only_and_exact_output(db_session, monkeypatch):
    db = db_session
    dirty = Company(name="Original", ticker="TASK298_DIRTY")
    deleted = Company(name="Deleted", ticker="TASK298_DELETE")
    db.add_all([dirty, deleted])
    db.commit()
    dirty.name = "Caller edit"
    db.delete(deleted)
    pending = Company(name="Pending", ticker="TASK298_NEW")
    db.add(pending)
    db.autoflush = True
    before = (set(db.new), set(db.dirty), set(db.deleted))
    source = snapshot([composite(1), composite(2)]).model_copy(update={
        "max_market_age_days":3, "max_curve_age_days":4,
        "liquidity_lookback_calendar_days":20, "liquidity_min_observation_days":2})
    original = source.model_dump()
    expected = UnifiedCandidateReducer.build(source)
    calls, statements = [], []
    def sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", sql)
    def runner(self, ids, day, **kwargs):
        assert self.db is db and db.autoflush is False
        assert ids == (1,2) and day == DAY
        assert kwargs == dict(market_source="moex", max_market_age_days=3, max_curve_age_days=4,
            liquidity_lookback_calendar_days=20, liquidity_min_observation_days=2)
        calls.append("runner")
        db.execute(select(Company.id)).all()
        return source
    reducer = UnifiedCandidateReducer.build
    def reduce(value):
        assert value is source and db.autoflush is False
        calls.append("reducer")
        return reducer(value)
    monkeypatch.setattr(module.M3AuditSnapshotService, "build", runner)
    monkeypatch.setattr(module.UnifiedCandidateReducer, "build", reduce)
    def forbidden(*args, **kwargs):
        pytest.fail("Task298 attempted a session mutation")
    for name in ("add", "add_all", "delete", "flush", "commit", "rollback"):
        monkeypatch.setattr(db, name, forbidden)
    try:
        result = UnifiedCandidateSnapshotService(db).build([2,1], DAY, max_market_age_days=3,
            max_curve_age_days=4, liquidity_lookback_calendar_days=20, liquidity_min_observation_days=2)
    finally:
        event.remove(db.bind, "before_cursor_execute", sql)
    assert calls == ["runner", "reducer"] and result.model_dump() == expected.model_dump()
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert (set(db.new), set(db.dirty), set(db.deleted)) == before and pending.id is None
    assert db.autoflush is True and source.model_dump() == original


@pytest.mark.parametrize("ids,kwargs", [(None,{}), ([],{}), ("1",{}), ({1},{}), ({1:1},{}),
    ([True],{}), ([0],{}), ([1,1],{}), ([1.0],{}), ([1],{"as_of_date":datetime(2026,9,19)}),
    ([1],{"market_source":"MOEX"}), ([1],{"max_market_age_days":True}),
    ([1],{"max_curve_age_days":-1}), ([1],{"liquidity_lookback_calendar_days":0}),
    ([1],{"liquidity_min_observation_days":31}), ([1],{"liquidity_min_observation_days":False}),
    ([1],{"as_of_date":date.min})])
def test_validation_before_dependency_call(db_session, monkeypatch, ids, kwargs):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid request reached Task286")
    monkeypatch.setattr(module.M3AuditSnapshotService, "build", forbidden)
    day = kwargs.pop("as_of_date", DAY)
    with pytest.raises(ValueError):
        UnifiedCandidateSnapshotService(db_session).build(ids, day, **kwargs)


def test_generators_rejected_and_dependency_failure_propagates(db_session, monkeypatch):
    error = RuntimeError("synthetic dependency failure")
    calls = []
    def failing(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(module.M3AuditSnapshotService, "build", failing)
    with pytest.raises(ValueError):
        UnifiedCandidateSnapshotService(db_session).build(iter([1]), DAY)
    with pytest.raises(RuntimeError) as exc:
        UnifiedCandidateSnapshotService(db_session).build([1], DAY)
    assert exc.value is error and calls == [1]


def test_real_m3_all_missing_integration(db_session):
    result = UnifiedCandidateSnapshotService(db_session).build([999999], DAY)
    assert result.status == "NO_EXISTING_BONDS" and result.candidate_count == 0
    assert result.exclusions[0].reasons == ("BOND_NOT_FOUND",)


def test_static_single_authority_no_hidden_calls():
    tree = ast.parse((Path(__file__).parents[1]/"app/services/unified_candidate_snapshot_service.py").read_text(encoding="utf-8"))
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    allowed = {"collections.abc", "datetime", "sqlalchemy.orm", "app.schemas.unified_candidate",
        "app.services.m3_audit_snapshot_service", "app.services.unified_candidate_reducer"}
    assert set(imported) <= allowed
    attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert attrs.count("build") == 2
    assert not set(attrs) & {"execute", "add", "delete", "flush", "commit", "rollback", "sync", "lookup"}
