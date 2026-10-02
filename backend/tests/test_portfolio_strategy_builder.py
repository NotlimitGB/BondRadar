"""Synthetic rank-first allocation, retries, immutable evidence and final risk authority."""

import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path

import pytest

from app.schemas.portfolio_strategy import PortfolioStrategyPolicyV1, PortfolioStrategyRequest, PortfolioStrategyView
from app.services.portfolio_strategy_builder import PortfolioStrategyBuilder
import app.services.portfolio_strategy_builder as module
from app.services.risk_candidate_reducer import RiskCandidateReducer
from test_risk_candidate_reducer import investment, missing_issuer
from test_investment_model_reducer import evaluate
from test_unified_candidate_reducer import snapshot
from app.services.unified_candidate_reducer import UnifiedCandidateReducer

D = Decimal


def source(n=3, overrides=None, order=None):
    inputs = {i: {"legal_issuer_id":200+i, **(overrides or {}).get(i,{})} for i in range(1,n+1)}
    result = investment(n,inputs) if n else evaluate(UnifiedCandidateReducer.build(snapshot([], missing=(9,))))
    if order is not None:
        # Synthetic supplied ranking envelope: Strategy must obey it, never re-score.
        ranks = {i:r for r,i in enumerate(order,1)}
        evaluations = tuple(e.model_copy(update={"rank":ranks[e.bond_id]}) for e in result.evaluations)
        result = result.model_copy(update={"evaluations":evaluations,"ranked_bond_ids":tuple(order)})
    return result


def build(batch=None, capital="100000"):
    batch = batch if batch is not None else source()
    return PortfolioStrategyBuilder.build(batch,RiskCandidateReducer.build(batch),PortfolioStrategyRequest(capital_rub=D(capital)))


def replay(result):
    weights = {}
    for index,t in enumerate(result.allocation_attempts,1):
        assert t.sequence_number == index
        assert t.previous_weight == weights.get(t.bond_id,D(0))
        assert t.previous_invested_weight == sum(weights.values(),D(0))
        assert t.attempted_invested_weight == t.previous_invested_weight-t.previous_weight+t.attempted_weight
        assert t.accepted is (t.risk_status == "PASS")
        assert t.attempted_weight-t.previous_weight == (D(".05") if t.phase == "SEED" else D(".01"))
        if t.accepted: weights[t.bond_id] = t.attempted_weight
    assert weights == {p.bond_id:p.target_weight for p in result.positions}


def test_frozen_policy_contracts_and_exact_defaults():
    p = PortfolioStrategyPolicyV1()
    assert (p.target_invested_weight,p.max_positions,p.min_initial_position_weight,p.allocation_increment) == (D("1.00"),10,D(".05"),D(".01"))
    for field,value in (("target_invested_weight",D(".99")),("max_positions",11),
                        ("min_initial_position_weight",D(".04")),("allocation_increment",D(".02"))):
        with pytest.raises(ValueError): PortfolioStrategyPolicyV1(**{field:value})
    result = build()
    for obj in (p,result,result.request,result.positions[0],result.non_selections if result.non_selections else result.summary,
                result.provenance,result.capabilities,result.allocation_attempts[0]):
        if isinstance(obj,tuple): continue
        with pytest.raises(ValueError): setattr(obj,next(iter(type(obj).model_fields)),None)
        with pytest.raises(ValueError): type(obj)(**obj.model_dump(),unexpected=True)
    assert PortfolioStrategyView.model_validate_json(result.model_dump_json()).model_dump() == result.model_dump()
    assert all(v is False for k,v in result.capabilities.model_dump().items() if k not in {
        "portfolio_strategy_ready","portfolio_construction_ready","deterministic_selection_ready",
        "risk_constrained_allocation_ready","target_weights_ready"})


def test_rank_order_seed_grid_and_no_normalization():
    s = source(order=(3,1,2))
    result = build(s)
    assert [t.bond_id for t in result.allocation_attempts[:3]] == [3,1,2]
    assert all(t.phase == "SEED" and t.attempted_weight == D(".05") for t in result.allocation_attempts[:3])
    assert [p.bond_id for p in result.positions] == [3,1,2]
    assert result.status == "PARTIALLY_INVESTED" and result.summary.invested_weight == D(".60")
    assert result.summary.cash_weight == D(".40")
    assert all(p.target_weight == D(".20") and p.accepted_top_up_count == 15 for p in result.positions)
    assert result.final_risk_evaluation.status == "PASS"
    assert result.source_investment_batch is s
    replay(result)


def test_deferred_high_duration_seed_is_retried_and_admitted():
    result = build(source(overrides={1:{"modified_duration_years":D(5)}},order=(1,2,3)))
    attempts = [t for t in result.allocation_attempts if t.bond_id == 1 and t.phase == "SEED"]
    assert attempts[0].accepted is False and attempts[0].risk_reasons == ("PORTFOLIO_DURATION_LIMIT_EXCEEDED",)
    assert attempts[1].round_number > attempts[0].round_number and attempts[1].accepted is True
    assert result.positions[0].bond_id == 1
    replay(result)


def test_rejected_top_up_is_retried_after_composition_changes():
    # High duration alongside a second high-duration bond: low-duration growth can unblock later attempts.
    result = build(source(4,overrides={1:{"modified_duration_years":D(5)},2:{"modified_duration_years":D(5)},
                                     3:{"modified_duration_years":D(3)},4:{"modified_duration_years":D(3)}},order=(1,2,3,4)))
    rejected = [t for t in result.allocation_attempts if t.phase == "TOP_UP" and not t.accepted]
    assert rejected
    assert any(later.bond_id == t.bond_id and later.phase == "TOP_UP" and later.round_number > t.round_number and later.accepted
               for t in rejected for later in result.allocation_attempts)
    replay(result)


def test_max_ten_no_replacement_and_nonselection_visibility():
    result = build(source(11,order=tuple(range(1,12))),capital="1000000")
    assert result.summary.selected_count == 10 and result.summary.invested_weight == D(".50")
    assert [p.bond_id for p in result.positions] == list(range(1,11))
    assert result.non_selections[0].bond_id == 11 and result.non_selections[0].reason == "MAX_POSITIONS_REACHED"
    assert not any(t.bond_id == 11 for t in result.allocation_attempts)
    assert len(result.positions)+len(result.non_selections) == 11
    replay(result)


def test_full_investment_target_reason_and_final_verification(monkeypatch):
    calls = []
    original = module.PortfolioRiskEvaluator.build
    def evaluate(risk,proposal):
        calls.append(proposal)
        return original(risk,proposal)
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",evaluate)
    result = build(source(11,order=tuple(range(1,12))))
    assert result.status == "FULLY_INVESTED" and result.summary.cash_weight == 0
    assert len(calls) == len(result.allocation_attempts)+1
    assert calls[-1] == result.final_risk_evaluation.proposal
    assert result.non_selections[0].reason == "TARGET_INVESTED_WEIGHT_REACHED"
    replay(result)


@pytest.mark.parametrize("kind",["unavailable","blocked","capacity","duration","empty"])
def test_empty_paths_and_reasons(kind):
    if kind == "unavailable": s = source(2)
    elif kind == "blocked": s = source(overrides={i:{"liquidity_score_v1":D(24)} for i in (1,2,3)})
    elif kind == "capacity": s = source(overrides={i:{"median_daily_turnover_value":D(99999)} for i in (1,2,3)})
    elif kind == "duration": s = source(overrides={i:{"modified_duration_years":D(5)} for i in (1,2,3)})
    else: s = source(0)
    result = build(s)
    assert result.status == "EMPTY" and result.summary.cash_weight == 1
    assert result.final_risk_evaluation.status == "PASS" and result.summary.final_invested_weighted_modified_duration_years is None
    assert result.summary.selected_count == 0 and len(result.non_selections) == len(s.evaluations)
    if kind in ("unavailable","blocked"): assert result.allocation_attempts == ()
    if kind == "blocked": assert all(n.reason == "RISK_CANDIDATE_BLOCKED" and n.risk_reasons for n in result.non_selections)
    if kind in ("capacity","duration"): assert all(n.reason == "MIN_INITIAL_POSITION_NOT_ADMISSIBLE" for n in result.non_selections)
    replay(result)


def test_capital_liquidity_and_exact_unallocated_cash():
    s = source(overrides={1:{"median_daily_turnover_value":D(340000)},2:{"median_daily_turnover_value":D(90000)}})
    small = build(s)
    large = build(s,capital="1000000")
    assert small.summary.invested_weight > large.summary.invested_weight
    assert 2 not in [p.bond_id for p in small.positions]
    # Four independent .20 caps, with one liquidity capacity constrained to .13.
    r = build(source(4,overrides={1:{"median_daily_turnover_value":D(260000)}}))
    assert r.summary.invested_weight == D(".73") and r.summary.cash_weight == D(".27")
    replay(r)


def test_same_issuer_and_dv01_rejections_are_exact_risk_reasons():
    same = build(source(overrides={i:{"legal_issuer_id":201} for i in (1,2,3)}))
    assert same.summary.invested_weight == D(".25")
    assert any(t.risk_reasons == ("ISSUER_WEIGHT_LIMIT_EXCEEDED",) for t in same.allocation_attempts)
    dv01 = build(source(overrides={i:{"relative_price_sensitivity_per_1bp":D(".001")} for i in (1,2,3)}))
    assert dv01.summary.invested_weight == D(".40")
    assert any(t.risk_reasons == ("PORTFOLIO_DV01_LIMIT_EXCEEDED",) for t in dv01.allocation_attempts)
    replay(same); replay(dv01)


def test_missing_issuer_blocker_preserved():
    result = build(missing_issuer(source()))
    assert result.non_selections[0].reason == "RISK_CANDIDATE_BLOCKED"
    assert "ISSUER_IDENTITY_UNAVAILABLE" in result.non_selections[0].risk_reasons


def test_source_and_decimal_immutability_determinism():
    s = source()
    r = RiskCandidateReducer.build(s)
    request = PortfolioStrategyRequest(capital_rub=D(100000))
    before = deepcopy((s.model_dump(),r.model_dump(),request.model_dump()))
    result = PortfolioStrategyBuilder.build(s,r,request)
    with localcontext() as ctx:
        ctx.prec=6; ctx.rounding=ROUND_UP
        altered = PortfolioStrategyBuilder.build(s,r,request)
        assert (ctx.prec,ctx.rounding) == (6,ROUND_UP)
    assert result.model_dump() == altered.model_dump()
    assert result.model_dump_json() == build(s).model_dump_json()
    assert before == (s.model_dump(),r.model_dump(),request.model_dump())


@pytest.mark.parametrize("capital",[0,True,1.0,"1",D(0),D(-1),D("NaN"),D("Infinity")])
def test_capital_types(capital):
    with pytest.raises(ValueError): PortfolioStrategyRequest(capital_rub=capital)


@pytest.mark.parametrize("change",["version","pit","count","rank","source","risk_policy","risk_candidate"])
def test_malformed_inputs_fail_closed(change):
    s = source(); r = RiskCandidateReducer.build(s)
    if change == "version": s=s.model_copy(update={"contract_version":"wrong"})
    elif change == "pit": s=s.model_copy(update={"pit_ready":True})
    elif change == "count": s=s.model_copy(update={"candidate_count":4})
    elif change == "rank": s=s.model_copy(update={"ranked_bond_ids":(1,1,3)})
    elif change == "source": s=source(overrides={1:{"median_daily_turnover_value":D(900000)}})
    elif change == "risk_policy": r=r.model_copy(update={"policy":r.policy.model_copy(update={"max_position_weight":D(".30")})})
    else: r=r.model_copy(update={"candidates":(r.candidates[0].model_copy(update={"status":"BLOCKED"}),*r.candidates[1:])})
    with pytest.raises(ValueError): PortfolioStrategyBuilder.build(s,r,PortfolioStrategyRequest(capital_rub=D(100000)))


def test_final_blocked_result_is_contract_failure(monkeypatch):
    original = module.PortfolioRiskEvaluator.build
    def blocked_empty(r,p):
        result=original(r,p)
        return result.model_copy(update={"status":"BLOCKED"}) if not p.positions else result
    monkeypatch.setattr(module.PortfolioRiskEvaluator,"build",blocked_empty)
    with pytest.raises(ValueError): build(source(0))


@pytest.mark.parametrize("argument",["investment","risk","request","request_tampered"])
def test_wrong_direct_argument_types(argument):
    s=source(); r=RiskCandidateReducer.build(s); request=PortfolioStrategyRequest(capital_rub=D(100000))
    if argument == "investment": s={}
    elif argument == "risk": r={}
    elif argument == "request": request={}
    else: request=request.model_copy(update={"capital_rub":D("NaN")})
    with pytest.raises(ValueError): PortfolioStrategyBuilder.build(s,r,request)


def test_ast_pure_no_legacy_or_risk_formulas():
    path=Path(__file__).parents[1]/"app/services/portfolio_strategy_builder.py"
    tree=ast.parse(path.read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert all(i.startswith(("decimal","app.schemas.","app.services.risk_candidate_reducer","app.services.portfolio_risk_evaluator")) for i in imports)
    forbidden={"add","delete","flush","commit","rollback","execute","open","request","sync","quantize"}
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in forbidden for n in ast.walk(tree))
    attributes={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    assert not attributes.intersection({"max_issuer_weight","max_portfolio_relative_dv01_per_1bp",
        "max_position_to_median_daily_turnover","max_invested_weighted_modified_duration_years"})
