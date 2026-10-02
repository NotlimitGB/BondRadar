"""Read-only Strategy -> narrow terms -> integer-lot planning orchestration."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.schemas.portfolio_strategy import PortfolioStrategyRequest
from app.services.portfolio_strategy_snapshot_service import PortfolioStrategySnapshotService
from app.services.shadow_execution_terms_loader import ShadowExecutionTermsLoader
from app.services.shadow_execution_planner import ShadowExecutionPlanner, validate_strategy


class ShadowExecutionSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    def build(self, bond_ids: Sequence[int], as_of_date: date, capital_rub: Decimal, *,
              market_source: str = "moex", max_market_age_days: int = 7, max_curve_age_days: int = 7,
              liquidity_lookback_calendar_days: int = 30, liquidity_min_observation_days: int = 5):
        request = PortfolioStrategyRequest(capital_rub=capital_rub)
        if not isinstance(bond_ids,Sequence) or isinstance(bond_ids,(str,bytes,bytearray)):
            raise ValueError("Explicit deterministic bond IDs required")
        ids = tuple(bond_ids)
        if not ids or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Unique positive bond IDs required")
        if type(as_of_date) is not date or type(market_source) is not str or market_source != "moex":
            raise ValueError("Exact market context required")
        if any(type(age) is not int or age < 0 for age in (max_market_age_days,max_curve_age_days)):
            raise ValueError("Nonnegative ages required")
        if (type(liquidity_lookback_calendar_days) is not int or type(liquidity_min_observation_days) is not int or
                not 0 < liquidity_min_observation_days <= liquidity_lookback_calendar_days):
            raise ValueError("Valid liquidity window required")
        try: as_of_date-timedelta(days=liquidity_lookback_calendar_days-1)
        except (ValueError,OverflowError): raise ValueError("Unrepresentable liquidity window") from None
        ids = tuple(sorted(ids))
        with self.db.no_autoflush:
            strategy = PortfolioStrategySnapshotService(self.db).build(ids,as_of_date,request.capital_rub,
                market_source=market_source,max_market_age_days=max_market_age_days,max_curve_age_days=max_curve_age_days,
                liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
                liquidity_min_observation_days=liquidity_min_observation_days)
            validate_strategy(strategy)
            source = strategy.source_investment_batch.source_batch
            if (strategy.as_of_date != as_of_date or strategy.market_source != market_source or strategy.request != request or
                source.requested_bond_ids != ids or source.max_market_age_days != max_market_age_days or
                source.max_curve_age_days != max_curve_age_days or source.liquidity_lookback_calendar_days != liquidity_lookback_calendar_days or
                source.liquidity_min_observation_days != liquidity_min_observation_days):
                raise ValueError("Strategy loader returned mismatched request context")
            terms = ShadowExecutionTermsLoader(self.db).build(strategy)
            return ShadowExecutionPlanner.build(strategy,terms)
