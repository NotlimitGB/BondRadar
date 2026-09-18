"""SELECT-only composition of Task272 MD, Task267 quote/NKD and canonical face value."""

from datetime import date
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.bond_dv01 import (
    BondDv01Availability, BondDv01MarketIdentity, BondDv01Provenance, BondDv01View,
)
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.bond_modified_duration_service import BondModifiedDurationService, DURATION_FLAGS

ONE_BP = Decimal("0.0001")


def _finite(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _identity(view: object) -> BondDv01MarketIdentity:
    return BondDv01MarketIdentity(
        bond_id=view.bond_id, market_snapshot_id=view.market_snapshot_id,
        market_trade_date=view.market_trade_date, market_source=view.market_source,
    )


class BondDv01Service:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7,
    ) -> BondDv01View:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if not isinstance(market_source, str) or market_source != "moex":
            raise ValueError("market_source must be exactly moex")
        if type(max_market_age_days) is not int or max_market_age_days < 0:
            raise ValueError("max_market_age_days must be a nonnegative integer")
        with self.db.no_autoflush, localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            modified = BondModifiedDurationService(self.db).build_for_bond(
                bond_id, as_of_date, market_source="moex", max_market_age_days=max_market_age_days,
            )
            market = BondMarketFeatureService(self.db).build_for_bond(
                bond_id, as_of_date, market_source="moex", max_market_age_days=max_market_age_days,
            )
            profile = self.db.execute(select(
                BondSecurityMasterProfile.id, BondSecurityMasterProfile.contract_version,
                BondSecurityMasterProfile.currency_state, BondSecurityMasterProfile.currency_code,
                BondSecurityMasterProfile.nominal_state, BondSecurityMasterProfile.nominal_value,
            ).where(BondSecurityMasterProfile.bond_id == bond_id)).one_or_none()
            md_identity, market_identity = _identity(modified), _identity(market)
            mismatch = (md_identity != market_identity or market_identity.bond_id != bond_id
                        or market_identity.market_source != "moex")
            matching_snapshot = (not mismatch and market.market_snapshot_id is not None
                                 and market.market_trade_date is not None)
            md = _finite(modified.modified_duration_years)
            valid_md = modified.status == "READY" and md is not None and md >= 0
            currency_state = profile.currency_state if profile else None
            currency = profile.currency_code if profile else None
            verified_currency = currency_state == "verified" and currency is not None
            supported_currency = verified_currency and currency == "RUB"
            nominal_state = profile.nominal_state if profile else None
            raw_nominal = profile.nominal_value if profile else None
            nominal = _finite(raw_nominal)
            valid_nominal = nominal is not None and nominal > 0
            verified_nominal = nominal_state == "verified" and valid_nominal
            basis = ("CLEAN_PRICE" if market.clean_price is not None else
                     "PRICE_FALLBACK" if market.price is not None else None)
            raw_quote = market.clean_price if basis == "CLEAN_PRICE" else market.price
            quote = _finite(raw_quote)
            valid_quote = quote is not None and quote > 0
            nkd = _finite(market.nkd)
            valid_nkd = nkd is not None and nkd >= 0
            clean = dirty = relative = dv01 = None
            arithmetic_invalid = False
            # Each intermediate keeps its own gates; unrelated failures cannot erase evidence.
            try:
                if not mismatch and valid_md:
                    relative = md * ONE_BP
                if not mismatch and supported_currency and verified_nominal and valid_quote:
                    clean = nominal * quote / Decimal("100")
                    if valid_nkd:
                        dirty = clean + nkd
                        if valid_md:
                            dv01 = dirty * md * ONE_BP
            except DecimalException:
                arithmetic_invalid = True
            if dirty is not None and (not dirty.is_finite() or dirty <= 0):
                dirty = dv01 = None
                arithmetic_invalid = True
            conditions = (
                (mismatch, "MARKET_CONTRACT_MISMATCH"),
                (not valid_md, "MODIFIED_DURATION_UNAVAILABLE"),
                (profile is None, "SECURITY_MASTER_MISSING"),
                (currency_state != "verified", "CURRENCY_NOT_VERIFIED"),
                (currency is None, "CURRENCY_MISSING"),
                (currency is not None and currency != "RUB", "UNSUPPORTED_CURRENCY"),
                (nominal_state != "verified", "NOMINAL_NOT_VERIFIED"),
                (raw_nominal is None, "NOMINAL_MISSING"),
                (raw_nominal is not None and not valid_nominal, "NOMINAL_INVALID"),
                (raw_quote is None, "MARKET_PRICE_MISSING"),
                (basis == "CLEAN_PRICE" and not valid_quote, "CLEAN_PRICE_INVALID"),
                (basis == "PRICE_FALLBACK" and not valid_quote, "MARKET_PRICE_INVALID"),
                (market.nkd is None, "NKD_MISSING"),
                (market.nkd is not None and not valid_nkd, "NKD_INVALID"),
                (arithmetic_invalid, "DIRTY_VALUE_INVALID"),
            )
            failures = [flag for blocked, flag in conditions if blocked]
            status = failures[0] if failures else "READY"
            if status != "READY":
                dv01 = None
            flags = set(failures) | (set(modified.quality_flags) & (DURATION_FLAGS | {"NEGATIVE_YIELD"}))
            if basis == "PRICE_FALLBACK":
                flags.add("MARKET_PRICE_FALLBACK_USED")
            return BondDv01View(
                bond_id=bond_id, isin=market.isin, secid=market.secid,
                as_of_date=as_of_date, market_source="moex",
                market_snapshot_id=market.market_snapshot_id if not mismatch else None,
                market_trade_date=market.market_trade_date if not mismatch else None,
                market_age_days=market.market_age_days if not mismatch else None,
                modified_duration_status=modified.status, modified_duration_years=md,
                currency_code=currency, currency_state=currency_state,
                nominal_state=nominal_state, nominal_value=nominal,
                price_basis=basis, clean_quote_pct=quote, nkd_currency=nkd,
                clean_value_currency=clean, dirty_value_currency=dirty,
                relative_price_sensitivity_per_1bp=relative, dv01_currency_per_bond=dv01,
                status=status, quality_flags=sorted(flags),
                availability=BondDv01Availability(
                    has_modified_duration=valid_md, has_matching_market_snapshot=matching_snapshot,
                    has_security_master=profile is not None, has_verified_currency=verified_currency,
                    is_supported_currency=supported_currency, has_verified_nominal=verified_nominal,
                    has_clean_quote=valid_quote, has_nkd=valid_nkd,
                    has_clean_value=clean is not None, has_dirty_value=dirty is not None,
                    has_relative_1bp_sensitivity=relative is not None, has_dv01=dv01 is not None,
                ),
                provenance=BondDv01Provenance(
                    modified_duration_contract_version=modified.contract_version,
                    modified_duration_formula_version=modified.provenance.formula_version,
                    modified_duration_market_identity=md_identity,
                    market_contract_version=market.contract_version, market_identity=market_identity,
                    market_snapshot_id=market.market_snapshot_id, market_trade_date=market.market_trade_date,
                    market_source=market.market_source, as_of_date=as_of_date,
                    max_market_age_days=max_market_age_days,
                    security_master_profile_id=profile.id if profile else None,
                    security_master_contract_version=profile.contract_version if profile else None,
                    currency_state=currency_state, nominal_state=nominal_state, price_basis=basis,
                ),
            )
