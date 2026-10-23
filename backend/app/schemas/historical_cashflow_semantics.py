"""Task308B2R3 immutable source-only contracts; no payment authority."""
from datetime import date, datetime
from typing import Literal
from app.schemas.historical_cashflow_qualification import DiagnosticContract, ExpectedHashes as UpstreamHashes


class ExpectedHashes(DiagnosticContract):
    upstream: UpstreamHashes
    r2_source_contract_sha256: str
    r2_qualification_sha256: str


class RowIntegrityFinding(DiagnosticContract):
    request_id: str
    secid: str
    table: str
    raw_body_sha256: str
    structural_status: str
    retained_rows_checked: int
    typed_row_sha256s: tuple[str, ...]
    legacy_event_row_sha256s: tuple[str, ...]
    status: Literal["PASS"] = "PASS"


class SourceRowIntegrityReport(DiagnosticContract):
    contract_version: Literal["bondization-source-row-integrity-v1"] = "bondization-source-row-integrity-v1"
    status: Literal["PASS", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    expected: ExpectedHashes
    input_fingerprints: tuple[tuple[str, str], ...] = ()
    observations: tuple[RowIntegrityFinding, ...] = ()
    retained_row_count: int = 0
    report_sha256: str = ""


class FieldEvidence(DiagnosticContract):
    field_name: str
    source_field_names: tuple[str, ...]
    raw_source_type: str
    sample_value: str
    proposed_semantic: str
    authority_source: tuple[str, ...]
    authority_scope: str
    proof_status: Literal["DOCUMENTED", "PARTIAL", "UNPROVEN", "CONFLICTING"]
    conflicts: tuple[str, ...] = ()
    limitations: tuple[str, ...]


class SourceSemanticEvent(DiagnosticContract):
    request_id: str
    secid: str
    accepted_current_isin_context: str
    returned_isin: str | None
    returned_secid: str | None
    source_identity_diagnostics: tuple[str, ...]
    table: str
    profile: str
    row_index: int
    source_row_sha256: str
    observed_at: datetime
    source_completeness: str
    event_date: date | None
    semantic_classification: Literal["SCHEDULED_COUPON", "SCHEDULED_FINAL_MATURITY", "SCHEDULED_PARTIAL_AMORTIZATION",
        "SOURCE_AMORTIZATION_UNCLASSIFIED", "SCHEDULED_OFFER_OBSERVATION", "SOURCE_REDEMPTION_UNCLASSIFIED"]
    classification_policy: Literal["bondization-source-semantics-policy-v1"] = "bondization-source-semantics-policy-v1"
    payment_status: Literal["NOT_CONFIRMED"] = "NOT_CONFIRMED"
    fields: tuple[FieldEvidence, ...]
    diagnostics: tuple[str, ...]


class CanonicalObservation(DiagnosticContract):
    secid: str
    table: str
    canonical_request_id: str | None
    observation_request_ids: tuple[str, ...]
    comparison_status: str
    selection_policy: Literal["COMPLETE_THEN_BASELINE_TABLE_ONLY_B2_ACCEPTED"] = "COMPLETE_THEN_BASELINE_TABLE_ONLY_B2_ACCEPTED"
    diagnostics: tuple[str, ...]


class CouponCoverage(DiagnosticContract):
    secid: str
    canonical_request_id: str | None
    source_schedule_completeness: str
    coupon_row_count: int
    coupon_amount_present_count: int
    coupon_amount_missing_count: int
    coupon_amount_invalid_count: int
    coupon_amount_missing_dates: tuple[date | None, ...]
    amount_unit_authority: Literal["UNPROVEN"] = "UNPROVEN"
    actual_payment_authority: Literal["UNPROVEN"] = "UNPROVEN"
    historical_publication_authority: Literal["UNPROVEN"] = "UNPROVEN"
    diagnostics: tuple[str, ...]


class CashflowSemanticsReport(DiagnosticContract):
    contract_version: Literal["bondization-cashflow-semantics-v1"] = "bondization-cashflow-semantics-v1"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    source_row_integrity_sha256: str
    structural_integrity_status: Literal["PASS", "BLOCKED"]
    source_cashflow_semantics_status: Literal["QUALIFIED_WITH_LIMITATIONS", "BLOCKED"]
    economic_amount_authority_status: Literal["UNPROVEN"] = "UNPROVEN"
    historical_pit_readiness: Literal["UNPROVEN"] = "UNPROVEN"
    canonical_observations: tuple[CanonicalObservation, ...] = ()
    events: tuple[SourceSemanticEvent, ...] = ()
    coupon_coverage: tuple[CouponCoverage, ...] = ()
    limitations: tuple[str, ...] = ()
    report_sha256: str = ""


class SemanticsPackage(DiagnosticContract):
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    integrity: SourceRowIntegrityReport
    semantics: CashflowSemanticsReport


class OperationEnvelope(DiagnosticContract):
    operation: Literal["QUALIFY_CASHFLOW_SEMANTICS"] = "QUALIFY_CASHFLOW_SEMANTICS"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    source_row_integrity_sha256: str | None = None
    cashflow_semantics_sha256: str | None = None
