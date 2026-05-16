from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import sqrt

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.paper_trading_report import (
    PaperTradingContributionItem,
    PaperTradingContributionsResponse,
    PaperTradingEquityPoint,
    PaperTradingPerformanceMetrics,
    PaperTradingPerformanceResponse,
    PaperTradingReportWarning,
)


ZERO = Decimal("0")


class PaperTradingReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def performance(
        self,
        portfolio_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        include_equity_curve: bool = True,
    ) -> PaperTradingPerformanceResponse:
        self._validate_date_range(date_from, date_to)
        portfolio = self._get_portfolio(portfolio_id)
        snapshots = self._snapshots(portfolio_id, date_from=date_from, date_to=date_to)
        transactions = self._transactions(
            portfolio_id,
            date_from=date_from,
            date_to=date_to,
        )
        positions = self._positions(portfolio_id)
        equity_curve = self._equity_curve_from_snapshots(snapshots)
        warnings = self._snapshot_warnings(snapshots)
        metrics = self._metrics(
            portfolio=portfolio,
            snapshots=snapshots,
            transactions=transactions,
            positions=positions,
            equity_curve=equity_curve,
        )
        return PaperTradingPerformanceResponse(
            portfolio_id=portfolio.id,
            name=portfolio.name,
            status=portfolio.status,
            base_currency=portfolio.base_currency,
            model_run_id=portfolio.model_run_id,
            return_method=portfolio.return_method,
            horizon_days=portfolio.horizon_days,
            date_from=date_from,
            date_to=date_to,
            metrics=metrics,
            equity_curve=equity_curve if include_equity_curve else [],
            warnings=warnings,
        )

    def equity_curve(
        self,
        portfolio_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[PaperTradingEquityPoint]:
        self._validate_date_range(date_from, date_to)
        self._get_portfolio(portfolio_id)
        snapshots = self._snapshots(portfolio_id, date_from=date_from, date_to=date_to)
        return self._equity_curve_from_snapshots(snapshots)

    def contributions(
        self,
        portfolio_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        include_inactive: bool = True,
    ) -> PaperTradingContributionsResponse:
        self._validate_date_range(date_from, date_to)
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 500",
            )
        self._get_portfolio(portfolio_id)
        transactions = self._transactions(
            portfolio_id,
            date_from=date_from,
            date_to=date_to,
        )
        positions_by_bond = {
            position.bond_id: position for position in self._positions(portfolio_id)
        }
        items = self._contribution_items(
            transactions,
            positions_by_bond=positions_by_bond,
            include_inactive=include_inactive,
        )[:limit]
        warnings = []
        if not items:
            warnings.append(
                PaperTradingReportWarning(
                    message="No paper trading transactions found for selected filters"
                )
            )
        return PaperTradingContributionsResponse(
            portfolio_id=portfolio_id,
            date_from=date_from,
            date_to=date_to,
            items=items,
            warnings=warnings,
        )

    def _get_portfolio(self, portfolio_id: int) -> PaperPortfolio:
        portfolio = self.db.get(PaperPortfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Paper portfolio not found",
            )
        return portfolio

    @staticmethod
    def _validate_date_range(date_from: date | None, date_to: date | None) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )

    def _snapshots(
        self,
        portfolio_id: int,
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> list[PaperPortfolioSnapshot]:
        query = select(PaperPortfolioSnapshot).where(
            PaperPortfolioSnapshot.portfolio_id == portfolio_id
        )
        if date_from is not None:
            query = query.where(PaperPortfolioSnapshot.as_of_date >= date_from)
        if date_to is not None:
            query = query.where(PaperPortfolioSnapshot.as_of_date <= date_to)
        query = query.order_by(
            PaperPortfolioSnapshot.as_of_date.asc(),
            PaperPortfolioSnapshot.id.asc(),
        )
        return list(self.db.execute(query).scalars())

    def _transactions(
        self,
        portfolio_id: int,
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> list[PaperPortfolioTransaction]:
        query = select(PaperPortfolioTransaction).where(
            PaperPortfolioTransaction.portfolio_id == portfolio_id
        )
        if date_from is not None:
            query = query.where(PaperPortfolioTransaction.as_of_date >= date_from)
        if date_to is not None:
            query = query.where(PaperPortfolioTransaction.as_of_date <= date_to)
        query = query.order_by(
            PaperPortfolioTransaction.as_of_date.asc(),
            PaperPortfolioTransaction.id.asc(),
        )
        return list(self.db.execute(query).scalars())

    def _positions(self, portfolio_id: int) -> list[PaperPortfolioPosition]:
        return list(
            self.db.execute(
                select(PaperPortfolioPosition).where(
                    PaperPortfolioPosition.portfolio_id == portfolio_id
                )
            ).scalars()
        )

    @staticmethod
    def _equity_curve_from_snapshots(
        snapshots: list[PaperPortfolioSnapshot],
    ) -> list[PaperTradingEquityPoint]:
        points: list[PaperTradingEquityPoint] = []
        peak = ZERO
        for snapshot in snapshots:
            if snapshot.portfolio_value > peak:
                peak = snapshot.portfolio_value
            drawdown = (
                (peak - snapshot.portfolio_value) / peak if peak > 0 else ZERO
            )
            points.append(
                PaperTradingEquityPoint(
                    as_of_date=snapshot.as_of_date,
                    portfolio_value=snapshot.portfolio_value,
                    cash_balance=snapshot.cash_balance,
                    allocated_value=snapshot.allocated_value,
                    allocated_weight=snapshot.allocated_weight,
                    unallocated_weight=snapshot.unallocated_weight,
                    cumulative_return=snapshot.cumulative_return,
                    period_return=snapshot.period_return,
                    drawdown=drawdown,
                    active_positions_count=snapshot.active_positions_count,
                )
            )
        return points

    @staticmethod
    def _snapshot_warnings(
        snapshots: list[PaperPortfolioSnapshot],
    ) -> list[PaperTradingReportWarning]:
        if snapshots:
            return []
        return [
            PaperTradingReportWarning(
                message="No snapshots found for selected paper portfolio and date range"
            )
        ]

    def _metrics(
        self,
        *,
        portfolio: PaperPortfolio,
        snapshots: list[PaperPortfolioSnapshot],
        transactions: list[PaperPortfolioTransaction],
        positions: list[PaperPortfolioPosition],
        equity_curve: list[PaperTradingEquityPoint],
    ) -> PaperTradingPerformanceMetrics:
        active_positions = [position for position in positions if position.is_active]
        inactive_positions = [position for position in positions if not position.is_active]
        latest_snapshot = snapshots[-1] if snapshots else None
        active_amount = sum(
            (position.current_amount for position in active_positions),
            ZERO,
        )
        portfolio_value = portfolio.current_value
        allocated_value = (
            latest_snapshot.allocated_value if latest_snapshot is not None else active_amount
        )
        allocated_weight = (
            latest_snapshot.allocated_weight
            if latest_snapshot is not None
            else (allocated_value / portfolio_value if portfolio_value > 0 else ZERO)
        )
        unallocated_weight = (
            latest_snapshot.unallocated_weight
            if latest_snapshot is not None
            else (portfolio.cash_balance / portfolio_value if portfolio_value > 0 else ZERO)
        )
        period_returns = [
            snapshot.period_return
            for snapshot in snapshots
            if snapshot.period_return is not None
        ]
        return PaperTradingPerformanceMetrics(
            snapshot_count=len(snapshots),
            transaction_count=len(transactions),
            initial_capital=portfolio.initial_capital,
            current_value=portfolio.current_value,
            cash_balance=portfolio.cash_balance,
            allocated_value=allocated_value,
            cumulative_return=self._cumulative_return(portfolio),
            annualized_return=self._annualized_return(snapshots),
            max_drawdown=max((point.drawdown for point in equity_curve), default=ZERO),
            volatility=self._volatility(period_returns),
            average_period_return=self._average(period_returns),
            positive_period_ratio=self._positive_period_ratio(period_returns),
            negative_period_count=sum(period_return < 0 for period_return in period_returns),
            total_fee_amount=self._total_fee_amount(transactions),
            total_period_return_amount=self._sum_by_type(
                transactions, "period_return"
            ),
            total_allocation_increase_amount=self._sum_by_type(
                transactions, "allocation_increase"
            ),
            total_allocation_decrease_amount=abs(
                self._sum_by_type(transactions, "allocation_decrease")
            ),
            total_removed_amount=abs(
                self._sum_by_type(transactions, "allocation_removed")
            ),
            current_allocated_weight=allocated_weight,
            current_unallocated_weight=unallocated_weight,
            active_positions_count=len(active_positions),
            inactive_positions_count=len(inactive_positions),
        )

    @staticmethod
    def _cumulative_return(portfolio: PaperPortfolio) -> Decimal:
        if portfolio.initial_capital <= 0:
            return ZERO
        return portfolio.current_value / portfolio.initial_capital - Decimal("1")

    @staticmethod
    def _annualized_return(
        snapshots: list[PaperPortfolioSnapshot],
    ) -> Decimal | None:
        if len(snapshots) < 2:
            return None
        first = snapshots[0]
        last = snapshots[-1]
        days = (last.as_of_date - first.as_of_date).days
        if days <= 0 or first.portfolio_value <= 0:
            return None
        total_return = last.portfolio_value / first.portfolio_value - Decimal("1")
        if total_return <= Decimal("-1"):
            return None
        return Decimal(str((1 + float(total_return)) ** (365 / days) - 1))

    @staticmethod
    def _volatility(period_returns: list[Decimal]) -> Decimal | None:
        if len(period_returns) < 2:
            return None
        mean_return = sum(period_returns) / Decimal(len(period_returns))
        variance = sum(
            (period_return - mean_return) ** 2 for period_return in period_returns
        ) / Decimal(len(period_returns))
        return Decimal(str(sqrt(float(variance))))

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values) / Decimal(len(values))

    @staticmethod
    def _positive_period_ratio(period_returns: list[Decimal]) -> Decimal | None:
        if not period_returns:
            return None
        return Decimal(sum(period_return > 0 for period_return in period_returns)) / Decimal(
            len(period_returns)
        )

    @staticmethod
    def _sum_by_type(
        transactions: list[PaperPortfolioTransaction],
        transaction_type: str,
    ) -> Decimal:
        return sum(
            (
                transaction.amount_delta
                for transaction in transactions
                if transaction.transaction_type == transaction_type
            ),
            ZERO,
        )

    @staticmethod
    def _total_fee_amount(
        transactions: list[PaperPortfolioTransaction],
    ) -> Decimal:
        total = ZERO
        for transaction in transactions:
            if transaction.transaction_type != "rebalance_fee":
                continue
            total += (
                transaction.fee_amount
                if transaction.fee_amount is not None
                else abs(transaction.amount_delta)
            )
        return total

    def _contribution_items(
        self,
        transactions: list[PaperPortfolioTransaction],
        *,
        positions_by_bond: dict[int, PaperPortfolioPosition],
        include_inactive: bool,
    ) -> list[PaperTradingContributionItem]:
        grouped: dict[int | None, list[PaperPortfolioTransaction]] = defaultdict(list)
        for transaction in transactions:
            grouped[transaction.bond_id].append(transaction)

        bond_ids = [bond_id for bond_id in grouped if bond_id is not None]
        bonds = self._bonds_by_id(bond_ids)
        companies = self._companies_by_id(
            [
                bond.company_id
                for bond in bonds.values()
                if bond.company_id is not None
            ]
        )

        items: list[PaperTradingContributionItem] = []
        for bond_id, rows in grouped.items():
            position = positions_by_bond.get(bond_id) if bond_id is not None else None
            if position is not None and not include_inactive and not position.is_active:
                continue
            item = self._contribution_item(
                bond_id=bond_id,
                rows=rows,
                position=position,
                bond=bonds.get(bond_id) if bond_id is not None else None,
                companies=companies,
            )
            if bond_id is None and item.fee_amount == 0 and item.net_amount_delta == 0:
                continue
            items.append(item)

        return sorted(
            items,
            key=lambda item: (
                -abs(item.net_amount_delta),
                item.bond_id is None,
                item.bond_id or 0,
            ),
        )

    @staticmethod
    def _contribution_item(
        *,
        bond_id: int | None,
        rows: list[PaperPortfolioTransaction],
        position: PaperPortfolioPosition | None,
        bond: Bond | None,
        companies: dict[int, Company],
    ) -> PaperTradingContributionItem:
        company_id = None
        if position is not None:
            company_id = position.company_id
        elif bond is not None:
            company_id = bond.company_id
        company = companies.get(company_id) if company_id is not None else None
        period_return_amount = PaperTradingReportService._sum_by_type(
            rows, "period_return"
        )
        allocation_increase_amount = PaperTradingReportService._sum_by_type(
            rows, "allocation_increase"
        )
        allocation_decrease_amount = abs(
            PaperTradingReportService._sum_by_type(rows, "allocation_decrease")
        )
        removed_amount = abs(
            PaperTradingReportService._sum_by_type(rows, "allocation_removed")
        )
        fee_amount = PaperTradingReportService._total_fee_amount(rows)
        return PaperTradingContributionItem(
            bond_id=bond_id,
            bond_name=None if bond is None else bond.name,
            isin=None if bond is None else bond.isin,
            secid=None if bond is None else bond.secid,
            company_id=company_id,
            company_name=None if company is None else company.name,
            period_return_amount=period_return_amount,
            allocation_increase_amount=allocation_increase_amount,
            allocation_decrease_amount=allocation_decrease_amount,
            removed_amount=removed_amount,
            fee_amount=fee_amount,
            net_amount_delta=sum((row.amount_delta for row in rows), ZERO),
            transaction_count=len(rows),
            current_amount=None if position is None else position.current_amount,
            current_weight=None if position is None else position.allocation_weight,
            is_active=None if position is None else position.is_active,
        )

    def _bonds_by_id(self, bond_ids: list[int]) -> dict[int, Bond]:
        if not bond_ids:
            return {}
        return {
            bond.id: bond
            for bond in self.db.execute(
                select(Bond).where(Bond.id.in_(bond_ids))
            ).scalars()
        }

    def _companies_by_id(self, company_ids: list[int]) -> dict[int, Company]:
        if not company_ids:
            return {}
        return {
            company.id: company
            for company in self.db.execute(
                select(Company).where(Company.id.in_(company_ids))
            ).scalars()
        }
