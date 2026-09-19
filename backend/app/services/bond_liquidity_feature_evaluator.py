"""Pure Task270 liquidity universe evaluation and feature projection."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any

from app.schemas.bond_liquidity_features import (
    BondLiquidityAvailability,
    BondLiquidityFeatureView,
    BondLiquidityProvenance,
    BondLiquidityScoreComponents,
    BondLiquidityWindowStatistics,
)


MINIMUM_UNIVERSE_SIZE = 10
SCORE_WEIGHTS = (Decimal("0.50"), Decimal("0.35"), Decimal("0.15"))

LiquidityParser = Callable[
    [Any, set[str]], tuple[Decimal | None, Decimal | None, int | None]
]


@dataclass(frozen=True)
class LiquidityBondIdentity:
    bond_id: int
    isin: str | None
    secid: str | None


@dataclass(frozen=True)
class LiquiditySnapshotEvidence:
    id: int
    bond_id: int
    trade_date: date
    raw_payload: Any


@dataclass(frozen=True)
class _WindowEvidence:
    statistics: BondLiquidityWindowStatistics
    snapshot_ids: list[int]
    trade_dates: list[date]
    quality_flags: set[str]


def _select_daily(rows: Iterable[Any]) -> dict[int, list[Any]]:
    selected: dict[tuple[int, date], Any] = {}
    for row in rows:
        key = (row.bond_id, row.trade_date)
        if key not in selected or row.id > selected[key].id:
            selected[key] = row
    grouped: dict[int, list[Any]] = defaultdict(list)
    for (bond_id, _), row in sorted(selected.items()):
        grouped[bond_id].append(row)
    return dict(grouped)


def _aggregate(
    values: list[Decimal] | list[int],
) -> tuple[Decimal | None, Decimal | None, Any]:
    if not values:
        return None, None, None
    ordered = sorted(Decimal(value) for value in values)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
    )
    total = sum(values)
    return median, Decimal(total) / Decimal(len(values)), total


def _summarize(
    rows: list[Any],
    boundary: date,
    calendar_days: int,
    *,
    liquidity_parser: LiquidityParser,
) -> _WindowEvidence:
    volumes: list[Decimal] = []
    turnovers: list[Decimal] = []
    trades: list[int] = []
    flags: set[str] = set()
    for row in rows:
        daily_flags: set[str] = set()
        volume, turnover, trade_count = liquidity_parser(row.raw_payload, daily_flags)
        flags.update(
            flag
            for flag in daily_flags
            if flag != "DURATION_LEGACY_DAY_NORMALIZATION"
        )
        if volume is not None:
            volumes.append(volume)
        if turnover is not None:
            turnovers.append(turnover)
        if trade_count is not None:
            trades.append(trade_count)
    vol_median, vol_mean, vol_total = _aggregate(volumes)
    val_median, val_mean, val_total = _aggregate(turnovers)
    num_median, num_mean, num_total = _aggregate(trades)
    for values, name in (
        (volumes, "TRADE_VOLUME"),
        (turnovers, "TURNOVER_VALUE"),
        (trades, "NUM_TRADES"),
    ):
        if not values:
            flags.add(f"{name}_MISSING")
        elif len(values) < len(rows):
            flags.add(f"PARTIAL_{name}_COVERAGE")
    positive_turnover = sum(value > 0 for value in turnovers)
    positive_trades = sum(value > 0 for value in trades)
    latest = rows[-1].trade_date if rows else None
    return _WindowEvidence(
        snapshot_ids=[row.id for row in rows],
        trade_dates=[row.trade_date for row in rows],
        quality_flags=flags,
        statistics=BondLiquidityWindowStatistics(
            calendar_window_days=calendar_days,
            snapshot_observation_days=len(rows),
            trade_volume_observation_days=len(volumes),
            turnover_value_observation_days=len(turnovers),
            num_trades_observation_days=len(trades),
            latest_trade_date=latest,
            latest_observation_age_days=(boundary - latest).days if latest else None,
            median_daily_turnover_value=val_median,
            mean_daily_turnover_value=val_mean,
            total_turnover_value=val_total,
            median_daily_trade_volume=vol_median,
            mean_daily_trade_volume=vol_mean,
            total_trade_volume=vol_total,
            median_daily_num_trades=num_median,
            mean_daily_num_trades=num_mean,
            total_num_trades=num_total,
            days_with_positive_turnover=positive_turnover,
            days_with_positive_trade_volume=sum(value > 0 for value in volumes),
            days_with_positive_num_trades=positive_trades,
            positive_turnover_share_of_turnover_observations=(
                Decimal(positive_turnover) / Decimal(len(turnovers))
                if turnovers
                else None
            ),
            positive_trade_count_share_of_trade_count_observations=(
                Decimal(positive_trades) / Decimal(len(trades)) if trades else None
            ),
        ),
    )


def _has_components(stats: BondLiquidityWindowStatistics) -> bool:
    return (
        stats.median_daily_turnover_value is not None
        and stats.median_daily_turnover_value >= 0
        and stats.median_daily_num_trades is not None
        and stats.median_daily_num_trades >= 0
        and stats.latest_observation_age_days is not None
    )


def _midrank_percentile(
    value: Decimal,
    values: list[Decimal],
    *,
    lower_is_better: bool = False,
) -> Decimal:
    if len(values) <= 1:
        raise ValueError("Midrank requires at least two eligible values")
    worse = sum(
        candidate > value if lower_is_better else candidate < value
        for candidate in values
    )
    equal = sum(candidate == value for candidate in values)
    return (
        Decimal(100)
        * (Decimal(worse) + Decimal(equal - 1) / Decimal(2))
        / Decimal(len(values) - 1)
    )


def evaluate_liquidity_features(
    identities: Sequence[LiquidityBondIdentity],
    rows: Sequence[LiquiditySnapshotEvidence],
    *,
    as_of_date: date,
    window_start_date: date,
    market_source: str,
    lookback_calendar_days: int,
    min_observation_days: int,
    liquidity_parser: LiquidityParser,
) -> tuple[BondLiquidityFeatureView, ...]:
    """Evaluate one shared Task270 benchmark universe and project requested bonds."""

    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        universe = {
            bond_id: _summarize(
                daily,
                as_of_date,
                lookback_calendar_days,
                liquidity_parser=liquidity_parser,
            )
            for bond_id, daily in _select_daily(rows).items()
        }
        eligible = [
            evidence.statistics
            for evidence in universe.values()
            if evidence.statistics.snapshot_observation_days >= min_observation_days
            and _has_components(evidence.statistics)
        ]

        features: list[BondLiquidityFeatureView] = []
        for identity in identities:
            target = universe.get(identity.bond_id)
            if target is None:
                target = _summarize(
                    [],
                    as_of_date,
                    lookback_calendar_days,
                    liquidity_parser=liquidity_parser,
                )
            stats = target.statistics
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
                        stats.median_daily_turnover_value,
                        [item.median_daily_turnover_value for item in eligible],
                    ),
                    trade_count_percentile=_midrank_percentile(
                        stats.median_daily_num_trades,
                        [item.median_daily_num_trades for item in eligible],
                    ),
                    recency_percentile=_midrank_percentile(
                        Decimal(stats.latest_observation_age_days),
                        [Decimal(item.latest_observation_age_days) for item in eligible],
                        lower_is_better=True,
                    ),
                )
                score = sum(
                    weight * component
                    for weight, component in zip(
                        SCORE_WEIGHTS,
                        (
                            components.turnover_percentile,
                            components.trade_count_percentile,
                            components.recency_percentile,
                        ),
                    )
                )

            features.append(
                BondLiquidityFeatureView(
                    **stats.model_dump(),
                    bond_id=identity.bond_id,
                    isin=identity.isin,
                    secid=identity.secid,
                    as_of_date=as_of_date,
                    window_start_date=window_start_date,
                    market_source=market_source,
                    lookback_calendar_days=lookback_calendar_days,
                    min_observation_days=min_observation_days,
                    score_status=status,
                    score_components=components,
                    liquidity_score_v1=score,
                    quality_flags=sorted(flags),
                    availability=BondLiquidityAvailability(
                        has_market_snapshots=bool(stats.snapshot_observation_days),
                        has_trade_volume=bool(stats.trade_volume_observation_days),
                        has_turnover_value=bool(stats.turnover_value_observation_days),
                        has_num_trades=bool(stats.num_trades_observation_days),
                        has_sufficient_observation_days=(
                            stats.snapshot_observation_days >= min_observation_days
                        ),
                        has_score_components=_has_components(stats),
                        has_liquidity_score=score is not None,
                    ),
                    provenance=BondLiquidityProvenance(
                        market_source=market_source,
                        window_start_date=window_start_date,
                        as_of_date=as_of_date,
                        lookback_calendar_days=lookback_calendar_days,
                        min_observation_days=min_observation_days,
                        selected_market_snapshot_ids=target.snapshot_ids,
                        selected_trade_dates=target.trade_dates,
                        universe_candidate_count=len(universe),
                        score_eligible_universe_count=len(eligible),
                    ),
                )
            )
        return tuple(features)
