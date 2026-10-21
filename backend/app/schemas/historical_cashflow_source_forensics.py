"""Task308B2R1 diagnostics cannot become financial event evidence."""
from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator
from app.schemas.historical_identity_feasibility import Contract
from app.schemas.historical_cashflow_feasibility import Request as OriginalRequest

SECIDS = ("RU000A0JQAM6", "SU24020RMFS8")
TABLES = ("coupons", "redemptions", "offers")
Table = Literal["coupons", "redemptions", "offers"]
Profile = Literal["BASELINE", "TABLE_ONLY"]


class DiagnosticContract(Contract):
    actual_payment_confirmed: Literal[False] = False
    historical_decision_availability_proven: Literal[False] = False
    historical_backtest_ready: Literal[False] = False
    total_return_execution_ready: Literal[False] = False
    financial_events_created: Literal[False] = False

    @field_validator("actual_payment_confirmed", "historical_decision_availability_proven", "historical_backtest_ready",
        "total_return_execution_ready", "financial_events_created", mode="before")
    @classmethod
    def false_only(cls, value):
        if value is not False: raise ValueError("DIAGNOSTIC_SAFETY_INVALID")
        return value


class DiagnosticLimits(DiagnosticContract):
    max_requests: int = Field(default=12, ge=1, le=12)
    max_pages: int = Field(default=12, ge=1, le=12)
    response_bytes: int = Field(default=2*1024**2, ge=1, le=2*1024**2)
    total_response_bytes: int = Field(default=12*1024**2, ge=1, le=12*1024**2)
    max_seconds: int = Field(default=90, ge=1, le=90)
    evidence_bytes: int = Field(default=32*1024**2, ge=1, le=32*1024**2)
    reserve_bytes: int = Field(default=8*1024**3, ge=8*1024**3)


class OriginalHashes(DiagnosticContract):
    plan_sha256: str
    authorization_sha256: str
    probe_sha256: str
    evaluation_sha256: str


class DiagnosticRequest(DiagnosticContract):
    secid: Literal["RU000A0JQAM6", "SU24020RMFS8"]
    table: Table
    profile: Profile
    offset: Literal[0] = 0

    @field_validator("offset",mode="before")
    @classmethod
    def exact_zero(cls,value):
        if type(value) is not int or value!=0:raise ValueError("ONLY_EXACT_OFFSET_ZERO_ALLOWED")
        return value


class DuplicateFinding(DiagnosticContract):
    key_display: str = Field(max_length=128)
    key_sha256: str
    occurrences: int
    location: str
    area: Literal["ROOT", "METADATA", "DATA", "OTHER", "UNKNOWN"]
    comparison: Literal["STRUCTURALLY_IDENTICAL", "DIFFERENT", "UNKNOWN"]
    value_sha256s: tuple[str, ...]


class TableFinding(DiagnosticContract):
    table: str
    columns: tuple[str, ...]
    row_count: int
    metadata_json: str | None
    rows_json: tuple[str, ...]
    cursor_present: bool
    cursor_json: str | None
    currency_fields: tuple[str, ...]
    amount_fields: tuple[str, ...]
    percentage_fields: tuple[str, ...]
    nominal_fields: tuple[str, ...]
    date_fields: tuple[str, ...]
    unit_claims: tuple[str, ...]
    returned_secids: tuple[str | None, ...] = ()
    returned_isins: tuple[str | None, ...] = ()


class Inspection(DiagnosticContract):
    body_sha256: str
    byte_count: int
    disposition: Literal["ACCEPTED", "QUARANTINE"]
    duplicates: tuple[DuplicateFinding, ...] = ()
    tables: tuple[TableFinding, ...] = ()
    completeness: Literal["COMPLETE", "INCOMPLETE"] = "INCOMPLETE"
    pagination: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    unit_authority: Literal["UNIT_AUTHORITY_DOCUMENTED", "UNIT_AUTHORITY_PARTIAL", "UNIT_AUTHORITY_UNPROVEN", "UNIT_AUTHORITY_CONFLICTING"] = "UNIT_AUTHORITY_UNPROVEN"
    diagnostics: tuple[str, ...] = ()


class OriginalOutcome(DiagnosticContract):
    table: str
    attempts: int
    accepted_pages: int
    rejected_attempts: int
    raw_rows: int
    invalid_rows: int
    receipt_categories: tuple[tuple[str, int], ...]
    missing_rejected_bodies: int


class Observation(DiagnosticContract):
    request: DiagnosticRequest
    accepted_current_isin_context: str
    observed_at: datetime
    original_receipt_sha256: str | None
    inspection: Inspection


class OriginalObservation(DiagnosticContract):
    request: OriginalRequest
    accepted_current_isin_context: str
    observed_at: datetime
    original_receipt_sha256: str
    inspection: Inspection


class ForensicsReport(DiagnosticContract):
    contract_version: Literal["bondization-source-forensics-v1"] = "bondization-source-forensics-v1"
    status: Literal["COMPLETE", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    expected: OriginalHashes
    source_fingerprints: tuple[tuple[str, str], ...] = ()
    original_evaluation_status: str | None = None
    outcomes: tuple[OriginalOutcome, ...] = ()
    observations: tuple[OriginalObservation, ...] = ()
    limitations: tuple[str, ...] = ()
    report_sha256: str = ""


class DiagnosticPlan(DiagnosticContract):
    contract_version: Literal["bondization-diagnostic-plan-v1"] = "bondization-diagnostic-plan-v1"
    status: Literal["PLANNED", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    original_hashes: OriginalHashes
    forensics_sha256: str
    protected_source_root_sha256s: tuple[str, ...] = ()
    secids: tuple[str, ...] = ()
    tables: tuple[Table, ...] = ()
    bindings: tuple[tuple[str, str], ...] = ()
    work: tuple[DiagnosticRequest, ...] = ()
    reused: tuple[Observation, ...] = ()
    limits: DiagnosticLimits = Field(default_factory=DiagnosticLimits)
    plan_sha256: str = ""


class ForensicPackage(DiagnosticContract):
    status: Literal["COMPLETE", "BLOCKED"]
    report: ForensicsReport
    plan: DiagnosticPlan


class DiagnosticAuthorization(DiagnosticContract):
    contract_version: Literal["bondization-diagnostic-authorization-v1"] = "bondization-diagnostic-authorization-v1"
    explicit_confirmation: Literal[True]
    plan_sha256: str
    secids: tuple[str, ...]
    tables: tuple[Table, ...]
    profiles: tuple[Profile, ...]
    work: tuple[DiagnosticRequest, ...]
    destination_sha256: str
    limits: DiagnosticLimits
    authorization_sha256: str = ""

    @field_validator("explicit_confirmation",mode="before")
    @classmethod
    def explicit_true(cls,value):
        if value is not True:raise ValueError("EXACT_EXPLICIT_CONFIRMATION_REQUIRED")
        return value


class DiagnosticIntent(DiagnosticContract):
    sequence: int
    authorization_sha256: str
    request: DiagnosticRequest
    previous_receipt_sha256: str | None
    started_at: datetime
    intent_sha256: str = ""


class DiagnosticReceipt(DiagnosticContract):
    sequence: int
    intent_sha256: str
    previous_receipt_sha256: str | None
    request: DiagnosticRequest
    observed_at: datetime
    disposition: Literal["ACCEPTED", "QUARANTINE", "SOURCE_ERROR", "BUDGET_STOP"]
    category: str
    retryable: bool
    retry_after: int = Field(default=0, ge=0, le=60)
    received_bytes: int
    body_sha256: str | None
    inspection_sha256: str | None
    receipt_sha256: str = ""


class DiagnosticState(DiagnosticContract):
    authorization_sha256: str
    receipt_sha256s: tuple[str, ...] = ()
    pending_sequence: int | None = None
    deadline_at: datetime
    budget_stopped: bool = False
    state_sha256: str = ""


class DiagnosticResult(DiagnosticContract):
    contract_version: Literal["bondization-diagnostic-report-v1"] = "bondization-diagnostic-report-v1"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    plan_sha256: str
    authorization_sha256: str
    original_hashes: OriginalHashes
    protected_source_root_sha256s: tuple[str, ...] = ()
    receipt_sha256s: tuple[str, ...] = ()
    observations: tuple[Observation, ...] = ()
    attempts: int = 0
    received_bytes: int = 0
    accepted_pages: int = 0
    quarantine_pages: int = 0
    critical_findings: tuple[str, ...] = ()
    report_sha256: str = ""


class OperationEnvelope(DiagnosticContract):
    operation: Literal["FORENSIC_PLAN", "DIAGNOSTIC_AUTHORIZE", "DIAGNOSTIC_PROBE", "DIAGNOSTIC_EVALUATE"]
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    result: ForensicPackage | DiagnosticAuthorization | DiagnosticResult | None = None
