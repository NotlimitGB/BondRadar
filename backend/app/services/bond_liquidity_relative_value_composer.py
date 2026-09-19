"""Pure composition of prebuilt Task268 and Task270 feature views."""

from datetime import date
from decimal import Decimal

from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.schemas.bond_liquidity_relative_value import (
    BondLiquidityAwareRelativeValueAvailability,
    BondLiquidityAwareRelativeValueProvenance,
    BondLiquidityAwareRelativeValueView,
    DependencyIdentity,
)
from app.schemas.ofz_reference_curve import BondRelativeValueView


def _finite(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _score_value(value: object) -> bool:
    finite = _finite(value)
    return finite is not None and Decimal("0") <= finite <= Decimal("100")


def _liquidity_provenance_valid(ids: object, dates: object, days: object, ready: bool) -> bool:
    if not isinstance(ids, list) or not isinstance(dates, list) or len(ids) != len(dates):
        return False
    if type(days) is not int or days < 0 or len(ids) != days:
        return False
    if ready and not ids:
        return False
    return (
        all(type(identity) is int and identity > 0 for identity in ids)
        and all(type(day) is date for day in dates)
        and all(lower < upper for lower, upper in zip(dates, dates[1:]))
    )


def compose_bond_liquidity_aware_relative_value(
    relative: BondRelativeValueView,
    liquidity: BondLiquidityFeatureView,
    *,
    bond_id: int,
    as_of_date: date,
    market_source: str,
) -> BondLiquidityAwareRelativeValueView:
    """Compose Task274 without loading data or recalculating financial features."""
    if type(bond_id) is not int or bond_id <= 0:
        raise ValueError("bond_id must be a positive integer")
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if not isinstance(market_source, str) or not market_source.strip():
        raise ValueError("market_source must be a nonblank string")
    if type(relative) is not BondRelativeValueView:
        raise ValueError("relative must be a BondRelativeValueView")
    if type(liquidity) is not BondLiquidityFeatureView:
        raise ValueError("liquidity must be a BondLiquidityFeatureView")

    requested = (bond_id, as_of_date, market_source)
    relative_identity = DependencyIdentity(
        bond_id=relative.bond_id,
        as_of_date=relative.as_of_date,
        market_source=relative.market_source,
    )
    liquidity_identity = DependencyIdentity(
        bond_id=liquidity.bond_id,
        as_of_date=liquidity.as_of_date,
        market_source=liquidity.market_source,
    )
    # Compare the original values; schema coercion cannot make contradictory evidence match.
    relative_identity_matches = (
        type(relative.bond_id) is int
        and type(relative.as_of_date) is date
        and (relative.bond_id, relative.as_of_date, relative.market_source) == requested
    )
    liquidity_identity_matches = (
        type(liquidity.bond_id) is int
        and type(liquidity.as_of_date) is date
        and (liquidity.bond_id, liquidity.as_of_date, liquidity.market_source) == requested
    )
    ids = liquidity.provenance.selected_market_snapshot_ids
    dates = liquidity.provenance.selected_trade_dates
    provenance_valid = _liquidity_provenance_valid(
        ids,
        dates,
        liquidity.snapshot_observation_days,
        liquidity.score_status == "READY",
    )
    latest_id = ids[-1] if provenance_valid and ids else None
    latest_date = dates[-1] if provenance_valid and dates else None
    target_id = relative.provenance.target_market_snapshot_id
    target_date = relative.provenance.target_market_trade_date
    target_pair_valid = type(target_id) is int and target_id > 0 and type(target_date) is date
    target_pair_invalid = (target_id is not None or target_date is not None) and not target_pair_valid
    mismatch = (
        not relative_identity_matches
        or not liquidity_identity_matches
        or target_pair_invalid
        or (relative.status == "READY" and not target_pair_valid)
        or (
            target_pair_valid
            and latest_id is not None
            and (target_id, target_date) != (latest_id, latest_date)
        )
    )
    matching_evidence = not mismatch and target_pair_valid and latest_id is not None
    relative_numbers_valid = all(
        _finite(value) is not None
        for value in (relative.spread_to_ofz_pp, relative.spread_to_ofz_bps)
    )
    components = liquidity.score_components
    liquidity_numbers_valid = all(
        _score_value(value)
        for value in (
            liquidity.liquidity_score_v1,
            components.turnover_percentile,
            components.trade_count_percentile,
            components.recency_percentile,
        )
    )
    relative_ready = (
        relative_identity_matches
        and relative.status == "READY"
        and relative_numbers_valid
        and target_pair_valid
    )
    liquidity_ready = (
        liquidity_identity_matches
        and liquidity.score_status == "READY"
        and liquidity_numbers_valid
        and provenance_valid
    )
    conditions = (
        (not provenance_valid, "LIQUIDITY_PROVENANCE_INVALID"),
        (mismatch, "MARKET_EVIDENCE_MISMATCH"),
        (not relative_ready, "RELATIVE_VALUE_UNAVAILABLE"),
        (not liquidity_ready, "LIQUIDITY_UNAVAILABLE"),
    )
    failures = [flag for blocked, flag in conditions if blocked]
    status = failures[0] if failures else "READY"
    flags = set(failures)
    if relative.status != "READY":
        flags.add(f"RELATIVE_VALUE_{relative.status}")
    elif not relative_numbers_valid:
        flags.add("RELATIVE_VALUE_EVIDENCE_INVALID")
    if liquidity.score_status != "READY":
        flags.add(f"LIQUIDITY_{liquidity.score_status}")
    elif not liquidity_numbers_valid:
        flags.add("LIQUIDITY_EVIDENCE_INVALID")

    # Keep each correctly attributed side, including during snapshot drift.
    def relative_number(value: object) -> Decimal | None:
        return _finite(value) if relative_identity_matches else None

    def liquidity_number(value: object) -> Decimal | None:
        return _finite(value) if liquidity_identity_matches else None

    identity_view = (
        relative
        if relative_identity_matches
        else liquidity
        if liquidity_identity_matches
        else None
    )
    return BondLiquidityAwareRelativeValueView(
        bond_id=bond_id,
        isin=identity_view.isin if identity_view else None,
        secid=identity_view.secid if identity_view else None,
        as_of_date=as_of_date,
        market_source=market_source,
        status=status,
        relative_value_status=relative.status,
        target_yield_to_maturity_pct=relative_number(relative.target_yield_to_maturity_pct),
        target_duration_years=relative_number(relative.target_duration_years),
        reference_ofz_yield_pct=relative_number(relative.reference_ofz_yield_pct),
        spread_to_ofz_pp=relative_number(relative.spread_to_ofz_pp),
        spread_to_ofz_bps=relative_number(relative.spread_to_ofz_bps),
        interpolation_method=relative.interpolation_method if relative_identity_matches else None,
        curve_trade_date=relative.curve.curve_trade_date if relative_identity_matches else None,
        liquidity_status=liquidity.score_status,
        liquidity_score_v1=liquidity_number(liquidity.liquidity_score_v1),
        turnover_percentile=liquidity_number(components.turnover_percentile),
        trade_count_percentile=liquidity_number(components.trade_count_percentile),
        recency_percentile=liquidity_number(components.recency_percentile),
        median_daily_turnover_value=liquidity_number(liquidity.median_daily_turnover_value),
        median_daily_trade_volume=liquidity_number(liquidity.median_daily_trade_volume),
        median_daily_num_trades=liquidity_number(liquidity.median_daily_num_trades),
        latest_liquidity_trade_date=(
            liquidity.latest_trade_date if liquidity_identity_matches else None
        ),
        latest_liquidity_observation_age_days=(
            liquidity.latest_observation_age_days if liquidity_identity_matches else None
        ),
        liquidity_snapshot_observation_days=(
            liquidity.snapshot_observation_days if liquidity_identity_matches else None
        ),
        availability=BondLiquidityAwareRelativeValueAvailability(
            has_relative_value=relative_ready,
            has_spread_to_ofz=relative_ready,
            has_liquidity_feature=(
                liquidity_identity_matches
                and provenance_valid
                and liquidity.snapshot_observation_days > 0
            ),
            has_liquidity_score=liquidity_ready,
            has_matching_market_evidence=matching_evidence,
            has_liquidity_aware_relative_value=status == "READY",
        ),
        quality_flags=sorted(flags),
        provenance=BondLiquidityAwareRelativeValueProvenance(
            relative_value_contract_version=relative.contract_version,
            ofz_curve_contract_version=relative.curve.contract_version,
            liquidity_contract_version=liquidity.contract_version,
            relative_value_identity=relative_identity,
            liquidity_identity=liquidity_identity,
            relative_value_target_market_snapshot_id=target_id,
            relative_value_target_market_trade_date=target_date,
            liquidity_latest_market_snapshot_id=latest_id,
            liquidity_latest_trade_date=latest_date,
            curve_trade_date=relative.curve.curve_trade_date,
            liquidity_window_start_date=liquidity.provenance.window_start_date,
            liquidity_lookback_calendar_days=liquidity.provenance.lookback_calendar_days,
            liquidity_min_observation_days=liquidity.provenance.min_observation_days,
            liquidity_provenance_valid=provenance_valid,
            requested_as_of_date=as_of_date,
            market_source=market_source,
        ),
    )
