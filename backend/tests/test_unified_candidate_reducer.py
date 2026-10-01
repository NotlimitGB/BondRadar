"""Task298 synthetic CORE projection and fail-closed envelope tests."""

import ast
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, getcontext, setcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.m3_audit_snapshot import M3AuditSnapshotView
from app.schemas.m3_audit_snapshot import M3AuditSnapshotDiagnostics
from app.schemas.unified_candidate import UnifiedCandidateBatchView
from app.services.m3_coverage_audit_reducer import M3CoverageAuditReducer
from app.services.unified_candidate_reducer import CORE_REASONS, UnifiedCandidateReducer
import test_bond_m3_feature_composer as H
import test_bond_credit_comparability_service as C

DAY = H.DAY
D = Decimal


def composite(bond_id=1):
    m3 = H._compose()
    def identity(value):
        if isinstance(value, dict):
            return {key: bond_id if key in {"bond_id", "requested_bond_id", "credit_feature_bond_id"}
                    else identity(item) for key, item in value.items()}
        if isinstance(value, list):
            return [identity(item) for item in value]
        return value
    data = identity(m3.model_dump())
    data["dv01"]["nkd_currency"] = data["market"]["nkd"]
    event = C.native_event(value=" AA+(RU) ", scale=" Native Scale ", provider="Native Provider",
        event_date=DAY)
    group = C.group(event, latest_event_date=DAY)
    data["credit"]["bond_ratings"] = [group.model_dump()]
    key = dict(target_kind="BOND", rating_agency="ACRA", source_provider=event.source_provider,
        rating_scale_raw=event.rating_scale_raw, rating_value_raw=event.rating_value_raw)
    data["credit_comparability"]["bond_rating_entries"] = [dict(target_kind="BOND", rating_agency="ACRA",
        status="READY", latest_event_date=DAY, event_count=1, event_ids=[event.event_id], selected_event_id=event.event_id,
        source_provider=event.source_provider, rating_scale_raw=event.rating_scale_raw,
        rating_value_raw=event.rating_value_raw, has_declared_rating_scale=True, has_rating_value=True,
        cohort_key=key, selected_event=event.model_dump(), quality_flags=[])]
    return type(m3).model_validate(data)


def snapshot(composites=None, missing=()):
    composites = [composite()] if composites is None else sorted(composites, key=lambda c: c.bond_id)
    existing = [c.bond_id for c in composites]
    missing = sorted(missing)
    requested = sorted(existing + missing)
    rows = {c.bond_id: c for c in composites}
    consistent = sum(c.status == "CONSISTENT" for c in composites)
    calls = {name: 0 for name in M3AuditSnapshotDiagnostics.model_fields}
    return M3AuditSnapshotView(as_of_date=DAY, market_source="moex", max_market_age_days=7,
        max_curve_age_days=7, liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5,
        status="NO_EXISTING_BONDS" if not existing else "PARTIAL" if missing else "COMPLETE",
        requested_bond_ids=requested, existing_bond_ids=existing, missing_bond_ids=missing,
        requested_bond_count=len(requested), existing_bond_count=len(existing), missing_bond_count=len(missing),
        composite_count=len(existing), consistent_composite_count=consistent,
        invalid_composite_count=len(existing)-consistent,
        items=[dict(bond_id=i, build_status="BUILT" if i in rows else "BOND_NOT_FOUND", composite=rows.get(i)) for i in requested],
        coverage_audit=M3CoverageAuditReducer.build(composites) if composites else None,
        diagnostics=calls, provenance=dict(shared_ofz_curve_contract_version="ofz-reference-curve-v1" if existing else None,
            shared_ofz_curve_status="READY" if existing else None, shared_ofz_curve_trade_date=DAY if existing else None,
            shared_ofz_curve_node_count=2 if existing else None, requested_bond_ids=requested,
            existing_bond_ids=existing, missing_bond_ids=missing))


def incomplete(c, name):
    a = c.availability.model_copy(update={name: False})
    updates = {"availability": a}
    child_status = {
        "market_fresh": ("market", "market_status", "STALE"),
        "relative_value_ready": ("relative_value", "status", "CURVE_NOT_READY"),
        "liquidity_score_ready": ("liquidity", "score_status", "INSUFFICIENT_UNIVERSE"),
        "modified_duration_ready": ("modified_duration", "status", "SECURITY_MASTER_MISSING"),
        "dv01_ready": ("dv01", "status", "SECURITY_MASTER_MISSING"),
        "liquidity_relative_value_ready": ("liquidity_relative_value", "status", "LIQUIDITY_UNAVAILABLE"),
    }
    if name in child_status:
        child, field, status = child_status[name]
        updates[child] = getattr(c, child).model_copy(update={field: status})
    elif name == "has_credit_rating_evidence":
        credit = c.credit
        updates["credit"] = credit.model_copy(update={"availability": credit.availability.model_copy(update={"has_bond_rating_event": False, "has_issuer_rating_event": False})})
    elif name == "has_credit_comparable_cohort":
        credit = c.credit_comparability
        updates["credit_comparability"] = credit.model_copy(update={"availability": credit.availability.model_copy(update={"has_any_comparable_cohort": False})})
    else:
        updates["status"] = "EVIDENCE_INVALID"
    return c.model_copy(update=updates)


def test_partition_exact_projection_and_full_source_identity():
    c1, c2 = composite(1), composite(2)
    noncore = incomplete(composite(3), "modified_duration_ready")
    source = snapshot([c2, noncore, c1], missing=[4])
    before = deepcopy(source.model_dump())
    result = UnifiedCandidateReducer.build(source)
    assert result.status == "PARTIAL" and result.candidate_bond_ids == (1, 2)
    assert result.excluded_bond_ids == (3, 4) and result.candidate_count == result.exclusion_count == 2
    candidate = result.candidates[0]
    assert candidate.m3 is c1
    key = candidate.m3.credit_comparability.bond_rating_entries[0].cohort_key
    assert (key.source_provider, key.rating_scale_raw, key.rating_value_raw) == ("Native Provider", " Native Scale ", " AA+(RU) ")
    f = candidate.features
    assert f.yield_to_maturity_pct == c1.market.yield_to_maturity_pct
    assert f.spread_to_ofz_bps == c1.relative_value.spread_to_ofz_bps
    assert f.liquidity_score_v1 == c1.liquidity.liquidity_score_v1
    assert f.modified_duration_years == c1.modified_duration.modified_duration_years
    assert f.dv01_currency_per_bond == c1.dv01.dv01_currency_per_bond
    assert f.nominal_value == c1.dv01.nominal_value and f.legal_issuer_id == c1.credit.legal_issuer_id
    assert f.maturity_date == c1.market.maturity_date
    assert candidate.provenance.liquidity_selected_market_snapshot_ids == tuple(c1.liquidity.provenance.selected_market_snapshot_ids)
    assert candidate.provenance.security_master_profile_id == c1.modified_duration.provenance.security_master_profile_id
    assert result.exclusions[0].reasons == ("MODIFIED_DURATION_NOT_READY",)
    assert result.exclusions[1].reasons == ("BOND_NOT_FOUND",) and result.exclusions[1].isin is None
    assert UnifiedCandidateReducer.build(source).model_dump() == result.model_dump()
    assert UnifiedCandidateBatchView.model_validate_json(result.model_dump_json()).model_dump() == result.model_dump()
    assert source.model_dump() == before
    for obj in (result, candidate, f, candidate.provenance, candidate.capabilities):
        with pytest.raises(ValidationError):
            setattr(obj, next(iter(type(obj).model_fields)), None)
    with pytest.raises(ValidationError):
        type(f)(**f.model_dump(), extra=1)
    for name, value in candidate.capabilities.model_dump().items():
        if name.startswith(("investment", "portfolio", "risk_engine", "shadow", "recommendation", "transaction", "slippage", "market_impact", "cross_agency", "pit")):
            assert value is False


@pytest.mark.parametrize("name,reason", CORE_REASONS)
def test_each_core_requirement_has_precise_exclusion(name, reason):
    result = UnifiedCandidateReducer.build(snapshot([incomplete(composite(), name)]))
    assert result.candidate_count == 0 and result.exclusions[0].reasons == (reason,)


@pytest.mark.parametrize("case", ["omit_core", "false_core", "unknown_core"])
def test_audit_core_disagreement_raises(case):
    source = snapshot()
    if case == "false_core":
        source = snapshot([incomplete(composite(), "dv01_ready")])
    audit = source.coverage_audit.model_copy(update={"core_m3_complete_bond_ids": [] if case == "omit_core" else [999] if case == "unknown_core" else [1]})
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(source.model_copy(update={"coverage_audit": audit}))


@pytest.mark.parametrize("field,value", [("contract_version", "wrong"), ("pit_ready", True),
    ("requested_bond_ids", [1,1]), ("requested_bond_ids", [True]), ("existing_bond_ids", [2]),
    ("missing_bond_ids", [1]), ("requested_bond_count", True), ("existing_bond_count", 3),
    ("composite_count", 0), ("consistent_composite_count", 0), ("invalid_composite_count", 1),
    ("status", "PARTIAL"), ("coverage_audit", None), ("as_of_date", datetime(2026,9,19)),
    ("market_source", "manual"), ("max_market_age_days", False), ("liquidity_min_observation_days", 31)])
def test_snapshot_envelope_fail_closed(field, value):
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(snapshot().model_copy(update={field: value}))


@pytest.mark.parametrize("case", ["item_id", "item_order", "missing_composite", "m3_id", "m3_date", "m3_source", "m3_version", "m3_pit", "availability_type", "audit_version", "audit_count", "audit_universe", "provenance_partition"])
def test_nested_envelope_fail_closed(case):
    source = snapshot([composite(1), composite(2)])
    first = source.items[0]
    if case == "item_order":
        source = source.model_copy(update={"items": source.items[::-1]})
    elif case == "provenance_partition":
        source = source.model_copy(update={"provenance": source.provenance.model_copy(update={"existing_bond_ids": []})})
    elif case.startswith("audit"):
        updates = {"audit_version": {"contract_version": "bad"}, "audit_count": {"core_m3_complete_count": 0}, "audit_universe": {"bond_ids": [1]}}[case]
        source = source.model_copy(update={"coverage_audit": source.coverage_audit.model_copy(update=updates)})
    else:
        if case == "item_id":
            first = first.model_copy(update={"bond_id": 2})
        elif case == "missing_composite":
            first = first.model_copy(update={"composite": None})
        else:
            updates = {"m3_id": {"bond_id": 3}, "m3_date": {"as_of_date": DAY-timedelta(days=1)},
                "m3_source": {"market_source": "other"}, "m3_version": {"contract_version": "bad"}, "m3_pit": {"pit_ready": True},
                "availability_type": {"availability": first.composite.availability.model_copy(update={"market_fresh": 1})}}[case]
            first = first.model_copy(update={"composite": first.composite.model_copy(update=updates)})
        source = source.model_copy(update={"items": [first, source.items[1]]})
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(source)


@pytest.mark.parametrize("child,field,value", [("market","yield_to_maturity_pct",None),
    ("market","market_status","STALE"), ("relative_value","spread_to_ofz_bps",D("NaN")),
    ("relative_value","spread_to_ofz_pp",1.0), ("liquidity","liquidity_score_v1",D("101")),
    ("modified_duration","modified_duration_years",D("-1")),
    ("modified_duration","coupon_frequency_per_year",True), ("dv01","nominal_value",D("0")),
    ("dv01","dv01_currency_per_bond",D("Infinity")), ("dv01","currency_code","USD"),
    ("liquidity_relative_value","spread_to_ofz_pp",D("999"))])
def test_contradictory_ready_inputs_rejected(child, field, value):
    source = snapshot()
    c = source.items[0].composite
    bad = c.model_copy(update={child: getattr(c,child).model_copy(update={field:value})})
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(source.model_copy(update={"items": [source.items[0].model_copy(update={"composite":bad})]}))


def test_null_clean_price_nullable_issuer_and_exact_zero_negative_fields():
    c = composite()
    c = c.model_copy(update={"market": c.market.model_copy(update={"clean_price":None}),
        "credit":c.credit.model_copy(update={"legal_issuer_id":None,"legal_issuer_source_issuer_id":None,"legal_issuer_inn":None,
            "issuer_link_status":"MAPPING_MISSING", "availability":c.credit.availability.model_copy(update={"has_verified_legal_issuer":False})}),
        "credit_comparability":c.credit_comparability.model_copy(update={"provenance":c.credit_comparability.provenance.model_copy(update={"legal_issuer_id":None,"issuer_link_status":"MAPPING_MISSING"})}),
        "relative_value":c.relative_value.model_copy(update={"spread_to_ofz_pp":D("-1.234567890123456789"),"spread_to_ofz_bps":D("-123.4567890123456789")}),
        "liquidity_relative_value":c.liquidity_relative_value.model_copy(update={"spread_to_ofz_pp":D("-1.234567890123456789"),"spread_to_ofz_bps":D("-123.4567890123456789")}),
        "dv01":c.dv01.model_copy(update={"dv01_currency_per_bond":D("0"),"relative_price_sensitivity_per_1bp":D("0")})})
    context = getcontext().copy()
    source = snapshot([c])
    try:
        getcontext().prec = 3
        result = UnifiedCandidateReducer.build(source)
        assert result.candidates[0].features.clean_price is None
        assert result.candidates[0].features.legal_issuer_id is None
        assert result.candidates[0].features.spread_to_ofz_pp == D("-1.234567890123456789")
        assert result.candidates[0].features.dv01_currency_per_bond == 0
        assert getcontext().prec == 3
    finally:
        setcontext(context)


def test_all_missing_and_static_pure_boundary():
    result = UnifiedCandidateReducer.build(snapshot([], missing=[1,2]))
    assert result.status == "NO_EXISTING_BONDS" and result.candidate_count == 0
    assert result.exclusion_count == 2
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(None)
    path = Path(__file__).parents[1]/"app/services/unified_candidate_reducer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert all(n.startswith(("app.schemas.","datetime","decimal")) for n in imports)
    assert not any(isinstance(n,(ast.Mult,ast.Div,ast.Pow)) for n in ast.walk(tree))
    names = {n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    assert not names & {"execute","commit","flush","rollback","sync","lookup","predict","now"}


@pytest.mark.parametrize("case", ["profile_mismatch", "liquidity_ids", "liquidity_dates", "snapshot_provenance",
    "credit_empty", "cohort_empty", "verified_issuer_missing", "credit_dependency_identity"])
def test_ready_lineage_contradictions_fail_closed(case):
    c = composite()
    if case == "profile_mismatch":
        c = c.model_copy(update={"dv01":c.dv01.model_copy(update={"provenance":c.dv01.provenance.model_copy(update={"security_master_profile_id":999})})})
    elif case in {"liquidity_ids", "liquidity_dates"}:
        patch = {"selected_market_snapshot_ids":[]} if case == "liquidity_ids" else {"selected_trade_dates":c.liquidity.provenance.selected_trade_dates[::-1]}
        c = c.model_copy(update={"liquidity":c.liquidity.model_copy(update={"provenance":c.liquidity.provenance.model_copy(update=patch)})})
    elif case == "snapshot_provenance":
        c = c.model_copy(update={"provenance":c.provenance.model_copy(update={"market_snapshot_id":999})})
    elif case == "credit_empty":
        c = c.model_copy(update={"credit":c.credit.model_copy(update={"bond_ratings":[]})})
    elif case == "cohort_empty":
        c = c.model_copy(update={"credit_comparability":c.credit_comparability.model_copy(update={"bond_rating_entries":[]})})
    elif case == "verified_issuer_missing":
        c = c.model_copy(update={"credit":c.credit.model_copy(update={"legal_issuer_id":None})})
    else:
        c = c.model_copy(update={"credit_comparability":c.credit_comparability.model_copy(update={"provenance":c.credit_comparability.provenance.model_copy(update={"credit_feature_bond_id":999})})})
    with pytest.raises(ValueError):
        UnifiedCandidateReducer.build(snapshot([c]))


def test_multiple_reasons_lexical_order_and_complete_is_not_core_readiness():
    c = incomplete(incomplete(composite(), "dv01_ready"), "has_credit_rating_evidence")
    result = UnifiedCandidateReducer.build(snapshot([c]))
    assert result.status == "COMPLETE" and result.candidate_count == 0
    assert result.exclusions[0].reasons == ("CREDIT_RATING_EVIDENCE_MISSING", "DV01_NOT_READY")
