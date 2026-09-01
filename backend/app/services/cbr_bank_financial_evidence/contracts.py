from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.services.cbr_bank_reporting.contracts import CbrRawRecord


CONTRACT_VERSION = "cbr-bank-raw-financial-evidence-v1"
TASK251_PARSER_CONTRACT_VERSION = "bondradar.cbr_bank_regulatory_bundle_probe.v1"
ARCHIVE_RUNTIME_CONTRACT = "rarfile-4.5+libarchive-bsdtar-current-cbr-artifacts"


class CbrIdentityLinkState(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_VERIFIED = "NOT_VERIFIED"
    SOURCE_IDENTITY_BLOCKED = "SOURCE_IDENTITY_BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True, slots=True)
class EntityWriteCounts:
    inserted: int = 0
    reused: int = 0
    updated: int = 0
    noop: int = 0


@dataclass(frozen=True, slots=True)
class PersistBundleResult:
    subjects: EntityWriteCounts
    artifacts: EntityWriteCounts
    snapshots: EntityWriteCounts
    observations: EntityWriteCounts
    identity_evidence: EntityWriteCounts
    identity_profiles: EntityWriteCounts
    forms: tuple[str, ...]
    subject_count: int
    observation_count: int


@dataclass(frozen=True, slots=True)
class ExactLexicalObservation:
    record: CbrRawRecord
    source_value_field: str
    raw_value_text: str
    parsed_decimal_value: Decimal | None
    source_dimensions: tuple[tuple[str, Any], ...]
    source_fields_sha256: str
    source_row_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExactFormEvidence:
    form: str
    report_date: date
    value_member_name: str
    observations: tuple[ExactLexicalObservation, ...]
