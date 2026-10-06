"""Task306A1 diagnostic contracts, never historical performance claims."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field
from app.schemas.modern_historical_replay_readiness import Contract, CountEntry


class RepresentativeBondV1(Contract):
    bond_id: int
    isin: str | None
    secid: str | None


class FunnelStageV1(Contract):
    stage: str
    kind: Literal["CUMULATIVE_GATE", "INDEPENDENT_DIAGNOSTIC"]
    unit: Literal["BONDS", "BOND_DATES", "COMPONENTS_TO_NODES"] = "BONDS"
    input_count: int
    pass_count: int
    fail_count: int
    pass_pct: Decimal
    independent_pass_count: int = 0


class BlockerCountV1(Contract):
    reason: str
    affected_bond_date_count: int
    affected_date_count: int
    distinct_bond_count: int
    blocking: bool = True
    universe_count: int = 0
    affected_pct: Decimal = Decimal("0")
    representative_bonds: tuple[RepresentativeBondV1, ...] = ()


class CountDistributionV1(Contract):
    count: int = 0
    minimum: int | None = None
    median: Decimal | None = None
    maximum: int | None = None


class OutcomeGapSummaryV1(Contract):
    maximum_continuous_covered_days: CountDistributionV1 = Field(default_factory=CountDistributionV1)
    first_gap_offset_from_entry: CountDistributionV1 = Field(default_factory=CountDistributionV1)
    largest_uncovered_interval_days: CountDistributionV1 = Field(default_factory=CountDistributionV1)
    gap_count: CountDistributionV1 = Field(default_factory=CountDistributionV1)
    analysis_scope: Literal["FULL_CALENDAR_HORIZON_INSIDE_MARKET_RANGE"] = "FULL_CALENDAR_HORIZON_INSIDE_MARKET_RANGE"


class RootCauseSummaryV1(Contract):
    funnel: tuple[FunnelStageV1, ...] = ()
    reasons: tuple[BlockerCountV1, ...] = ()
    primary_root_causes: tuple[str, ...] = ()
    secondary_root_causes: tuple[str, ...] = ()
    affected_date_count: int = 0
    affected_bond_date_count: int = 0


class OutcomeRootCauseSummaryV1(RootCauseSummaryV1):
    candidate_date_count_with_complete_calendar_horizon: int = 0
    candidate_date_count_beyond_available_history: int = 0
    earliest_theoretically_complete_horizon_date: date | None = None
    latest_theoretically_complete_horizon_date: date | None = None
    horizons_with_zero_persisted_cashflow_events: int = 0
    cashflow_type_counts: tuple[CountEntry, ...] = ()
    gaps: OutcomeGapSummaryV1 = Field(default_factory=OutcomeGapSummaryV1)


class CreditCohortKeyV1(Contract):
    target_kind: str
    rating_agency: str
    source_provider: str
    rating_scale_raw: str | None
    rating_value_raw: str


class CreditCohortCountV1(Contract):
    key: CreditCohortKeyV1
    bond_ids: tuple[int, ...]
    total_member_count: int
    size_implied_peer_count: int


class HistoricalCohortMaximumV1(Contract):
    key: CreditCohortKeyV1
    maximum_total_member_count: int
    first_date_at_maximum: date


class OfzRootCauseSummaryV1(RootCauseSummaryV1):
    canonical_ofz_count: int = 0
    canonical_ofz_with_market_history_count: int = 0
    first_market_date: date | None = None
    last_market_date: date | None = None
    market_observation_count: int = 0
    usable_yield_observation_count: int = 0
    usable_duration_observation_count: int = 0
    usable_yield_and_duration_observation_count: int = 0
    structural_distributions: tuple[CountEntry, ...] = ()
    market_duration_nodes_by_date: tuple[tuple[date, int], ...] = ()
    task268_ready_date_count: int = 0
    diagnostic_gate_mismatch_date_count: int = 0


class CreditPeerRootCauseSummaryV1(RootCauseSummaryV1):
    proven_funnel: tuple[FunnelStageV1, ...] = ()
    top_cohort_maxima: tuple[HistoricalCohortMaximumV1, ...] = ()
    maximum_cohort_sizes_by_target_kind: tuple[CountEntry, ...] = ()
    actual_task277_readiness: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


class JointChainRootCauseSummaryV1(RootCauseSummaryV1):
    maximum_decision_only_intersection_count: int = 0
    maximum_decision_plus_outcome_intersection_count: int = 0
    maximum_independent_credit_peer_prerequisite_count: int = 0


class RemediationEvidenceV1(Contract):
    category: Literal["AUDIT_LOGIC_FIX", "MARKET_HISTORY_BACKFILL", "CASHFLOW_HISTORY_REPAIR",
        "SECURITY_MASTER_EVIDENCE_VERSIONING", "OFZ_SECURITY_MASTER_REPAIR", "ISSUER_IDENTITY_VERSIONING",
        "CREDIT_COHORT_EVIDENCE_REPAIR", "HISTORICAL_UNIVERSE_CAPTURE", "NO_REMEDIATION_REQUIRED"]
    factual_reasons: tuple[str, ...]


class HistoricalReplayBlockerDateRcaV1(Contract):
    as_of_date: date
    source_task306a_classification: str
    outcome_funnel: tuple[FunnelStageV1, ...]
    outcome_reasons: tuple[BlockerCountV1, ...]
    outcome_primary_blockers: tuple[str, ...]
    outcome_horizon_inside_available_market_history: bool
    maximum_continuous_outcome_days: int
    outcome_gaps: OutcomeGapSummaryV1
    horizons_with_zero_persisted_cashflow_events: int
    ofz_funnel: tuple[FunnelStageV1, ...]
    ofz_reasons: tuple[BlockerCountV1, ...]
    task306a_ofz_node_count: int
    task268_status: str
    task268_curve_trade_date: date | None
    task268_node_count: int
    task268_diagnostics: tuple[CountEntry, ...]
    task268_uses_entry_day_quotes: bool
    credit_funnel: tuple[FunnelStageV1, ...]
    credit_proven_funnel: tuple[FunnelStageV1, ...]
    credit_reasons: tuple[BlockerCountV1, ...]
    credit_cohorts: tuple[CreditCohortCountV1, ...]
    credit_proven_cohorts: tuple[CreditCohortCountV1, ...]
    unique_credit_key_count: int
    maximum_credit_cohort_size: int
    median_credit_cohort_size: Decimal
    credit_cohorts_size_one_count: int
    credit_cohorts_size_two_count: int
    credit_cohorts_size_ge_three_count: int
    actual_task277_readiness: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    joint_funnel: tuple[FunnelStageV1, ...]
    joint_reasons: tuple[BlockerCountV1, ...]
    decision_only_intersection_count: int
    decision_plus_outcome_intersection_count: int
    source_task306a_joint_count: int
    source_task306a_peer_count: int
    reconstructed_task306a_joint_count: int
    reconstructed_task306a_peer_count: int
    task306a_liquidity_benchmark_count: int
    authoritative_liquidity_component_count: int
    task270_benchmark_size_prerequisite_met: bool
    blockers: tuple[str, ...]
    diagnostics: tuple[str, ...]


class RcaCapabilitiesV1(Contract):
    outcome_root_cause_funnel_ready: Literal[True] = True
    outcome_horizon_tail_separated: Literal[True] = True
    outcome_quote_gap_analysis_ready: Literal[True] = True
    outcome_cashflow_blocker_analysis_ready: Literal[True] = True
    ofz_root_cause_funnel_ready: Literal[True] = True
    task268_historical_crosscheck_ready: Literal[True] = True
    ofz_structure_breakdown_ready: Literal[True] = True
    ofz_market_breakdown_ready: Literal[True] = True
    credit_peer_root_cause_funnel_ready: Literal[True] = True
    credit_cohort_size_analysis_ready: Literal[True] = True
    credit_identity_breakdown_ready: Literal[True] = True
    rating_publication_breakdown_ready: Literal[True] = True
    joint_chain_funnel_ready: Literal[True] = True
    decision_only_intersection_ready: Literal[True] = True
    decision_plus_outcome_intersection_ready: Literal[True] = True
    remediation_classification_ready: Literal[True] = True
    replay_ready: Literal[False] = False
    profitability_calculated: Literal[False] = False
    live_ready: Literal[False] = False


class HistoricalReplayBlockerRootCauseAuditV1(Contract):
    contract_version: Literal["historical-replay-blocker-root-cause-audit-v1"] = "historical-replay-blocker-root-cause-audit-v1"
    status: Literal["COMPLETE", "BLOCKED"]
    source_readiness_audit_sha256: str | None = None
    source_readiness_classification: str | None = None
    candidate_date_count: int = 0
    source_pit_safe_date_count: int = 0
    source_diagnostic_only_date_count: int = 0
    source_unusable_date_count: int = 0
    outcome_summary: OutcomeRootCauseSummaryV1 = Field(default_factory=OutcomeRootCauseSummaryV1)
    ofz_summary: OfzRootCauseSummaryV1 = Field(default_factory=OfzRootCauseSummaryV1)
    credit_summary: CreditPeerRootCauseSummaryV1 = Field(default_factory=CreditPeerRootCauseSummaryV1)
    joint_summary: JointChainRootCauseSummaryV1 = Field(default_factory=JointChainRootCauseSummaryV1)
    per_date: tuple[HistoricalReplayBlockerDateRcaV1, ...] = ()
    remediation_categories: tuple[RemediationEvidenceV1, ...] = ()
    blockers: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()
    audit_sha256: str | None = None
    hash_method: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = "SORTED_CANONICAL_JSON_SHA256_V1"
    DB_MUTATION: Literal[False] = False
    NETWORK_ACCESS: Literal[False] = False
    capabilities: RcaCapabilitiesV1 = Field(default_factory=RcaCapabilitiesV1)
