"""Synthetic modern risk evidence and frozen candidate constraints."""

import ast
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP
from pathlib import Path

import pytest

from app.schemas.risk_engine import RiskEnginePolicyV1, RiskCandidateBatchView
from app.services.risk_candidate_reducer import RiskCandidateReducer, validate_risk_batch
from app.services.investment_model_reducer import InvestmentModelReducer
from app.services.investment_model_snapshot_service import build_peer_contexts
from test_investment_model_reducer import varied_batch

D = Decimal


def investment(n=3, overrides=None):
    values = []
    for i in range(1,n+1):
        values.append({"median_daily_turnover_value":D("1000000"),"liquidity_score_v1":D("75"),
            "modified_duration_years":D("2"),"relative_price_sensitivity_per_1bp":D("0.0002"),
            **(overrides or {}).get(i,{})})
    batch = varied_batch(values)
    return InvestmentModelReducer.build(batch,build_peer_contexts(batch))


def risk(n=3, overrides=None):
    return RiskCandidateReducer.build(investment(n,overrides))


def missing_issuer(source, index=0):
    data = source.model_dump()
    def clear(value):
        if isinstance(value,dict):
            return {key:None if key in ("legal_issuer_id","legal_issuer_source_issuer_id","legal_issuer_inn") else
                "MAPPING_MISSING" if key == "issuer_link_status" else False if key == "has_verified_legal_issuer" else clear(item)
                for key,item in value.items()}
        if isinstance(value,list): return [clear(item) for item in value]
        if isinstance(value,tuple): return tuple(clear(item) for item in value)
        return value
    candidate = clear(data["source_batch"]["candidates"][index])
    data["source_batch"]["candidates"] = tuple(candidate if j == index else c for j,c in enumerate(data["source_batch"]["candidates"]))
    data["evaluations"] = tuple({**e,"candidate":candidate} if j == index else e for j,e in enumerate(data["evaluations"]))
    return type(source).model_validate(data)


def test_frozen_policy_and_source_preservation():
    p = RiskEnginePolicyV1()
    assert [getattr(p,name) for name in type(p).model_fields if name not in ("contract_version","pit_ready")] == list(map(D,[".20",".25","25",".05","5","3.5",".00040","40"]))
    for name in type(p).model_fields:
        if name in ("contract_version","pit_ready"): continue
        with pytest.raises(ValueError): RiskEnginePolicyV1(**{name:D("NaN")})
        with pytest.raises(ValueError): RiskEnginePolicyV1(**{name:getattr(p,name)+D(1)})
    source = investment()
    before = deepcopy(source.model_dump())
    result = RiskCandidateReducer.build(source)
    assert result.eligible_count == 3 and result.blocked_count == result.investment_not_ready_count == 0
    assert result.source_batch is source and result.candidates[0].source_evaluation is source.evaluations[0]
    assert result.candidates[0].liquidity_capacity_amount_rub == D("50000")
    assert result.candidates[0].liquidity_capacity_weight is result.candidates[0].max_admissible_position_weight is None
    assert source.model_dump() == before
    validate_risk_batch(result)
    assert RiskCandidateBatchView.model_validate_json(result.model_dump_json()).model_dump() == result.model_dump()
    for obj in (p,result,result.candidates[0],result.provenance,result.capabilities):
        with pytest.raises(ValueError): setattr(obj,next(iter(type(obj).model_fields)),None)
        with pytest.raises(ValueError): type(obj)(**obj.model_dump(),extra=1)


@pytest.mark.parametrize("field,value,reason", [
    ("liquidity_score_v1",D("24.999999"),"LIQUIDITY_SCORE_BELOW_MINIMUM"),
    ("liquidity_score_v1",D("25"),None), ("liquidity_score_v1",D("25.001"),None),
    ("modified_duration_years",D("5"),None), ("modified_duration_years",D("5.000001"),"CANDIDATE_DURATION_LIMIT_EXCEEDED"),
    ("median_daily_turnover_value",D("0"),"LIQUIDITY_CAPACITY_UNAVAILABLE"),
    ("modified_duration_years",D("0"),None), ("relative_price_sensitivity_per_1bp",D("0"),None),
    ("dv01_currency_per_bond",D("0"),None)])
def test_candidate_boundaries(field,value,reason):
    result = risk(overrides={1:{field:value}}).candidates[0]
    assert result.status == ("BLOCKED" if reason else "ELIGIBLE")
    assert result.reasons == ((reason,) if reason else ())


def test_missing_issuer_and_all_reasons():
    source = missing_issuer(investment(overrides={1:{"liquidity_score_v1":D(24),"median_daily_turnover_value":D(0),"modified_duration_years":D(6)}}))
    c = RiskCandidateReducer.build(source).candidates[0]
    assert c.status == "BLOCKED" and c.legal_issuer_id is None
    assert c.reasons == tuple(sorted(("ISSUER_IDENTITY_UNAVAILABLE","LIQUIDITY_SCORE_BELOW_MINIMUM","LIQUIDITY_CAPACITY_UNAVAILABLE","CANDIDATE_DURATION_LIMIT_EXCEEDED")))


def test_nonready_priority_and_no_score_gate():
    result = RiskCandidateReducer.build(missing_issuer(investment(1)))
    assert result.candidates[0].status == "INVESTMENT_MODEL_NOT_READY"
    assert result.candidates[0].reasons == ("INVESTMENT_MODEL_NOT_READY","ISSUER_IDENTITY_UNAVAILABLE")
    source = investment()
    e = source.evaluations[0].model_copy(update={"investment_score_v1":D("0")})
    source = source.model_copy(update={"evaluations":(e,)+source.evaluations[1:],"score_min":D(0)})
    assert RiskCandidateReducer.build(source).candidates[0].status == "ELIGIBLE"


@pytest.mark.parametrize("case", ["version","pit","count","rank","score_none","score_range","identity","raw_drift","sensitivity","source_version","issuer_contradiction"])
def test_invalid_evidence_raises(case):
    source = investment(); e = source.evaluations[0]
    if case in ("version","pit","count"):
        source = source.model_copy(update={"version":{"contract_version":"bad"},"pit":{"pit_ready":True},"count":{"candidate_count":2}}[case])
    else:
        updates = {"rank":{"rank":99},"score_none":{"investment_score_v1":None},"score_range":{"investment_score_v1":D(101)},
            "identity":{"bond_id":99},"raw_drift":{"modified_duration_years":D(4)}}
        if case in updates: e = e.model_copy(update=updates[case])
        else:
            c = e.candidate
            if case == "sensitivity": c = c.model_copy(update={"features":c.features.model_copy(update={"relative_price_sensitivity_per_1bp":D("Infinity")})})
            elif case == "source_version": c = c.model_copy(update={"contract_version":"bad"})
            else: c = c.model_copy(update={"m3":c.m3.model_copy(update={"credit":c.m3.credit.model_copy(update={"issuer_link_status":"MAPPING_MISSING"})})})
            e = e.model_copy(update={"candidate":c})
        source = source.model_copy(update={"evaluations":(e,)+source.evaluations[1:]})
    with pytest.raises(ValueError): RiskCandidateReducer.build(source)


def test_empty_context_and_decimal_isolation():
    source = investment()
    expected = RiskCandidateReducer.build(source).model_dump()
    with localcontext() as ctx:
        ctx.prec=3; ctx.rounding=ROUND_UP
        assert RiskCandidateReducer.build(source).model_dump() == expected
        assert ctx.prec == 3 and ctx.rounding == ROUND_UP


def test_static_pure_and_legacy_boundary():
    forbidden = ("sqlalchemy","app.models","httpx","requests","os","pathlib","legacy","portfolio_construction","bond_risk_assessment","paper_trading","ml_prediction")
    for filename in ("risk_candidate_reducer.py","portfolio_risk_evaluator.py"):
        tree = ast.parse((Path(__file__).parents[1]/"app/services"/filename).read_text())
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(token in name for name in imports for token in forbidden)
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {"execute","commit","flush","request","sync","rollback"} for n in ast.walk(tree))
