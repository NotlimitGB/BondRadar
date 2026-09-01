from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Float, Numeric, func, select
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence,
    CbrBankSubjectLegalIssuerProfile,
)
from app.models.legal_issuer import LegalIssuer
from app.services.cbr_bank_financial_evidence import (
    CbrBankRawFinancialEvidenceStore,
    extract_exact_form_evidence,
)
from app.services.cbr_bank_financial_evidence.fingerprints import sha256_canonical
from app.services.cbr_bank_financial_evidence.lexical import _parse_numeric_lexical
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
)
from app.services.cbr_legal_issuer_bridge.contracts import (
    CbrBridgeState,
    CbrLegalIssuerBridgeResult,
    CbrLegalIssuerBridgeSnapshot,
    identifier_set_sha256,
)


FIXTURES = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
REPORT_DATE = date(2026, 8, 1)
T1 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
EXPECTED_ROWS = {
    "0409101": 25_654,
    "0409102": 10_079,
    "0409123": 1_400,
    "0409135": 1_709,
}


def _artifact(form: CbrBankForm) -> CbrBankArtifact:
    filename = f"{form.short_code}-20260801.rar"
    content = (FIXTURES / filename).read_bytes()
    reference = CbrArtifactReference(
        form=form,
        source_href=f"/vfs/credit/forms/{filename}",
        source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
        artifact_filename=filename,
        report_date=REPORT_DATE,
        discovered_at=T1,
    )
    return CbrBankArtifact(
        reference=reference,
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        content_type="application/octet-stream",
        retrieved_at=T1,
    )


@pytest.fixture(scope="module")
def task251_bundle():
    return CbrBankRegulatoryBundleService().build_snapshot(
        report_date=REPORT_DATE,
        artifacts=tuple(_artifact(form) for form in CbrBankForm),
    )


def _single_form_bundle(task251_bundle, form: CbrBankForm):
    result = next(item for item in task251_bundle.forms if item.form == form)
    return replace(
        task251_bundle,
        forms=(result,),
        subjects_by_form=((form.value, len(result.subjects)),),
        records_by_form=((form.value, len(result.records)),),
        subject_set_hashes=(
            next(item for item in task251_bundle.subject_set_hashes if item[0] == form.value),
        ),
        cross_form_overlap=(),
        exclusive_membership_counts=((form.short_code, len(result.subjects)),),
    )


def test_exact_raw_lexical_feasibility_for_all_approved_fixtures(
    task251_bundle,
) -> None:
    counts = {}
    blanks = {}
    zeros = {}
    for form in task251_bundle.forms:
        exact = extract_exact_form_evidence(form)
        counts[form.form.value] = len(exact.observations)
        blanks[form.form.value] = sum(
            item.raw_value_text == "" for item in exact.observations
        )
        zeros[form.form.value] = sum(
            item.parsed_decimal_value == Decimal("0")
            for item in exact.observations
            if item.parsed_decimal_value is not None
        )
        assert all(
            item.parsed_decimal_value == item.record.source_value
            for item in exact.observations
        )
        assert all(len(item.source_fields_sha256) == 64 for item in exact.observations)
        assert all(len(item.source_row_fingerprint) == 64 for item in exact.observations)

    assert counts == EXPECTED_ROWS
    assert blanks == {"0409101": 0, "0409102": 6, "0409123": 0, "0409135": 0}
    assert zeros == {"0409101": 2827, "0409102": 3316, "0409123": 335, "0409135": 125}
    form_101 = extract_exact_form_evidence(task251_bundle.forms[0])
    assert form_101.observations[0].raw_value_text == "1330000.0000"
    assert form_101.observations[0].parsed_decimal_value == Decimal("1330000.0000")
    form_135 = extract_exact_form_evidence(task251_bundle.forms[-1])
    assert form_135.observations[0].raw_value_text == "0.169"
    assert form_135.observations[0].parsed_decimal_value.as_tuple().exponent == -3


def test_raw_numeric_lexical_padding_comma_blank_and_invalid_values() -> None:
    assert _parse_numeric_lexical(b"     001.2300 ") == (
        "001.2300",
        Decimal("1.2300"),
    )
    assert _parse_numeric_lexical(b"  -0,125 ") == ("-0,125", Decimal("-0.125"))
    assert _parse_numeric_lexical(b"        ") == ("", None)
    for invalid in (b"not-a-number", b"NaN", b"Infinity", b"-Infinity"):
        with pytest.raises(CbrSourceError):
            _parse_numeric_lexical(invalid)


def test_models_have_six_tables_binary_numeric_json_and_required_fk_actions() -> None:
    tables = {
        CbrBankReportingSubject.__table__.name,
        CbrBankSourceArtifact.__table__.name,
        CbrBankReportSnapshot.__table__.name,
        CbrBankRawObservation.__table__.name,
        CbrBankSubjectLegalIssuerEvidence.__table__.name,
        CbrBankSubjectLegalIssuerProfile.__table__.name,
    }
    assert tables == {
        "cbr_bank_reporting_subjects",
        "cbr_bank_source_artifacts",
        "cbr_bank_report_snapshots",
        "cbr_bank_raw_observations",
        "cbr_bank_subject_legal_issuer_evidence",
        "cbr_bank_subject_legal_issuer_profiles",
    }
    parsed_type = CbrBankRawObservation.__table__.c.parsed_decimal_value.type
    assert isinstance(parsed_type, Numeric)
    assert not isinstance(parsed_type, Float)
    assert CbrBankSourceArtifact.__table__.c.content_bytes.type.python_type is bytes
    foreign_keys = {
        (fk.parent.table.name, fk.parent.name): fk.ondelete
        for table in (
            CbrBankReportSnapshot.__table__,
            CbrBankRawObservation.__table__,
            CbrBankSubjectLegalIssuerEvidence.__table__,
            CbrBankSubjectLegalIssuerProfile.__table__,
        )
        for fk in table.foreign_keys
    }
    assert foreign_keys[("cbr_bank_report_snapshots", "artifact_id")] == "RESTRICT"
    assert foreign_keys[("cbr_bank_raw_observations", "snapshot_id")] == "RESTRICT"
    assert foreign_keys[("cbr_bank_raw_observations", "reporting_subject_id")] == "RESTRICT"
    assert foreign_keys[("cbr_bank_subject_legal_issuer_evidence", "legal_issuer_id")] == "SET NULL"
    assert foreign_keys[("cbr_bank_subject_legal_issuer_profiles", "legal_issuer_id")] == "SET NULL"


def test_full_fixture_persistence_readback_and_exact_retry(
    db_session: Session,
    task251_bundle,
) -> None:
    store = CbrBankRawFinancialEvidenceStore(db_session)
    first = store.persist_bundle(task251_bundle, observed_at=T1, ingested_at=T1)
    assert first.subjects.inserted == 353
    assert first.artifacts.inserted == 4
    assert first.snapshots.inserted == 4
    assert first.observations.inserted == 38_842
    assert first.observation_count == 38_842
    assert db_session.scalar(select(func.count()).select_from(CbrBankReportingSubject)) == 353
    assert db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)) == 4
    assert db_session.scalar(select(func.count()).select_from(CbrBankReportSnapshot)) == 4
    assert db_session.scalar(select(func.count()).select_from(CbrBankRawObservation)) == 38_842

    blank_rows = list(
        db_session.execute(
            select(CbrBankRawObservation).where(
                CbrBankRawObservation.form == "0409102",
                CbrBankRawObservation.disclosure_state == "PUBLIC_VALUE_BLANK",
            )
        ).scalars()
    )
    assert len(blank_rows) == 6
    assert all(row.raw_value_text == "" and row.parsed_decimal_value is None for row in blank_rows)
    zero = db_session.execute(
        select(CbrBankRawObservation).where(
            CbrBankRawObservation.form == "0409135",
            CbrBankRawObservation.raw_value_text == "0.000",
        )
    ).scalars().first()
    assert zero is not None and zero.parsed_decimal_value == Decimal("0")
    for artifact in db_session.execute(select(CbrBankSourceArtifact)).scalars():
        assert hashlib.sha256(artifact.content_bytes).hexdigest() == artifact.content_sha256
        assert len(artifact.content_bytes) == artifact.compressed_size

    second = store.persist_bundle(task251_bundle, observed_at=T1, ingested_at=T1)
    assert second.artifacts.inserted == second.snapshots.inserted == 0
    assert second.observations.inserted == 0
    assert second.artifacts.reused == 4
    assert second.snapshots.reused == 4
    assert second.observations.reused == 38_842
    assert db_session.scalar(select(func.count()).select_from(CbrBankRawObservation)) == 38_842


def test_artifact_hash_size_and_snapshot_contract_fail_closed(
    db_session: Session,
    task251_bundle,
) -> None:
    original = task251_bundle.forms[0]
    bad_artifact = replace(original.artifact, content_sha256="0" * 64)
    bad_form = replace(original, artifact=bad_artifact)
    bad_bundle = replace(task251_bundle, forms=(bad_form, *task251_bundle.forms[1:]))
    with pytest.raises(ValueError, match="hash or size"):
        CbrBankRawFinancialEvidenceStore(db_session).persist_bundle(
            bad_bundle, observed_at=T1, ingested_at=T1
        )
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)) == 0

    bad_hashes = replace(
        task251_bundle,
        subject_set_hashes=(("0409101", "0" * 64), *task251_bundle.subject_set_hashes[1:]),
    )
    with pytest.raises(ValueError, match="subject hash"):
        CbrBankRawFinancialEvidenceStore(db_session).persist_bundle(
            bad_hashes, observed_at=T1, ingested_at=T1
        )


def test_artifact_byte_revision_does_not_overwrite_prior_evidence(
    db_session: Session,
    task251_bundle,
) -> None:
    form = task251_bundle.forms[0]
    store = CbrBankRawFinancialEvidenceStore(db_session)
    first, inserted = store._persist_artifact(form, ingested_at=T1)
    assert inserted is True
    changed_bytes = form.artifact.content[:-1] + bytes([form.artifact.content[-1] ^ 1])
    changed_artifact = replace(
        form.artifact,
        content=changed_bytes,
        content_sha256=hashlib.sha256(changed_bytes).hexdigest(),
        compressed_size=len(changed_bytes),
    )
    second, inserted = store._persist_artifact(
        replace(form, artifact=changed_artifact), ingested_at=T1 + timedelta(seconds=1)
    )
    assert inserted is True
    assert first.id != second.id
    assert first.artifact_filename == second.artifact_filename
    assert first.content_sha256 != second.content_sha256
    assert db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)) == 2


def test_reporting_subject_regn_is_canonical_and_reused(db_session: Session) -> None:
    store = CbrBankRawFinancialEvidenceStore(db_session)
    subjects, first = store._persist_subjects({"00042"}, observed_at=T1)
    assert first.inserted == 1
    assert set(subjects) == {"42"}
    subjects, second = store._persist_subjects({"42"}, observed_at=T1)
    assert second.reused == 1
    assert set(subjects) == {"42"}
    for invalid in ("0", "-1", "abc", ""):
        with pytest.raises(ValueError):
            store._persist_subjects({invalid}, observed_at=T1)


def _bridge_snapshot(
    *,
    regn: str,
    state: CbrBridgeState,
    observed_at: datetime,
    legal_issuer: LegalIssuer | None = None,
) -> CbrLegalIssuerBridgeSnapshot:
    result = CbrLegalIssuerBridgeResult(
        regn=regn,
        bridge_state=state,
        ogrn="1027700000000",
        inn="7700000001",
        legal_issuer_id=legal_issuer.id if legal_issuer else None,
        legal_issuer_source_issuer_id=(
            legal_issuer.source_issuer_id if legal_issuer else None
        ),
        cbr_registry_name="Diagnostic CBR Name",
        finorg_name="Diagnostic FinOrg Name",
        legal_issuer_title=legal_issuer.issuer_title if legal_issuer else None,
        warnings=("title_mismatch_warning_only",),
        registry_as_of=observed_at.date(),
        finorg_last_update=observed_at,
        retrieved_at=observed_at,
    )
    verified = (regn,) if state == CbrBridgeState.VERIFIED else ()
    return CbrLegalIssuerBridgeSnapshot(
        requested_regns=(regn,),
        registry_as_of=observed_at.date(),
        finorg_last_update=observed_at,
        retrieved_at=observed_at,
        registry_records=(),
        finorg_records=(),
        bridge_results=(result,),
        state_counts=((state.value, 1),),
        regn_set_hash=identifier_set_sha256((regn,)),
        source_resolved_regn_set_hash=identifier_set_sha256((regn,)),
        legal_issuer_verified_regn_set_hash=identifier_set_sha256(verified),
        warnings=("current_identity_sources_only",),
        legal_issuer_evaluation_performed=True,
    )


def test_identity_states_and_a_b_a_reobservation(
    db_session: Session,
    task251_bundle,
) -> None:
    bundle = _single_form_bundle(task251_bundle, CbrBankForm.FORM_135)
    regn = bundle.forms[0].subjects[0]
    issuer_a = LegalIssuer(
        identity_source="moex_security_reference",
        source_issuer_id="A",
        resolution_state="verified",
        issuer_title="Issuer A",
        issuer_inn="7700000001",
    )
    issuer_b = LegalIssuer(
        identity_source="moex_security_reference",
        source_issuer_id="B",
        resolution_state="verified",
        issuer_title="Issuer B",
        issuer_inn="7700000002",
    )
    db_session.add_all([issuer_a, issuer_b])
    db_session.flush()
    store = CbrBankRawFinancialEvidenceStore(db_session)
    t2, t3 = T1 + timedelta(days=1), T1 + timedelta(days=2)
    for instant, issuer in ((T1, issuer_a), (t2, issuer_b), (t3, issuer_a)):
        result = store.persist_bundle(
            bundle,
            observed_at=instant,
            ingested_at=instant,
            identity_snapshot=_bridge_snapshot(
                regn=regn,
                state=CbrBridgeState.VERIFIED,
                observed_at=instant,
                legal_issuer=issuer,
            ),
        )
        assert result.identity_evidence.inserted == 1
    evidence = list(
        db_session.execute(
            select(CbrBankSubjectLegalIssuerEvidence)
            .where(CbrBankSubjectLegalIssuerEvidence.subject_regn == regn)
            .order_by(CbrBankSubjectLegalIssuerEvidence.observed_at)
        ).scalars()
    )
    assert [row.legal_issuer_source_issuer_id for row in evidence] == ["A", "B", "A"]
    subject = db_session.execute(
        select(CbrBankReportingSubject).where(
            CbrBankReportingSubject.subject_regn == regn
        )
    ).scalar_one()
    profile = db_session.get(CbrBankSubjectLegalIssuerProfile, subject.id)
    assert profile.bridge_state == "VERIFIED"
    assert profile.legal_issuer_id == issuer_a.id
    assert profile.last_observed_at == t3.replace(tzinfo=None) or profile.last_observed_at == t3

    retry = store.persist_bundle(
        bundle,
        observed_at=t3,
        ingested_at=t3,
        identity_snapshot=_bridge_snapshot(
            regn=regn,
            state=CbrBridgeState.VERIFIED,
            observed_at=t3,
            legal_issuer=issuer_a,
        ),
    )
    assert retry.identity_evidence.reused == 1
    assert retry.identity_profiles.noop == 1
    assert db_session.scalar(
        select(func.count()).select_from(CbrBankSubjectLegalIssuerEvidence)
    ) == 3


@pytest.mark.parametrize(
    ("task252_state", "stored_state"),
    [
        (CbrBridgeState.LEGAL_ISSUER_NOT_FOUND, "NOT_FOUND"),
        (CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS, "AMBIGUOUS"),
        (CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED, "NOT_VERIFIED"),
        (CbrBridgeState.CBR_REGN_NOT_FOUND, "SOURCE_IDENTITY_BLOCKED"),
        (CbrBridgeState.LEGAL_ISSUER_NOT_EVALUATED, "NOT_EVALUATED"),
    ],
)
def test_non_verified_identity_never_blocks_raw_persistence_or_links_by_title(
    db_session: Session,
    task251_bundle,
    task252_state: CbrBridgeState,
    stored_state: str,
) -> None:
    bundle = _single_form_bundle(task251_bundle, CbrBankForm.FORM_135)
    regn = bundle.forms[0].subjects[0]
    result = CbrBankRawFinancialEvidenceStore(db_session).persist_bundle(
        bundle,
        observed_at=T1,
        ingested_at=T1,
        identity_snapshot=_bridge_snapshot(
            regn=regn, state=task252_state, observed_at=T1
        ),
    )
    assert result.observations.inserted == 1709
    evidence = db_session.scalar(select(CbrBankSubjectLegalIssuerEvidence))
    assert evidence.bridge_state == stored_state
    assert evidence.legal_issuer_id is None
    profile = db_session.scalar(select(CbrBankSubjectLegalIssuerProfile))
    assert profile.bridge_state == stored_state
    assert profile.legal_issuer_id is None


def test_caller_rollback_removes_partial_ingestion(
    db_session: Session,
    task251_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CbrBankRawFinancialEvidenceStore(db_session)

    def fail(name: str) -> None:
        if name == "raw_evidence":
            raise RuntimeError("injected failure")

    monkeypatch.setattr(store, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.persist_bundle(
            _single_form_bundle(task251_bundle, CbrBankForm.FORM_135),
            observed_at=T1,
            ingested_at=T1,
        )
    db_session.rollback()
    for model in (
        CbrBankReportingSubject,
        CbrBankSourceArtifact,
        CbrBankReportSnapshot,
        CbrBankRawObservation,
    ):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0


def test_fingerprint_json_order_is_deterministic_and_float_is_rejected() -> None:
    assert sha256_canonical({"b": 2, "a": [1, None]}) == sha256_canonical(
        {"a": [1, None], "b": 2}
    )
    with pytest.raises(TypeError):
        sha256_canonical({"unsafe": 1.25})
