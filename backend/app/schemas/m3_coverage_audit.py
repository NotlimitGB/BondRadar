"""Immutable contracts for the pure Task283 M3 coverage audit."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


M3_COVERAGE_AUDIT_CONTRACT_VERSION = "m3-coverage-audit-v1"
M3CoverageBottleneckKey = Literal[
    "MARKET_NOT_FRESH",
    "RELATIVE_VALUE_NOT_READY",
    "CREDIT_RATING_EVIDENCE_MISSING",
    "CREDIT_COMPARABLE_COHORT_MISSING",
    "LIQUIDITY_SCORE_NOT_READY",
    "MODIFIED_DURATION_NOT_READY",
    "DV01_NOT_READY",
    "LIQUIDITY_RELATIVE_VALUE_NOT_READY",
    "COMPOSITE_EVIDENCE_INVALID",
]


class M3CoverageAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class M3FeatureCoverage(M3CoverageAuditModel):
    available_count: int
    missing_count: int
    coverage_pct: Decimal
    available_bond_ids: list[int]
    missing_bond_ids: list[int]


class M3FeatureCoverageSummary(M3CoverageAuditModel):
    market_fresh: M3FeatureCoverage
    relative_value_ready: M3FeatureCoverage
    has_credit_rating_evidence: M3FeatureCoverage
    has_credit_comparable_cohort: M3FeatureCoverage
    liquidity_score_ready: M3FeatureCoverage
    modified_duration_ready: M3FeatureCoverage
    dv01_ready: M3FeatureCoverage
    liquidity_relative_value_ready: M3FeatureCoverage
    has_peer_distribution_context: M3FeatureCoverage
    peer_distribution_ready: M3FeatureCoverage
    all_supplied_evidence_consistent: M3FeatureCoverage


class M3CoverageBottleneck(M3CoverageAuditModel):
    key: M3CoverageBottleneckKey
    missing_count: int
    missing_pct: Decimal
    affected_bond_ids: list[int]


class M3StatusBreakdownEntry(M3CoverageAuditModel):
    status: str
    count: int
    pct: Decimal
    bond_ids: list[int]


class M3StatusBreakdowns(M3CoverageAuditModel):
    market: list[M3StatusBreakdownEntry]
    relative_value: list[M3StatusBreakdownEntry]
    liquidity: list[M3StatusBreakdownEntry]
    modified_duration: list[M3StatusBreakdownEntry]
    dv01: list[M3StatusBreakdownEntry]
    liquidity_relative_value: list[M3StatusBreakdownEntry]
    peer_distribution: list[M3StatusBreakdownEntry]


class M3CoverageAuditProvenance(M3CoverageAuditModel):
    source_contract_version: Literal["bond-m3-feature-view-v1"] = (
        "bond-m3-feature-view-v1"
    )
    as_of_date: date
    market_source: str
    universe_size: int
    bond_ids: list[int]
    core_completeness_definition: Literal["CORE_M3_COMPLETE_V1"] = (
        "CORE_M3_COMPLETE_V1"
    )
    extended_completeness_definition: Literal["EXTENDED_M3_COMPLETE_V1"] = (
        "EXTENDED_M3_COMPLETE_V1"
    )
    percentage_method: Literal["DECIMAL_EXACT_RATIO_V1"] = (
        "DECIMAL_EXACT_RATIO_V1"
    )


class M3CoverageAuditCapabilities(M3CoverageAuditModel):
    task282_composite_input_ready: Literal[True] = True
    m3_coverage_audit_ready: Literal[True] = True
    feature_coverage_ready: Literal[True] = True
    feature_status_breakdown_ready: Literal[True] = True
    missingness_bottlenecks_ready: Literal[True] = True
    core_m3_completeness_ready: Literal[True] = True
    extended_m3_completeness_ready: Literal[True] = True
    actual_db_audit_runner_ready: Literal[False] = False
    automatic_universe_discovery_ready: Literal[False] = False
    m4_readiness_decision_ready: Literal[False] = False
    investment_score_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class M3CoverageAuditView(M3CoverageAuditModel):
    contract_version: Literal["m3-coverage-audit-v1"] = (
        M3_COVERAGE_AUDIT_CONTRACT_VERSION
    )
    as_of_date: date
    market_source: str
    universe_size: int
    bond_ids: list[int]
    consistent_composite_count: int
    invalid_composite_count: int
    consistent_composite_pct: Decimal
    invalid_composite_pct: Decimal
    feature_coverage: M3FeatureCoverageSummary
    peer_context_count: int
    peer_context_pct: Decimal
    peer_distribution_ready_count: int
    peer_distribution_ready_pct: Decimal
    core_m3_complete_count: int
    core_m3_complete_pct: Decimal
    core_m3_complete_bond_ids: list[int]
    core_m3_incomplete_bond_ids: list[int]
    extended_m3_complete_count: int
    extended_m3_complete_pct: Decimal
    bottlenecks: list[M3CoverageBottleneck]
    status_breakdowns: M3StatusBreakdowns
    provenance: M3CoverageAuditProvenance
    capabilities: M3CoverageAuditCapabilities = Field(
        default_factory=M3CoverageAuditCapabilities
    )
    pit_ready: Literal[False] = False
