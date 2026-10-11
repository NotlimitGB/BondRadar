"""Task307 diagnostic contracts; no historical readiness promotion."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")

    @field_validator("pit_ready","db_mutation","network_access","acquisition_ready",
                     "historical_universe_complete","official_day0","replay","profitability",
                     mode="before",check_fields=False)
    @classmethod
    def exact_safety(cls, value):
        if value is not False:
            raise ValueError("AUDIT_SAFETY_DECLARATION_INVALID")
        return value


class AuditLimits(Contract):
    scratch_bytes: int = Field(default=4 * 1024**3, gt=0)
    output_bytes: int = Field(default=32 * 1024**2, gt=0)
    reserve_bytes: int = Field(default=8 * 1024**3, ge=0)
    max_identity_rows: int = Field(default=100000, gt=0)


class Metric(Contract):
    name: str
    count: int = Field(ge=0)
    denominator: int = Field(ge=0)
    percentage: Decimal

    @model_validator(mode="after")
    def valid(self):
        if self.count > self.denominator or not self.percentage.is_finite() or not 0 <= self.percentage <= 100:
            raise ValueError("AUDIT_METRIC_INVALID")
        return self


class Integrity(Contract):
    status: Literal["VERIFIED", "NOT_VERIFIED"] = "NOT_VERIFIED"
    manifest_verified: bool = False
    indexes_verified: bool = False
    receipt_pages_verified: bool = False
    index_replay_verified: bool = False
    reconciliation_verified: bool = False
    verification_level: Literal["FULL_ACCEPTED_GENERATION_REPLAY", "NONE"] = "NONE"
    prior_operator_validation_used_as_authority: Literal[False] = False


class DiscoveryCoverage(Contract):
    history_start: date
    cutoff: date
    requested_partitions: int
    completed_partitions: int
    accepted_pages: int
    http_attempts: int
    retries: int
    empty_market_dates: tuple[date, ...]
    source_families: tuple[str, ...]
    source_observation_first: datetime
    source_observation_last: datetime


class UniverseCoverage(Contract):
    identity_records: int
    distinct_secids: int
    distinct_isins: int
    exact_pairs: int
    secid_board_pairs: int
    listing_pairs: int
    observed_pairs: int
    listing_only_pairs: int
    observed_only_pairs: int
    shared_pairs: int
    missing_isin_pairs: int
    affected_missing_isin_secids: int
    conflicting_secids: int
    multi_board_pairs: int
    not_observed_on_last_market_date_pairs: int


class IdentityDiagnostic(Contract):
    secid: str
    isin: str | None
    listing: bool
    observed: bool
    listing_boards: tuple[str, ...]
    observed_boards: tuple[str, ...]
    first_observed_date: date | None
    last_observed_date: date | None
    first_listing_interval: date | None
    last_listing_interval: date | None
    observed_date_count: int
    actual_trade_date_count: int
    usable_price_nkd_date_count: int
    conflicting_isin_candidates: tuple[str, ...]


class IdentityCoverage(Contract):
    unresolved_definition: Literal["DISTINCT_EXACT_SECID_PLUS_NULL_ISIN"] = "DISTINCT_EXACT_SECID_PLUS_NULL_ISIN"
    missing_isin_listing_records: int
    missing_isin_history_records: int
    affected_market_dates: tuple[date, ...]
    listing_market_discrepancy_secids: tuple[str, ...]
    identities: tuple[IdentityDiagnostic, ...]


class PeriodCoverage(Contract):
    period: str
    market_rows: int
    observed_dates: int
    actual_trade_rows: int
    zero_trade_rows: int
    unknown_trade_rows: int
    usable_price_nkd_rows: int
    actual_and_usable_rows: int
    distinct_identities: int


class HorizonCoverage(Contract):
    horizon_days: Literal[90, 180, 365]
    target_date: date | None
    status: Literal["OBSERVED_RANGE_REACHES_TARGET", "OBSERVED_RANGE_SHORT", "BEYOND_CUTOFF", "NO_ENTRY_DATE"]
    entry_identities_with_subsequent_observation: int | None


class EntryCoverage(Contract):
    month: str
    entry_date: date | None
    source_rows: int
    identities: int
    actual_trade_identities: int
    usable_price_nkd_identities: int
    jointly_covered_identities: int
    diagnostics: tuple[str, ...]
    horizons: tuple[HorizonCoverage, ...]


class DateCoverage(Contract):
    observed_dates: tuple[date, ...]
    actual_trade_dates: tuple[date, ...]
    usable_price_nkd_dates: tuple[date, ...]
    actual_and_usable_dates: tuple[date, ...]
    unknown_trade_dates: tuple[date, ...]
    annual: tuple[PeriodCoverage, ...]
    monthly: tuple[PeriodCoverage, ...]
    research_entries: tuple[EntryCoverage, ...]


class BoardCoverage(Contract):
    board: str
    market_rows: int
    distinct_identities: int
    observed_dates: int
    first_date: date | None
    last_date: date | None
    actual_trade_rows: int
    zero_trade_rows: int
    unknown_trade_rows: int
    usable_price_nkd_rows: int
    eligibility: Literal["UNVERIFIED"] = "UNVERIFIED"


class PitEvidence(Contract):
    family: str
    available_evidence: str
    unavailable_evidence: str
    provenance: str
    publication_proven: Literal[False] = False
    decision_time_usability: Literal["UNPROVEN", "NOT_ACQUIRED"]
    blocker: str


class Remediation(Contract):
    priority: Literal["P0", "P1", "P2", "P3"]
    reason: str
    affected_count: int | None
    missing_proof: str
    source_family: str
    required_step: str
    validation: str
    dependency: str


class Capabilities(Contract):
    source_coverage_audit: bool = True
    deep_accepted_source_integrity: bool = False
    historical_universe_complete: Literal[False] = False
    pit_ready: Literal[False] = False
    acquisition_ready: Literal[False] = False
    db_mutation: Literal[False] = False
    network_access: Literal[False] = False
    replay: Literal[False] = False
    profitability: Literal[False] = False
    official_day0: Literal[False] = False


class HistoricalDiscoveryCoverageAudit(Contract):
    contract_version: Literal["historical-discovery-coverage-audit-v1"] = "historical-discovery-coverage-audit-v1"
    status: Literal["COMPLETE", "BLOCKED"]
    source_manifest_sha256: str | None = None
    source_content_sha256: str | None = None
    scope_sha256: str | None = None
    implementation_sha256: str | None = None
    audit_sha256: str = ""
    evidence_integrity: Integrity = Integrity()
    discovery_coverage: DiscoveryCoverage | None = None
    universe_coverage: UniverseCoverage | None = None
    identity_coverage: IdentityCoverage | None = None
    market_quality: tuple[Metric, ...] = ()
    trading_activity: tuple[Metric, ...] = ()
    price_nkd_coverage: tuple[Metric, ...] = ()
    date_coverage: DateCoverage | None = None
    board_coverage: tuple[BoardCoverage, ...] = ()
    ofz_coverage: tuple[Metric, ...] = ()
    pit_evidence: tuple[PitEvidence, ...] = ()
    readiness_blockers: tuple[str, ...] = ()
    remediation_priorities: tuple[Remediation, ...] = ()
    capabilities: Capabilities = Capabilities()

    @model_validator(mode="after")
    def envelope(self):
        sections=(self.discovery_coverage,self.universe_coverage,self.identity_coverage,self.date_coverage)
        if self.status=="COMPLETE":
            checks=(self.evidence_integrity.manifest_verified,self.evidence_integrity.indexes_verified,
                    self.evidence_integrity.receipt_pages_verified,self.evidence_integrity.index_replay_verified,
                    self.evidence_integrity.reconciliation_verified)
            if any(s is None for s in sections) or self.evidence_integrity.status!="VERIFIED" or not all(checks) or not self.capabilities.deep_accepted_source_integrity:
                raise ValueError("AUDIT_COMPLETE_ENVELOPE_INVALID")
        elif any(s is not None for s in sections) or self.capabilities.deep_accepted_source_integrity or self.evidence_integrity.status!="NOT_VERIFIED":
            raise ValueError("AUDIT_BLOCKED_ENVELOPE_INVALID")
        return self
