"""CORE M3 readiness projections, never investment decisions."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_m3_feature_view import BondM3FeatureView

UNIFIED_CANDIDATE_CONTRACT_VERSION = "unified-candidate-v1"
UNIFIED_CANDIDATE_BATCH_CONTRACT_VERSION = "unified-candidate-batch-v1"
ExclusionReason = Literal[
    "EVIDENCE_INVALID", "MARKET_NOT_FRESH", "RELATIVE_VALUE_NOT_READY",
    "CREDIT_RATING_EVIDENCE_MISSING", "CREDIT_COMPARABLE_COHORT_MISSING",
    "LIQUIDITY_SCORE_NOT_READY", "MODIFIED_DURATION_NOT_READY", "DV01_NOT_READY",
    "LIQUIDITY_RELATIVE_VALUE_NOT_READY", "BOND_NOT_FOUND",
]


class CandidateModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class UnifiedCandidateFeatures(CandidateModel):
    market_snapshot_id: int
    market_trade_date: date
    clean_price: Decimal | None
    nkd: Decimal
    yield_to_maturity_pct: Decimal
    maturity_date: date | None
    days_to_maturity: int | None
    reference_ofz_yield_pct: Decimal
    spread_to_ofz_pp: Decimal
    spread_to_ofz_bps: Decimal
    curve_trade_date: date
    interpolation_method: Literal["EXACT_NODE", "LINEAR_INTERPOLATION"]
    liquidity_score_v1: Decimal
    turnover_percentile: Decimal
    trade_count_percentile: Decimal
    recency_percentile: Decimal
    median_daily_turnover_value: Decimal
    median_daily_trade_volume: Decimal | None
    median_daily_num_trades: Decimal
    latest_liquidity_trade_date: date
    latest_liquidity_observation_age_days: int
    macaulay_duration_years: Decimal
    modified_duration_years: Decimal
    relative_price_sensitivity_per_1bp: Decimal
    dv01_currency_per_bond: Decimal
    currency_code: Literal["RUB"]
    nominal_value: Decimal
    coupon_structure: Literal["fixed"]
    coupon_frequency_per_year: int
    legal_issuer_id: int | None
    legal_issuer_source_issuer_id: str | None
    legal_issuer_inn: str | None


class UnifiedCandidateProvenance(CandidateModel):
    source_m3_snapshot_contract_version: Literal["m3-audit-snapshot-v1"]
    source_m3_composite_contract_version: Literal["bond-m3-feature-view-v1"]
    core_completeness_definition: Literal["CORE_M3_COMPLETE_V1"] = "CORE_M3_COMPLETE_V1"
    requested_as_of_date: date
    requested_market_source: Literal["moex"]
    market_snapshot_id: int
    market_trade_date: date
    curve_trade_date: date
    bond_legal_issuer_profile_id: int | None
    legal_issuer_id: int | None
    security_master_profile_id: int
    liquidity_window_start_date: date
    liquidity_selected_market_snapshot_ids: tuple[int, ...]
    maturity_source: Literal["TASK267_MARKET_METADATA_NOT_VERIFIED_STRUCTURE"] = "TASK267_MARKET_METADATA_NOT_VERIFIED_STRUCTURE"


class UnifiedCandidateCapabilities(CandidateModel):
    core_m3_candidate_ready: Literal[True] = True
    market_context_ready: Literal[True] = True
    ofz_relative_value_context_ready: Literal[True] = True
    source_native_credit_context_ready: Literal[True] = True
    liquidity_context_ready: Literal[True] = True
    duration_risk_context_ready: Literal[True] = True
    investment_model_ready: Literal[False] = False
    investment_score_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    portfolio_construction_ready: Literal[False] = False
    risk_engine_ready: Literal[False] = False
    shadow_execution_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class UnifiedCandidateView(CandidateModel):
    contract_version: Literal["unified-candidate-v1"] = UNIFIED_CANDIDATE_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: Literal["moex"]
    eligibility: Literal["CORE_M3_COMPLETE_V1"] = "CORE_M3_COMPLETE_V1"
    features: UnifiedCandidateFeatures
    provenance: UnifiedCandidateProvenance
    m3: BondM3FeatureView
    capabilities: UnifiedCandidateCapabilities = Field(default_factory=UnifiedCandidateCapabilities)
    pit_ready: Literal[False] = False


class UnifiedCandidateExclusion(CandidateModel):
    bond_id: int
    build_status: Literal["BUILT", "BOND_NOT_FOUND"]
    isin: str | None
    secid: str | None
    m3_status: Literal["CONSISTENT", "EVIDENCE_INVALID"] | None
    reasons: tuple[ExclusionReason, ...]


class UnifiedCandidateBatchView(CandidateModel):
    contract_version: Literal["unified-candidate-batch-v1"] = UNIFIED_CANDIDATE_BATCH_CONTRACT_VERSION
    source_snapshot_contract_version: Literal["m3-audit-snapshot-v1"] = "m3-audit-snapshot-v1"
    status: Literal["COMPLETE", "PARTIAL", "NO_EXISTING_BONDS"]
    as_of_date: date
    market_source: Literal["moex"]
    max_market_age_days: int
    max_curve_age_days: int
    liquidity_lookback_calendar_days: int
    liquidity_min_observation_days: int
    requested_bond_ids: tuple[int, ...]
    existing_bond_ids: tuple[int, ...]
    missing_bond_ids: tuple[int, ...]
    candidate_bond_ids: tuple[int, ...]
    excluded_bond_ids: tuple[int, ...]
    requested_bond_count: int
    existing_bond_count: int
    missing_bond_count: int
    candidate_count: int
    exclusion_count: int
    candidates: tuple[UnifiedCandidateView, ...]
    exclusions: tuple[UnifiedCandidateExclusion, ...]
    capabilities: UnifiedCandidateCapabilities = Field(default_factory=UnifiedCandidateCapabilities)
    pit_ready: Literal[False] = False
