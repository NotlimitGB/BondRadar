"""Read-only duration-matched OFZ reference and yield-spread contracts."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OFZ_CURVE_CONTRACT_VERSION = "ofz-reference-curve-v1"
RELATIVE_VALUE_CONTRACT_VERSION = "bond-relative-value-v1"

CurveStatus = Literal[
    "READY", "NO_ELIGIBLE_OFZ", "NO_FRESH_MARKET_DATA",
    "INSUFFICIENT_DISTINCT_DURATIONS",
]
RelativeValueStatus = Literal[
    "READY", "TARGET_MARKET_MISSING", "TARGET_MARKET_STALE",
    "TARGET_YIELD_MISSING", "TARGET_DURATION_MISSING", "CURVE_NOT_READY",
    "TARGET_CURVE_DATE_MISMATCH", "TARGET_DURATION_OUTSIDE_CURVE",
]


class CurveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OfzCurveNode(CurveModel):
    duration_years: Decimal
    yield_to_maturity_pct: Decimal
    aggregation_method: Literal["SINGLE", "MEDIAN"]
    component_bond_ids: list[int]
    component_snapshot_ids: list[int]
    component_secids: list[str | None]
    component_isins: list[str | None]
    component_yields_pct: list[Decimal]


class OfzCurveDiagnostics(CurveModel):
    ofz_identity_count: int = 0
    security_master_eligible_count: int = 0
    with_market_snapshot_count: int = 0
    with_valid_yield_duration_count: int = 0
    fresh_market_count: int = 0
    curve_date_member_count: int = 0
    distinct_duration_node_count: int = 0
    excluded_security_master_count: int = 0
    excluded_missing_snapshot_count: int = 0
    excluded_stale_count: int = 0
    excluded_curve_date_mismatch_count: int = 0
    excluded_missing_yield_count: int = 0
    excluded_invalid_yield_count: int = 0
    excluded_missing_duration_count: int = 0
    excluded_invalid_duration_count: int = 0
    excluded_nonpositive_duration_count: int = 0


class OfzReferenceCurveView(CurveModel):
    contract_version: Literal["ofz-reference-curve-v1"] = OFZ_CURVE_CONTRACT_VERSION
    as_of_date: date
    market_source: str
    status: CurveStatus
    curve_trade_date: date | None
    node_count: int
    min_duration_years: Decimal | None
    max_duration_years: Decimal | None
    nodes: list[OfzCurveNode]
    diagnostics: OfzCurveDiagnostics
    pit_ready: Literal[False] = False


class BondRelativeValueProvenance(CurveModel):
    target_market_snapshot_id: int | None
    target_market_trade_date: date | None
    curve_trade_date: date | None
    lower_curve_duration_years: Decimal | None = None
    lower_curve_yield_pct: Decimal | None = None
    lower_component_snapshot_ids: list[int] = Field(default_factory=list)
    upper_curve_duration_years: Decimal | None = None
    upper_curve_yield_pct: Decimal | None = None
    upper_component_snapshot_ids: list[int] = Field(default_factory=list)


class BondRelativeValueView(CurveModel):
    contract_version: Literal["bond-relative-value-v1"] = RELATIVE_VALUE_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: str
    status: RelativeValueStatus
    target_yield_to_maturity_pct: Decimal | None
    target_duration_years: Decimal | None
    reference_ofz_yield_pct: Decimal | None
    spread_to_ofz_pp: Decimal | None
    spread_to_ofz_bps: Decimal | None
    interpolation_method: Literal["EXACT_NODE", "LINEAR_INTERPOLATION"] | None
    curve: OfzReferenceCurveView
    provenance: BondRelativeValueProvenance
    quality_flags: list[str]
    pit_ready: Literal[False] = False
