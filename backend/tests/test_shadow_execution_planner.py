"""Integer lots, cash identities, partial evidence and post-rounding risk."""

import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path

import pytest

from app.schemas.shadow_execution import ShadowExecutionPolicyV1, ShadowExecutionPlanView
from app.services.shadow_execution_planner import ShadowExecutionPlanner, exact_lot_floor
import app.services.shadow_execution_planner as module
from test_shadow_execution_terms_loader import strategy, terms

D=Decimal


@pytest.fixture(scope="module")
def selected(): return strategy(overrides={i:{"dirty_value_currency":D(1020)} for i in (1,2,3)})


def test_exact_basic_lot_cash_and_task273_authority(selected):
    result=ShadowExecutionPlanner.build(selected,terms(selected))
    assert result.status=="READY"
    for p in result.positions:
        assert (p.target_amount_rub,p.lot_dirty_value_rub,p.planned_lot_count,p.planned_bond_quantity,
                p.planned_cash_cost_rub,p.target_shortfall_rub)==(D(20000),D(1020),19,19,D(19380),D(620))
        assert p.dirty_value_currency==p.source_strategy_position.source_evaluation.candidate.m3.dv01.dirty_value_currency
        assert p.planned_cash_cost_rub<=p.target_amount_rub and p.realized_shadow_weight==D(".1938")
    m=result.summary
    assert (m.planned_shadow_invested_rub,m.execution_tracking_gap_rub,m.shadow_cash_rub)==(D(58140),D(1860),D(41860))
    assert m.shadow_cash_rub==selected.summary.cash_rub+m.execution_tracking_gap_rub==m.capital_rub-m.planned_shadow_invested_rub
    assert m.realized_invested_weight==D(".5814") and m.realized_cash_weight==D(".4186")
    assert result.post_rounding_risk_evaluation.status=="PASS"


def test_multiunit_lots_no_cash_redistribution(selected):
    result=ShadowExecutionPlanner.build(selected,terms(selected,{i:{"lot_size":10} for i in (1,2,3)}))
    assert all(p.planned_lot_count==1 and p.planned_bond_quantity==10 and p.lot_dirty_value_rub==D(10200) for p in result.positions)
    assert result.summary.shadow_cash_rub==D(69400)
    assert result.summary.shadow_cash_rub>D(10200) and all(p.planned_cash_cost_rub==D(10200) for p in result.positions)


@pytest.mark.parametrize("mode,expected",[("missing","PARTIAL"),("below","PARTIAL"),("all_missing","UNEXECUTABLE"),("all_below","UNEXECUTABLE")])
def test_zero_lot_and_partial_semantics(selected,mode,expected):
    changes={1:None} if mode=="missing" else {1:{"lot_size":100}} if mode=="below" else {
        i:None if mode=="all_missing" else {"lot_size":100} for i in (1,2,3)}
    result=ShadowExecutionPlanner.build(selected,terms(selected,changes))
    assert result.status==expected and len(result.positions)==len(selected.positions)
    for p in result.positions:
        if p.status!="PLANNED":
            assert p.planned_lot_count==p.planned_bond_quantity==p.planned_cash_cost_rub==p.realized_shadow_weight==0
            assert p.target_shortfall_rub==p.target_amount_rub and p.target_weight_gap==p.target_weight
    assert result.post_rounding_risk_evaluation.status=="PASS"


def test_target_below_one_lot_5000_10200():
    s=strategy(overrides={i:{"dirty_value_currency":D(1020)} for i in (1,2,3)},capital="25000")
    result=ShadowExecutionPlanner.build(s,terms(s,{i:{"lot_size":10} for i in (1,2,3)}))
    assert result.status=="UNEXECUTABLE" and all(p.target_amount_rub==D(5000) and p.status=="TARGET_BELOW_ONE_LOT" for p in result.positions)


def test_empty_plan():
    s=strategy(0); result=ShadowExecutionPlanner.build(s,terms(s))
    assert result.status=="EMPTY" and result.positions==() and result.summary.shadow_cash_rub==D(100000)
    assert result.summary.realized_cash_weight==1 and result.post_rounding_risk_evaluation.status=="PASS"


def test_asymmetric_rounding_can_block_duration_without_repair():
    s=strategy(overrides={1:{"modified_duration_years":D(5)}})
    assert s.final_risk_evaluation.status=="PASS"
    result=ShadowExecutionPlanner.build(s,terms(s,{2:{"lot_size":100},3:{"lot_size":100}}))
    assert result.status=="RISK_BLOCKED"
    assert result.post_rounding_risk_evaluation.reasons==("PORTFOLIO_DURATION_LIMIT_EXCEEDED",)
    p=next(p for p in result.positions if p.bond_id==1)
    assert p.planned_lot_count==20 and p.planned_cash_cost_rub==D(20000)
    assert result.summary.planned_position_count==1 and result.summary.zero_lot_position_count==2
    assert result.quality_flags==("POST_ROUNDING_RISK_BLOCKED","TARGET_BELOW_ONE_LOT")


def test_exact_floor_cannot_round_quotient_up():
    target=D("1.99999999999999999999999999999")
    with localcontext() as ctx:
        ctx.prec=28
        assert target/D(1)==2
        assert exact_lot_floor(target,D(1))==1
    for target,value,expected in ((D(20000),D(1020),19),(D(20400),D(1020),20),(D(0),D(1),0)):
        assert exact_lot_floor(target,value)==expected


def test_exact_one_final_risk_call_and_weights(selected,monkeypatch):
    original=module.PortfolioRiskEvaluator.build; calls=[]
    def evaluate(r,p): calls.append((r,p)); return original(r,p)
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",evaluate)
    result=ShadowExecutionPlanner.build(selected,terms(selected,{1:None}))
    assert len(calls)==1 and calls[0][0] is selected.source_risk_batch
    assert {p.bond_id:p.target_weight for p in calls[0][1].positions}=={
        p.bond_id:p.realized_shadow_weight for p in result.positions if p.status=="PLANNED"}
    assert result.post_rounding_risk_evaluation.proposal==calls[0][1]


@pytest.mark.parametrize("dirty",[D(0),D(-1)])
def test_nonpositive_dirty_is_malformed_not_terms_unavailability(dirty):
    s=strategy(overrides={1:{"dirty_value_currency":dirty}})
    with pytest.raises(ValueError): ShadowExecutionPlanner.build(s,terms(s))


@pytest.mark.parametrize("change",["proposal","source","empty_blocked"])
def test_contradictory_final_risk_result_rejected(selected,monkeypatch,change):
    original=module.PortfolioRiskEvaluator.build
    def malformed(r,p):
        result=original(r,p)
        if change=="proposal": return result.model_copy(update={"proposal":p.model_copy(update={"capital_rub":D(1)})})
        if change=="source": return result.model_copy(update={"source_risk_batch":r.model_copy(update={"candidate_count":0})})
        return result.model_copy(update={"status":"BLOCKED"})
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",malformed)
    with pytest.raises(ValueError): ShadowExecutionPlanner.build(selected,terms(selected,{i:None for i in (1,2,3)}) if change=="empty_blocked" else terms(selected))


def test_frozen_roundtrip_determinism_context_and_inputs(selected):
    t=terms(selected); before=deepcopy((selected.model_dump(),t.model_dump()))
    result=ShadowExecutionPlanner.build(selected,t)
    with localcontext() as ctx:
        ctx.prec=6; ctx.rounding=ROUND_UP
        changed=ShadowExecutionPlanner.build(selected,t)
        assert (ctx.prec,ctx.rounding)==(6,ROUND_UP)
    assert result.model_dump()==changed.model_dump() and result.model_dump_json()==changed.model_dump_json()
    assert before==(selected.model_dump(),t.model_dump()) and result.source_strategy is selected and result.execution_terms_batch is t
    assert ShadowExecutionPlanView.model_validate_json(result.model_dump_json()).model_dump()==result.model_dump()
    for obj in (result,result.policy,t,t.terms[0],result.positions[0],result.summary,result.provenance,result.capabilities):
        with pytest.raises(ValueError): setattr(obj,next(iter(type(obj).model_fields)),None)
        with pytest.raises(ValueError): type(obj)(**obj.model_dump(),extra=True)
    p=ShadowExecutionPolicyV1()
    assert p.lot_rounding_mode=="FLOOR_TO_TARGET" and p.require_verified_lot_size and p.require_verified_trading_board
    for name in ("allow_target_overshoot","redistribute_residual_cash","use_transaction_costs","use_slippage","use_market_impact"):
        assert getattr(p,name) is False
        with pytest.raises(ValueError): ShadowExecutionPolicyV1(**{name:True})
    assert result.capabilities.shadow_ledger_ready is result.capabilities.broker_execution_ready is False


@pytest.mark.parametrize("change",["version","pit","summary","position","source","trace","risk","profile","dirty"])
def test_malformed_strategy_fails_before_risk_call(selected,monkeypatch,change):
    s=selected; t=terms(s)
    if change=="version": s=s.model_copy(update={"contract_version":"bad"})
    elif change=="pit": s=s.model_copy(update={"pit_ready":True})
    elif change=="summary": s=s.model_copy(update={"summary":s.summary.model_copy(update={"cash_rub":D(999)})})
    elif change=="position": s=s.model_copy(update={"positions":(s.positions[0],s.positions[0],*s.positions[2:])})
    elif change=="source": s=s.model_copy(update={"positions":(s.positions[0].model_copy(update={"target_amount_rub":D(1)}),*s.positions[1:])})
    elif change=="trace": s=s.model_copy(update={"allocation_attempts":(s.allocation_attempts[0].model_copy(update={"accepted":False}),*s.allocation_attempts[1:])})
    elif change=="risk": s=s.model_copy(update={"final_risk_evaluation":s.final_risk_evaluation.model_copy(update={"status":"BLOCKED"})})
    else:
        p=s.positions[0]; e=p.source_evaluation; c=e.candidate
        if change=="dirty": c=c.model_copy(update={"m3":c.m3.model_copy(update={"dv01":c.m3.dv01.model_copy(update={"dirty_value_currency":D("NaN")})})})
        else: c=c.model_copy(update={"provenance":c.provenance.model_copy(update={"security_master_profile_id":999})})
        s=s.model_copy(update={"positions":(p.model_copy(update={"source_evaluation":e.model_copy(update={"candidate":c})}),*s.positions[1:])})
    def forbidden(*a,**k): pytest.fail("Malformed input reached risk evaluator")
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",forbidden)
    with pytest.raises(ValueError): ShadowExecutionPlanner.build(s,t)


@pytest.mark.parametrize("change",["version","pit","count","identity","order","ready_claim","lot","nominal","types"])
def test_malformed_terms(selected,change):
    t=terms(selected)
    if change=="version": t=t.model_copy(update={"contract_version":"bad"})
    elif change=="pit": t=t.model_copy(update={"pit_ready":True})
    elif change=="count": t=t.model_copy(update={"ready_count":0})
    elif change=="identity": t=t.model_copy(update={"as_of_date":selected.as_of_date.replace(year=2025)})
    elif change=="order": t=t.model_copy(update={"terms":tuple(reversed(t.terms))})
    elif change=="types": t={}
    else:
        changes={"status":"UNAVAILABLE"} if change=="ready_claim" else {"lot_size":True} if change=="lot" else {"nominal_value":D(999)}
        t=t.model_copy(update={"terms":(t.terms[0].model_copy(update=changes),*t.terms[1:])})
    with pytest.raises(ValueError): ShadowExecutionPlanner.build(selected,t)


def test_ast_no_sources_mutations_pricing_or_risk_formulas():
    path=Path(__file__).parents[1]/"app/services/shadow_execution_planner.py"
    tree=ast.parse(path.read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert all(i.startswith(("decimal","app.schemas.","app.services.risk_candidate_reducer","app.services.portfolio_risk_evaluator")) for i in imports)
    forbidden={"add","delete","flush","commit","rollback","execute","open","sync","request","quantize","round"}
    assert not any(isinstance(n,ast.Call) and (isinstance(n.func,ast.Attribute) and n.func.attr in forbidden and not
        (n.func.attr=="add" and isinstance(n.func.value,ast.Name) and n.func.value.id=="flags") or
        isinstance(n.func,ast.Name) and n.func.id in forbidden) for n in ast.walk(tree))
    assert not any(isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Mult)) and any(
        isinstance(child,ast.Attribute) and child.attr in {"clean_quote_pct","nkd_currency","clean_value_currency","nominal_value"}
        for child in ast.walk(n)) for n in ast.walk(tree))
    attributes={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    assert not attributes.intersection({"max_issuer_weight","max_portfolio_relative_dv01_per_1bp","max_invested_weighted_modified_duration_years"})
