from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Any


class SourceProvider(StrEnum):
    CBR_RATINGS = "CBR_RATINGS"
    ACRA = "ACRA"
    EXPERT_RA = "EXPERT_RA"
    NRA = "NRA"
    NKR = "NKR"
    MOEX = "MOEX"


class SourceKind(StrEnum):
    RATING_REPOSITORY_RESPONSE = "RATING_REPOSITORY_RESPONSE"
    RATING_ISSUER_PAGE = "RATING_ISSUER_PAGE"
    RATING_ISSUE_PAGE = "RATING_ISSUE_PAGE"
    RATING_RELEASE = "RATING_RELEASE"
    DEFAULT_INFORMATION = "DEFAULT_INFORMATION"
    OTHER = "OTHER"


class RatingAgency(StrEnum):
    ACRA = "ACRA"
    EXPERT_RA = "EXPERT_RA"
    NRA = "NRA"
    NKR = "NKR"


class RatingTarget(StrEnum):
    LEGAL_ISSUER = "LEGAL_ISSUER"
    BOND = "BOND"


class IdentityResolutionState(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class PublicationPrecision(StrEnum):
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    UNKNOWN = "UNKNOWN"


class DefaultClass(StrEnum):
    TECHNICAL_DEFAULT = "TECHNICAL_DEFAULT"
    DEFAULT = "DEFAULT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ObligationType(StrEnum):
    COUPON = "COUPON"
    PRINCIPAL = "PRINCIPAL"
    OFFER = "OFFER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceArtifactInput:
    provider: SourceProvider
    kind: SourceKind
    source_url: str
    content_type: str
    content_bytes: bytes
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class RatingEventInput:
    agency: RatingAgency
    target: RatingTarget
    source_object_id: str
    source_issuer_inn: str | None
    source_bond_isin: str | None
    source_name_raw: str | None
    event_date: date
    publication_precision: PublicationPrecision
    publication_date: date | None
    publication_at: datetime | None
    rating_scale_raw: str | None
    rating_value_raw: str | None = None
    rating_outlook_raw: str | None = None
    rating_watch_raw: str | None = None
    rating_action_raw: str | None = None


@dataclass(frozen=True, slots=True)
class DefaultEventInput:
    source_object_id: str
    source_bond_isin: str
    source_name_raw: str | None
    event_date: date
    publication_precision: PublicationPrecision
    publication_date: date | None
    publication_at: datetime | None
    default_class: DefaultClass
    obligation_type: ObligationType
    default_status_raw: str | None = None
    default_reason_raw: str | None = None
    amount_raw: str | None = None
    provider: SourceProvider = SourceProvider.MOEX


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    state: IdentityResolutionState
    legal_issuer_id: int | None = None
    bond_id: int | None = None


@dataclass(frozen=True, slots=True)
class PersistResult:
    row: Any
    inserted: bool


@dataclass(frozen=True, slots=True)
class RatingEventDraft:
    """Immutable preview; constructing it performs no persistence."""

    fields: tuple[tuple[str, Any], ...]

    def to_values(self) -> dict[str, Any]:
        return dict(self.fields)

    @property
    def event_fingerprint(self) -> str:
        return dict(self.fields)["event_fingerprint"]


class CreditRiskEvidenceError(ValueError):
    pass


class CreditRiskEvidenceCollision(CreditRiskEvidenceError):
    pass


def aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CreditRiskEvidenceError(f"{field} must be timezone-aware")
    offset = value.utcoffset()
    if offset is None:
        raise CreditRiskEvidenceError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def real_date(value: date, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise CreditRiskEvidenceError(f"{field} must be a date")
    return value


def canonical_inn(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{10}", value) is None:
        raise CreditRiskEvidenceError("issuer INN must be exactly 10 digits")
    return value


def canonical_isin(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z0-9]{12}", value) is None:
        raise CreditRiskEvidenceError(
            "bond ISIN must be exactly 12 uppercase alphanumeric characters"
        )
    return value


def nonempty(value: str, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CreditRiskEvidenceError(f"{field} must be non-empty and bounded")
    return value


def optional_text(value: str | None, field: str, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CreditRiskEvidenceError(f"{field} must be null or non-empty and bounded")
    return value


def validate_publication(
    precision: PublicationPrecision,
    publication_date: date | None,
    publication_at: datetime | None,
) -> tuple[date | None, datetime | None]:
    try:
        precision = PublicationPrecision(precision)
    except (TypeError, ValueError) as exc:
        raise CreditRiskEvidenceError("unsupported publication precision") from exc
    if precision is PublicationPrecision.DATE:
        if publication_date is None or publication_at is not None:
            raise CreditRiskEvidenceError("DATE publication requires date only")
        return real_date(publication_date, "publication_date"), None
    if precision is PublicationPrecision.TIMESTAMP:
        if publication_at is None or publication_date is not None:
            raise CreditRiskEvidenceError("TIMESTAMP publication requires timestamp only")
        return None, aware_utc(publication_at, "publication_at")
    if publication_date is not None or publication_at is not None:
        raise CreditRiskEvidenceError("UNKNOWN publication cannot carry a date")
    return None, None


def canonical_json_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return aware_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def date_text(value: date | None) -> str | None:
    return None if value is None else real_date(value, "date").isoformat()
