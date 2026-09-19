"""SELECT-only loader for Task270's shared liquidity evaluator."""

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.services.bond_liquidity_feature_evaluator import (
    MINIMUM_UNIVERSE_SIZE,
    SCORE_WEIGHTS,
    LiquidityBondIdentity,
    LiquiditySnapshotEvidence,
    _WindowEvidence,
    _aggregate,
    _has_components,
    _midrank_percentile,
    _select_daily,
    _summarize as _evaluate_summarize,
    evaluate_liquidity_features,
)
from app.services.bond_market_feature_service import _liquidity


def _summarize(rows, boundary: date, calendar_days: int) -> _WindowEvidence:
    """Compatibility wrapper retained for existing Task270 consumers and tests."""

    return _evaluate_summarize(
        rows,
        boundary,
        calendar_days,
        liquidity_parser=_liquidity,
    )


class BondLiquidityFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self,
        bond_id: int,
        as_of_date: date,
        *,
        market_source: str = "moex",
        lookback_calendar_days: int = 30,
        min_observation_days: int = 5,
    ) -> BondLiquidityFeatureView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
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
            raise ValueError(
                "Lookback window is outside the calendar date range"
            ) from exc

        with self.db.no_autoflush:
            bond = self.db.execute(
                select(Bond.id, Bond.isin, Bond.secid).where(Bond.id == bond_id)
            ).first()
            if bond is None:
                raise HTTPException(status_code=404, detail="Bond not found")
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

            return evaluate_liquidity_features(
                (
                    LiquidityBondIdentity(
                        bond_id=bond.id,
                        isin=bond.isin,
                        secid=bond.secid,
                    ),
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
            )[0]
