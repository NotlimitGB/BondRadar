from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_portfolio import PaperPortfolio
from app.schemas.paper_trading import (
    PaperPortfolioCreate,
    PaperPortfolioMarkPeriodRequest,
    PaperPortfolioMarkPeriodResult,
    PaperPortfolioRead,
    PaperPortfolioRebalanceRequest,
    PaperPortfolioRebalanceResult,
)
from app.schemas.paper_trading_live_cycle import (
    LivePaperCycleRunRead,
    LivePaperCycleRunRequest,
    LivePaperCycleRunResponse,
)
from app.schemas.paper_trading_live_readiness import LivePaperReadinessResponse
from app.services.paper_trading_live_readiness_service import LivePaperReadinessService
from app.services.paper_trading_service import PaperTradingService


class LivePaperCycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, request: LivePaperCycleRunRequest) -> LivePaperCycleRunResponse:
        self._validate_request(request)
        client_cycle_key = self._normalized_key(request.client_cycle_key)
        existing = self._get_by_client_cycle_key(client_cycle_key)
        if existing is not None:
            return self._stored_response(
                existing,
                request=request,
                extra_warnings=[
                    {
                        "message": "Existing live paper cycle was returned for client_cycle_key",
                        "details": {"client_cycle_key": client_cycle_key},
                    }
                ],
            )

        cycle = self._create_running_cycle(request, client_cycle_key)
        readiness: LivePaperReadinessResponse | None = None
        mark_result: PaperPortfolioMarkPeriodResult | None = None
        rebalance_result: PaperPortfolioRebalanceResult | None = None
        portfolio: PaperPortfolio | None = None
        try:
            readiness = LivePaperReadinessService(self.db).check(request.readiness)
            self._store_readiness(cycle, readiness)
            block_message = self._readiness_block_message(request, readiness)
            if block_message is not None:
                return self._block_cycle(
                    cycle,
                    request=request,
                    readiness=readiness,
                    message=block_message,
                )

            selected = readiness.selected_candidate
            if selected is None or selected.model_run_id is None:
                return self._block_cycle(
                    cycle,
                    request=request,
                    readiness=readiness,
                    message="Manual live paper cycle requires a single selected ML model run",
                )

            selected_model_run_id = selected.model_run_id
            selected_model_run_ids = selected.model_run_ids or [selected_model_run_id]
            cycle.selected_model_run_id = selected_model_run_id
            cycle.selected_model_run_ids_json = selected_model_run_ids
            self.db.commit()
            self.db.refresh(cycle)

            portfolio = self._resolve_portfolio(
                request,
                cycle=cycle,
                selected_model_run_id=selected_model_run_id,
            )
            cycle.portfolio_id = portfolio.id
            self.db.commit()
            self.db.refresh(cycle)

            if request.mark_period_before_rebalance:
                mark_request = request.mark_period or PaperPortfolioMarkPeriodRequest()
                mark_result = PaperTradingService(self.db).mark_period(
                    portfolio.id,
                    mark_request,
                )
                cycle.mark_period_result_json = mark_result.model_dump(mode="json")
                self.db.commit()
                self.db.refresh(cycle)

            rebalance_request = self._rebalance_request(
                request,
                selected_model_run_id=selected_model_run_id,
            )
            rebalance_result = PaperTradingService(self.db).rebalance(
                portfolio.id,
                rebalance_request,
            )
            cycle.rebalance_result_json = rebalance_result.model_dump(mode="json")
            cycle.as_of_date = rebalance_result.snapshot.as_of_date
            self._complete_cycle(cycle, readiness, rebalance_result)
            return self._response(
                cycle,
                request=request,
                readiness=readiness,
                portfolio=rebalance_result.portfolio,
                mark_result=mark_result,
                rebalance_result=rebalance_result,
            )
        except HTTPException as exc:
            return self._fail_cycle(
                cycle.id,
                request=request,
                readiness=readiness,
                portfolio=portfolio,
                mark_result=mark_result,
                error={
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive stable cycle logging
            return self._fail_cycle(
                cycle.id,
                request=request,
                readiness=readiness,
                portfolio=portfolio,
                mark_result=mark_result,
                error={
                    "type": exc.__class__.__name__,
                    "detail": str(exc),
                },
            )

    def list_runs(self, *, limit: int = 100) -> list[PaperLiveCycleRun]:
        return list(
            self.db.execute(
                select(PaperLiveCycleRun)
                .order_by(PaperLiveCycleRun.id.desc())
                .limit(max(1, min(limit, 500)))
            ).scalars()
        )

    def get_run(self, cycle_run_id: int) -> PaperLiveCycleRun:
        cycle = self.db.get(PaperLiveCycleRun, cycle_run_id)
        if cycle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Live paper cycle run not found",
            )
        return cycle

    def _validate_request(self, request: LivePaperCycleRunRequest) -> None:
        if request.portfolio_id is not None and request.portfolio_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="portfolio_id must be positive",
            )
        if request.client_cycle_key is not None and not request.client_cycle_key.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_cycle_key must not be blank",
            )
        if request.portfolio_name is not None and not request.portfolio_name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="portfolio_name must not be blank",
            )
        if request.portfolio_id is None and not request.create_portfolio_if_missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="portfolio_id is required when create_portfolio_if_missing is false",
            )

    def _get_by_client_cycle_key(
        self,
        client_cycle_key: str | None,
    ) -> PaperLiveCycleRun | None:
        if client_cycle_key is None:
            return None
        return self.db.execute(
            select(PaperLiveCycleRun).where(
                PaperLiveCycleRun.client_cycle_key == client_cycle_key
            )
        ).scalar_one_or_none()

    def _create_running_cycle(
        self,
        request: LivePaperCycleRunRequest,
        client_cycle_key: str | None,
    ) -> PaperLiveCycleRun:
        now = datetime.now(timezone.utc)
        cycle = PaperLiveCycleRun(
            status="running",
            mode="manual",
            client_cycle_key=client_cycle_key,
            as_of_date=request.as_of_date or request.rebalance.as_of_date,
            request_json=request.model_dump(mode="json"),
            readiness_json={},
            summary_json={},
            warnings_json=[],
            errors_json=[],
            started_at=now,
            created_at=now,
        )
        self.db.add(cycle)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    def _store_readiness(
        self,
        cycle: PaperLiveCycleRun,
        readiness: LivePaperReadinessResponse,
    ) -> None:
        selected = readiness.selected_candidate
        cycle.readiness_status = readiness.readiness_status
        cycle.readiness_json = readiness.model_dump(mode="json")
        if selected is not None:
            cycle.selected_model_run_id = selected.model_run_id
            cycle.selected_model_run_ids_json = selected.model_run_ids
        self.db.commit()
        self.db.refresh(cycle)

    @staticmethod
    def _readiness_block_message(
        request: LivePaperCycleRunRequest,
        readiness: LivePaperReadinessResponse,
    ) -> str | None:
        if readiness.readiness_status == "not_ready" and not request.allow_not_ready:
            return "Readiness status did not meet manual cycle requirements"
        if readiness.readiness_status == "warning" and not request.allow_readiness_warning:
            return "Readiness warning status requires explicit allowance"
        return None

    def _block_cycle(
        self,
        cycle: PaperLiveCycleRun,
        *,
        request: LivePaperCycleRunRequest,
        readiness: LivePaperReadinessResponse | None,
        message: str,
    ) -> LivePaperCycleRunResponse:
        warning = {"message": message, "details": {}}
        cycle.status = "blocked"
        cycle.finished_at = datetime.now(timezone.utc)
        cycle.warnings_json = [*cycle.warnings_json, warning]
        cycle.summary_json = {
            "readiness_status": None if readiness is None else readiness.readiness_status,
            "blocked_reason": message,
        }
        self.db.commit()
        self.db.refresh(cycle)
        return self._response(
            cycle,
            request=request,
            readiness=readiness,
            portfolio=None,
            mark_result=None,
            rebalance_result=None,
        )

    def _resolve_portfolio(
        self,
        request: LivePaperCycleRunRequest,
        *,
        cycle: PaperLiveCycleRun,
        selected_model_run_id: int,
    ) -> PaperPortfolio:
        paper_service = PaperTradingService(self.db)
        if request.portfolio_id is not None:
            return paper_service.get_portfolio(request.portfolio_id)

        name = (
            request.portfolio_name.strip()
            if request.portfolio_name is not None
            else f"Live Paper Manual Cycle {cycle.id}"
        )
        return paper_service.create_portfolio(
            PaperPortfolioCreate(
                name=name,
                description=request.portfolio_description,
                initial_capital=request.readiness.virtual_initial_capital,
                base_currency="RUB",
                model_run_id=selected_model_run_id,
            )
        )

    @staticmethod
    def _rebalance_request(
        request: LivePaperCycleRunRequest,
        *,
        selected_model_run_id: int,
    ) -> PaperPortfolioRebalanceRequest:
        payload = request.rebalance.model_dump()
        payload["model_run_id"] = selected_model_run_id
        payload["as_of_date"] = request.as_of_date or request.rebalance.as_of_date
        return PaperPortfolioRebalanceRequest(**payload)

    def _complete_cycle(
        self,
        cycle: PaperLiveCycleRun,
        readiness: LivePaperReadinessResponse,
        rebalance_result: PaperPortfolioRebalanceResult,
    ) -> None:
        cycle.status = "completed"
        cycle.finished_at = datetime.now(timezone.utc)
        cycle.summary_json = {
            "portfolio_id": rebalance_result.portfolio.id,
            "selected_model_run_id": cycle.selected_model_run_id,
            "readiness_status": readiness.readiness_status,
            "rebalance_as_of_date": rebalance_result.snapshot.as_of_date.isoformat(),
            "selected_position_count": len(rebalance_result.selected_positions),
            "turnover": str(rebalance_result.turnover),
            "fee_amount": str(rebalance_result.fee_amount),
            "snapshot_id": rebalance_result.snapshot.id,
            "portfolio_value": str(rebalance_result.snapshot.portfolio_value),
        }
        self.db.commit()
        self.db.refresh(cycle)

    def _fail_cycle(
        self,
        cycle_id: int,
        *,
        request: LivePaperCycleRunRequest,
        readiness: LivePaperReadinessResponse | None,
        portfolio: PaperPortfolio | None,
        mark_result: PaperPortfolioMarkPeriodResult | None,
        error: dict[str, Any],
    ) -> LivePaperCycleRunResponse:
        self.db.rollback()
        cycle = self.get_run(cycle_id)
        cycle.status = "failed"
        cycle.finished_at = datetime.now(timezone.utc)
        cycle.errors_json = [*cycle.errors_json, error]
        cycle.summary_json = {
            **cycle.summary_json,
            "failure_detail": error.get("detail"),
        }
        self.db.commit()
        self.db.refresh(cycle)
        return self._response(
            cycle,
            request=request,
            readiness=readiness,
            portfolio=portfolio,
            mark_result=mark_result,
            rebalance_result=None,
        )

    def _stored_response(
        self,
        cycle: PaperLiveCycleRun,
        *,
        request: LivePaperCycleRunRequest,
        extra_warnings: list[dict[str, Any]] | None = None,
    ) -> LivePaperCycleRunResponse:
        readiness = self._readiness_from_json(cycle.readiness_json)
        mark_result = (
            self._mark_result_from_json(cycle.mark_period_result_json)
            if request.include_mark_period_result
            else None
        )
        rebalance_result = (
            self._rebalance_result_from_json(cycle.rebalance_result_json)
            if request.include_rebalance_result
            else None
        )
        portfolio = self.db.get(PaperPortfolio, cycle.portfolio_id) if cycle.portfolio_id else None
        return self._response(
            cycle,
            request=request,
            readiness=readiness,
            portfolio=portfolio,
            mark_result=mark_result,
            rebalance_result=rebalance_result,
            extra_warnings=extra_warnings,
        )

    def _response(
        self,
        cycle: PaperLiveCycleRun,
        *,
        request: LivePaperCycleRunRequest,
        readiness: LivePaperReadinessResponse | None,
        portfolio: PaperPortfolio | PaperPortfolioRead | None,
        mark_result: PaperPortfolioMarkPeriodResult | None,
        rebalance_result: PaperPortfolioRebalanceResult | None,
        extra_warnings: list[dict[str, Any]] | None = None,
    ) -> LivePaperCycleRunResponse:
        if isinstance(portfolio, PaperPortfolioRead):
            portfolio_read = portfolio
        elif portfolio is None:
            portfolio_read = None
        else:
            portfolio_read = PaperPortfolioRead.model_validate(portfolio)

        warnings = [*cycle.warnings_json, *(extra_warnings or [])]
        return LivePaperCycleRunResponse(
            cycle=LivePaperCycleRunRead.model_validate(cycle),
            readiness=readiness if request.include_readiness_report else None,
            portfolio=portfolio_read,
            mark_period_result=(
                mark_result if request.include_mark_period_result else None
            ),
            rebalance_result=(
                rebalance_result if request.include_rebalance_result else None
            ),
            warnings=warnings,
            errors=cycle.errors_json,
        )

    @staticmethod
    def _readiness_from_json(
        payload: dict[str, Any] | None,
    ) -> LivePaperReadinessResponse | None:
        if not payload:
            return None
        return LivePaperReadinessResponse.model_validate(payload)

    @staticmethod
    def _mark_result_from_json(
        payload: dict[str, Any] | None,
    ) -> PaperPortfolioMarkPeriodResult | None:
        if payload is None:
            return None
        return PaperPortfolioMarkPeriodResult.model_validate(payload)

    @staticmethod
    def _rebalance_result_from_json(
        payload: dict[str, Any] | None,
    ) -> PaperPortfolioRebalanceResult | None:
        if payload is None:
            return None
        return PaperPortfolioRebalanceResult.model_validate(payload)

    @staticmethod
    def _normalized_key(value: str | None) -> str | None:
        return None if value is None else value.strip()
