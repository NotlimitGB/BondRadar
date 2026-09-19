"""Efficient SELECT-only batch loader for Task270 liquidity features."""

from collections.abc import Mapping, Sequence, Set
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.bond_liquidity_batch_features import (
    BondLiquidityBatchDiagnostics,
    BondLiquidityBatchFeatureItem,
    BondLiquidityBatchFeatureView,
)
from app.services.bond_liquidity_feature_evaluator import (
    LiquidityBondIdentity,
    LiquiditySnapshotEvidence,
    evaluate_liquidity_features,
)
from app.services.bond_market_feature_service import _liquidity


def _validate_request(
    bond_ids: Sequence[int],
    as_of_date: date,
    market_source: str,
    lookback_calendar_days: int,
    min_observation_days: int,
) -> tuple[tuple[int, ...], date]:
    if (
        not isinstance(bond_ids, Sequence)
        or isinstance(
            bond_ids,
            (str, bytes, bytearray, memoryview, Mapping, Set),
        )
    ):
        raise ValueError("bond_ids must be a deterministic sequence")
    requested_ids = tuple(bond_ids)
    if not requested_ids:
        raise ValueError("bond_ids must be nonempty")
    if any(type(bond_id) is not int or bond_id <= 0 for bond_id in requested_ids):
        raise ValueError("bond_ids must contain exact positive integers")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("bond_ids must not contain duplicates")
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if not isinstance(market_source, str) or not market_source.strip():
        raise ValueError("market_source must be a nonempty string")
    if type(lookback_calendar_days) is not int or lookback_calendar_days <= 0:
        raise ValueError("lookback_calendar_days must be a positive integer")
    if (
        type(min_observation_days) is not int
        or min_observation_days <= 0
        or min_observation_days > lookback_calendar_days
    ):
        raise ValueError(
            "min_observation_days must be positive and no larger than the window"
        )
    try:
        window_start = as_of_date - timedelta(days=lookback_calendar_days - 1)
    except OverflowError as exc:
        raise ValueError("Lookback window is outside the calendar date range") from exc
    return tuple(sorted(requested_ids)), window_start


class BondLiquidityBatchFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bonds(
        self,
        bond_ids: Sequence[int],
        as_of_date: date,
        *,
        market_source: str = "moex",
        lookback_calendar_days: int = 30,
        min_observation_days: int = 5,
    ) -> BondLiquidityBatchFeatureView:
        requested_ids, window_start = _validate_request(
            bond_ids,
            as_of_date,
            market_source,
            lookback_calendar_days,
            min_observation_days,
        )

        features = {}
        universe_candidate_count = 0
        score_eligible_universe_count = 0
        market_window_query_count = 0
        universe_evaluation_count = 0

        with self.db.no_autoflush:
            identity_rows = self.db.execute(
                select(Bond.id, Bond.isin, Bond.secid)
                .where(Bond.id.in_(requested_ids))
                .order_by(Bond.id)
            ).all()
            existing_ids = tuple(row.id for row in identity_rows)

            if identity_rows:
                rows = self.db.execute(
                    select(
                        BondMarketSnapshot.id,
                        BondMarketSnapshot.bond_id,
                        BondMarketSnapshot.trade_date,
                        BondMarketSnapshot.raw_payload,
                    )
                    .join(Bond, Bond.id == BondMarketSnapshot.bond_id)
                    .where(
                        BondMarketSnapshot.source == market_source,
                        BondMarketSnapshot.trade_date >= window_start,
                        BondMarketSnapshot.trade_date <= as_of_date,
                    )
                    .order_by(
                        BondMarketSnapshot.bond_id,
                        BondMarketSnapshot.trade_date,
                        BondMarketSnapshot.id,
                    )
                ).all()
                market_window_query_count = 1
                evaluated = evaluate_liquidity_features(
                    tuple(
                        LiquidityBondIdentity(
                            bond_id=row.id,
                            isin=row.isin,
                            secid=row.secid,
                        )
                        for row in identity_rows
                    ),
                    tuple(
                        LiquiditySnapshotEvidence(
                            id=row.id,
                            bond_id=row.bond_id,
                            trade_date=row.trade_date,
                            raw_payload=row.raw_payload,
                        )
                        for row in rows
                    ),
                    as_of_date=as_of_date,
                    window_start_date=window_start,
                    market_source=market_source,
                    lookback_calendar_days=lookback_calendar_days,
                    min_observation_days=min_observation_days,
                    liquidity_parser=_liquidity,
                )
                universe_evaluation_count = 1
                features = {feature.bond_id: feature for feature in evaluated}
                universe_candidate_count = (
                    evaluated[0].provenance.universe_candidate_count
                )
                score_eligible_universe_count = (
                    evaluated[0].provenance.score_eligible_universe_count
                )

        existing_set = set(existing_ids)
        missing_ids = tuple(
            bond_id for bond_id in requested_ids if bond_id not in existing_set
        )
        items = [
            BondLiquidityBatchFeatureItem(
                bond_id=bond_id,
                build_status=(
                    "BUILT" if bond_id in existing_set else "BOND_NOT_FOUND"
                ),
                feature=features.get(bond_id),
            )
            for bond_id in requested_ids
        ]
        built_feature_count = len(existing_ids)
        ready_feature_count = sum(
            feature.score_status == "READY" for feature in features.values()
        )
        status = (
            "NO_EXISTING_BONDS"
            if not existing_ids
            else "PARTIAL"
            if missing_ids
            else "COMPLETE"
        )

        return BondLiquidityBatchFeatureView(
            as_of_date=as_of_date,
            market_source=market_source,
            lookback_calendar_days=lookback_calendar_days,
            min_observation_days=min_observation_days,
            status=status,
            requested_bond_ids=list(requested_ids),
            existing_bond_ids=list(existing_ids),
            missing_bond_ids=list(missing_ids),
            requested_bond_count=len(requested_ids),
            existing_bond_count=len(existing_ids),
            missing_bond_count=len(missing_ids),
            built_feature_count=built_feature_count,
            ready_feature_count=ready_feature_count,
            unavailable_feature_count=built_feature_count - ready_feature_count,
            items=items,
            diagnostics=BondLiquidityBatchDiagnostics(
                requested_identity_query_count=1,
                market_window_query_count=market_window_query_count,
                universe_evaluation_count=universe_evaluation_count,
                universe_candidate_count=universe_candidate_count,
                score_eligible_universe_count=score_eligible_universe_count,
                feature_projection_count=built_feature_count,
            ),
        )
