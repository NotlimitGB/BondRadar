"""One read-only candidate load, then pure member/peer/evaluation delegation."""

from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.schemas.investment_model import InvestmentPeerContext
from app.services.unified_candidate_snapshot_service import UnifiedCandidateSnapshotService
from app.services.bond_credit_cohort_relative_value_composer import compose_credit_cohort_relative_value_member
from app.services.credit_cohort_peer_spread_distribution_service import CreditCohortPeerSpreadDistributionService
from app.services.investment_model_reducer import InvestmentModelReducer, validate_candidate_batch, selectors


def build_peer_contexts(batch):
    validate_candidate_batch(batch)
    all_selectors = sorted({selector for c in batch.candidates for selector in selectors(c)})
    contexts = []
    for kind, agency in all_selectors:
        members = tuple(compose_credit_cohort_relative_value_member(
            c.m3.credit_comparability, c.m3.relative_value, bond_id=c.bond_id,
            as_of_date=c.as_of_date, market_source=c.market_source, target_kind=kind, rating_agency=agency)
            for c in batch.candidates)
        for c, member in zip(batch.candidates, members):
            if (kind, agency) in selectors(c):
                distribution = CreditCohortPeerSpreadDistributionService.build(member, members, min_peer_count=2)
                contexts.append(InvestmentPeerContext(bond_id=c.bond_id, target_kind=kind,
                    rating_agency=agency, member=member, distribution=distribution))
    return tuple(sorted(contexts, key=lambda ctx: (ctx.bond_id, ctx.target_kind, ctx.rating_agency)))


class InvestmentModelSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    def build(self, bond_ids: Sequence[int], as_of_date: date, *, market_source: str = "moex",
              max_market_age_days: int = 7, max_curve_age_days: int = 7,
              liquidity_lookback_calendar_days: int = 30, liquidity_min_observation_days: int = 5):
        if (not isinstance(bond_ids, Sequence) or isinstance(bond_ids, (str, bytes, bytearray))):
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
        try:
            as_of_date - timedelta(days=liquidity_lookback_calendar_days - 1)
        except (OverflowError, ValueError):
            raise ValueError("Unrepresentable liquidity window") from None
        ids = tuple(sorted(ids))
        with self.db.no_autoflush:
            batch = UnifiedCandidateSnapshotService(self.db).build(ids, as_of_date,
                market_source=market_source, max_market_age_days=max_market_age_days,
                max_curve_age_days=max_curve_age_days,
                liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
                liquidity_min_observation_days=liquidity_min_observation_days)
            validate_candidate_batch(batch)
            if (batch.requested_bond_ids != ids or batch.as_of_date != as_of_date or batch.market_source != market_source or
                    batch.max_market_age_days != max_market_age_days or batch.max_curve_age_days != max_curve_age_days or
                    batch.liquidity_lookback_calendar_days != liquidity_lookback_calendar_days or
                    batch.liquidity_min_observation_days != liquidity_min_observation_days):
                raise ValueError("Candidate loader returned mismatched request context")
            return InvestmentModelReducer.build(batch, build_peer_contexts(batch))
