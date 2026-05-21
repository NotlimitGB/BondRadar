from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.portfolio_construction import (
    PORTFOLIO_DECISION_STATUSES,
    PORTFOLIO_RISK_LEVELS,
    ExcludedPortfolioCandidate,
    PortfolioCandidate,
    PortfolioConstructionRequest,
    PortfolioConstructionResponse,
    PortfolioConstructionSummary,
    PortfolioConstructionWarning,
    PortfolioConstraintReport,
)
from app.services.ml_feature_builder import RETURN_METHODS


HIGH_RISK_LEVELS = {"high", "critical"}


@dataclass(frozen=True)
class RawPortfolioCandidate:
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    as_of_date: date
    probability_positive: Decimal
    predicted_label: str
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


@dataclass(frozen=True)
class AllocationResult:
    candidate: RawPortfolioCandidate
    allocation_weight: Decimal
    selection_reasons: list[str]


class PortfolioConstructionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def construct(
        self,
        request: PortfolioConstructionRequest,
    ) -> PortfolioConstructionResponse:
        self._validate_request(request)
        model_run = self._load_model_run(request.model_run_id)
        return_method = self._return_method(model_run)
        as_of_date = self._resolve_as_of_date(model_run, request.as_of_date)
        candidates = self._load_candidates(model_run, as_of_date)
        warnings = self._warnings(candidates, as_of_date)

        excluded_reasons: dict[int, list[str]] = {}
        eligible: list[RawPortfolioCandidate] = []
        for candidate in candidates:
            reasons = self._filter_candidate(candidate, request)
            if reasons:
                excluded_reasons[candidate.bond_id] = reasons
            else:
                eligible.append(candidate)

        sorted_eligible = self._sort_candidates(eligible)[: request.top_n]
        allocations: list[AllocationResult] = []
        issuer_weights: dict[int, Decimal] = defaultdict(Decimal)
        high_risk_weight = Decimal("0")
        allocated_weight = Decimal("0")

        for candidate in sorted_eligible:
            if allocated_weight >= Decimal("1"):
                break
            weight = min(request.max_position_weight, Decimal("1") - allocated_weight)
            issuer_capacity = (
                request.max_issuer_weight - issuer_weights[candidate.company_id or 0]
            )
            reduced_by_issuer = weight > issuer_capacity
            weight = min(weight, issuer_capacity)

            reduced_by_high_risk = False
            if candidate.risk_level in HIGH_RISK_LEVELS:
                high_risk_capacity = request.max_high_risk_weight - high_risk_weight
                reduced_by_high_risk = weight > high_risk_capacity
                weight = min(weight, high_risk_capacity)

            if weight <= 0:
                excluded_reasons[candidate.bond_id] = [
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

            allocations.append(
                AllocationResult(
                    candidate=candidate,
                    allocation_weight=weight,
                    selection_reasons=reasons,
                )
            )
            allocated_weight += weight
            issuer_weights[candidate.company_id or 0] += weight
            if candidate.risk_level in HIGH_RISK_LEVELS:
                high_risk_weight += weight

        selected = [
            self._selected_candidate(allocation, request.capital)
            for allocation in allocations
        ]
        excluded = []
        if request.include_excluded_candidates:
            excluded = [
                self._excluded_candidate(candidate, excluded_reasons[candidate.bond_id])
                for candidate in candidates
                if candidate.bond_id in excluded_reasons
            ]

        summary = self._summary(
            candidates=candidates,
            selected=selected,
            excluded_count=len(excluded_reasons),
            exclusion_reason_counts=self._exclusion_reason_counts(excluded_reasons),
            capital=request.capital,
            issuer_weights=issuer_weights,
            high_risk_weight=high_risk_weight,
        )
        constraints = self._constraints(
            candidates=candidates,
            selected=selected,
            excluded_reasons=excluded_reasons,
            request=request,
            summary=summary,
        )
        if not selected:
            warnings.append(
                PortfolioConstructionWarning(
                    message="No candidates passed all filters",
                    as_of_date=as_of_date,
                    details={
                        "exclusion_reason_counts": summary.exclusion_reason_counts,
                    },
                )
            )
        if summary.unallocated_weight > 0:
            warnings.append(
                PortfolioConstructionWarning(
                    message="Some capital remains unallocated because constraints limited allocation",
                    as_of_date=as_of_date,
                    details={"unallocated_weight": summary.unallocated_weight},
                )
            )

        return PortfolioConstructionResponse(
            model_run_id=model_run.id,
            as_of_date=as_of_date,
            return_method=return_method,
            horizon_days=model_run.horizon_days,
            capital=request.capital,
            summary=summary,
            selected_candidates=selected,
            excluded_candidates=excluded,
            constraints=constraints,
            warnings=warnings,
        )

    def _validate_request(self, request: PortfolioConstructionRequest) -> None:
        if request.capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="capital must be positive",
            )
        if request.top_n <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="top_n must be positive",
            )
        if request.min_probability_positive < 0 or request.min_probability_positive > 1:
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
        invalid_risk = set(request.allowed_risk_levels or []) - PORTFOLIO_RISK_LEVELS
        if invalid_risk:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid risk level",
            )
        invalid_decision = (
            set(request.allowed_decision_statuses or [])
            - PORTFOLIO_DECISION_STATUSES
        )
        if invalid_decision:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid decision status",
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

    def _resolve_as_of_date(
        self,
        model_run: MLModelRun,
        requested_date: date | None,
    ) -> date:
        if requested_date is not None:
            exists = self.db.execute(
                select(func.count())
                .select_from(MLPrediction)
                .where(
                    MLPrediction.model_run_id == model_run.id,
                    MLPrediction.as_of_date == requested_date,
                )
            ).scalar_one()
            if exists == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No predictions found for selected model run and as_of_date",
                )
            return requested_date

        latest_date = self.db.execute(
            select(func.max(MLPrediction.as_of_date)).where(
                MLPrediction.model_run_id == model_run.id
            )
        ).scalar_one()
        if latest_date is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No predictions found for selected model run",
            )
        return latest_date

    def _load_candidates(
        self,
        model_run: MLModelRun,
        as_of_date: date,
    ) -> list[RawPortfolioCandidate]:
        rows = self.db.execute(
            select(MLPrediction, BondFeatureSnapshot, Bond, Company)
            .join(Bond, Bond.id == MLPrediction.bond_id)
            .join(Company, Company.id == Bond.company_id)
            .outerjoin(
                BondFeatureSnapshot,
                BondFeatureSnapshot.id == MLPrediction.feature_snapshot_id,
            )
            .where(
                MLPrediction.model_run_id == model_run.id,
                MLPrediction.as_of_date == as_of_date,
            )
            .order_by(MLPrediction.id.asc())
        ).all()
        risks = self._latest_risk_by_bond(
            as_of_date,
            [prediction.bond_id for prediction, *_ in rows],
        )
        candidates: list[RawPortfolioCandidate] = []
        for prediction, feature, bond, company in rows:
            risk = risks.get(prediction.bond_id)
            feature_liquidity = None if feature is None else feature.liquidity_score
            risk_liquidity = None if risk is None else risk.liquidity_score
            candidates.append(
                RawPortfolioCandidate(
                    bond_id=bond.id,
                    bond_name=bond.name,
                    isin=bond.isin,
                    secid=bond.secid,
                    company_id=company.id,
                    company_name=company.name,
                    as_of_date=prediction.as_of_date,
                    probability_positive=prediction.probability_positive,
                    predicted_label=prediction.predicted_label,
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

    def _filter_candidate(
        self,
        candidate: RawPortfolioCandidate,
        request: PortfolioConstructionRequest,
    ) -> list[str]:
        reasons: list[str] = []
        if candidate.probability_positive < request.min_probability_positive:
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
    def _sort_candidates(
        candidates: list[RawPortfolioCandidate],
    ) -> list[RawPortfolioCandidate]:
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

    def _summary(
        self,
        *,
        candidates: list[RawPortfolioCandidate],
        selected: list[PortfolioCandidate],
        excluded_count: int,
        exclusion_reason_counts: dict[str, int],
        capital: Decimal,
        issuer_weights: dict[int, Decimal],
        high_risk_weight: Decimal,
    ) -> PortfolioConstructionSummary:
        allocated_weight = sum(
            (candidate.allocation_weight for candidate in selected),
            Decimal("0"),
        )
        selected_probs = [candidate.probability_positive for candidate in selected]
        selected_ytms = [
            candidate.yield_to_maturity
            for candidate in selected
            if candidate.yield_to_maturity is not None
        ]
        weighted_probability = None
        if allocated_weight > 0:
            weighted_probability = sum(
                candidate.probability_positive * candidate.allocation_weight
                for candidate in selected
            ) / allocated_weight
        weighted_ytm = None
        ytm_weight = sum(
            (
                candidate.allocation_weight
                for candidate in selected
                if candidate.yield_to_maturity is not None
            ),
            Decimal("0"),
        )
        if ytm_weight > 0:
            weighted_ytm = sum(
                (candidate.yield_to_maturity or Decimal("0"))
                * candidate.allocation_weight
                for candidate in selected
            ) / ytm_weight
        return PortfolioConstructionSummary(
            candidate_count=len(candidates),
            selected_count=len(selected),
            excluded_count=excluded_count,
            allocated_weight=allocated_weight,
            unallocated_weight=max(Decimal("0"), Decimal("1") - allocated_weight),
            allocated_capital=capital * allocated_weight,
            unallocated_capital=capital * max(Decimal("0"), Decimal("1") - allocated_weight),
            average_probability_positive=(
                sum(selected_probs) / Decimal(len(selected_probs))
                if selected_probs
                else None
            ),
            weighted_probability_positive=weighted_probability,
            average_yield_to_maturity=(
                sum(selected_ytms) / Decimal(len(selected_ytms))
                if selected_ytms
                else None
            ),
            weighted_yield_to_maturity=weighted_ytm,
            max_issuer_weight=max(issuer_weights.values(), default=Decimal("0")),
            high_risk_weight=high_risk_weight,
            exclusion_reason_counts=exclusion_reason_counts,
        )

    def _constraints(
        self,
        *,
        candidates: list[RawPortfolioCandidate],
        selected: list[PortfolioCandidate],
        excluded_reasons: dict[int, list[str]],
        request: PortfolioConstructionRequest,
        summary: PortfolioConstructionSummary,
    ) -> list[PortfolioConstraintReport]:
        probability_passed = sum(
            candidate.probability_positive >= request.min_probability_positive
            for candidate in candidates
        )
        liquidity_excluded = sum(
            any(reason.startswith("Liquidity") for reason in reasons)
            for reasons in excluded_reasons.values()
        )
        risk_excluded = sum(
            any(
                reason in {"Blocked by risk assessment", "Risk assessment is missing", "Insufficient credit risk data"}
                for reason in reasons
            )
            for reasons in excluded_reasons.values()
        )
        reports = [
            PortfolioConstraintReport(
                name="probability_filter",
                status="pass" if probability_passed else "warning",
                message=(
                    "At least one candidate passed probability filter"
                    if probability_passed
                    else "No candidates passed probability filter"
                ),
                details={"passed_count": probability_passed},
            ),
            PortfolioConstraintReport(
                name="liquidity_filter",
                status="pass" if liquidity_excluded == 0 else "warning",
                message=(
                    "Liquidity filter did not exclude candidates"
                    if liquidity_excluded == 0
                    else "Liquidity filter excluded candidates"
                ),
                details={"excluded_count": liquidity_excluded},
            ),
            PortfolioConstraintReport(
                name="risk_block_filter",
                status="pass" if risk_excluded == 0 else "warning",
                message=(
                    "Risk filters did not exclude candidates"
                    if risk_excluded == 0
                    else "Risk filters excluded candidates"
                ),
                details={"excluded_count": risk_excluded},
            ),
            PortfolioConstraintReport(
                name="issuer_concentration",
                status=(
                    "pass"
                    if summary.max_issuer_weight <= request.max_issuer_weight
                    else "fail"
                ),
                message="Issuer concentration is within configured cap",
                details={
                    "max_issuer_weight": summary.max_issuer_weight,
                    "cap": request.max_issuer_weight,
                },
            ),
            PortfolioConstraintReport(
                name="high_risk_weight",
                status=(
                    "pass"
                    if summary.high_risk_weight <= request.max_high_risk_weight
                    else "fail"
                ),
                message="High-risk weight is within configured cap",
                details={
                    "high_risk_weight": summary.high_risk_weight,
                    "cap": request.max_high_risk_weight,
                },
            ),
        ]
        allocation_status = "pass"
        allocation_message = "Allocated weight is positive"
        if summary.allocated_weight == 0:
            allocation_status = "warning"
            allocation_message = "Allocated weight is zero"
        elif summary.unallocated_weight > 0:
            allocation_status = "warning"
            allocation_message = "Some portfolio weight remains unallocated"
        reports.append(
            PortfolioConstraintReport(
                name="allocation",
                status=allocation_status,
                message=allocation_message,
                details={
                    "allocated_weight": summary.allocated_weight,
                    "unallocated_weight": summary.unallocated_weight,
                    "selected_count": len(selected),
                },
            )
        )
        return reports

    @staticmethod
    def _selected_candidate(
        allocation: AllocationResult,
        capital: Decimal,
    ) -> PortfolioCandidate:
        candidate = allocation.candidate
        return PortfolioCandidate(
            bond_id=candidate.bond_id,
            bond_name=candidate.bond_name,
            isin=candidate.isin,
            secid=candidate.secid,
            company_id=candidate.company_id,
            company_name=candidate.company_name,
            as_of_date=candidate.as_of_date,
            probability_positive=candidate.probability_positive,
            predicted_label=candidate.predicted_label,
            allocation_weight=allocation.allocation_weight,
            allocation_amount=capital * allocation.allocation_weight,
            yield_to_maturity=candidate.yield_to_maturity,
            duration_years=candidate.duration_years,
            liquidity_score=candidate.liquidity_score,
            volume=candidate.volume,
            decision_status=candidate.decision_status,
            risk_level=candidate.risk_level,
            assessment_score=candidate.assessment_score,
            required_risk_premium=candidate.required_risk_premium,
            selection_reasons=allocation.selection_reasons,
            risk_notes=candidate.risk_notes,
        )

    @staticmethod
    def _excluded_candidate(
        candidate: RawPortfolioCandidate,
        reasons: list[str],
    ) -> ExcludedPortfolioCandidate:
        return ExcludedPortfolioCandidate(
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
            exclusion_reasons=reasons,
        )

    @staticmethod
    def _warnings(
        candidates: list[RawPortfolioCandidate],
        as_of_date: date,
    ) -> list[PortfolioConstructionWarning]:
        warnings: list[PortfolioConstructionWarning] = []
        if any(not candidate.has_feature_snapshot for candidate in candidates):
            warnings.append(
                PortfolioConstructionWarning(
                    message="Feature snapshot is missing for some predictions",
                    as_of_date=as_of_date,
                )
            )
        if any(not candidate.has_risk_assessment for candidate in candidates):
            warnings.append(
                PortfolioConstructionWarning(
                    message="Risk assessment is missing for some candidates",
                    as_of_date=as_of_date,
                )
            )
        return warnings

    @staticmethod
    def _exclusion_reason_counts(
        excluded_reasons: dict[int, list[str]],
    ) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for reasons in excluded_reasons.values():
            for reason in reasons:
                counts[reason] += 1
        return dict(
            sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )

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
        return return_method if return_method in RETURN_METHODS else "price"
