"""Task306A audit facts; capabilities are not assertions of historical safety."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    pit_ready: Literal[False] = False

    @field_validator("pit_ready", mode="before")
    @classmethod
    def exact_false(cls, value):
        if value is not False:
            raise ValueError("PIT_DECLARATION_INVALID")
        return value


class AuditConfiguration(Contract):
    market_source: Literal["moex"] = "moex"
    horizon_days: Literal[90] = 90
    liquidity_lookback_calendar_days: Literal[30] = 30
    liquidity_min_observation_days: Literal[5] = 5
    maximum_quote_age_days: Literal[7] = 7
    decision_cutoff: Literal["START_OF_DAY_UTC"] = "START_OF_DAY_UTC"
    outcome_scope: Literal["OBSERVED_PERSISTED_EVIDENCE"] = "OBSERVED_PERSISTED_EVIDENCE"
    diagnostic_minimum_bonds: Literal[3] = 3


class CountEntry(Contract):
    key: str
    count: int
    pct: Decimal = Decimal("0")


class FieldCoverage(Contract):
    field: str
    row_count: int
    nonnull_count: int
    nonnull_pct: Decimal
    usable_count: int
    bond_count: int = 0
    date_count: int = 0
    verified_count: int = 0


class EvidenceTimeRange(Contract):
    field: str
    nonnull_count: int
    null_count: int
    minimum: datetime | None = None
    maximum: datetime | None = None


class EvidenceDateRange(Contract):
    field: str
    nonnull_count: int
    null_count: int
    minimum: date | None = None
    maximum: date | None = None


class HistoricalSourceInventoryV1(Contract):
    source_table: str
    row_count: int
    bond_count: int = 0
    date_count: int = 0
    min_date: date | None = None
    max_date: date | None = None
    fields: tuple[FieldCoverage, ...] = ()
    breakdowns: tuple[CountEntry, ...] = ()
    time_ranges: tuple[EvidenceTimeRange, ...] = ()
    date_ranges: tuple[EvidenceDateRange, ...] = ()
    diagnostics: tuple[str, ...] = ()


class ObservedBondHistory(Contract):
    bond_id: int
    snapshot_count: int
    first_trade_date: date | None
    last_trade_date: date | None
    current_listing_status: str | None
    canonical_ofz: bool
    cashflow_count: int


class CreditTargetInventory(Contract):
    target_kind: str
    bond_id: int | None
    legal_issuer_id: int | None
    rating_agency: str
    source_provider: str
    rating_scale_raw: str | None
    rating_value_raw: str | None
    event_count: int
    first_event_date: date
    last_event_date: date
    publication_breakdown: tuple[CountEntry, ...]


class MarketDateCoverage(Contract):
    trade_date: date
    row_count: int
    bond_count: int
    positive_duration_node_count: int = 0


class HistoricalEvidenceFamilyAuditV1(Contract):
    family: str
    available_now: bool
    historically_dated: bool
    historical_availability_proven: bool
    historical_value_versioned: bool
    current_state_dependency: bool
    pit_status: Literal["PROVEN", "PARTIAL", "UNPROVEN", "NOT_APPLICABLE"]
    current_model_input: bool = True
    blockers: tuple[str, ...] = ()


class HistoricalReplayDateReadinessV1(Contract):
    as_of_date: date
    classification: Literal["PIT_SAFE", "DIAGNOSTIC_ONLY", "UNUSABLE"]
    observed_universe_bond_count: int
    entry_market_ready_bond_count: int
    liquidity_history_ready_bond_count: int
    liquidity_component_ready_bond_count: int = 0
    liquidity_benchmark_eligible_bond_count: int = 0
    liquidity_benchmark_size_ready: bool = False
    credit_evidence_ready_bond_count: int
    rating_publication_proven_bond_count: int
    security_master_current_ready_bond_count: int
    security_master_pit_proven_bond_count: int = 0
    issuer_current_verified_bond_count: int = 0
    issuer_pit_proven_bond_count: int = 0
    duration_market_history_bond_count: int = 0
    duration_pit_terms_proven: bool = False
    ofz_market_ready: bool
    ofz_duration_node_count: int
    ofz_structure_pit_proven: bool = False
    outcome_90d_ready_bond_count: int
    outcome_full_count: int
    outcome_partial_count: int
    outcome_none_count: int
    coupon_history_ready_bond_count: int
    cashflow_completeness_proven: bool = False
    universe_membership_proven: bool = False
    joint_qualifying_bond_ids: tuple[int, ...] = ()
    credit_peer_qualified_bond_ids: tuple[int, ...] = ()
    first_observation_after_entry_bond_count: int = 0
    blockers: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class HistoricalReplayWindowSummaryV1(Contract):
    all_observed_candidate_dates: tuple[date, ...] = ()
    monthly_candidate_dates: tuple[date, ...] = ()
    monthly_grid_method: Literal["FIRST_ELIGIBLE_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"] = "FIRST_ELIGIBLE_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"
    minimum_bonds_per_trade_date: int = 0
    median_bonds_per_trade_date: Decimal = Decimal("0")
    maximum_bonds_per_trade_date: int = 0
    missing_calendar_date_count: int = 0
    duplicate_economic_date_count: int = 0


class AuditCapabilities(Contract):
    historical_data_inventory_ready: Literal[True] = True
    pit_readiness_audit_ready: Literal[True] = True
    historical_universe_audit_ready: Literal[True] = True
    survivorship_bias_audit_ready: Literal[True] = True
    market_history_coverage_audit_ready: Literal[True] = True
    security_master_pit_audit_ready: Literal[True] = True
    credit_publication_audit_ready: Literal[True] = True
    financial_publication_audit_ready: Literal[True] = True
    liquidity_history_audit_ready: Literal[True] = True
    duration_dv01_pit_audit_ready: Literal[True] = True
    ofz_history_audit_ready: Literal[True] = True
    cashflow_history_audit_ready: Literal[True] = True
    outcome_90d_coverage_audit_ready: Literal[True] = True
    legacy_label_inventory_ready: Literal[True] = True
    replay_date_classification_ready: Literal[True] = True
    historical_replay_engine_ready: Literal[False] = False
    historical_profitability_ready: Literal[False] = False
    historical_alpha_proven: Literal[False] = False
    model_effectiveness_proven: Literal[False] = False
    live_ready: Literal[False] = False
    official_day0_started: Literal[False] = False


class HistoricalReplayReadinessAuditV1(Contract):
    contract_version: Literal["modern-historical-replay-readiness-audit-v1"] = "modern-historical-replay-readiness-audit-v1"
    status: Literal["COMPLETE", "BLOCKED"]
    audit_classification: Literal["PIT_READY_FOR_REPLAY", "DIAGNOSTIC_REPLAY_ONLY", "INSUFFICIENT_FOR_REPLAY"]
    configuration: AuditConfiguration = Field(default_factory=AuditConfiguration)
    horizon_days: Literal[90] = 90
    market_source: Literal["moex"] = "moex"
    source_inventories: tuple[HistoricalSourceInventoryV1, ...] = ()
    evidence_matrix: tuple[HistoricalEvidenceFamilyAuditV1, ...] = ()
    bond_histories: tuple[ObservedBondHistory, ...] = ()
    credit_target_inventory: tuple[CreditTargetInventory, ...] = ()
    market_date_coverage: tuple[MarketDateCoverage, ...] = ()
    ofz_market_date_coverage: tuple[MarketDateCoverage, ...] = ()
    per_date_readiness: tuple[HistoricalReplayDateReadinessV1, ...] = ()
    window_summary: HistoricalReplayWindowSummaryV1 = Field(default_factory=HistoricalReplayWindowSummaryV1)
    all_candidate_date_count: int = 0
    monthly_candidate_date_count: int = 0
    pit_safe_date_count: int = 0
    diagnostic_only_date_count: int = 0
    unusable_date_count: int = 0
    earliest_candidate_date: date | None = None
    latest_candidate_date: date | None = None
    earliest_pit_safe_date: date | None = None
    latest_pit_safe_date: date | None = None
    earliest_diagnostic_date: date | None = None
    latest_diagnostic_date: date | None = None
    historical_universe_membership_proven: bool = False
    known_p0_blockers: tuple[str, ...] = ()
    remediation_requirements: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    audit_sha256: str | None = None
    hash_method: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = "SORTED_CANONICAL_JSON_SHA256_V1"
    DB_MUTATION: Literal[False] = False
    NETWORK_ACCESS: Literal[False] = False
    capabilities: AuditCapabilities = Field(default_factory=AuditCapabilities)
