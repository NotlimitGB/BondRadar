"""One read-only orchestration chain, request forwarding and caller session ownership."""

import ast
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event

from app.models.company import Company
from app.services.shadow_execution_snapshot_service import ShadowExecutionSnapshotService
import app.services.shadow_execution_snapshot_service as module
from app.services.shadow_execution_planner import ShadowExecutionPlanner
from test_shadow_execution_terms_loader import strategy, terms, seed_profiles
from test_unified_candidate_reducer import DAY

D=Decimal


def test_exact_calls_forwarding_select_only_pending_state_and_parity(db_session,monkeypatch):
    s=strategy(); db=db_session; company=seed_profiles(db,s)
    deleted=Company(name="Deleted",ticker="TASK302_SNAPSHOT_DELETE"); db.add(deleted); db.commit()
    company.name="Caller dirty"; db.delete(deleted)
    pending=Company(name="Pending",ticker="TASK302_SNAPSHOT_NEW"); db.add(pending); db.autoflush=True
    state=(set(db.new),set(db.dirty),set(db.deleted)); before=s.model_dump()
    expected=ShadowExecutionPlanner.build(s,terms(s))
    load_terms,plan=module.ShadowExecutionTermsLoader.build,module.ShadowExecutionPlanner.build
    calls,sql=[],[]
    def load(self,ids,day,capital,**kwargs):
        assert self.db is db and db.autoflush is False and ids==(1,2,3) and day==DAY and capital==D(100000)
        assert kwargs==dict(market_source="moex",max_market_age_days=7,max_curve_age_days=7,
            liquidity_lookback_calendar_days=30,liquidity_min_observation_days=5)
        calls.append("strategy"); return s
    def term_load(self,value):
        assert value is s and db.autoflush is False; calls.append("terms"); return load_terms(self,value)
    def planner(value,t):
        assert value is s and db.autoflush is False; calls.append("plan"); return plan(value,t)
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",load)
    monkeypatch.setattr(module.ShadowExecutionTermsLoader,"build",term_load)
    monkeypatch.setattr(module.ShadowExecutionPlanner,"build",planner)
    def forbidden(*a,**k): pytest.fail("Snapshot mutation attempted")
    for name in ("add","add_all","delete","flush","commit","rollback"): monkeypatch.setattr(db,name,forbidden)
    def capture(conn,cursor,statement,params,context,many): sql.append(statement)
    event.listen(db.bind,"before_cursor_execute",capture)
    try: result=ShadowExecutionSnapshotService(db).build([3,1,2],DAY,D(100000))
    finally: event.remove(db.bind,"before_cursor_execute",capture)
    assert calls==["strategy","terms","plan"] and result.model_dump()==expected.model_dump()
    assert len(sql)==1 and sql[0].lstrip().upper().startswith("SELECT")
    assert state==(set(db.new),set(db.dirty),set(db.deleted)) and pending.id is None and db.autoflush is True
    assert s.model_dump()==before


@pytest.mark.parametrize("ids,kwargs",[(None,{}),([],{}),("1",{}),({1},{}),({1:1},{}),(iter([1]),{}),
    ([True],{}),([0],{}),([1.0],{}),([1,1],{}),([1],{"as_of_date":datetime(2026,9,19)}),
    ([1],{"market_source":"MOEX"}),([1],{"max_market_age_days":True}),([1],{"max_curve_age_days":-1}),
    ([1],{"liquidity_lookback_calendar_days":0}),([1],{"liquidity_min_observation_days":31}),
    ([1],{"liquidity_min_observation_days":False}),([1],{"as_of_date":date.min})])
def test_validation_before_load(db_session,monkeypatch,ids,kwargs):
    def forbidden(*a,**k): pytest.fail("Invalid request reached upstream")
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",forbidden)
    day=kwargs.get("as_of_date",DAY); other={k:v for k,v in kwargs.items() if k!="as_of_date"}
    with pytest.raises(ValueError): ShadowExecutionSnapshotService(db_session).build(ids,day,D(100000),**other)


@pytest.mark.parametrize("capital",[True,100000,"100000",D(0),D(-1),D("NaN"),D("Infinity")])
def test_bad_capital_before_load(db_session,monkeypatch,capital):
    def forbidden(*a,**k): pytest.fail("Invalid capital reached upstream")
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",forbidden)
    with pytest.raises(ValueError): ShadowExecutionSnapshotService(db_session).build([1],DAY,capital)


def test_real_all_missing_and_exception_propagation(db_session,monkeypatch):
    result=ShadowExecutionSnapshotService(db_session).build([999999],DAY,D(100000))
    assert result.status=="EMPTY" and result.summary.strategy_selected_count==0 and result.summary.shadow_cash_rub==D(100000)
    failure=RuntimeError("Synthetic dependency failure")
    def fail(*a,**k): raise failure
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",fail)
    with pytest.raises(RuntimeError) as caught: ShadowExecutionSnapshotService(db_session).build([1],DAY,D(100000))
    assert caught.value is failure


def test_wrong_returned_request_context(db_session,monkeypatch):
    s=strategy()
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",lambda *a,**k:s)
    for kwargs in ({},{"max_market_age_days":3},{"max_curve_age_days":4},
        {"liquidity_lookback_calendar_days":20},{"liquidity_min_observation_days":2}):
        ids=[1] if not kwargs else [1,2,3]
        with pytest.raises(ValueError): ShadowExecutionSnapshotService(db_session).build(ids,DAY,D(100000),**kwargs)


def test_nondefault_arguments_forwarded_exactly(db_session,monkeypatch):
    original=strategy()
    changes=dict(max_market_age_days=3,max_curve_age_days=4,liquidity_lookback_calendar_days=20,liquidity_min_observation_days=2)
    def rewrite(value):
        if isinstance(value,dict): return {k:changes.get(k,rewrite(v)) for k,v in value.items()}
        if isinstance(value,tuple): return tuple(rewrite(v) for v in value)
        if isinstance(value,list): return [rewrite(v) for v in value]
        return value
    s=type(original).model_validate(rewrite(original.model_dump()))
    def load(self,ids,day,capital,**kwargs):
        assert ids==(1,2,3) and day==DAY and capital==D(100000)
        assert kwargs==dict(market_source="moex",**changes)
        return s
    monkeypatch.setattr(module.PortfolioStrategySnapshotService,"build",load)
    result=ShadowExecutionSnapshotService(db_session).build([3,2,1],DAY,D(100000),**changes)
    assert result.status=="UNEXECUTABLE" and result.source_strategy is s


def test_ast_only_three_delegations_no_lower_sources():
    tree=ast.parse((Path(__file__).parents[1]/"app/services/shadow_execution_snapshot_service.py").read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any("models" in i or "tinvest" in i or "moex" in i or "paper" in i for i in imports)
    forbidden={"add","add_all","delete","flush","commit","rollback","execute","select","sync","request","open"}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in ast.walk(tree))
