"""Read-only loader for the pure Task284 liquidity-relative-value composer."""

from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.schemas.bond_liquidity_relative_value import BondLiquidityAwareRelativeValueView
from app.services.bond_liquidity_feature_service import BondLiquidityFeatureService
from app.services.bond_liquidity_relative_value_composer import (
    compose_bond_liquidity_aware_relative_value,
)
from app.services.ofz_reference_curve_service import OfzReferenceCurveService


class BondLiquidityAwareRelativeValueService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7, max_curve_age_days: int = 7,
        liquidity_lookback_calendar_days: int = 30, liquidity_min_observation_days: int = 5,
    ) -> BondLiquidityAwareRelativeValueView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if not isinstance(market_source, str) or not market_source.strip():
            raise ValueError("market_source must be a nonblank string")
        for name, value in (("max_market_age_days", max_market_age_days),
                            ("max_curve_age_days", max_curve_age_days)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if type(liquidity_lookback_calendar_days) is not int or liquidity_lookback_calendar_days <= 0:
            raise ValueError("liquidity_lookback_calendar_days must be a positive integer")
        if (type(liquidity_min_observation_days) is not int or liquidity_min_observation_days <= 0
                or liquidity_min_observation_days > liquidity_lookback_calendar_days):
            raise ValueError("liquidity_min_observation_days must be positive and no larger than the window")
        try:
            as_of_date - timedelta(days=liquidity_lookback_calendar_days - 1)
        except OverflowError as exc:
            raise ValueError("Lookback window is outside the calendar date range") from exc
        with self.db.no_autoflush:
            relative = OfzReferenceCurveService(self.db).evaluate_bond(
                bond_id, as_of_date, market_source=market_source,
                max_market_age_days=max_market_age_days, max_curve_age_days=max_curve_age_days,
            )
            liquidity = BondLiquidityFeatureService(self.db).build_for_bond(
                bond_id, as_of_date, market_source=market_source,
                lookback_calendar_days=liquidity_lookback_calendar_days,
                min_observation_days=liquidity_min_observation_days,
            )
        return compose_bond_liquidity_aware_relative_value(
            relative,
            liquidity,
            bond_id=bond_id,
            as_of_date=as_of_date,
            market_source=market_source,
        )
