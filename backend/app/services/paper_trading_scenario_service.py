from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_portfolio import PaperPortfolio
from app.schemas.paper_trading import (
    PaperPortfolioCreate,
    PaperPortfolioMarkPeriodRequest,
    PaperPortfolioRebalanceRequest,
)
from app.schemas.paper_trading_scenario import (
    SCENARIO_REBALANCE_FREQUENCIES,
    PaperTradingScenarioCycleResult,
    PaperTradingScenarioRunRequest,
    PaperTradingScenarioRunResponse,
    PaperTradingScenarioSummary,
    PaperTradingScenarioWarning,
)
from app.schemas.portfolio_construction import (
    PORTFOLIO_DECISION_STATUSES,
    PORTFOLIO_RISK_LEVELS,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.paper_trading_report_service import PaperTradingReportService
from app.services.paper_trading_service import PaperTradingService


ZERO = Decimal("0")


class PaperTradingScenarioService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.paper_service = PaperTradingService(db)

    def run(
        self,
        request: PaperTradingScenarioRunRequest,
    ) -> PaperTradingScenarioRunResponse:
        self._validate_request(request)
        model_run = self._load_model_run(request.model_run_id)
        portfolio, warnings = self._resolve_portfolio(request)
        prediction_dates = self._prediction_dates(model_run, request)
        cycle_dates = self._rebalance_dates(
            prediction_dates,
            frequency=request.rebalance_frequency,
            horizon_days=model_run.horizon_days,
            rebalance_gap_days=request.rebalance_gap_days,
        )[: request.max_cycles]

        cycles: list[PaperTradingScenarioCycleResult] = []
        for index, as_of_date in enumerate(cycle_dates, start=1):
            cycle = self._run_cycle(
                portfolio_id=portfolio.id,
                model_run_id=model_run.id,
                as_of_date=as_of_date,
                cycle_index=index,
                request=request,
            )
            cycles.append(cycle)
            warnings.extend(cycle.warnings)
            if (
                cycle.rebalance_status == "failed"
                and request.stop_on_rebalance_error
            ):
                break
            if cycle.mark_status == "failed" and request.stop_on_mark_error:
                break

        self.db.refresh(portfolio)
        performance_report = None
        if request.include_performance_report:
            try:
                performance_report = PaperTradingReportService(self.db).performance(
                    portfolio.id,
                    date_from=request.date_from,
                    date_to=request.date_to,
                    include_equity_curve=True,
                )
            except Exception:
                warnings.append(
                    PaperTradingScenarioWarning(
                        message="Performance report failed after scenario execution"
                    )
                )

        summary = self._summary(
            portfolio=portfolio,
            cycles=cycles,
            performance_report=performance_report,
        )
        cycles_for_response = cycles if request.include_cycle_details else []
        return PaperTradingScenarioRunResponse(
            portfolio_id=portfolio.id,
            model_run_id=model_run.id,
            return_method=self._return_method(model_run),
            horizon_days=model_run.horizon_days,
            date_from=request.date_from,
            date_to=request.date_to,
            cycles_requested=len(cycle_dates),
            cycles_completed=sum(
                cycle.rebalance_status == "completed"
                and cycle.mark_status == "completed"
                for cycle in cycles
            ),
            rebalance_success_count=sum(
                cycle.rebalance_status == "completed" for cycle in cycles
            ),
            mark_success_count=sum(cycle.mark_status == "completed" for cycle in cycles),
            rebalance_failed_count=sum(
                cycle.rebalance_status == "failed" for cycle in cycles
            ),
            mark_failed_count=sum(cycle.mark_status == "failed" for cycle in cycles),
            final_portfolio_value=portfolio.current_value,
            final_cash_balance=portfolio.cash_balance,
            summary=summary,
            cycles=cycles_for_response,
            performance_report=performance_report,
            warnings=warnings,
        )

    def _run_cycle(
        self,
        *,
        portfolio_id: int,
        model_run_id: int,
        as_of_date: date,
        cycle_index: int,
        request: PaperTradingScenarioRunRequest,
    ) -> PaperTradingScenarioCycleResult:
        portfolio = self.paper_service.get_portfolio(portfolio_id)
        value_before = portfolio.current_value
        cycle_warnings: list[PaperTradingScenarioWarning] = []
        try:
            rebalance = self.paper_service.rebalance(
                portfolio_id,
                PaperPortfolioRebalanceRequest(
                    model_run_id=model_run_id,
                    as_of_date=as_of_date,
                    top_n=request.top_n,
                    min_probability_positive=request.min_probability_positive,
                    max_position_weight=request.max_position_weight,
                    max_issuer_weight=request.max_issuer_weight,
                    max_high_risk_weight=request.max_high_risk_weight,
                    min_liquidity_score=request.min_liquidity_score,
                    exclude_blocked_by_risk=request.exclude_blocked_by_risk,
                    exclude_insufficient_credit_data=request.exclude_insufficient_credit_data,
                    allowed_risk_levels=request.allowed_risk_levels,
                    allowed_decision_statuses=request.allowed_decision_statuses,
                    transaction_cost_rate=request.transaction_cost_rate,
                    include_excluded_candidates=False,
                ),
            )
        except Exception as exc:
            self.db.rollback()
            cycle_warnings.append(
                self._cycle_warning(
                    message="Rebalance failed for cycle",
                    exc=exc,
                    as_of_date=as_of_date,
                    cycle_index=cycle_index,
                )
            )
            return PaperTradingScenarioCycleResult(
                cycle_index=cycle_index,
                as_of_date=as_of_date,
                mark_snapshot_date=None,
                rebalance_status="failed",
                mark_status="skipped",
                portfolio_value_before=value_before,
                portfolio_value_after_rebalance=None,
                portfolio_value_after_mark=None,
                selected_positions_count=0,
                turnover=None,
                fee_amount=None,
                warnings=cycle_warnings,
            )

        for warning in rebalance.warnings:
            cycle_warnings.append(
                PaperTradingScenarioWarning(
                    message=warning.message,
                    as_of_date=warning.as_of_date or as_of_date,
                    cycle_index=cycle_index,
                    details=warning.details,
                )
            )

        mark_snapshot_date = None
        value_after_mark = None
        mark_status = "completed"
        try:
            mark = self.paper_service.mark_period(
                portfolio_id,
                PaperPortfolioMarkPeriodRequest(
                    as_of_date=as_of_date,
                    allow_partial=request.allow_partial_marking,
                ),
            )
            mark_snapshot_date = mark.snapshot.as_of_date
            value_after_mark = mark.portfolio.current_value
            for warning in mark.warnings:
                cycle_warnings.append(
                    PaperTradingScenarioWarning(
                        message=warning.message,
                        as_of_date=warning.as_of_date or as_of_date,
                        cycle_index=cycle_index,
                        details=warning.details,
                    )
                )
        except Exception as exc:
            self.db.rollback()
            mark_status = "failed"
            cycle_warnings.append(
                self._cycle_warning(
                    message="Mark period failed for cycle",
                    exc=exc,
                    as_of_date=as_of_date,
                    cycle_index=cycle_index,
                )
            )

        return PaperTradingScenarioCycleResult(
            cycle_index=cycle_index,
            as_of_date=as_of_date,
            mark_snapshot_date=mark_snapshot_date,
            rebalance_status="completed",
            mark_status=mark_status,
            portfolio_value_before=value_before,
            portfolio_value_after_rebalance=rebalance.portfolio.current_value,
            portfolio_value_after_mark=value_after_mark,
            selected_positions_count=len(rebalance.selected_positions),
            turnover=rebalance.turnover,
            fee_amount=rebalance.fee_amount,
            warnings=cycle_warnings,
        )

    def _resolve_portfolio(
        self,
        request: PaperTradingScenarioRunRequest,
    ) -> tuple[PaperPortfolio, list[PaperTradingScenarioWarning]]:
        if request.portfolio_id is None:
            portfolio = self.paper_service.create_portfolio(
                PaperPortfolioCreate(
                    name=request.name or f"Scenario portfolio {request.model_run_id}",
                    description=request.description,
                    initial_capital=request.initial_capital,
                    base_currency=request.base_currency,
                    model_run_id=request.model_run_id,
                )
            )
            return portfolio, []

        portfolio = self.paper_service.get_portfolio(request.portfolio_id)
        if portfolio.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paper portfolio is archived",
            )
        return portfolio, [
            PaperTradingScenarioWarning(
                message=(
                    "Existing paper portfolio was reused; scenario operations "
                    "were appended to current virtual state"
                )
            )
        ]

    def _prediction_dates(
        self,
        model_run: MLModelRun,
        request: PaperTradingScenarioRunRequest,
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

    def _summary(
        self,
        *,
        portfolio: PaperPortfolio,
        cycles: list[PaperTradingScenarioCycleResult],
        performance_report: Any | None,
    ) -> PaperTradingScenarioSummary:
        if performance_report is not None:
            metrics = performance_report.metrics
            return PaperTradingScenarioSummary(
                initial_capital=metrics.initial_capital,
                final_value=portfolio.current_value,
                cumulative_return=metrics.cumulative_return,
                max_drawdown=metrics.max_drawdown,
                total_fee_amount=metrics.total_fee_amount,
                snapshot_count=metrics.snapshot_count,
                transaction_count=metrics.transaction_count,
                active_positions_count=metrics.active_positions_count,
                last_cycle_as_of_date=cycles[-1].as_of_date if cycles else None,
            )
        cumulative_return = (
            portfolio.current_value / portfolio.initial_capital - Decimal("1")
            if portfolio.initial_capital > 0
            else ZERO
        )
        active_positions = [
            position
            for position in self.paper_service.list_positions(portfolio.id)
            if position.is_active
        ]
        return PaperTradingScenarioSummary(
            initial_capital=portfolio.initial_capital,
            final_value=portfolio.current_value,
            cumulative_return=cumulative_return,
            max_drawdown=None,
            total_fee_amount=None,
            snapshot_count=len(self.paper_service.list_snapshots(portfolio.id)),
            transaction_count=len(self.paper_service.list_transactions(portfolio.id)),
            active_positions_count=len(active_positions),
            last_cycle_as_of_date=cycles[-1].as_of_date if cycles else None,
        )

    def _load_model_run(self, model_run_id: int | None) -> MLModelRun:
        if model_run_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_run_id is required",
            )
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

    def _validate_request(self, request: PaperTradingScenarioRunRequest) -> None:
        if request.initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="initial_capital must be positive",
            )
        if request.model_run_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_run_id is required",
            )
        if (
            request.date_from is not None
            and request.date_to is not None
            and request.date_from > request.date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.rebalance_frequency not in SCENARIO_REBALANCE_FREQUENCIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid rebalance frequency",
            )
        if request.rebalance_gap_days is not None and request.rebalance_gap_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rebalance_gap_days must be positive",
            )
        if request.max_cycles < 1 or request.max_cycles > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_cycles must be between 1 and 500",
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
        if request.transaction_cost_rate < 0 or request.transaction_cost_rate > Decimal("0.1"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="transaction_cost_rate must be between 0 and 0.1",
            )
        if request.allowed_risk_levels is not None and any(
            value not in PORTFOLIO_RISK_LEVELS for value in request.allowed_risk_levels
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid risk level",
            )
        if request.allowed_decision_statuses is not None and any(
            value not in PORTFOLIO_DECISION_STATUSES
            for value in request.allowed_decision_statuses
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid decision status",
            )

    @staticmethod
    def _cycle_warning(
        *,
        message: str,
        exc: Exception,
        as_of_date: date,
        cycle_index: int,
    ) -> PaperTradingScenarioWarning:
        detail: Any = str(exc)
        if isinstance(exc, HTTPException):
            detail = exc.detail
        return PaperTradingScenarioWarning(
            message=message,
            as_of_date=as_of_date,
            cycle_index=cycle_index,
            details={"detail": detail},
        )

    @staticmethod
    def _return_method(model_run: MLModelRun) -> str:
        return_method = (model_run.params or {}).get("return_method") or "price"
        return return_method if return_method in RETURN_METHODS else "price"
