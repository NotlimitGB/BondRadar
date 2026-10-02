"""Single loader/reducer/evaluator orchestration and caller session ownership."""

import ast
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.models.company import Company
from app.services.risk_engine_snapshot_service import RiskEngineSnapshotService
import app.services.risk_engine_snapshot_service as module
from app.services.risk_candidate_reducer import RiskCandidateReducer
from app.services.portfolio_risk_evaluator import PortfolioRiskEvaluator
from test_risk_candidate_reducer import investment
from test_portfolio_risk_evaluator import proposal
from test_unified_candidate_reducer import DAY


def test_delegation_forwarding_select_only_and_pending_state(db_session,monkeypatch):
    db = db_session
    dirty,deleted = Company(name="Old",ticker="TASK300_DIRTY"),Company(name="Delete",ticker="TASK300_DELETE")
    db.add_all([dirty,deleted]); db.commit()
    dirty.name="Caller edit"; db.delete(deleted)
    pending=Company(name="Pending",ticker="TASK300_NEW"); db.add(pending)
    db.autoflush=True
    state=(set(db.new),set(db.dirty),set(db.deleted))
    source=investment()
    source=source.model_copy(update={"source_batch":source.source_batch.model_copy(update={
        "max_market_age_days":3,"max_curve_age_days":4,"liquidity_lookback_calendar_days":20,"liquidity_min_observation_days":2})})
    p=proposal()
    before=(source.model_dump(),p.model_dump())
    expected=PortfolioRiskEvaluator.build(RiskCandidateReducer.build(source),p)
    reduce,evaluate=RiskCandidateReducer.build,PortfolioRiskEvaluator.build
    calls,sql=[],[]
    def capture(conn,cursor,statement,params,context,many): sql.append(statement)
    def load(self,ids,day,**kwargs):
        assert self.db is db and db.autoflush is False and ids==(1,2,3) and day==DAY
        assert kwargs==dict(market_source="moex",max_market_age_days=3,max_curve_age_days=4,
            liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
        calls.append("load"); db.execute(select(Company.id)).all(); return source
    def reducer(value):
        calls.append("reduce"); assert value is source and db.autoflush is False; return reduce(value)
    def evaluator(value,request):
        calls.append("evaluate"); assert db.autoflush is False and request.model_dump()==p.model_dump()
        return evaluate(value,request)
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",load)
    monkeypatch.setattr(module.RiskCandidateReducer,"build",reducer)
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",evaluator)
    def forbidden(*a,**k): pytest.fail("Session mutation attempted")
    for name in ("add","add_all","delete","flush","commit","rollback"): monkeypatch.setattr(db,name,forbidden)
    event.listen(db.bind,"before_cursor_execute",capture)
    try:
        result=RiskEngineSnapshotService(db).build([3,2,1],DAY,p,max_market_age_days=3,max_curve_age_days=4,
            liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
    finally: event.remove(db.bind,"before_cursor_execute",capture)
    assert calls==["load","reduce","evaluate"] and result.model_dump()==expected.model_dump()
    assert sql and all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert state==(set(db.new),set(db.dirty),set(db.deleted)) and pending.id is None and db.autoflush is True
    assert before==(source.model_dump(),p.model_dump())


@pytest.mark.parametrize("ids,kwargs", [(None,{}),([],{}),("1",{}),({1},{}),({1:1},{}),(iter([1]),{}),
    ([True],{}),([0],{}),([1,1],{}),([1.0],{}),([1],{"as_of_date":datetime(2026,9,19)}),
    ([1],{"market_source":"MOEX"}),([1],{"max_market_age_days":True}),([1],{"max_curve_age_days":-1}),
    ([1],{"liquidity_lookback_calendar_days":0}),([1],{"liquidity_min_observation_days":31}),
    ([1],{"liquidity_min_observation_days":False}),([1],{"as_of_date":date.min})])
def test_request_validation_before_load(db_session,monkeypatch,ids,kwargs):
    def forbidden(*a,**k): pytest.fail("Invalid request reached loader")
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",forbidden)
    day=kwargs.pop("as_of_date",DAY)
    with pytest.raises(ValueError): RiskEngineSnapshotService(db_session).build(ids,day,proposal(()),**kwargs)


def test_bad_proposal_before_load(db_session,monkeypatch):
    def forbidden(*a,**k): pytest.fail("Invalid proposal reached loader")
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",forbidden)
    for p in ({},proposal(((999,".1"),)),proposal(((1,".6"),(2,".6")))):
        with pytest.raises(ValueError): RiskEngineSnapshotService(db_session).build([1,2,3],DAY,p)


def test_upstream_failure_and_wrong_context(db_session,monkeypatch):
    failure=RuntimeError("Synthetic dependency error")
    def fail(*a,**k): raise failure
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",fail)
    with pytest.raises(RuntimeError) as caught: RiskEngineSnapshotService(db_session).build([1],DAY,proposal(()))
    assert caught.value is failure
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",lambda *a,**k:investment())
    with pytest.raises(ValueError): RiskEngineSnapshotService(db_session).build([1],DAY,proposal(()))


def test_real_all_missing_empty_portfolio(db_session):
    result=RiskEngineSnapshotService(db_session).build([999999],DAY,proposal(()))
    assert result.status=="PASS" and result.source_risk_batch.candidate_count==0
    assert result.quality_flags==("EMPTY_PORTFOLIO",)


def test_no_lower_loaders_mutations_or_allocation():
    tree=ast.parse((Path(__file__).parents[1]/"app/services/risk_engine_snapshot_service.py").read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(any(token in name for token in ("app.models","m3_","unified_candidate","moex","portfolio_construction","risk_assessment","httpx")) for name in imports)
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {"execute","add","delete","flush","commit","rollback","sync"} for n in ast.walk(tree))
