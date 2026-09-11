"""Pure historical artifact versioning and conservative PIT selection.

The selector uses only exact-payload availability observations.  An observation
is a conservative evidence boundary, never a fabricated publication timestamp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Iterable

from app.services.cbr_bank_reporting.contracts import CbrBankForm


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceSource(StrEnum):
    CBR_DIRECT = "CBR_DIRECT"
    WAYBACK = "WAYBACK"
    COMMON_CRAWL = "COMMON_CRAWL"
    OTHER_ARCHIVE = "OTHER_ARCHIVE"


class PitSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_KNOWN_VERSION_AS_OF = "NO_KNOWN_VERSION_AS_OF"
    AMBIGUOUS_VERSION_AT_BOUNDARY = "AMBIGUOUS_VERSION_AT_BOUNDARY"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class PitSelectionQuality(StrEnum):
    CONSERVATIVE_LAST_PROVEN = "CONSERVATIVE_LAST_PROVEN"


def _validate_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _validate_date(value: object, *, field_name: str) -> date:
    if type(value) is not date:
        raise ValueError(f"{field_name} must be a date")
    return value


def _strict_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    try:
        if value.utcoffset() is None:
            raise ValueError(f"{field_name} must have a valid UTC offset")
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid aware datetime") from exc


@dataclass(frozen=True, slots=True)
class ArtifactVersionKey:
    form: CbrBankForm
    report_date: date
    artifact_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.form, CbrBankForm):
            raise ValueError("form must be a canonical CbrBankForm")
        _validate_date(self.report_date, field_name="report_date")
        _validate_sha256(self.artifact_sha256, field_name="artifact_sha256")


@dataclass(frozen=True, slots=True)
class ArtifactAvailabilityEvidence:
    artifact_sha256: str
    observed_at: datetime
    source: EvidenceSource
    exact_payload_bound: bool
    source_reference: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.artifact_sha256, field_name="artifact_sha256")
        object.__setattr__(
            self,
            "observed_at",
            _strict_utc(self.observed_at, field_name="observed_at"),
        )
        if not isinstance(self.source, EvidenceSource):
            raise ValueError("source must be an EvidenceSource")
        if type(self.exact_payload_bound) is not bool:
            raise ValueError("exact_payload_bound must be a bool")
        if self.source_reference is not None and (
            not isinstance(self.source_reference, str)
            or not self.source_reference.strip()
        ):
            raise ValueError("source_reference must be a non-empty string or None")

    def identity_key(self) -> tuple[str, datetime, str, bool, str]:
        return (
            self.artifact_sha256,
            self.observed_at,
            self.source.value,
            self.exact_payload_bound,
            self.source_reference or "",
        )


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    key: ArtifactVersionKey
    availability_evidence: tuple[ArtifactAvailabilityEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, ArtifactVersionKey):
            raise ValueError("key must be an ArtifactVersionKey")
        try:
            evidence_items = tuple(self.availability_evidence)
        except TypeError as exc:
            raise ValueError("availability_evidence must be iterable") from exc
        canonical: dict[
            tuple[str, datetime, str, bool, str], ArtifactAvailabilityEvidence
        ] = {}
        for evidence in evidence_items:
            if not isinstance(evidence, ArtifactAvailabilityEvidence):
                raise ValueError(
                    "availability_evidence must contain ArtifactAvailabilityEvidence"
                )
            if evidence.artifact_sha256 != self.key.artifact_sha256:
                raise ValueError("evidence SHA does not match artifact version SHA")
            canonical[evidence.identity_key()] = evidence
        object.__setattr__(
            self,
            "availability_evidence",
            tuple(canonical[item] for item in sorted(canonical)),
        )

    @property
    def safe_known_from(self) -> datetime | None:
        eligible = tuple(
            item.observed_at
            for item in self.availability_evidence
            if item.exact_payload_bound
        )
        return min(eligible) if eligible else None


@dataclass(frozen=True, slots=True)
class PitSelectionResult:
    status: PitSelectionStatus
    form: CbrBankForm
    report_date: date
    as_of: datetime
    selected_version: ArtifactVersion | None
    selected_safe_known_from: datetime | None
    selection_quality: PitSelectionQuality | None
    eligible_version_count: int
    candidate_artifact_sha256s: tuple[str, ...]
    diagnostic_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, PitSelectionStatus):
            raise ValueError("status must be a PitSelectionStatus")
        if not isinstance(self.form, CbrBankForm):
            raise ValueError("form must be a canonical CbrBankForm")
        _validate_date(self.report_date, field_name="report_date")
        object.__setattr__(
            self, "as_of", _strict_utc(self.as_of, field_name="as_of")
        )
        if type(self.eligible_version_count) is not int or self.eligible_version_count < 0:
            raise ValueError("eligible_version_count must be a non-negative int")
        candidates = tuple(self.candidate_artifact_sha256s)
        if candidates != tuple(sorted(set(candidates))):
            raise ValueError("candidate_artifact_sha256s must be sorted and unique")
        for digest in candidates:
            _validate_sha256(digest, field_name="candidate_artifact_sha256s")
        diagnostics = tuple(self.diagnostic_codes)
        if any(not isinstance(item, str) or not item for item in diagnostics):
            raise ValueError("diagnostic_codes must contain non-empty strings")

        if self.status is PitSelectionStatus.SELECTED:
            if (
                self.selected_version is None
                or self.selected_safe_known_from is None
                or self.selection_quality
                is not PitSelectionQuality.CONSERVATIVE_LAST_PROVEN
                or self.eligible_version_count < 1
                or self.selected_version.key.form is not self.form
                or self.selected_version.key.report_date != self.report_date
                or self.selected_version.safe_known_from
                != self.selected_safe_known_from
                or candidates != (self.selected_version.key.artifact_sha256,)
                or self.selected_safe_known_from > self.as_of
            ):
                raise ValueError("selected result is internally inconsistent")
        elif (
            self.selected_version is not None
            or self.selected_safe_known_from is not None
            or self.selection_quality is not None
        ):
            raise ValueError("non-selected result cannot carry a selected version")


def _result(
    *,
    status: PitSelectionStatus,
    form: CbrBankForm,
    report_date: date,
    as_of: datetime,
    eligible_version_count: int = 0,
    candidates: tuple[str, ...] = (),
    diagnostics: tuple[str, ...] = (),
    selected: ArtifactVersion | None = None,
    boundary: datetime | None = None,
) -> PitSelectionResult:
    return PitSelectionResult(
        status=status,
        form=form,
        report_date=report_date,
        as_of=as_of,
        selected_version=selected,
        selected_safe_known_from=boundary,
        selection_quality=(
            PitSelectionQuality.CONSERVATIVE_LAST_PROVEN
            if status is PitSelectionStatus.SELECTED
            else None
        ),
        eligible_version_count=eligible_version_count,
        candidate_artifact_sha256s=candidates,
        diagnostic_codes=diagnostics,
    )


def select_artifact_version_as_of(
    *,
    form: CbrBankForm,
    report_date: date,
    versions: Iterable[ArtifactVersion],
    as_of: datetime,
) -> PitSelectionResult:
    """Select the last exact-payload version proven at or before ``as_of``."""
    if not isinstance(form, CbrBankForm):
        raise ValueError("form must be a canonical CbrBankForm")
    _validate_date(report_date, field_name="report_date")
    normalized_as_of = _strict_utc(as_of, field_name="as_of")
    try:
        supplied_versions = tuple(versions)
    except TypeError as exc:
        raise ValueError("versions must be iterable") from exc

    if any(not isinstance(item, ArtifactVersion) for item in supplied_versions):
        return _result(
            status=PitSelectionStatus.INVALID_EVIDENCE,
            form=form,
            report_date=report_date,
            as_of=normalized_as_of,
            diagnostics=("INVALID_VERSION_COLLECTION",),
        )

    canonical_versions: list[ArtifactVersion] = []
    try:
        for item in supplied_versions:
            canonical_versions.append(
                ArtifactVersion(
                    key=ArtifactVersionKey(
                        form=item.key.form,
                        report_date=item.key.report_date,
                        artifact_sha256=item.key.artifact_sha256,
                    ),
                    availability_evidence=item.availability_evidence,
                )
            )
    except (AttributeError, TypeError, ValueError):
        return _result(
            status=PitSelectionStatus.INVALID_EVIDENCE,
            form=form,
            report_date=report_date,
            as_of=normalized_as_of,
            diagnostics=("INVALID_VERSION_COLLECTION",),
        )

    matching = tuple(
        item
        for item in canonical_versions
        if item.key.form is form and item.key.report_date == report_date
    )
    grouped: dict[ArtifactVersionKey, list[ArtifactAvailabilityEvidence]] = {}
    for item in matching:
        grouped.setdefault(item.key, []).extend(item.availability_evidence)
    logical_versions = tuple(
        ArtifactVersion(key=key, availability_evidence=tuple(grouped[key]))
        for key in sorted(grouped, key=lambda item: item.artifact_sha256)
    )

    known = tuple(item for item in logical_versions if item.safe_known_from is not None)
    eligible = tuple(
        item for item in known if item.safe_known_from <= normalized_as_of  # type: ignore[operator]
    )
    if not eligible:
        if not logical_versions:
            diagnostic = "NO_VERSION_FOR_FORM_REPORT_DATE"
        elif not known:
            diagnostic = "NO_EXACT_PAYLOAD_BOUND_EVIDENCE"
        else:
            diagnostic = "NO_VERSION_OBSERVED_AT_OR_BEFORE_AS_OF"
        return _result(
            status=PitSelectionStatus.NO_KNOWN_VERSION_AS_OF,
            form=form,
            report_date=report_date,
            as_of=normalized_as_of,
            diagnostics=(diagnostic,),
        )

    boundary = max(
        item.safe_known_from for item in eligible if item.safe_known_from is not None
    )
    boundary_versions = tuple(
        item for item in eligible if item.safe_known_from == boundary
    )
    candidates = tuple(sorted(item.key.artifact_sha256 for item in boundary_versions))
    if len(boundary_versions) != 1:
        return _result(
            status=PitSelectionStatus.AMBIGUOUS_VERSION_AT_BOUNDARY,
            form=form,
            report_date=report_date,
            as_of=normalized_as_of,
            eligible_version_count=len(eligible),
            candidates=candidates,
            diagnostics=("MULTIPLE_ARTIFACT_SHA256_AT_SAFE_BOUNDARY",),
        )

    selected = boundary_versions[0]
    return _result(
        status=PitSelectionStatus.SELECTED,
        form=form,
        report_date=report_date,
        as_of=normalized_as_of,
        eligible_version_count=len(eligible),
        candidates=candidates,
        selected=selected,
        boundary=boundary,
    )


__all__ = [
    "ArtifactAvailabilityEvidence",
    "ArtifactVersion",
    "ArtifactVersionKey",
    "EvidenceSource",
    "PitSelectionQuality",
    "PitSelectionResult",
    "PitSelectionStatus",
    "select_artifact_version_as_of",
]
