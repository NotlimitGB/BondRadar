"""Pure Task278 target-against-prebuilt-curve contract tests."""

import ast
import inspect
import itertools
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, Inexact, ROUND_DOWN, ROUND_HALF_EVEN, localcontext

import pytest

from app.schemas.bond_market_features import BondMarketFeatureView
from app.schemas.ofz_reference_curve import OfzCurveNode, OfzReferenceCurveView
from app.services.ofz_relative_value_evaluator import evaluate_market_against_ofz_curve

D = Decimal
DAY = date(2026, 9, 18)


def market(**updates):
    data = dict(
        bond_id=100, isin="RU000TARGET", secid="TARGET", as_of_date=DAY,
        market_snapshot_id=500, market_trade_date=DAY, market_source="moex",
        market_age_days=0, market_status="FRESH", price=D("99"), clean_price=D("98"),
        nkd=D("1"), yield_to_maturity_pct=D("15"), duration_years=D("3"),
        trade_volume=D("1"), turnover_value=D("2"), num_trades=3, currency="RUB",
        nominal_value=D("1000"), coupon_rate=D("10"), maturity_date=DAY + timedelta(days=365),
        offer_date=None, days_to_maturity=365, days_to_offer=None, is_matured=False,
        is_floating_coupon=False, is_subordinated=False, is_perpetual=False,
        metadata_has_amortization=False, future_cashflow_event_count=0, future_coupon_count=0,
        future_amortization_count=0, next_coupon_date=None, next_amortization_date=None,
        next_redemption_date=None, next_offer_redemption_date=None, has_future_coupon=False,
        has_future_amortization=False, has_future_redemption=False,
        has_future_offer_redemption=False, spread_to_ofz=None,
        availability=dict(
            has_market_snapshot=True, has_price=True, has_clean_price=True, has_nkd=True,
            has_yield_to_maturity=True, has_duration=True, has_trade_volume=True,
            has_turnover_value=True, has_num_trades=True, has_cashflow_schedule=False,
            has_maturity=True, has_offer=False,
        ), quality_flags=[], provenance=dict(
            market_snapshot_id=500, market_source="moex", market_trade_date=DAY,
            selected_cashflow_event_ids=[],
        ),
    )
    return BondMarketFeatureView(**data).model_copy(update=updates)


def node(duration, ytm, snapshot=1, aggregation="SINGLE"):
    return OfzCurveNode(
        duration_years=D(duration), yield_to_maturity_pct=D(ytm),
        aggregation_method=aggregation, component_bond_ids=[snapshot],
        component_snapshot_ids=[snapshot], component_secids=[f"OFZ{snapshot}"],
        component_isins=[f"SU{snapshot:010d}"], component_yields_pct=[D(ytm)],
    )


def curve(nodes=None, **updates):
    nodes = nodes or [node("2", "12", 10), node("4", "14", 20)]
    data = dict(
        as_of_date=DAY, market_source="moex", status="READY", curve_trade_date=DAY,
        node_count=len(nodes), min_duration_years=nodes[0].duration_years,
        max_duration_years=nodes[-1].duration_years, nodes=nodes,
        diagnostics=dict(distinct_duration_node_count=len(nodes)),
    )
    return OfzReferenceCurveView(**data).model_copy(update=updates)


def evaluate(target=None, reference=None, **kwargs):
    return evaluate_market_against_ofz_curve(
        target or market(), reference or curve(),
        as_of_date=kwargs.pop("as_of_date", DAY),
        market_source=kwargs.pop("market_source", "moex"), **kwargs,
    )


def assert_unavailable(result, status):
    assert result.status == status and status in result.quality_flags
    assert result.reference_ofz_yield_pct is None
    assert result.spread_to_ofz_pp is result.spread_to_ofz_bps is None
    assert result.interpolation_method is None
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert result.pit_ready is False


@pytest.mark.parametrize("duration,ytm,reference,method,pp,bps", [
    ("2", "15", "12", "EXACT_NODE", "3", "300"),
    ("4", "14", "14", "EXACT_NODE", "0", "0"),
    ("3", "15", "13", "LINEAR_INTERPOLATION", "2", "200"),
    ("3", "11", "13", "LINEAR_INTERPOLATION", "-2", "-200"),
])
def test_exact_interpolation_and_signed_spreads(duration, ytm, reference, method, pp, bps):
    result = evaluate(market(duration_years=D(duration), yield_to_maturity_pct=D(ytm)))
    assert result.status == "READY" and result.interpolation_method == method
    assert result.reference_ofz_yield_pct == D(reference)
    assert result.spread_to_ofz_pp == D(pp) and result.spread_to_ofz_bps == D(bps)
    assert result.quality_flags == []
    provenance = result.provenance
    if method == "EXACT_NODE":
        assert provenance.lower_curve_duration_years == provenance.upper_curve_duration_years == D(duration)
        assert provenance.lower_component_snapshot_ids == provenance.upper_component_snapshot_ids
    else:
        assert provenance.lower_curve_duration_years == D("2")
        assert provenance.upper_curve_duration_years == D("4")


@pytest.mark.parametrize("updates,status,extra_flag", [
    ({"market_status": "MISSING", "market_snapshot_id": None, "market_trade_date": None},
     "TARGET_MARKET_MISSING", None),
    ({"market_status": "STALE"}, "TARGET_MARKET_STALE", None),
    ({"yield_to_maturity_pct": None}, "TARGET_YIELD_MISSING", None),
    ({"yield_to_maturity_pct": D("NaN")}, "TARGET_YIELD_MISSING", "TARGET_YIELD_INVALID"),
    ({"duration_years": None}, "TARGET_DURATION_MISSING", None),
    ({"duration_years": D("NaN")}, "TARGET_DURATION_MISSING", "TARGET_DURATION_INVALID"),
    ({"duration_years": D("0")}, "TARGET_DURATION_MISSING", "TARGET_DURATION_NONPOSITIVE"),
    ({"duration_years": D("-1")}, "TARGET_DURATION_MISSING", "TARGET_DURATION_NONPOSITIVE"),
])
def test_target_unavailable_semantics(updates, status, extra_flag):
    result = evaluate(market(**updates))
    assert_unavailable(result, status)
    if extra_flag:
        assert extra_flag in result.quality_flags


def test_all_existing_failures_collected_with_original_precedence():
    target = market(market_status="STALE", yield_to_maturity_pct=None, duration_years=D("0"))
    reference = curve(status="NO_ELIGIBLE_OFZ", nodes=[], node_count=0,
                      min_duration_years=None, max_duration_years=None, curve_trade_date=None)
    result = evaluate(target, reference)
    assert_unavailable(result, "TARGET_MARKET_STALE")
    assert set(result.quality_flags) == {
        "TARGET_MARKET_STALE", "TARGET_YIELD_MISSING", "TARGET_DURATION_MISSING",
        "TARGET_DURATION_NONPOSITIVE", "CURVE_NOT_READY", "CURVE_NO_ELIGIBLE_OFZ",
    }


def test_non_ready_curve_and_duplicate_aggregation_flag():
    unavailable = curve(status="INSUFFICIENT_DISTINCT_DURATIONS")
    result = evaluate(reference=unavailable)
    assert_unavailable(result, "CURVE_NOT_READY")
    assert "CURVE_INSUFFICIENT_DISTINCT_DURATIONS" in result.quality_flags
    duplicate = curve([node("2", "12", 10, "MEDIAN"), node("4", "14", 20)])
    assert "CURVE_DUPLICATE_DURATION_AGGREGATED" in evaluate(reference=duplicate).quality_flags


def test_target_curve_trade_date_mismatch_and_no_extrapolation():
    assert_unavailable(evaluate(market(market_trade_date=DAY - timedelta(days=1))),
                       "TARGET_CURVE_DATE_MISMATCH")
    for duration in ("1", "5"):
        assert_unavailable(evaluate(market(duration_years=D(duration))), "TARGET_DURATION_OUTSIDE_CURVE")


@pytest.mark.parametrize("target,reference,as_of,source", [
    (None, curve(), DAY, "moex"), ({}, curve(), DAY, "moex"),
    (market(), None, DAY, "moex"), (market(), {}, DAY, "moex"),
    (market(), curve(), datetime(2026, 9, 18), "moex"),
    (market(), curve(), "2026-09-18", "moex"),
    (market(), curve(), DAY, ""), (market(), curve(), DAY, " "),
    (market(), curve(), DAY, None),
])
def test_invalid_outer_arguments(target, reference, as_of, source):
    with pytest.raises(ValueError):
        evaluate_market_against_ofz_curve(target, reference, as_of_date=as_of, market_source=source)


@pytest.mark.parametrize("updates", [
    {"contract_version": "wrong"}, {"pit_ready": True},
    {"as_of_date": DAY - timedelta(days=1)}, {"as_of_date": datetime(2026, 9, 18)},
    {"market_source": "manual"}, {"market_source": "MOEX"},
    {"market_status": "OTHER"}, {"market_snapshot_id": None},
    {"market_snapshot_id": True}, {"market_snapshot_id": "500"},
    {"market_trade_date": None}, {"market_trade_date": datetime(2026, 9, 18)},
    {"market_trade_date": DAY + timedelta(days=1)},
])
def test_contradictory_target_maps_to_market_missing(updates):
    result = evaluate(market(**updates))
    assert_unavailable(result, "TARGET_MARKET_MISSING")
    assert "TARGET_CONTRACT_INVALID" in result.quality_flags
    assert result.target_yield_to_maturity_pct == D("15")
    assert result.target_duration_years == D("3")


@pytest.mark.parametrize("updates", [
    {"contract_version": "wrong"}, {"pit_ready": True},
    {"as_of_date": DAY - timedelta(days=1)}, {"as_of_date": datetime(2026, 9, 18)},
    {"market_source": "manual"}, {"market_source": "MOEX"},
])
def test_curve_contract_identity_contradictions_fail_closed(updates):
    reference = curve(**updates)
    before = repr(reference)
    result = evaluate(reference=reference)
    assert_unavailable(result, "CURVE_NOT_READY")
    assert "CURVE_INTEGRITY_INVALID" in result.quality_flags
    assert repr(reference) == before and result.curve is reference


@pytest.mark.parametrize("mutate", [
    lambda c: c.model_copy(update={"node_count": 3}),
    lambda c: c.model_copy(update={"node_count": True}),
    lambda c: c.model_copy(update={"nodes": None}),
    lambda c: c.model_copy(update={"nodes": c.nodes[:1], "node_count": 1,
                                   "max_duration_years": c.nodes[0].duration_years}),
    lambda c: c.model_copy(update={"curve_trade_date": None}),
    lambda c: c.model_copy(update={"curve_trade_date": datetime(2026, 9, 18)}),
    lambda c: c.model_copy(update={"curve_trade_date": DAY + timedelta(days=1)}),
    lambda c: c.model_copy(update={"nodes": list(reversed(c.nodes))}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0], c.nodes[0]]}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0].model_copy(update={"duration_years": D("0")}), c.nodes[1]]}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0].model_copy(update={"duration_years": D("NaN")}), c.nodes[1]]}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0].model_copy(update={"duration_years": 2}), c.nodes[1]]}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0].model_copy(update={"yield_to_maturity_pct": D("NaN")}), c.nodes[1]]}),
    lambda c: c.model_copy(update={"nodes": [c.nodes[0].model_copy(update={"yield_to_maturity_pct": 12}), c.nodes[1]]}),
    lambda c: c.model_copy(update={"min_duration_years": D("1")}),
    lambda c: c.model_copy(update={"max_duration_years": D("5")}),
    lambda c: c.model_copy(update={"min_duration_years": "2"}),
])
def test_ready_curve_integrity_fail_closed_without_repair(mutate):
    reference = mutate(curve())
    before = repr(reference)
    result = evaluate(reference=reference)
    assert_unavailable(result, "CURVE_NOT_READY")
    assert set(("CURVE_NOT_READY", "CURVE_INTEGRITY_INVALID")) <= set(result.quality_flags)
    assert repr(reference) == before


def test_provenance_exact_and_output_serialization_stable():
    nodes = [node("2", "12", 101), node("4", "14", 202)]
    result = evaluate(market(market_snapshot_id=777), curve(nodes))
    assert result.provenance.model_dump() == dict(
        target_market_snapshot_id=777, target_market_trade_date=DAY, curve_trade_date=DAY,
        lower_curve_duration_years=D("2"), lower_curve_yield_pct=D("12"),
        lower_component_snapshot_ids=[101], upper_curve_duration_years=D("4"),
        upper_curve_yield_pct=D("14"), upper_component_snapshot_ids=[202],
    )
    assert type(result).model_validate_json(result.model_dump_json()) == result
    unavailable = evaluate(reference=curve(node_count=3))
    assert type(result).model_validate_json(unavailable.model_dump_json()) == unavailable


def test_input_immutability_and_result_order_independence_for_valid_curve():
    target, reference = market(), curve()
    before = target.model_dump_json(), reference.model_dump_json()
    first = evaluate(target, reference)
    second = evaluate(target, reference)
    assert first.model_dump_json() == second.model_dump_json()
    assert before == (target.model_dump_json(), reference.model_dump_json())
    first.provenance.lower_component_snapshot_ids.append(999)
    assert reference.nodes[0].component_snapshot_ids == [10]


def test_fresh_decimal_context_and_no_caller_context_mutation():
    reference = curve([node("1", "12"), node("4", "13")])
    target = market(duration_years=D("2"), yield_to_maturity_pct=D("15"))
    expected = evaluate(target, reference)
    assert expected.reference_ofz_yield_pct == D("12.33333333333333333333333333")
    with localcontext(Context(prec=3, rounding=ROUND_DOWN, Emax=9, Emin=-9)) as caller:
        caller.traps[Inexact] = True
        caller.flags[Inexact] = True
        before = caller.copy()
        actual = evaluate(target, reference)
        assert actual.model_dump_json() == expected.model_dump_json()
        assert caller.prec == before.prec and caller.rounding == before.rounding
        assert caller.Emax == before.Emax and caller.Emin == before.Emin
        assert caller.traps == before.traps and caller.flags == before.flags


def test_ast_pure_boundary_and_only_supported_financial_formulas():
    import app.services.ofz_relative_value_evaluator as module
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports <= {"datetime", "decimal", "app.schemas.bond_market_features",
                       "app.schemas.ofz_reference_curve"}
    assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
    forbidden_calls = {"execute", "query", "select", "commit", "flush", "add", "add_all",
                       "delete", "update", "insert", "urlopen", "get", "post", "float",
                       "build_for_bond", "build_curve"}
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
        if name == "add" and isinstance(call.func, ast.Attribute):
            assert isinstance(call.func.value, ast.Name) and call.func.value.id == "flags"
            continue
        assert name not in forbidden_calls
    assert not any(token in source for token in (
        "sqlalchemy", "Session", "app.models", "app.services", "requests", "httpx", "urllib",
        "BondMarketFeatureService", "BondMarketSnapshot", "BondSecurityMasterProfile",
    ))
    forbidden_labels = {"CHEAP", "EXPENSIVE", "BUY", "SELL", "ATTRACTIVE", "UNATTRACTIVE"}
    assert not forbidden_labels & {n.value.upper() for n in ast.walk(tree)
                                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
