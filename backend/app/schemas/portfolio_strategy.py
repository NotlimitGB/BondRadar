"""Immutable informational target allocations; never executable orders."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.investment_model import InvestmentEvaluationBatchView, InvestmentEvaluationView
from app.schemas.risk_engine import RiskCandidateBatchView, RiskCandidateView, PortfolioRiskEvaluationView, PortfolioRiskReason, RiskCandidateReason


class StrategyContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PortfolioStrategyPolicyV1(StrategyContract):
    contract_version: Literal["portfolio-strategy-policy-v1"] = "portfolio-strategy-policy-v1"
    target_invested_weight: Decimal = Decimal("1.00")
    max_positions: int = 10
    min_initial_position_weight: Decimal = Decimal("0.05")
    allocation_increment: Decimal = Decimal("0.01")
    pit_ready: Literal[False] = False

    @model_validator(mode="after")
    def frozen_vector(self):
        for name, expected in (("target_invested_weight", "1.00"), ("min_initial_position_weight", "0.05"), ("allocation_increment", "0.01")):
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite() or value != Decimal(expected):
                raise ValueError("Portfolio Strategy v1 policy is frozen")
        if type(self.max_positions) is not int or self.max_positions != 10:
            raise ValueError("Portfolio Strategy v1 policy is frozen")
        return self


class PortfolioStrategyRequest(StrategyContract):
    capital_rub: Decimal

    @model_validator(mode="after")
    def valid_capital(self):
        if type(self.capital_rub) is not Decimal or not self.capital_rub.is_finite() or self.capital_rub <= 0:
            raise ValueError("Positive finite Decimal capital required")
        return self


class StrategyCapabilities(StrategyContract):
    portfolio_strategy_ready: Literal[True] = True
    portfolio_construction_ready: Literal[True] = True
    deterministic_selection_ready: Literal[True] = True
    risk_constrained_allocation_ready: Literal[True] = True
    target_weights_ready: Literal[True] = True
    executable_quantities_ready: Literal[False] = False
    lot_sizing_ready: Literal[False] = False
    rebalance_delta_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False
    shadow_execution_ready: Literal[False] = False
    realized_performance_ready: Literal[False] = False
    benchmark_comparison_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class PortfolioStrategyPosition(StrategyContract):
    bond_id: int
    isin: str | None
    secid: str | None
    investment_rank: int
    investment_score_v1: Decimal
    legal_issuer_id: int
    target_weight: Decimal
    target_amount_rub: Decimal
    initial_seed_weight: Decimal
    accepted_top_up_count: int
    liquidity_capacity_amount_rub: Decimal
    modified_duration_years: Decimal
    relative_price_sensitivity_per_1bp: Decimal
    position_dv01_rub_per_1bp: Decimal
    source_evaluation: InvestmentEvaluationView
    source_risk_candidate: RiskCandidateView


NonSelectionReason = Literal["INVESTMENT_MODEL_NOT_READY", "RISK_CANDIDATE_BLOCKED", "MIN_INITIAL_POSITION_NOT_ADMISSIBLE", "MAX_POSITIONS_REACHED", "TARGET_INVESTED_WEIGHT_REACHED", "NOT_SELECTED_AFTER_CONVERGENCE"]


class PortfolioStrategyNonSelection(StrategyContract):
    bond_id: int
    investment_rank: int | None
    investment_status: str
    risk_candidate_status: str
    reason: NonSelectionReason
    risk_reasons: tuple[RiskCandidateReason, ...]
    final_attempt_risk_reasons: tuple[PortfolioRiskReason, ...]


class PortfolioStrategyAllocationAttempt(StrategyContract):
    sequence_number: int
    round_number: int
    phase: Literal["SEED", "TOP_UP"]
    bond_id: int
    investment_rank: int
    previous_weight: Decimal
    attempted_weight: Decimal
    previous_invested_weight: Decimal
    attempted_invested_weight: Decimal
    accepted: bool
    risk_status: Literal["PASS", "BLOCKED"]
    risk_reasons: tuple[PortfolioRiskReason, ...]


class PortfolioStrategySummary(StrategyContract):
    capital_rub: Decimal
    candidate_count: int
    investment_ready_count: int
    risk_eligible_count: int
    selected_count: int
    non_selected_count: int
    invested_weight: Decimal
    cash_weight: Decimal
    invested_capital_rub: Decimal
    cash_rub: Decimal
    allocation_attempt_count: int
    accepted_attempt_count: int
    rejected_attempt_count: int
    round_count: int
    final_max_position_weight: Decimal
    final_max_issuer_weight: Decimal
    final_invested_weighted_modified_duration_years: Decimal | None
    final_portfolio_relative_dv01_per_1bp: Decimal
    final_portfolio_dv01_per_100k_rub: Decimal


class PortfolioStrategyProvenance(StrategyContract):
    investment_batch_contract_version: Literal["investment-evaluation-batch-v1"] = "investment-evaluation-batch-v1"
    investment_policy_version: Literal["investment-model-policy-v1"] = "investment-model-policy-v1"
    risk_policy_version: Literal["risk-engine-policy-v1"] = "risk-engine-policy-v1"
    strategy_policy_version: Literal["portfolio-strategy-policy-v1"] = "portfolio-strategy-policy-v1"
    allocation_algorithm_version: Literal["RANK_ORDERED_PROGRESSIVE_ALLOCATION_V1"] = "RANK_ORDERED_PROGRESSIVE_ALLOCATION_V1"
    candidate_bond_ids: tuple[int, ...]
    ranked_bond_ids: tuple[int, ...]
    risk_eligible_bond_ids: tuple[int, ...]
    selected_bond_ids: tuple[int, ...]
    as_of_date: date
    market_source: Literal["moex"]
    capital_rub: Decimal
    policy: PortfolioStrategyPolicyV1


class PortfolioStrategyView(StrategyContract):
    contract_version: Literal["portfolio-strategy-v1"] = "portfolio-strategy-v1"
    as_of_date: date
    market_source: Literal["moex"]
    status: Literal["FULLY_INVESTED", "PARTIALLY_INVESTED", "EMPTY"]
    request: PortfolioStrategyRequest
    policy: PortfolioStrategyPolicyV1
    positions: tuple[PortfolioStrategyPosition, ...]
    non_selections: tuple[PortfolioStrategyNonSelection, ...]
    allocation_attempts: tuple[PortfolioStrategyAllocationAttempt, ...]
    summary: PortfolioStrategySummary
    provenance: PortfolioStrategyProvenance
    final_risk_evaluation: PortfolioRiskEvaluationView
    source_investment_batch: InvestmentEvaluationBatchView
    source_risk_batch: RiskCandidateBatchView
    capabilities: StrategyCapabilities = Field(default_factory=StrategyCapabilities)
    pit_ready: Literal[False] = False
