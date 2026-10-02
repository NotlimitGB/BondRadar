"""Frozen batch-relative evaluation policy and evidence, never trade instructions."""

from datetime import date
from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.unified_candidate import UnifiedCandidateView, UnifiedCandidateBatchView
from app.schemas.bond_credit_cohort_relative_value import BondCreditCohortRelativeValueMemberView
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.credit_cohort_peer_spread_distribution import CreditCohortPeerSpreadDistributionView


class ModelContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class InvestmentModelPolicyV1(ModelContract):
    contract_version: Literal["investment-model-policy-v1"] = "investment-model-policy-v1"
    ofz_relative_value_weight: Decimal = Decimal("0.40")
    credit_cohort_relative_value_weight: Decimal = Decimal("0.30")
    liquidity_weight: Decimal = Decimal("0.20")
    duration_weight: Decimal = Decimal("0.10")
    min_peer_count: Literal[2] = 2
    pit_ready: Literal[False] = False

    @model_validator(mode="after")
    def fixed_weights(self):
        weights = (self.ofz_relative_value_weight, self.credit_cohort_relative_value_weight,
                   self.liquidity_weight, self.duration_weight)
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            if (any(not w.is_finite() or w < 0 for w in weights) or sum(weights) != Decimal("1") or
                    weights != (Decimal(".40"), Decimal(".30"), Decimal(".20"), Decimal(".10"))):
                raise ValueError("Investment Model v1 weights are fixed")
        return self


class InvestmentPeerContext(ModelContract):
    bond_id: int
    target_kind: RatingTargetKind
    rating_agency: RatingAgency
    member: BondCreditCohortRelativeValueMemberView
    distribution: CreditCohortPeerSpreadDistributionView
    pit_ready: Literal[False] = False


class InvestmentCapabilities(ModelContract):
    investment_model_ready: Literal[True] = True
    investment_score_ready: Literal[True] = True
    investment_ranking_ready: Literal[True] = True
    recommendation_ready: Literal[False] = False
    portfolio_construction_ready: Literal[False] = False
    risk_engine_ready: Literal[False] = False
    shadow_execution_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    default_probability_ready: Literal[False] = False
    expected_loss_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class InvestmentProvenance(ModelContract):
    unified_candidate_contract_version: Literal["unified-candidate-v1"] = "unified-candidate-v1"
    unified_candidate_batch_contract_version: Literal["unified-candidate-batch-v1"] = "unified-candidate-batch-v1"
    policy_version: Literal["investment-model-policy-v1"] = "investment-model-policy-v1"
    formula_version: Literal["investment-score-v1"] = "investment-score-v1"
    normalization_method: Literal["BATCH_ENDPOINT_MIDRANK_V1"] = "BATCH_ENDPOINT_MIDRANK_V1"
    selected_credit_percentile_method: Literal["MIN_READY_SOURCE_NATIVE_COHORT_PERCENTILE_V1"] = "MIN_READY_SOURCE_NATIVE_COHORT_PERCENTILE_V1"
    peer_distribution_contract_versions: tuple[str, ...]
    batch_candidate_bond_ids: tuple[int, ...]
    batch_ready_bond_ids: tuple[int, ...]
    target_market_snapshot_id: int
    curve_trade_date: date
    m3_as_of_date: date
    policy: InvestmentModelPolicyV1 = Field(default_factory=InvestmentModelPolicyV1)


class InvestmentEvaluationView(ModelContract):
    contract_version: Literal["investment-evaluation-v1"] = "investment-evaluation-v1"
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: str
    status: Literal["READY", "CREDIT_PEER_CONTEXT_UNAVAILABLE"]
    investment_score_v1: Decimal | None
    ofz_relative_value_percentile: Decimal | None
    ofz_relative_value_score: Decimal | None
    credit_cohort_relative_value_score: Decimal | None
    liquidity_score: Decimal
    duration_percentile: Decimal | None
    duration_score: Decimal | None
    yield_to_maturity_pct: Decimal
    spread_to_ofz_bps: Decimal
    reference_ofz_yield_pct: Decimal
    modified_duration_years: Decimal
    dv01_currency_per_bond: Decimal
    liquidity_score_v1: Decimal
    attempted_credit_contexts: tuple[InvestmentPeerContext, ...]
    ready_credit_contexts: tuple[InvestmentPeerContext, ...]
    selected_credit_context: InvestmentPeerContext | None
    selected_credit_percentile_method: Literal["MIN_READY_SOURCE_NATIVE_COHORT_PERCENTILE_V1"] = "MIN_READY_SOURCE_NATIVE_COHORT_PERCENTILE_V1"
    rank: int | None
    quality_flags: tuple[str, ...]
    candidate: UnifiedCandidateView
    provenance: InvestmentProvenance
    capabilities: InvestmentCapabilities = Field(default_factory=InvestmentCapabilities)
    pit_ready: Literal[False] = False


class InvestmentEvaluationBatchView(ModelContract):
    contract_version: Literal["investment-evaluation-batch-v1"] = "investment-evaluation-batch-v1"
    as_of_date: date
    market_source: str
    policy: InvestmentModelPolicyV1 = Field(default_factory=InvestmentModelPolicyV1)
    candidate_count: int
    ready_count: int
    unavailable_count: int
    candidate_bond_ids: tuple[int, ...]
    normalization_bond_ids: tuple[int, ...]
    ranked_bond_ids: tuple[int, ...]
    score_min: Decimal | None
    score_max: Decimal | None
    evaluations: tuple[InvestmentEvaluationView, ...]
    source_batch: UnifiedCandidateBatchView
    capabilities: InvestmentCapabilities = Field(default_factory=InvestmentCapabilities)
    pit_ready: Literal[False] = False
