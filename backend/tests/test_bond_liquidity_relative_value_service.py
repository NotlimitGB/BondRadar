"""Task274 A–AD composition, contract-drift and SELECT-only integration acceptance."""

import ast
import inspect
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView, BondLiquidityScoreComponents
from app.schemas.bond_liquidity_relative_value import BondLiquidityAwareRelativeValueView
from app.schemas.ofz_reference_curve import BondRelativeValueView
from app.services.bond_liquidity_feature_service import BondLiquidityFeatureService
from app.services.bond_liquidity_relative_value_composer import (
    compose_bond_liquidity_aware_relative_value,
)
import app.services.bond_liquidity_relative_value_service as service_module
from app.services.bond_liquidity_relative_value_service import BondLiquidityAwareRelativeValueService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService

DAY = date(2026, 9, 18)
D = Decimal


@pytest.fixture
def evidence(monkeypatch):
    dates = [DAY - timedelta(days=offset) for offset in range(4, -1, -1)]
    relative = BondRelativeValueView(
        bond_id=1, isin="RU000A123474", secid="TASK274", as_of_date=DAY, market_source="moex",
        status="READY", target_yield_to_maturity_pct=D("12.25"), target_duration_years=D(2),
        reference_ofz_yield_pct=D(11), spread_to_ofz_pp=D("1.25"), spread_to_ofz_bps=D(125),
        interpolation_method="LINEAR_INTERPOLATION",
        curve=dict(as_of_date=DAY, market_source="moex", status="READY", curve_trade_date=DAY,
                   node_count=2, min_duration_years=D(1), max_duration_years=D(3), diagnostics={}, nodes=[
                       dict(duration_years=D(1), yield_to_maturity_pct=D(10), aggregation_method="SINGLE",
                            component_bond_ids=[2], component_snapshot_ids=[20], component_secids=["OFZ1"],
                            component_isins=[None], component_yields_pct=[D(10)]),
                       dict(duration_years=D(3), yield_to_maturity_pct=D(12), aggregation_method="SINGLE",
                            component_bond_ids=[3], component_snapshot_ids=[30], component_secids=["OFZ2"],
                            component_isins=[None], component_yields_pct=[D(12)]),
                   ]),
        provenance=dict(target_market_snapshot_id=5, target_market_trade_date=DAY, curve_trade_date=DAY),
        quality_flags=["CURVE_DUPLICATE_DURATION_AGGREGATED"],
    )
    liquidity = BondLiquidityFeatureView(
        bond_id=1, isin="RU000A123474", secid="TASK274", as_of_date=DAY, market_source="moex",
        window_start_date=DAY - timedelta(days=29), lookback_calendar_days=30, min_observation_days=5,
        calendar_window_days=30, snapshot_observation_days=5,
        trade_volume_observation_days=5, turnover_value_observation_days=5, num_trades_observation_days=5,
        latest_trade_date=DAY, latest_observation_age_days=0,
        median_daily_turnover_value=D("12345.67890123456789012345678"),
        mean_daily_turnover_value=D(12345), total_turnover_value=D(61725),
        median_daily_trade_volume=D("12.34567890123456789012345678"),
        mean_daily_trade_volume=D(12), total_trade_volume=D(60),
        median_daily_num_trades=D("12.5"), mean_daily_num_trades=D("12.5"), total_num_trades=62,
        days_with_positive_turnover=5, days_with_positive_trade_volume=5, days_with_positive_num_trades=5,
        positive_turnover_share_of_turnover_observations=D(1),
        positive_trade_count_share_of_trade_count_observations=D(1),
        score_status="READY", liquidity_score_v1=D("83.12345678912345678912345678"),
        score_components=dict(turnover_percentile=D("72.12345678912345678912345678"),
                              trade_count_percentile=D("85.23456789123456789123456789"), recency_percentile=D(100)),
        availability=dict(has_market_snapshots=True, has_trade_volume=True, has_turnover_value=True,
                          has_num_trades=True, has_sufficient_observation_days=True,
                          has_score_components=True, has_liquidity_score=True),
        provenance=dict(market_source="moex", window_start_date=DAY - timedelta(days=29), as_of_date=DAY,
                        lookback_calendar_days=30, min_observation_days=5,
                        selected_market_snapshot_ids=[1, 2, 3, 4, 5], selected_trade_dates=dates,
                        universe_candidate_count=12, score_eligible_universe_count=10),
        quality_flags=["PARTIAL_TURNOVER_VALUE_COVERAGE"],
    )
    result = SimpleNamespace(relative=relative, liquidity=liquidity, calls=[])
    def read_relative(self, *args, **kwargs):
        result.calls.append(("RELATIVE", args, kwargs))
        return result.relative
    def read_liquidity(self, *args, **kwargs):
        result.calls.append(("LIQUIDITY", args, kwargs))
        return result.liquidity
    monkeypatch.setattr(OfzReferenceCurveService, "evaluate_bond", read_relative)
    monkeypatch.setattr(BondLiquidityFeatureService, "build_for_bond", read_liquidity)
    return result


def build(db, **kwargs):
    return BondLiquidityAwareRelativeValueService(db).build_for_bond(1, DAY, **kwargs)


def test_service_delegates_once_and_matches_direct_composer(db_session, evidence, monkeypatch):
    expected = compose_bond_liquidity_aware_relative_value(
        evidence.relative,
        evidence.liquidity,
        bond_id=1,
        as_of_date=DAY,
        market_source="moex",
    )
    calls = []

    def tracked(relative, liquidity, **kwargs):
        calls.append((relative, liquidity, kwargs))
        return compose_bond_liquidity_aware_relative_value(relative, liquidity, **kwargs)

    monkeypatch.setattr(service_module, "compose_bond_liquidity_aware_relative_value", tracked)
    result = build(db_session)
    assert result.model_dump() == expected.model_dump()
    assert evidence.calls == [
        ("RELATIVE", (1, DAY), {"market_source": "moex", "max_market_age_days": 7,
                                "max_curve_age_days": 7}),
        ("LIQUIDITY", (1, DAY), {"market_source": "moex", "lookback_calendar_days": 30,
                                 "min_observation_days": 5}),
    ]
    assert calls == [(evidence.relative, evidence.liquidity, {
        "bond_id": 1, "as_of_date": DAY, "market_source": "moex",
    })]


def test_public_signature_is_unchanged():
    assert str(inspect.signature(BondLiquidityAwareRelativeValueService.build_for_bond)) == (
        "(self, bond_id: int, as_of_date: datetime.date, *, market_source: str = 'moex', "
        'max_market_age_days: int = 7, max_curve_age_days: int = 7, '
        'liquidity_lookback_calendar_days: int = 30, '
        'liquidity_min_observation_days: int = 5) '
        '-> app.schemas.bond_liquidity_relative_value.BondLiquidityAwareRelativeValueView'
    )


def unavailable(result, status):
    assert result.status == status
    assert not result.availability.has_liquidity_aware_relative_value
    assert result.capabilities.liquidity_aware_relative_value_ready is True and result.pit_ready is False
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert BondLiquidityAwareRelativeValueView.model_validate_json(result.model_dump_json()) == result


def relative_unavailable(evidence, status="CURVE_NOT_READY"):
    evidence.relative = evidence.relative.model_copy(update=dict(status=status, reference_ofz_yield_pct=None,
        spread_to_ofz_pp=None, spread_to_ofz_bps=None, interpolation_method=None))


def liquidity_unavailable(evidence, status="INSUFFICIENT_UNIVERSE"):
    evidence.liquidity = evidence.liquidity.model_copy(update=dict(score_status=status, liquidity_score_v1=None,
        score_components=BondLiquidityScoreComponents()))


@pytest.mark.parametrize("pp,bps", [("1.5", "150"), ("0", "0"), ("-0.25", "-25"),
    ("0.123456789123456789123456789", "12.3456789123456789123456789")])
@pytest.mark.parametrize("score", ["0", "50", "100"])
def test_ready_exact_values_spread_sign_and_score_extrema(db_session, evidence, pp, bps, score):
    evidence.relative = evidence.relative.model_copy(update={"spread_to_ofz_pp": D(pp), "spread_to_ofz_bps": D(bps)})
    evidence.liquidity = evidence.liquidity.model_copy(update={"liquidity_score_v1": D(score)})
    result = build(db_session)
    assert result.status == "READY"
    assert all(result.availability.model_dump().values())
    assert result.spread_to_ofz_pp == D(pp) and result.spread_to_ofz_bps == D(bps)
    assert result.liquidity_score_v1 == D(score)
    for field in ("target_yield_to_maturity_pct", "target_duration_years", "reference_ofz_yield_pct",
                  "interpolation_method"):
        assert getattr(result, field) == getattr(evidence.relative, field)
    for field in ("turnover_percentile", "trade_count_percentile", "recency_percentile"):
        assert getattr(result, field) == getattr(evidence.liquidity.score_components, field)
    for field in ("median_daily_turnover_value", "median_daily_trade_volume", "median_daily_num_trades"):
        assert getattr(result, field) == getattr(evidence.liquidity, field)
    assert result.latest_liquidity_trade_date == DAY and result.latest_liquidity_observation_age_days == 0
    assert result.liquidity_snapshot_observation_days == 5
    assert result.quality_flags == []


def test_forwarding_exact_source_and_compact_provenance(db_session, evidence):
    source = " manual "
    evidence.relative = evidence.relative.model_copy(update={"market_source": source})
    evidence.liquidity = evidence.liquidity.model_copy(update={"market_source": source})
    result = build(db_session, market_source=source, max_market_age_days=0, max_curve_age_days=3,
                   liquidity_lookback_calendar_days=12, liquidity_min_observation_days=2)
    assert result.status == "READY" and result.market_source == source
    assert evidence.calls == [
        ("RELATIVE", (1, DAY), {"market_source": source, "max_market_age_days": 0, "max_curve_age_days": 3}),
        ("LIQUIDITY", (1, DAY), {"market_source": source, "lookback_calendar_days": 12, "min_observation_days": 2}),
    ]
    p = result.provenance
    assert p.relative_value_contract_version == "bond-relative-value-v1"
    assert p.ofz_curve_contract_version == "ofz-reference-curve-v1"
    assert p.liquidity_contract_version == "bond-liquidity-feature-v1"
    assert p.relative_value_target_market_snapshot_id == p.liquidity_latest_market_snapshot_id == 5
    assert p.relative_value_target_market_trade_date == p.liquidity_latest_trade_date == p.curve_trade_date == DAY
    assert p.relative_value_identity == p.liquidity_identity
    assert p.liquidity_window_start_date == DAY - timedelta(days=29)
    assert p.liquidity_lookback_calendar_days == 30 and p.liquidity_min_observation_days == 5
    assert p.requested_as_of_date == DAY and p.market_source == source and p.liquidity_provenance_valid
    assert "curve" not in result.model_dump()
    assert "selected_market_snapshot_ids" not in p.model_dump() and "selected_trade_dates" not in p.model_dump()


@pytest.mark.parametrize("side", ["relative", "liquidity"])
@pytest.mark.parametrize("changes", [{"bond_id": 99}, {"as_of_date": DAY - timedelta(days=1)},
    {"market_source": "manual"}, {"bond_id": True}, {"as_of_date": datetime(2026, 9, 18)}])
def test_request_identity_mismatch_hides_only_wrong_side(db_session, evidence, side, changes):
    setattr(evidence, side, getattr(evidence, side).model_copy(update=changes))
    result = build(db_session)
    unavailable(result, "MARKET_EVIDENCE_MISMATCH")
    assert not result.availability.has_matching_market_evidence
    if side == "relative":
        assert result.spread_to_ofz_bps is result.target_duration_years is result.curve_trade_date is None
        assert result.liquidity_score_v1 == evidence.liquidity.liquidity_score_v1
        assert result.availability.has_liquidity_score
    else:
        assert result.liquidity_score_v1 is result.median_daily_turnover_value is None
        assert result.latest_liquidity_trade_date is result.liquidity_snapshot_observation_days is None
        assert result.spread_to_ofz_bps == D(125) and result.availability.has_relative_value
    identity = getattr(result.provenance, "relative_value_identity" if side == "relative" else "liquidity_identity")
    for field, value in changes.items():
        expected = value.date() if type(value) is datetime else value
        assert getattr(identity, field) == expected


def test_both_wrong_identities_do_not_agree_into_ready(db_session, evidence):
    evidence.relative = evidence.relative.model_copy(update={"bond_id": 99})
    evidence.liquidity = evidence.liquidity.model_copy(update={"bond_id": 99})
    result = build(db_session)
    unavailable(result, "MARKET_EVIDENCE_MISMATCH")
    assert result.bond_id == 1 and result.isin is result.secid is None
    assert result.spread_to_ofz_bps is result.liquidity_score_v1 is None


@pytest.mark.parametrize("changes", [{"target_market_snapshot_id": 99},
    {"target_market_trade_date": DAY - timedelta(days=1)}, {"target_market_snapshot_id": None},
    {"target_market_trade_date": None}, {"target_market_snapshot_id": None, "target_market_trade_date": None}])
def test_snapshot_mismatch_retains_both_independent_sides(db_session, evidence, changes):
    evidence.relative = evidence.relative.model_copy(update={
        "provenance": evidence.relative.provenance.model_copy(update=changes)})
    result = build(db_session)
    unavailable(result, "MARKET_EVIDENCE_MISMATCH")
    assert result.spread_to_ofz_bps == D(125)
    assert result.liquidity_score_v1 == evidence.liquidity.liquidity_score_v1
    assert not result.availability.has_matching_market_evidence
    assert result.provenance.liquidity_latest_market_snapshot_id == 5
    assert result.provenance.relative_value_target_market_snapshot_id == changes.get("target_market_snapshot_id", 5)


@pytest.mark.parametrize("updates", [
    {"selected_market_snapshot_ids": [1]}, {"selected_trade_dates": []},
    {"selected_market_snapshot_ids": [], "selected_trade_dates": []},
    {"selected_trade_dates": [DAY] * 5},
    {"selected_trade_dates": [DAY - timedelta(days=i) for i in range(5)]},
    {"selected_market_snapshot_ids": None}, {"selected_trade_dates": None},
    {"selected_market_snapshot_ids": "12345"}, {"selected_market_snapshot_ids": [1, 2, 3, 4, True]},
    {"selected_market_snapshot_ids": [1, 2, 3, 4, 0]},
    {"selected_trade_dates": [DAY - timedelta(days=i) for i in range(4, 0, -1)] + ["2026-09-18"]},
])
def test_malformed_liquidity_provenance_never_repaired(db_session, evidence, updates):
    evidence.liquidity = evidence.liquidity.model_copy(update={
        "provenance": evidence.liquidity.provenance.model_copy(update=updates)})
    before = deepcopy((evidence.liquidity.provenance.selected_market_snapshot_ids,
                       evidence.liquidity.provenance.selected_trade_dates))
    result = build(db_session)
    unavailable(result, "LIQUIDITY_PROVENANCE_INVALID")
    assert result.provenance.liquidity_latest_market_snapshot_id is None
    assert result.provenance.liquidity_latest_trade_date is None
    assert not result.provenance.liquidity_provenance_valid
    assert result.spread_to_ofz_bps == D(125)
    assert not result.availability.has_liquidity_score
    assert (evidence.liquidity.provenance.selected_market_snapshot_ids,
            evidence.liquidity.provenance.selected_trade_dates) == before


def test_ready_empty_arrays_invalid_even_with_zero_reported_days(db_session, evidence):
    evidence.liquidity = evidence.liquidity.model_copy(update={"snapshot_observation_days": 0,
        "provenance": evidence.liquidity.provenance.model_copy(update={
            "selected_market_snapshot_ids": [], "selected_trade_dates": []})})
    unavailable(build(db_session), "LIQUIDITY_PROVENANCE_INVALID")


@pytest.mark.parametrize("status", ["TARGET_MARKET_STALE", "CURVE_NOT_READY", "TARGET_DURATION_OUTSIDE_CURVE"])
def test_relative_unavailable_preserves_liquidity_and_prefixed_status(db_session, evidence, status):
    relative_unavailable(evidence, status)
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
    assert result.liquidity_score_v1 == evidence.liquidity.liquidity_score_v1
    assert result.availability.has_liquidity_score and not result.availability.has_relative_value
    assert "RELATIVE_VALUE_" + status in result.quality_flags


@pytest.mark.parametrize("status", ["NO_MARKET_DATA", "INSUFFICIENT_TARGET_EVIDENCE", "MISSING_SCORE_COMPONENT", "INSUFFICIENT_UNIVERSE"])
def test_liquidity_unavailable_preserves_spread_and_raw_evidence(db_session, evidence, status):
    liquidity_unavailable(evidence, status)
    result = build(db_session)
    unavailable(result, "LIQUIDITY_UNAVAILABLE")
    assert result.spread_to_ofz_bps == D(125) and result.availability.has_spread_to_ofz
    assert result.median_daily_turnover_value == evidence.liquidity.median_daily_turnover_value
    assert result.liquidity_score_v1 is None
    assert "LIQUIDITY_" + status in result.quality_flags


def test_both_unavailable_precedence_and_all_flags(db_session, evidence):
    relative_unavailable(evidence)
    liquidity_unavailable(evidence)
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
    assert {"RELATIVE_VALUE_UNAVAILABLE", "LIQUIDITY_UNAVAILABLE", "RELATIVE_VALUE_CURVE_NOT_READY",
            "LIQUIDITY_INSUFFICIENT_UNIVERSE"} <= set(result.quality_flags)


def test_empty_missing_side_not_artificial_market_mismatch(db_session, evidence):
    liquidity_unavailable(evidence, "NO_MARKET_DATA")
    evidence.liquidity = evidence.liquidity.model_copy(update={"snapshot_observation_days": 0,
        "latest_trade_date": None, "latest_observation_age_days": None,
        "provenance": evidence.liquidity.provenance.model_copy(update={
            "selected_market_snapshot_ids": [], "selected_trade_dates": []})})
    result = build(db_session)
    unavailable(result, "LIQUIDITY_UNAVAILABLE")
    assert "MARKET_EVIDENCE_MISMATCH" not in result.quality_flags
    assert result.provenance.liquidity_provenance_valid
    assert not result.availability.has_matching_market_evidence and not result.availability.has_liquidity_feature
    relative_unavailable(evidence, "TARGET_MARKET_MISSING")
    evidence.relative = evidence.relative.model_copy(update={"provenance": evidence.relative.provenance.model_copy(
        update={"target_market_snapshot_id": None, "target_market_trade_date": None})})
    repeated = build(db_session)
    unavailable(repeated, "RELATIVE_VALUE_UNAVAILABLE")
    assert "MARKET_EVIDENCE_MISMATCH" not in repeated.quality_flags
    assert repeated.model_dump_json() == build(db_session).model_dump_json()


@pytest.mark.parametrize("value", [None, D("NaN"), D("Infinity"), D("-Infinity"), 125, 1.25, True, "125"])
@pytest.mark.parametrize("field", ["spread_to_ofz_pp", "spread_to_ofz_bps"])
def test_invalid_ready_spread_fail_closed(db_session, evidence, field, value):
    evidence.relative = evidence.relative.model_copy(update={field: value})
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
    assert "RELATIVE_VALUE_EVIDENCE_INVALID" in result.quality_flags
    assert getattr(result, field) is None
    assert result.availability.has_liquidity_score


@pytest.mark.parametrize("value", [None, D(-1), D(101), D("NaN"), D("Infinity"), 50, 50.0, True, "50"])
@pytest.mark.parametrize("field", ["liquidity_score_v1", "turnover_percentile", "trade_count_percentile", "recency_percentile"])
def test_invalid_ready_score_or_component_fail_closed(db_session, evidence, field, value):
    if field == "liquidity_score_v1":
        evidence.liquidity = evidence.liquidity.model_copy(update={field: value})
    else:
        evidence.liquidity = evidence.liquidity.model_copy(update={
            "score_components": evidence.liquidity.score_components.model_copy(update={field: value})})
    result = build(db_session)
    unavailable(result, "LIQUIDITY_UNAVAILABLE")
    assert "LIQUIDITY_EVIDENCE_INVALID" in result.quality_flags
    assert not result.availability.has_liquidity_score and result.availability.has_spread_to_ofz
    assert getattr(result, field) == (value if isinstance(value, Decimal) and value.is_finite() else None)


def test_full_status_precedence_provenance_before_identity_before_missingness(db_session, evidence):
    relative_unavailable(evidence)
    liquidity_unavailable(evidence)
    evidence.relative = evidence.relative.model_copy(update={"bond_id": 99})
    evidence.liquidity = evidence.liquidity.model_copy(update={"provenance": evidence.liquidity.provenance.model_copy(
        update={"selected_market_snapshot_ids": []})})
    result = build(db_session)
    unavailable(result, "LIQUIDITY_PROVENANCE_INVALID")
    assert {"LIQUIDITY_PROVENANCE_INVALID", "MARKET_EVIDENCE_MISMATCH", "RELATIVE_VALUE_UNAVAILABLE",
            "LIQUIDITY_UNAVAILABLE"} <= set(result.quality_flags)
    evidence.liquidity = evidence.liquidity.model_copy(update={"provenance": evidence.liquidity.provenance.model_copy(
        update={"selected_market_snapshot_ids": [1, 2, 3, 4, 5]})})
    unavailable(build(db_session), "MARKET_EVIDENCE_MISMATCH")


def test_no_formula_checks_exact_copy_context_and_input_immutability(db_session, evidence):
    evidence.relative = evidence.relative.model_copy(update={"spread_to_ofz_pp": D("0.123456789123456789123456789"),
        "spread_to_ofz_bps": D("987.654321987654321987654321")})
    before = deepcopy((evidence.relative.model_dump(), evidence.liquidity.model_dump()))
    result = build(db_session)
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)) as context:
        repeated = build(db_session)
        assert context.prec == 3 and context.rounding == ROUND_DOWN
    assert result.status == "READY" and result.model_dump_json() == repeated.model_dump_json()
    assert result.spread_to_ofz_pp == evidence.relative.spread_to_ofz_pp
    assert result.spread_to_ofz_bps == evidence.relative.spread_to_ofz_bps
    assert (evidence.relative.model_dump(), evidence.liquidity.model_dump()) == before


@pytest.fixture
def integration(db_session):
    company = Company(name="Task274 integration issuer", ticker="TASK274")
    db_session.add(company)
    db_session.flush()
    bonds, snapshots, profiles = [], [], []
    for index in range(12):
        ofz = index >= 10
        duration = D(1) if index == 10 else D(3) if index == 11 else D(2)
        ytm = D(10) if index == 10 else D(12)
        bond = Bond(company_id=company.id, name="ОФЗ-ПД" if ofz else "Corporate bond",
                    secid=f"TASK274_{index}", duration_years=D(99), yield_to_maturity=D(99),
                    volume=D(999999), liquidity_score=99)
        db_session.add(bond)
        db_session.flush()
        bonds.append(bond)
        if ofz:
            profile = BondSecurityMasterProfile(bond_id=bond.id, currency_state="verified", currency_code="RUB",
                coupon_structure="fixed", amortization_structure="bullet", perpetual_structure="dated",
                maturity_state="verified", maturity_date=DAY + timedelta(days=365))
            db_session.add(profile)
            profiles.append(profile)
        for age in range(5):
            snapshot = BondMarketSnapshot(bond_id=bond.id, trade_date=DAY - timedelta(days=age), source="moex",
                duration_years=duration, yield_to_maturity=ytm, spread_to_ofz=D(999), liquidity_score=99,
                raw_payload={"moex": {"DURATION": str(duration * D(365)),
                                     "VALUE": str((index + 1) * 100), "NUMTRADES": index + 1, "VOLUME": "50"}})
            db_session.add(snapshot)
            snapshots.append(snapshot)
    db_session.commit()
    return SimpleNamespace(target=bonds[0], bonds=bonds, snapshots=snapshots, profiles=profiles)


def test_real_dependency_integration_same_snapshot_values_and_repeat(db_session, integration):
    identity = integration.target.id
    relative = OfzReferenceCurveService(db_session).evaluate_bond(identity, DAY)
    liquidity = BondLiquidityFeatureService(db_session).build_for_bond(identity, DAY)
    service = BondLiquidityAwareRelativeValueService(db_session)
    result = service.build_for_bond(identity, DAY)
    assert result.status == relative.status == liquidity.score_status == "READY"
    assert result.spread_to_ofz_bps == relative.spread_to_ofz_bps == D(100)
    assert result.liquidity_score_v1 == liquidity.liquidity_score_v1
    assert result.provenance.relative_value_target_market_snapshot_id == liquidity.provenance.selected_market_snapshot_ids[-1]
    assert result.model_dump_json() == service.build_for_bond(identity, DAY).model_dump_json()


@pytest.mark.parametrize("pending", [False, True])
def test_sql_select_only_pending_state_and_source_rows_unchanged(db_session, integration, monkeypatch, pending):
    doomed = Company(name="Caller deletion", ticker="DELETE274")
    db_session.add(doomed)
    db_session.commit()
    identity = integration.target.id
    source = deepcopy([(row.raw_payload, row.duration_years, row.yield_to_maturity,
                        row.spread_to_ofz, row.liquidity_score) for row in integration.snapshots])
    legacy = [(row.duration_years, row.yield_to_maturity, row.volume, row.liquidity_score) for row in integration.bonds]
    profile = integration.profiles[0]
    original_currency = profile.currency_code
    if pending:
        db_session.add(Company(name="Caller pending", ticker="PENDING274"))
        integration.target.name = "Caller edit"
        profile.currency_code = "USD"
        db_session.delete(doomed)
    state = tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        pytest.fail("Composition attempted session mutation")
    db_session.autoflush = True
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for name in ("add", "add_all", "delete", "flush", "commit", "merge"):
                patch.setattr(db_session, name, forbidden)
            result = BondLiquidityAwareRelativeValueService(db_session).build_for_bond(identity, DAY)
        if pending:
            # Task268 observes its existing ORM profile; do not override caller evidence.
            unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
            assert result.relative_value_status == "CURVE_NOT_READY"
            assert result.availability.has_liquidity_score
        else:
            assert result.status == "READY" and result.spread_to_ofz_bps == D(100)
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted)) == state
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert [(row.raw_payload, row.duration_years, row.yield_to_maturity, row.spread_to_ofz, row.liquidity_score)
            for row in integration.snapshots] == source
    assert [(row.duration_years, row.yield_to_maturity, row.volume, row.liquidity_score)
            for row in integration.bonds] == legacy
    assert profile.currency_code == ("USD" if pending else original_currency)


@pytest.mark.parametrize("changes", [
    {"bond_id": True}, {"bond_id": 0}, {"bond_id": -1}, {"bond_id": 1.0}, {"bond_id": "1"},
    {"as_of_date": datetime(2026, 9, 18)}, {"as_of_date": None}, {"as_of_date": "2026-09-18"},
    {"market_source": None}, {"market_source": 1}, {"market_source": ""}, {"market_source": " "},
    {"max_market_age_days": True}, {"max_market_age_days": -1}, {"max_market_age_days": 1.0},
    {"max_curve_age_days": True}, {"max_curve_age_days": -1}, {"max_curve_age_days": "7"},
    {"liquidity_lookback_calendar_days": True}, {"liquidity_lookback_calendar_days": 0},
    {"liquidity_lookback_calendar_days": -1}, {"liquidity_lookback_calendar_days": 30.0},
    {"liquidity_min_observation_days": True}, {"liquidity_min_observation_days": 0},
    {"liquidity_min_observation_days": -1}, {"liquidity_min_observation_days": 31},
    {"liquidity_min_observation_days": 5.0}, {"as_of_date": date.min},
    {"liquidity_lookback_calendar_days": 10**30},
])
def test_invalid_arguments_before_dependency_reads(db_session, evidence, changes):
    with pytest.raises(ValueError):
        BondLiquidityAwareRelativeValueService(db_session).build_for_bond(**{"bond_id": 1, "as_of_date": DAY, **changes})
    assert evidence.calls == []


def test_date_min_single_day_window_is_representable(db_session, evidence):
    result = BondLiquidityAwareRelativeValueService(db_session).build_for_bond(1, date.min,
        liquidity_lookback_calendar_days=1, liquidity_min_observation_days=1)
    unavailable(result, "MARKET_EVIDENCE_MISMATCH")
    assert len(evidence.calls) == 2


def test_absent_bond_preserves_task267_http_404(db_session):
    with pytest.raises(HTTPException) as error:
        BondLiquidityAwareRelativeValueService(db_session).build_for_bond(999999, DAY)
    assert error.value.status_code == 404 and error.value.detail == "Bond not found"


def test_frozen_extra_forbid_capabilities_and_absent_synthetic_outputs(db_session, evidence):
    result = build(db_session)
    assert result.contract_version == "bond-liquidity-aware-relative-value-v1"
    for model in (result, result.availability, result.provenance, result.capabilities,
                  result.provenance.relative_value_identity, result.provenance.liquidity_identity):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    for changes in ({"pit_ready": True}, {"status": "ATTRACTIVE"}, {"relative_value_status": "GOOD"}):
        with pytest.raises(ValidationError):
            BondLiquidityAwareRelativeValueView.model_validate({**result.model_dump(), **changes})
    true_fields = {"spread_to_ofz_input_ready", "liquidity_score_input_ready", "liquidity_aware_relative_value_ready"}
    for name, value in result.capabilities.model_dump().items():
        assert value is (name in true_fields)
    forbidden = {"liquidity_adjusted_spread_bps", "liquidity_penalty_bps", "liquidity_premium_bps", "net_spread_bps",
        "tradable_spread_bps", "execution_adjusted_spread_bps", "investment_score", "composite_score", "score",
        "recommendation", "ranking", "signal", "label", "quantity", "weight"}
    assert not forbidden & set(result.model_dump())


def test_narrow_static_safety_no_calculations_direct_rows_or_network():
    path = Path(__file__).parents[1] / "app/services/bond_liquidity_relative_value_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = " ".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert not any(word in imports for word in ("app.models", "requests", "httpx", "urllib", "moex_iss",
        "strategy", "portfolio", "risk_engine", "broker", "_midrank", "_liquidity" + " import"))
    assert "bond_liquidity_relative_value_composer" in imports
    assert sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "compose_bond_liquidity_aware_relative_value"
               for node in ast.walk(tree)) == 1
    forbidden_names = {"_finite", "_score_value", "_liquidity_provenance_valid",
                       "relative_numbers_valid", "liquidity_numbers_valid", "conditions", "failures"}
    assert not forbidden_names & {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "add":
                    assert ast.unparse(node.func.value) == "flags"
                else:
                    assert node.func.attr not in {"add_all", "flush", "commit", "delete", "merge", "execute", "get", "build_curve"}
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"float", "insert", "update", "delete", "create_engine"}
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"liquidity_score", "volume", "spread_to_ofz", "raw_payload", "nodes", "num_trades"}
        if isinstance(node, ast.BinOp):
            assert isinstance(node.op, ast.Sub)
            assert ast.unparse(node) in {"as_of_date - timedelta(days=liquidity_lookback_calendar_days - 1)",
                                         "liquidity_lookback_calendar_days - 1"}
        assert not isinstance(node, ast.Constant) or not isinstance(node.value, float)
