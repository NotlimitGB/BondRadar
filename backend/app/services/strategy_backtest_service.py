from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.strategy_backtest import (
    BACKTEST_REBALANCE_FREQUENCIES,
    StrategyBacktestBaselineResult,
    StrategyBacktestExcludedCandidate,
    StrategyBacktestMetricSet,
    StrategyBacktestPeriodResult,
    StrategyBacktestRequest,
    StrategyBacktestResponse,
    StrategyBacktestSelectedCandidate,
    StrategyBacktestWarning,
)
from app.schemas.portfolio_construction import (
    PORTFOLIO_DECISION_STATUSES,
    PORTFOLIO_RISK_LEVELS,
)
from app.services.ml_feature_builder import RETURN_METHODS


EVALUABLE_LABELS = {"positive_return", "negative_return"}
HIGH_RISK_LEVELS = {"high", "critical"}
BASELINE_NAMES = {
    "equal_weight_all_evaluable",
    "top_yield_to_maturity",
    "top_liquidity",
}


@dataclass(frozen=True)
class BacktestCandidate:
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    as_of_date: date
    probability_positive: Decimal
    predicted_label: str
    realized_label: str | None
    realized_return: Decimal | None
    yield_to_maturity: Decimal | None
    duration_years: Decimal | None
    liquidity_score: int | None
    volume: Decimal | None
    decision_status: str | None
    risk_level: str | None
    assessment_score: int | None
    required_risk_premium: Decimal | None
    risk_notes: list[str]
    has_feature_snapshot: bool
    has_risk_assessment: bool
    is_evaluable: bool


@dataclass(frozen=True)
class AllocationChoice:
    candidate: BacktestCandidate
    weight: Decimal
    selection_reasons: list[str]


@dataclass(frozen=True)
class PeriodAllocation:
    choices: list[AllocationChoice]
    excluded_reasons: dict[int, list[str]]
    constraints: list[dict[str, Any]]
    allocated_weight: Decimal
    unallocated_weight: Decimal
    high_risk_weight: Decimal
    max_issuer_weight: Decimal


@dataclass(frozen=True)
class SimulationResult:
    final_portfolio_value: Decimal
    metrics: StrategyBacktestMetricSet
    periods: list[StrategyBacktestPeriodResult]
    warnings: list[StrategyBacktestWarning]


class StrategyBacktestService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, request: StrategyBacktestRequest) -> StrategyBacktestResponse:
        self._validate_request(request)
        model_run = self._load_model_run(request.model_run_id)
        return_method = self._return_method(model_run)
        prediction_dates = self._prediction_dates(model_run, request)
        rebalance_dates = self._rebalance_dates(
            prediction_dates,
            frequency=request.rebalance_frequency,
            horizon_days=model_run.horizon_days,
            rebalance_gap_days=request.rebalance_gap_days,
        )
        candidates_by_date = {
            as_of_date: self._candidate_rows_for_date(
                model_run,
                return_method,
                as_of_date,
            )
            for as_of_date in rebalance_dates
        }

        model_result = self._simulate(
            request=request,
            rebalance_dates=rebalance_dates,
            candidates_by_date=candidates_by_date,
            selector=(
                self._rank_model_candidates
                if request.use_portfolio_constraints
                else lambda candidates: self._select_model_candidates(
                    candidates, request
                )
            ),
            apply_probability_filter=request.use_portfolio_constraints,
            limit_to_top_n=request.use_portfolio_constraints,
        )
        warnings = list(model_result.warnings)
        if not any(candidate.is_evaluable for rows in candidates_by_date.values() for candidate in rows):
            warnings.append(
                StrategyBacktestWarning(
                    message="No evaluable realized labels found for selected predictions"
                )
            )

        baselines: list[StrategyBacktestBaselineResult] = []
        if request.include_baselines:
            baselines = self._baseline_results(
                request=request,
                rebalance_dates=rebalance_dates,
                candidates_by_date=candidates_by_date,
            )

        return StrategyBacktestResponse(
            model_run_id=model_run.id,
            return_method=return_method,
            horizon_days=model_run.horizon_days,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.initial_capital,
            final_portfolio_value=model_result.final_portfolio_value,
            metrics=model_result.metrics,
            periods=model_result.periods,
            baselines=baselines,
            warnings=warnings,
        )

    def _validate_request(self, request: StrategyBacktestRequest) -> None:
        if (
            request.date_from is not None
            and request.date_to is not None
            and request.date_from > request.date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="initial_capital must be positive",
            )
        if request.top_n <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="top_n must be positive",
            )
        if (
            request.min_probability_positive < 0
            or request.min_probability_positive > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_probability_positive must be between 0 and 1",
            )
        if request.max_position_weight <= 0 or request.max_position_weight > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_position_weight must be greater than 0 and at most 1",
            )
        if request.max_issuer_weight <= 0 or request.max_issuer_weight > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_issuer_weight must be greater than 0 and at most 1",
            )
        if request.max_high_risk_weight < 0 or request.max_high_risk_weight > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_high_risk_weight must be between 0 and 1",
            )
        if set(request.allowed_risk_levels or []) - PORTFOLIO_RISK_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid risk level",
            )
        if (
            set(request.allowed_decision_statuses or [])
            - PORTFOLIO_DECISION_STATUSES
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid decision status",
            )
        if request.transaction_cost_rate < 0 or request.transaction_cost_rate > Decimal(
            "0.1"
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="transaction_cost_rate must be between 0 and 0.1",
            )
        if request.rebalance_frequency not in BACKTEST_REBALANCE_FREQUENCIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid rebalance frequency",
            )
        if (
            request.rebalance_gap_days is not None
            and request.rebalance_gap_days <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rebalance_gap_days must be positive",
            )

    def _load_model_run(self, model_run_id: int) -> MLModelRun:
        model_run = self.db.get(MLModelRun, model_run_id)
        if model_run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ML model run not found",
            )
        if model_run.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML model run is not completed",
            )
        return model_run

    def _prediction_dates(
        self,
        model_run: MLModelRun,
        request: StrategyBacktestRequest,
    ) -> list[date]:
        conditions = [MLPrediction.model_run_id == model_run.id]
        if request.date_from is not None:
            conditions.append(MLPrediction.as_of_date >= request.date_from)
        if request.date_to is not None:
            conditions.append(MLPrediction.as_of_date <= request.date_to)

        dates = list(
            self.db.execute(
                select(MLPrediction.as_of_date)
                .where(*conditions)
                .distinct()
                .order_by(MLPrediction.as_of_date.asc())
            ).scalars()
        )
        if not dates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No predictions found for selected model run and date range",
            )
        return dates

    def _candidate_rows_for_date(
        self,
        model_run: MLModelRun,
        return_method: str,
        as_of_date: date,
    ) -> list[BacktestCandidate]:
        rows = self.db.execute(
            select(
                MLPrediction,
                BondFeatureSnapshot,
                BondReturnLabel,
                Bond,
                Company,
            )
            .join(Bond, Bond.id == MLPrediction.bond_id)
            .join(Company, Company.id == Bond.company_id)
            .outerjoin(
                BondFeatureSnapshot,
                BondFeatureSnapshot.id == MLPrediction.feature_snapshot_id,
            )
            .outerjoin(
                BondReturnLabel,
                and_(
                    BondReturnLabel.bond_id == MLPrediction.bond_id,
                    BondReturnLabel.as_of_date == MLPrediction.as_of_date,
                    BondReturnLabel.horizon_days == model_run.horizon_days,
                    BondReturnLabel.return_method == return_method,
                ),
            )
            .where(
                MLPrediction.model_run_id == model_run.id,
                MLPrediction.as_of_date == as_of_date,
            )
            .order_by(MLPrediction.id.asc())
        ).all()
        bond_ids = [prediction.bond_id for prediction, *_ in rows]
        risk_by_bond = self._latest_risk_by_bond(as_of_date, bond_ids)

        candidates: list[BacktestCandidate] = []
        for prediction, feature, label, bond, company in rows:
            risk = risk_by_bond.get(prediction.bond_id)
            is_evaluable = (
                label is not None
                and label.label in EVALUABLE_LABELS
                and label.label_binary is not None
                and label.future_return is not None
            )
            feature_liquidity = None if feature is None else feature.liquidity_score
            risk_liquidity = None if risk is None else risk.liquidity_score
            candidates.append(
                BacktestCandidate(
                    bond_id=bond.id,
                    bond_name=bond.name,
                    isin=bond.isin,
                    secid=bond.secid,
                    company_id=company.id,
                    company_name=company.name,
                    as_of_date=prediction.as_of_date,
                    probability_positive=prediction.probability_positive,
                    predicted_label=prediction.predicted_label,
                    realized_label=None if label is None else label.label,
                    realized_return=label.future_return if is_evaluable else None,
                    yield_to_maturity=(
                        feature.yield_to_maturity
                        if feature is not None and feature.yield_to_maturity is not None
                        else (None if risk is None else risk.yield_to_maturity)
                    ),
                    duration_years=(
                        feature.duration_years
                        if feature is not None and feature.duration_years is not None
                        else (None if risk is None else risk.duration_years)
                    ),
                    liquidity_score=(
                        feature_liquidity
                        if feature_liquidity is not None
                        else risk_liquidity
                    ),
                    volume=(
                        feature.volume
                        if feature is not None and feature.volume is not None
                        else (None if risk is None else risk.volume)
                    ),
                    decision_status=None if risk is None else risk.decision_status,
                    risk_level=None if risk is None else risk.risk_level,
                    assessment_score=None if risk is None else risk.assessment_score,
                    required_risk_premium=(
                        None if risk is None else risk.required_risk_premium
                    ),
                    risk_notes=[] if risk is None else self._risk_notes(risk),
                    has_feature_snapshot=feature is not None,
                    has_risk_assessment=risk is not None,
                    is_evaluable=is_evaluable,
                )
            )
        return candidates

    def _latest_risk_by_bond(
        self,
        as_of_date: date,
        bond_ids: list[int],
    ) -> dict[int, BondRiskAssessment]:
        if not bond_ids:
            return {}
        risk_by_bond: dict[int, BondRiskAssessment] = {}
        risks = self.db.execute(
            select(BondRiskAssessment)
            .where(
                BondRiskAssessment.bond_id.in_(set(bond_ids)),
                BondRiskAssessment.as_of_date <= as_of_date,
            )
            .order_by(
                BondRiskAssessment.bond_id.asc(),
                BondRiskAssessment.as_of_date.desc(),
                BondRiskAssessment.id.desc(),
            )
        ).scalars()
        for risk in risks:
            risk_by_bond.setdefault(risk.bond_id, risk)
        return risk_by_bond

    def _select_model_candidates(
        self,
        candidates: list[BacktestCandidate],
        request: StrategyBacktestRequest,
    ) -> list[BacktestCandidate]:
        selected: list[BacktestCandidate] = []
        for candidate in candidates:
            if candidate.probability_positive < request.min_probability_positive:
                continue
            if (
                request.min_liquidity_score is not None
                and (
                    candidate.liquidity_score is None
                    or candidate.liquidity_score < request.min_liquidity_score
                )
            ):
                continue
            if (
                request.exclude_blocked_by_risk
                and candidate.decision_status == "blocked_by_risk"
            ):
                continue
            if request.exclude_insufficient_credit_data and (
                candidate.decision_status is None
                or candidate.decision_status == "insufficient_data"
            ):
                continue
            selected.append(candidate)
        selected.sort(
            key=lambda candidate: (
                candidate.probability_positive,
                candidate.liquidity_score if candidate.liquidity_score is not None else -1,
                -candidate.bond_id,
            ),
            reverse=True,
        )
        return selected[: request.top_n]

    def _baseline_results(
        self,
        *,
        request: StrategyBacktestRequest,
        rebalance_dates: list[date],
        candidates_by_date: dict[date, list[BacktestCandidate]],
    ) -> list[StrategyBacktestBaselineResult]:
        selectors: dict[str, Callable[[list[BacktestCandidate]], list[BacktestCandidate]]] = {
            "equal_weight_all_evaluable": lambda candidates: [
                candidate for candidate in candidates if candidate.is_evaluable
            ],
            "top_yield_to_maturity": lambda candidates: sorted(
                [
                    candidate
                    for candidate in candidates
                    if candidate.is_evaluable and candidate.yield_to_maturity is not None
                ],
                key=lambda candidate: (candidate.yield_to_maturity, -candidate.bond_id),
                reverse=True,
            )[: request.top_n],
            "top_liquidity": lambda candidates: sorted(
                [
                    candidate
                    for candidate in candidates
                    if candidate.is_evaluable and candidate.liquidity_score is not None
                ],
                key=lambda candidate: (candidate.liquidity_score, -candidate.bond_id),
                reverse=True,
            )[: request.top_n],
        }
        results: list[StrategyBacktestBaselineResult] = []
        for name in sorted(BASELINE_NAMES):
            simulation = self._simulate(
                request=request,
                rebalance_dates=rebalance_dates,
                candidates_by_date=candidates_by_date,
                selector=selectors[name],
                apply_probability_filter=False,
                limit_to_top_n=False,
            )
            results.append(
                StrategyBacktestBaselineResult(
                    name=name,
                    final_portfolio_value=simulation.final_portfolio_value,
                    metrics=simulation.metrics,
                    warnings=simulation.warnings,
                )
            )
        return results

    def _simulate(
        self,
        *,
        request: StrategyBacktestRequest,
        rebalance_dates: list[date],
        candidates_by_date: dict[date, list[BacktestCandidate]],
        selector: Callable[[list[BacktestCandidate]], list[BacktestCandidate]],
        apply_probability_filter: bool,
        limit_to_top_n: bool,
    ) -> SimulationResult:
        value = request.initial_capital
        previous_weights: dict[int, Decimal] = {}
        periods: list[StrategyBacktestPeriodResult] = []
        warnings: list[StrategyBacktestWarning] = []
        turnovers: list[Decimal] = []

        for as_of_date in rebalance_dates:
            start_value = value
            ranked_candidates = selector(candidates_by_date.get(as_of_date, []))
            if request.use_portfolio_constraints:
                allocation = self._constrained_allocation(
                    ranked_candidates,
                    request,
                    apply_probability_filter=apply_probability_filter,
                    limit_to_top_n=limit_to_top_n,
                )
            else:
                allocation = self._simplified_allocation(ranked_candidates, request)

            for candidate_id, reasons in allocation.excluded_reasons.items():
                if "Candidate excluded from period calculation because realized label is missing" in reasons:
                    warnings.append(
                        StrategyBacktestWarning(
                            message="Candidate excluded from period calculation because realized label is missing",
                            as_of_date=as_of_date,
                            bond_id=candidate_id,
                        )
                    )
            if not allocation.choices:
                warnings.append(
                    StrategyBacktestWarning(
                        message="No candidates passed all filters for rebalance date",
                        as_of_date=as_of_date,
                    )
                )
                weights: dict[int, Decimal] = {}
                turnover = self._turnover(previous_weights, weights)
                gross_return = Decimal("0")
                costs_return = turnover * request.transaction_cost_rate
                period_return = gross_return - costs_return
                value = start_value * (Decimal("1") + period_return)
                turnovers.append(turnover)
                previous_weights = {}
                periods.append(
                    StrategyBacktestPeriodResult(
                        as_of_date=as_of_date,
                        portfolio_value_start=start_value,
                        portfolio_value_end=value,
                        period_return=period_return,
                        gross_period_return=gross_return,
                        estimated_costs_return=costs_return,
                        allocated_weight=Decimal("0"),
                        unallocated_weight=Decimal("1"),
                        allocated_capital=Decimal("0"),
                        unallocated_capital=value,
                        high_risk_weight=Decimal("0"),
                        max_issuer_weight=Decimal("0"),
                        excluded_candidates_count=len(allocation.excluded_reasons),
                        constraints=allocation.constraints,
                        selected_candidates_count=0,
                        selected_candidates=[],
                        excluded_candidates=self._excluded_candidates(
                            ranked_candidates,
                            allocation.excluded_reasons,
                            request.include_excluded_candidates,
                        ),
                    )
                )
                continue

            weights = {
                choice.candidate.bond_id: choice.weight for choice in allocation.choices
            }
            turnover = self._turnover(previous_weights, weights)
            gross_return = sum(
                choice.weight * (choice.candidate.realized_return or Decimal("0"))
                for choice in allocation.choices
            )
            costs_return = turnover * request.transaction_cost_rate
            period_return = gross_return - costs_return
            value = start_value * (Decimal("1") + period_return)
            turnovers.append(turnover)
            previous_weights = weights
            periods.append(
                StrategyBacktestPeriodResult(
                    as_of_date=as_of_date,
                    portfolio_value_start=start_value,
                        portfolio_value_end=value,
                        period_return=period_return,
                        gross_period_return=gross_return,
                        estimated_costs_return=costs_return,
                        allocated_weight=allocation.allocated_weight,
                        unallocated_weight=allocation.unallocated_weight,
                        allocated_capital=start_value * allocation.allocated_weight,
                        unallocated_capital=start_value * allocation.unallocated_weight,
                        high_risk_weight=allocation.high_risk_weight,
                        max_issuer_weight=allocation.max_issuer_weight,
                        excluded_candidates_count=len(allocation.excluded_reasons),
                        constraints=allocation.constraints,
                        selected_candidates_count=len(allocation.choices),
                    selected_candidates=[
                            self._selected_candidate(
                                choice,
                                start_value * choice.weight,
                            )
                            for choice in allocation.choices
                    ],
                        excluded_candidates=self._excluded_candidates(
                            ranked_candidates,
                            allocation.excluded_reasons,
                            request.include_excluded_candidates,
                        ),
                )
            )

        return SimulationResult(
            final_portfolio_value=value,
            metrics=self._metrics(
                initial_capital=request.initial_capital,
                final_portfolio_value=value,
                periods=periods,
                turnovers=turnovers,
            ),
            periods=periods,
            warnings=warnings,
        )

    def _rank_model_candidates(
        self,
        candidates: list[BacktestCandidate],
    ) -> list[BacktestCandidate]:
        return self._sort_for_constraints(candidates)

    def _simplified_allocation(
        self,
        ranked_candidates: list[BacktestCandidate],
        request: StrategyBacktestRequest,
    ) -> PeriodAllocation:
        excluded: dict[int, list[str]] = {}
        selected = []
        for candidate in ranked_candidates:
            if not candidate.is_evaluable:
                excluded[candidate.bond_id] = [
                    "Candidate excluded from period calculation because realized label is missing"
                ]
                continue
            selected.append(candidate)

        if not selected:
            return PeriodAllocation(
                choices=[],
                excluded_reasons=excluded,
                constraints=self._period_constraints(
                    excluded,
                    Decimal("0"),
                    Decimal("1"),
                    Decimal("0"),
                    Decimal("0"),
                    request,
                ),
                allocated_weight=Decimal("0"),
                unallocated_weight=Decimal("1"),
                high_risk_weight=Decimal("0"),
                max_issuer_weight=Decimal("0"),
            )

        raw_weight = Decimal("1") / Decimal(len(selected))
        weight = min(raw_weight, request.max_position_weight)
        choices = [
            AllocationChoice(
                candidate=candidate,
                weight=weight,
                selection_reasons=["Selected by probability ranking"],
            )
            for candidate in selected
        ]
        return self._period_allocation(choices, excluded, request)

    def _constrained_allocation(
        self,
        ranked_candidates: list[BacktestCandidate],
        request: StrategyBacktestRequest,
        *,
        apply_probability_filter: bool,
        limit_to_top_n: bool,
    ) -> PeriodAllocation:
        excluded: dict[int, list[str]] = {}
        eligible: list[BacktestCandidate] = []
        for candidate in ranked_candidates:
            reasons = self._filter_candidate(
                candidate,
                request,
                apply_probability_filter=apply_probability_filter,
            )
            if reasons:
                excluded[candidate.bond_id] = reasons
            else:
                eligible.append(candidate)
        if limit_to_top_n:
            eligible = eligible[: request.top_n]

        choices: list[AllocationChoice] = []
        issuer_weights: dict[int, Decimal] = defaultdict(Decimal)
        high_risk_weight = Decimal("0")
        allocated_weight = Decimal("0")
        for candidate in eligible:
            if allocated_weight >= Decimal("1"):
                break
            weight = min(request.max_position_weight, Decimal("1") - allocated_weight)
            issuer_key = candidate.company_id or 0
            issuer_capacity = request.max_issuer_weight - issuer_weights[issuer_key]
            reduced_by_issuer = weight > issuer_capacity
            weight = min(weight, issuer_capacity)

            reduced_by_high_risk = False
            if candidate.risk_level in HIGH_RISK_LEVELS:
                high_risk_capacity = request.max_high_risk_weight - high_risk_weight
                reduced_by_high_risk = weight > high_risk_capacity
                weight = min(weight, high_risk_capacity)

            if weight <= 0:
                excluded[candidate.bond_id] = [
                    "Allocation constraints left no available weight"
                ]
                continue

            reasons = [
                "Probability filter passed",
                "Risk constraints passed",
                "Liquidity constraints passed",
                "Selected by probability ranking",
            ]
            if reduced_by_issuer:
                reasons.append("Allocation reduced by issuer concentration cap")
            if reduced_by_high_risk:
                reasons.append("Allocation reduced by high-risk cap")
            choices.append(
                AllocationChoice(
                    candidate=candidate,
                    weight=weight,
                    selection_reasons=reasons,
                )
            )
            allocated_weight += weight
            issuer_weights[issuer_key] += weight
            if candidate.risk_level in HIGH_RISK_LEVELS:
                high_risk_weight += weight

        return self._period_allocation(choices, excluded, request)

    def _period_allocation(
        self,
        choices: list[AllocationChoice],
        excluded: dict[int, list[str]],
        request: StrategyBacktestRequest,
    ) -> PeriodAllocation:
        allocated_weight = sum((choice.weight for choice in choices), Decimal("0"))
        high_risk_weight = sum(
            (
                choice.weight
                for choice in choices
                if choice.candidate.risk_level in HIGH_RISK_LEVELS
            ),
            Decimal("0"),
        )
        issuer_weights: dict[int, Decimal] = defaultdict(Decimal)
        for choice in choices:
            issuer_weights[choice.candidate.company_id or 0] += choice.weight
        max_issuer_weight = max(issuer_weights.values(), default=Decimal("0"))
        unallocated_weight = max(Decimal("0"), Decimal("1") - allocated_weight)
        return PeriodAllocation(
            choices=choices,
            excluded_reasons=excluded,
            constraints=self._period_constraints(
                excluded,
                allocated_weight,
                unallocated_weight,
                high_risk_weight,
                max_issuer_weight,
                request,
            ),
            allocated_weight=allocated_weight,
            unallocated_weight=unallocated_weight,
            high_risk_weight=high_risk_weight,
            max_issuer_weight=max_issuer_weight,
        )

    def _filter_candidate(
        self,
        candidate: BacktestCandidate,
        request: StrategyBacktestRequest,
        *,
        apply_probability_filter: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if not candidate.is_evaluable:
            reasons.append(
                "Candidate excluded from period calculation because realized label is missing"
            )
        if (
            apply_probability_filter
            and candidate.probability_positive < request.min_probability_positive
        ):
            reasons.append("Probability below minimum")
        if request.min_liquidity_score is not None:
            if candidate.liquidity_score is None:
                reasons.append("Liquidity score is missing")
            elif candidate.liquidity_score < request.min_liquidity_score:
                reasons.append("Liquidity score below minimum")
        if (
            request.exclude_blocked_by_risk
            and candidate.decision_status == "blocked_by_risk"
        ):
            reasons.append("Blocked by risk assessment")
        if request.exclude_insufficient_credit_data:
            if candidate.decision_status is None:
                reasons.append("Risk assessment is missing")
            elif candidate.decision_status == "insufficient_data":
                reasons.append("Insufficient credit risk data")
        if (
            request.allowed_risk_levels is not None
            and candidate.risk_level not in request.allowed_risk_levels
        ):
            reasons.append("Risk level is not allowed")
        if (
            request.allowed_decision_statuses is not None
            and candidate.decision_status not in request.allowed_decision_statuses
        ):
            reasons.append("Decision status is not allowed")
        return reasons

    @staticmethod
    def _sort_for_constraints(
        candidates: list[BacktestCandidate],
    ) -> list[BacktestCandidate]:
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.probability_positive,
                candidate.liquidity_score if candidate.liquidity_score is not None else -1,
                candidate.assessment_score if candidate.assessment_score is not None else -1,
                -candidate.bond_id,
            ),
            reverse=True,
        )

    @staticmethod
    def _period_constraints(
        excluded: dict[int, list[str]],
        allocated_weight: Decimal,
        unallocated_weight: Decimal,
        high_risk_weight: Decimal,
        max_issuer_weight: Decimal,
        request: StrategyBacktestRequest,
    ) -> list[dict[str, Any]]:
        liquidity_excluded = sum(
            any(reason.startswith("Liquidity") for reason in reasons)
            for reasons in excluded.values()
        )
        risk_excluded = sum(
            any(
                reason
                in {
                    "Blocked by risk assessment",
                    "Risk assessment is missing",
                    "Insufficient credit risk data",
                    "Risk level is not allowed",
                    "Decision status is not allowed",
                }
                for reason in reasons
            )
            for reasons in excluded.values()
        )
        allocation_status = "pass"
        allocation_message = "Allocated weight is positive"
        if allocated_weight == 0:
            allocation_status = "warning"
            allocation_message = "Allocated weight is zero"
        elif unallocated_weight > 0:
            allocation_status = "warning"
            allocation_message = "Some portfolio weight remains unallocated"
        return [
            {
                "name": "liquidity_filter",
                "status": "pass" if liquidity_excluded == 0 else "warning",
                "message": (
                    "Liquidity filter did not exclude candidates"
                    if liquidity_excluded == 0
                    else "Liquidity filter excluded candidates"
                ),
                "details": {"excluded_count": liquidity_excluded},
            },
            {
                "name": "risk_filter",
                "status": "pass" if risk_excluded == 0 else "warning",
                "message": (
                    "Risk filters did not exclude candidates"
                    if risk_excluded == 0
                    else "Risk filters excluded candidates"
                ),
                "details": {"excluded_count": risk_excluded},
            },
            {
                "name": "issuer_concentration",
                "status": (
                    "pass"
                    if max_issuer_weight <= request.max_issuer_weight
                    else "fail"
                ),
                "message": "Issuer concentration is within configured cap",
                "details": {
                    "max_issuer_weight": max_issuer_weight,
                    "cap": request.max_issuer_weight,
                },
            },
            {
                "name": "high_risk_weight",
                "status": (
                    "pass"
                    if high_risk_weight <= request.max_high_risk_weight
                    else "fail"
                ),
                "message": "High-risk weight is within configured cap",
                "details": {
                    "high_risk_weight": high_risk_weight,
                    "cap": request.max_high_risk_weight,
                },
            },
            {
                "name": "allocation",
                "status": allocation_status,
                "message": allocation_message,
                "details": {
                    "allocated_weight": allocated_weight,
                    "unallocated_weight": unallocated_weight,
                },
            },
        ]

    def _excluded_candidates(
        self,
        candidates: list[BacktestCandidate],
        excluded_reasons: dict[int, list[str]],
        include_excluded: bool,
    ) -> list[StrategyBacktestExcludedCandidate]:
        if not include_excluded:
            return []
        return [
            StrategyBacktestExcludedCandidate(
                bond_id=candidate.bond_id,
                bond_name=candidate.bond_name,
                isin=candidate.isin,
                secid=candidate.secid,
                company_id=candidate.company_id,
                company_name=candidate.company_name,
                as_of_date=candidate.as_of_date,
                probability_positive=candidate.probability_positive,
                predicted_label=candidate.predicted_label,
                yield_to_maturity=candidate.yield_to_maturity,
                liquidity_score=candidate.liquidity_score,
                decision_status=candidate.decision_status,
                risk_level=candidate.risk_level,
                exclusion_reasons=excluded_reasons[candidate.bond_id],
            )
            for candidate in candidates
            if candidate.bond_id in excluded_reasons
        ]

    @staticmethod
    def _turnover(
        previous_weights: dict[int, Decimal],
        current_weights: dict[int, Decimal],
    ) -> Decimal:
        bond_ids = set(previous_weights) | set(current_weights)
        return sum(
            abs(current_weights.get(bond_id, Decimal("0")) - previous_weights.get(bond_id, Decimal("0")))
            for bond_id in bond_ids
        )

    @staticmethod
    def _selected_candidate(
        choice: AllocationChoice,
        allocation_amount: Decimal,
    ) -> StrategyBacktestSelectedCandidate:
        candidate = choice.candidate
        return StrategyBacktestSelectedCandidate(
            bond_id=candidate.bond_id,
            bond_name=candidate.bond_name,
            isin=candidate.isin,
            secid=candidate.secid,
            company_id=candidate.company_id,
            company_name=candidate.company_name,
            as_of_date=candidate.as_of_date,
            probability_positive=candidate.probability_positive,
            predicted_label=candidate.predicted_label,
            realized_label=candidate.realized_label,
            realized_return=candidate.realized_return,
            weight=choice.weight,
            allocation_amount=allocation_amount,
            yield_to_maturity=candidate.yield_to_maturity,
            duration_years=candidate.duration_years,
            liquidity_score=candidate.liquidity_score,
            volume=candidate.volume,
            decision_status=candidate.decision_status,
            risk_level=candidate.risk_level,
            assessment_score=candidate.assessment_score,
            required_risk_premium=candidate.required_risk_premium,
            selection_reasons=choice.selection_reasons,
            risk_notes=candidate.risk_notes,
        )

    @staticmethod
    def _metrics(
        *,
        initial_capital: Decimal,
        final_portfolio_value: Decimal,
        periods: list[StrategyBacktestPeriodResult],
        turnovers: list[Decimal],
    ) -> StrategyBacktestMetricSet:
        period_returns = [period.period_return for period in periods]
        selected_counts = [period.selected_candidates_count for period in periods]
        selected_candidates = [
            candidate
            for period in periods
            for candidate in period.selected_candidates
            if candidate.realized_return is not None
        ]
        period_count = len(periods)
        total_return = final_portfolio_value / initial_capital - Decimal("1")
        annualized_return = None
        if period_count >= 2:
            days = (periods[-1].as_of_date - periods[0].as_of_date).days
            if days > 0 and total_return > Decimal("-1"):
                annualized_return = Decimal(
                    str((1 + float(total_return)) ** (365 / days) - 1)
                )
        volatility = None
        if period_count >= 2:
            mean_return = sum(period_returns) / Decimal(period_count)
            variance = sum(
                (period_return - mean_return) ** 2 for period_return in period_returns
            ) / Decimal(period_count)
            volatility = Decimal(str(sqrt(float(variance))))
        hit_rate = None
        if selected_candidates:
            hit_rate = Decimal(
                sum(candidate.realized_return > 0 for candidate in selected_candidates)
            ) / Decimal(len(selected_candidates))
        average_period_return = (
            sum(period_returns) / Decimal(period_count) if periods else None
        )
        average_selected_candidates = (
            sum(Decimal(count) for count in selected_counts) / Decimal(period_count)
            if periods
            else None
        )
        average_allocated_weight = (
            sum(period.allocated_weight for period in periods) / Decimal(period_count)
            if periods
            else None
        )
        average_unallocated_weight = (
            sum(period.unallocated_weight for period in periods) / Decimal(period_count)
            if periods
            else None
        )
        average_high_risk_weight = (
            sum(period.high_risk_weight for period in periods) / Decimal(period_count)
            if periods
            else None
        )
        average_max_issuer_weight = (
            sum(period.max_issuer_weight for period in periods) / Decimal(period_count)
            if periods
            else None
        )
        average_turnover = (
            sum(turnovers) / Decimal(len(turnovers)) if turnovers else Decimal("0")
        )
        return StrategyBacktestMetricSet(
            period_count=period_count,
            selected_period_count=sum(count > 0 for count in selected_counts),
            total_return=total_return,
            annualized_return=annualized_return,
            max_drawdown=StrategyBacktestService._max_drawdown(
                initial_capital, periods
            ),
            volatility=volatility,
            hit_rate=hit_rate,
            average_period_return=average_period_return,
            negative_periods_count=sum(
                period_return < 0 for period_return in period_returns
            ),
            turnover=average_turnover,
            average_selected_candidates=average_selected_candidates,
            average_allocated_weight=average_allocated_weight,
            average_unallocated_weight=average_unallocated_weight,
            average_high_risk_weight=average_high_risk_weight,
            average_max_issuer_weight=average_max_issuer_weight,
        )

    @staticmethod
    def _max_drawdown(
        initial_capital: Decimal,
        periods: list[StrategyBacktestPeriodResult],
    ) -> Decimal:
        peak = initial_capital
        max_drawdown = Decimal("0")
        for value in [period.portfolio_value_end for period in periods]:
            if value > peak:
                peak = value
            if peak > 0:
                drawdown = (peak - value) / peak
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
        return max_drawdown

    @staticmethod
    def _rebalance_dates(
        prediction_dates: list[date],
        *,
        frequency: str,
        horizon_days: int,
        rebalance_gap_days: int | None,
    ) -> list[date]:
        if frequency == "weekly":
            seen: set[tuple[int, int]] = set()
            dates: list[date] = []
            for prediction_date in prediction_dates:
                key = (
                    prediction_date.isocalendar().year,
                    prediction_date.isocalendar().week,
                )
                if key not in seen:
                    seen.add(key)
                    dates.append(prediction_date)
            return dates
        if frequency == "monthly":
            seen_months: set[tuple[int, int]] = set()
            dates = []
            for prediction_date in prediction_dates:
                key = (prediction_date.year, prediction_date.month)
                if key not in seen_months:
                    seen_months.add(key)
                    dates.append(prediction_date)
            return dates

        gap_days = rebalance_gap_days if rebalance_gap_days is not None else horizon_days
        dates = []
        previous_date: date | None = None
        for prediction_date in prediction_dates:
            if previous_date is None or (prediction_date - previous_date).days >= gap_days:
                dates.append(prediction_date)
                previous_date = prediction_date
        return dates

    @staticmethod
    def _risk_notes(risk: BondRiskAssessment) -> list[str]:
        notes: list[str] = []
        for values in (
            risk.warnings,
            risk.blocking_reasons,
            risk.negative_factors,
            risk.missing_data,
        ):
            notes.extend(str(value) for value in values or [])
        return notes

    @staticmethod
    def _return_method(model_run: MLModelRun) -> str:
        return_method = (model_run.params or {}).get("return_method") or "price"
        if return_method not in RETURN_METHODS:
            return "price"
        return return_method
