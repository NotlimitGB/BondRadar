"""Read-only, source-bound diagnostics; never an execution authorization."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.tinvest_frozen_admission_evidence import FrozenAdmissionEvidence
from app.schemas.tinvest_bond_import_preflight import TInvestBondImportPreflightView
from app.schemas.shadow_ledger import ShadowGenesisPlanV1
from app.schemas.portfolio_strategy import PortfolioStrategySummary
from app.schemas.shadow_execution import ShadowExecutionSummary
from app.schemas.risk_engine import PortfolioRiskMetrics
from app.schemas.shadow_scale_experiment import CAPITAL_GRID_RUB

SHA = r"^[0-9a-f]{64}$"
FactState = Literal["SOURCE_FACT_VERIFIED", "SOURCE_FACT_UNKNOWN", "SOURCE_FACT_CONFLICT", "SOURCE_FACT_UNAVAILABLE"]
Status = Literal["READY", "BLOCKED", "NOT_EVALUATED", "INPUTS_MISSING", "SOURCE_STALE", "SOURCE_CONFLICT", "EMPTY", "UNEXECUTABLE"]
Decision = Literal["YES", "NO", "UNKNOWN"]
TRADABILITY_POLICY = "FRESH_BROKER_API_POSITIVE_WITH_EXPLICIT_INACTIVE_VETO_V1"
DECISION_NAMES = ("CURRENT_UNIVERSE_BOUND", "CURRENT_CORE_READY", "INVESTMENT_MODEL_EVALUABLE",
    "RISK_ELIGIBLE_SUBSET_AVAILABLE", "STRATEGY_50K_NONEMPTY", "SHADOW_50K_EXECUTABLE",
    "SHADOW_50K_POST_RISK_PASS", "SEVEN_CAPITAL_CASES_EVALUATED", "SEVEN_CAPITAL_CASES_READY",
    "OFZ_BENCHMARK_EVALUABLE", "OPERATIONAL_EVIDENCE_SUFFICIENT", "FORWARD_GENESIS_INPUTS_READY")


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    pit_ready: Literal[False] = False

    @field_validator("pit_ready", "db_mutation", "official_day0_ready", "official_day0_started",
        "p0_01_closed", "p0_02_closed", "p0_03_closed", "live_source_requests", "broker_orders",
        "experiment_activation", "source_max_age_seconds", "max_market_age_days", "max_curve_age_days",
        "liquidity_lookback_calendar_days", "liquidity_min_observation_days", "diagnostic_scope",
        "implementation_available", "fractional_reference", "operator_action_required", mode="before", check_fields=False)
    @classmethod
    def original_literal_types(cls, value, info):
        field = cls.model_fields[info.field_name]
        if get_origin(field.annotation) is Literal and type(value) not in {type(v) for v in get_args(field.annotation)}:
            raise ValueError("EXACT_LITERAL_TYPE_REQUIRED")
        return value

    @field_validator("pit_ready", mode="before")
    @classmethod
    def false_only(cls, value):
        if value is not False:
            raise ValueError("FALSE_REQUIRED")
        return value

    @model_validator(mode="after")
    def literal_types(self):
        for name, field in type(self).model_fields.items():
            if get_origin(field.annotation) is Literal:
                if type(getattr(self, name)) not in {type(v) for v in get_args(field.annotation)}:
                    raise ValueError("EXACT_LITERAL_TYPE_REQUIRED")
        return self


def utc(value):
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC_TIMESTAMP_REQUIRED")
    return value


class AccountEvidence(Contract):
    """Operator-supplied frozen facts, not a signed broker attestation."""
    uid: str = Field(min_length=1, max_length=256)
    observed_at: datetime
    permitted: bool | None
    passed_tests: tuple[str, ...] = ()
    evidence_sha256: str = Field(pattern=SHA)

    _utc = field_validator("observed_at")(utc)


class SourceContext(Contract):
    collection_id: str = Field(min_length=1, max_length=256)
    observed_at: datetime | None
    method: Literal["Bonds"] = "Bonds"
    instrument_status: Literal["INSTRUMENT_STATUS_BASE"] = "INSTRUMENT_STATUS_BASE"
    frozen_admission: FrozenAdmissionEvidence | None = None
    expected_artifact_sha256: str | None = Field(default=None, pattern=SHA)
    expected_source_bonds_sha256: str | None = Field(default=None, pattern=SHA)
    preflight: TInvestBondImportPreflightView | None = None
    accounts: tuple[AccountEvidence, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        return utc(value) if value is not None else None


class ReviewedGenesis(Contract):
    capital_rub: Decimal
    plan: ShadowGenesisPlanV1


class CurrentForwardReadinessRequest(Contract):
    contract_version: Literal["current-forward-readiness-request-v1"] = "current-forward-readiness-request-v1"
    as_of_date: date
    evaluation_at: datetime
    implementation_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    bond_ids: tuple[int, ...]
    source_context: SourceContext | None = None
    reviewed_genesis: tuple[ReviewedGenesis, ...] = ()
    market_source: Literal["moex"] = "moex"
    max_market_age_days: Literal[7] = 7
    max_curve_age_days: Literal[7] = 7
    liquidity_lookback_calendar_days: Literal[30] = 30
    liquidity_min_observation_days: Literal[5] = 5
    source_max_age_seconds: Literal[86400] = 86400

    _utc = field_validator("evaluation_at")(utc)

    @model_validator(mode="after")
    def request_context(self):
        if self.evaluation_at.date() != self.as_of_date:
            raise ValueError("EVALUATION_DATE_MISMATCH")
        if not self.bond_ids or len(set(self.bond_ids)) != len(self.bond_ids) or any(type(i) is not int or i <= 0 for i in self.bond_ids):
            raise ValueError("EXACT_UNIQUE_POSITIVE_IDS_REQUIRED")
        self.as_of_date - timedelta(days=29)
        for name in ("max_market_age_days", "max_curve_age_days", "liquidity_lookback_calendar_days", "liquidity_min_observation_days", "source_max_age_seconds"):
            if type(getattr(self, name)) is not int:
                raise ValueError("EXACT_POLICY_TYPE_REQUIRED")
        return self


class Fingerprint(Contract):
    name: str
    sha256: str = Field(pattern=SHA)


class Count(Contract):
    name: str
    count: int | None = Field(ge=0)


class Stage(Contract):
    name: str
    unit: Literal["UID", "BOND_ID"] = "BOND_ID"
    capital_rub: Decimal | None = None
    evaluation_status: Literal["EVALUATED", "NOT_EVALUATED"] = "EVALUATED"
    input_ids: tuple[str, ...]
    passing_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    input_count: int
    passing_count: int
    excluded_count: int
    unknown_count: int
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partition(self):
        groups = (self.passing_ids, self.excluded_ids, self.unknown_ids)
        if (any(len(set(g)) != len(g) for g in (self.input_ids, *groups)) or
                len(set().union(*map(set, groups))) != sum(map(len, groups)) or
                set().union(*map(set, groups)) != set(self.input_ids) or
                (self.input_count, self.passing_count, self.excluded_count, self.unknown_count) != tuple(map(len, (self.input_ids, *groups)))):
            raise ValueError("FUNNEL_PARTITION_INVALID")
        return self


class SourceFinding(Contract):
    uid: str
    bond_id: int | None
    figi: str | None
    isin: str | None
    secid: str | None
    instrument_type: Literal["TINVEST_BASE_BOND"] = "TINVEST_BASE_BOND"
    currency: str | None
    class_code: str | None
    lot: int | None
    admission_status: str | None
    canonical_identity_status: FactState
    availability_status: FactState
    account_status: FactState
    source_observed_at: datetime | None
    buy_available: bool | None
    api_trade_available: bool | None
    for_qual_investor: bool | None
    required_tests: tuple[str, ...] | None
    required_tests_state: str
    active_status: FactState = "SOURCE_FACT_UNKNOWN"
    tradability_status: FactState
    tradability_basis: Literal["FRESH_BROKER_API_POSITIVE", "EXPLICIT_INACTIVE_VETO", "SOURCE_CONFLICT", "NONE"]
    source_eligible: bool
    reason_codes: tuple[str, ...]

    @field_validator("source_observed_at")
    @classmethod
    def observation_timestamp(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def eligibility_authority(self):
        if self.source_eligible and not (
            self.tradability_status == "SOURCE_FACT_VERIFIED" and self.tradability_basis == "FRESH_BROKER_API_POSITIVE"
            and self.api_trade_available is True and self.buy_available is True and self.for_qual_investor is False
            and self.active_status not in ("SOURCE_FACT_CONFLICT", "SOURCE_FACT_UNAVAILABLE")
            and self.canonical_identity_status == self.availability_status == self.account_status == "SOURCE_FACT_VERIFIED"
            and self.source_observed_at is not None and self.class_code == "TQCB" and not self.reason_codes):
            raise ValueError("SOURCE_ELIGIBILITY_AUTHORITY_INVALID")
        return self


class CandidateFinding(Contract):
    bond_id: int
    isin: str | None
    secid: str | None
    source_uids: tuple[str, ...]
    eligible_witness_uids: tuple[str, ...]
    core_ready: bool
    investment_status: str | None
    rank: int | None
    investment_score: Decimal | None
    risk_status: str | None
    legal_issuer_id: int | None
    issuer_profile_id: int | None
    security_master_profile_id: int | None
    market_snapshot_id: int | None
    market_trade_date: date | None
    curve_trade_date: date | None
    nominal_value: Decimal | None
    currency: str | None
    liquidity_score: Decimal | None
    median_daily_turnover: Decimal | None
    modified_duration: Decimal | None
    relative_dv01: Decimal | None
    ready_peer_context_count: int | None
    reason_codes: tuple[str, ...]
    source_evaluation_sha256: str | None


class PositionFinding(Contract):
    bond_id: int
    target_weight: Decimal
    target_amount_rub: Decimal
    terms_status: str
    lot_size: int | None
    trading_board: str | None
    dirty_value: Decimal
    lot_dirty_value: Decimal | None
    planned_lots: int
    planned_quantity: int
    cash_cost_rub: Decimal
    residual_target_rub: Decimal
    realized_weight: Decimal
    market_snapshot_id: int
    security_master_profile_id: int | None
    status: str
    seed_below_one_lot: bool | None
    blockers: tuple[str, ...]


class StrategyTarget(Contract):
    bond_id: int
    legal_issuer_id: int
    target_weight: Decimal
    target_amount_rub: Decimal


class Representative(Contract):
    bond_id: int
    snapshot_id: int
    modified_duration: Decimal


class BenchmarkFinding(Contract):
    implementation_available: Literal[True] = True
    status: Literal["READY", "BLOCKED", "NOT_EVALUATED"] = "NOT_EVALUATED"
    production_ready: Decision = "UNKNOWN"
    fractional_reference: Literal[True] = True
    common_market_trade_date: date | None = None
    representatives: tuple[Representative, ...] = ()
    selected_bond_ids: tuple[int, ...] = ()
    matching_mode: str | None = None
    benchmark_genesis_sha256: str | None = None
    blockers: tuple[str, ...] = ("REVIEWED_GENESIS_MISSING",)


class CapitalCase(Contract):
    capital_rub: Decimal
    status: Status = "NOT_EVALUATED"
    diagnostic_scope: Literal[True] = True
    strategy_status: str | None = None
    execution_status: str | None = None
    post_rounding_risk_status: str | None = None
    strategy_summary: PortfolioStrategySummary | None = None
    execution_summary: ShadowExecutionSummary | None = None
    risk_metrics: PortfolioRiskMetrics | None = None
    issuer_count: int | None = None
    selected_bond_ids: tuple[int, ...] = ()
    strategy_targets: tuple[StrategyTarget, ...] = ()
    positions: tuple[PositionFinding, ...] = ()
    benchmark: BenchmarkFinding = Field(default_factory=BenchmarkFinding)
    source_evidence_sha256: str | None = None
    plan_sha256: str | None = None
    blockers: tuple[str, ...] = ()


class Remediation(Contract):
    reason_code: str
    affected_bond_ids: tuple[int, ...]
    source_family: str
    classification: Literal["REPAIRABLE_INPUT_COVERAGE_GAP", "POLICY_BLOCKED_AS_DESIGNED", "NO_SOURCE_AUTHORITY_AVAILABLE"]
    missing_or_conflicting_evidence: str
    dependency: str
    minimum_safe_remediation: str
    operator_action_required: Literal[True] = True


class ReadinessDecision(Contract):
    name: str
    value: Decision
    reason_codes: tuple[str, ...] = ()


class CurrentForwardReadinessReport(Contract):
    contract_version: Literal["current-forward-readiness-report-v1"] = "current-forward-readiness-report-v1"
    tradability_policy: Literal["FRESH_BROKER_API_POSITIVE_WITH_EXPLICIT_INACTIVE_VETO_V1"] = TRADABILITY_POLICY
    status: Status
    as_of_date: date | None = None
    evaluation_at: datetime | None = None
    implementation_sha: str | None = None
    input_fingerprints: tuple[Fingerprint, ...] = ()
    source_collection_id: str | None = None
    source_observed_at: datetime | None = None
    source_contract_version: str | None = None
    admission_manifest_sha256: str | None = None
    input_counts: tuple[Count, ...] = ()
    funnel_stages: tuple[Stage, ...] = ()
    source_findings: tuple[SourceFinding, ...] = ()
    candidate_findings: tuple[CandidateFinding, ...] = ()
    capital_cases: tuple[CapitalCase, ...]
    independent_family_counts: tuple[Count, ...] = ()
    joint_source_core_bond_ids: tuple[int, ...] = ()
    readiness_decisions: tuple[ReadinessDecision, ...]
    recommended_remediations: tuple[Remediation, ...] = ()
    blockers: tuple[str, ...] = ()
    production_status: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    p0_01_closed: Literal[False] = False
    p0_02_closed: Literal[False] = False
    p0_03_closed: Literal[False] = False
    official_day0_ready: Literal[False] = False
    official_day0_started: Literal[False] = False
    db_mutation: Literal[False] = False
    live_source_requests: Literal[0] = 0
    broker_orders: Literal[False] = False
    experiment_activation: Literal[False] = False
    report_sha256: str = ""

    @model_validator(mode="after")
    def complete_envelope(self):
        if tuple(c.capital_rub for c in self.capital_cases) != CAPITAL_GRID_RUB:
            raise ValueError("CAPITAL_CASE_ORDER_CONFLICT")
        if tuple(d.name for d in self.readiness_decisions) != DECISION_NAMES:
            raise ValueError("READINESS_DECISION_SET_INVALID")
        if self.status == "READY":
            if self.blockers or self.evaluation_at is None or any(
                    row.tradability_status == "SOURCE_FACT_CONFLICT" for row in self.source_findings):
                raise ValueError("READY_SOURCE_AUTHORITY_INVALID")
            for row in self.source_findings:
                SourceFinding.model_validate(row.model_dump(warnings=False))
            for case in self.capital_cases:
                if (case.status != "READY" or case.execution_status != "READY" or case.post_rounding_risk_status != "PASS"
                        or case.blockers or not case.positions or case.benchmark.status != "READY"
                        or case.selected_bond_ids != tuple(p.bond_id for p in case.positions)
                        or len(set(case.selected_bond_ids)) != len(case.selected_bond_ids)):
                    raise ValueError("READY_CASE_INCOMPLETE")
                for position in case.positions:
                    if (position.status != "PLANNED" or position.terms_status != "READY" or position.blockers
                            or position.planned_lots <= 0 or position.planned_quantity <= 0 or position.trading_board != "TQCB"):
                        raise ValueError("READY_POSITION_INCOMPLETE")
                    witnesses = tuple(row for row in self.source_findings if row.bond_id == position.bond_id and row.source_eligible)
                    if not any(row.source_observed_at is not None and timedelta(0) <= self.evaluation_at-row.source_observed_at <= timedelta(seconds=86400)
                               for row in witnesses):
                        raise ValueError("READY_SOURCE_WITNESS_MISSING_OR_STALE")
        return self
