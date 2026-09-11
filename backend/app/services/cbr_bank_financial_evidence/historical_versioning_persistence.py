"""Persistence adapter for Task260A artifact availability evidence."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION,
    CbrBankArtifactAvailabilityEvidence,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.historical_versioning import (
    ArtifactAvailabilityEvidence,
    ArtifactVersion,
    ArtifactVersionKey,
    EvidenceSource,
    PitSelectionResult,
    select_artifact_version_as_of,
)
from app.services.cbr_bank_reporting.contracts import CbrBankForm


def _storage_datetime(session: Session, value: datetime) -> datetime:
    """Restore UTC lost by SQLite's timezone-naive DateTime test adapter."""
    if not isinstance(value, datetime):
        raise ValueError("persisted observed_at must be a datetime")
    if value.tzinfo is None:
        if session.get_bind().dialect.name != "sqlite":
            raise ValueError("persisted observed_at must be timezone-aware")
        return value.replace(tzinfo=timezone.utc)
    if value.utcoffset() is None:
        raise ValueError("persisted observed_at must have a valid UTC offset")
    return value.astimezone(timezone.utc)


def _validate_reference(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("source_reference must be a non-empty string up to 2048 chars")
    return value


def _identity_predicates(
    artifact_id: int,
    evidence: ArtifactAvailabilityEvidence,
    source_reference: str,
):
    return (
        CbrBankArtifactAvailabilityEvidence.artifact_id == artifact_id,
        CbrBankArtifactAvailabilityEvidence.evidence_source == evidence.source.value,
        CbrBankArtifactAvailabilityEvidence.observed_at == evidence.observed_at,
        CbrBankArtifactAvailabilityEvidence.exact_payload_bound
        == evidence.exact_payload_bound,
        CbrBankArtifactAvailabilityEvidence.source_reference == source_reference,
    )


def _validate_row(
    session: Session,
    row: CbrBankArtifactAvailabilityEvidence,
    *,
    artifact: CbrBankSourceArtifact,
    evidence: ArtifactAvailabilityEvidence,
    source_reference: str,
) -> None:
    if (
        row.artifact_id != artifact.id
        or row.contract_version
        != CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION
        or row.evidence_source != evidence.source.value
        or _storage_datetime(session, row.observed_at) != evidence.observed_at
        or type(row.exact_payload_bound) is not bool
        or row.exact_payload_bound != evidence.exact_payload_bound
        or row.source_reference != source_reference
    ):
        raise ValueError("persisted artifact availability evidence contract mismatch")


def persist_artifact_availability_evidence(
    session: Session,
    *,
    artifact: CbrBankSourceArtifact,
    evidence_source: EvidenceSource,
    observed_at: datetime,
    exact_payload_bound: bool,
    source_reference: str,
) -> tuple[CbrBankArtifactAvailabilityEvidence, bool]:
    """Insert or reuse one semantic availability observation; never commit."""
    if not isinstance(session, Session):
        raise TypeError("session must be a SQLAlchemy Session")
    if not isinstance(artifact, CbrBankSourceArtifact) or artifact.id is None:
        raise ValueError("artifact must be a persisted CbrBankSourceArtifact")
    reference = _validate_reference(source_reference)
    evidence = ArtifactAvailabilityEvidence(
        artifact_sha256=artifact.content_sha256,
        observed_at=observed_at,
        source=evidence_source,
        exact_payload_bound=exact_payload_bound,
        source_reference=reference,
    )
    predicates = _identity_predicates(artifact.id, evidence, reference)
    existing = session.execute(
        select(CbrBankArtifactAvailabilityEvidence).where(*predicates)
    ).scalar_one_or_none()
    if existing is not None:
        _validate_row(
            session,
            existing,
            artifact=artifact,
            evidence=evidence,
            source_reference=reference,
        )
        return existing, False

    row = CbrBankArtifactAvailabilityEvidence(
        artifact=artifact,
        contract_version=CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION,
        evidence_source=evidence.source.value,
        observed_at=evidence.observed_at,
        exact_payload_bound=evidence.exact_payload_bound,
        source_reference=reference,
    )
    recoverable = session.get_bind().dialect.name != "sqlite"
    savepoint = session.begin_nested() if recoverable else nullcontext()
    try:
        with savepoint:
            session.add(row)
            session.flush()
    except IntegrityError:
        if not recoverable:
            raise
        existing = session.execute(
            select(CbrBankArtifactAvailabilityEvidence).where(*predicates)
        ).scalar_one_or_none()
        if existing is None:
            raise
        _validate_row(
            session,
            existing,
            artifact=artifact,
            evidence=evidence,
            source_reference=reference,
        )
        return existing, False
    return row, True


def load_artifact_versions(
    session: Session,
    *,
    form: CbrBankForm,
    report_date: date,
) -> tuple[ArtifactVersion, ...]:
    """Load deterministic ORM projections for the authoritative Task260A selector."""
    if not isinstance(session, Session):
        raise TypeError("session must be a SQLAlchemy Session")
    if not isinstance(form, CbrBankForm):
        raise ValueError("form must be a canonical CbrBankForm")
    if type(report_date) is not date:
        raise ValueError("report_date must be a date")

    statement = (
        select(CbrBankSourceArtifact, CbrBankArtifactAvailabilityEvidence)
        .outerjoin(
            CbrBankArtifactAvailabilityEvidence,
            CbrBankArtifactAvailabilityEvidence.artifact_id
            == CbrBankSourceArtifact.id,
        )
        .where(
            CbrBankSourceArtifact.form == form.value,
            CbrBankSourceArtifact.report_date == report_date,
        )
        .order_by(
            CbrBankSourceArtifact.content_sha256,
            CbrBankSourceArtifact.id,
            CbrBankArtifactAvailabilityEvidence.observed_at,
            CbrBankArtifactAvailabilityEvidence.evidence_source,
            CbrBankArtifactAvailabilityEvidence.exact_payload_bound,
            CbrBankArtifactAvailabilityEvidence.source_reference,
            CbrBankArtifactAvailabilityEvidence.id,
        )
    )
    grouped: dict[
        int,
        tuple[CbrBankSourceArtifact, list[ArtifactAvailabilityEvidence]],
    ] = {}
    with session.no_autoflush:
        for artifact, row in session.execute(statement):
            entry = grouped.setdefault(artifact.id, (artifact, []))
            if row is None:
                continue
            if row.contract_version != CBR_BANK_ARTIFACT_AVAILABILITY_CONTRACT_VERSION:
                raise ValueError("persisted availability contract version is invalid")
            reference = _validate_reference(row.source_reference)
            entry[1].append(
                ArtifactAvailabilityEvidence(
                    artifact_sha256=artifact.content_sha256,
                    observed_at=_storage_datetime(session, row.observed_at),
                    source=EvidenceSource(row.evidence_source),
                    exact_payload_bound=row.exact_payload_bound,
                    source_reference=reference,
                )
            )

    versions = []
    for artifact, evidence in grouped.values():
        versions.append(
            ArtifactVersion(
                key=ArtifactVersionKey(
                    form=CbrBankForm(artifact.form),
                    report_date=artifact.report_date,
                    artifact_sha256=artifact.content_sha256,
                ),
                availability_evidence=tuple(evidence),
            )
        )
    return tuple(sorted(versions, key=lambda item: item.key.artifact_sha256))


def select_persisted_artifact_version_as_of(
    session: Session,
    *,
    form: CbrBankForm,
    report_date: date,
    as_of: datetime,
) -> PitSelectionResult:
    """Delegate persisted AS-OF selection to Task260A without alternate precedence."""
    return select_artifact_version_as_of(
        form=form,
        report_date=report_date,
        versions=load_artifact_versions(session, form=form, report_date=report_date),
        as_of=as_of,
    )


__all__ = [
    "load_artifact_versions",
    "persist_artifact_availability_evidence",
    "select_persisted_artifact_version_as_of",
]
