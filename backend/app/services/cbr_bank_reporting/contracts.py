from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias


PROBE_SCHEMA_VERSION = "bondradar.cbr_bank_regulatory_bundle_probe.v1"
SOURCE_PAGE = "https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/"


class CbrBankForm(StrEnum):
    FORM_101 = "0409101"
    FORM_102 = "0409102"
    FORM_123 = "0409123"
    FORM_135 = "0409135"

    @property
    def short_code(self) -> str:
        return self.value[-3:]

    @classmethod
    def parse(cls, value: str) -> "CbrBankForm":
        normalized = str(value or "").strip()
        aliases = {item.value: item for item in cls}
        aliases.update({item.short_code: item for item in cls})
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError("unsupported CBR bank form") from exc


class CbrSourceStatus(StrEnum):
    ARTIFACT_FOUND = "ARTIFACT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    SOURCE_ERROR = "SOURCE_ERROR"
    INVALID_CONTENT = "INVALID_CONTENT"
    ARTIFACT_TOO_LARGE = "ARTIFACT_TOO_LARGE"
    ARTIFACT_MUTATED = "ARTIFACT_MUTATED"
    RAR_RUNTIME_UNAVAILABLE = "RAR_RUNTIME_UNAVAILABLE"
    INVALID_ARCHIVE = "INVALID_ARCHIVE"
    ARCHIVE_PATH_TRAVERSAL = "ARCHIVE_PATH_TRAVERSAL"
    ARCHIVE_DUPLICATE_MEMBER = "ARCHIVE_DUPLICATE_MEMBER"
    ARCHIVE_TOO_MANY_MEMBERS = "ARCHIVE_TOO_MANY_MEMBERS"
    ARCHIVE_MEMBER_TOO_LARGE = "ARCHIVE_MEMBER_TOO_LARGE"
    ARCHIVE_TOTAL_TOO_LARGE = "ARCHIVE_TOTAL_TOO_LARGE"
    UNSUPPORTED_ARCHIVE_FEATURE = "UNSUPPORTED_ARCHIVE_FEATURE"
    INVALID_DBF = "INVALID_DBF"
    VALUE_PARSE_ERROR = "VALUE_PARSE_ERROR"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"


class DisclosureState(StrEnum):
    PUBLIC_VALUE = "PUBLIC_VALUE"
    PUBLIC_VALUE_BLANK = "PUBLIC_VALUE_BLANK"
    SUPPRESSED_OR_REDUCED = "SUPPRESSED_OR_REDUCED"
    NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT = "NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT"
    UNKNOWN = "UNKNOWN"


class CbrSourceError(RuntimeError):
    def __init__(self, code: CbrSourceStatus, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CbrArtifactReference:
    form: CbrBankForm
    source_href: str
    source_url: str
    artifact_filename: str
    report_date: date
    discovered_at: datetime


@dataclass(frozen=True, slots=True)
class CbrBankArtifact:
    reference: CbrArtifactReference
    content: bytes
    content_sha256: str
    compressed_size: int
    content_type: str | None
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    normalized_name: str
    compressed_size: int
    uncompressed_size: int
    crc32: int | None


@dataclass(frozen=True, slots=True)
class DbfFieldDefinition:
    name: str
    field_type: str
    length: int
    decimal_count: int


@dataclass(frozen=True, slots=True)
class DbfMember:
    name: str
    content: bytes
    fields: tuple[DbfFieldDefinition, ...]
    records: tuple[tuple[tuple[str, "RawScalar"], ...], ...]
    encoding: str
    schema_fingerprint: str


RawScalar: TypeAlias = str | Decimal | date | datetime | bool | bytes | None


@dataclass(frozen=True, slots=True)
class CbrRawRecord:
    form: CbrBankForm
    regn: str
    source_member: str
    source_row_index: int
    source_date: date | None
    source_fields: tuple[tuple[str, RawScalar], ...]
    source_code: str | None
    source_label: str | None
    source_value: Decimal | None
    raw_unit: str | None
    raw_currency: str | None
    raw_multiplier: int | None
    disclosure_state: DisclosureState


@dataclass(frozen=True, slots=True)
class CbrNomenclatureRow:
    form: CbrBankForm
    source_member: str
    source_key: str
    label: str | None
    source_fields: tuple[tuple[str, RawScalar], ...]


@dataclass(frozen=True, slots=True)
class CbrFormResult:
    form: CbrBankForm
    artifact: CbrBankArtifact
    member_schema_fingerprints: tuple[tuple[str, str], ...]
    form_schema_fingerprint: str
    records: tuple[CbrRawRecord, ...]
    nomenclature_rows: tuple[CbrNomenclatureRow, ...]
    supporting_rows: tuple[tuple[tuple[str, RawScalar], ...], ...]
    subjects: tuple[str, ...]
    source_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CbrBankRegulatoryBundleSnapshot:
    report_date: date
    forms: tuple[CbrFormResult, ...]
    subjects_by_form: tuple[tuple[str, int], ...]
    records_by_form: tuple[tuple[str, int], ...]
    subject_set_hashes: tuple[tuple[str, str], ...]
    cross_form_overlap: tuple[tuple[str, int], ...]
    exclusive_membership_counts: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]
    published_at: None = None
    pit_state: str = "PIT_PARTIAL"
