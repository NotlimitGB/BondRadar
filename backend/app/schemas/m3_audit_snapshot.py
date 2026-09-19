"""Immutable contract for an explicit-universe M3 audit snapshot."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_m3_feature_view import BondM3FeatureView
from app.schemas.m3_coverage_audit import M3CoverageAuditView
from app.schemas.ofz_reference_curve import CurveStatus


M3_AUDIT_SNAPSHOT_CONTRACT_VERSION = "m3-audit-snapshot-v1"
M3AuditSnapshotStatus = Literal["COMPLETE", "PARTIAL", "NO_EXISTING_BONDS"]
M3AuditSnapshotItemStatus = Literal["BUILT", "BOND_NOT_FOUND"]


class M3AuditSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class M3AuditSnapshotItem(M3AuditSnapshotModel):
    bond_id: int
    build_status: M3AuditSnapshotItemStatus
    composite: BondM3FeatureView | None


class M3AuditSnapshotDiagnostics(M3AuditSnapshotModel):
    liquidity_batch_call_count: int
    liquidity_market_window_query_count: int
    liquidity_universe_evaluation_count: int
    ofz_curve_build_count: int
    market_feature_call_count: int
    relative_evaluator_call_count: int
    credit_feature_call_count: int
    credit_comparability_call_count: int
    modified_duration_call_count: int
    dv01_call_count: int
    liquidity_relative_value_composer_call_count: int
    m3_composer_call_count: int
    coverage_reducer_call_count: int


class M3AuditSnapshotProvenance(M3AuditSnapshotModel):
    task285_batch_contract_version: Literal["bond-liquidity-batch-feature-v1"] = (
        "bond-liquidity-batch-feature-v1"
    )
    task282_contract_version: Literal["bond-m3-feature-view-v1"] = (
        "bond-m3-feature-view-v1"
    )
    task283_audit_contract_version: Literal["m3-coverage-audit-v1"] = (
        "m3-coverage-audit-v1"
    )
    shared_ofz_curve_contract_version: str | None
    shared_ofz_curve_status: CurveStatus | None
    shared_ofz_curve_trade_date: date | None
    shared_ofz_curve_node_count: int | None
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    audit_denominator: Literal["EXISTING_REQUESTED_BONDS"] = (
        "EXISTING_REQUESTED_BONDS"
    )
    peer_context_mode: Literal["NOT_BUILT"] = "NOT_BUILT"


class M3AuditSnapshotCapabilities(M3AuditSnapshotModel):
    explicit_audit_universe_ready: Literal[True] = True
    shared_ofz_curve_ready: Literal[True] = True
    shared_liquidity_batch_ready: Literal[True] = True
    task282_composite_snapshot_ready: Literal[True] = True
    task283_coverage_audit_ready: Literal[True] = True
    core_m3_audit_ready: Literal[True] = True
    peer_context_built: Literal[False] = False
    extended_m3_audit_interpretation_ready: Literal[False] = False
    automatic_investment_universe_discovery_ready: Literal[False] = False
    m4_readiness_decision_ready: Literal[False] = False
    investment_score_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class M3AuditSnapshotView(M3AuditSnapshotModel):
    contract_version: Literal["m3-audit-snapshot-v1"] = (
        M3_AUDIT_SNAPSHOT_CONTRACT_VERSION
    )
    as_of_date: date
    market_source: str
    max_market_age_days: int
    max_curve_age_days: int
    liquidity_lookback_calendar_days: int
    liquidity_min_observation_days: int
    status: M3AuditSnapshotStatus
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    requested_bond_count: int
    existing_bond_count: int
    missing_bond_count: int
    composite_count: int
    consistent_composite_count: int
    invalid_composite_count: int
    items: list[M3AuditSnapshotItem]
    coverage_audit: M3CoverageAuditView | None
    diagnostics: M3AuditSnapshotDiagnostics
    provenance: M3AuditSnapshotProvenance
    capabilities: M3AuditSnapshotCapabilities = Field(
        default_factory=M3AuditSnapshotCapabilities
    )
    pit_ready: Literal[False] = False
