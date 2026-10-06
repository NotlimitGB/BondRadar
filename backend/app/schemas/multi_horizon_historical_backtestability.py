"""Task306A2 diagnostic evidence contracts, without economic performance."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, field_validator, ValidationInfo
from app.schemas.modern_historical_replay_readiness import Contract
from app.schemas.historical_replay_blocker_root_cause import FunnelStageV1

class HistoricalResearchHorizonPolicyV1(Contract):
    horizons_days: tuple[Literal[90,180,365], ...] = (90,180,365)
    primary_horizon_days: Literal[365] = 365
    endpoint_max_age_days: Literal[7] = 7
    monthly_entry_grid: Literal["FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"] = "FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"

    @field_validator("horizons_days",mode="before")
    @classmethod
    def fixed_horizons(cls,value,info: ValidationInfo):
        if info.mode=="json" and type(value) is list:value=tuple(value)
        if type(value) is not tuple or value!=(90,180,365) or any(type(n) is not int for n in value):
            raise ValueError("FIXED_HORIZON_POLICY_REQUIRED")
        return value

class RawFieldRecoverabilityV1(Contract):
    field: Literal["clean_price","price","nkd"]
    status: Literal["ALREADY_CANONICAL","RAW_RECOVERABLE","RAW_CONFLICT","RAW_INVALID","RAW_MISSING"]
    value: Decimal | None = None
    source_fields: tuple[str,...] = ()
    blockers: tuple[str,...] = ()

class SnapshotEndpointReadinessV1(Contract):
    bond_id: int
    target_date: date
    kind: Literal["ENTRY","TERMINAL"]
    snapshot_id: int | None = None
    trade_date: date | None = None
    age_days: int | None = None
    price_basis: str | None = None
    recovered_price_basis: str | None = None
    current_terms_ready: bool = False
    fresh: bool = False
    canonical_price_ready: bool = False
    canonical_nkd_ready: bool = False
    recoverable_price_ready: bool = False
    recoverable_nkd_ready: bool = False
    canonical_ready: bool = False
    after_raw_recovery_ready: bool = False
    fields: tuple[RawFieldRecoverabilityV1,...] = ()
    blockers: tuple[str,...] = ()

class HistoricalBondHorizonReadinessV1(Contract):
    bond_id: int
    entry: SnapshotEndpointReadinessV1
    terminal: SnapshotEndpointReadinessV1 | None
    terminal_path: Literal["MARKET","REDEEMED"]
    redemption_date: date | None = None
    cashflow_events_valid: bool
    persisted_event_count: int
    persisted_coupon_event_count: int
    coupon_events_valid: bool
    coupon_baseline_observable: bool
    zero_persisted_events: bool
    contractual_completeness: Literal["UNPROVEN"] = "UNPROVEN"
    canonical_ready: bool
    after_raw_recovery_ready: bool
    blockers: tuple[str,...]
    diagnostics: tuple[str,...]

class HistoricalHorizonReadinessV1(Contract):
    horizon_days: Literal[90,180,365]
    terminal_target_date: date
    terminal_target_inside_global_range: bool
    entry_ready_bond_count: int
    terminal_market_ready_bond_count: int
    redeemed_ready_bond_count: int
    cashflow_valid_bond_count: int
    terminal_outcome_ready_bond_count: int
    terminal_outcome_ready_after_raw_recovery_count: int
    coupon_baseline_observable_bond_count: int
    decision_ready_bond_ids: tuple[int,...]
    decision_ready_after_raw_recovery_bond_ids: tuple[int,...]
    canonical_funnel: tuple[FunnelStageV1,...]
    recoverable_funnel: tuple[FunnelStageV1,...]
    bonds: tuple[HistoricalBondHorizonReadinessV1,...]
    blockers: tuple[str,...]

class OfzHistoricalHorizonReadinessV1(Contract):
    as_of_date: date
    selection_as_of_date: date
    task306a1_curve_status_at_t: str
    curve_status: str
    curve_trade_date: date | None
    node_count: int
    component_bond_ids: tuple[int,...]
    curve_ready: bool
    modified_duration_prerequisites_ready: bool
    actual_task272_readiness: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    frequency_recoverability: tuple[tuple[int,str],...]
    horizons: tuple[HistoricalHorizonReadinessV1,...]
    blockers: tuple[str,...]

class HistoricalMultiHorizonDateReadinessV1(Contract):
    as_of_date: date
    monthly_research_entry_date: bool
    decision_only_bond_ids: tuple[int,...]
    decision_only_intersection_count: int
    credit_prerequisite_count: int
    horizons: tuple[HistoricalHorizonReadinessV1,...]

class HorizonPortfolioCoverageV1(Contract):
    horizon_days: Literal[90,180,365]
    monthly_entry_date_count: int
    dates_with_3_ready: int
    dates_with_5_ready: int
    dates_with_10_ready: int
    dates_with_3_after_raw_recovery: int
    dates_with_5_after_raw_recovery: int
    dates_with_10_after_raw_recovery: int

class Historical365DayQualificationReadinessV1(Contract):
    monthly_entry_date_count: int = 0
    entry_dates_with_terminal_global_range: int = 0
    dates_with_any_terminal_outcome: int = 0
    dates_with_any_after_raw_recovery: int = 0
    classification: Literal["NO_365D_WINDOWS","PARTIAL_365D_WINDOWS","RESEARCH_WINDOWS_AVAILABLE"] = "NO_365D_WINDOWS"
    after_raw_recovery_classification: Literal["NO_365D_WINDOWS","PARTIAL_365D_WINDOWS","RESEARCH_WINDOWS_AVAILABLE"] = "NO_365D_WINDOWS"
    coverage: HorizonPortfolioCoverageV1 | None = None

class HistoricalResearchRangeScenarioV1(Contract):
    scenario: str
    monthly_window_count: int
    desired_earliest_entry_date: date
    desired_latest_completed_entry_date: date
    required_history_start_date: date
    required_history_end_date: date
    missing_calendar_span_days: int
    range_covered: bool
    remaining_evidence_families: tuple[str,...] = ("PRICE","NKD","CASHFLOW","SECURITY_MASTER","OFZ","HISTORICAL_UNIVERSE")

class EndpointCoverageV1(Contract):
    endpoint: str
    snapshot_count: int
    usable_price_and_nkd: int
    usable_price_missing_nkd: int
    missing_price_usable_nkd: int
    missing_both: int
    already_ready: int
    additional_ready_from_raw: int
    still_unavailable: int
    raw_field_status_counts: tuple[tuple[str,int],...]

class BackfillRequirementV1(Contract):
    category: Literal["NO_MARKET_REPAIR_REQUIRED","OFFLINE_RAW_NKD_REPAIR_SUFFICIENT","OFFLINE_RAW_PRICE_REPAIR_REQUIRED","OFFLINE_RAW_PRICE_AND_NKD_REPAIR_REQUIRED","TARGETED_ENDPOINT_MOEX_BACKFILL_REQUIRED","BROAD_HISTORICAL_MOEX_BACKFILL_REQUIRED","CASHFLOW_HISTORY_REPAIR_REQUIRED","OFZ_SECURITY_MASTER_REPAIR_REQUIRED","HISTORICAL_UNIVERSE_CAPTURE_REQUIRED","SECURITY_MASTER_VERSIONING_REQUIRED"]
    factual_reasons: tuple[str,...]

class HistoricalBacktestabilityCapabilitiesV1(Contract):
    multi_horizon_endpoint_audit_ready: Literal[True] = True
    raw_recoverability_audit_ready: Literal[True] = True
    historical_range_planning_ready: Literal[True] = True
    historical_profitability_ready: Literal[False] = False
    replay_ready: Literal[False] = False
    live_ready: Literal[False] = False

class MultiHorizonHistoricalBacktestabilityAuditV1(Contract):
    contract_version: Literal["multi-horizon-historical-backtestability-audit-v1"] = "multi-horizon-historical-backtestability-audit-v1"
    status: Literal["COMPLETE","BLOCKED"]
    policy: HistoricalResearchHorizonPolicyV1 = Field(default_factory=HistoricalResearchHorizonPolicyV1)
    source_task306a1_sha256: str | None = None
    market_date_range: tuple[date,date] | None = None
    raw_market_dates: tuple[date,...] = ()
    monthly_entry_dates: tuple[date,...] = ()
    per_date: tuple[HistoricalMultiHorizonDateReadinessV1,...] = ()
    readiness_by_horizon: tuple[HorizonPortfolioCoverageV1,...] = ()
    primary_365d_readiness: Historical365DayQualificationReadinessV1 = Field(default_factory=Historical365DayQualificationReadinessV1)
    capabilities: HistoricalBacktestabilityCapabilitiesV1 = Field(default_factory=HistoricalBacktestabilityCapabilitiesV1)
    canonical_market_coverage: tuple[EndpointCoverageV1,...] = ()
    raw_recoverability: tuple[EndpointCoverageV1,...] = ()
    research_range_scenarios: tuple[HistoricalResearchRangeScenarioV1,...] = ()
    required_backfill_ranges: tuple[HistoricalResearchRangeScenarioV1,...] = ()
    ofz_readiness: tuple[OfzHistoricalHorizonReadinessV1,...] = ()
    remediation_requirements: tuple[BackfillRequirementV1,...] = ()
    known_limitations: tuple[str,...] = ()
    blockers: tuple[str,...] = ()
    audit_sha256: str | None = None
    DB_MUTATION: Literal[False] = False
    NETWORK_ACCESS: Literal[False] = False
    historical_profitability_calculated: Literal[False] = False
    actual_task277_readiness: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    hash_method: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = "SORTED_CANONICAL_JSON_SHA256_V1"
