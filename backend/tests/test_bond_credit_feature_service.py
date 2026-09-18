"""Task269 acceptance on disposable SQLite; source strings are fixture data."""

import ast
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.cbr_bank_financial_evidence import (
    CbrBankCreditMetric, CbrBankNormalizedObservation, CbrBankRawObservation,
    CbrBankReportSnapshot, CbrBankReportingSubject, CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence, CbrBankSubjectLegalIssuerProfile,
)
from app.models.company import Company
from app.models.credit_risk_evidence import CreditDefaultEvent, CreditRatingEvent, CreditRiskSourceArtifact
from app.models.legal_issuer import LegalIssuer
from app.schemas.bond_credit_features import BondCreditFeatureView
from app.services.bond_credit_feature_service import BondCreditFeatureService, _utc_date

DAY = date(2026, 9, 17)
NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def seed(db_session):
    sequence = 0
    def digest():
        nonlocal sequence
        sequence += 1
        return hashlib.sha256(str(sequence).encode()).hexdigest()
    def save(row):
        db_session.add(row)
        db_session.flush()
        return row
    company = save(Company(name="Same issuer name", ticker="TASK269"))
    bond = save(Bond(company_id=company.id, name="Same issuer name", isin="RU000A123456", secid="TASK269"))
    def issuer(state="verified", source_id=None):
        return save(LegalIssuer(source_issuer_id=source_id or digest(), resolution_state=state,
                                issuer_title="Same issuer name", issuer_inn="7701234567"))
    def mapping(issuer=None, state="verified", source="moex_security_reference", source_id=None):
        known = state in ("verified", "observed")
        return save(BondLegalIssuerProfile(
            bond_id=bond.id, mapping_state=state, mapping_source=source,
            source_issuer_id=(source_id or issuer.source_issuer_id) if known else None,
            issuer_title="Same issuer name" if known else None,
            issuer_inn="7701234567" if known else None,
            security_match_status="EXACT_SECID" if known else None,
        ))
    def rating(target=None, agency="ACRA", when=DAY, provider=None, value="AAA(RU)",
               action=None, precision="UNKNOWN", publication=None, state="RESOLVED"):
        artifact = save(CreditRiskSourceArtifact(
            source_provider=provider or agency,
            source_kind="RATING_REPOSITORY_RESPONSE" if provider == "CBR_RATINGS" else "RATING_RELEASE",
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
            rating_scale_raw=None if provider == "CBR_RATINGS" else "Native scale",
            rating_value_raw=value, rating_outlook_raw="Stable", rating_watch_raw="Native watch",
            rating_action_raw=action, event_fingerprint=digest(),
        ))
        return row
    def subject(issuer, regn="1", state="VERIFIED"):
        row = save(CbrBankReportingSubject(subject_regn=regn, first_observed_at=NOW, last_observed_at=NOW))
        evidence = save(CbrBankSubjectLegalIssuerEvidence(
            reporting_subject_id=row.id, subject_regn=regn, bridge_contract_version="cbr-legal-issuer-bridge-v1",
            bridge_state=state, legal_issuer_id=issuer.id if state == "VERIFIED" else None,
            legal_issuer_identity_source=issuer.identity_source,
            legal_issuer_source_issuer_id=issuer.source_issuer_id,
            registry_as_of=DAY, finorg_last_update=NOW, observed_at=NOW,
            retrieved_at=NOW, ingested_at=NOW, diagnostic_codes=[], evidence_fingerprint=digest(),
        ))
        save(CbrBankSubjectLegalIssuerProfile(
            reporting_subject_id=row.id, current_evidence_id=evidence.id, bridge_state=state,
            legal_issuer_id=issuer.id if state == "VERIFIED" else None,
            legal_issuer_identity_source=issuer.identity_source,
            legal_issuer_source_issuer_id=issuer.source_issuer_id,
            last_observed_at=NOW, last_resolved_at=NOW,
        ))
        return row
    def metric(subject, when=DAY, code="N1.0", value="11.25", form="0409135", publication=None):
        ratio = form == "0409135"
        unit = "PERCENT" if ratio else "RUB"
        artifact = save(CbrBankSourceArtifact(
            source_url="https://example.invalid/" + digest(), artifact_filename="fixture.rar",
            form=form, report_date=when, content_bytes=b"x", compressed_size=1,
            content_sha256=digest(), content_type="application/octet-stream",
            first_discovered_at=NOW, first_retrieved_at=NOW, ingested_at=NOW,
            parser_contract_version="fixture", archive_runtime_contract="fixture",
            artifact_fingerprint=digest(),
        ))
        snapshot = save(CbrBankReportSnapshot(
            artifact_id=artifact.id, form=form, report_date=when, value_member_name="VALUE.DBF",
            member_schema_inventory=[], form_schema_fingerprint=digest(), parser_contract_version="fixture",
            observed_at=NOW, retrieved_at=NOW, ingested_at=NOW,
            publication_status="KNOWN" if publication else "UNKNOWN", publication_at=publication,
            record_count=1, subject_count=1, subject_set_sha256=digest(),
            observation_set_sha256=digest(), snapshot_fingerprint=digest(),
        ))
        raw = save(CbrBankRawObservation(
            snapshot_id=snapshot.id, reporting_subject_id=subject.id, form=form,
            report_date=when, subject_regn=subject.subject_regn, archive_member_name="VALUE.DBF",
            source_row_number=1, source_row_fingerprint=digest(), source_value_field="C3", source_code=code,
            source_dimensions=[["C1", code]], source_fields_sha256=digest(), raw_value_text=value,
            parsed_decimal_value=D(value), disclosure_state="PUBLIC_VALUE",
            source_unit=unit if ratio else "RUB_THOUSANDS", source_currency=None if ratio else "RUB",
            source_multiplier=None if ratio else 1000, parser_contract_version="fixture",
            ingested_at=NOW, observation_fingerprint=digest(),
        ))
        normalized = save(CbrBankNormalizedObservation(
            raw_observation_id=raw.id, form=form, report_date=when, subject_regn=subject.subject_regn,
            source_code=code, source_dimensions=[["C1", code]], source_disclosure_state="PUBLIC_VALUE",
            value_state="VALUE", normalized_value=D(value), normalized_unit=unit,
            normalized_currency=None if ratio else "RUB", transformation_kind="IDENTITY" if ratio else "SCALE_BY_SOURCE_MULTIPLIER",
            applied_multiplier=None if ratio else 1000, item_fingerprint=digest(), normalization_fingerprint=digest(),
        ))
        row = save(CbrBankCreditMetric(
            normalized_observation_id=normalized.id, subject_regn=subject.subject_regn,
            report_date=when, metric_key=("CBR_135_" + code.replace(".", "_") if ratio else "CBR_123_" + code),
            metric_family="REGULATORY_RATIO" if ratio else "REGULATORY_CAPITAL", source_form=form,
            source_code=code, metric_value=D(value), metric_unit=unit, metric_fingerprint=digest(),
        ))
        return SimpleNamespace(metric=row, normalized=normalized, raw=raw, snapshot=snapshot, artifact=artifact)
    return SimpleNamespace(bond=bond, issuer=issuer, mapping=mapping, rating=rating,
                           subject=subject, metric=metric, save=save)


def build(db, seed):
    return BondCreditFeatureService(db).build_for_bond(seed.bond.id, DAY)


@pytest.fixture
def bank(seed):
    issuer = seed.issuer()
    seed.mapping(issuer)
    return seed.subject(issuer)


def test_existing_bond_empty_evidence_neutral_and_deterministic(db_session, seed):
    result = build(db_session, seed)
    assert result.bond_id == seed.bond.id
    assert result.issuer_link_status == "MAPPING_MISSING"
    assert result.bank_subject_status == "ISSUER_NOT_VERIFIED"
    assert result.bond_ratings == result.issuer_ratings == result.bank_metrics == []
    assert result.bank_report_date is None and result.bank_report_age_days is None
    assert result.quality_flags == ["BANK_CREDIT_METRICS_MISSING", "BANK_SUBJECT_NOT_VERIFIED",
        "BOND_RATING_MISSING", "ISSUER_RATING_MISSING", "LEGAL_ISSUER_MAPPING_MISSING"]
    assert not any(v for k, v in result.availability.model_dump().items() if k.startswith("has_"))
    assert not result.pit_ready
    assert result.model_dump_json() == build(db_session, seed).model_dump_json()
    assert db_session.query(CreditDefaultEvent).count() == 0
    assert "default_free" not in result.model_dump_json()


def test_missing_bond(db_session):
    with pytest.raises(HTTPException) as exc:
        BondCreditFeatureService(db_session).build_for_bond(999999, DAY)
    assert exc.value.status_code == 404 and exc.value.detail == "Bond not found"


@pytest.mark.parametrize("state,status", [("observed", "MAPPING_NOT_VERIFIED"),
    ("unknown", "MAPPING_NOT_VERIFIED"), ("conflict", "MAPPING_CONFLICT")])
def test_unverified_mapping_preserves_only_bond_evidence(db_session, seed, state, status):
    issuer = seed.issuer()
    seed.mapping(issuer, state=state)
    seed.rating(issuer)
    seed.rating()
    seed.metric(seed.subject(issuer))
    result = build(db_session, seed)
    assert result.issuer_link_status == status
    assert result.availability.has_bond_rating_event
    assert result.issuer_ratings == result.bank_metrics == []
    assert result.legal_issuer_id is None


def test_missing_mapping_preserves_bond_rating(db_session, seed):
    seed.rating(seed.issuer())
    seed.rating()
    result = build(db_session, seed)
    assert result.availability.has_bond_rating_value
    assert not result.availability.has_issuer_rating_event


def test_verified_identity_exact_and_no_inn_name_company_fallback(db_session, seed):
    distractor = seed.issuer(source_id="same-inn-and-name")
    canonical = seed.issuer(source_id="canonical")
    mapping = seed.mapping(canonical)
    seed.rating(distractor, value="ruAAA")
    actual = seed.rating(canonical, value="AA(RU)")
    result = build(db_session, seed)
    assert result.legal_issuer_id == canonical.id
    assert result.provenance.bond_legal_issuer_profile_id == mapping.id
    assert result.provenance.mapping_source_issuer_id == "canonical"
    assert result.provenance.legal_issuer_identity_source == "moex_security_reference"
    assert result.issuer_ratings[0].events[0].event_id == actual.id


@pytest.mark.parametrize("state", ["observed", "conflict", "absent"])
def test_legal_issuer_missing_or_unverified_no_fallback(db_session, seed, state):
    distractor = seed.issuer()
    seed.rating(distractor)
    if state == "absent":
        seed.mapping(distractor, source_id="no-canonical-row")
    else:
        seed.mapping(seed.issuer(state=state))
    result = build(db_session, seed)
    assert result.issuer_link_status == "LEGAL_ISSUER_NOT_VERIFIED"
    assert not result.availability.has_verified_legal_issuer
    assert result.issuer_ratings == []


def test_verified_mapping_requires_source(db_session, seed):
    issuer = seed.issuer()
    seed.mapping(issuer, source=None)
    assert build(db_session, seed).issuer_link_status == "MAPPING_NOT_VERIFIED"


@pytest.mark.parametrize("issuer_only", [True, False])
def test_no_rating_inheritance_in_either_direction(db_session, seed, issuer_only):
    issuer = seed.issuer()
    seed.mapping(issuer)
    seed.rating(issuer if issuer_only else None)
    result = build(db_session, seed)
    assert result.availability.has_bond_rating_value is (not issuer_only)
    assert result.availability.has_issuer_rating_value is issuer_only


def test_all_agencies_native_fields_and_provider_preserved(db_session, seed):
    selected = [seed.rating(agency=agency, value=value, provider="CBR_RATINGS" if agency == "ACRA" else None)
                for agency, value in [("NKR", "AAA.ru"), ("EXPERT_RA", "ruAAA"), ("ACRA", "AAA(RU)"), ("NRA", "AAA|ru|")]]
    result = build(db_session, seed)
    assert result.availability.bond_rating_agencies == ["ACRA", "EXPERT_RA", "NKR", "NRA"]
    events = [block.events[0] for block in result.bond_ratings]
    assert [e.rating_value_raw for e in events] == ["AAA(RU)", "ruAAA", "AAA.ru", "AAA|ru|"]
    assert events[0].source_provider == "CBR_RATINGS" and events[0].rating_agency == "ACRA"
    for output in events:
        original = next(r for r in selected if r.id == output.event_id)
        assert output.artifact_id == original.artifact_id
        artifact = db_session.get(CreditRiskSourceArtifact, output.artifact_id)
        assert output.source_artifact_sha256 == artifact.content_sha256
        assert output.artifact_retrieved_at is not None
        for field in ("source_object_id", "source_bond_isin", "source_issuer_inn", "event_fingerprint",
                      "rating_scale_raw", "rating_action_raw", "rating_outlook_raw", "rating_watch_raw"):
            assert getattr(output, field) == getattr(original, field)


@pytest.mark.parametrize("target_issuer", [False, True])
@pytest.mark.parametrize("precision", ["DATE", "TIMESTAMP"])
def test_known_future_publication_filtered_before_latest_date(db_session, seed, target_issuer, precision):
    issuer = seed.issuer()
    seed.mapping(issuer)
    target = issuer if target_issuer else None
    previous = seed.rating(target, when=DAY - timedelta(days=1))
    publication = DAY + timedelta(days=1) if precision == "DATE" else NOW
    seed.rating(target, precision=precision, publication=publication)
    result = build(db_session, seed)
    blocks = result.issuer_ratings if target_issuer else result.bond_ratings
    assert blocks[0].events[0].event_id == previous.id


@pytest.mark.parametrize("precision", ["DATE", "TIMESTAMP"])
def test_publication_on_boundary_allowed(db_session, seed, precision):
    publication = DAY if precision == "DATE" else datetime(2026, 9, 17, 23, 59, tzinfo=timezone.utc)
    row = seed.rating(precision=precision, publication=publication)
    result = build(db_session, seed)
    assert result.bond_ratings[0].events[0].event_id == row.id
    assert "BOND_RATING_PUBLICATION_UNKNOWN" not in result.quality_flags


def test_rating_future_unrelated_and_unresolved_excluded(db_session, seed):
    actual = seed.rating()
    seed.rating(when=DAY + timedelta(days=1))
    seed.rating(state="UNRESOLVED")
    seed.rating(state="AMBIGUOUS")
    other = seed.save(Bond(company_id=seed.bond.company_id, name="Same issuer name", isin="RU000A999999"))
    seed.rating(other)
    result = build(db_session, seed)
    assert result.bond_ratings[0].event_count == 1
    assert result.bond_ratings[0].events[0].event_id == actual.id


def test_latest_rating_date_is_independent_per_agency(db_session, seed):
    seed.rating(when=DAY - timedelta(days=2), agency="ACRA")
    acra = seed.rating(agency="ACRA")
    expert = seed.rating(when=DAY - timedelta(days=1), agency="EXPERT_RA", value="ruAAA")
    result = build(db_session, seed)
    assert [b.latest_event_date for b in result.bond_ratings] == [DAY, DAY - timedelta(days=1)]
    assert [b.events[0].event_id for b in result.bond_ratings] == [acra.id, expert.id]


@pytest.mark.parametrize("issuer_target", [False, True])
def test_same_day_events_all_retained_and_unknown_flag(db_session, seed, issuer_target):
    issuer = seed.issuer()
    seed.mapping(issuer)
    target = issuer if issuer_target else None
    seed.rating(target, when=DAY - timedelta(days=1))
    first = seed.rating(target)
    second = seed.rating(target, value="AA(RU)")
    result = build(db_session, seed)
    block = (result.issuer_ratings if issuer_target else result.bond_ratings)[0]
    assert [e.event_id for e in block.events] == [first.id, second.id]
    assert block.event_count == 2 and block.latest_event_date == DAY
    prefix = "ISSUER" if issuer_target else "BOND"
    assert f"MULTIPLE_LATEST_{prefix}_RATING_EVENTS" in result.quality_flags
    assert f"{prefix}_RATING_PUBLICATION_UNKNOWN" in result.quality_flags


@pytest.mark.parametrize("issuer_target", [False, True])
def test_withdrawal_event_not_replaced_by_older_value(db_session, seed, issuer_target):
    issuer = seed.issuer()
    seed.mapping(issuer)
    target = issuer if issuer_target else None
    seed.rating(target, when=DAY - timedelta(days=1))
    withdrawn = seed.rating(target, value=None, action="Withdrawn")
    withdrawn.rating_outlook_raw = withdrawn.rating_watch_raw = None
    result = build(db_session, seed)
    block = (result.issuer_ratings if issuer_target else result.bond_ratings)[0]
    assert block.events[0].event_id == withdrawn.id
    assert block.events[0].rating_action_raw == "Withdrawn"
    assert not block.has_rating_value
    assert (result.availability.has_issuer_rating_event if issuer_target else result.availability.has_bond_rating_event)
    assert not (result.availability.has_issuer_rating_value if issuer_target else result.availability.has_bond_rating_value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_raw_value_availability_requires_nonempty_string(db_session, seed, value):
    seed.rating(value=value, action="Native action")
    result = build(db_session, seed)
    assert result.availability.has_bond_rating_event
    assert not result.availability.has_bond_rating_value


def test_exact_bank_join_provenance_and_no_metrics(db_session, seed, bank):
    result = build(db_session, seed)
    assert result.bank_subject_status == "VERIFIED"
    assert result.bank_reporting_subject_id == bank.id and result.bank_subject_regn == bank.subject_regn
    profile = db_session.get(CbrBankSubjectLegalIssuerProfile, bank.id)
    assert result.provenance.bank_bridge_current_evidence_id == profile.current_evidence_id
    assert result.provenance.verified_bank_reporting_subject_ids == [bank.id]
    assert result.availability.has_verified_bank_subject and not result.availability.has_bank_credit_metrics


@pytest.mark.parametrize("state", [None, "NOT_VERIFIED", "AMBIGUOUS", "NOT_FOUND"])
def test_no_verified_bridge_no_bank_inference(db_session, seed, state):
    issuer = seed.issuer()
    seed.mapping(issuer)
    if state:
        seed.metric(seed.subject(issuer, state=state))
    result = build(db_session, seed)
    assert result.bank_subject_status == "NO_VERIFIED_SUBJECT"
    assert result.bank_metrics == [] and result.bank_subject_regn is None


def test_multiple_verified_subjects_fail_closed(db_session, seed, bank):
    issuer = db_session.get(LegalIssuer, build(db_session, seed).legal_issuer_id)
    other = seed.subject(issuer, regn="2")
    seed.metric(bank)
    seed.metric(other)
    result = build(db_session, seed)
    assert result.bank_subject_status == "AMBIGUOUS_VERIFIED_SUBJECT"
    assert result.bank_metrics == [] and result.bank_reporting_subject_id is None
    assert result.provenance.verified_bank_reporting_subject_ids == [bank.id, other.id]
    assert "BANK_SUBJECT_AMBIGUOUS" in result.quality_flags


def test_unrelated_verified_bank_subject_and_metrics_not_used(db_session, seed, bank):
    unrelated = seed.subject(seed.issuer(), regn="2")
    seed.metric(unrelated)
    actual = seed.metric(bank, when=DAY - timedelta(days=1))
    result = build(db_session, seed)
    assert result.bank_subject_status == "VERIFIED"
    assert result.bank_subject_regn == bank.subject_regn
    assert [e.metric_id for e in result.bank_metrics] == [actual.metric.id]
    assert result.bank_report_date == DAY - timedelta(days=1)


def test_bank_latest_date_no_mixing_all_keys_and_duplicates(db_session, seed, bank):
    seed.metric(bank, when=DAY - timedelta(days=30), code="N18")
    first = seed.metric(bank, code="N2")
    second = seed.metric(bank, code="N1.0", value="11.25")
    third = seed.metric(bank, code="N1.0", value="12.50")
    result = build(db_session, seed)
    assert [e.metric_id for e in result.bank_metrics] == [second.metric.id, third.metric.id, first.metric.id]
    assert [e.metric_value for e in result.bank_metrics] == [D("11.25"), D("12.50"), D("11.25")]
    assert result.bank_report_date == DAY and result.bank_report_age_days == 0
    assert "BANK_METRIC_DUPLICATE_KEY_EVIDENCE" in result.quality_flags
    assert "BANK_METRIC_PUBLICATION_UNKNOWN" in result.quality_flags


def test_future_report_and_future_publication_filtered_before_latest_date(db_session, seed, bank):
    earlier = seed.metric(bank, when=DAY - timedelta(days=1))
    seed.metric(bank, when=DAY + timedelta(days=1))
    seed.metric(bank, publication=NOW)
    result = build(db_session, seed)
    assert [e.metric_id for e in result.bank_metrics] == [earlier.metric.id]
    assert result.bank_report_age_days == 1


def test_only_future_bank_evidence_returns_null_date(db_session, seed, bank):
    seed.metric(bank, publication=NOW)
    result = build(db_session, seed)
    assert not result.availability.has_bank_credit_metrics
    assert result.bank_report_date is None and result.bank_report_age_days is None


def test_bank_publication_boundary_and_lineage_units(db_session, seed, bank):
    ratio = seed.metric(bank, publication=datetime(2026, 9, 17, 23, 59, tzinfo=timezone.utc))
    capital = seed.metric(bank, code="000", form="0409123", value="123000")
    result = build(db_session, seed)
    assert [e.metric_unit for e in result.bank_metrics] == ["RUB", "PERCENT"]
    assert [e.metric_value for e in result.bank_metrics] == [D("123000"), D("11.25")]
    for output, original in zip(result.bank_metrics, (capital, ratio)):
        assert type(output.metric_value) is Decimal
        assert output.normalized_observation_id == original.normalized.id
        assert output.raw_observation_id == original.raw.id
        assert output.report_snapshot_id == original.snapshot.id
        assert output.source_artifact_id == original.artifact.id
        assert output.source_artifact_sha256 == original.artifact.content_sha256
        assert output.normalization_fingerprint == original.normalized.normalization_fingerprint
        assert output.metric_fingerprint == original.metric.metric_fingerprint
        assert output.source_dimensions == original.normalized.source_dimensions
        assert output.source_disclosure_state == "PUBLIC_VALUE"
        assert output.snapshot_retrieved_at is not None
    assert result.bank_metrics[1].snapshot_publication_status == "KNOWN"


@pytest.mark.parametrize("object_name,field,value", [
    ("normalized", "subject_regn", "2"), ("raw", "subject_regn", "2"),
    ("raw", "reporting_subject_id", 99999),
    ("normalized", "report_date", DAY - timedelta(days=1)),
    ("raw", "report_date", DAY - timedelta(days=1)),
    ("snapshot", "report_date", DAY - timedelta(days=1)),
    ("artifact", "report_date", DAY - timedelta(days=1)),
    ("normalized", "form", "0409123"), ("raw", "form", "0409123"),
    ("snapshot", "form", "0409123"), ("artifact", "form", "0409123"),
    ("normalized", "source_code", "N2"), ("normalized", "normalized_unit", "RUB"),
    ("normalized", "normalized_value", D("99")), ("metric", "metric_value", D("NaN")),
])
def test_lineage_contradictions_excluded_without_repair(db_session, seed, bank, object_name, field, value):
    original = seed.metric(bank)
    setattr(getattr(original, object_name), field, value)
    if object_name == "artifact":
        # Artifact provenance is selected as columns, excluding its binary bytes.
        db_session.flush()
    result = build(db_session, seed)
    assert result.bank_metrics == []
    assert "BANK_METRIC_LINEAGE_MISMATCH" in result.quality_flags
    stored_value = getattr(getattr(original, object_name), field)
    assert (stored_value.is_nan() if isinstance(value, Decimal) and value.is_nan() else stored_value == value)


def test_incomplete_lineage_excluded(db_session, seed, bank):
    original = seed.metric(bank)
    original.metric.normalized_observation_id = 99999
    db_session.flush()
    result = build(db_session, seed)
    assert result.bank_metrics == [] and "BANK_METRIC_LINEAGE_MISMATCH" in result.quality_flags


def test_invalid_latest_bank_row_does_not_hide_older_eligible_date(db_session, seed, bank):
    old = seed.metric(bank, when=DAY - timedelta(days=1))
    newest = seed.metric(bank)
    newest.raw.subject_regn = "2"
    result = build(db_session, seed)
    assert [m.metric_id for m in result.bank_metrics] == [old.metric.id]
    assert result.bank_report_date == DAY - timedelta(days=1)


@pytest.mark.parametrize("timestamp,expected", [
    (datetime(2026, 9, 18, 0, 30, tzinfo=timezone(timedelta(hours=3))), DAY),
    (datetime(2026, 9, 17, 23, 30, tzinfo=timezone(timedelta(hours=-3))), DAY + timedelta(days=1)),
    (datetime(2026, 9, 17, 23, 30), DAY),
])
def test_utc_calendar_boundary(timestamp, expected):
    assert _utc_date(timestamp) == expected


@pytest.mark.parametrize("future", [True, False])
def test_offset_publication_filters_rating_and_bank(db_session, seed, bank, future):
    timestamp = (datetime(2026, 9, 17, 23, 30, tzinfo=timezone(timedelta(hours=-3))) if future
                 else datetime(2026, 9, 18, 0, 30, tzinfo=timezone(timedelta(hours=3))))
    rating = seed.rating(precision="TIMESTAMP", publication=timestamp)
    metric = seed.metric(bank, publication=timestamp)
    # Keep aware fixture objects loaded: SQLite persists these timestamps without tz.
    result = build(db_session, seed)
    assert result.availability.has_bond_rating_event is (not future)
    assert result.availability.has_bank_credit_metrics is (not future)
    assert rating.publication_at == metric.snapshot.publication_at


def test_read_only_sql_capture_clean_and_pending_state(db_session, seed, bank, monkeypatch):
    seed.rating()
    seed.metric(bank)
    db_session.commit()
    bond_id = seed.bond.id
    before_json = BondCreditFeatureService(db_session).build_for_bond(bond_id, DAY).model_dump_json()
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    deletion = Company(name="Caller deletion", ticker="DELETE269")
    db_session.add(deletion)
    db_session.commit()
    db_session.refresh(seed.bond)
    seed.bond.name = "Caller pending edit"
    pending = Company(name="Caller pending insert", ticker="PENDING269")
    db_session.add(pending)
    db_session.delete(deletion)
    db_session.autoflush = True
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        raise AssertionError("Service must not write or flush")
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for name in ("add", "add_all", "delete", "flush", "commit"):
                patch.setattr(db_session, name, forbidden)
            result = BondCreditFeatureService(db_session).build_for_bond(bond_id, DAY)
            assert result.model_dump_json() == before_json
        assert before == (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
        assert pending.id is None
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        assert all("content_bytes" not in sql for sql in statements)
        assert all("credit_default_events" not in sql for sql in statements)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
        db_session.rollback()


@pytest.mark.parametrize("arguments", [{"bond_id": True}, {"bond_id": False}, {"bond_id": 0},
    {"bond_id": -1}, {"bond_id": 1.0}, {"bond_id": "1"}, {"bond_id": None},
    {"as_of_date": datetime(2026, 9, 17)}, {"as_of_date": "2026-09-17"}, {"as_of_date": None}])
def test_invalid_inputs(db_session, arguments):
    with pytest.raises(ValueError):
        BondCreditFeatureService(db_session).build_for_bond(**{"bond_id": 1, "as_of_date": DAY, **arguments})


def test_contract_roundtrip_flags_capabilities_and_pit(db_session, seed, bank):
    seed.rating()
    seed.metric(bank)
    result = build(db_session, seed)
    assert result.contract_version == "bond-credit-feature-v1"
    assert not result.pit_ready
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert result.model_dump_json() == build(db_session, seed).model_dump_json()
    assert BondCreditFeatureView.model_validate_json(result.model_dump_json()) == result
    for field in ("issuer_to_bond_rating_inheritance", "bond_to_issuer_rating_inheritance",
                  "cross_agency_normalization_ready", "unified_credit_score_ready", "pd_model_ready",
                  "default_ingestion_ready", "default_feature_join_ready", "credit_adjusted_spread_ready",
                  "liquidity_adjusted_spread_ready"):
        assert getattr(result.capabilities, field) is False
    assert result.capabilities.credit_feature_join_ready
    assert result.capabilities.default_evidence_schema_ready
    with pytest.raises(ValidationError):
        result.pit_ready = True
    with pytest.raises(ValidationError):
        BondCreditFeatureView.model_validate({**result.model_dump(), "pit_ready": True})
    with pytest.raises(ValidationError):
        BondCreditFeatureView.model_validate({**result.model_dump(), "unexpected": 1})


def test_static_safety_and_no_interpretation_fields(db_session, seed):
    import app.services.bond_credit_feature_service as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imports = " ".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert not any(word in imports for word in ("requests", "httpx", "urllib", "clients", "strategy",
                                               "scoring", "credit_metrics", "credit_risk_evidence.service"))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and ast.unparse(node.func.value) == "self.db"}
    assert not calls & {"commit", "flush", "add", "add_all", "delete"}
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not names & {"insert", "update", "delete", "float"}
    forbidden = {"rating", "credit_rating", "credit_score", "rating_score", "risk_score", "PD",
        "probability_of_default", "expected_loss", "LGD", "EL", "investment_grade", "junk", "safe", "unsafe",
        "good_credit", "bad_credit", "weak_credit", "strong_credit", "buy", "sell", "hold", "attractive",
        "unattractive", "alpha", "expected_return", "recommendation", "portfolio_weight", "default_free"}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden & value.keys()
            for member in value.values():
                check(member)
        elif isinstance(value, list):
            for member in value:
                check(member)
    check(build(db_session, seed).model_dump())
