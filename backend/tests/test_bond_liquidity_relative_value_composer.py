"""Pure Task284 composer acceptance and safety tests."""

import ast
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path

import pytest

from app.schemas.bond_liquidity_features import (
    BondLiquidityFeatureView,
    BondLiquidityScoreComponents,
)
from app.schemas.ofz_reference_curve import BondRelativeValueView
from app.services.bond_liquidity_relative_value_composer import (
    compose_bond_liquidity_aware_relative_value,
)

DAY = date(2026, 9, 18)
D = Decimal


def relative_view() -> BondRelativeValueView:
    return BondRelativeValueView(
        bond_id=1,
        isin="RU000A123474",
        secid="TASK284",
        as_of_date=DAY,
        market_source="moex",
        status="READY",
        target_yield_to_maturity_pct=D("12.25"),
        target_duration_years=D("2"),
        reference_ofz_yield_pct=D("11"),
        spread_to_ofz_pp=D("1.25"),
        spread_to_ofz_bps=D("125"),
        interpolation_method="LINEAR_INTERPOLATION",
        curve=dict(
            as_of_date=DAY,
            market_source="moex",
            status="READY",
            curve_trade_date=DAY,
            node_count=2,
            min_duration_years=D("1"),
            max_duration_years=D("3"),
            diagnostics={},
            nodes=[
                dict(
                    duration_years=D("1"),
                    yield_to_maturity_pct=D("10"),
                    aggregation_method="SINGLE",
                    component_bond_ids=[2],
                    component_snapshot_ids=[20],
                    component_secids=["OFZ1"],
                    component_isins=[None],
                    component_yields_pct=[D("10")],
                ),
                dict(
                    duration_years=D("3"),
                    yield_to_maturity_pct=D("12"),
                    aggregation_method="SINGLE",
                    component_bond_ids=[3],
                    component_snapshot_ids=[30],
                    component_secids=["OFZ2"],
                    component_isins=[None],
                    component_yields_pct=[D("12")],
                ),
            ],
        ),
        provenance=dict(
            target_market_snapshot_id=5,
            target_market_trade_date=DAY,
            curve_trade_date=DAY,
        ),
        quality_flags=[],
    )


def liquidity_view() -> BondLiquidityFeatureView:
    dates = [DAY - timedelta(days=offset) for offset in range(4, -1, -1)]
    return BondLiquidityFeatureView(
        bond_id=1,
        isin="RU000A123474",
        secid="TASK284",
        as_of_date=DAY,
        market_source="moex",
        window_start_date=DAY - timedelta(days=29),
        lookback_calendar_days=30,
        min_observation_days=5,
        calendar_window_days=30,
        snapshot_observation_days=5,
        trade_volume_observation_days=5,
        turnover_value_observation_days=5,
        num_trades_observation_days=5,
        latest_trade_date=DAY,
        latest_observation_age_days=0,
        median_daily_turnover_value=D("12345.67890123456789012345678"),
        mean_daily_turnover_value=D("12345"),
        total_turnover_value=D("61725"),
        median_daily_trade_volume=D("12.34567890123456789012345678"),
        mean_daily_trade_volume=D("12"),
        total_trade_volume=D("60"),
        median_daily_num_trades=D("12.5"),
        mean_daily_num_trades=D("12.5"),
        total_num_trades=62,
        days_with_positive_turnover=5,
        days_with_positive_trade_volume=5,
        days_with_positive_num_trades=5,
        positive_turnover_share_of_turnover_observations=D("1"),
        positive_trade_count_share_of_trade_count_observations=D("1"),
        score_status="READY",
        liquidity_score_v1=D("83.12345678912345678912345678"),
        score_components=dict(
            turnover_percentile=D("72.12345678912345678912345678"),
            trade_count_percentile=D("85.23456789123456789123456789"),
            recency_percentile=D("100"),
        ),
        availability=dict(
            has_market_snapshots=True,
            has_trade_volume=True,
            has_turnover_value=True,
            has_num_trades=True,
            has_sufficient_observation_days=True,
            has_score_components=True,
            has_liquidity_score=True,
        ),
        provenance=dict(
            market_source="moex",
            window_start_date=DAY - timedelta(days=29),
            as_of_date=DAY,
            lookback_calendar_days=30,
            min_observation_days=5,
            selected_market_snapshot_ids=[1, 2, 3, 4, 5],
            selected_trade_dates=dates,
            universe_candidate_count=12,
            score_eligible_universe_count=10,
        ),
        quality_flags=[],
    )


def compose(relative=None, liquidity=None, **kwargs):
    return compose_bond_liquidity_aware_relative_value(
        relative if relative is not None else relative_view(),
        liquidity if liquidity is not None else liquidity_view(),
        bond_id=kwargs.pop("bond_id", 1),
        as_of_date=kwargs.pop("as_of_date", DAY),
        market_source=kwargs.pop("market_source", "moex"),
        **kwargs,
    )


@pytest.mark.parametrize(
    "pp,bps,score",
    [
        ("1.25", "125", "50"),
        ("0", "0", "0"),
        ("-0.25", "-25", "100"),
    ],
)
def test_ready_preserves_spreads_score_extrema_and_full_contract(pp, bps, score):
    relative = relative_view().model_copy(
        update={"spread_to_ofz_pp": D(pp), "spread_to_ofz_bps": D(bps)}
    )
    liquidity = liquidity_view().model_copy(update={"liquidity_score_v1": D(score)})
    result = compose(relative, liquidity)
    assert result.status == "READY"
    assert result.spread_to_ofz_pp == D(pp)
    assert result.spread_to_ofz_bps == D(bps)
    assert result.liquidity_score_v1 == D(score)
    assert result.availability.model_dump() == {
        "has_relative_value": True,
        "has_spread_to_ofz": True,
        "has_liquidity_feature": True,
        "has_liquidity_score": True,
        "has_matching_market_evidence": True,
        "has_liquidity_aware_relative_value": True,
    }


def test_relative_unavailable_preserves_liquidity_side():
    relative = relative_view().model_copy(
        update={
            "status": "CURVE_NOT_READY",
            "reference_ofz_yield_pct": None,
            "spread_to_ofz_pp": None,
            "spread_to_ofz_bps": None,
            "interpolation_method": None,
        }
    )
    result = compose(relative, liquidity_view())
    assert result.status == "RELATIVE_VALUE_UNAVAILABLE"
    assert result.liquidity_score_v1 == liquidity_view().liquidity_score_v1
    assert result.availability.has_liquidity_score
    assert "RELATIVE_VALUE_CURVE_NOT_READY" in result.quality_flags


def test_liquidity_unavailable_preserves_relative_side():
    liquidity = liquidity_view().model_copy(
        update={
            "score_status": "INSUFFICIENT_UNIVERSE",
            "liquidity_score_v1": None,
            "score_components": BondLiquidityScoreComponents(),
        }
    )
    result = compose(relative_view(), liquidity)
    assert result.status == "LIQUIDITY_UNAVAILABLE"
    assert result.spread_to_ofz_bps == D("125")
    assert result.availability.has_spread_to_ofz
    assert "LIQUIDITY_INSUFFICIENT_UNIVERSE" in result.quality_flags


def test_both_unavailable_preserves_precedence_and_flags():
    relative = relative_view().model_copy(
        update={"status": "CURVE_NOT_READY", "spread_to_ofz_pp": None, "spread_to_ofz_bps": None}
    )
    liquidity = liquidity_view().model_copy(
        update={"score_status": "NO_MARKET_DATA", "liquidity_score_v1": None,
                "score_components": BondLiquidityScoreComponents()}
    )
    result = compose(relative, liquidity)
    assert result.status == "RELATIVE_VALUE_UNAVAILABLE"
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert {"RELATIVE_VALUE_UNAVAILABLE", "LIQUIDITY_UNAVAILABLE"} <= set(result.quality_flags)


@pytest.mark.parametrize("side", ["relative", "liquidity"])
def test_identity_mismatch_hides_only_wrong_side(side):
    relative, liquidity = relative_view(), liquidity_view()
    if side == "relative":
        relative = relative.model_copy(update={"bond_id": 2})
    else:
        liquidity = liquidity.model_copy(update={"bond_id": 2})
    result = compose(relative, liquidity)
    assert result.status == "MARKET_EVIDENCE_MISMATCH"
    assert not result.availability.has_matching_market_evidence
    if side == "relative":
        assert result.spread_to_ofz_bps is None and result.liquidity_score_v1 is not None
    else:
        assert result.spread_to_ofz_bps is not None and result.liquidity_score_v1 is None


def test_both_wrong_but_equal_identities_never_become_authoritative():
    relative = relative_view().model_copy(update={"bond_id": 2})
    liquidity = liquidity_view().model_copy(update={"bond_id": 2})
    result = compose(relative, liquidity)
    assert result.status == "MARKET_EVIDENCE_MISMATCH"
    assert result.isin is None and result.secid is None
    assert result.spread_to_ofz_bps is None and result.liquidity_score_v1 is None


def test_snapshot_mismatch_retains_both_valid_sides():
    provenance = relative_view().provenance.model_copy(update={"target_market_snapshot_id": 99})
    relative = relative_view().model_copy(update={"provenance": provenance})
    result = compose(relative, liquidity_view())
    assert result.status == "MARKET_EVIDENCE_MISMATCH"
    assert result.spread_to_ofz_bps == D("125")
    assert result.liquidity_score_v1 == liquidity_view().liquidity_score_v1


@pytest.mark.parametrize(
    "updates",
    [
        {"selected_market_snapshot_ids": [1, 2]},
        {"selected_trade_dates": [DAY, DAY]},
        {"selected_market_snapshot_ids": [1, True, 3, 4, 5]},
        {"selected_trade_dates": [DAY, "bad", DAY, DAY, DAY]},
    ],
)
def test_malformed_liquidity_provenance_is_not_repaired(updates):
    liquidity = liquidity_view()
    provenance = liquidity.provenance.model_copy(update=updates)
    result = compose(relative_view(), liquidity.model_copy(update={"provenance": provenance}))
    assert result.status == "LIQUIDITY_PROVENANCE_INVALID"
    assert not result.provenance.liquidity_provenance_valid
    assert result.provenance.liquidity_latest_market_snapshot_id is None


def test_empty_no_market_evidence_is_valid_unavailable_provenance():
    liquidity = liquidity_view()
    provenance = liquidity.provenance.model_copy(
        update={"selected_market_snapshot_ids": [], "selected_trade_dates": []}
    )
    liquidity = liquidity.model_copy(
        update={
            "score_status": "NO_MARKET_DATA",
            "snapshot_observation_days": 0,
            "latest_trade_date": None,
            "latest_observation_age_days": None,
            "liquidity_score_v1": None,
            "score_components": BondLiquidityScoreComponents(),
            "provenance": provenance,
        }
    )
    result = compose(relative_view(), liquidity)
    assert result.status == "LIQUIDITY_UNAVAILABLE"
    assert result.provenance.liquidity_provenance_valid
    assert "LIQUIDITY_PROVENANCE_INVALID" not in result.quality_flags


@pytest.mark.parametrize("field,value", [("spread_to_ofz_pp", D("NaN")),
                                           ("spread_to_ofz_bps", D("Infinity"))])
def test_nonfinite_ready_spread_is_unavailable(field, value):
    result = compose(relative_view().model_copy(update={field: value}), liquidity_view())
    assert result.status == "RELATIVE_VALUE_UNAVAILABLE"
    assert "RELATIVE_VALUE_EVIDENCE_INVALID" in result.quality_flags


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", D("NaN")),
        ("score", D("101")),
        ("turnover_percentile", D("-1")),
        ("recency_percentile", D("Infinity")),
    ],
)
def test_invalid_ready_liquidity_numbers_are_unavailable(field, value):
    liquidity = liquidity_view()
    if field == "score":
        liquidity = liquidity.model_copy(update={"liquidity_score_v1": value})
    else:
        components = liquidity.score_components.model_copy(update={field: value})
        liquidity = liquidity.model_copy(update={"score_components": components})
    result = compose(relative_view(), liquidity)
    assert result.status == "LIQUIDITY_UNAVAILABLE"
    assert "LIQUIDITY_EVIDENCE_INVALID" in result.quality_flags


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bond_id": True},
        {"bond_id": 0},
        {"bond_id": "1"},
        {"as_of_date": datetime(2026, 9, 18)},
        {"as_of_date": "2026-09-18"},
        {"market_source": ""},
        {"market_source": " "},
        {"market_source": 1},
    ],
)
def test_invalid_direct_arguments(kwargs):
    with pytest.raises(ValueError):
        compose(**kwargs)


@pytest.mark.parametrize("relative,liquidity", [(object(), liquidity_view()),
                                                  (relative_view(), object())])
def test_wrong_dependency_types_raise_value_error(relative, liquidity):
    with pytest.raises(ValueError):
        compose_bond_liquidity_aware_relative_value(
            relative, liquidity, bond_id=1, as_of_date=DAY, market_source="moex"
        )


def test_context_inputs_and_stable_serialization_are_unchanged():
    relative, liquidity = relative_view(), liquidity_view()
    before = (relative.model_dump(), liquidity.model_dump())
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)) as context:
        first = compose(relative, liquidity)
        second = compose(relative, liquidity)
        assert context.prec == 3 and context.rounding == ROUND_DOWN
    assert first.model_dump_json() == second.model_dump_json()
    assert (relative.model_dump(), liquidity.model_dump()) == before


def test_ast_pure_boundary_and_no_financial_formula_duplication():
    path = Path(__file__).parents[1] / "app/services/bond_liquidity_relative_value_composer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = " ".join(ast.unparse(node) for node in ast.walk(tree)
                       if isinstance(node, (ast.Import, ast.ImportFrom)))
    assert "app.schemas" in imports
    assert not any(token in imports for token in (
        "sqlalchemy", "app.models", "app.services", "requests", "httpx", "urllib"
    ))
    forbidden_calls = {"evaluate_bond", "build_for_bond", "execute", "query", "add", "add_all",
                       "flush", "commit", "delete", "merge", "open"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            if name == "add":
                assert isinstance(node.func, ast.Attribute)
                assert ast.unparse(node.func.value) == "flags"
            else:
                assert name not in forbidden_calls
        if isinstance(node, ast.BinOp):
            assert isinstance(node.op, ast.BitOr)
    text = path.read_text(encoding="utf-8")
    assert not any(term in text for term in (
        "0.50", "0.35", "0.15", "0.0001", "liquidity_premium",
        "transaction_cost", "slippage", "market_impact",
    ))
