from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum


CONTRACT_VERSION = "cbr-legal-issuer-bridge-v1"
PROBE_SCHEMA_VERSION = "bondradar.cbr_legal_issuer_bridge_probe.v1"
FULLCOLIST_URL = "https://www.cbr.ru/banking_sector/credit/FullCoList/"
FINORG_URL = "https://www.cbr.ru/FO_ZoomWS/FinOrg.asmx"
PIT_STATUS = "CURRENT_ONLY"
MAX_OGRNS_PER_REQUEST = 100
MAX_TOTAL_OGRNS_PER_RUN = 1000

_REGN = re.compile(r"[0-9]+")
_OGRN = re.compile(r"[0-9]{13}")
_INN = re.compile(r"[0-9]{10}")


class CbrBridgeSourceStatus(StrEnum):
    COMPLETE = "COMPLETE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_ERROR = "SOURCE_ERROR"
    INVALID_CONTENT = "INVALID_CONTENT"
    INVALID_XML = "INVALID_XML"
    SOAP_FAULT = "SOAP_FAULT"
    SOURCE_DECLARED_FAILURE = "SOURCE_DECLARED_FAILURE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"


class CbrBridgeState(StrEnum):
    VERIFIED = "VERIFIED"
    CBR_REGN_NOT_FOUND = "CBR_REGN_NOT_FOUND"
    CBR_REGN_AMBIGUOUS = "CBR_REGN_AMBIGUOUS"
    CBR_OGRN_MISSING = "CBR_OGRN_MISSING"
    CBR_OGRN_CONFLICT = "CBR_OGRN_CONFLICT"
    FINORG_NOT_FOUND = "FINORG_NOT_FOUND"
    FINORG_SOURCE_ERROR = "FINORG_SOURCE_ERROR"
    FINORG_OGRN_MISMATCH = "FINORG_OGRN_MISMATCH"
    FINORG_INN_MISSING = "FINORG_INN_MISSING"
    FINORG_INN_INVALID = "FINORG_INN_INVALID"
    FINORG_INN_CONFLICT = "FINORG_INN_CONFLICT"
    LEGAL_ISSUER_NOT_EVALUATED = "LEGAL_ISSUER_NOT_EVALUATED"
    LEGAL_ISSUER_NOT_FOUND = "LEGAL_ISSUER_NOT_FOUND"
    LEGAL_ISSUER_INN_AMBIGUOUS = "LEGAL_ISSUER_INN_AMBIGUOUS"
    LEGAL_ISSUER_NOT_VERIFIED = "LEGAL_ISSUER_NOT_VERIFIED"


class CbrBridgeError(RuntimeError):
    def __init__(self, code: CbrBridgeSourceStatus, message: str) -> None:
        super().__init__(message)
        self.code = code


def utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_regn(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("REGN must be a string")
    text = value.strip()
    if not _REGN.fullmatch(text) or int(text) <= 0:
        raise ValueError("REGN must be a positive decimal string")
    return str(int(text))


def canonical_ogrn(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("OGRN must be a string")
    text = value.strip()
    if not _OGRN.fullmatch(text):
        raise ValueError("OGRN must contain exactly 13 digits")
    return text


def canonical_inn(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("INN must be a string")
    text = value.strip()
    if not _INN.fullmatch(text):
        raise ValueError("INN must contain exactly 10 digits")
    return text


def optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def identifier_set_sha256(values: tuple[str, ...] | list[str] | set[str]) -> str:
    canonical = sorted({canonical_regn(value) for value in values}, key=int)
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class CbrCreditOrganizationRegistryRecord:
    regn: str
    ogrn: str | None
    name: str
    organization_type: str | None
    legal_form: str | None
    registration_date: date | None
    license_status: str | None
    location: str | None
    registry_as_of: date
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class CbrCreditOrganizationRegistrySnapshot:
    records: tuple[CbrCreditOrganizationRegistryRecord, ...]
    registry_as_of: date
    retrieved_at: datetime
    ambiguous_regns: tuple[str, ...]
    conflicting_ogrns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FinOrgRecord:
    source_id: str | None
    ogrn: str
    inn: str | None
    inn_status: str
    name: str | None
    status: str | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class FinOrgSearchResult:
    requested_ogrns: tuple[str, ...]
    records: tuple[FinOrgRecord, ...]
    source_error: str | None


@dataclass(frozen=True, slots=True)
class LegalIssuerCandidate:
    legal_issuer_id: int
    issuer_inn: str
    resolution_state: str
    source_issuer_id: str
    issuer_title: str | None


@dataclass(frozen=True, slots=True)
class CbrLegalIssuerBridgeResult:
    regn: str
    bridge_state: CbrBridgeState
    ogrn: str | None
    inn: str | None
    legal_issuer_id: int | None
    legal_issuer_source_issuer_id: str | None
    cbr_registry_name: str | None
    finorg_name: str | None
    legal_issuer_title: str | None
    warnings: tuple[str, ...]
    registry_as_of: date | None = None
    finorg_last_update: datetime | None = None
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CbrLegalIssuerBridgeSnapshot:
    requested_regns: tuple[str, ...]
    registry_as_of: date
    finorg_last_update: datetime
    retrieved_at: datetime
    registry_records: tuple[CbrCreditOrganizationRegistryRecord, ...]
    finorg_records: tuple[FinOrgRecord, ...]
    bridge_results: tuple[CbrLegalIssuerBridgeResult, ...]
    state_counts: tuple[tuple[str, int], ...]
    regn_set_hash: str
    source_resolved_regn_set_hash: str
    legal_issuer_verified_regn_set_hash: str
    warnings: tuple[str, ...]
    pit_status: str = PIT_STATUS
    historical_backcast_allowed: bool = False
    legal_issuer_evaluation_performed: bool = False
