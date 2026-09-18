"""Deterministic SELECT-only projection over existing market evidence."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.services.moex_duration_semantics import LEGACY_DURATION_MAPPING_NOTE, normalize_moex_duration
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.bond_market_features import (
    BondMarketFeatureAvailability,
    BondMarketFeatureProvenance,
    BondMarketFeatureView,
)

_VOLUME_NOTE = "VALUE was used as volume fallback"
_DURATION_NOTE = LEGACY_DURATION_MAPPING_NOTE


def _raw_number(value: Any, *, integer: bool = False) -> Decimal | int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ValueError("Unsupported numeric representation")
    try:
        number = value if isinstance(value, Decimal) else Decimal(
            str(value).strip().replace(",", ".")
        )
    except InvalidOperation as exc:
        raise ValueError("Malformed numeric representation") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Non-finite or negative liquidity input")
    if integer:
        if number != number.to_integral_value():
            raise ValueError("Fractional trade count")
        return int(number)
    return number


def _liquidity(payload: Any, flags: set[str]) -> tuple[Any, Any, Any]:
    # Only the two shapes emitted by existing ingestion, not recursive JSON search.
    payload = payload if isinstance(payload, dict) else {}
    moex = payload.get("moex")
    canonical = payload.get("canonical")
    moex = moex if isinstance(moex, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else {}
    specs = (
        ("VOLUME", ((moex, ("VOLUME", "volume")), (canonical, ("volume",))), False),
        ("VALUE", ((moex, ("VALUE", "value")), (canonical, ("value",)),
                   (payload, ("value",))), False),
        ("NUMTRADES", ((moex, ("NUMTRADES", "numtrades", "num_trades")),
                       (canonical, ("num_trades",)), (payload, ("num_trades",))), True),
    )
    result = []
    for name, locations, integer in specs:
        values = []
        malformed = False
        for container, keys in locations:
            for key in keys:
                if key not in container:
                    continue
                try:
                    value = _raw_number(container[key], integer=integer)
                except ValueError:
                    malformed = True
                else:
                    if value is not None:
                        values.append(value)
        conflict = bool(values) and any(value != values[0] for value in values[1:])
        if malformed:
            flags.add(f"MALFORMED_RAW_{name}")
        if conflict:
            flags.add(f"CONFLICTING_RAW_{name}")
        result.append(None if malformed or conflict or not values else values[0])
    notes = payload.get("mapping_notes")
    if isinstance(notes, list):
        if _VOLUME_NOTE in notes:
            flags.add("VOLUME_VALUE_FALLBACK_NOT_PROMOTED")
        if _DURATION_NOTE in notes:
            flags.add("DURATION_LEGACY_DAY_NORMALIZATION")
    return tuple(result)


class BondMarketFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7,
    ) -> BondMarketFeatureView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if not isinstance(market_source, str) or not market_source.strip():
            raise ValueError("market_source must be a nonempty string")
        if type(max_market_age_days) is not int or max_market_age_days < 0:
            raise ValueError("max_market_age_days must be a nonnegative integer")
        with self.db.no_autoflush:
            bond = self.db.get(Bond, bond_id)
            if bond is None:
                raise HTTPException(status_code=404, detail="Bond not found")
            snapshot = self.db.execute(
                select(BondMarketSnapshot).where(
                    BondMarketSnapshot.bond_id == bond_id,
                    BondMarketSnapshot.source == market_source,
                    BondMarketSnapshot.trade_date <= as_of_date,
                ).order_by(BondMarketSnapshot.trade_date.desc(),
                           BondMarketSnapshot.id.desc()).limit(1)
            ).scalar_one_or_none()
            events = list(self.db.execute(
                select(BondCashflowEvent).where(
                    BondCashflowEvent.bond_id == bond_id,
                    BondCashflowEvent.source == "moex",
                    BondCashflowEvent.event_date >= as_of_date,
                ).order_by(BondCashflowEvent.event_date.asc(), BondCashflowEvent.id.asc())
            ).scalars())
            return self._project(bond, snapshot, events, as_of_date,
                                 market_source, max_market_age_days)

    @staticmethod
    def _project(
        bond: Bond,
        snapshot: BondMarketSnapshot | None,
        events: list[BondCashflowEvent],
        as_of_date: date,
        source: str,
        threshold: int,
    ) -> BondMarketFeatureView:
        flags: set[str] = set()
        age = (as_of_date - snapshot.trade_date).days if snapshot else None
        status = "MISSING" if snapshot is None else ("FRESH" if age <= threshold else "STALE")
        if status != "FRESH":
            flags.add(f"MARKET_DATA_{status}")
        market = {
            name: getattr(snapshot, column) if snapshot else None
            for name, column in (
                ("price", "price"), ("clean_price", "clean_price"), ("nkd", "nkd"),
                ("yield_to_maturity_pct", "yield_to_maturity"),
                ("duration_years", "duration_years"),
            )
        }
        volume, value, trades = _liquidity(snapshot.raw_payload if snapshot else None, flags)
        if source == "moex":
            duration = normalize_moex_duration(
                snapshot.raw_payload if snapshot else None,
                stored_duration_years=snapshot.duration_years if snapshot else None,
            )
            market["duration_years"] = duration.duration_years
            flags.update(duration.quality_flags)
        market.update(trade_volume=volume, turnover_value=value, num_trades=trades)
        for field, flag in (
            ("price", "PRICE_MISSING"), ("yield_to_maturity_pct", "YIELD_MISSING"),
            ("duration_years", "DURATION_MISSING"), ("trade_volume", "TRADE_VOLUME_MISSING"),
            ("turnover_value", "TURNOVER_VALUE_MISSING"), ("num_trades", "NUM_TRADES_MISSING"),
        ):
            if market[field] is None:
                flags.add(flag)
        grouped = {kind: [e for e in events if e.event_type == kind] for kind in (
            "coupon", "amortization", "redemption", "offer_redemption"
        )}
        matured = bool(not bond.is_perpetual and bond.maturity_date
                       and bond.maturity_date < as_of_date)
        for condition, flag in (
            (not events, "CASHFLOW_SCHEDULE_MISSING"), (bond.is_perpetual, "PERPETUAL_BOND"),
            (bond.is_floating_coupon, "FLOATING_COUPON"),
            (bond.is_subordinated, "SUBORDINATED_BOND"), (matured, "MATURED_BOND"),
            (bond.amortization is False and bool(grouped["amortization"]),
             "AMORTIZATION_METADATA_SCHEDULE_MISMATCH"),
        ):
            if condition:
                flags.add(flag)
        snapshot_id = snapshot.id if snapshot else None
        trade_date = snapshot.trade_date if snapshot else None
        availability = dict(
            has_market_snapshot=snapshot is not None,
            has_price=market["price"] is not None,
            has_clean_price=market["clean_price"] is not None,
            has_nkd=market["nkd"] is not None,
            has_yield_to_maturity=market["yield_to_maturity_pct"] is not None,
            has_duration=market["duration_years"] is not None,
            has_trade_volume=volume is not None, has_turnover_value=value is not None,
            has_num_trades=trades is not None, has_cashflow_schedule=bool(events),
            has_maturity=bond.maturity_date is not None, has_offer=bond.offer_date is not None,
        )
        cashflows = {}
        for kind, rows in grouped.items():
            cashflows[f"next_{kind}_date"] = rows[0].event_date if rows else None
            cashflows[f"has_future_{kind}"] = bool(rows)
        return BondMarketFeatureView(
            bond_id=bond.id, isin=bond.isin, secid=bond.secid, as_of_date=as_of_date,
            market_snapshot_id=snapshot_id, market_trade_date=trade_date, market_source=source,
            market_age_days=age, market_status=status, **market,
            currency=bond.currency, nominal_value=bond.nominal_value, coupon_rate=bond.coupon_rate,
            maturity_date=bond.maturity_date, offer_date=bond.offer_date,
            days_to_maturity=(bond.maturity_date - as_of_date).days
                if not bond.is_perpetual and bond.maturity_date else None,
            days_to_offer=(bond.offer_date - as_of_date).days if bond.offer_date else None,
            is_matured=matured, is_floating_coupon=bond.is_floating_coupon,
            is_subordinated=bond.is_subordinated, is_perpetual=bond.is_perpetual,
            metadata_has_amortization=bond.amortization,
            future_cashflow_event_count=len(events), future_coupon_count=len(grouped["coupon"]),
            future_amortization_count=len(grouped["amortization"]), **cashflows,
            availability=BondMarketFeatureAvailability(**availability), quality_flags=sorted(flags),
            provenance=BondMarketFeatureProvenance(
                market_snapshot_id=snapshot_id, market_source=source, market_trade_date=trade_date,
                selected_cashflow_event_ids=[event.id for event in events],
            ),
        )
