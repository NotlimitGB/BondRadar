"""Single upstream load, exact delegation and read-only session ownership."""

import ast
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, select

from app.models.company import Company
from app.services.portfolio_strategy_snapshot_service import PortfolioStrategySnapshotService
import app.services.portfolio_strategy_snapshot_service as module
from app.services.portfolio_strategy_builder import PortfolioStrategyBuilder
from app.services.risk_candidate_reducer import RiskCandidateReducer
from app.schemas.portfolio_strategy import PortfolioStrategyRequest
from test_portfolio_strategy_builder import source
from test_unified_candidate_reducer import DAY

D=Decimal


def test_exact_delegation_forwarding_pending_state_and_parity(db_session,monkeypatch):
    db=db_session
    dirty,deleted=Company(name="Old",ticker="TASK301_DIRTY"),Company(name="Delete",ticker="TASK301_DELETE")
    db.add_all([dirty,deleted]); db.commit()
    dirty.name="Caller edit"; db.delete(deleted)
    pending=Company(name="Pending",ticker="TASK301_NEW"); db.add(pending)
    db.autoflush=True
    state=(set(db.new),set(db.dirty),set(db.deleted))
    s=source()
    s=s.model_copy(update={"source_batch":s.source_batch.model_copy(update={"max_market_age_days":3,
        "max_curve_age_days":4,"liquidity_lookback_calendar_days":20,"liquidity_min_observation_days":2})})
    before=s.model_dump()
    expected=PortfolioStrategyBuilder.build(s,RiskCandidateReducer.build(s),PortfolioStrategyRequest(capital_rub=D(100000)))
    reduce,build=RiskCandidateReducer.build,PortfolioStrategyBuilder.build
    calls,sql=[],[]
    def load(self,ids,day,**kwargs):
        assert self.db is db and db.autoflush is False and ids==(1,2,3) and day==DAY
        assert kwargs==dict(market_source="moex",max_market_age_days=3,max_curve_age_days=4,
            liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
        calls.append("load"); db.execute(select(Company.id)).all(); return s
    def reducer(value):
        assert value is s and db.autoflush is False
        calls.append("reduce"); return reduce(value)
    def builder(value,risk,request):
        assert value is s and risk.source_batch is s and request.capital_rub==D(100000) and db.autoflush is False
        calls.append("build"); return build(value,risk,request)
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",load)
    monkeypatch.setattr(module.RiskCandidateReducer,"build",reducer)
    monkeypatch.setattr(module.PortfolioStrategyBuilder,"build",builder)
    def forbidden(*a,**k): pytest.fail("Session mutation attempted")
    for name in ("add","add_all","delete","flush","commit","rollback"): monkeypatch.setattr(db,name,forbidden)
    def capture(conn,cursor,statement,params,context,many): sql.append(statement)
    event.listen(db.bind,"before_cursor_execute",capture)
    try:
        result=PortfolioStrategySnapshotService(db).build([3,2,1],DAY,D(100000),max_market_age_days=3,max_curve_age_days=4,
            liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
    finally: event.remove(db.bind,"before_cursor_execute",capture)
    assert calls==["load","reduce","build"] and result.model_dump()==expected.model_dump()
    assert sql and all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert state==(set(db.new),set(db.dirty),set(db.deleted)) and pending.id is None and db.autoflush is True
    assert s.model_dump()==before


@pytest.mark.parametrize("ids,kwargs",[(None,{}),([],{}),("1",{}),({1},{}),({1:1},{}),(iter([1]),{}),
    ([True],{}),([0],{}),([1.0],{}),([1,1],{}),([1],{"as_of_date":datetime(2026,9,19)}),
    ([1],{"market_source":"MOEX"}),([1],{"max_market_age_days":True}),([1],{"max_curve_age_days":-1}),
    ([1],{"liquidity_lookback_calendar_days":0}),([1],{"liquidity_min_observation_days":31}),
    ([1],{"liquidity_min_observation_days":False}),([1],{"as_of_date":date.min})])
def test_validation_before_calls(db_session,monkeypatch,ids,kwargs):
    def forbidden(*a,**k): pytest.fail("Invalid request reached loader")
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",forbidden)
    day=kwargs.get("as_of_date",DAY)
    other={k:v for k,v in kwargs.items() if k!="as_of_date"}
    with pytest.raises(ValueError): PortfolioStrategySnapshotService(db_session).build(ids,day,D(100000),**other)


@pytest.mark.parametrize("capital",[True,100000,"100000",0,D(0),D("NaN"),D("Infinity")])
def test_bad_capital_before_load(db_session,monkeypatch,capital):
    def forbidden(*a,**k): pytest.fail("Invalid capital reached loader")
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",forbidden)
    with pytest.raises(ValueError): PortfolioStrategySnapshotService(db_session).build([1],DAY,capital)


def test_real_all_missing_empty_and_exception_propagation(db_session,monkeypatch):
    result=PortfolioStrategySnapshotService(db_session).build([999999],DAY,D(100000))
    assert result.status=="EMPTY" and result.summary.candidate_count==0 and result.summary.cash_weight==1
    failure=RuntimeError("Synthetic dependency failure")
    def fail(*a,**k): raise failure
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",fail)
    with pytest.raises(RuntimeError) as caught: PortfolioStrategySnapshotService(db_session).build([1],DAY,D(100000))
    assert caught.value is failure


def test_wrong_upstream_request_context(db_session,monkeypatch):
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",lambda *a,**k:source())
    with pytest.raises(ValueError): PortfolioStrategySnapshotService(db_session).build([1],DAY,D(100000))


def test_ast_no_own_queries_or_lower_loaders():
    tree=ast.parse((Path(__file__).parents[1]/"app/services/portfolio_strategy_snapshot_service.py").read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any("models" in i or "risk_engine_snapshot" in i or "legacy" in i for i in imports)
    forbidden={"add","delete","flush","commit","rollback","execute","select","sync","open"}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in ast.walk(tree))
