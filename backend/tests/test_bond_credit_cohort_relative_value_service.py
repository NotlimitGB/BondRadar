"""Task276 A–AU: atomic composition, contract drift and read-only acceptance."""

import ast
import hashlib
import inspect
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact
from app.models.legal_issuer import LegalIssuer
from app.schemas.bond_credit_cohort_relative_value import BondCreditCohortRelativeValueMemberView
from app.schemas.bond_credit_comparability import BondCreditComparabilityView, RatingComparabilityEntry
from app.schemas.ofz_reference_curve import BondRelativeValueView
from app.services.bond_credit_cohort_relative_value_service import BondCreditCohortRelativeValueMemberService
from app.services.bond_credit_cohort_relative_value_composer import (
    compose_credit_cohort_relative_value_member,
)
from app.services.bond_credit_comparability_service import BondCreditComparabilityService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService

DAY = date(2026, 9, 18)
NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
D = Decimal


def rating_entry(agency="ACRA", target="BOND", event_id=17, provider="CBR_RATINGS", scale=None, value="AA(RU)"):
    return RatingComparabilityEntry(
        target_kind=target, rating_agency=agency, status="READY", latest_event_date=DAY,
        event_count=1, event_ids=[event_id], selected_event_id=event_id, source_provider=provider,
        rating_scale_raw=scale, rating_value_raw=value, has_declared_rating_scale=bool(scale and scale.strip()),
        has_rating_value=True, cohort_key=dict(target_kind=target, rating_agency=agency,
            source_provider=provider, rating_scale_raw=scale, rating_value_raw=value),
        selected_event=dict(event_id=event_id, artifact_id=31, source_provider=provider,
            source_artifact_sha256="a" * 64, artifact_retrieved_at=NOW, source_object_id="native-object",
            rating_agency=agency, target_kind=target, event_date=DAY, publication_precision="UNKNOWN",
            publication_date=None, publication_at=None, rating_scale_raw=scale, rating_value_raw=value,
            rating_outlook_raw="Stable", rating_watch_raw=None, rating_action_raw=None,
            source_issuer_inn=None, source_bond_isin="RU000A123456", event_fingerprint="b" * 64),
        quality_flags=["PUBLICATION_TIME_UNKNOWN"],
    )


@pytest.fixture
def evidence(monkeypatch):
    credit = BondCreditComparabilityView(
        bond_id=1, isin="RU000A123456", secid="TASK276", as_of_date=DAY, status="READY",
        bond_rating_entries=[rating_entry()], issuer_rating_entries=[rating_entry(target="LEGAL_ISSUER", event_id=18)],
        availability=dict(has_bond_rating_evidence=True, has_issuer_rating_evidence=True,
            has_bond_comparable_cohort=True, has_issuer_comparable_cohort=True,
            bond_comparable_agencies=["ACRA"], issuer_comparable_agencies=["ACRA"], has_any_comparable_cohort=True),
        provenance=dict(credit_feature_contract_version="bond-credit-feature-v1", credit_feature_bond_id=1,
            credit_feature_as_of_date=DAY, as_of_date=DAY, bond_legal_issuer_profile_id=41, legal_issuer_id=51,
            issuer_link_status="VERIFIED", bond_rating_event_ids=[17], issuer_rating_event_ids=[18],
            selected_bond_rating_event_ids=[17], selected_issuer_rating_event_ids=[18],
            bond_rating_agencies=["ACRA"], issuer_rating_agencies=["ACRA"]),
        quality_flags=["RATING_PUBLICATION_UNKNOWN", "UNRELATED_TOP_FLAG"],
    )
    relative = BondRelativeValueView(
        bond_id=1, isin="RU000A123456", secid="TASK276", as_of_date=DAY, market_source="moex", status="READY",
        target_yield_to_maturity_pct=D("14.375"), target_duration_years=D("2.5"),
        reference_ofz_yield_pct=D("12.875"), spread_to_ofz_pp=D("1.5"), spread_to_ofz_bps=D("150"),
        interpolation_method="LINEAR_INTERPOLATION",
        curve=dict(as_of_date=DAY, market_source="moex", status="READY", curve_trade_date=DAY,
            node_count=2, min_duration_years=D(1), max_duration_years=D(3), nodes=[], diagnostics={}),
        provenance=dict(target_market_snapshot_id=61, target_market_trade_date=DAY, curve_trade_date=DAY),
        quality_flags=["CURVE_DUPLICATE_DURATION_AGGREGATED", "UNRELATED_RELATIVE_FLAG"],
    )
    state = SimpleNamespace(credit=credit, relative=relative, calls=[])

    def read_credit(service, *args, **kwargs):
        assert service.db.autoflush is False
        state.calls.append(("CREDIT", args, kwargs))
        return state.credit

    def read_relative(service, *args, **kwargs):
        assert service.db.autoflush is False
        state.calls.append(("RELATIVE", args, kwargs))
        return state.relative

    monkeypatch.setattr(BondCreditComparabilityService, "build_for_bond", read_credit)
    monkeypatch.setattr(OfzReferenceCurveService, "evaluate_bond", read_relative)
    return state


def build(db, **kwargs):
    arguments = dict(target_kind="BOND", rating_agency="ACRA")
    arguments.update(kwargs)
    return BondCreditCohortRelativeValueMemberService(db).build_for_bond(1, DAY, **arguments)


def unavailable(result, status):
    assert result.status == status
    assert not result.availability.has_credit_cohort_relative_value_member
    assert result.capabilities.credit_cohort_relative_value_member_ready is True
    assert result.pit_ready is False
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert BondCreditCohortRelativeValueMemberView.model_validate_json(result.model_dump_json()) == result


def test_task279_service_delegates_composition_once(db_session, evidence, monkeypatch):
    import app.services.bond_credit_cohort_relative_value_service as module
    original = module.compose_credit_cohort_relative_value_member
    calls = []
    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "compose_credit_cohort_relative_value_member", spy)
    result = build(db_session)
    assert result.status == "READY" and len(calls) == 1
    assert calls[0][0] == (evidence.credit, evidence.relative)
    assert calls[0][1] == dict(
        bond_id=1, as_of_date=DAY, target_kind="BOND",
        rating_agency="ACRA", market_source="moex")


@pytest.mark.parametrize("state", [
    "READY", "SELECTED_COHORT_MISSING", "SELECTED_COHORT_UNAVAILABLE",
    "CREDIT_COHORT_EVIDENCE_INVALID", "CREDIT_COMPARABILITY_UNAVAILABLE",
    "RELATIVE_VALUE_UNAVAILABLE", "RELATIVE_VALUE_EVIDENCE_INVALID",
    "DEPENDENCY_IDENTITY_MISMATCH",
])
def test_task279_direct_composer_full_equivalence(db_session, evidence, state):
    if state == "SELECTED_COHORT_MISSING":
        evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": []})
    elif state == "SELECTED_COHORT_UNAVAILABLE":
        evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [
            rating_entry().model_copy(update={"status": "RATING_VALUE_MISSING", "cohort_key": None})]})
    elif state == "CREDIT_COHORT_EVIDENCE_INVALID":
        evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [
            rating_entry(), rating_entry(event_id=99)]})
    elif state == "CREDIT_COMPARABILITY_UNAVAILABLE":
        evidence.credit = evidence.credit.model_copy(update={"status": "DEPENDENCY_EVIDENCE_INVALID"})
    elif state == "RELATIVE_VALUE_UNAVAILABLE":
        evidence.relative = evidence.relative.model_copy(update={"status": "CURVE_NOT_READY"})
    elif state == "RELATIVE_VALUE_EVIDENCE_INVALID":
        evidence.relative = evidence.relative.model_copy(update={"spread_to_ofz_bps": D("NaN")})
    elif state == "DEPENDENCY_IDENTITY_MISMATCH":
        evidence.relative = evidence.relative.model_copy(update={"bond_id": 2})
    direct = compose_credit_cohort_relative_value_member(
        evidence.credit, evidence.relative, bond_id=1, as_of_date=DAY,
        target_kind="BOND", rating_agency="ACRA", market_source="moex")
    delegated = build(db_session)
    assert direct.status == state
    assert delegated.model_dump() == direct.model_dump()


@pytest.mark.parametrize("target,event_id", [("BOND", 17), ("LEGAL_ISSUER", 18)])
def test_a_b_h_selected_target_and_exact_key(db_session, evidence, target, event_id):
    result = build(db_session, target_kind=target)
    assert result.status == "READY" and all(result.availability.model_dump().values())
    selected = evidence.credit.bond_rating_entries[0] if target == "BOND" else evidence.credit.issuer_rating_entries[0]
    assert result.cohort_key == selected.cohort_key and result.rating_event_id == event_id
    assert result.requested_target_kind == result.cohort_key.target_kind == target
    assert result.requested_rating_agency == "ACRA"
    assert result.rating_source_provider == "CBR_RATINGS" and result.rating_scale_raw is None
    assert result.rating_event_date == DAY and result.rating_value_raw == "AA(RU)"
    assert result.provenance.selected_rating_event_id == event_id
    assert result.provenance.bond_legal_issuer_profile_id == 41 and result.provenance.legal_issuer_id == 51


@pytest.mark.parametrize("agency", ["ACRA", "EXPERT_RA", "NRA", "NKR"])
def test_c_explicit_agency_only_no_preference(db_session, evidence, agency):
    entries = [rating_entry(a, event_id=i + 20) for i, a in enumerate(["NRA", "NKR", "EXPERT_RA", "ACRA"])]
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": entries})
    result = build(db_session, rating_agency=agency)
    assert result.status == "READY"
    assert result.cohort_key.rating_agency == agency
    assert result.rating_event_id == next(e.selected_event_id for e in entries if e.rating_agency == agency)


@pytest.mark.parametrize("family_empty,agency", [(True, "ACRA"), (False, "NKR")])
def test_d_l_missing_selected_agency_no_family_fallback(db_session, evidence, family_empty, agency):
    if family_empty:
        evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": []})
    result = build(db_session, rating_agency=agency)
    unavailable(result, "SELECTED_COHORT_MISSING")
    assert result.cohort_key is result.rating_event_id is None
    assert not result.availability.has_selected_rating_entry
    assert result.availability.has_credit_comparability and result.availability.has_relative_value
    assert result.spread_to_ofz_bps == D(150)


@pytest.mark.parametrize("field,values", [
    ("target_kind", ["bond", " BOND", "ISSUER", "", None, True, 1]),
    ("rating_agency", ["acra", " ACRA", "MOEX", "", None, True, 1]),
])
def test_e_f_invalid_selector_before_calls(db_session, evidence, field, values):
    for value in values:
        with pytest.raises(ValueError):
            build(db_session, **{field: value})
    assert evidence.calls == []


def test_g_required_keyword_selectors_no_defaults(db_session, evidence):
    method = BondCreditCohortRelativeValueMemberService(db_session).build_for_bond
    signature = inspect.signature(method)
    for name in ("target_kind", "rating_agency"):
        assert signature.parameters[name].default is inspect.Parameter.empty
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    for arguments in ({}, {"target_kind": "BOND"}, {"rating_agency": "ACRA"}):
        with pytest.raises(TypeError):
            method(1, DAY, **arguments)
    assert evidence.calls == []


@pytest.mark.parametrize("scale", [None, "", " \t ", " Russian Native Scale (RU) "])
@pytest.mark.parametrize("provider,value", [("CBR_RATINGS", "AA(RU)"), ("ACRA", " ruAA+ "), (" Native Provider ", "ААА")])
def test_i_j_k_exact_native_strings_no_normalization(db_session, evidence, scale, provider, value):
    entry = rating_entry(scale=scale, provider=provider, value=value)
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [entry]})
    result = build(db_session)
    assert result.status == "READY"
    assert result.cohort_key.model_dump() == entry.cohort_key.model_dump()
    assert result.rating_scale_raw == result.provenance.selected_rating_scale_raw == scale
    assert result.rating_value_raw == result.provenance.selected_rating_value_raw == value
    assert result.rating_source_provider == result.provenance.selected_source_provider == provider


@pytest.mark.parametrize("status,diagnostic", [
    ("MULTIPLE_LATEST_EVENTS", "MULTIPLE_LATEST_EVENTS"), ("RATING_VALUE_MISSING", "RATING_VALUE_MISSING"),
    ("EVIDENCE_INVALID", "EVIDENCE_INVALID"),
])
def test_m_n_unavailable_selected_entry_keeps_relevant_diagnostics(db_session, evidence, status, diagnostic):
    entry = evidence.credit.bond_rating_entries[0].model_copy(update={"status": status, "cohort_key": None,
        "rating_value_raw": None, "quality_flags": [diagnostic, "PUBLICATION_TIME_UNKNOWN", "BANK_FLAG"]})
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [entry]})
    result = build(db_session)
    unavailable(result, "SELECTED_COHORT_UNAVAILABLE")
    assert result.cohort_key is None
    assert result.availability.has_selected_rating_entry and not result.availability.has_selected_cohort_key
    assert "PUBLICATION_TIME_UNKNOWN" in result.quality_flags and "BANK_FLAG" not in result.quality_flags
    assert result.rating_event_id == entry.selected_event_id


def test_o_globally_invalid_credit_never_uses_surviving_key(db_session, evidence):
    evidence.credit = evidence.credit.model_copy(update={"status": "DEPENDENCY_EVIDENCE_INVALID"})
    result = build(db_session)
    unavailable(result, "CREDIT_COMPARABILITY_UNAVAILABLE")
    assert result.cohort_key is None and not result.availability.has_selected_cohort_key
    assert not result.availability.has_credit_comparability
    assert result.rating_event_id == 17 and result.spread_to_ofz_bps == D(150)


@pytest.mark.parametrize("field,value", [
    ("target_kind", "LEGAL_ISSUER"), ("rating_agency", "EXPERT_RA"),
    ("source_provider", "ACRA"), ("rating_scale_raw", "Other scale"),
    ("rating_value_raw", "ruAA"), ("rating_value_raw", None), ("rating_value_raw", 1),
])
def test_p_q_r_key_contradictions_fail_closed(db_session, evidence, field, value):
    entry = evidence.credit.bond_rating_entries[0]
    entry = entry.model_copy(update={"cohort_key": entry.cohort_key.model_copy(update={field: value})})
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [entry]})
    result = build(db_session)
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")
    assert result.cohort_key is None and result.availability.has_relative_value


@pytest.mark.parametrize("patch", [
    {"cohort_key": None}, {"selected_event_id": None}, {"selected_event_id": True},
    {"source_provider": None}, {"source_provider": " \t"}, {"rating_value_raw": " "},
    {"rating_value_raw": 123}, {"rating_scale_raw": 123}, {"target_kind": "LEGAL_ISSUER"},
])
def test_ready_selected_entry_must_have_consistent_usable_key(db_session, evidence, patch):
    entry = evidence.credit.bond_rating_entries[0].model_copy(update=patch)
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [entry]})
    result = build(db_session)
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")
    assert result.cohort_key is None


@pytest.mark.parametrize("pp,bps", [("1.5", "150"), ("0", "0"), ("-0.4", "-40"),
    ("0.123456789123456789123456789", "12.3456789123456789123456789")])
@pytest.mark.parametrize("method", ["EXACT_NODE", "LINEAR_INTERPOLATION"])
def test_s_to_v_z_aa_exact_decimal_and_provenance_copy(db_session, evidence, pp, bps, method):
    evidence.relative = evidence.relative.model_copy(update={"spread_to_ofz_pp": D(pp),
        "spread_to_ofz_bps": D(bps), "interpolation_method": method})
    result = build(db_session)
    assert result.status == "READY"
    assert result.spread_to_ofz_pp.as_tuple() == D(pp).as_tuple()
    assert result.spread_to_ofz_bps.as_tuple() == D(bps).as_tuple()
    for field in ("target_yield_to_maturity_pct", "target_duration_years", "reference_ofz_yield_pct", "interpolation_method"):
        assert getattr(result, field) == getattr(evidence.relative, field)
    assert result.market_snapshot_id == result.provenance.target_market_snapshot_id == 61
    assert result.market_trade_date == result.provenance.target_market_trade_date == DAY
    assert result.curve_trade_date == result.provenance.curve_trade_date == DAY
    assert result.provenance.credit_comparability_contract_version == "bond-credit-comparability-v1"
    assert result.provenance.relative_value_contract_version == "bond-relative-value-v1"
    assert result.provenance.ofz_curve_contract_version == "ofz-reference-curve-v1"


@pytest.mark.parametrize("status", ["TARGET_MARKET_MISSING", "TARGET_MARKET_STALE", "TARGET_YIELD_MISSING",
    "TARGET_DURATION_MISSING", "CURVE_NOT_READY", "TARGET_CURVE_DATE_MISMATCH", "TARGET_DURATION_OUTSIDE_CURVE"])
def test_w_relative_unavailable_keeps_credit_and_market_inputs(db_session, evidence, status):
    evidence.relative = evidence.relative.model_copy(update={"status": status, "reference_ofz_yield_pct": None,
        "spread_to_ofz_pp": None, "spread_to_ofz_bps": None, "interpolation_method": None})
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
    assert result.cohort_key == evidence.credit.bond_rating_entries[0].cohort_key
    assert result.rating_event_id == 17 and result.target_duration_years == D("2.5")
    assert result.market_snapshot_id == 61
    assert result.availability.has_selected_cohort_key and not result.availability.has_spread_to_ofz
    assert "RELATIVE_VALUE_" + status in result.quality_flags
    assert "UNRELATED_RELATIVE_FLAG" not in result.quality_flags


@pytest.mark.parametrize("field", ["spread_to_ofz_pp", "spread_to_ofz_bps"])
@pytest.mark.parametrize("value", [None, D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"), 1, 1.5, "1.5", True])
def test_x_y_ready_relative_requires_finite_decimal_spreads(db_session, evidence, field, value):
    evidence.relative = evidence.relative.model_copy(update={field: value})
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_EVIDENCE_INVALID")
    assert getattr(result, field) is None
    assert result.cohort_key is not None and not result.availability.has_relative_value


@pytest.mark.parametrize("side", ["credit", "relative"])
@pytest.mark.parametrize("patch", [{"bond_id": 99}, {"bond_id": True}, {"bond_id": "1"},
    {"as_of_date": DAY - timedelta(days=1)}, {"as_of_date": datetime(2026, 9, 18)}])
def test_ab_ac_af_identity_mismatch_invalidates_only_attribution(db_session, evidence, side, patch):
    setattr(evidence, side, getattr(evidence, side).model_copy(update=patch))
    result = build(db_session)
    unavailable(result, "DEPENDENCY_IDENTITY_MISMATCH")
    assert result.isin is result.secid is None
    if side == "credit":
        assert result.cohort_key is result.rating_event_id is None
        assert result.spread_to_ofz_bps == D(150)
        assert result.availability.has_relative_value
    else:
        assert result.spread_to_ofz_bps is result.market_snapshot_id is None
        assert result.cohort_key is not None and result.availability.has_selected_cohort_key
    identity = result.provenance.credit_identity if side == "credit" else result.provenance.relative_value_identity
    if "bond_id" in patch:
        assert identity.bond_id == (patch["bond_id"] if type(patch["bond_id"]) is int else None)
    if "as_of_date" in patch:
        assert identity.as_of_date == (patch["as_of_date"] if type(patch["as_of_date"]) is date else None)


def test_ad_exact_market_source_mismatch(db_session, evidence):
    evidence.relative = evidence.relative.model_copy(update={"market_source": "MOEX"})
    result = build(db_session)
    unavailable(result, "DEPENDENCY_IDENTITY_MISMATCH")
    assert result.provenance.relative_value_identity.market_source == "MOEX"
    assert result.spread_to_ofz_bps is None


def test_two_wrong_dependency_identities_cannot_agree_into_ready(db_session, evidence):
    evidence.credit = evidence.credit.model_copy(update={"bond_id": 99})
    evidence.relative = evidence.relative.model_copy(update={"bond_id": 99})
    result = build(db_session)
    unavailable(result, "DEPENDENCY_IDENTITY_MISMATCH")
    assert result.cohort_key is result.spread_to_ofz_pp is result.market_snapshot_id is None


@pytest.mark.parametrize("patch", [{"contract_version": "wrong"}, {"pit_ready": True}, {"status": "UNKNOWN"}])
def test_ae_credit_contract_fail_closed(db_session, evidence, patch):
    evidence.credit = evidence.credit.model_copy(update=patch)
    result = build(db_session)
    unavailable(result, "CREDIT_COMPARABILITY_UNAVAILABLE")
    assert result.cohort_key is None and result.spread_to_ofz_bps == D(150)


@pytest.mark.parametrize("side,patch", [("relative", {"contract_version": "wrong"}),
    ("relative", {"pit_ready": True}), ("curve", {"contract_version": "wrong"}), ("curve", {"pit_ready": True})])
def test_relative_contract_invalid_is_not_a_ready_member(db_session, evidence, side, patch):
    if side == "curve":
        evidence.relative = evidence.relative.model_copy(update={"curve": evidence.relative.curve.model_copy(update=patch)})
    else:
        evidence.relative = evidence.relative.model_copy(update=patch)
    result = build(db_session)
    unavailable(result, "RELATIVE_VALUE_EVIDENCE_INVALID")
    assert "RELATIVE_VALUE_CONTRACT_INVALID" in result.quality_flags


def test_ag_duplicate_selected_entries_no_arbitrary_choice(db_session, evidence):
    evidence.credit = evidence.credit.model_copy(update={"bond_rating_entries": [rating_entry(event_id=17), rating_entry(event_id=19)]})
    result = build(db_session)
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")
    assert result.cohort_key is result.rating_event_id is None
    assert result.provenance.selected_entry_count == 2 and result.provenance.selected_rating_event_id is None
    assert not result.availability.has_selected_rating_entry


@pytest.mark.parametrize("credit_change,relative_change,expected", [
    ({"bond_id": 99, "status": "DEPENDENCY_EVIDENCE_INVALID"}, {"status": "CURVE_NOT_READY"}, "DEPENDENCY_IDENTITY_MISMATCH"),
    ({"status": "DEPENDENCY_EVIDENCE_INVALID", "bond_rating_entries": []}, {"status": "CURVE_NOT_READY"}, "CREDIT_COMPARABILITY_UNAVAILABLE"),
    ({"bond_rating_entries": [rating_entry(), rating_entry(event_id=19)]}, {"status": "CURVE_NOT_READY"}, "CREDIT_COHORT_EVIDENCE_INVALID"),
    ({"bond_rating_entries": []}, {"status": "CURVE_NOT_READY"}, "SELECTED_COHORT_MISSING"),
    ({"bond_rating_entries": [rating_entry().model_copy(update={"status": "RATING_VALUE_MISSING", "cohort_key": None})]},
     {"spread_to_ofz_pp": None}, "SELECTED_COHORT_UNAVAILABLE"),
    ({}, {"status": "CURVE_NOT_READY", "spread_to_ofz_pp": None}, "RELATIVE_VALUE_UNAVAILABLE"),
])
def test_status_precedence_simultaneous_limits(db_session, evidence, credit_change, relative_change, expected):
    evidence.credit = evidence.credit.model_copy(update=credit_change)
    evidence.relative = evidence.relative.model_copy(update=relative_change)
    result = build(db_session)
    unavailable(result, expected)
    assert expected in result.quality_flags


def test_valid_no_comparable_credit_distinct_from_dependency_failure(db_session, evidence):
    evidence.credit = evidence.credit.model_copy(update={"status": "NO_COMPARABLE_RATING", "bond_rating_entries": []})
    result = build(db_session)
    unavailable(result, "SELECTED_COHORT_MISSING")
    assert result.availability.has_credit_comparability


def test_ah_ai_forwarding_exact_source_calls_once_and_no_own_db_read(db_session, evidence, monkeypatch):
    source = " manual "
    evidence.relative = evidence.relative.model_copy(update={"market_source": source})
    db_session.autoflush = True
    for name in ("get", "query", "execute"):
        monkeypatch.setattr(db_session, name, lambda *a, **k: pytest.fail("own DB read"))
    result = build(db_session, market_source=source, max_market_age_days=0, max_curve_age_days=3)
    assert result.status == "READY" and db_session.autoflush is True
    assert evidence.calls == [
        ("CREDIT", (1, DAY), {}),
        ("RELATIVE", (1, DAY), {"market_source": source, "max_market_age_days": 0, "max_curve_age_days": 3}),
    ]


@pytest.mark.parametrize("bond_id", [True, False, 0, -1, 1.0, "1", None])
def test_invalid_bond_argument_before_any_call(db_session, evidence, bond_id):
    with pytest.raises(ValueError):
        BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(bond_id, DAY, target_kind="BOND", rating_agency="ACRA")
    assert evidence.calls == []


@pytest.mark.parametrize("when", [datetime(2026, 9, 18), datetime(2026, 9, 18, tzinfo=timezone.utc), "2026-09-18", None, True])
def test_invalid_date_argument_before_any_call(db_session, evidence, when):
    with pytest.raises(ValueError):
        BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(1, when, target_kind="BOND", rating_agency="ACRA")
    assert evidence.calls == []


@pytest.mark.parametrize("field,values", [("market_source", [None, "", " \t", True, 1]),
    ("max_market_age_days", [None, True, -1, 1.0, "1"]), ("max_curve_age_days", [None, True, -1, 1.0, "1"])])
def test_invalid_market_arguments_before_any_call(db_session, evidence, field, values):
    for value in values:
        with pytest.raises(ValueError):
            build(db_session, **{field: value})
    assert evidence.calls == []


def test_au_input_immutability_decimal_context_and_stable_serialization(db_session, evidence):
    before = (evidence.credit.model_dump(), evidence.relative.model_dump())
    normal = build(db_session)
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)) as caller:
        result = build(db_session)
        assert caller.prec == 3 and caller.rounding == ROUND_DOWN
    assert result.model_dump_json() == normal.model_dump_json()
    assert before == (evidence.credit.model_dump(), evidence.relative.model_dump())
    assert result.cohort_key is not evidence.credit.bond_rating_entries[0].cohort_key
    assert result.quality_flags == ["PUBLICATION_TIME_UNKNOWN"]
    assert "curve" not in result.model_dump() and "selected_event" not in result.provenance.model_dump()
    assert "artifact" not in result.model_dump_json()


def test_schema_frozen_extra_forbid_and_at_false_pit(db_session, evidence):
    result = build(db_session)
    for model in (result, result.availability, result.provenance, result.provenance.credit_identity,
                  result.provenance.relative_value_identity, result.capabilities, result.cohort_key):
        assert model.model_config["frozen"] and model.model_config["extra"] == "forbid"
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unrequested": True})
    with pytest.raises(ValidationError):
        result.status = "READY"
    with pytest.raises(ValidationError):
        BondCreditCohortRelativeValueMemberView.model_validate({**result.model_dump(), "pit_ready": True})
    assert {key for key, value in result.capabilities.model_dump().items() if value} == {
        "source_native_rating_cohort_input_ready", "spread_to_ofz_input_ready", "credit_cohort_relative_value_member_ready",
    }


@pytest.fixture
def seed(db_session):
    counter = 0

    def digest():
        nonlocal counter
        counter += 1
        return hashlib.sha256(str(counter).encode()).hexdigest()

    def save(row):
        db_session.add(row)
        db_session.flush()
        return row

    company = save(Company(name="Task276 fixture", ticker="TASK276"))

    def bond(name="Corporate", duration="2", ytm="14", when=DAY, source="moex", market=True):
        row = save(Bond(company_id=company.id, name=name, secid=digest()[:20],
            isin="RU" + digest()[:10], yield_to_maturity=D(99), duration_years=D(99), liquidity_score=7))
        profile = save(BondSecurityMasterProfile(bond_id=row.id, currency_state="verified", currency_code="RUB",
            coupon_structure="fixed", amortization_structure="bullet", perpetual_structure="dated",
            maturity_state="verified", maturity_date=DAY + timedelta(days=100)))
        snapshot = save(BondMarketSnapshot(bond_id=row.id, trade_date=when, source=source,
            yield_to_maturity=D(ytm), duration_years=D(duration), spread_to_ofz=D("999.999"),
            raw_payload={"moex": {"DURATION": str(D(duration) * D(365))}})) if market else None
        return SimpleNamespace(bond=row, profile=profile, snapshot=snapshot)

    target = bond()

    def rating(target_kind="BOND", value="AA(RU)"):
        issuer = profile = None
        if target_kind == "LEGAL_ISSUER":
            issuer = save(LegalIssuer(source_issuer_id=digest(), resolution_state="verified",
                issuer_title="Fixture issuer", issuer_inn="7701234567"))
            profile = save(BondLegalIssuerProfile(bond_id=target.bond.id, mapping_state="verified",
                mapping_source="moex_security_reference", source_issuer_id=issuer.source_issuer_id,
                issuer_title=issuer.issuer_title, security_match_status="EXACT_SECID"))
        artifact = save(CreditRiskSourceArtifact(source_provider="CBR_RATINGS", source_kind="RATING_REPOSITORY_RESPONSE",
            source_url="https://example.invalid/" + digest(), content_bytes=b"fixture", content_sha256=digest(),
            retrieved_at=NOW, content_type="application/json"))
        row = save(CreditRatingEvent(artifact_id=artifact.id, rating_agency="ACRA", target_kind=target_kind,
            resolution_state="RESOLVED", bond_id=target.bond.id if target_kind == "BOND" else None,
            legal_issuer_id=issuer.id if issuer else None, source_bond_isin=target.bond.isin if target_kind == "BOND" else None,
            source_issuer_inn=issuer.issuer_inn if issuer else None,
            source_object_id=digest(), event_date=DAY, publication_precision="UNKNOWN", rating_scale_raw=None,
            rating_value_raw=value, rating_action_raw="Native action", event_fingerprint=digest()))
        return SimpleNamespace(row=row, artifact=artifact, issuer=issuer, profile=profile)

    return SimpleNamespace(target=target, bond=bond, rating=rating, company=company, save=save)


@pytest.mark.parametrize("target_kind", ["BOND", "LEGAL_ISSUER"])
def test_sqlite_integration_real_dependencies(db_session, seed, target_kind):
    seed.bond(name="ОФЗ-ПД", duration="1", ytm="10")
    seed.bond(name="ОФЗ-ПД", duration="3", ytm="12")
    rating = seed.rating(target_kind)
    result = BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(seed.target.bond.id, DAY,
        target_kind=target_kind, rating_agency="ACRA")
    assert result.status == "READY" and result.spread_to_ofz_pp == D(3) and result.spread_to_ofz_bps == D(300)
    assert result.target_duration_years == D(2) and result.reference_ofz_yield_pct == D(11)
    assert result.interpolation_method == "LINEAR_INTERPOLATION"
    assert result.rating_event_id == rating.row.id and result.market_snapshot_id == seed.target.snapshot.id
    if target_kind == "LEGAL_ISSUER":
        assert result.provenance.legal_issuer_id == rating.issuer.id
        assert result.provenance.bond_legal_issuer_profile_id == rating.profile.id
    assert seed.target.snapshot.spread_to_ofz == D("999.999")
    assert seed.target.bond.liquidity_score == 7 and seed.target.bond.duration_years == D(99)


def test_sqlite_no_evidence_and_stable_unavailable(db_session, seed):
    result = BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(seed.target.bond.id, DAY,
        target_kind="BOND", rating_agency="ACRA")
    unavailable(result, "SELECTED_COHORT_MISSING")
    assert result.availability.has_credit_comparability
    assert not result.availability.has_selected_cohort_key and not result.availability.has_relative_value
    assert result.target_duration_years == D(2) and result.spread_to_ofz_bps is None


def test_missing_bond_http_404(db_session):
    with pytest.raises(HTTPException) as exc:
        BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(999999, DAY,
            target_kind="BOND", rating_agency="ACRA")
    assert exc.value.status_code == 404 and exc.value.detail == "Bond not found"


def test_aq_ar_select_only_pending_state_and_source_rows(db_session, seed, monkeypatch):
    seed.bond(name="ОФЗ-ПД", duration="1", ytm="10")
    seed.bond(name="ОФЗ-ПД", duration="3", ytm="12")
    rating = seed.rating()
    doomed = seed.save(Bond(company_id=seed.company.id, name="Caller deleted", isin="RU000A654321"))
    db_session.commit()
    # Load source values before enabling autoflush and creating caller pending state.
    before_source = (deepcopy(seed.target.snapshot.raw_payload), seed.target.snapshot.spread_to_ofz,
        rating.row.rating_value_raw, rating.row.event_fingerprint, rating.artifact.content_bytes,
        seed.target.bond.duration_years, seed.target.bond.liquidity_score)
    bond_id = seed.target.bond.id
    db_session.delete(doomed)
    pending = Company(name="Caller new", ticker="CALLER")
    db_session.add(pending)
    seed.target.bond.name = "Caller dirty"
    db_session.autoflush = True
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    for name in ("add", "add_all", "flush", "commit", "delete", "bulk_save_objects"):
        monkeypatch.setattr(db_session, name, lambda *a, **k: pytest.fail("mutation call"))
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(bond_id, DAY,
            target_kind="BOND", rating_agency="ACRA")
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.status == "READY"
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any("content_bytes" in sql for sql in statements)
    assert (set(db_session.new), set(db_session.dirty), set(db_session.deleted)) == before
    assert pending.id is None and db_session.autoflush is True
    assert before_source == (seed.target.snapshot.raw_payload, seed.target.snapshot.spread_to_ofz,
        rating.row.rating_value_raw, rating.row.event_fingerprint, rating.artifact.content_bytes,
        seed.target.bond.duration_years, seed.target.bond.liquidity_score)


def test_aj_to_ap_as_static_safety():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app/services/bond_credit_cohort_relative_value_service.py").read_text(encoding="utf-8")
    schema = (root / "app/schemas/bond_credit_cohort_relative_value.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(any(p in name for p in ("models", "requests", "httpx", "urllib", "socket", "ingest", "strategy", "portfolio", "risk_engine")) for name in imports)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"CreditRatingEvent", "BondCreditFeatureService", "BondMarketSnapshot", "select"}
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert sum(isinstance(n.func, ast.Attribute) and n.func.attr == "build_for_bond" for n in calls) == 1
    assert sum(isinstance(n.func, ast.Attribute) and n.func.attr == "evaluate_bond" for n in calls) == 1
    assert sum(isinstance(n.func, ast.Name)
               and n.func.id == "compose_credit_cohort_relative_value_member" for n in calls) == 1
    assert "_key_valid" not in source
    assert "BondCreditCohortRelativeValueMemberProvenance" not in source
    # PEP 604 type unions use BinOp(BitOr); they are annotations, not arithmetic.
    assert not any(isinstance(n, ast.BinOp) and not isinstance(n.op, ast.BitOr) for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Dict) and n.keys for n in ast.walk(tree))
    assert not any(isinstance(n.func, ast.Attribute) and n.func.attr in {"get", "execute", "query", "flush", "commit", "delete"} for n in calls)
    for name in ("primary_rating", "preferred_rating", "best_rating", "worst_rating", "strongest_rating", "weakest_rating",
                 "cohort_member_count", "cohort_median_spread", "cohort_mean_spread", "cohort_percentile", "peer_rank", "spread_z_score"):
        assert name not in source + schema
