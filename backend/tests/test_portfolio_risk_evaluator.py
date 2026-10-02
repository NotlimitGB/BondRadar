"""Policy breaches, exact capital-relative measures and unchanged proposals."""

from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_UP

import pytest

from app.schemas.risk_engine import ProposedRiskPortfolio, PortfolioRiskEvaluationView
from app.services.portfolio_risk_evaluator import PortfolioRiskEvaluator
from app.services.risk_candidate_reducer import RiskCandidateReducer
from test_risk_candidate_reducer import risk, investment, missing_issuer

D = Decimal


def proposal(positions=((1,".20"),),capital="100000"):
    return ProposedRiskPortfolio(capital_rub=D(capital),positions=tuple(dict(bond_id=i,target_weight=D(w)) for i,w in positions))


def test_capacity_position_gate_and_unknown_capital_envelope():
    batch = risk()
    result = PortfolioRiskEvaluator.build(batch,proposal())
    assert result.status == "PASS" and result.reasons == ()
    p = result.positions[0]
    assert p.position_amount_rub == D(20000) and p.liquidity_capacity_amount_rub == D(50000)
    assert p.liquidity_capacity_weight == D(".50") and p.max_admissible_position_weight == D(".20")
    result = PortfolioRiskEvaluator.build(batch,proposal(((1,".10"),),"1000000"))
    assert result.status == "BLOCKED" and result.reasons == ("POSITION_LIQUIDITY_CAPACITY_EXCEEDED",)
    assert result.positions[0].max_admissible_position_weight == D(".05")
    result = PortfolioRiskEvaluator.build(batch,proposal(((1,".2000001"),)))
    assert result.reasons == ("POSITION_WEIGHT_LIMIT_EXCEEDED",)
    assert result.proposal.positions[0].target_weight == D(".2000001")


def test_issuer_boundary_negative_headroom_and_exact_ids():
    result = PortfolioRiskEvaluator.build(risk(),proposal(((1,".15"),(2,".10"))))
    assert result.status == "PASS" and result.issuer_concentrations[0].headroom == 0
    result = PortfolioRiskEvaluator.build(risk(),proposal(((1,".15"),(2,".1000001"))))
    assert result.reasons == ("ISSUER_WEIGHT_LIMIT_EXCEEDED",)
    assert result.issuer_concentrations[0].headroom == D("-.0000001")
    result = PortfolioRiskEvaluator.build(risk(overrides={2:{"legal_issuer_id":202}}),proposal(((1,".20"),(2,".20"))))
    assert result.status == "PASS" and [i.legal_issuer_id for i in result.issuer_concentrations] == [201,202]


def test_missing_issuer_known_groups_and_zero_admissible_weight():
    batch = RiskCandidateReducer.build(missing_issuer(investment()))
    result = PortfolioRiskEvaluator.build(batch,proposal(((1,".1"),(2,".1"))))
    assert result.status == "BLOCKED" and result.reasons == ("CANDIDATE_NOT_RISK_ELIGIBLE",)
    assert result.metrics.ungrouped_bond_ids == (1,) and result.metrics.issuer_concentration_complete is False
    assert result.metrics.issuer_count == 1 and result.issuer_concentrations[0].bond_ids == (2,)
    assert result.positions[0].max_admissible_position_weight == 0 and result.positions[0].capacity_bound == D(".2")
    assert result.metrics.invested_weighted_modified_duration_years == D(2)
    assert result.metrics.portfolio_dv01_rub_per_1bp == D(4)
    assert result.quality_flags == ("ISSUER_CONCENTRATION_INCOMPLETE",)


@pytest.mark.parametrize("duration,breach", [("3.5",False),("3.500001",True)])
def test_duration_sleeve_not_diluted_by_cash(duration,breach):
    result = PortfolioRiskEvaluator.build(risk(overrides={1:{"modified_duration_years":D(duration)}}),proposal(((1,".10"),)))
    assert result.metrics.invested_weighted_modified_duration_years == D(duration)
    assert result.metrics.capital_weighted_modified_duration_years == D(duration)*D(".1")
    assert result.metrics.cash_weight == D(".9")
    assert ("PORTFOLIO_DURATION_LIMIT_EXCEEDED" in result.reasons) is breach


def test_exact_duration_and_dv01_aggregation():
    batch = risk(overrides={1:{"modified_duration_years":D(1),"relative_price_sensitivity_per_1bp":D(".0001")},
        2:{"modified_duration_years":D(3),"relative_price_sensitivity_per_1bp":D(".0003")}})
    result = PortfolioRiskEvaluator.build(batch,proposal(((1,".1"),(2,".15"))))
    m = result.metrics
    assert m.capital_weighted_modified_duration_years == D(".55")
    assert m.invested_weighted_modified_duration_years == D("2.2")
    assert m.portfolio_dv01_rub_per_1bp == D("5.5") and m.portfolio_relative_dv01_per_1bp == D(".000055")
    assert m.portfolio_dv01_per_100k_rub == D("5.5") and m.cash_rub == D(75000)


@pytest.mark.parametrize("sensitivity,breach", [(".002",False),(".002000001",True)])
def test_dv01_boundary_independent_of_other_gates(sensitivity,breach):
    result = PortfolioRiskEvaluator.build(risk(overrides={1:{"relative_price_sensitivity_per_1bp":D(sensitivity)}}),proposal())
    assert ("PORTFOLIO_DV01_LIMIT_EXCEEDED" in result.reasons) is breach
    if not breach:
        assert result.metrics.portfolio_relative_dv01_per_1bp == D(".00040")
        assert result.metrics.portfolio_dv01_per_100k_rub == D(40)


def test_simultaneous_blockers_not_first_failure_only():
    batch = risk(overrides={1:{"modified_duration_years":D(6),"relative_price_sensitivity_per_1bp":D(".002"),"median_daily_turnover_value":D(100)}})
    result = PortfolioRiskEvaluator.build(batch,proposal(((1,".30"),)))
    assert result.reasons == tuple(sorted(("CANDIDATE_NOT_RISK_ELIGIBLE","POSITION_WEIGHT_LIMIT_EXCEEDED",
        "POSITION_LIQUIDITY_CAPACITY_EXCEEDED","ISSUER_WEIGHT_LIMIT_EXCEEDED","PORTFOLIO_DURATION_LIMIT_EXCEEDED","PORTFOLIO_DV01_LIMIT_EXCEEDED")))
    assert result.proposal.positions[0].target_weight == D(".30")


def test_empty_portfolio_and_unallocated_cash():
    result = PortfolioRiskEvaluator.build(risk(),proposal(()))
    assert result.status == "PASS" and result.quality_flags == ("EMPTY_PORTFOLIO",)
    assert result.metrics.invested_weight == result.metrics.portfolio_dv01_rub_per_1bp == 0
    assert result.metrics.invested_weighted_modified_duration_years is None
    assert result.metrics.cash_weight == 1 and result.metrics.issuer_count == 0
    batch = risk(4,overrides={i:{"legal_issuer_id":200+i} for i in range(1,5)})
    result = PortfolioRiskEvaluator.build(batch,proposal(tuple((i,".175") for i in range(1,5))))
    assert result.status == "PASS" and result.metrics.invested_weight == D(".70") and result.metrics.cash_weight == D(".30")


@pytest.mark.parametrize("case", ["capital_zero","capital_float","capital_nan","weight_zero","weight_negative","weight_nan","weight_float","weight_above_one","sum_above_one","duplicate","bool_id","unknown_id","wrong_type","pit"])
def test_invalid_proposal(case):
    p = proposal()
    if case == "wrong_type": p = {}
    elif case == "pit": p = p.model_copy(update={"pit_ready":True})
    elif case.startswith("capital"):
        p = p.model_copy(update={"capital_rub":{"capital_zero":D(0),"capital_float":1.0,"capital_nan":D("NaN")}[case]})
    elif case == "sum_above_one": p = proposal(((1,".6"),(2,".5")))
    elif case == "duplicate": p = proposal(((1,".1"),(1,".1")))
    else:
        row = p.positions[0]
        values = {"weight_zero":D(0),"weight_negative":D(-1),"weight_nan":D("NaN"),"weight_float":.1,"weight_above_one":D("1.1")}
        row = row.model_copy(update={"target_weight":values[case]} if case in values else {"bond_id":True if case == "bool_id" else 999})
        p = p.model_copy(update={"positions":(row,)})
    with pytest.raises(ValueError): PortfolioRiskEvaluator.build(risk(),p)


@pytest.mark.parametrize("field,value", [("eligible_count",99),("pit_ready",True),("contract_version","bad")])
def test_risk_batch_tampering(field,value):
    with pytest.raises(ValueError): PortfolioRiskEvaluator.build(risk().model_copy(update={field:value}),proposal())


def test_tampered_capacity_and_status_rejected():
    batch = risk(); c = batch.candidates[0]
    for updates in ({"liquidity_capacity_amount_rub":D(9999999)},{"status":"BLOCKED"},{"legal_issuer_id":999}):
        changed = batch.model_copy(update={"candidates":(c.model_copy(update=updates),)+batch.candidates[1:]})
        with pytest.raises(ValueError): PortfolioRiskEvaluator.build(changed,proposal())


def test_order_immutability_serialization_context_and_capabilities():
    batch, p = risk(),proposal(((2,".1"),(1,".15")))
    before = deepcopy([batch.model_dump(),p.model_dump()])
    result = PortfolioRiskEvaluator.build(batch,p)
    reverse = p.model_copy(update={"positions":tuple(reversed(p.positions))})
    assert result.model_dump() == PortfolioRiskEvaluator.build(batch,reverse).model_dump()
    assert result.proposal.positions[0].bond_id == 1 and p.positions[0].bond_id == 2
    with localcontext() as ctx:
        ctx.prec=3; ctx.rounding=ROUND_UP
        assert result.model_dump() == PortfolioRiskEvaluator.build(batch,p).model_dump()
        assert ctx.prec == 3 and ctx.rounding == ROUND_UP
    assert [batch.model_dump(),p.model_dump()] == before
    assert PortfolioRiskEvaluationView.model_validate_json(result.model_dump_json()).model_dump() == result.model_dump()
    for obj in (result,result.metrics,result.positions[0],result.proposal,result.issuer_concentrations[0]):
        with pytest.raises(ValueError): setattr(obj,next(iter(type(obj).model_fields)),None)
        with pytest.raises(ValueError): type(obj)(**obj.model_dump(),extra=1)
    false_prefixes = ("portfolio_construction","portfolio_optimization","recommendation","shadow","lot","transaction","slippage","market_impact","key_rate","convexity","var","expected","default","economic","sector","pit")
    assert all(v is False for k,v in result.capabilities.model_dump().items() if k.startswith(false_prefixes))
