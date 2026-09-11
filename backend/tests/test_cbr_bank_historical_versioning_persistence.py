from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Boolean, func, select
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION,
    CbrBankArtifactAvailabilityEvidence,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.historical_versioning import (
    EvidenceSource,
    PitSelectionStatus,
)
from app.services.cbr_bank_financial_evidence.historical_versioning_persistence import (
    load_artifact_versions,
    persist_artifact_availability_evidence,
    select_persisted_artifact_version_as_of,
)
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
)


FIXTURES = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
REPORT_DATE = date(2026, 8, 1)
T1 = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=10)
T3 = T2 + timedelta(days=10)


def _task251_artifact(*, retrieved_at: datetime = T1) -> CbrBankArtifact:
    form = CbrBankForm.FORM_135
    filename = "135-20260801.rar"
    content = (FIXTURES / filename).read_bytes()
    return CbrBankArtifact(
        reference=CbrArtifactReference(
            form=form,
            source_href=f"/vfs/credit/forms/{filename}",
            source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
            artifact_filename=filename,
            report_date=REPORT_DATE,
            discovered_at=T1,
        ),
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        content_type="application/octet-stream",
        retrieved_at=retrieved_at,
    )


@pytest.fixture(scope="module")
def one_form_bundle():
    return CbrBankRegulatoryBundleService().build_snapshot(
        report_date=REPORT_DATE,
        artifacts=(_task251_artifact(),),
    )


def _artifact_row(
    digest: str,
    *,
    report_date: date = date(2024, 4, 1),
    retrieved_at: datetime = T1,
) -> CbrBankSourceArtifact:
    return CbrBankSourceArtifact(
        source_url=f"https://www.cbr.ru/{digest}.rar",
        artifact_filename=f"{digest}.rar",
        form=CbrBankForm.FORM_102.value,
        report_date=report_date,
        content_bytes=b"x",
        content_sha256=digest,
        compressed_size=1,
        content_type="application/octet-stream",
        first_discovered_at=retrieved_at,
        first_retrieved_at=retrieved_at,
        ingested_at=retrieved_at,
        parser_contract_version="task251-test",
        archive_runtime_contract="test",
        artifact_fingerprint=digest,
    )


def test_model_contract_relationships_and_constraints() -> None:
    table = CbrBankArtifactAvailabilityEvidence.__table__
    assert table.name == "cbr_bank_artifact_availability_evidence"
    assert isinstance(table.c.exact_payload_bound.type, Boolean)
    assert table.c.contract_version.default.arg == (
        CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION
    )
    assert next(iter(table.c.artifact_id.foreign_keys)).ondelete == "RESTRICT"
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_cbr_artifact_availability_evidence_identity" in constraint_names
    for suffix in (
        "cbr_artifact_availability_evidence_contract_valid",
        "cbr_artifact_availability_evidence_source_valid",
        "cbr_artifact_availability_evidence_reference_valid",
    ):
        assert any(name.endswith(suffix) for name in constraint_names)
    assert {index.name for index in table.indexes} == {
        "ix_cbr_artifact_availability_evidence_artifact_observed"
    }
    assert CbrBankSourceArtifact.availability_evidence.property.back_populates == (
        "artifact"
    )
    assert CbrBankArtifactAvailabilityEvidence.artifact.property.back_populates == (
        "availability_evidence"
    )


def test_store_records_first_exact_replay_and_later_direct_observation(
    db_session: Session,
    one_form_bundle,
) -> None:
    store = CbrBankRawFinancialEvidenceStore(db_session)
    with patch.object(db_session, "commit", side_effect=AssertionError("commit forbidden")):
        first = store.persist_bundle(one_form_bundle, observed_at=T1, ingested_at=T1)
        replay = store.persist_bundle(one_form_bundle, observed_at=T1, ingested_at=T2)
        later_artifact = replace(one_form_bundle.forms[0].artifact, retrieved_at=T2)
        later_form = replace(one_form_bundle.forms[0], artifact=later_artifact)
        later_bundle = replace(one_form_bundle, forms=(later_form,))
        later = store.persist_bundle(later_bundle, observed_at=T2, ingested_at=T3)

    assert first.artifacts.inserted == 1
    assert replay.artifacts.reused == later.artifacts.reused == 1
    evidence = tuple(
        db_session.execute(
            select(CbrBankArtifactAvailabilityEvidence).order_by(
                CbrBankArtifactAvailabilityEvidence.observed_at
            )
        ).scalars()
    )
    assert len(evidence) == 2
    assert [row.evidence_source for row in evidence] == ["CBR_DIRECT", "CBR_DIRECT"]
    assert all(row.exact_payload_bound is True for row in evidence)
    assert all(row.source_reference.startswith("https://www.cbr.ru/") for row in evidence)
    observed = {
        row.observed_at.replace(tzinfo=timezone.utc)
        if row.observed_at.tzinfo is None
        else row.observed_at.astimezone(timezone.utc)
        for row in evidence
    }
    assert observed == {T1, T2}


def test_revision_preservation_adapter_and_as_of_selection_are_deterministic(
    db_session: Session,
) -> None:
    report_date = date(2024, 4, 1)
    sha_a, sha_b = "a" * 64, "b" * 64
    artifact_b = _artifact_row(sha_b, report_date=report_date, retrieved_at=T2)
    artifact_a = _artifact_row(sha_a, report_date=report_date, retrieved_at=T1)
    db_session.add_all((artifact_b, artifact_a))
    db_session.flush()
    persist_artifact_availability_evidence(
        db_session,
        artifact=artifact_b,
        evidence_source=EvidenceSource.CBR_DIRECT,
        observed_at=T2,
        exact_payload_bound=True,
        source_reference=artifact_b.source_url,
    )
    persist_artifact_availability_evidence(
        db_session,
        artifact=artifact_a,
        evidence_source=EvidenceSource.CBR_DIRECT,
        observed_at=T1,
        exact_payload_bound=True,
        source_reference=artifact_a.source_url,
    )
    _, inserted = persist_artifact_availability_evidence(
        db_session,
        artifact=artifact_a,
        evidence_source=EvidenceSource.CBR_DIRECT,
        observed_at=T1,
        exact_payload_bound=True,
        source_reference=artifact_a.source_url,
    )
    assert inserted is False

    loaded = load_artifact_versions(
        db_session, form=CbrBankForm.FORM_102, report_date=report_date
    )
    assert tuple(item.key.artifact_sha256 for item in loaded) == (sha_a, sha_b)
    before = select_persisted_artifact_version_as_of(
        db_session,
        form=CbrBankForm.FORM_102,
        report_date=report_date,
        as_of=T2 - timedelta(microseconds=1),
    )
    after = select_persisted_artifact_version_as_of(
        db_session,
        form=CbrBankForm.FORM_102,
        report_date=report_date,
        as_of=T2,
    )
    assert before.selected_version.key.artifact_sha256 == sha_a
    assert after.selected_version.key.artifact_sha256 == sha_b
    assert db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)) == 2


def test_unbound_and_current_restated_evidence_do_not_leak_backward(
    db_session: Session,
) -> None:
    report_date = date(2024, 6, 1)
    current = _artifact_row("c" * 64, report_date=report_date, retrieved_at=T3)
    db_session.add(current)
    db_session.flush()
    persist_artifact_availability_evidence(
        db_session,
        artifact=current,
        evidence_source=EvidenceSource.OTHER_ARCHIVE,
        observed_at=T1,
        exact_payload_bound=False,
        source_reference="archive-index-only",
    )
    result = select_persisted_artifact_version_as_of(
        db_session,
        form=CbrBankForm.FORM_102,
        report_date=report_date,
        as_of=T2,
    )
    assert result.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF
    persist_artifact_availability_evidence(
        db_session,
        artifact=current,
        evidence_source=EvidenceSource.CBR_DIRECT,
        observed_at=T3,
        exact_payload_bound=True,
        source_reference=current.source_url,
    )
    still_historical = select_persisted_artifact_version_as_of(
        db_session,
        form=CbrBankForm.FORM_102,
        report_date=report_date,
        as_of=T2,
    )
    assert still_historical.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF


def test_raw_observation_lineage_already_reaches_exact_artifact_version() -> None:
    assert CbrBankRawObservation.snapshot.property.mapper.class_ is CbrBankReportSnapshot
    assert CbrBankReportSnapshot.artifact.property.mapper.class_ is CbrBankSourceArtifact
    assert "artifact_id" not in CbrBankRawObservation.__table__.c


def test_persistence_input_rejects_naive_time_and_invalid_reference(
    db_session: Session,
) -> None:
    artifact = _artifact_row("d" * 64)
    db_session.add(artifact)
    db_session.flush()
    with pytest.raises(ValueError, match="timezone-aware"):
        persist_artifact_availability_evidence(
            db_session,
            artifact=artifact,
            evidence_source=EvidenceSource.CBR_DIRECT,
            observed_at=T1.replace(tzinfo=None),
            exact_payload_bound=True,
            source_reference=artifact.source_url,
        )
    with pytest.raises(ValueError, match="source_reference"):
        persist_artifact_availability_evidence(
            db_session,
            artifact=artifact,
            evidence_source=EvidenceSource.CBR_DIRECT,
            observed_at=T1,
            exact_payload_bound=True,
            source_reference="",
        )
