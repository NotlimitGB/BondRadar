from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_return_label import BondReturnLabel
from app.models.ml_model_run import MLModelRun
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.paper_trading import (
    PaperPortfolioCreate,
    PaperPortfolioMarkPeriodRequest,
    PaperPortfolioMarkPeriodResult,
    PaperPortfolioPositionRead,
    PaperPortfolioRead,
    PaperPortfolioRebalanceRequest,
    PaperPortfolioRebalanceResult,
    PaperPortfolioSnapshotRead,
    PaperPortfolioTransactionRead,
    PaperTradingWarning,
)
from app.schemas.portfolio_construction import PortfolioConstructionRequest
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.paper_trading_risk_policy import (
    paper_risk_policy_payload,
    risk_override_metadata,
    risk_override_warning,
    validate_paper_risk_policy,
)
from app.services.portfolio_construction_service import PortfolioConstructionService


EVALUABLE_LABELS = {"positive_return", "negative_return"}


class PaperTradingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_portfolio(self, request: PaperPortfolioCreate) -> PaperPortfolio:
        name = request.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="portfolio name is required",
            )
        if request.initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="initial_capital must be positive",
            )

        model_run = None
        return_method = None
        horizon_days = None
        if request.model_run_id is not None:
            model_run = self._load_completed_model_run(request.model_run_id)
            return_method = self._return_method(model_run)
            horizon_days = model_run.horizon_days

        portfolio = PaperPortfolio(
            name=name,
            description=request.description,
            status="active",
            base_currency=request.base_currency,
            initial_capital=request.initial_capital,
            cash_balance=request.initial_capital,
            current_value=request.initial_capital,
            model_run_id=None if model_run is None else model_run.id,
            return_method=return_method,
            horizon_days=horizon_days,
            params_json=self._jsonable({"model_run_id": request.model_run_id}),
            summary_json={},
            warnings_json=[],
        )
        self.db.add(portfolio)
        self.db.flush()
        self._add_transaction(
            portfolio_id=portfolio.id,
            bond_id=None,
            transaction_type="portfolio_created",
            as_of_date=date.today(),
            amount_delta=request.initial_capital,
            weight_delta=None,
            fee_amount=None,
            value_before=Decimal("0"),
            value_after=request.initial_capital,
            details={"base_currency": request.base_currency},
        )
        self._create_or_update_snapshot(portfolio, date.today(), warnings=[])
        self.db.commit()
        self.db.refresh(portfolio)
        return portfolio

    def list_portfolios(self, *, limit: int = 100) -> list[PaperPortfolio]:
        return list(
            self.db.execute(
                select(PaperPortfolio)
                .order_by(PaperPortfolio.id.desc())
                .limit(max(1, min(limit, 500)))
            ).scalars()
        )

    def get_portfolio(self, portfolio_id: int) -> PaperPortfolio:
        portfolio = self.db.get(PaperPortfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Paper portfolio not found",
            )
        return portfolio

    def list_positions(self, portfolio_id: int) -> list[PaperPortfolioPosition]:
        self.get_portfolio(portfolio_id)
        return list(
            self.db.execute(
                select(PaperPortfolioPosition)
                .where(PaperPortfolioPosition.portfolio_id == portfolio_id)
                .order_by(
                    PaperPortfolioPosition.is_active.desc(),
                    PaperPortfolioPosition.id.asc(),
                )
            ).scalars()
        )

    def list_transactions(self, portfolio_id: int) -> list[PaperPortfolioTransaction]:
        self.get_portfolio(portfolio_id)
        return list(
            self.db.execute(
                select(PaperPortfolioTransaction)
                .where(PaperPortfolioTransaction.portfolio_id == portfolio_id)
                .order_by(
                    PaperPortfolioTransaction.as_of_date.asc(),
                    PaperPortfolioTransaction.id.asc(),
                )
            ).scalars()
        )

    def list_snapshots(self, portfolio_id: int) -> list[PaperPortfolioSnapshot]:
        self.get_portfolio(portfolio_id)
        return list(
            self.db.execute(
                select(PaperPortfolioSnapshot)
                .where(PaperPortfolioSnapshot.portfolio_id == portfolio_id)
                .order_by(PaperPortfolioSnapshot.as_of_date.asc())
            ).scalars()
        )

    def rebalance(
        self,
        portfolio_id: int,
        request: PaperPortfolioRebalanceRequest,
    ) -> PaperPortfolioRebalanceResult:
        portfolio = self.get_portfolio(portfolio_id)
        self._ensure_active(portfolio)
        model_run_id = request.model_run_id or portfolio.model_run_id
        if model_run_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML model run is required",
            )
        model_run = self._load_completed_model_run(model_run_id)
        if request.transaction_cost_rate < 0 or request.transaction_cost_rate > Decimal("0.1"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="transaction_cost_rate must be between 0 and 0.1",
            )
        validate_paper_risk_policy(request)

        construction = PortfolioConstructionService(self.db).construct(
            PortfolioConstructionRequest(
                model_run_id=model_run.id,
                as_of_date=request.as_of_date,
                capital=portfolio.current_value,
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
                include_excluded_candidates=request.include_excluded_candidates,
            )
        )

        active_positions = self._active_positions(portfolio.id)
        previous_weight = {
            position.bond_id: position.allocation_weight for position in active_positions
        }
        previous_by_bond = {position.bond_id: position for position in active_positions}
        target_weight = {
            candidate.bond_id: candidate.allocation_weight
            for candidate in construction.selected_candidates
        }
        turnover = self._turnover(previous_weight, target_weight)
        value_before = portfolio.current_value
        fee_amount = value_before * turnover * request.transaction_cost_rate
        investable_value = max(value_before - fee_amount, Decimal("0"))

        if fee_amount > 0:
            self._add_transaction(
                portfolio_id=portfolio.id,
                bond_id=None,
                transaction_type="rebalance_fee",
                as_of_date=construction.as_of_date,
                amount_delta=-fee_amount,
                weight_delta=None,
                fee_amount=fee_amount,
                value_before=value_before,
                value_after=value_before - fee_amount,
                details={"turnover": turnover, "transaction_cost_rate": request.transaction_cost_rate},
            )

        selected_bond_ids = set(target_weight)
        selected_positions: list[PaperPortfolioPosition] = []
        for candidate in construction.selected_candidates:
            previous = previous_by_bond.get(candidate.bond_id)
            old_weight = Decimal("0") if previous is None else previous.allocation_weight
            old_amount = Decimal("0") if previous is None else previous.current_amount
            new_amount = investable_value * candidate.allocation_weight
            weight_delta = candidate.allocation_weight - old_weight
            amount_delta = new_amount - old_amount
            position = self._upsert_position(
                portfolio=portfolio,
                candidate=candidate,
                model_run_id=model_run.id,
                as_of_date=construction.as_of_date,
                allocation_amount=new_amount,
            )
            selected_positions.append(position)
            if weight_delta > 0:
                tx_type = "allocation_increase"
            elif weight_delta < 0:
                tx_type = "allocation_decrease"
            else:
                tx_type = ""
            if tx_type:
                self._add_transaction(
                    portfolio_id=portfolio.id,
                    bond_id=candidate.bond_id,
                    transaction_type=tx_type,
                    as_of_date=construction.as_of_date,
                    amount_delta=amount_delta,
                    weight_delta=weight_delta,
                    fee_amount=None,
                    value_before=value_before,
                    value_after=None,
                    details={
                        "probability_positive": candidate.probability_positive,
                        "allocation_weight": candidate.allocation_weight,
                    },
                )

        for position in active_positions:
            if position.bond_id in selected_bond_ids:
                continue
            old_amount = position.current_amount
            old_weight = position.allocation_weight
            position.is_active = False
            position.allocation_weight = Decimal("0")
            position.allocation_amount = Decimal("0")
            position.current_amount = Decimal("0")
            self._add_transaction(
                portfolio_id=portfolio.id,
                bond_id=position.bond_id,
                transaction_type="allocation_removed",
                as_of_date=construction.as_of_date,
                amount_delta=-old_amount,
                weight_delta=-old_weight,
                fee_amount=None,
                value_before=value_before,
                value_after=None,
                details={},
            )

        allocated_total = sum(
            (position.current_amount for position in selected_positions),
            Decimal("0"),
        )
        portfolio.cash_balance = investable_value - allocated_total
        portfolio.current_value = portfolio.cash_balance + allocated_total
        portfolio.model_run_id = model_run.id
        portfolio.return_method = construction.return_method
        portfolio.horizon_days = construction.horizon_days
        portfolio.last_rebalanced_at = datetime.now(timezone.utc)
        portfolio.last_rebalance_as_of_date = construction.as_of_date
        construction_summary = self._jsonable(construction.summary.model_dump())
        risk_policy = self._jsonable(paper_risk_policy_payload(request))
        risk_override = self._jsonable(risk_override_metadata(request))
        portfolio.params_json = self._jsonable(
            {
                "last_rebalance_request": request.model_dump(),
                "risk_policy": risk_policy,
                "risk_override": risk_override,
            }
        )
        portfolio.summary_json = self._jsonable(
            {
                "construction_summary": construction_summary,
                "risk_policy": risk_policy,
                "risk_override": risk_override,
            }
        )
        warning_payloads = [warning.model_dump() for warning in construction.warnings]
        override_warning = risk_override_warning(request)
        if override_warning is not None:
            warning_payloads.append(override_warning)
        portfolio.warnings_json = self._jsonable(warning_payloads)
        self.db.flush()
        snapshot = self._create_or_update_snapshot(
            portfolio,
            construction.as_of_date,
            warnings=[warning.model_dump() for warning in construction.warnings],
        )
        self.db.flush()
        self.db.commit()
        self.db.refresh(portfolio)
        self.db.refresh(snapshot)
        for position in selected_positions:
            self.db.refresh(position)

        return PaperPortfolioRebalanceResult(
            portfolio=PaperPortfolioRead.model_validate(portfolio),
            snapshot=PaperPortfolioSnapshotRead.model_validate(snapshot),
            selected_positions=[
                PaperPortfolioPositionRead.model_validate(position)
                for position in selected_positions
            ],
            excluded_candidates=self._jsonable(
                [candidate.model_dump() for candidate in construction.excluded_candidates]
            ),
            turnover=turnover,
            fee_amount=fee_amount,
            construction_summary=construction_summary,
            warnings=[
                PaperTradingWarning(
                    message=warning.message,
                    bond_id=warning.bond_id,
                    as_of_date=warning.as_of_date,
                    details=warning.details,
                )
                for warning in construction.warnings
            ]
            + (
                []
                if override_warning is None
                else [
                    PaperTradingWarning(
                        message=override_warning["message"],
                        details=override_warning["details"],
                    )
                ]
            ),
        )

    def mark_period(
        self,
        portfolio_id: int,
        request: PaperPortfolioMarkPeriodRequest,
    ) -> PaperPortfolioMarkPeriodResult:
        portfolio = self.get_portfolio(portfolio_id)
        self._ensure_active(portfolio)
        active_positions = self._active_positions(portfolio.id)
        if not active_positions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paper portfolio has no active positions",
            )
        as_of_date = request.as_of_date or portfolio.last_rebalance_as_of_date
        if as_of_date is None or portfolio.horizon_days is None or portfolio.return_method is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML model run is required",
            )

        labels = self._labels_by_bond(
            [position.bond_id for position in active_positions],
            as_of_date,
            portfolio.horizon_days,
            portfolio.return_method,
        )
        missing = [
            position.bond_id
            for position in active_positions
            if position.bond_id not in labels
        ]
        if missing and not request.allow_partial:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing realized labels for active positions",
            )

        warnings = [
            PaperTradingWarning(
                message="Missing realized label for active position",
                bond_id=bond_id,
                as_of_date=as_of_date,
            )
            for bond_id in missing
        ]
        transactions: list[PaperPortfolioTransaction] = []
        value_before = portfolio.current_value
        for position in active_positions:
            label = labels.get(position.bond_id)
            if label is None:
                continue
            old_amount = position.current_amount
            new_amount = old_amount * (Decimal("1") + label.future_return)
            amount_delta = new_amount - old_amount
            position.current_amount = new_amount
            transaction = self._add_transaction(
                portfolio_id=portfolio.id,
                bond_id=position.bond_id,
                transaction_type="period_return",
                as_of_date=as_of_date,
                amount_delta=amount_delta,
                weight_delta=None,
                fee_amount=None,
                value_before=value_before,
                value_after=None,
                details={
                    "future_return": label.future_return,
                    "label_id": label.id,
                    "return_method": portfolio.return_method,
                },
            )
            transactions.append(transaction)

        allocated_total = sum(
            (position.current_amount for position in active_positions),
            Decimal("0"),
        )
        portfolio.current_value = portfolio.cash_balance + allocated_total
        portfolio.last_marked_at = datetime.now(timezone.utc)
        snapshot_date = as_of_date + timedelta(days=portfolio.horizon_days)
        snapshot = self._create_or_update_snapshot(
            portfolio,
            snapshot_date,
            warnings=[warning.model_dump() for warning in warnings],
        )
        self.db.flush()
        for transaction in transactions:
            transaction.portfolio_value_after = portfolio.current_value
        self.db.commit()
        self.db.refresh(portfolio)
        self.db.refresh(snapshot)
        for position in active_positions:
            self.db.refresh(position)
        for transaction in transactions:
            self.db.refresh(transaction)

        return PaperPortfolioMarkPeriodResult(
            portfolio=PaperPortfolioRead.model_validate(portfolio),
            snapshot=PaperPortfolioSnapshotRead.model_validate(snapshot),
            updated_positions=[
                PaperPortfolioPositionRead.model_validate(position)
                for position in active_positions
            ],
            transactions=[
                PaperPortfolioTransactionRead.model_validate(transaction)
                for transaction in transactions
            ],
            warnings=warnings,
        )

    def _upsert_position(
        self,
        *,
        portfolio: PaperPortfolio,
        candidate: Any,
        model_run_id: int,
        as_of_date: date,
        allocation_amount: Decimal,
    ) -> PaperPortfolioPosition:
        position = self.db.execute(
            select(PaperPortfolioPosition).where(
                PaperPortfolioPosition.portfolio_id == portfolio.id,
                PaperPortfolioPosition.bond_id == candidate.bond_id,
            )
        ).scalar_one_or_none()
        if position is None:
            position = PaperPortfolioPosition(
                portfolio_id=portfolio.id,
                bond_id=candidate.bond_id,
                company_id=candidate.company_id,
                as_of_date=as_of_date,
                allocation_weight=candidate.allocation_weight,
                allocation_amount=allocation_amount,
                current_amount=allocation_amount,
                probability_positive=candidate.probability_positive,
                predicted_label=candidate.predicted_label,
                yield_to_maturity=candidate.yield_to_maturity,
                liquidity_score=candidate.liquidity_score,
                decision_status=candidate.decision_status,
                risk_level=candidate.risk_level,
                is_active=True,
                source_model_run_id=model_run_id,
                source_prediction_id=None,
                source_details_json=self._jsonable(
                    {
                        "selection_reasons": candidate.selection_reasons,
                        "risk_notes": candidate.risk_notes,
                    }
                ),
            )
            self.db.add(position)
            return position

        position.company_id = candidate.company_id
        position.as_of_date = as_of_date
        position.allocation_weight = candidate.allocation_weight
        position.allocation_amount = allocation_amount
        position.current_amount = allocation_amount
        position.probability_positive = candidate.probability_positive
        position.predicted_label = candidate.predicted_label
        position.yield_to_maturity = candidate.yield_to_maturity
        position.liquidity_score = candidate.liquidity_score
        position.decision_status = candidate.decision_status
        position.risk_level = candidate.risk_level
        position.is_active = True
        position.source_model_run_id = model_run_id
        position.source_prediction_id = None
        position.source_details_json = self._jsonable(
            {
                "selection_reasons": candidate.selection_reasons,
                "risk_notes": candidate.risk_notes,
            }
        )
        return position

    def _create_or_update_snapshot(
        self,
        portfolio: PaperPortfolio,
        snapshot_date: date,
        *,
        warnings: list[dict[str, Any]],
    ) -> PaperPortfolioSnapshot:
        positions = list(
            self.db.execute(
                select(PaperPortfolioPosition).where(
                    PaperPortfolioPosition.portfolio_id == portfolio.id
                )
            ).scalars()
        )
        active_positions = [position for position in positions if position.is_active]
        allocated_value = sum(
            (position.current_amount for position in active_positions),
            Decimal("0"),
        )
        portfolio_value = portfolio.cash_balance + allocated_value
        allocated_weight = (
            allocated_value / portfolio_value if portfolio_value > 0 else Decimal("0")
        )
        unallocated_weight = (
            portfolio.cash_balance / portfolio_value if portfolio_value > 0 else Decimal("0")
        )
        cumulative_return = (
            portfolio_value / portfolio.initial_capital - Decimal("1")
            if portfolio.initial_capital > 0
            else Decimal("0")
        )
        previous = self.db.execute(
            select(PaperPortfolioSnapshot)
            .where(
                PaperPortfolioSnapshot.portfolio_id == portfolio.id,
                PaperPortfolioSnapshot.as_of_date < snapshot_date,
            )
            .order_by(PaperPortfolioSnapshot.as_of_date.desc())
        ).scalars().first()
        period_return = Decimal("0")
        if previous is not None and previous.portfolio_value > 0:
            period_return = portfolio_value / previous.portfolio_value - Decimal("1")

        snapshot = self.db.execute(
            select(PaperPortfolioSnapshot).where(
                PaperPortfolioSnapshot.portfolio_id == portfolio.id,
                PaperPortfolioSnapshot.as_of_date == snapshot_date,
            )
        ).scalar_one_or_none()
        payload = {
            "portfolio_value": portfolio_value,
            "cash_balance": portfolio.cash_balance,
            "allocated_value": allocated_value,
            "allocated_weight": allocated_weight,
            "unallocated_weight": unallocated_weight,
            "positions_count": len(positions),
            "active_positions_count": len(active_positions),
            "cumulative_return": cumulative_return,
            "period_return": period_return,
            "metrics_json": self._jsonable({}),
            "warnings_json": self._jsonable(warnings),
        }
        if snapshot is None:
            snapshot = PaperPortfolioSnapshot(
                portfolio_id=portfolio.id,
                as_of_date=snapshot_date,
                **payload,
            )
            self.db.add(snapshot)
        else:
            for key, value in payload.items():
                setattr(snapshot, key, value)
        portfolio.current_value = portfolio_value
        return snapshot

    def _labels_by_bond(
        self,
        bond_ids: list[int],
        as_of_date: date,
        horizon_days: int,
        return_method: str,
    ) -> dict[int, BondReturnLabel]:
        labels = self.db.execute(
            select(BondReturnLabel).where(
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.as_of_date == as_of_date,
                BondReturnLabel.horizon_days == horizon_days,
                BondReturnLabel.return_method == return_method,
                BondReturnLabel.label.in_(EVALUABLE_LABELS),
                BondReturnLabel.label_binary.is_not(None),
                BondReturnLabel.future_return.is_not(None),
            )
        ).scalars()
        return {label.bond_id: label for label in labels}

    def _active_positions(self, portfolio_id: int) -> list[PaperPortfolioPosition]:
        return list(
            self.db.execute(
                select(PaperPortfolioPosition).where(
                    PaperPortfolioPosition.portfolio_id == portfolio_id,
                    PaperPortfolioPosition.is_active.is_(True),
                )
            ).scalars()
        )

    def _load_completed_model_run(self, model_run_id: int) -> MLModelRun:
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

    @staticmethod
    def _return_method(model_run: MLModelRun) -> str:
        return_method = (model_run.params or {}).get("return_method") or "price"
        return return_method if return_method in RETURN_METHODS else "price"

    @staticmethod
    def _ensure_active(portfolio: PaperPortfolio) -> None:
        if portfolio.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paper portfolio is archived",
            )

    def _add_transaction(
        self,
        *,
        portfolio_id: int,
        bond_id: int | None,
        transaction_type: str,
        as_of_date: date,
        amount_delta: Decimal,
        weight_delta: Decimal | None,
        fee_amount: Decimal | None,
        value_before: Decimal | None,
        value_after: Decimal | None,
        details: dict[str, Any],
    ) -> PaperPortfolioTransaction:
        transaction = PaperPortfolioTransaction(
            portfolio_id=portfolio_id,
            bond_id=bond_id,
            transaction_type=transaction_type,
            as_of_date=as_of_date,
            amount_delta=amount_delta,
            weight_delta=weight_delta,
            fee_amount=fee_amount,
            portfolio_value_before=value_before,
            portfolio_value_after=value_after,
            details_json=self._jsonable(details),
        )
        self.db.add(transaction)
        return transaction

    @staticmethod
    def _turnover(
        previous_weights: dict[int, Decimal],
        target_weights: dict[int, Decimal],
    ) -> Decimal:
        bond_ids = set(previous_weights) | set(target_weights)
        return sum(
            abs(target_weights.get(bond_id, Decimal("0")) - previous_weights.get(bond_id, Decimal("0")))
            for bond_id in bond_ids
        )

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._jsonable(item) for key, item in value.items()}
        return value
