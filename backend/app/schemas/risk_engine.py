"""Frozen constraint policy and informational risk evaluation contracts."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.investment_model import InvestmentEvaluationView, InvestmentEvaluationBatchView


class RiskContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class RiskEnginePolicyV1(RiskContract):
    contract_version: Literal["risk-engine-policy-v1"] = "risk-engine-policy-v1"
    max_position_weight: Decimal = Decimal("0.20")
    max_issuer_weight: Decimal = Decimal("0.25")
    min_liquidity_score: Decimal = Decimal("25")
    max_position_to_median_daily_turnover: Decimal = Decimal("0.05")
    max_candidate_modified_duration_years: Decimal = Decimal("5.0")
    max_invested_weighted_modified_duration_years: Decimal = Decimal("3.5")
    max_portfolio_relative_dv01_per_1bp: Decimal = Decimal("0.00040")
    max_portfolio_dv01_per_100k_rub: Decimal = Decimal("40")
    pit_ready: Literal[False] = False

    @model_validator(mode="after")
    def frozen_vector(self):
        expected = ("0.20", "0.25", "25", "0.05", "5.0", "3.5", "0.00040", "40")
        names = tuple(name for name in type(self).model_fields if name not in ("contract_version", "pit_ready"))
        if any(type(getattr(self, name)) is not Decimal or not getattr(self, name).is_finite() or
               getattr(self, name) != Decimal(value) for name, value in zip(names, expected)):
            raise ValueError("Risk Engine v1 policy is frozen")
        return self


RiskCandidateReason = Literal["INVESTMENT_MODEL_NOT_READY", "ISSUER_IDENTITY_UNAVAILABLE",
    "LIQUIDITY_SCORE_BELOW_MINIMUM", "LIQUIDITY_CAPACITY_UNAVAILABLE", "CANDIDATE_DURATION_LIMIT_EXCEEDED"]
PortfolioRiskReason = Literal["CANDIDATE_NOT_RISK_ELIGIBLE", "POSITION_WEIGHT_LIMIT_EXCEEDED",
    "POSITION_LIQUIDITY_CAPACITY_EXCEEDED", "ISSUER_WEIGHT_LIMIT_EXCEEDED",
    "PORTFOLIO_DURATION_LIMIT_EXCEEDED", "PORTFOLIO_DV01_LIMIT_EXCEEDED"]


class RiskCapabilities(RiskContract):
    candidate_risk_envelope_ready: Literal[True] = True
    position_weight_gate_ready: Literal[True] = True
    issuer_concentration_gate_ready: Literal[True] = True
    liquidity_score_gate_ready: Literal[True] = True
    liquidity_capacity_proxy_ready: Literal[True] = True
    candidate_duration_gate_ready: Literal[True] = True
    portfolio_duration_gate_ready: Literal[True] = True
    portfolio_dv01_ready: Literal[True] = True
    risk_engine_ready: Literal[True] = True
    portfolio_construction_ready: Literal[False] = False
    portfolio_optimization_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    shadow_execution_ready: Literal[False] = False
    lot_sizing_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    key_rate_duration_ready: Literal[False] = False
    convexity_ready: Literal[False] = False
    var_ready: Literal[False] = False
    expected_shortfall_ready: Literal[False] = False
    default_probability_ready: Literal[False] = False
    economic_group_concentration_ready: Literal[False] = False
    sector_concentration_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class RiskProvenance(RiskContract):
    investment_batch_contract_version: Literal["investment-evaluation-batch-v1"] = "investment-evaluation-batch-v1"
    investment_policy_version: Literal["investment-model-policy-v1"] = "investment-model-policy-v1"
    risk_policy_version: Literal["risk-engine-policy-v1"] = "risk-engine-policy-v1"
    position_capacity_version: Literal["risk-position-capacity-v1"] = "risk-position-capacity-v1"
    issuer_concentration_version: Literal["risk-issuer-concentration-v1"] = "risk-issuer-concentration-v1"
    duration_aggregation_version: Literal["risk-duration-aggregation-v1"] = "risk-duration-aggregation-v1"
    portfolio_dv01_version: Literal["risk-portfolio-dv01-v1"] = "risk-portfolio-dv01-v1"
    as_of_date: date
    market_source: Literal["moex"]
    candidate_bond_ids: tuple[int, ...]
    ready_evaluation_bond_ids: tuple[int, ...]
    proposed_bond_ids: tuple[int, ...] = ()
    legal_issuer_ids: tuple[int, ...]
    policy: RiskEnginePolicyV1 = Field(default_factory=RiskEnginePolicyV1)


class RiskCandidateView(RiskContract):
    contract_version: Literal["risk-candidate-v1"] = "risk-candidate-v1"
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: Literal["moex"]
    status: Literal["ELIGIBLE", "BLOCKED", "INVESTMENT_MODEL_NOT_READY"]
    reasons: tuple[RiskCandidateReason, ...]
    legal_issuer_id: int | None
    legal_issuer_source_issuer_id: str | None
    legal_issuer_inn: str | None
    investment_score_v1: Decimal | None
    liquidity_score_v1: Decimal
    median_daily_turnover_value: Decimal
    modified_duration_years: Decimal
    relative_price_sensitivity_per_1bp: Decimal
    dv01_currency_per_bond: Decimal
    policy_max_position_weight: Decimal
    liquidity_capacity_amount_rub: Decimal
    liquidity_capacity_weight: Decimal | None = None
    max_admissible_position_weight: Decimal | None = None
    source_evaluation: InvestmentEvaluationView
    capabilities: RiskCapabilities = Field(default_factory=RiskCapabilities)
    pit_ready: Literal[False] = False


class RiskCandidateBatchView(RiskContract):
    contract_version: Literal["risk-candidate-batch-v1"] = "risk-candidate-batch-v1"
    as_of_date: date
    market_source: Literal["moex"]
    policy: RiskEnginePolicyV1 = Field(default_factory=RiskEnginePolicyV1)
    candidate_bond_ids: tuple[int, ...]
    candidate_count: int
    eligible_count: int
    blocked_count: int
    investment_not_ready_count: int
    candidates: tuple[RiskCandidateView, ...]
    source_batch: InvestmentEvaluationBatchView
    provenance: RiskProvenance
    capabilities: RiskCapabilities = Field(default_factory=RiskCapabilities)
    pit_ready: Literal[False] = False


class ProposedRiskPosition(RiskContract):
    bond_id: int
    target_weight: Decimal


class ProposedRiskPortfolio(RiskContract):
    contract_version: Literal["proposed-risk-portfolio-v1"] = "proposed-risk-portfolio-v1"
    capital_rub: Decimal
    positions: tuple[ProposedRiskPosition, ...]
    pit_ready: Literal[False] = False


class RiskPositionResult(RiskContract):
    bond_id: int
    legal_issuer_id: int | None
    requested_weight: Decimal
    position_amount_rub: Decimal
    position_dv01_rub_per_1bp: Decimal
    policy_max_position_weight: Decimal
    liquidity_capacity_amount_rub: Decimal
    liquidity_capacity_weight: Decimal
    capacity_bound: Decimal
    max_admissible_position_weight: Decimal
    status: Literal["PASS", "BLOCKED"]
    reasons: tuple[PortfolioRiskReason, ...]
    candidate: RiskCandidateView


class RiskIssuerConcentration(RiskContract):
    legal_issuer_id: int
    bond_ids: tuple[int, ...]
    total_weight: Decimal
    limit: Decimal
    headroom: Decimal
    status: Literal["PASS", "BLOCKED"]


class PortfolioRiskMetrics(RiskContract):
    capital_rub: Decimal
    invested_weight: Decimal
    cash_weight: Decimal
    invested_capital_rub: Decimal
    cash_rub: Decimal
    position_count: int
    issuer_count: int
    issuer_concentration_complete: bool
    ungrouped_bond_ids: tuple[int, ...]
    max_position_weight_observed: Decimal
    max_issuer_weight_observed: Decimal
    invested_weighted_modified_duration_years: Decimal | None
    capital_weighted_modified_duration_years: Decimal
    portfolio_dv01_rub_per_1bp: Decimal
    portfolio_relative_dv01_per_1bp: Decimal
    portfolio_dv01_per_100k_rub: Decimal


class PortfolioRiskEvaluationView(RiskContract):
    contract_version: Literal["portfolio-risk-evaluation-v1"] = "portfolio-risk-evaluation-v1"
    as_of_date: date
    market_source: Literal["moex"]
    status: Literal["PASS", "BLOCKED"]
    reasons: tuple[PortfolioRiskReason, ...]
    quality_flags: tuple[Literal["EMPTY_PORTFOLIO", "ISSUER_CONCENTRATION_INCOMPLETE"], ...]
    policy: RiskEnginePolicyV1 = Field(default_factory=RiskEnginePolicyV1)
    proposal: ProposedRiskPortfolio
    positions: tuple[RiskPositionResult, ...]
    issuer_concentrations: tuple[RiskIssuerConcentration, ...]
    metrics: PortfolioRiskMetrics
    source_risk_batch: RiskCandidateBatchView
    provenance: RiskProvenance
    capabilities: RiskCapabilities = Field(default_factory=RiskCapabilities)
    pit_ready: Literal[False] = False
