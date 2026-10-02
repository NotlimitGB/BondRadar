"""One authoritative read-only load and pure context delegation."""

import ast
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.models.company import Company
from app.services.investment_model_snapshot_service import InvestmentModelSnapshotService, build_peer_contexts
from app.services.investment_model_reducer import InvestmentModelReducer
import app.services.investment_model_snapshot_service as module
from test_investment_model_reducer import batch
from test_unified_candidate_reducer import DAY


def test_one_load_reducer_context_reuse_and_caller_pending_state(db_session, monkeypatch):
    db = db_session
    dirty, deleted = Company(name="Old", ticker="TASK299_DIRTY"), Company(name="Delete", ticker="TASK299_DELETE")
    db.add_all([dirty, deleted]); db.commit()
    dirty.name = "Caller edit"; db.delete(deleted)
    pending = Company(name="Pending", ticker="TASK299_NEW"); db.add(pending)
    db.autoflush = True
    state = (set(db.new), set(db.dirty), set(db.deleted))
    source = batch().model_copy(update={"max_market_age_days":3, "max_curve_age_days":4,
        "liquidity_lookback_calendar_days":20,"liquidity_min_observation_days":2})
    before = deepcopy(source.model_dump())
    expected = InvestmentModelReducer.build(source, build_peer_contexts(source))
    calls, sql = [], []
    def capture(conn, cursor, statement, params, context, many): sql.append(statement)
    def load(self, ids, day, **kwargs):
        assert self.db is db and db.autoflush is False and ids == (1,2,3) and day == DAY
        assert kwargs == dict(market_source="moex",max_market_age_days=3,max_curve_age_days=4,
            liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
        calls.append("load"); db.execute(select(Company.id)).all(); return source
    reducer = InvestmentModelReducer.build
    def reduce(value, contexts):
        calls.append("reducer"); assert value is source and db.autoflush is False
        return reducer(value, contexts)
    monkeypatch.setattr(module.UnifiedCandidateSnapshotService, "build", load)
    monkeypatch.setattr(module.InvestmentModelReducer, "build", reduce)
    def forbidden(*args, **kwargs): pytest.fail("Mutation attempted")
    for name in ("add", "add_all", "delete", "flush", "commit", "rollback"):
        monkeypatch.setattr(db, name, forbidden)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        result = InvestmentModelSnapshotService(db).build([3,1,2],DAY,max_market_age_days=3,
            max_curve_age_days=4,liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
    finally: event.remove(db.bind, "before_cursor_execute", capture)
    assert calls == ["load", "reducer"] and result.model_dump() == expected.model_dump()
    assert sql and all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert state == (set(db.new), set(db.dirty), set(db.deleted)) and pending.id is None
    assert db.autoflush is True and source.model_dump() == before


@pytest.mark.parametrize("ids,kwargs", [(None,{}),([],{}),("1",{}),({1},{}),({1:1},{}),(iter([1]),{}),
    ([True],{}),([1,1],{}),([0],{}),([1.0],{}),([1],{"as_of_date":datetime(2026,9,19)}),
    ([1],{"market_source":"MOEX"}),([1],{"max_market_age_days":True}),([1],{"max_curve_age_days":-1}),
    ([1],{"liquidity_lookback_calendar_days":0}),([1],{"liquidity_min_observation_days":31}),
    ([1],{"liquidity_min_observation_days":False}),([1],{"as_of_date":date.min})])
def test_validation_before_load(db_session, monkeypatch, ids, kwargs):
    def forbidden(*args, **kwargs): pytest.fail("Invalid request reached loader")
    monkeypatch.setattr(module.UnifiedCandidateSnapshotService,"build",forbidden)
    day = kwargs.pop("as_of_date",DAY)
    with pytest.raises(ValueError): InvestmentModelSnapshotService(db_session).build(ids,day,**kwargs)


def test_selector_cache_and_exact_delegation(monkeypatch):
    source = batch(3)
    compose = module.compose_credit_cohort_relative_value_member
    reduce = module.CreditCohortPeerSpreadDistributionService.build
    members, targets, sequences = [], [], []
    def composer(*args, **kwargs):
        result = compose(*args, **kwargs); members.append(result); return result
    def reducer(target, candidates, **kwargs):
        assert kwargs == {"min_peer_count":2}
        targets.append(target); sequences.append(candidates)
        return reduce(target,candidates,**kwargs)
    monkeypatch.setattr(module,"compose_credit_cohort_relative_value_member",composer)
    monkeypatch.setattr(module.CreditCohortPeerSpreadDistributionService,"build",reducer)
    contexts = build_peer_contexts(source)
    assert len(members) == len(targets) == len(contexts) == 3
    assert all(a is b for a,b in zip(members,targets))
    assert all(seq is sequences[0] for seq in sequences)
    assert all(any(ctx.member is member for member in members) for ctx in contexts)


def test_dependency_exception_and_wrong_return_context(db_session, monkeypatch):
    error = RuntimeError("Synthetic failure")
    def fail(*args,**kwargs): raise error
    monkeypatch.setattr(module.UnifiedCandidateSnapshotService,"build",fail)
    with pytest.raises(RuntimeError) as caught: InvestmentModelSnapshotService(db_session).build([1],DAY)
    assert caught.value is error
    monkeypatch.setattr(module.UnifiedCandidateSnapshotService,"build",lambda *a,**k:batch(3))
    with pytest.raises(ValueError): InvestmentModelSnapshotService(db_session).build([1],DAY)


def test_real_all_missing(db_session):
    result = InvestmentModelSnapshotService(db_session).build([999999],DAY)
    assert result.candidate_count == 0 and result.source_batch.status == "NO_EXISTING_BONDS"


def test_static_orchestration_boundary():
    tree = ast.parse((Path(__file__).parents[1]/"app/services/investment_model_snapshot_service.py").read_text())
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(any(token in name for token in ("app.models", "httpx", "requests", "Task280", "portfolio")) for name in imports)
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {"execute","commit","flush","sync","evaluate_bond"} for n in ast.walk(tree))
