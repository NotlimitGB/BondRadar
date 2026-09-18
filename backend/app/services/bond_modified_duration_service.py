"""SELECT-only Task267 market inputs joined to canonical coupon-frequency evidence."""

from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.bond_modified_duration import (
    BondModifiedDurationAvailability, BondModifiedDurationProvenance, BondModifiedDurationView,
)
from app.services.bond_market_feature_service import BondMarketFeatureService

DURATION_FLAGS = frozenset({
    "DURATION_RAW_MISSING", "MALFORMED_RAW_DURATION", "CONFLICTING_RAW_DURATION",
    "STORED_DURATION_MISMATCH", "DURATION_LEGACY_DAY_NORMALIZATION", "DURATION_MISSING",
})


def _finite(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


class BondModifiedDurationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7,
    ) -> BondModifiedDurationView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if not isinstance(market_source, str) or not market_source.strip():
            raise ValueError("market_source must be a nonblank string")
        if type(max_market_age_days) is not int or max_market_age_days < 0:
            raise ValueError("max_market_age_days must be a nonnegative integer")
        with self.db.no_autoflush, localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            market = BondMarketFeatureService(self.db).build_for_bond(
                bond_id, as_of_date, market_source=market_source, max_market_age_days=max_market_age_days,
            )
            profile = self.db.execute(select(
                BondSecurityMasterProfile.id, BondSecurityMasterProfile.contract_version,
                BondSecurityMasterProfile.coupon_structure, BondSecurityMasterProfile.coupon_frequency_state,
                BondSecurityMasterProfile.coupon_frequency_per_year, BondSecurityMasterProfile.perpetual_structure,
            ).where(BondSecurityMasterProfile.bond_id == bond_id)).one_or_none()
            duration = _finite(market.duration_years)
            ytm = _finite(market.yield_to_maturity_pct)
            coupon = profile.coupon_structure if profile else None
            frequency_state = profile.coupon_frequency_state if profile else None
            raw_frequency = profile.coupon_frequency_per_year if profile else None
            # Do not coerce synthetic/corrupt values into a canonical integer.
            frequency = raw_frequency if type(raw_frequency) is int else None
            valid_frequency = type(raw_frequency) is int and raw_frequency > 0
            verified_frequency = frequency_state == "verified" and valid_frequency
            perpetual = profile.perpetual_structure if profile else None
            yield_decimal = ytm / Decimal("100") if ytm is not None else None
            denominator = (Decimal("1") + yield_decimal / Decimal(frequency)
                           if yield_decimal is not None and verified_frequency else None)
            valid_denominator = denominator is not None and denominator > 0
            valid_duration = duration is not None and duration >= 0
            conditions = (
                (market.market_status == "MISSING", "MARKET_DATA_MISSING"),
                (market.market_status == "STALE", "MARKET_DATA_STALE"),
                (market.duration_years is None, "MACAULAY_DURATION_MISSING"),
                (market.duration_years is not None and not valid_duration, "MACAULAY_DURATION_INVALID"),
                (market.yield_to_maturity_pct is None, "YIELD_TO_MATURITY_MISSING"),
                (market.yield_to_maturity_pct is not None and ytm is None, "YIELD_TO_MATURITY_INVALID"),
                (profile is None, "SECURITY_MASTER_MISSING"),
                (coupon != "fixed", "COUPON_STRUCTURE_NOT_FIXED"),
                (frequency_state != "verified", "COUPON_FREQUENCY_NOT_VERIFIED"),
                (raw_frequency is None, "COUPON_FREQUENCY_MISSING"),
                (raw_frequency is not None and not valid_frequency, "COUPON_FREQUENCY_INVALID"),
                (perpetual != "dated", "PERPETUAL_STRUCTURE_NOT_DATED"),
                (denominator is not None and not valid_denominator, "INVALID_MODIFIED_DURATION_DENOMINATOR"),
            )
            failures = [flag for blocked, flag in conditions if blocked]
            status = failures[0] if failures else "READY"
            flags = set(failures) | (set(market.quality_flags) & DURATION_FLAGS)
            if ytm is not None and ytm < 0:
                flags.add("NEGATIVE_YIELD")
            if coupon == "unknown":
                flags.add("COUPON_STRUCTURE_UNKNOWN")
            elif coupon == "conflict":
                flags.add("COUPON_STRUCTURE_CONFLICT")
            modified = duration / denominator if status == "READY" else None
            return BondModifiedDurationView(
                bond_id=market.bond_id, isin=market.isin, secid=market.secid,
                as_of_date=as_of_date, market_source=market.market_source,
                market_snapshot_id=market.market_snapshot_id, market_trade_date=market.market_trade_date,
                market_age_days=market.market_age_days, market_status=market.market_status,
                macaulay_duration_years=duration, yield_to_maturity_pct=ytm, yield_decimal=yield_decimal,
                coupon_structure=coupon, coupon_frequency_state=frequency_state,
                coupon_frequency_per_year=frequency, perpetual_structure=perpetual,
                modified_duration_denominator=denominator, modified_duration_years=modified,
                status=status, quality_flags=sorted(flags),
                availability=BondModifiedDurationAvailability(
                    has_market_snapshot=market.market_snapshot_id is not None,
                    has_fresh_market_data=market.market_status == "FRESH",
                    has_macaulay_duration=valid_duration, has_yield_to_maturity=ytm is not None,
                    has_security_master=profile is not None, has_fixed_coupon_structure=coupon == "fixed",
                    has_verified_coupon_frequency=verified_frequency, has_dated_structure=perpetual == "dated",
                    has_valid_denominator=valid_denominator, has_modified_duration=modified is not None,
                ),
                provenance=BondModifiedDurationProvenance(
                    market_contract_version=market.contract_version,
                    market_snapshot_id=market.market_snapshot_id, market_trade_date=market.market_trade_date,
                    market_source=market.market_source, as_of_date=as_of_date,
                    max_market_age_days=max_market_age_days,
                    security_master_profile_id=profile.id if profile else None,
                    security_master_contract_version=profile.contract_version if profile else None,
                    coupon_frequency_state=frequency_state, coupon_frequency_per_year=frequency,
                ),
            )
