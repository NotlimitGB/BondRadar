"""Task308B1: offline identity qualifications, never acquisition authority."""
from datetime import date, datetime
from typing import Literal
from pydantic import Field, field_validator
from app.schemas.historical_identity_feasibility import Contract, Context, SourceResult, ProbeLimits
from app.schemas.historical_discovery_coverage_audit import IdentityDiagnostic, Metric


class OfflineContract(Contract):
    network_access: Literal[False] = False
    historical_identity_at_decision_proven: Literal[False] = False
    official_day0_started: Literal[False] = False
    network_authorized: Literal[False] = False

    @field_validator("network_access","historical_identity_at_decision_proven","official_day0_started","network_authorized",mode="before")
    @classmethod
    def offline(cls,value):
        if value is not False:raise ValueError("OFFLINE_SAFETY_INVALID")
        return value


class InventoryLimits(OfflineContract):
    task307_bytes: int = Field(default=32*1024**2,gt=0,le=32*1024**2)
    auxiliary_bytes: int = Field(default=8*1024**2,gt=0,le=8*1024**2)
    probe_bytes: int = Field(default=128*1024**2,gt=0,le=128*1024**2)
    total_input_bytes: int = Field(default=192*1024**2,gt=0,le=192*1024**2)
    output_bytes: int = Field(default=32*1024**2,gt=0,le=32*1024**2)
    max_identity_rows: int = Field(default=100000,gt=0,le=100000)
    working_row_bytes: int = Field(default=1024**2,gt=0,le=1024**2)
    reserve_bytes: int = Field(default=8*1024**3,ge=8*1024**3)


class Expectations(OfflineContract):
    total_secids: int | None = Field(default=None,ge=0)
    format_valid: int | None = Field(default=None,ge=0)
    checksum_valid: int | None = Field(default=None,ge=0)
    checksum_invalid: int | None = Field(default=None,ge=0)
    probed: int | None = Field(default=None,ge=0)
    current_bindings: int | None = Field(default=None,ge=0)
    mismatched_bindings: int | None = Field(default=None,ge=0)


Syntax = Literal["ISIN_FORMAT_VALID_CHECKSUM_VALID","ISIN_FORMAT_VALID_CHECKSUM_INVALID","NOT_ISIN_FORMAT"]
Binding = Literal["EXACT_CURRENT_BINDING_FOUND","EXACT_CURRENT_BINDING_NOT_EQUAL_SECID","EXACT_RECORD_WITHOUT_ISIN",
    "SOURCE_CONFLICT","SOURCE_RESULT_AMBIGUOUS","SOURCE_UNAVAILABLE","SOURCE_RESULT_NOT_EXACT","SOURCE_INVALID","SOURCE_RESPONSE_INCOMPLETE","NOT_PROBED"]


class InventoryRow(OfflineContract):
    contract_version: Literal["historical-identity-recovery-inventory-v1"] = "historical-identity-recovery-inventory-v1"
    secid: str
    original_identities: tuple[IdentityDiagnostic,...]
    boards: tuple[str,...]
    listing: bool
    observed: bool
    first_observed_date: date | None
    last_observed_date: date | None
    supplied_listing_intervals: tuple[tuple[date | None,date | None],...]
    economic_relevance: Literal["OBSERVED_COMPONENT_EVIDENCE","OBSERVED_WITHOUT_TRADE_OR_USABLE_SIGNAL","LISTING_ONLY_UNASSESSED"]
    actual_trade_evidence: bool
    usable_price_nkd_evidence: bool
    joint_execution_coverage: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    syntax: Syntax
    format_valid: bool
    checksum_valid: bool
    raw_prefix: str
    canonical_ofz: bool
    strata: tuple[str,...]
    task308a_sample_selected: bool
    task308a_probe_approved: bool
    previously_probed: bool
    source_results: tuple[SourceResult,...]
    current_binding: Binding
    confirmed_current_isin: str | None
    current_binding_confirmed: bool
    secid_equals_current_isin: bool | None
    binding_conflict: bool
    requires_review: bool
    source_observation_times: tuple[datetime,...]
    next_investigation: str
    requirements: tuple[str,...]


class ProposalRow(OfflineContract):
    secid: str
    role: Literal["CHECKSUM_EXCEPTION","CONTROL","EXCEPTION_BACKFILL"]
    original_identities: tuple[IdentityDiagnostic,...]
    features: tuple[str,...]
    new_features: tuple[str,...]
    selection_reason: str


class ProposalStratum(OfflineContract):
    feature: str
    eligible: int
    selected: int
    selected_secids: tuple[str,...]
    overlaps: tuple[tuple[str,int],...]


class ExceptionProposal(OfflineContract):
    contract_version: Literal["historical-identity-exception-probe-plan-v1"] = "historical-identity-exception-probe-plan-v1"
    policy: Literal["GREEDY_9_EXCEPTIONS_3_NOVEL_CONTROLS_V1"] = "GREEDY_9_EXCEPTIONS_3_NOVEL_CONTROLS_V1"
    maximum_secids: Literal[12] = 12
    previously_probed_secids: tuple[str,...]
    selected: tuple[ProposalRow,...]
    strata: tuple[ProposalStratum,...]
    unmet_exception_slots: int
    unmet_control_slots: int
    unfilled_total_slots: int
    sources: tuple[Literal["REFERENCE","DESCRIPTION"],...] = ("REFERENCE","DESCRIPTION")
    recommended_limits: ProbeLimits = Field(default_factory=ProbeLimits)
    consumable_by_task308a_probe: Literal[False] = False
    authorization_required: Literal[True] = True
    proposal_sha256: str = ""


class FieldRequirement(OfflineContract):
    field: str
    family: Literal["REFERENCE","CASHFLOW"]
    priority: Literal["P0","P1"]
    source_status: Literal["SOURCE_DOCUMENTED","REQUIRES_PROBE","NOT_AVAILABLE","UNKNOWN"]
    source_contract: str
    application_field_or_gate: str
    required_proof: str


class AcquisitionStage(OfflineContract):
    stage: int
    name: str
    cohort_secids: tuple[str,...]
    scope: str
    prerequisite: str
    authorization_required: Literal[True] = True


class ResourceScenario(OfflineContract):
    name: str
    secids: int
    query_count: int
    attempt_ceiling: int
    body_byte_ceiling: int
    interpretation: str


class SourceFieldObservation(OfflineContract):
    source: Literal["REFERENCE","DESCRIPTION"]
    source_field: str
    exact_record_secids: tuple[str,...]
    supplied_value_secids: tuple[str,...]
    canonical_validity: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


class AcquisitionRoadmap(OfflineContract):
    contract_version: Literal["historical-identity-acquisition-plan-v1"] = "historical-identity-acquisition-plan-v1"
    requirements: tuple[FieldRequirement,...]
    source_field_observations: tuple[SourceFieldObservation,...] = ()
    stages: tuple[AcquisitionStage,...]
    existing_evidence_reuse_secids: tuple[str,...]
    economic_structure_proof: Literal["UNKNOWN_EXCEPT_CANONICAL_OFZ_IDENTITY"] = "UNKNOWN_EXCEPT_CANONICAL_OFZ_IDENTITY"
    cashflow_distinctions: tuple[str,...]
    measured_attempts: int
    measured_pages: int
    measured_response_bytes: int
    measured_probed_secids: int
    descriptive_averages: tuple[tuple[str,str],...]
    scenarios: tuple[ResourceScenario,...]
    limitations: tuple[str,...]
    roadmap_sha256: str = ""


class PopulationView(OfflineContract):
    name: str
    secids: tuple[str,...]
    definition: str


class InventoryAudit(OfflineContract):
    contract_version: Literal["historical-identity-recovery-audit-v1"] = "historical-identity-recovery-audit-v1"
    status: Literal["COMPLETE","BLOCKED"]
    context: Context | None = None
    input_hashes: tuple[tuple[str,str],...] = ()
    policy: Literal["OFFLINE_FULL_SECID_INVENTORY_V1"] = "OFFLINE_FULL_SECID_INVENTORY_V1"
    limits: InventoryLimits
    counts: tuple[tuple[str,int],...] = ()
    metrics: tuple[Metric,...] = ()
    population_views: tuple[PopulationView,...] = ()
    upstream_joint_metrics: tuple[Metric,...] = ()
    joint_execution_coverage: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    checksum_invalid_secids: tuple[str,...] = ()
    expectations: Expectations
    invariants: tuple[str,...] = ()
    inventory_sha256: str | None = None
    proposal_sha256: str | None = None
    roadmap_sha256: str | None = None
    summary_sha256: str | None = None
    blockers: tuple[str,...] = ()
    limitations: tuple[str,...] = ()
    report_sha256: str = ""


class OperationResult(OfflineContract):
    contract_version: Literal["historical-identity-recovery-operation-v1"] = "historical-identity-recovery-operation-v1"
    mode: Literal["REPORT"] = "REPORT"
    status: Literal["COMPLETE","BLOCKED"]
    report_sha256: str | None = None
    input_hashes: tuple[tuple[str,str],...] = ()
    artifact_hashes: tuple[tuple[str,str],...] = ()
    resource_status: Literal["WITHIN_LIMITS","LIMIT_EXCEEDED","NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    blockers: tuple[str,...] = ()
