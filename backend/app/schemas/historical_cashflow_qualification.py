"""Offline v2 observations; structural recovery never authorizes cashflows."""
from datetime import datetime
from typing import Literal
from pydantic import field_validator
from app.schemas.historical_cashflow_source_forensics import DiagnosticContract as UpstreamContract, OriginalHashes, DuplicateFinding
from app.schemas.historical_cashflow_feasibility import Event


class DiagnosticContract(UpstreamContract):
    network_access: Literal[False] = False

    @field_validator("network_access", mode="before")
    @classmethod
    def exact_network_false(cls, value):
        if value is not False:
            raise ValueError("NETWORK_DECLARATION_INVALID")
        return value


class ExpectedHashes(DiagnosticContract):
    b2: OriginalHashes
    forensics_sha256: str
    diagnostic_plan_sha256: str
    diagnostic_authorization_sha256: str
    diagnostic_report_sha256: str


class DuplicateException(DiagnosticContract):
    key: Literal["coupons.cursor"] = "coupons.cursor"
    location: Literal["ROOT"] = "ROOT"
    occurrences: Literal[2] = 2
    equality_verdict: Literal["STRICTLY_STRUCTURALLY_IDENTICAL"] = "STRICTLY_STRUCTURALLY_IDENTICAL"
    repair_policy_version: Literal["root-coupons-cursor-exception-v2"] = "root-coupons-cursor-exception-v2"


class DecodedView(DiagnosticContract):
    contract_version: Literal["bondization-candidate-decoder-v2"] = "bondization-candidate-decoder-v2"
    structural_status: Literal["STRUCTURALLY_RECOVERABLE", "STRUCTURALLY_REJECTED", "REQUESTED_TABLE_MISSING"]
    requested_table: Literal["coupons", "amortizations", "redemptions", "offers"]
    requested_secid: str
    accepted_current_isin_context: str
    raw_body_sha256: str
    repaired_view_sha256: str | None = None
    duplicate_exception: DuplicateException | None = None
    duplicate_findings: tuple[DuplicateFinding, ...] = ()
    requested_table_present: bool = False
    columns: tuple[str, ...] = ()
    metadata_json: str | None = None
    # Lossless values are typed JSON trees, not normalized financial fields.
    source_rows_json: tuple[str, ...] = ()
    source_row_sha256s: tuple[str, ...] = ()
    cursor_json: str | None = None
    cursor_index: int | None = None
    cursor_total: int | None = None
    cursor_page_size: int | None = None
    completeness: Literal["COMPLETE", "PAGINATION_REQUIRED", "COMPLETENESS_UNPROVEN", "CURSOR_INVALID"] = "COMPLETENESS_UNPROVEN"
    unit_status: Literal["DOCUMENTED", "PARTIAL", "UNPROVEN", "CONFLICTING"] = "UNPROVEN"
    unit_claims: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()
    could_become_economic_after_qualification: tuple[bool, ...] = ()
    diagnostics: tuple[str, ...] = ()


class QualifiedObservation(DiagnosticContract):
    requested_secid: str
    table: str
    profile: Literal["BASELINE", "TABLE_ONLY", "B2_ACCEPTED"]
    request_id: str
    observed_at: datetime
    original_disposition: Literal["ACCEPTED", "QUARANTINE"]
    view: DecodedView


class ProfileComparison(DiagnosticContract):
    requested_secid: str
    table: str
    baseline_request_id: str | None
    table_only_request_id: str | None
    verdict: Literal["CROSS_PROFILE_MATCH", "CROSS_PROFILE_DIFFERENCE", "COMPARISON_UNPROVEN"]


class SourceContractReport(DiagnosticContract):
    contract_version: Literal["bondization-source-contract-report-v2"] = "bondization-source-contract-report-v2"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    expected: ExpectedHashes
    input_fingerprints: tuple[tuple[str, str], ...] = ()
    observations: tuple[QualifiedObservation, ...] = ()
    comparisons: tuple[ProfileComparison, ...] = ()
    missing_observations: tuple[tuple[str, str, str], ...] = ()
    report_sha256: str = ""


class CoverageFinding(DiagnosticContract):
    requested_secid: str
    table: str
    observation_count: int
    structural_observation_count: int
    # Per-observation counts must never be summed into a payment schedule.
    source_row_counts: tuple[int, ...]
    structural_status: Literal["AVAILABLE", "UNAVAILABLE"]
    cashflow_coverage_status: Literal["STRUCTURALLY_COMPLETE", "UNPROVEN"]
    monetary_unit_status: Literal["PARTIAL", "UNPROVEN", "CONFLICTING"]
    historical_pit_status: Literal["UNPROVEN"] = "UNPROVEN"
    total_return_readiness: Literal["NOT_READY"] = "NOT_READY"
    blockers: tuple[str, ...]


class QualificationReport(DiagnosticContract):
    contract_version: Literal["bondization-offline-qualification-v2"] = "bondization-offline-qualification-v2"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    source_contract_report_sha256: str
    coverage: tuple[CoverageFinding, ...] = ()
    next_step: Literal["INDEPENDENT_REVIEW_OF_OFFLINE_FINDINGS"] = "INDEPENDENT_REVIEW_OF_OFFLINE_FINDINGS"
    report_sha256: str = ""


class QualificationPackage(DiagnosticContract):
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    source_contract: SourceContractReport
    qualification: QualificationReport


class OperationEnvelope(DiagnosticContract):
    operation: Literal["QUALIFY_EXISTING_EVIDENCE"] = "QUALIFY_EXISTING_EVIDENCE"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    result: QualificationPackage | None = None
