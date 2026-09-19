"""Task282 pure M3 composite evidence-contract acceptance."""

import ast
import inspect
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, getcontext, setcontext

import pytest
from pydantic import ValidationError

from app.schemas.bond_m3_feature_view import BondM3FeatureView
from app.services.bond_m3_feature_composer import compose_bond_m3_feature_view


DAY = date(2026, 9, 19)
D = Decimal


def _market(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        market_snapshot_id=101, market_trade_date=DAY, market_source="moex",
        market_age_days=0, market_status="FRESH", price=D("99"),
        clean_price=D("98.5"), nkd=D("1.5"), yield_to_maturity_pct=D("12"),
        duration_years=D("2"), trade_volume=D("100"), turnover_value=D("9900"),
        num_trades=10, currency="RUB", nominal_value=D("1000"), coupon_rate=D("10"),
        maturity_date=DAY + timedelta(days=730), offer_date=None, days_to_maturity=730,
        days_to_offer=None, is_matured=False, is_floating_coupon=False,
        is_subordinated=False, is_perpetual=False, metadata_has_amortization=False,
        future_cashflow_event_count=2, future_coupon_count=1,
        future_amortization_count=0, next_coupon_date=DAY + timedelta(days=180),
        next_amortization_date=None, next_redemption_date=DAY + timedelta(days=730),
        next_offer_redemption_date=None, has_future_coupon=True,
        has_future_amortization=False, has_future_redemption=True,
        has_future_offer_redemption=False,
        availability=dict(
            has_market_snapshot=True, has_price=True, has_clean_price=True, has_nkd=True,
            has_yield_to_maturity=True, has_duration=True, has_trade_volume=True,
            has_turnover_value=True, has_num_trades=True, has_cashflow_schedule=True,
            has_maturity=True, has_offer=False,
        ),
        quality_flags=["NESTED_MARKET_FLAG"],
        provenance=dict(
            market_snapshot_id=101, market_source="moex", market_trade_date=DAY,
            selected_cashflow_event_ids=[1, 2],
        ),
    )
    data.update(updates)
    from app.schemas.bond_market_features import BondMarketFeatureView
    return BondMarketFeatureView(**data)


def _relative(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        market_source="moex", status="READY", target_yield_to_maturity_pct=D("12"),
        target_duration_years=D("2"), reference_ofz_yield_pct=D("11"),
        spread_to_ofz_pp=D("1"), spread_to_ofz_bps=D("100"),
        interpolation_method="EXACT_NODE",
        curve=dict(
            as_of_date=DAY, market_source="moex", status="READY", curve_trade_date=DAY,
            node_count=2, min_duration_years=D("1"), max_duration_years=D("3"),
            nodes=[], diagnostics={},
        ),
        provenance=dict(
            target_market_snapshot_id=101, target_market_trade_date=DAY,
            curve_trade_date=DAY,
        ),
        quality_flags=["NESTED_RELATIVE_FLAG"],
    )
    data.update(updates)
    from app.schemas.ofz_reference_curve import BondRelativeValueView
    return BondRelativeValueView(**data)


def _credit(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        issuer_link_status="VERIFIED", legal_issuer_id=201,
        legal_issuer_source_issuer_id="issuer-201", legal_issuer_inn="7700000000",
        bond_ratings=[], issuer_ratings=[], bank_subject_status="NO_VERIFIED_SUBJECT",
        bank_reporting_subject_id=None, bank_subject_regn=None, bank_report_date=None,
        bank_report_age_days=None, bank_metrics=[],
        availability=dict(
            has_verified_legal_issuer=True, has_bond_rating_event=True,
            has_bond_rating_value=True, has_issuer_rating_event=False,
            has_issuer_rating_value=False, has_verified_bank_subject=False,
            has_bank_credit_metrics=False, bond_rating_agencies=["ACRA"],
            issuer_rating_agencies=[],
        ),
        provenance=dict(
            bond_legal_issuer_profile_id=301, mapping_state="verified",
            mapping_source="moex_security_reference", mapping_source_issuer_id="issuer-201",
            legal_issuer_identity_source="moex_security_reference",
            verified_bank_reporting_subject_ids=[], bank_bridge_current_evidence_id=None,
        ),
        quality_flags=["NESTED_CREDIT_FLAG"],
    )
    data.update(updates)
    from app.schemas.bond_credit_features import BondCreditFeatureView
    return BondCreditFeatureView(**data)


def _comparison(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        status="READY", bond_rating_entries=[], issuer_rating_entries=[],
        availability=dict(
            has_bond_rating_evidence=True, has_issuer_rating_evidence=False,
            has_bond_comparable_cohort=True, has_issuer_comparable_cohort=False,
            bond_comparable_agencies=["ACRA"], issuer_comparable_agencies=[],
            has_any_comparable_cohort=True,
        ),
        provenance=dict(
            credit_feature_contract_version="bond-credit-feature-v1",
            credit_feature_bond_id=1, credit_feature_as_of_date=DAY, as_of_date=DAY,
            bond_legal_issuer_profile_id=301, legal_issuer_id=201,
            issuer_link_status="VERIFIED", bond_rating_event_ids=[], issuer_rating_event_ids=[],
            selected_bond_rating_event_ids=[], selected_issuer_rating_event_ids=[],
            bond_rating_agencies=["ACRA"], issuer_rating_agencies=[],
        ),
        quality_flags=["NESTED_COMPARABILITY_FLAG"],
    )
    data.update(updates)
    from app.schemas.bond_credit_comparability import BondCreditComparabilityView
    return BondCreditComparabilityView(**data)


def _liquidity(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        window_start_date=DAY - timedelta(days=29), market_source="moex",
        lookback_calendar_days=30, min_observation_days=5, calendar_window_days=30,
        snapshot_observation_days=5, trade_volume_observation_days=5,
        turnover_value_observation_days=5, num_trades_observation_days=5,
        latest_trade_date=DAY, latest_observation_age_days=0,
        median_daily_turnover_value=D("100"), mean_daily_turnover_value=D("100"),
        total_turnover_value=D("500"), median_daily_trade_volume=D("10"),
        mean_daily_trade_volume=D("10"), total_trade_volume=D("50"),
        median_daily_num_trades=D("4"), mean_daily_num_trades=D("4"),
        total_num_trades=20, days_with_positive_turnover=5,
        days_with_positive_trade_volume=5, days_with_positive_num_trades=5,
        positive_turnover_share_of_turnover_observations=D("1"),
        positive_trade_count_share_of_trade_count_observations=D("1"),
        score_status="READY",
        score_components=dict(
            turnover_percentile=D("70"), trade_count_percentile=D("60"),
            recency_percentile=D("100"),
        ),
        liquidity_score_v1=D("71"),
        availability=dict(
            has_market_snapshots=True, has_trade_volume=True, has_turnover_value=True,
            has_num_trades=True, has_sufficient_observation_days=True,
            has_score_components=True, has_liquidity_score=True,
        ),
        provenance=dict(
            market_source="moex", window_start_date=DAY - timedelta(days=29),
            as_of_date=DAY, lookback_calendar_days=30, min_observation_days=5,
            selected_market_snapshot_ids=[97, 98, 99, 100, 101],
            selected_trade_dates=[DAY - timedelta(days=i) for i in range(4, -1, -1)],
            universe_candidate_count=12, score_eligible_universe_count=10,
        ),
        quality_flags=["NESTED_LIQUIDITY_FLAG"],
    )
    data.update(updates)
    from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
    return BondLiquidityFeatureView(**data)


def _duration(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        market_source="moex", market_snapshot_id=101, market_trade_date=DAY,
        market_age_days=0, market_status="FRESH", macaulay_duration_years=D("2"),
        yield_to_maturity_pct=D("12"), yield_decimal=D("0.12"), coupon_structure="fixed",
        coupon_frequency_state="verified", coupon_frequency_per_year=2,
        perpetual_structure="dated", modified_duration_denominator=D("1.06"),
        modified_duration_years=D("1.886792452830188679245283019"), status="READY",
        availability=dict(
            has_market_snapshot=True, has_fresh_market_data=True,
            has_macaulay_duration=True, has_yield_to_maturity=True,
            has_security_master=True, has_fixed_coupon_structure=True,
            has_verified_coupon_frequency=True, has_dated_structure=True,
            has_valid_denominator=True, has_modified_duration=True,
        ),
        quality_flags=["NESTED_DURATION_FLAG"],
        provenance=dict(
            market_contract_version="bond-market-feature-v1", market_snapshot_id=101,
            market_trade_date=DAY, market_source="moex", as_of_date=DAY,
            max_market_age_days=7, security_master_profile_id=401,
            security_master_contract_version="bond-security-master-v1",
            coupon_frequency_state="verified", coupon_frequency_per_year=2,
        ),
    )
    data.update(updates)
    from app.schemas.bond_modified_duration import BondModifiedDurationView
    return BondModifiedDurationView(**data)


def _dv01(**updates):
    md = D("1.886792452830188679245283019")
    identity = dict(
        bond_id=1, market_snapshot_id=101, market_trade_date=DAY, market_source="moex"
    )
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        market_source="moex", market_snapshot_id=101, market_trade_date=DAY,
        market_age_days=0, modified_duration_status="READY",
        modified_duration_years=md, currency_code="RUB", currency_state="verified",
        nominal_state="verified", nominal_value=D("1000"), price_basis="CLEAN_PRICE",
        clean_quote_pct=D("98.5"), nkd_currency=D("15"), clean_value_currency=D("985"),
        dirty_value_currency=D("1000"), relative_price_sensitivity_per_1bp=D("0.000188"),
        dv01_currency_per_bond=D("0.188679"), status="READY",
        availability=dict(
            has_modified_duration=True, has_matching_market_snapshot=True,
            has_security_master=True, has_verified_currency=True,
            is_supported_currency=True, has_verified_nominal=True, has_clean_quote=True,
            has_nkd=True, has_clean_value=True, has_dirty_value=True,
            has_relative_1bp_sensitivity=True, has_dv01=True,
        ),
        quality_flags=["NESTED_DV01_FLAG"],
        provenance=dict(
            modified_duration_contract_version="bond-modified-duration-v1",
            modified_duration_formula_version="modified-duration-v1",
            modified_duration_market_identity=identity,
            market_contract_version="bond-market-feature-v1", market_identity=identity,
            market_snapshot_id=101, market_trade_date=DAY, market_source="moex",
            as_of_date=DAY, max_market_age_days=7, security_master_profile_id=401,
            security_master_contract_version="bond-security-master-v1",
            currency_state="verified", nominal_state="verified", price_basis="CLEAN_PRICE",
        ),
    )
    data.update(updates)
    from app.schemas.bond_dv01 import BondDv01View
    return BondDv01View(**data)


def _liquidity_relative(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK282", as_of_date=DAY,
        market_source="moex", status="READY", relative_value_status="READY",
        target_yield_to_maturity_pct=D("12"), target_duration_years=D("2"),
        reference_ofz_yield_pct=D("11"), spread_to_ofz_pp=D("1"),
        spread_to_ofz_bps=D("100"), interpolation_method="EXACT_NODE",
        curve_trade_date=DAY, liquidity_status="READY", liquidity_score_v1=D("71"),
        turnover_percentile=D("70"), trade_count_percentile=D("60"),
        recency_percentile=D("100"), median_daily_turnover_value=D("100"),
        median_daily_trade_volume=D("10"), median_daily_num_trades=D("4"),
        latest_liquidity_trade_date=DAY, latest_liquidity_observation_age_days=0,
        liquidity_snapshot_observation_days=5,
        availability=dict(
            has_relative_value=True, has_spread_to_ofz=True, has_liquidity_feature=True,
            has_liquidity_score=True, has_matching_market_evidence=True,
            has_liquidity_aware_relative_value=True,
        ),
        quality_flags=["NESTED_LIQUIDITY_RELATIVE_FLAG"],
        provenance=dict(
            relative_value_contract_version="bond-relative-value-v1",
            ofz_curve_contract_version="ofz-reference-curve-v1",
            liquidity_contract_version="bond-liquidity-feature-v1",
            relative_value_identity=dict(bond_id=1, as_of_date=DAY, market_source="moex"),
            liquidity_identity=dict(bond_id=1, as_of_date=DAY, market_source="moex"),
            relative_value_target_market_snapshot_id=101,
            relative_value_target_market_trade_date=DAY,
            liquidity_latest_market_snapshot_id=101, liquidity_latest_trade_date=DAY,
            curve_trade_date=DAY, liquidity_window_start_date=DAY - timedelta(days=29),
            liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5,
            liquidity_provenance_valid=True, requested_as_of_date=DAY,
            market_source="moex",
        ),
    )
    data.update(updates)
    from app.schemas.bond_liquidity_relative_value import BondLiquidityAwareRelativeValueView
    return BondLiquidityAwareRelativeValueView(**data)


def _peer(status="READY", **updates):
    data = dict(
        target_bond_id=1, min_peer_count=1, as_of_date=DAY, market_source="moex",
        requested_target_kind="BOND", requested_rating_agency="ACRA", status=status,
        batch_status="COMPLETE", requested_bond_count=2, existing_bond_count=2,
        missing_bond_count=0, built_member_count=2, candidate_member_count=2,
        distribution=None,
        availability=dict(
            has_valid_batch=True, target_requested=True, target_exists=True,
            has_target_member=True, has_candidate_members=True,
            has_distribution_result=status == "READY", has_ready_distribution=status == "READY",
        ),
        quality_flags=[],
        provenance=dict(
            batch_contract_version="credit-cohort-batch-members-v1", batch_status="COMPLETE",
            batch_as_of_date=DAY, batch_market_source="moex", batch_target_kind="BOND",
            batch_rating_agency="ACRA", requested_bond_ids=[1, 2],
            existing_bond_ids=[1, 2], missing_bond_ids=[], target_bond_id=1,
            target_item_build_status="BUILT", candidate_member_bond_ids=[1, 2],
            min_peer_count=1,
            reducer_contract_version="credit-cohort-peer-spread-distribution-v1",
            reducer_status=status if status in {
                "TARGET_MEMBER_INVALID", "PEER_INPUT_INVALID", "NO_ELIGIBLE_PEERS",
                "INSUFFICIENT_PEERS", "READY"
            } else None,
            reducer_call_count=1,
        ),
    )
    data.update(updates)
    from app.schemas.credit_cohort_peer_distribution_orchestration import (
        CreditCohortPeerDistributionOrchestrationView,
    )
    return CreditCohortPeerDistributionOrchestrationView(**data)


def _views():
    return dict(
        market=_market(), relative_value=_relative(), credit=_credit(),
        credit_comparability=_comparison(), liquidity=_liquidity(),
        modified_duration=_duration(), dv01=_dv01(),
        liquidity_relative_value=_liquidity_relative(), peer_distribution=None,
    )


def _compose(views=None, **request):
    args = dict(bond_id=1, as_of_date=DAY, market_source="moex")
    args.update(request)
    return compose_bond_m3_feature_view(**args, **(views or _views()))


def test_a_all_required_ready_preserves_authoritative_views_and_availability():
    views = _views()
    result = _compose(views)
    assert result.status == "CONSISTENT" and result.quality_flags == []
    for name, value in views.items():
        assert getattr(result, name) is value
    assert result.isin == "RU000A123456" and result.secid == "TASK282"
    assert result.availability.model_dump() == dict(
        market_fresh=True, relative_value_ready=True,
        has_credit_rating_evidence=True, has_credit_comparable_cohort=True,
        liquidity_score_ready=True, modified_duration_ready=True, dv01_ready=True,
        liquidity_relative_value_ready=True, has_peer_distribution_context=False,
        peer_distribution_ready=False, all_supplied_evidence_consistent=True,
    )
    assert result.pit_ready is False


def test_b_e_coherent_unavailable_features_remain_consistent():
    views = _views()
    views["market"] = _market(
        market_snapshot_id=None, market_trade_date=None, market_status="MISSING",
        yield_to_maturity_pct=None, duration_years=None,
    )
    views["relative_value"] = _relative(
        status="TARGET_MARKET_MISSING", target_yield_to_maturity_pct=None,
        target_duration_years=None, reference_ofz_yield_pct=None,
        spread_to_ofz_pp=None, spread_to_ofz_bps=None, interpolation_method=None,
        provenance=dict(
            target_market_snapshot_id=None, target_market_trade_date=None,
            curve_trade_date=DAY,
        ),
    )
    views["liquidity"] = _liquidity(
        score_status="INSUFFICIENT_UNIVERSE", liquidity_score_v1=None,
        score_components=dict(
            turnover_percentile=None, trade_count_percentile=None, recency_percentile=None
        ),
    )
    views["modified_duration"] = _duration(
        market_snapshot_id=None, market_trade_date=None, market_status="MISSING",
        macaulay_duration_years=None, yield_to_maturity_pct=None, yield_decimal=None,
        modified_duration_denominator=None, modified_duration_years=None,
        status="MARKET_DATA_MISSING",
        provenance={
            **_duration().provenance.model_dump(),
            "market_snapshot_id": None, "market_trade_date": None,
        },
    )
    identity = dict(bond_id=1, market_snapshot_id=None, market_trade_date=None, market_source="moex")
    views["dv01"] = _dv01(
        market_snapshot_id=None, market_trade_date=None,
        modified_duration_status="MARKET_DATA_MISSING", modified_duration_years=None,
        status="MODIFIED_DURATION_UNAVAILABLE",
        provenance={
            **_dv01().provenance.model_dump(),
            "market_snapshot_id": None, "market_trade_date": None,
            "market_identity": identity, "modified_duration_market_identity": identity,
        },
    )
    views["liquidity_relative_value"] = _liquidity_relative(
        status="RELATIVE_VALUE_UNAVAILABLE", relative_value_status="TARGET_MARKET_MISSING",
        reference_ofz_yield_pct=None, spread_to_ofz_pp=None, spread_to_ofz_bps=None,
        interpolation_method=None, liquidity_status="INSUFFICIENT_UNIVERSE",
        liquidity_score_v1=None, turnover_percentile=None, trade_count_percentile=None,
        recency_percentile=None,
    )
    result = _compose(views)
    assert result.status == "CONSISTENT" and result.quality_flags == []
    assert not result.availability.market_fresh
    assert not result.availability.relative_value_ready
    assert not result.availability.liquidity_score_ready
    assert not result.availability.modified_duration_ready
    assert not result.availability.dv01_ready


@pytest.mark.parametrize("status", [
    "READY", "TARGET_MEMBER_INVALID", "PEER_INPUT_INVALID", "NO_ELIGIBLE_PEERS",
    "INSUFFICIENT_PEERS", "TARGET_NOT_REQUESTED", "TARGET_BOND_NOT_FOUND",
    "BATCH_EVIDENCE_INVALID",
])
def test_f_h_optional_ready_and_nonready_peer_context(status):
    views = _views()
    views["peer_distribution"] = _peer(status)
    result = _compose(views)
    assert result.status == "CONSISTENT"
    assert result.peer_distribution is views["peer_distribution"]
    assert result.availability.has_peer_distribution_context
    assert result.availability.peer_distribution_ready is (status == "READY")


@pytest.mark.parametrize("name,flag", [
    ("market", "MARKET_CONTRACT_INVALID"),
    ("relative_value", "RELATIVE_VALUE_CONTRACT_INVALID"),
    ("credit", "CREDIT_CONTRACT_INVALID"),
    ("credit_comparability", "CREDIT_COMPARABILITY_CONTRACT_INVALID"),
    ("liquidity", "LIQUIDITY_CONTRACT_INVALID"),
    ("modified_duration", "MODIFIED_DURATION_CONTRACT_INVALID"),
    ("dv01", "DV01_CONTRACT_INVALID"),
    ("liquidity_relative_value", "LIQUIDITY_RELATIVE_VALUE_CONTRACT_INVALID"),
    ("peer_distribution", "PEER_DISTRIBUTION_CONTRACT_INVALID"),
])
def test_i_r_contract_drift_is_diagnostic_not_exception(name, flag):
    views = _views()
    if name == "peer_distribution":
        views[name] = _peer().model_copy(update={"contract_version": "wrong"})
    else:
        views[name] = views[name].model_copy(update={"contract_version": "wrong"})
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID" and flag in result.quality_flags


def test_k_wrong_curve_contract_and_t_curve_pit_are_diagnostic():
    for patch in ({"contract_version": "wrong"}, {"pit_ready": True}):
        views = _views()
        views["relative_value"] = views["relative_value"].model_copy(update={
            "curve": views["relative_value"].curve.model_copy(update=patch)
        })
        result = _compose(views)
        assert result.status == "EVIDENCE_INVALID"
        expected = "OFZ_CURVE_CONTRACT_INVALID" if "contract_version" in patch else "PIT_CONTRACT_INVALID"
        assert expected in result.quality_flags


@pytest.mark.parametrize("name", [
    "market", "relative_value", "credit", "credit_comparability", "liquidity",
    "modified_duration", "dv01", "liquidity_relative_value",
])
def test_s_any_required_pit_true_is_invalid(name):
    views = _views()
    views[name] = views[name].model_copy(update={"pit_ready": True})
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID"
    assert "PIT_CONTRACT_INVALID" in result.quality_flags


@pytest.mark.parametrize("name", [
    "market", "relative_value", "credit", "credit_comparability", "liquidity",
    "modified_duration", "dv01", "liquidity_relative_value",
])
@pytest.mark.parametrize("field,value,flag", [
    ("bond_id", 2, "BOND_IDENTITY_MISMATCH"),
    ("as_of_date", DAY - timedelta(days=1), "AS_OF_DATE_MISMATCH"),
])
def test_u_v_primary_identity_mismatch(name, field, value, flag):
    views = _views()
    views[name] = views[name].model_copy(update={field: value})
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID" and flag in result.quality_flags


@pytest.mark.parametrize("name", [
    "market", "relative_value", "liquidity", "modified_duration", "dv01",
    "liquidity_relative_value",
])
def test_w_source_aware_identity_is_exact(name):
    views = _views()
    views[name] = views[name].model_copy(update={"market_source": "MOEX"})
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID"
    assert "MARKET_SOURCE_MISMATCH" in result.quality_flags


@pytest.mark.parametrize("field,value", [
    ("target_bond_id", 2), ("as_of_date", DAY - timedelta(days=1)),
    ("market_source", "MOEX"),
])
def test_x_z_peer_identity_mismatch(field, value):
    views = _views()
    views["peer_distribution"] = _peer(**{field: value})
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID"
    assert "PEER_DISTRIBUTION_EVIDENCE_MISMATCH" in result.quality_flags


@pytest.mark.parametrize("patch", [
    {"target_market_snapshot_id": 999},
    {"target_market_trade_date": DAY - timedelta(days=1)},
])
def test_aa_ab_market_relative_provenance_mismatch(patch):
    views = _views()
    views["relative_value"] = views["relative_value"].model_copy(update={
        "provenance": views["relative_value"].provenance.model_copy(update=patch)
    })
    result = _compose(views)
    assert "MARKET_RELATIVE_VALUE_EVIDENCE_MISMATCH" in result.quality_flags


@pytest.mark.parametrize("field,value", [
    ("target_yield_to_maturity_pct", D("12.1")),
    ("target_duration_years", D("2.1")),
    ("target_yield_to_maturity_pct", D("NaN")),
])
def test_ac_ad_market_relative_numeric_mismatch(field, value):
    views = _views()
    views["relative_value"] = views["relative_value"].model_copy(update={field: value})
    assert "MARKET_RELATIVE_VALUE_EVIDENCE_MISMATCH" in _compose(views).quality_flags


@pytest.mark.parametrize("patch", [
    {"credit_feature_contract_version": "wrong"}, {"credit_feature_bond_id": 2},
    {"credit_feature_as_of_date": DAY - timedelta(days=1)}, {"legal_issuer_id": 999},
])
def test_ae_credit_comparability_provenance_mismatch(patch):
    views = _views()
    views["credit_comparability"] = views["credit_comparability"].model_copy(update={
        "provenance": views["credit_comparability"].provenance.model_copy(update=patch)
    })
    assert "CREDIT_COMPARABILITY_EVIDENCE_MISMATCH" in _compose(views).quality_flags


@pytest.mark.parametrize("top_patch,provenance_patch", [
    ({"market_snapshot_id": 999}, {}),
    ({"market_trade_date": DAY - timedelta(days=1)}, {}),
    ({}, {"market_snapshot_id": 999}),
    ({}, {"market_source": "MOEX"}),
])
def test_af_ag_modified_duration_market_evidence(top_patch, provenance_patch):
    views = _views()
    md = views["modified_duration"]
    views["modified_duration"] = md.model_copy(update={
        **top_patch, "provenance": md.provenance.model_copy(update=provenance_patch)
    })
    assert "MODIFIED_DURATION_MARKET_EVIDENCE_MISMATCH" in _compose(views).quality_flags


@pytest.mark.parametrize("kind", ["snapshot", "status", "value", "provenance"])
def test_ah_aj_dv01_cross_evidence(kind):
    views = _views()
    dv = views["dv01"]
    if kind == "snapshot":
        dv = dv.model_copy(update={"market_snapshot_id": 999})
        expected = "DV01_MARKET_EVIDENCE_MISMATCH"
    elif kind == "status":
        dv = dv.model_copy(update={"modified_duration_status": "MARKET_DATA_MISSING"})
        expected = "DV01_MODIFIED_DURATION_EVIDENCE_MISMATCH"
    elif kind == "value":
        dv = dv.model_copy(update={"modified_duration_years": D("1.8")})
        expected = "DV01_MODIFIED_DURATION_EVIDENCE_MISMATCH"
    else:
        dv = dv.model_copy(update={
            "provenance": dv.provenance.model_copy(update={"market_snapshot_id": 999})
        })
        expected = "DV01_MARKET_EVIDENCE_MISMATCH"
    views["dv01"] = dv
    assert expected in _compose(views).quality_flags


@pytest.mark.parametrize("field,value", [
    ("relative_value_status", "CURVE_NOT_READY"),
    ("spread_to_ofz_pp", D("2")), ("spread_to_ofz_bps", D("200")),
    ("reference_ofz_yield_pct", D("10")), ("interpolation_method", "LINEAR_INTERPOLATION"),
    ("liquidity_status", "INSUFFICIENT_UNIVERSE"),
    ("liquidity_score_v1", D("70")), ("turnover_percentile", D("0")),
    ("trade_count_percentile", D("0")), ("recency_percentile", D("0")),
])
def test_ak_an_task274_exact_dependency_copy(field, value):
    views = _views()
    views["liquidity_relative_value"] = views["liquidity_relative_value"].model_copy(
        update={field: value}
    )
    assert "LIQUIDITY_RELATIVE_VALUE_EVIDENCE_MISMATCH" in _compose(views).quality_flags


def test_ao_ap_exact_zeros_and_matching_none_are_not_missing():
    views = _views()
    views["market"] = views["market"].model_copy(update={
        "yield_to_maturity_pct": D("0"), "duration_years": D("0")
    })
    views["relative_value"] = views["relative_value"].model_copy(update={
        "target_yield_to_maturity_pct": D("0"), "target_duration_years": D("0"),
        "spread_to_ofz_pp": D("0"), "spread_to_ofz_bps": D("0"),
        "reference_ofz_yield_pct": None, "interpolation_method": None,
    })
    views["liquidity"] = views["liquidity"].model_copy(update={
        "liquidity_score_v1": D("0"),
        "score_components": views["liquidity"].score_components.model_copy(update={
            "turnover_percentile": D("0"), "trade_count_percentile": D("0"),
            "recency_percentile": D("0"),
        }),
    })
    views["liquidity_relative_value"] = views["liquidity_relative_value"].model_copy(update={
        "spread_to_ofz_pp": D("0"), "spread_to_ofz_bps": D("0"),
        "reference_ofz_yield_pct": None, "interpolation_method": None,
        "liquidity_score_v1": D("0"), "turnover_percentile": D("0"),
        "trade_count_percentile": D("0"), "recency_percentile": D("0"),
    })
    assert _compose(views).status == "CONSISTENT"


@pytest.mark.parametrize("bond_id", [True, False, 0, -1, 1.0, "1"])
def test_request_bond_id_validation_precedes_feature_inspection(bond_id):
    views = _views()
    views["market"] = object()
    with pytest.raises(ValueError):
        _compose(views, bond_id=bond_id)


@pytest.mark.parametrize("as_of", [datetime(2026, 9, 19), "2026-09-19", None])
def test_request_date_validation(as_of):
    with pytest.raises(ValueError):
        _compose(as_of_date=as_of)


@pytest.mark.parametrize("source", ["", " ", None, 1])
def test_request_source_validation(source):
    with pytest.raises(ValueError):
        _compose(market_source=source)


@pytest.mark.parametrize("name", [
    "market", "relative_value", "credit", "credit_comparability", "liquidity",
    "modified_duration", "dv01", "liquidity_relative_value", "peer_distribution",
])
def test_dependency_arguments_require_exact_schema_types(name):
    views = _views()
    views[name] = object()
    with pytest.raises(ValueError):
        _compose(views)


def test_schema_is_frozen_extra_forbid_and_serialization_is_stable():
    result = _compose()
    with pytest.raises(ValidationError):
        result.bond_id = 2
    with pytest.raises(ValidationError):
        BondM3FeatureView(**result.model_dump(), unexpected=True)
    assert BondM3FeatureView.model_validate_json(result.model_dump_json()) == result
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert "NESTED_MARKET_FLAG" not in result.quality_flags
    assert result.capabilities.m3_composite_feature_view_ready is True
    assert result.capabilities.investment_score_ready is False
    assert result.capabilities.pit_ready is False


def test_inputs_and_decimal_context_are_unchanged():
    views = _views()
    before = deepcopy({name: value.model_dump() if value is not None else None for name, value in views.items()})
    original = getcontext().copy()
    changed = original.copy()
    changed.prec = 9
    changed.rounding = ROUND_DOWN
    setcontext(changed)
    try:
        _compose(views)
        assert getcontext().prec == 9 and getcontext().rounding == ROUND_DOWN
    finally:
        setcontext(original)
    after = {name: value.model_dump() if value is not None else None for name, value in views.items()}
    assert after == before


def test_composite_flags_are_sorted_unique_under_multiple_contradictions():
    views = _views()
    for name in (
        "market", "relative_value", "credit", "credit_comparability",
        "liquidity", "modified_duration", "dv01", "liquidity_relative_value",
    ):
        views[name] = views[name].model_copy(update={"bond_id": 2})
    views["market"] = views["market"].model_copy(update={
        "as_of_date": DAY - timedelta(days=1), "market_source": "MOEX",
        "pit_ready": True,
    })
    result = _compose(views)
    assert result.status == "EVIDENCE_INVALID"
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert {"BOND_IDENTITY_MISMATCH", "AS_OF_DATE_MISMATCH", "MARKET_SOURCE_MISMATCH",
            "PIT_CONTRACT_INVALID"}.issubset(result.quality_flags)
    assert not result.availability.all_supplied_evidence_consistent
    assert result.isin is None and result.secid is None


def test_static_pure_boundary_and_no_financial_formula_implementation():
    import app.services.bond_m3_feature_composer as module

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
    assert not any(
        name == "sqlalchemy" or name.startswith("sqlalchemy.")
        or name.startswith("app.models") or name.startswith("app.services")
        for name in imports
    )
    forbidden_calls = {
        "build_for_bond", "build_for_bonds", "build_curve", "evaluate_bond",
        "execute", "query", "add", "flush", "commit", "delete",
    }
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert forbidden_calls.isdisjoint(called)
    assert not any(
        isinstance(node, ast.BinOp) and not isinstance(node.op, ast.BitOr)
        for node in ast.walk(tree)
    )
    assert "http" not in source.lower() and "socket" not in source.lower()
