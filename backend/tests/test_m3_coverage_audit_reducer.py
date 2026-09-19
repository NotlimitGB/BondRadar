"""Task283 pure M3 coverage audit contract acceptance."""

import ast
import importlib.util
import inspect
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, getcontext, setcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.m3_coverage_audit import M3FeatureCoverage
from app.services.m3_coverage_audit_reducer import M3CoverageAuditReducer


def _task282_helpers():
    path = Path(__file__).with_name("test_bond_m3_feature_composer.py")
    spec = importlib.util.spec_from_file_location("task282_test_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _task282_helpers()
DAY = date(2026, 9, 19)


def _composite(bond_id=1, *, availability=None, status=None, peer=None):
    views = H._views()
    if peer is not None:
        views["peer_distribution"] = peer
    result = H._compose(views)
    if availability:
        available = result.availability.model_copy(update=availability)
        result = result.model_copy(update={"availability": available})
    if status is not None:
        result = result.model_copy(update={"status": status})
    return result.model_copy(update={"bond_id": bond_id})


def _missing(composite, *names):
    updates = {name: False for name in names}
    if "all_supplied_evidence_consistent" in updates:
        composite = composite.model_copy(update={"status": "EVIDENCE_INVALID"})
    return composite.model_copy(
        update={"availability": composite.availability.model_copy(update=updates)}
    )


def test_a_b_one_and_multiple_composites_are_accepted_and_sorted():
    one = M3CoverageAuditReducer.build([_composite(1)])
    assert one.universe_size == 1 and one.bond_ids == [1]
    many = M3CoverageAuditReducer.build(
        (_composite(3), _composite(1), _composite(2))
    )
    assert many.bond_ids == [1, 2, 3]


def test_c_input_order_does_not_change_serialized_output():
    inputs = [_composite(3), _composite(1), _composite(2)]
    forward = M3CoverageAuditReducer.build(inputs)
    reverse = M3CoverageAuditReducer.build(list(reversed(inputs)))
    assert forward.model_dump(mode="json") == reverse.model_dump(mode="json")


@pytest.mark.parametrize(
    "value",
    [[], "x", b"x", bytearray(b"x"), {"x": 1}, {1}, iter(())],
)
def test_d_e_invalid_sequence_shapes_fail(value):
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build(value)


def test_f_i_wrong_item_duplicate_and_mixed_context_fail():
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([object()])
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([_composite(1), _composite(1)])
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build(
            [_composite(1), _composite(2).model_copy(update={"as_of_date": DAY + timedelta(days=1)})]
        )
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build(
            [_composite(1), _composite(2).model_copy(update={"market_source": "manual"})]
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"contract_version": "future"},
        {"pit_ready": True},
        {"bond_id": True},
        {"bond_id": 0},
        {"as_of_date": datetime(2026, 9, 19)},
        {"market_source": " "},
        {"status": "UNKNOWN"},
    ],
)
def test_j_k_outer_contract_drift_fails(patch):
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([_composite().model_copy(update=patch)])


def test_l_self_contradictory_envelopes_fail():
    composite = _composite().model_copy(
        update={
            "availability": _composite().availability.model_copy(
                update={"all_supplied_evidence_consistent": False}
            )
        }
    )
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([composite])
    composite = _composite().model_copy(
        update={
            "availability": _composite().availability.model_copy(
                update={"market_fresh": 1}
            )
        }
    )
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([composite])
    composite = _composite().model_copy(
        update={
            "availability": _composite().availability.model_copy(
                update={"peer_distribution_ready": True}
            )
        }
    )
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([composite])
    composite = _composite().model_copy(
        update={
            "availability": _composite().availability.model_copy(
                update={"has_peer_distribution_context": True}
            )
        }
    )
    with pytest.raises(ValueError):
        M3CoverageAuditReducer.build([composite])


def test_m_n_all_and_no_feature_coverage():
    complete = M3CoverageAuditReducer.build([_composite(1), _composite(2)])
    assert complete.feature_coverage.market_fresh.coverage_pct == Decimal("100")
    assert complete.bottlenecks == []
    inputs = [
        _missing(_composite(1), "liquidity_score_ready"),
        _missing(_composite(2), "liquidity_score_ready"),
    ]
    audit = M3CoverageAuditReducer.build(inputs)
    coverage = audit.feature_coverage.liquidity_score_ready
    assert coverage.available_count == 0
    assert coverage.missing_count == 2
    assert coverage.coverage_pct == Decimal("0")
    assert coverage.missing_bond_ids == [1, 2]


def test_o_u_v_mixed_universe_counts_ids_and_exact_percentages():
    inputs = [
        _composite(3),
        _missing(_composite(1), "market_fresh"),
        _missing(_composite(2), "market_fresh"),
    ]
    audit = M3CoverageAuditReducer.build(inputs)
    coverage = audit.feature_coverage.market_fresh
    assert coverage.available_bond_ids == [3]
    assert coverage.missing_bond_ids == [1, 2]
    assert coverage.coverage_pct == Decimal(1) * Decimal(100) / Decimal(3)
    assert coverage.missing_count == 2
    assert next(b for b in audit.bottlenecks if b.key == "MARKET_NOT_FRESH").missing_pct == (
        Decimal(2) * Decimal(100) / Decimal(3)
    )


def test_q_invalid_composite_is_counted_in_the_common_denominator():
    invalid = _missing(_composite(2), "all_supplied_evidence_consistent")
    audit = M3CoverageAuditReducer.build([_composite(1), invalid])
    assert audit.universe_size == 2
    assert audit.consistent_composite_count == 1
    assert audit.invalid_composite_count == 1
    assert audit.invalid_composite_pct == Decimal("50")
    assert audit.core_m3_complete_bond_ids == [1]


def test_p_zero_financial_values_do_not_change_authoritative_availability():
    composite = _composite()
    composite = composite.model_copy(update={
        "market": composite.market.model_copy(update={"price": Decimal("0")}),
        "relative_value": composite.relative_value.model_copy(
            update={"spread_to_ofz_pp": Decimal("0"), "spread_to_ofz_bps": Decimal("0")}
        ),
        "liquidity": composite.liquidity.model_copy(
            update={"liquidity_score_v1": Decimal("0")}
        ),
        "modified_duration": composite.modified_duration.model_copy(
            update={"modified_duration_years": Decimal("0")}
        ),
        "dv01": composite.dv01.model_copy(
            update={"dv01_currency_per_bond": Decimal("0")}
        ),
        "liquidity_relative_value": composite.liquidity_relative_value.model_copy(
            update={"spread_to_ofz_bps": Decimal("0"), "liquidity_score_v1": Decimal("0")}
        ),
    })
    audit = M3CoverageAuditReducer.build([composite])
    assert audit.core_m3_complete_count == 1
    assert all(
        getattr(audit.feature_coverage, name).available_count
        == int(getattr(composite.availability, name))
        for name in type(composite.availability).model_fields
    )


def test_r_t_peer_context_is_optional_for_core_and_required_for_extended():
    no_peer = M3CoverageAuditReducer.build([_composite(1)])
    assert no_peer.core_m3_complete_bond_ids == [1]
    assert no_peer.extended_m3_complete_count == 0
    assert no_peer.peer_context_count == 0
    assert no_peer.peer_distribution_ready_pct == Decimal("0")
    assert no_peer.status_breakdowns.peer_distribution == []

    ready = M3CoverageAuditReducer.build([_composite(1, peer=H._peer())])
    assert ready.extended_m3_complete_count == 1
    assert ready.peer_context_count == ready.peer_distribution_ready_count == 1

    unavailable = M3CoverageAuditReducer.build(
        [_composite(1, peer=H._peer(status="INSUFFICIENT_PEERS"))]
    )
    assert unavailable.core_m3_complete_bond_ids == [1]
    assert unavailable.extended_m3_complete_count == 0


def test_y_decimal_context_is_not_changed():
    original = getcontext().copy()
    changed = original.copy()
    changed.prec = 7
    changed.rounding = ROUND_DOWN
    setcontext(changed)
    try:
        M3CoverageAuditReducer.build([_composite(1), _composite(2), _composite(3)])
        assert getcontext().prec == 7 and getcontext().rounding == ROUND_DOWN
    finally:
        setcontext(original)


def test_z_ad_bottlenecks_are_independent_sorted_and_omit_zero_counts():
    inputs = [
        _missing(_composite(3), "market_fresh", "dv01_ready"),
        _missing(_composite(1), "market_fresh", "liquidity_score_ready"),
        _missing(_composite(2), "dv01_ready"),
    ]
    audit = M3CoverageAuditReducer.build(inputs)
    assert [(row.key, row.missing_count) for row in audit.bottlenecks] == [
        ("DV01_NOT_READY", 2),
        ("MARKET_NOT_FRESH", 2),
        ("LIQUIDITY_SCORE_NOT_READY", 1),
    ]
    assert audit.bottlenecks[0].affected_bond_ids == [2, 3]


def test_ae_al_status_breakdowns_preserve_statuses_and_peer_denominator():
    stale_market = _composite(3).model_copy(
        update={"market": H._market(market_status="STALE")}
    )
    peer_ready = _composite(1, peer=H._peer())
    peer_unavailable = _composite(
        2, peer=H._peer(status="INSUFFICIENT_PEERS")
    ).model_copy(update={"market": H._market(market_status="MISSING")})
    audit = M3CoverageAuditReducer.build([stale_market, peer_unavailable, peer_ready])
    assert [entry.status for entry in audit.status_breakdowns.market] == [
        "FRESH", "MISSING", "STALE"
    ]
    peer = audit.status_breakdowns.peer_distribution
    assert audit.peer_distribution_ready_pct == Decimal("50")
    assert (
        audit.feature_coverage.peer_distribution_ready.coverage_pct
        == Decimal(1) * Decimal(100) / Decimal(3)
    )
    assert [(entry.status, entry.count, entry.pct) for entry in peer] == [
        ("INSUFFICIENT_PEERS", 1, Decimal("50")),
        ("READY", 1, Decimal("50")),
    ]


def test_af_aj_all_required_status_families_are_preserved_verbatim():
    composite = _composite().model_copy(update={
        "relative_value": H._relative(status="TARGET_MARKET_STALE"),
        "liquidity": H._liquidity(score_status="NO_MARKET_DATA"),
        "modified_duration": H._duration(status="MARKET_DATA_STALE"),
        "dv01": H._dv01(status="NKD_MISSING"),
        "liquidity_relative_value": H._liquidity_relative(
            status="LIQUIDITY_UNAVAILABLE"
        ),
    })
    breakdowns = M3CoverageAuditReducer.build([composite]).status_breakdowns
    assert breakdowns.relative_value[0].status == "TARGET_MARKET_STALE"
    assert breakdowns.liquidity[0].status == "NO_MARKET_DATA"
    assert breakdowns.modified_duration[0].status == "MARKET_DATA_STALE"
    assert breakdowns.dv01[0].status == "NKD_MISSING"
    assert breakdowns.liquidity_relative_value[0].status == "LIQUIDITY_UNAVAILABLE"


def test_am_aq_core_and_extended_require_every_declared_flag():
    names = (
        "market_fresh", "relative_value_ready", "has_credit_rating_evidence",
        "has_credit_comparable_cohort", "liquidity_score_ready",
        "modified_duration_ready", "dv01_ready", "liquidity_relative_value_ready",
    )
    for name in names:
        audit = M3CoverageAuditReducer.build([_missing(_composite(), name)])
        assert audit.core_m3_complete_count == 0
    invalid = _missing(_composite(), "all_supplied_evidence_consistent")
    assert M3CoverageAuditReducer.build([invalid]).core_m3_complete_count == 0


def test_au_av_input_is_unchanged_and_serialization_is_stable():
    inputs = [_composite(2), _composite(1)]
    before = [item.model_dump(mode="json") for item in inputs]
    first = M3CoverageAuditReducer.build(inputs)
    second = M3CoverageAuditReducer.build(inputs)
    assert [item.model_dump(mode="json") for item in inputs] == before
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_aw_schema_is_frozen_extra_forbid_and_pit_is_false():
    audit = M3CoverageAuditReducer.build([_composite()])
    assert audit.pit_ready is False
    with pytest.raises(ValidationError):
        M3FeatureCoverage(
            available_count=1, missing_count=0, coverage_pct=Decimal("100"),
            available_bond_ids=[1], missing_bond_ids=[], extra_field=True,
        )
    with pytest.raises(ValidationError):
        audit.universe_size = 2
    assert audit.capabilities.model_dump() == {
        "task282_composite_input_ready": True,
        "m3_coverage_audit_ready": True,
        "feature_coverage_ready": True,
        "feature_status_breakdown_ready": True,
        "missingness_bottlenecks_ready": True,
        "core_m3_completeness_ready": True,
        "extended_m3_completeness_ready": True,
        "actual_db_audit_runner_ready": False,
        "automatic_universe_discovery_ready": False,
        "m4_readiness_decision_ready": False,
        "investment_score_ready": False,
        "investment_ranking_ready": False,
        "recommendation_ready": False,
        "pit_ready": False,
    }


def test_ar_at_ax_az_static_pure_boundary_and_no_financial_or_m4_logic():
    import app.services.m3_coverage_audit_reducer as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("sqlalchemy") for name in imports)
    assert not any(name.startswith("app.models") for name in imports)
    assert not any(name.startswith("app.services") for name in imports)
    forbidden_calls = {
        "build_for_bond", "build_for_bonds", "build_curve", "evaluate_bond",
        "execute", "query",
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls.intersection(forbidden_calls)
    lower = source.lower()
    for forbidden in (
        "yield_to_maturity", "spread_to_ofz", "clean_price",
        "dv01_currency_per_bond", "liquidity_score_v1", "ready_for_m4",
        "buy", "sell",
    ):
        assert forbidden not in lower
