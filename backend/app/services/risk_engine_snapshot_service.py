"""Single read-only Investment Model load and constraint delegation."""

from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.investment_model_snapshot_service import InvestmentModelSnapshotService
from app.services.risk_candidate_reducer import RiskCandidateReducer, validate_investment_batch
from app.services.portfolio_risk_evaluator import PortfolioRiskEvaluator, validate_proposal


class RiskEngineSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    def build(self, bond_ids: Sequence[int], as_of_date: date, proposed_portfolio, *, market_source: str = "moex",
              max_market_age_days: int = 7, max_curve_age_days: int = 7,
              liquidity_lookback_calendar_days: int = 30, liquidity_min_observation_days: int = 5):
        proposal = validate_proposal(proposed_portfolio)
        if not isinstance(bond_ids, Sequence) or isinstance(bond_ids, (str,bytes,bytearray)):
            raise ValueError("Explicit deterministic bond IDs required")
        ids = tuple(bond_ids)
        if not ids or any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Unique positive bond IDs required")
        if type(as_of_date) is not date or type(market_source) is not str or market_source != "moex":
            raise ValueError("Exact market context required")
        if any(type(age) is not int or age < 0 for age in (max_market_age_days,max_curve_age_days)):
            raise ValueError("Nonnegative ages required")
        if (type(liquidity_lookback_calendar_days) is not int or type(liquidity_min_observation_days) is not int or
                not 0 < liquidity_min_observation_days <= liquidity_lookback_calendar_days):
            raise ValueError("Valid liquidity window required")
        try: as_of_date - timedelta(days=liquidity_lookback_calendar_days-1)
        except (ValueError,OverflowError): raise ValueError("Unrepresentable liquidity window") from None
        if not all(p.bond_id in ids for p in proposal.positions):
            raise ValueError("Proposal references an unrequested Bond")
        ids = tuple(sorted(ids))
        with self.db.no_autoflush:
            investment = InvestmentModelSnapshotService(self.db).build(ids,as_of_date,market_source=market_source,
                max_market_age_days=max_market_age_days,max_curve_age_days=max_curve_age_days,
                liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
                liquidity_min_observation_days=liquidity_min_observation_days)
            validate_investment_batch(investment)
            source = investment.source_batch
            if (source.requested_bond_ids != ids or source.as_of_date != as_of_date or source.market_source != market_source or
                source.max_market_age_days != max_market_age_days or source.max_curve_age_days != max_curve_age_days or
                source.liquidity_lookback_calendar_days != liquidity_lookback_calendar_days or
                source.liquidity_min_observation_days != liquidity_min_observation_days):
                raise ValueError("Investment loader returned mismatched request context")
            candidates = RiskCandidateReducer.build(investment)
            return PortfolioRiskEvaluator.build(candidates,proposal)
