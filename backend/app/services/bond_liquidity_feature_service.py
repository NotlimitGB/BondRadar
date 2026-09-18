"""SELECT-only window evidence and Task270's explicitly authorized liquidity score."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.bond_liquidity_features import (
    BondLiquidityAvailability, BondLiquidityFeatureView, BondLiquidityProvenance,
    BondLiquidityScoreComponents, BondLiquidityWindowStatistics,
)
from app.services.bond_market_feature_service import _liquidity

MINIMUM_UNIVERSE_SIZE = 10
SCORE_WEIGHTS = (Decimal("0.50"), Decimal("0.35"), Decimal("0.15"))


@dataclass(frozen=True)
class _WindowEvidence:
    statistics: BondLiquidityWindowStatistics
    snapshot_ids: list[int]
    trade_dates: list[date]
    quality_flags: set[str]


def _select_daily(rows: Iterable[Any]) -> dict[int, list[Any]]:
    selected = {}
    for row in rows:
        key = (row.bond_id, row.trade_date)
        if key not in selected or row.id > selected[key].id:
            selected[key] = row
    grouped = defaultdict(list)
    for (bond_id, _), row in sorted(selected.items()):
        grouped[bond_id].append(row)
    return dict(grouped)


def _aggregate(values: list[Decimal] | list[int]) -> tuple[Decimal | None, Decimal | None, Any]:
    if not values:
        return None, None, None
    ordered = sorted(Decimal(value) for value in values)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / Decimal(2))
    total = sum(values)
    return median, Decimal(total) / Decimal(len(values)), total


def _summarize(rows: list[Any], boundary: date, calendar_days: int) -> _WindowEvidence:
    volumes, turnovers, trades = [], [], []
    flags: set[str] = set()
    for row in rows:
        daily_flags: set[str] = set()
        volume, turnover, trade_count = _liquidity(row.raw_payload, daily_flags)
        flags.update(flag for flag in daily_flags if flag != "DURATION_LEGACY_DAY_NORMALIZATION")
        if volume is not None:
            volumes.append(volume)
        if turnover is not None:
            turnovers.append(turnover)
        if trade_count is not None:
            trades.append(trade_count)
    vol_median, vol_mean, vol_total = _aggregate(volumes)
    val_median, val_mean, val_total = _aggregate(turnovers)
    num_median, num_mean, num_total = _aggregate(trades)
    for values, name in ((volumes, "TRADE_VOLUME"), (turnovers, "TURNOVER_VALUE"), (trades, "NUM_TRADES")):
        if not values:
            flags.add(f"{name}_MISSING")
        elif len(values) < len(rows):
            flags.add(f"PARTIAL_{name}_COVERAGE")
    positive_turnover = sum(v > 0 for v in turnovers)
    positive_trades = sum(v > 0 for v in trades)
    latest = rows[-1].trade_date if rows else None
    return _WindowEvidence(
        snapshot_ids=[row.id for row in rows], trade_dates=[row.trade_date for row in rows],
        quality_flags=flags,
        statistics=BondLiquidityWindowStatistics(
            calendar_window_days=calendar_days, snapshot_observation_days=len(rows),
            trade_volume_observation_days=len(volumes), turnover_value_observation_days=len(turnovers),
            num_trades_observation_days=len(trades), latest_trade_date=latest,
            latest_observation_age_days=(boundary - latest).days if latest else None,
            median_daily_turnover_value=val_median, mean_daily_turnover_value=val_mean,
            total_turnover_value=val_total, median_daily_trade_volume=vol_median,
            mean_daily_trade_volume=vol_mean, total_trade_volume=vol_total,
            median_daily_num_trades=num_median, mean_daily_num_trades=num_mean,
            total_num_trades=num_total, days_with_positive_turnover=positive_turnover,
            days_with_positive_trade_volume=sum(v > 0 for v in volumes),
            days_with_positive_num_trades=positive_trades,
            positive_turnover_share_of_turnover_observations=(
                Decimal(positive_turnover) / Decimal(len(turnovers)) if turnovers else None),
            positive_trade_count_share_of_trade_count_observations=(
                Decimal(positive_trades) / Decimal(len(trades)) if trades else None),
        ),
    )


def _has_components(stats: BondLiquidityWindowStatistics) -> bool:
    return (stats.median_daily_turnover_value is not None and stats.median_daily_turnover_value >= 0
            and stats.median_daily_num_trades is not None and stats.median_daily_num_trades >= 0
            and stats.latest_observation_age_days is not None)


def _midrank_percentile(value: Decimal, values: list[Decimal], *, lower_is_better: bool = False) -> Decimal:
    if len(values) <= 1:
        raise ValueError("Midrank requires at least two eligible values")
    worse = sum(v > value if lower_is_better else v < value for v in values)
    equal = sum(v == value for v in values)
    return Decimal(100) * (Decimal(worse) + Decimal(equal - 1) / Decimal(2)) / Decimal(len(values) - 1)


class BondLiquidityFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        lookback_calendar_days: int = 30, min_observation_days: int = 5,
    ) -> BondLiquidityFeatureView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if not isinstance(market_source, str) or not market_source.strip():
            raise ValueError("market_source must be a nonempty string")
        if type(lookback_calendar_days) is not int or lookback_calendar_days <= 0:
            raise ValueError("lookback_calendar_days must be a positive integer")
        if (type(min_observation_days) is not int or min_observation_days <= 0
                or min_observation_days > lookback_calendar_days):
            raise ValueError("min_observation_days must be positive and no larger than the window")
        try:
            window_start = as_of_date - timedelta(days=lookback_calendar_days - 1)
        except OverflowError as exc:
            raise ValueError("Lookback window is outside the calendar date range") from exc
        with self.db.no_autoflush, localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            bond = self.db.execute(select(Bond.id, Bond.isin, Bond.secid).where(Bond.id == bond_id)).first()
            if bond is None:
                raise HTTPException(status_code=404, detail="Bond not found")
            rows = self.db.execute(select(
                BondMarketSnapshot.id, BondMarketSnapshot.bond_id,
                BondMarketSnapshot.trade_date, BondMarketSnapshot.raw_payload,
            ).join(Bond, Bond.id == BondMarketSnapshot.bond_id).where(
                BondMarketSnapshot.source == market_source,
                BondMarketSnapshot.trade_date >= window_start,
                BondMarketSnapshot.trade_date <= as_of_date,
            ).order_by(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date, BondMarketSnapshot.id)).all()
            universe = {identity: _summarize(daily, as_of_date, lookback_calendar_days)
                        for identity, daily in _select_daily(rows).items()}
            target = universe.get(bond_id)
            if target is None:
                target = _summarize([], as_of_date, lookback_calendar_days)
            stats = target.statistics
            eligible = [e.statistics for e in universe.values()
                        if e.statistics.snapshot_observation_days >= min_observation_days
                        and _has_components(e.statistics)]
            flags = set(target.quality_flags)
            if not stats.snapshot_observation_days:
                status = "NO_MARKET_DATA"
                flags.add("MARKET_DATA_MISSING")
            elif stats.snapshot_observation_days < min_observation_days:
                status = "INSUFFICIENT_TARGET_EVIDENCE"
            elif not _has_components(stats):
                status = "MISSING_SCORE_COMPONENT"
            elif len(eligible) < MINIMUM_UNIVERSE_SIZE:
                status = "INSUFFICIENT_UNIVERSE"
            else:
                status = "READY"
            if stats.snapshot_observation_days < min_observation_days:
                flags.add("INSUFFICIENT_TARGET_OBSERVATIONS")
            if not _has_components(stats):
                flags.add("LIQUIDITY_SCORE_COMPONENT_MISSING")
            if len(eligible) < MINIMUM_UNIVERSE_SIZE:
                flags.add("INSUFFICIENT_LIQUIDITY_UNIVERSE")
            components = BondLiquidityScoreComponents()
            score = None
            if status == "READY":
                components = BondLiquidityScoreComponents(
                    turnover_percentile=_midrank_percentile(
                        stats.median_daily_turnover_value, [e.median_daily_turnover_value for e in eligible]),
                    trade_count_percentile=_midrank_percentile(
                        stats.median_daily_num_trades, [e.median_daily_num_trades for e in eligible]),
                    recency_percentile=_midrank_percentile(
                        Decimal(stats.latest_observation_age_days),
                        [Decimal(e.latest_observation_age_days) for e in eligible], lower_is_better=True),
                )
                score = sum(weight * component for weight, component in zip(SCORE_WEIGHTS, (
                    components.turnover_percentile, components.trade_count_percentile, components.recency_percentile,
                )))
            return BondLiquidityFeatureView(
                **stats.model_dump(), bond_id=bond.id, isin=bond.isin, secid=bond.secid,
                as_of_date=as_of_date, window_start_date=window_start, market_source=market_source,
                lookback_calendar_days=lookback_calendar_days, min_observation_days=min_observation_days,
                score_status=status, score_components=components, liquidity_score_v1=score,
                quality_flags=sorted(flags),
                availability=BondLiquidityAvailability(
                    has_market_snapshots=bool(stats.snapshot_observation_days),
                    has_trade_volume=bool(stats.trade_volume_observation_days),
                    has_turnover_value=bool(stats.turnover_value_observation_days),
                    has_num_trades=bool(stats.num_trades_observation_days),
                    has_sufficient_observation_days=stats.snapshot_observation_days >= min_observation_days,
                    has_score_components=_has_components(stats), has_liquidity_score=score is not None,
                ),
                provenance=BondLiquidityProvenance(
                    market_source=market_source, window_start_date=window_start, as_of_date=as_of_date,
                    lookback_calendar_days=lookback_calendar_days, min_observation_days=min_observation_days,
                    selected_market_snapshot_ids=target.snapshot_ids, selected_trade_dates=target.trade_dates,
                    universe_candidate_count=len(universe), score_eligible_universe_count=len(eligible),
                ),
            )
