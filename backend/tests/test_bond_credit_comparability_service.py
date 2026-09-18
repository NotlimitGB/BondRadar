"""Task275 A–AK: exact equality, defensive contracts and disposable DB safety."""

import ast
import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.company import Company
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact
from app.models.legal_issuer import LegalIssuer
from app.schemas.bond_credit_comparability import (
    BondCreditComparabilityView, SourceNativeRatingCohortKey,
)
from app.schemas.bond_credit_features import CreditRatingEventEvidence, RatingAgencyLatestEvidence
from app.services.bond_credit_comparability_service import BondCreditComparabilityService
from app.services.bond_credit_feature_service import BondCreditFeatureService

DAY = date(2026, 9, 17)
NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


@pytest.fixture
def seed(db_session):
    serial = 0

    def digest():
        nonlocal serial
        serial += 1
        return hashlib.sha256(str(serial).encode()).hexdigest()

    def save(row):
        db_session.add(row)
        db_session.flush()
        return row

    company = save(Company(name="Task275 issuer", ticker="TASK275"))
    bond = save(Bond(company_id=company.id, name="Task275 bond", isin="RU000A123456", secid="TASK275"))

    def issuer():
        row = save(LegalIssuer(source_issuer_id=digest(), resolution_state="verified",
                               issuer_title="Task275 issuer", issuer_inn="7701234567"))
        profile = save(BondLegalIssuerProfile(
            bond_id=bond.id, mapping_state="verified", mapping_source="moex_security_reference",
            source_issuer_id=row.source_issuer_id, issuer_title=row.issuer_title,
            issuer_inn=row.issuer_inn, security_match_status="EXACT_SECID",
        ))
        return row, profile

    def rating(target=None, agency="ACRA", provider="CBR_RATINGS", value="AA(RU)",
               scale=None, when=DAY, precision="UNKNOWN", publication=None, state="RESOLVED"):
        artifact = save(CreditRiskSourceArtifact(
            source_provider=provider, source_kind="RATING_REPOSITORY_RESPONSE",
            source_url="https://example.invalid/" + digest(), content_type="application/pdf",
            content_bytes=b"fixture", content_sha256=digest(), retrieved_at=NOW,
        ))
        issuer_target = isinstance(target, LegalIssuer)
        row = save(CreditRatingEvent(
            artifact_id=artifact.id, rating_agency=agency,
            target_kind="LEGAL_ISSUER" if issuer_target else "BOND", resolution_state=state,
            legal_issuer_id=target.id if issuer_target and state == "RESOLVED" else None,
            bond_id=(target or bond).id if not issuer_target and state == "RESOLVED" else None,
            source_issuer_inn=target.issuer_inn if issuer_target else None,
            source_bond_isin=None if issuer_target else (target or bond).isin,
            source_object_id=digest(), event_date=when, publication_precision=precision,
            publication_date=publication if precision == "DATE" else None,
            publication_at=publication if precision == "TIMESTAMP" else None,
            rating_scale_raw=scale, rating_value_raw=value, rating_outlook_raw="Stable",
            rating_watch_raw="Native watch", rating_action_raw="Withdrawn", event_fingerprint=digest(),
        ))
        return row, artifact

    return SimpleNamespace(bond=bond, company=company, issuer=issuer, rating=rating, save=save)


@pytest.fixture
def evidence(db_session, seed):
    return BondCreditFeatureService(db_session).build_for_bond(seed.bond.id, DAY)


def native_event(event_id=1, agency="ACRA", target="BOND", value="AA(RU)", scale=None,
                 provider="CBR_RATINGS", **updates):
    result = CreditRatingEventEvidence(
        event_id=event_id, artifact_id=100 + event_id, source_provider=provider,
        source_artifact_sha256="a" * 64, artifact_retrieved_at=NOW,
        source_object_id="native:" + str(event_id), rating_agency=agency, target_kind=target,
        event_date=DAY, publication_precision="UNKNOWN", publication_date=None, publication_at=None,
        rating_scale_raw=scale, rating_value_raw=value, rating_outlook_raw="Stable",
        rating_watch_raw="Watch", rating_action_raw="Withdrawn", source_issuer_inn=None,
        source_bond_isin="RU000A123456", event_fingerprint="b" * 64,
    )
    return result.model_copy(update=updates)


def group(*events, agency=None, **updates):
    result = RatingAgencyLatestEvidence(
        rating_agency=agency or (events[0].rating_agency if events else "ACRA"),
        latest_event_date=DAY, event_count=len(events),
        has_rating_value=any(type(e.rating_value_raw) is str and bool(e.rating_value_raw.strip()) for e in events),
        events=list(events),
    )
    return result.model_copy(update=updates)


def dependency(evidence, bonds=(), issuers=(), **updates):
    patch = {"bond_ratings": list(bonds), "issuer_ratings": list(issuers)}
    if issuers:
        patch.update(issuer_link_status="VERIFIED", legal_issuer_id=71,
                     provenance=evidence.provenance.model_copy(update={"bond_legal_issuer_profile_id": 81}),
                     availability=evidence.availability.model_copy(update={"has_verified_legal_issuer": True}))
    patch.update(updates)
    return evidence.model_copy(update=patch)


def synthetic(db, seed, monkeypatch, source):
    calls = []

    def read(service, bond_id, as_of_date):
        assert service.db is db
        assert db.autoflush is False
        calls.append((bond_id, as_of_date))
        return source

    monkeypatch.setattr(BondCreditFeatureService, "build_for_bond", read)
    before = source.model_dump(warnings=False)
    result = BondCreditComparabilityService(db).build_for_bond(seed.bond.id, DAY)
    assert calls == [(seed.bond.id, DAY)]
    assert source.model_dump(warnings=False) == before
    return result


def build(db, seed):
    return BondCreditComparabilityService(db).build_for_bond(seed.bond.id, DAY)


def test_a_single_bond_exact_key_integration(db_session, seed):
    row, artifact = seed.rating()
    result = build(db_session, seed)
    assert result.status == "READY"
    entry = result.bond_rating_entries[0]
    assert entry.cohort_key.model_dump() == dict(target_kind="BOND", rating_agency="ACRA",
        source_provider="CBR_RATINGS", rating_scale_raw=None, rating_value_raw="AA(RU)")
    assert not entry.has_declared_rating_scale
    assert entry.selected_event_id == row.id
    assert entry.selected_event.artifact_id == artifact.id
    assert entry.selected_event.source_artifact_sha256 == artifact.content_sha256
    assert entry.selected_event.source_object_id == row.source_object_id
    assert entry.selected_event.event_fingerprint == row.event_fingerprint
    assert entry.selected_event.rating_action_raw == "Withdrawn"
    assert result.issuer_rating_entries == []
    assert result.provenance.selected_bond_rating_event_ids == [row.id]
    assert result.provenance.selected_issuer_rating_event_ids == []
    assert not any("BANK" in flag for flag in result.quality_flags)


def test_b_issuer_separate_verified_identity_integration(db_session, seed):
    issuer, profile = seed.issuer()
    row, _ = seed.rating(target=issuer)
    result = build(db_session, seed)
    assert result.status == "READY" and result.bond_rating_entries == []
    assert result.issuer_rating_entries[0].cohort_key.target_kind == "LEGAL_ISSUER"
    assert result.provenance.legal_issuer_id == issuer.id
    assert result.provenance.bond_legal_issuer_profile_id == profile.id
    assert result.provenance.issuer_link_status == "VERIFIED"
    assert result.provenance.selected_issuer_rating_event_ids == [row.id]
    assert not result.availability.has_bond_comparable_cohort


@pytest.mark.parametrize("scale", [None, "National scale", " Russian National (RU) ", "", " \t\n"])
@pytest.mark.parametrize("value", ["AA+(RU)", " ruAA+ ", "AAA", "ААА"])
def test_c_d_f_exact_scale_and_value(db_session, seed, evidence, monkeypatch, scale, value):
    source = dependency(evidence, [group(native_event(scale=scale, value=value))])
    entry = synthetic(db_session, seed, monkeypatch, source).bond_rating_entries[0]
    assert entry.status == "READY"
    assert entry.cohort_key.rating_scale_raw == scale
    assert entry.cohort_key.rating_value_raw == value
    assert entry.rating_scale_raw == scale and entry.rating_value_raw == value
    assert entry.has_declared_rating_scale is (scale is not None and bool(scale.strip()))
    assert not source.capabilities.cross_agency_normalization_ready


@pytest.mark.parametrize("field,other", [
    ("rating_agency", "EXPERT_RA"), ("target_kind", "LEGAL_ISSUER"),
    ("source_provider", "ACRA"), ("rating_scale_raw", "National"),
    ("rating_value_raw", "AA+(RU)"), ("rating_value_raw", "ruAAA"),
    ("rating_value_raw", "AAA"), ("rating_value_raw", " AAA(RU) "),
])
def test_e_h_to_m_each_key_dimension_distinct(field, other):
    key = SourceNativeRatingCohortKey(target_kind="BOND", rating_agency="ACRA",
        source_provider="CBR_RATINGS", rating_scale_raw=None, rating_value_raw="AAA(RU)")
    assert key != key.model_copy(update={field: other})


def test_h_i_agencies_and_targets_never_merged(db_session, seed, evidence, monkeypatch):
    source = dependency(evidence,
        [group(native_event()), group(native_event(2, agency="EXPERT_RA"))],
        [group(native_event(3, target="LEGAL_ISSUER"))])
    result = synthetic(db_session, seed, monkeypatch, source)
    assert len(result.bond_rating_entries) == 2 and len(result.issuer_rating_entries) == 1
    keys = [e.cohort_key for e in result.bond_rating_entries + result.issuer_rating_entries]
    assert all(a != b for i, a in enumerate(keys) for b in keys[i + 1:])
    assert result.availability.bond_comparable_agencies == ["ACRA", "EXPERT_RA"]
    assert result.availability.issuer_comparable_agencies == ["ACRA"]


@pytest.mark.parametrize("values", [("AA(RU)", "AA(RU)"), ("AA(RU)", "AAA(RU)"), (None, "AA(RU)")])
def test_n_o_multiple_no_selection_or_dedup(db_session, seed, evidence, monkeypatch, values):
    source = dependency(evidence, [group(native_event(9, value=values[0]), native_event(2, value=values[1]))])
    result = synthetic(db_session, seed, monkeypatch, source)
    entry = result.bond_rating_entries[0]
    assert entry.status == "MULTIPLE_LATEST_EVENTS" and entry.event_count == 2
    assert entry.event_ids == [2, 9]
    assert entry.cohort_key is entry.selected_event is entry.selected_event_id is None
    assert entry.source_provider is entry.rating_value_raw is None
    assert result.status == "NO_COMPARABLE_RATING"
    assert result.availability.has_bond_rating_evidence
    assert not result.availability.has_bond_comparable_cohort


@pytest.mark.parametrize("value", [None, "", " \n\t "])
@pytest.mark.parametrize("native_fields", [
    {"rating_outlook_raw": "Stable", "rating_watch_raw": None, "rating_action_raw": None},
    {"rating_outlook_raw": None, "rating_watch_raw": None, "rating_action_raw": "WITHDRAWN"},
    {"rating_outlook_raw": None, "rating_watch_raw": "Positive", "rating_action_raw": None},
])
def test_p_to_s_missing_value_keeps_event_no_action_inference(db_session, seed, evidence, monkeypatch, value, native_fields):
    e = native_event(value=value, **native_fields)
    result = synthetic(db_session, seed, monkeypatch, dependency(evidence, [group(e)]))
    entry = result.bond_rating_entries[0]
    assert entry.status == "RATING_VALUE_MISSING" and entry.cohort_key is None
    assert entry.selected_event_id == e.event_id and entry.selected_event == e
    assert entry.rating_value_raw == value
    assert result.provenance.selected_bond_rating_event_ids == [e.event_id]
    assert "BOND_RATING_VALUE_MISSING" in result.quality_flags


@pytest.mark.parametrize("precision,publication_date,publication_at", [
    ("UNKNOWN", None, None), ("DATE", DAY, None), ("TIMESTAMP", None, NOW),
])
def test_t_publication_fields_copied_without_new_selection(db_session, seed, evidence, monkeypatch, precision, publication_date, publication_at):
    e = native_event(publication_precision=precision, publication_date=publication_date, publication_at=publication_at)
    result = synthetic(db_session, seed, monkeypatch, dependency(evidence, [group(e)]))
    entry = result.bond_rating_entries[0]
    assert entry.status == "READY"
    assert entry.selected_event.publication_precision == precision
    assert entry.selected_event.publication_date == publication_date
    assert entry.selected_event.publication_at == publication_at
    assert ("PUBLICATION_TIME_UNKNOWN" in entry.quality_flags) is (precision == "UNKNOWN")
    assert ("RATING_PUBLICATION_UNKNOWN" in result.quality_flags) is (precision == "UNKNOWN")
    assert result.pit_ready is result.capabilities.pit_ready is False


@pytest.mark.parametrize("bad", [
    group(native_event(), event_count=2), group(native_event(), event_count=True),
    group(native_event(), event_count="1"), group(native_event(), event_count=1.0),
    group(native_event(), agency="EXPERT_RA"),
    group(native_event(target="LEGAL_ISSUER")),
    group(native_event(event_date=DAY - timedelta(days=1))),
    group(native_event(), latest_event_date=datetime(2026, 9, 17)),
    group(), group(native_event(provider="")), group(native_event(provider=" \t")),
    group(native_event(source_provider=None)), group(native_event(source_provider=1)),
    group(native_event(rating_value_raw=123)), group(native_event(rating_scale_raw=123)),
    group(native_event().model_copy(update={"event_id": True})), group(native_event(artifact_id=None)),
    group(native_event(publication_precision="INVALID")),
])
def test_u_v_w_x_aa_defensive_integrity(db_session, seed, evidence, monkeypatch, bad):
    result = synthetic(db_session, seed, monkeypatch, dependency(evidence, [bad]))
    entry = result.bond_rating_entries[0]
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
    assert entry.status == "EVIDENCE_INVALID"
    assert entry.cohort_key is entry.selected_event is entry.selected_event_id is None
    assert "DEPENDENCY_EVIDENCE_INVALID" in result.quality_flags
    assert not result.availability.has_any_comparable_cohort
    assert BondCreditComparabilityView.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("family", ["BOND", "LEGAL_ISSUER"])
def test_y_z_duplicates_remain_separate_invalid_entries(db_session, seed, evidence, monkeypatch, family):
    groups = [group(native_event(9, target=family)), group(native_event(2, target=family))]
    source = dependency(evidence, groups if family == "BOND" else [], groups if family == "LEGAL_ISSUER" else [])
    result = synthetic(db_session, seed, monkeypatch, source)
    entries = result.bond_rating_entries if family == "BOND" else result.issuer_rating_entries
    assert len(entries) == 2
    assert [e.event_ids for e in entries] == [[2], [9]]
    assert all(e.status == "EVIDENCE_INVALID" and e.cohort_key is None for e in entries)
    assert all("DUPLICATE_AGENCY_GROUP" in e.quality_flags for e in entries)
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"


def test_local_error_preserves_other_ready_keys_and_availability(db_session, seed, evidence, monkeypatch):
    source = dependency(evidence, [group(native_event(), event_count=2), group(native_event(2, agency="NKR"))],
                        [group(native_event(3, target="LEGAL_ISSUER"))])
    result = synthetic(db_session, seed, monkeypatch, source)
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
    assert result.bond_rating_entries[0].cohort_key is None
    assert result.bond_rating_entries[1].status == "READY"
    assert result.issuer_rating_entries[0].status == "READY"
    assert result.availability.has_any_comparable_cohort
    assert result.availability.bond_comparable_agencies == ["NKR"]
    assert result.availability.issuer_comparable_agencies == ["ACRA"]


@pytest.mark.parametrize("patch", [
    {"bond_id": 999}, {"bond_id": True}, {"bond_id": "1"},
    {"as_of_date": DAY - timedelta(days=1)}, {"as_of_date": datetime(2026, 9, 17)},
    {"contract_version": "wrong"}, {"pit_ready": True},
])
def test_global_dependency_identity_invalidates_both_families(db_session, seed, evidence, monkeypatch, patch):
    source = dependency(evidence, [group(native_event())], [group(native_event(2, target="LEGAL_ISSUER"))], **patch)
    result = synthetic(db_session, seed, monkeypatch, source)
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
    assert all(e.status == "EVIDENCE_INVALID" for e in result.bond_rating_entries + result.issuer_rating_entries)
    assert not result.availability.has_any_comparable_cohort
    assert result.provenance.credit_feature_bond_id == (patch.get("bond_id", seed.bond.id) if type(patch.get("bond_id", seed.bond.id)) is int else None)
    if "bond_id" in patch or "as_of_date" in patch:
        assert result.isin is result.secid is None


@pytest.mark.parametrize("patch", [
    {"issuer_link_status": "MAPPING_MISSING"}, {"legal_issuer_id": None},
    {"legal_issuer_id": True}, {"legal_issuer_id": "71"},
])
def test_issuer_link_integrity_does_not_invalidate_bond_key(db_session, seed, evidence, monkeypatch, patch):
    source = dependency(evidence, [group(native_event())], [group(native_event(2, target="LEGAL_ISSUER"))], **patch)
    result = synthetic(db_session, seed, monkeypatch, source)
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
    assert result.bond_rating_entries[0].status == "READY"
    assert result.issuer_rating_entries[0].status == "EVIDENCE_INVALID"
    assert result.availability.has_bond_comparable_cohort
    assert not result.availability.has_issuer_comparable_cohort


def test_verified_issuer_requires_availability_and_profile(db_session, seed, evidence, monkeypatch):
    source = dependency(evidence, [group(native_event())], [group(native_event(2, target="LEGAL_ISSUER"))])
    for patch in (
        {"availability": source.availability.model_copy(update={"has_verified_legal_issuer": False})},
        {"provenance": source.provenance.model_copy(update={"bond_legal_issuer_profile_id": None})},
    ):
        result = synthetic(db_session, seed, monkeypatch, source.model_copy(update=patch))
        assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
        assert result.issuer_rating_entries[0].status == "EVIDENCE_INVALID"


def test_status_precedence_all_constraints_and_surviving_key(db_session, seed, evidence, monkeypatch):
    bad = group(native_event(1), native_event(2), event_count=3)
    source = dependency(evidence, [bad, group(native_event(3, agency="NRA", value=None)),
                                   group(native_event(4, agency="NKR"))])
    result = synthetic(db_session, seed, monkeypatch, source)
    assert result.status == "DEPENDENCY_EVIDENCE_INVALID"
    assert result.bond_rating_entries[0].status == "EVIDENCE_INVALID"
    assert "MULTIPLE_LATEST_EVENTS" in result.bond_rating_entries[0].quality_flags
    assert "BOND_MULTIPLE_LATEST_RATING_EVENTS" in result.quality_flags
    assert "BOND_RATING_VALUE_MISSING" in result.quality_flags
    assert result.availability.has_any_comparable_cohort
    assert result.availability.bond_comparable_agencies == ["NKR"]


@pytest.mark.parametrize("bond_id", [True, False, 0, -1, 1.0, "1", None])
def test_argument_bond_validation_before_dependency(db_session, monkeypatch, bond_id):
    def forbidden(*args, **kwargs):
        pytest.fail("dependency called for invalid arguments")
    monkeypatch.setattr(BondCreditFeatureService, "build_for_bond", forbidden)
    with pytest.raises(ValueError):
        BondCreditComparabilityService(db_session).build_for_bond(bond_id, DAY)


@pytest.mark.parametrize("when", [datetime(2026, 9, 17), datetime(2026, 9, 17, tzinfo=timezone.utc), "2026-09-17", None, True])
def test_argument_date_validation_before_dependency(db_session, monkeypatch, when):
    def forbidden(*args, **kwargs):
        pytest.fail("dependency called for invalid arguments")
    monkeypatch.setattr(BondCreditFeatureService, "build_for_bond", forbidden)
    with pytest.raises(ValueError):
        BondCreditComparabilityService(db_session).build_for_bond(1, when)


def test_missing_bond_preserves_http_404(db_session):
    with pytest.raises(HTTPException) as exc:
        BondCreditComparabilityService(db_session).build_for_bond(98765, DAY)
    assert exc.value.status_code == 404 and exc.value.detail == "Bond not found"


def test_empty_stable_serialization_no_placeholders(db_session, seed):
    result = build(db_session, seed)
    assert result.status == "NO_COMPARABLE_RATING"
    assert result.bond_rating_entries == result.issuer_rating_entries == []
    assert not any(value for name, value in result.availability.model_dump().items() if name.startswith("has_"))
    assert result.quality_flags == ["BOND_COMPARABLE_RATING_MISSING", "ISSUER_COMPARABLE_RATING_MISSING"]
    assert result.model_dump_json() == build(db_session, seed).model_dump_json()
    assert BondCreditComparabilityView.model_validate_json(result.model_dump_json()) == result


def test_g_ak_sorting_determinism_without_numeric_rating_order(db_session, seed, evidence, monkeypatch):
    groups = [group(native_event(1, agency="NRA", value="AAA")),
              group(native_event(2, agency="ACRA", value="BB")),
              group(native_event(3, agency="NKR", value="CC")),
              group(native_event(4, agency="EXPERT_RA", value="AA"))]
    source = dependency(evidence, groups)
    a = synthetic(db_session, seed, monkeypatch, source)
    b = synthetic(db_session, seed, monkeypatch, dependency(evidence, groups[::-1]))
    assert a.model_dump_json() == b.model_dump_json()
    assert [e.rating_agency for e in a.bond_rating_entries] == ["ACRA", "EXPERT_RA", "NKR", "NRA"]
    assert a.availability.bond_comparable_agencies == ["ACRA", "EXPERT_RA", "NKR", "NRA"]
    for entry in a.bond_rating_entries:
        assert entry.quality_flags == sorted(set(entry.quality_flags))
    assert a.quality_flags == sorted(set(a.quality_flags))


def test_duplicate_diagnostic_order_date_then_ids(db_session, seed, evidence, monkeypatch):
    early = DAY - timedelta(days=1)
    groups = [group(native_event(9)), group(native_event(3)),
              group(native_event(20, event_date=early), latest_event_date=early)]
    a = synthetic(db_session, seed, monkeypatch, dependency(evidence, groups))
    b = synthetic(db_session, seed, monkeypatch, dependency(evidence, groups[::-1]))
    assert a.model_dump_json() == b.model_dump_json()
    assert [e.event_ids for e in a.bond_rating_entries] == [[20], [3], [9]]
    assert len(a.bond_rating_entries) == 3


def test_ab_no_own_query_single_call_exact_forwarding(db_session, seed, evidence, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Task275 issued its own DB query")
    monkeypatch.setattr(db_session, "execute", forbidden)
    monkeypatch.setattr(db_session, "get", forbidden)
    monkeypatch.setattr(db_session, "query", forbidden)
    db_session.autoflush = True
    result = synthetic(db_session, seed, monkeypatch, dependency(evidence, [group(native_event())]))
    assert result.status == "READY" and db_session.autoflush is True


def test_ag_ah_sql_select_only_caller_pending_state_and_rows_immutable(db_session, seed, monkeypatch):
    issuer, _ = seed.issuer()
    row, artifact = seed.rating()
    seed.rating(target=issuer)
    doomed = seed.save(Bond(company_id=seed.company.id, name="Caller deleted", isin="RU000A654321"))
    db_session.commit()
    db_session.delete(doomed)
    pending = Company(name="Caller new", ticker="CALLER")
    db_session.add(pending)
    seed.bond.name = "Caller dirty"
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    source_before = (row.rating_value_raw, row.event_fingerprint, artifact.content_bytes, artifact.content_sha256,
                     seed.bond.liquidity_score, seed.bond.duration_years)
    db_session.autoflush = True
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def forbidden(*args, **kwargs):
        pytest.fail("service mutation call")

    for name in ("add", "add_all", "flush", "commit", "delete", "bulk_save_objects"):
        monkeypatch.setattr(db_session, name, forbidden)
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = build(db_session, seed)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.status == "READY"
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert not any("content_bytes" in s for s in statements)
    assert (set(db_session.new), set(db_session.dirty), set(db_session.deleted)) == before
    assert pending.id is None and db_session.autoflush is True
    assert source_before == (row.rating_value_raw, row.event_fingerprint, artifact.content_bytes, artifact.content_sha256,
                             seed.bond.liquidity_score, seed.bond.duration_years)


def test_task269_selection_only_no_future_or_unrelated_evidence(db_session, seed):
    old, _ = seed.rating(when=DAY - timedelta(days=1))
    seed.rating(when=DAY, precision="DATE", publication=DAY + timedelta(days=1))
    seed.rating(when=DAY + timedelta(days=1))
    unrelated = seed.save(Bond(company_id=seed.company.id, name="Unrelated", isin="RU000A654321"))
    seed.rating(target=unrelated)
    seed.rating(state="UNRESOLVED")
    result = build(db_session, seed)
    assert result.provenance.selected_bond_rating_event_ids == [old.id]
    assert result.bond_rating_entries[0].latest_event_date == DAY - timedelta(days=1)


def test_relevant_dependency_flags_only(db_session, seed, evidence, monkeypatch):
    source = dependency(evidence, [group(native_event(publication_precision="DATE", publication_date=DAY))],
        quality_flags=["BANK_METRIC_LINEAGE_MISMATCH", "BANK_SUBJECT_AMBIGUOUS", "BOND_RATING_PUBLICATION_UNKNOWN",
                       "MULTIPLE_LATEST_ISSUER_RATING_EVENTS"])
    result = synthetic(db_session, seed, monkeypatch, source)
    assert "RATING_PUBLICATION_UNKNOWN" in result.quality_flags
    assert "ISSUER_MULTIPLE_LATEST_RATING_EVENTS" in result.quality_flags
    assert not any("BANK" in f for f in result.quality_flags)


def test_ac_to_af_aj_capabilities_no_interpretation(db_session, seed):
    result = build(db_session, seed)
    capabilities = result.capabilities.model_dump()
    assert {k for k, v in capabilities.items() if v} == {
        "credit_feature_input_ready", "source_native_rating_cohort_keys_ready", "exact_rating_label_comparability_ready",
    }
    assert result.pit_ready is False and result.comparison_method == "EXACT_SOURCE_NATIVE_LABEL"
    assert not any(name in result.model_dump() for name in ("best_agency", "primary_rating", "worst_rating",
        "normalized_grade", "rating_notch", "rating_score", "credit_score", "agency_equivalent_grade"))


def test_schema_frozen_forbid_extra_and_false_pit(db_session, seed):
    result = build(db_session, seed)
    for model in (result, result.availability, result.provenance, result.capabilities,
                  SourceNativeRatingCohortKey(target_kind="BOND", rating_agency="ACRA",
                    source_provider="CBR_RATINGS", rating_scale_raw=None, rating_value_raw="AA(RU)")):
        assert model.model_config["frozen"] and model.model_config["extra"] == "forbid"
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unrequested": True})
    with pytest.raises(ValidationError):
        result.status = "READY"
    with pytest.raises(ValidationError):
        BondCreditComparabilityView.model_validate({**result.model_dump(), "pit_ready": True})


def test_static_safety_no_queries_network_mutation_or_grade_maps():
    root = Path(__file__).resolve().parents[1]
    service = (root / "app/services/bond_credit_comparability_service.py").read_text(encoding="utf-8")
    schema = (root / "app/schemas/bond_credit_comparability.py").read_text(encoding="utf-8")
    tree = ast.parse(service)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(part in name for part in ("models", "requests", "httpx", "urllib", "socket", "ingest", "strategy", "portfolio", "risk_engine")) for name in imports)
    forbidden = {"add", "add_all", "flush", "commit", "delete", "execute", "query", "get", "bulk_save_objects"}
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(isinstance(n.func, ast.Attribute) and n.func.attr in forbidden for n in calls
                   if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Attribute)
                   and n.func.value.attr == "db")
    assert sum(isinstance(n.func, ast.Attribute) and n.func.attr == "build_for_bond" for n in calls) == 1
    assert not any(isinstance(node, ast.Dict) and node.keys for node in ast.walk(tree))
    assert not any(isinstance(n, ast.Name) and n.id == "CreditRatingEvent" for n in ast.walk(tree))
    for name in ("spread_to_ofz", "liquidity_score", "normalized_grade", "rating_notch",
                 "rating_score", "agency_equivalent_grade", "best_agency", "primary_rating", "worst_rating"):
        assert name not in service + schema
