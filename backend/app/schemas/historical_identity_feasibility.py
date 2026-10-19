"""Task308A frozen diagnostic and separately authorized probe contracts."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.historical_discovery_coverage_audit import IdentityDiagnostic, Metric

Source = Literal["REFERENCE", "DESCRIPTION"]
Resolution = Literal["EXACT_CURRENT_SOURCE_BINDING_FOUND", "EXACT_CURRENT_SOURCE_BINDING_CONFLICT",
    "SOURCE_MATCH_AMBIGUOUS", "SOURCE_RESULT_NOT_EXACT", "ISIN_NOT_PRESENT", "SOURCE_RECORD_UNAVAILABLE",
    "SOURCE_RESPONSE_INCOMPLETE", "SOURCE_INVALID", "TRANSIENT_SOURCE_FAILURE", "SOURCE_FAILURE", "NOT_PROBED"]


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", revalidate_instances="always")
    pit_ready: Literal[False] = False
    acquisition_ready: Literal[False] = False
    db_mutation: Literal[False] = False
    historical_publication_proven: Literal[False] = False

    @field_validator("pit_ready","acquisition_ready","db_mutation","historical_publication_proven",mode="before")
    @classmethod
    def exact_false(cls,value):
        if value is not False: raise ValueError("SAFETY_DECLARATION_INVALID")
        return value


class Context(Contract):
    task307_audit_sha256: str
    source_manifest_sha256: str
    source_content_sha256: str
    scope_sha256: str
    source_implementation_sha256: str
    history_start: date
    cutoff: date
    last_observed_date: date | None


class SampleUnit(Contract):
    secid: str
    original_identities: tuple[IdentityDiagnostic, ...]
    strata: tuple[str, ...]
    selection_reason: str


class Stratum(Contract):
    name: str
    available: bool
    eligible_count: int
    selected_count: int
    selected_secids: tuple[str, ...]
    overlap_counts: tuple[tuple[str, int], ...]
    represented: bool


class Diagnosis(Contract):
    explanation: str
    classification: Literal["CONFIRMED_BY_CODE", "CONFIRMED_BY_OFFICIAL_DOCUMENTATION", "REQUIRES_SOURCE_PROBE", "CONTRADICTED", "UNKNOWN"]
    evidence: str


class SourceCapability(Contract):
    source: str
    documentation_url: str
    host: str | None
    request_contract: str
    expected_fields: tuple[str, ...]
    provenance: str
    limits_and_access: str
    historical_limitations: str
    unknowns: tuple[str, ...]
    probe_supported: bool
    priority: int


class SamplePlan(Contract):
    contract_version: Literal["historical-identity-sample-plan-v1"] = "historical-identity-sample-plan-v1"
    status: Literal["DIAGNOSTIC_PLAN"] = "DIAGNOSTIC_PLAN"
    context: Context
    sampling_policy: Literal["historical-identity-round-robin-v1"] = "historical-identity-round-robin-v1"
    requested_sample_size: int = Field(ge=1,le=48)
    population_secids: int
    samples: tuple[SampleUnit, ...]
    strata: tuple[Stratum, ...]
    diagnoses: tuple[Diagnosis, ...]
    source_matrix: tuple[SourceCapability, ...]
    plan_sha256: str = ""
    network_access: Literal[False] = False


class ProbeLimits(Contract):
    max_requests: int = Field(default=72,ge=1,le=72)
    max_pages: int = Field(default=48,ge=1,le=48)
    response_bytes: int = Field(default=8*1024**2,ge=1,le=8*1024**2)
    total_response_bytes: int = Field(default=64*1024**2,ge=1,le=64*1024**2)
    max_seconds: int = Field(default=300,ge=1,le=300)
    evidence_bytes: int = Field(default=128*1024**2,ge=1,le=128*1024**2)
    reserve_bytes: int = Field(default=8*1024**3,ge=8*1024**3)


class ProbeAuthorization(Contract):
    contract_version: Literal["historical-identity-probe-authorization-v1"] = "historical-identity-probe-authorization-v1"
    operation: Literal["IDENTITY_SOURCE_PROBE"] = "IDENTITY_SOURCE_PROBE"
    explicit_authorization: Literal[True]
    plan_sha256: str
    context: Context
    secids: tuple[str, ...]
    max_secids: int = Field(ge=1,le=12)
    sources: tuple[Source, ...]
    limits: ProbeLimits
    destination_sha256: str
    authorization_sha256: str = ""

    @field_validator("explicit_authorization",mode="before")
    @classmethod
    def explicit(cls,value):
        if value is not True: raise ValueError("EXPLICIT_AUTHORIZATION_REQUIRED")
        return value


class ProbeRequest(Contract):
    source: Source
    secid: str
    offset: int = Field(ge=0)
    method: Literal["GET"] = "GET"
    host: Literal["iss.moex.com"] = "iss.moex.com"
    path: str
    params: tuple[tuple[str, str], ...]


class AttemptReceipt(Contract):
    sequence: int = Field(ge=1)
    authorization_sha256: str
    request: ProbeRequest
    request_sha256: str
    previous_receipt_sha256: str | None
    observed_at: datetime
    outcome: Literal["ACCEPTED", "TRANSIENT_SOURCE_FAILURE", "SOURCE_FAILURE", "SOURCE_INVALID", "BUDGET_STOP"]
    category: str
    received_bytes: int = Field(ge=0)
    response_sha256: str | None
    raw_stored: bool
    complete: bool | None
    next_offset: int | None
    cursor_total: int | None
    cursor_page_size: int | None
    completion_basis: Literal["CURSOR", "EMPTY_TERMINATOR", "SINGLE_SPECIFICATION"] | None
    elapsed_seconds: float = Field(ge=0,allow_inf_nan=False)
    receipt_sha256: str = ""


class ProbeManifest(Contract):
    contract_version: Literal["historical-identity-probe-manifest-v1"] = "historical-identity-probe-manifest-v1"
    status: Literal["COMPLETE", "INCOMPLETE", "BLOCKED"]
    plan_sha256: str
    authorization_sha256: str
    accepted_pages: int
    attempts: int
    received_bytes: int
    receipt_sha256s: tuple[str, ...]
    blockers: tuple[str, ...]
    network_access: bool
    evidence_sha256: str = ""


class SourceResult(Contract):
    secid: str
    source: Source
    status: Resolution
    request_sha256s: tuple[str, ...]
    response_sha256s: tuple[str, ...]
    observation_times: tuple[datetime, ...]
    retrieval_times: tuple[datetime, ...]
    returned_secid: str | None
    returned_isin: str | None
    returned_isin_candidates: tuple[str, ...]
    candidate_count: int
    exact_candidate_count: int
    complete_response: bool
    attempt_count: int
    transient_failure_count: int
    current_binding_observed: bool
    requires_review: bool
    diagnostics: tuple[str, ...]
    source_type_values: tuple[str, ...]
    source_reported_publication_values: tuple[str, ...]
    source_reported_effective_values: tuple[str, ...]
    provenance: Literal["CURRENT_SOURCE_OBSERVATION"] = "CURRENT_SOURCE_OBSERVATION"
    historical_decision_usability: Literal["UNPROVEN"] = "UNPROVEN"
    publication_authority_status: Literal["UNPROVEN"] = "UNPROVEN"


class EvaluatedUnit(Contract):
    sample: SampleUnit
    approved: bool
    actually_probed: bool
    results: tuple[SourceResult, ...]
    status: Resolution
    recovered_current_isin: str | None
    requires_review: bool


class StratumEvaluation(Contract):
    name: str
    selected: int
    probed: int
    recovered: int


class FeasibilityReport(Contract):
    contract_version: Literal["historical-identity-source-feasibility-v1"] = "historical-identity-source-feasibility-v1"
    context: Context
    plan_sha256: str
    authorization_sha256: str
    evidence_sha256: str
    feasibility: Literal["FEASIBLE_ON_OBSERVED_SAMPLE", "PARTIALLY_FEASIBLE_ON_OBSERVED_SAMPLE", "INCONCLUSIVE", "SOURCE_CONTRACT_BLOCKED", "NOT_EXECUTED"]
    units: tuple[EvaluatedUnit, ...]
    metrics: tuple[Metric, ...]
    strata: tuple[StratumEvaluation, ...]
    limitations: tuple[str, ...]
    next_steps: tuple[str, ...]
    report_sha256: str = ""
    network_access: Literal[False] = False


class OperationResult(Contract):
    contract_version: Literal["historical-identity-operation-v1"] = "historical-identity-operation-v1"
    mode: Literal["PLAN", "PROBE_AUTHORIZE", "PROBE", "PROBE_EVALUATE"]
    status: Literal["COMPLETE", "BLOCKED", "INCOMPLETE"]
    artifact_sha256: str | None = None
    blockers: tuple[str, ...] = ()
