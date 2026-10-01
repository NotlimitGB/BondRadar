"""Read-only delegation to the authoritative M3 snapshot runner."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from app.schemas.unified_candidate import UnifiedCandidateBatchView
from app.services.m3_audit_snapshot_service import M3AuditSnapshotService, _validate_request
from app.services.unified_candidate_reducer import UnifiedCandidateReducer


class UnifiedCandidateSnapshotService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(
        self, bond_ids: Sequence[int], as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7, max_curve_age_days: int = 7,
        liquidity_lookback_calendar_days: int = 30, liquidity_min_observation_days: int = 5,
    ) -> UnifiedCandidateBatchView:
        # Reuse Task286's request validator without copying orchestration/economics.
        requested_ids = _validate_request(bond_ids, as_of_date, market_source,
            max_market_age_days, max_curve_age_days, liquidity_lookback_calendar_days,
            liquidity_min_observation_days)
        with self.db.no_autoflush:
            snapshot = M3AuditSnapshotService(self.db).build(requested_ids, as_of_date,
                market_source=market_source, max_market_age_days=max_market_age_days,
                max_curve_age_days=max_curve_age_days,
                liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
                liquidity_min_observation_days=liquidity_min_observation_days)
            if (snapshot.requested_bond_ids != list(requested_ids) or snapshot.as_of_date != as_of_date or
                    snapshot.market_source != market_source or snapshot.max_market_age_days != max_market_age_days or
                    snapshot.max_curve_age_days != max_curve_age_days or
                    snapshot.liquidity_lookback_calendar_days != liquidity_lookback_calendar_days or
                    snapshot.liquidity_min_observation_days != liquidity_min_observation_days):
                raise ValueError("Task286 returned mismatched request context")
            return UnifiedCandidateReducer.build(snapshot)
