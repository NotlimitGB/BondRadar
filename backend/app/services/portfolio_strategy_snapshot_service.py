"""One modern Investment load, candidate risk reduction, and pure strategy build."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.schemas.portfolio_strategy import PortfolioStrategyRequest, PortfolioStrategyView
from app.services.investment_model_snapshot_service import InvestmentModelSnapshotService
from app.services.risk_candidate_reducer import RiskCandidateReducer, validate_investment_batch
from app.services.portfolio_strategy_builder import PortfolioStrategyBuilder


class PortfolioStrategySnapshotService:
    def __init__(self, db: Session):
        self.db = db

    def build(self, bond_ids: Sequence[int], as_of_date: date, capital_rub: Decimal, *,
              market_source: str = "moex", max_market_age_days: int = 7, max_curve_age_days: int = 7,
              liquidity_lookback_calendar_days: int = 30,
              liquidity_min_observation_days: int = 5) -> PortfolioStrategyView:
        request = PortfolioStrategyRequest(capital_rub=capital_rub)
        if not isinstance(bond_ids, Sequence) or isinstance(bond_ids, (str, bytes, bytearray)):
            raise ValueError("Explicit deterministic bond IDs required")
        ids = tuple(bond_ids)
        if not ids or any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Unique positive bond IDs required")
        if type(as_of_date) is not date or type(market_source) is not str or market_source != "moex":
            raise ValueError("Exact market context required")
        if any(type(age) is not int or age < 0 for age in (max_market_age_days, max_curve_age_days)):
            raise ValueError("Nonnegative ages required")
        if (type(liquidity_lookback_calendar_days) is not int or type(liquidity_min_observation_days) is not int or
                not 0 < liquidity_min_observation_days <= liquidity_lookback_calendar_days):
            raise ValueError("Valid liquidity window required")
        try: as_of_date - timedelta(days=liquidity_lookback_calendar_days-1)
        except (ValueError, OverflowError): raise ValueError("Unrepresentable liquidity window") from None
        ids = tuple(sorted(ids))
        with self.db.no_autoflush:
            investment = InvestmentModelSnapshotService(self.db).build(ids, as_of_date,
                market_source=market_source, max_market_age_days=max_market_age_days,
                max_curve_age_days=max_curve_age_days,
                liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
                liquidity_min_observation_days=liquidity_min_observation_days)
            validate_investment_batch(investment)
            source = investment.source_batch
            if (source.requested_bond_ids != ids or source.as_of_date != as_of_date or source.market_source != market_source or
                    source.max_market_age_days != max_market_age_days or source.max_curve_age_days != max_curve_age_days or
                    source.liquidity_lookback_calendar_days != liquidity_lookback_calendar_days or
                    source.liquidity_min_observation_days != liquidity_min_observation_days):
                raise ValueError("Investment loader returned mismatched request context")
            risk = RiskCandidateReducer.build(investment)
            return PortfolioStrategyBuilder.build(investment, risk, request)
