"""Pure Task279 composer tests over synthetic Task275 and Task268 views."""

import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, ROUND_DOWN, localcontext

import pytest

from app.schemas.bond_credit_comparability import (
    BondCreditComparabilityView, RatingComparabilityEntry,
)
from app.schemas.ofz_reference_curve import BondRelativeValueView
from app.services.bond_credit_cohort_relative_value_composer import (
    compose_credit_cohort_relative_value_member,
)

DAY = date(2026, 9, 18)
NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
D = Decimal


def entry(agency="ACRA", target="BOND", event_id=17, provider="CBR_RATINGS",
          scale=None, value="AA(RU)", status="READY", flags=None):
    usable = status == "READY"
    return RatingComparabilityEntry(
        target_kind=target, rating_agency=agency, status=status, latest_event_date=DAY,
        event_count=1, event_ids=[event_id], selected_event_id=event_id if usable else None,
        source_provider=provider if usable else None, rating_scale_raw=scale if usable else None,
        rating_value_raw=value if usable else None, has_declared_rating_scale=bool(scale and scale.strip()),
        has_rating_value=usable, cohort_key=(dict(
            target_kind=target, rating_agency=agency, source_provider=provider,
            rating_scale_raw=scale, rating_value_raw=value) if usable else None),
        selected_event=(dict(
            event_id=event_id, artifact_id=31, source_provider=provider,
            source_artifact_sha256="a" * 64, artifact_retrieved_at=NOW,
            source_object_id="native-object", rating_agency=agency, target_kind=target,
            event_date=DAY, publication_precision="UNKNOWN", publication_date=None,
            publication_at=None, rating_scale_raw=scale, rating_value_raw=value,
            rating_outlook_raw="Stable", rating_watch_raw=None, rating_action_raw=None,
            source_issuer_inn=None, source_bond_isin="RU000A123456",
            event_fingerprint="b" * 64) if usable else None),
        quality_flags=flags or (["PUBLICATION_TIME_UNKNOWN"] if usable else []),
    )


def credit(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK279", as_of_date=DAY, status="READY",
        bond_rating_entries=[entry(), entry(agency="EXPERT_RA", event_id=19, value="ruAA")],
        issuer_rating_entries=[entry(target="LEGAL_ISSUER", event_id=18)],
        availability=dict(
            has_bond_rating_evidence=True, has_issuer_rating_evidence=True,
            has_bond_comparable_cohort=True, has_issuer_comparable_cohort=True,
            bond_comparable_agencies=["ACRA", "EXPERT_RA"],
            issuer_comparable_agencies=["ACRA"], has_any_comparable_cohort=True),
        provenance=dict(
            credit_feature_contract_version="bond-credit-feature-v1", credit_feature_bond_id=1,
            credit_feature_as_of_date=DAY, as_of_date=DAY,
            bond_legal_issuer_profile_id=41, legal_issuer_id=51, issuer_link_status="VERIFIED",
            bond_rating_event_ids=[17, 19], issuer_rating_event_ids=[18],
            selected_bond_rating_event_ids=[17, 19], selected_issuer_rating_event_ids=[18],
            bond_rating_agencies=["ACRA", "EXPERT_RA"], issuer_rating_agencies=["ACRA"]),
        quality_flags=["RATING_PUBLICATION_UNKNOWN", "UNRELATED_TOP_FLAG"],
    )
    return BondCreditComparabilityView(**data).model_copy(update=updates)


def relative(**updates):
    data = dict(
        bond_id=1, isin="RU000A123456", secid="TASK279", as_of_date=DAY,
        market_source="moex", status="READY", target_yield_to_maturity_pct=D("14.375"),
        target_duration_years=D("2.5"), reference_ofz_yield_pct=D("12.875"),
        spread_to_ofz_pp=D("1.5"), spread_to_ofz_bps=D("150"),
        interpolation_method="LINEAR_INTERPOLATION",
        curve=dict(as_of_date=DAY, market_source="moex", status="READY", curve_trade_date=DAY,
                   node_count=2, min_duration_years=D(1), max_duration_years=D(3),
                   nodes=[], diagnostics={}),
        provenance=dict(target_market_snapshot_id=61, target_market_trade_date=DAY,
                        curve_trade_date=DAY),
        quality_flags=["CURVE_DUPLICATE_DURATION_AGGREGATED", "UNRELATED_RELATIVE_FLAG"],
    )
    return BondRelativeValueView(**data).model_copy(update=updates)


def compose(c=None, r=None, **kwargs):
    args = dict(bond_id=1, as_of_date=DAY, target_kind="BOND",
                rating_agency="ACRA", market_source="moex")
    args.update(kwargs)
    return compose_credit_cohort_relative_value_member(c or credit(), r or relative(), **args)


def unavailable(result, status):
    assert result.status == status
    assert not result.availability.has_credit_cohort_relative_value_member
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert result.pit_ready is False
    assert type(result).model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("target,agency,event_id,value", [
    ("BOND", "ACRA", 17, "AA(RU)"),
    ("LEGAL_ISSUER", "ACRA", 18, "AA(RU)"),
    ("BOND", "EXPERT_RA", 19, "ruAA"),
])
def test_ready_explicit_family_and_agency(target, agency, event_id, value):
    result = compose(target_kind=target, rating_agency=agency)
    assert result.status == "READY" and all(result.availability.model_dump().values())
    assert result.rating_event_id == event_id and result.rating_value_raw == value
    assert result.cohort_key.target_kind == target and result.cohort_key.rating_agency == agency
    assert result.spread_to_ofz_pp == D("1.5") and result.spread_to_ofz_bps == D("150")
    assert result.quality_flags == ["PUBLICATION_TIME_UNKNOWN"]


def test_missing_requested_agency_has_no_fallback():
    result = compose(rating_agency="NRA")
    unavailable(result, "SELECTED_COHORT_MISSING")
    assert result.cohort_key is None and result.provenance.selected_entry_count == 0


@pytest.mark.parametrize("status,flag", [
    ("RATING_VALUE_MISSING", "RATING_VALUE_MISSING"),
    ("MULTIPLE_LATEST_EVENTS", "MULTIPLE_LATEST_EVENTS"),
    ("EVIDENCE_INVALID", None),
])
def test_selected_non_ready_entry(status, flag):
    selected = entry(status=status, flags=[flag, "BANK_FLAG"] if flag else ["BANK_FLAG"])
    result = compose(credit(bond_rating_entries=[selected]))
    unavailable(result, "SELECTED_COHORT_UNAVAILABLE")
    assert result.availability.has_selected_rating_entry
    assert not result.availability.has_selected_cohort_key
    assert (flag in result.quality_flags) if flag else "BANK_FLAG" not in result.quality_flags


def test_duplicate_selected_entries_fail_without_selection():
    result = compose(credit(bond_rating_entries=[entry(), entry(event_id=99)]))
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")
    assert result.provenance.selected_entry_count == 2
    assert result.rating_event_id is result.cohort_key is None


@pytest.mark.parametrize("patch", [
    {"cohort_key": None}, {"selected_event_id": None}, {"selected_event_id": True},
    {"source_provider": " "}, {"rating_value_raw": ""}, {"rating_scale_raw": 1},
])
def test_ready_entry_key_presence_integrity(patch):
    result = compose(credit(bond_rating_entries=[entry().model_copy(update=patch)]))
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")


@pytest.mark.parametrize("field,value", [
    ("target_kind", "LEGAL_ISSUER"), ("rating_agency", "NRA"),
    ("source_provider", "other"), ("rating_scale_raw", "other"),
    ("rating_value_raw", "AA"),
])
def test_cohort_key_exact_field_contradictions(field, value):
    selected = entry()
    selected = selected.model_copy(update={
        "cohort_key": selected.cohort_key.model_copy(update={field: value})})
    result = compose(credit(bond_rating_entries=[selected]))
    unavailable(result, "CREDIT_COHORT_EVIDENCE_INVALID")


@pytest.mark.parametrize("scale,provider,value", [
    (None, " Provider ", " AA(RU) "), ("", "provider", "AA"),
    (" ", "PROVIDER", "aa"), (" National ", "provider", "AA+")])
def test_raw_strings_preserved_without_normalization(scale, provider, value):
    selected = entry(scale=scale, provider=provider, value=value)
    result = compose(credit(bond_rating_entries=[selected]))
    assert result.status == "READY"
    assert result.cohort_key.model_dump() == selected.cohort_key.model_dump()
    assert result.rating_scale_raw == scale and result.rating_source_provider == provider
    assert result.rating_value_raw == value and result.cohort_key is not selected.cohort_key


@pytest.mark.parametrize("provider,value", [(" ", "AA"), ("provider", " ")])
def test_whitespace_only_required_raw_fields_are_invalid(provider, value):
    selected = entry(provider=provider, value=value)
    unavailable(compose(credit(bond_rating_entries=[selected])), "CREDIT_COHORT_EVIDENCE_INVALID")


@pytest.mark.parametrize("updates", [
    {"status": "DEPENDENCY_EVIDENCE_INVALID"}, {"contract_version": "wrong"},
    {"pit_ready": True}, {"status": "UNKNOWN"},
])
def test_credit_dependency_unavailable(updates):
    result = compose(credit(**updates))
    unavailable(result, "CREDIT_COMPARABILITY_UNAVAILABLE")
    assert not result.availability.has_credit_comparability


@pytest.mark.parametrize("status", [
    "TARGET_MARKET_MISSING", "TARGET_MARKET_STALE", "CURVE_NOT_READY",
    "TARGET_CURVE_DATE_MISMATCH", "TARGET_DURATION_OUTSIDE_CURVE",
])
def test_relative_unavailable_retains_independent_credit(status):
    result = compose(r=relative(status=status, spread_to_ofz_pp=None, spread_to_ofz_bps=None))
    unavailable(result, "RELATIVE_VALUE_UNAVAILABLE")
    assert result.cohort_key is not None and result.availability.has_selected_cohort_key
    assert "RELATIVE_VALUE_" + status in result.quality_flags


@pytest.mark.parametrize("field,value", [
    ("spread_to_ofz_pp", None), ("spread_to_ofz_bps", None),
    ("spread_to_ofz_pp", D("NaN")), ("spread_to_ofz_bps", D("Infinity")),
    ("spread_to_ofz_pp", 1.5), ("spread_to_ofz_bps", "150"),
])
def test_ready_relative_requires_finite_decimal_spreads(field, value):
    result = compose(r=relative(**{field: value}))
    unavailable(result, "RELATIVE_VALUE_EVIDENCE_INVALID")
    assert result.availability.has_selected_cohort_key and not result.availability.has_relative_value


@pytest.mark.parametrize("side,patch", [
    ("credit", {"bond_id": 2}), ("credit", {"bond_id": True}),
    ("credit", {"as_of_date": DAY - timedelta(days=1)}),
    ("relative", {"bond_id": 2}), ("relative", {"as_of_date": DAY - timedelta(days=1)}),
    ("relative", {"market_source": "MOEX"}),
])
def test_dependency_identity_mismatch_is_highest_priority(side, patch):
    c, r = credit(status="DEPENDENCY_EVIDENCE_INVALID"), relative(status="CURVE_NOT_READY")
    c = c.model_copy(update=patch) if side == "credit" else c
    r = r.model_copy(update=patch) if side == "relative" else r
    result = compose(c, r)
    unavailable(result, "DEPENDENCY_IDENTITY_MISMATCH")
    if side == "credit":
        assert result.cohort_key is result.rating_event_id is None
    else:
        assert result.spread_to_ofz_pp is result.market_snapshot_id is None


@pytest.mark.parametrize("side,patch", [
    ("relative", {"contract_version": "wrong"}),
    ("relative", {"pit_ready": True}),
    ("curve", {"contract_version": "wrong"}),
    ("curve", {"pit_ready": True}),
])
def test_relative_contract_integrity(side, patch):
    r = relative()
    r = r.model_copy(update=patch) if side == "relative" else r.model_copy(
        update={"curve": r.curve.model_copy(update=patch)})
    result = compose(r=r)
    unavailable(result, "RELATIVE_VALUE_EVIDENCE_INVALID")
    assert "RELATIVE_VALUE_CONTRACT_INVALID" in result.quality_flags


@pytest.mark.parametrize("minimum_status,expected", [
    ("duplicate", "CREDIT_COHORT_EVIDENCE_INVALID"),
    ("missing", "SELECTED_COHORT_MISSING"),
    ("unavailable", "SELECTED_COHORT_UNAVAILABLE"),
])
def test_status_precedence_after_valid_dependencies(minimum_status, expected):
    rows = ([entry(), entry(event_id=99)] if minimum_status == "duplicate" else []
            if minimum_status == "missing" else [entry(status="RATING_VALUE_MISSING")])
    result = compose(credit(bond_rating_entries=rows), relative(status="CURVE_NOT_READY"))
    unavailable(result, expected)
    assert "RELATIVE_VALUE_UNAVAILABLE" in result.quality_flags


@pytest.mark.parametrize("kwargs", [
    {"bond_id": True}, {"bond_id": 0}, {"bond_id": 1.0},
    {"as_of_date": datetime(2026, 9, 18)}, {"as_of_date": "2026-09-18"},
    {"target_kind": "ISSUER"}, {"target_kind": None},
    {"rating_agency": "OTHER"}, {"rating_agency": None},
    {"market_source": ""}, {"market_source": " "}, {"market_source": None},
])
def test_invalid_request_context_before_dependency_inspection(kwargs):
    class Bomb:
        def __getattribute__(self, name):
            raise AssertionError("dependency must not be inspected")
    args = dict(bond_id=1, as_of_date=DAY, target_kind="BOND",
                rating_agency="ACRA", market_source="moex")
    args.update(kwargs)
    with pytest.raises(ValueError):
        compose_credit_cohort_relative_value_member(Bomb(), Bomb(), **args)


@pytest.mark.parametrize("c,r", [(None, relative()), ({}, relative()),
                                           (credit(), None), (credit(), {})])
def test_wrong_dependency_object_types(c, r):
    with pytest.raises(ValueError):
        compose_credit_cohort_relative_value_member(
            c, r, bond_id=1, as_of_date=DAY, target_kind="BOND",
            rating_agency="ACRA", market_source="moex")


def test_full_provenance_availability_and_exact_decimal_copy():
    result = compose()
    assert result.provenance.credit_identity.model_dump() == {
        "bond_id": 1, "as_of_date": DAY, "market_source": None}
    assert result.provenance.relative_value_identity.model_dump() == {
        "bond_id": 1, "as_of_date": DAY, "market_source": "moex"}
    assert result.provenance.selected_rating_event_id == 17
    assert result.provenance.bond_legal_issuer_profile_id == 41
    assert result.provenance.legal_issuer_id == 51
    assert result.provenance.target_market_snapshot_id == 61
    assert result.provenance.target_market_trade_date == result.provenance.curve_trade_date == DAY
    assert result.spread_to_ofz_pp.as_tuple() == D("1.5").as_tuple()
    assert result.spread_to_ofz_bps.as_tuple() == D("150").as_tuple()


def test_inputs_immutable_stable_serialization_and_decimal_context_isolation():
    c, r = credit(), relative()
    before = c.model_dump_json(), r.model_dump_json()
    expected = compose(c, r).model_dump_json()
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = True
        caller.flags[Inexact] = True
        state = caller.copy()
        actual = compose(c, r)
        assert actual.model_dump_json() == expected
        assert caller.prec == state.prec and caller.rounding == state.rounding
        assert caller.traps == state.traps and caller.flags == state.flags
    assert before == (c.model_dump_json(), r.model_dump_json())
    assert actual.cohort_key is not c.bond_rating_entries[0].cohort_key


def test_ast_pure_boundary_no_service_db_normalization_or_ranking():
    import app.services.bond_credit_cohort_relative_value_composer as module
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports <= {"datetime", "decimal", "typing",
        "app.schemas.bond_credit_cohort_relative_value",
        "app.schemas.bond_credit_comparability", "app.schemas.bond_credit_features",
        "app.schemas.ofz_reference_curve"}
    assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    forbidden = {"execute", "query", "select", "flush", "commit", "add", "add_all",
                 "delete", "get", "post", "urlopen", "build_for_bond", "evaluate_bond"}
    for call in calls:
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
        if name in {"add", "update"} and isinstance(call.func, ast.Attribute):
            assert isinstance(call.func.value, ast.Name) and call.func.value.id == "flags"
            continue
        assert name not in forbidden
    assert not any(token in source for token in (
        "sqlalchemy", "Session", "app.models", "app.services", "requests", "httpx", "urllib",
        "preferred_rating", "best_rating", "worst_rating", "peer_rank", "recommendation"))
    # The composer only copies financial fields; it has no arithmetic implementation.
    assert not any(isinstance(n, ast.BinOp) and not isinstance(n.op, ast.BitOr) for n in ast.walk(tree))
