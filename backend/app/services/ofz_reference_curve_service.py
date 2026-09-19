"""Deterministic SELECT-only nominal RUB OFZ reference; no historical PIT claim."""

from collections import defaultdict
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.ofz_reference_curve import (
    BondRelativeValueView,
    OfzCurveDiagnostics,
    OfzCurveNode,
    OfzReferenceCurveView,
)
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.moex_duration_semantics import normalize_moex_duration
from app.services.ofz_identity import is_ofz_instrument
from app.services.ofz_relative_value_evaluator import evaluate_market_against_ofz_curve


def _validate(as_of_date: date, market_source: str, **ages: int) -> None:
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if not isinstance(market_source, str) or not market_source.strip():
        raise ValueError("market_source must be a nonempty string")
    for name, value in ages.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")


def _finite(value: object) -> Decimal | None:
    # SQLAlchemy Numeric supplies Decimal; never promote bools or binary floats.
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _structural_marker_text(bond: Bond) -> str:
    return " ".join((bond.name, bond.secid or "", bond.isin or "")).upper()


def _eligible(bond: Bond, profile: BondSecurityMasterProfile | None, day: date) -> bool:
    text = _structural_marker_text(bond)
    if any(marker in text for marker in (
        "ОФЗ-ИН", "OFZ-IN", "ОФЗ-ПК", "OFZ-PK", "ОФЗ-АД", "OFZ-AD",
    )):
        return False
    return bool(
        profile is not None
        and profile.currency_state == "verified" and profile.currency_code == "RUB"
        and profile.coupon_structure == "fixed"
        and profile.amortization_structure == "bullet"
        and profile.perpetual_structure == "dated"
        and profile.maturity_state == "verified" and profile.maturity_date is not None
        and profile.maturity_date >= day
    )


class OfzReferenceCurveService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_curve(
        self, as_of_date: date, *, market_source: str = "moex",
        max_curve_age_days: int = 7,
    ) -> OfzReferenceCurveView:
        _validate(as_of_date, market_source, max_curve_age_days=max_curve_age_days)
        counts = dict.fromkeys(OfzCurveDiagnostics.model_fields, 0)
        points = []
        with self.db.no_autoflush:
            candidates = self.db.execute(
                select(Bond, BondSecurityMasterProfile).outerjoin(
                    BondSecurityMasterProfile, BondSecurityMasterProfile.bond_id == Bond.id,
                ).order_by(Bond.id)
            ).all()
            for bond, profile in candidates:
                if not is_ofz_instrument(isin=bond.isin, secid=bond.secid):
                    continue
                counts["ofz_identity_count"] += 1
                if not _eligible(bond, profile, as_of_date):
                    counts["excluded_security_master_count"] += 1
                    continue
                counts["security_master_eligible_count"] += 1
                # Narrow columns deliberately omit legacy persisted spread.
                snapshot = self.db.execute(
                    select(
                        BondMarketSnapshot.id, BondMarketSnapshot.trade_date,
                        BondMarketSnapshot.yield_to_maturity, BondMarketSnapshot.duration_years,
                        BondMarketSnapshot.raw_payload,
                    ).where(
                        BondMarketSnapshot.bond_id == bond.id,
                        BondMarketSnapshot.source == market_source,
                        BondMarketSnapshot.trade_date <= as_of_date,
                    ).order_by(BondMarketSnapshot.trade_date.desc(),
                               BondMarketSnapshot.id.desc()).limit(1)
                ).first()
                if snapshot is None:
                    counts["excluded_missing_snapshot_count"] += 1
                    continue
                counts["with_market_snapshot_count"] += 1
                ytm = _finite(snapshot.yield_to_maturity)
                duration = _finite(snapshot.duration_years)
                duration_status = None
                if market_source == "moex":
                    normalized = normalize_moex_duration(
                        snapshot.raw_payload, stored_duration_years=snapshot.duration_years,
                    )
                    duration = normalized.duration_years
                    duration_status = normalized.status
                if ytm is None:
                    reason = "missing" if snapshot.yield_to_maturity is None else "invalid"
                    counts[f"excluded_{reason}_yield_count"] += 1
                    continue
                if duration is None:
                    reason = ("missing" if duration_status == "RAW_DURATION_MISSING"
                              or (duration_status is None and snapshot.duration_years is None) else "invalid")
                    counts[f"excluded_{reason}_duration_count"] += 1
                    continue
                if duration <= 0:
                    counts["excluded_nonpositive_duration_count"] += 1
                    continue
                counts["with_valid_yield_duration_count"] += 1
                if (as_of_date - snapshot.trade_date).days > max_curve_age_days:
                    counts["excluded_stale_count"] += 1
                    continue
                counts["fresh_market_count"] += 1
                points.append((bond, snapshot, duration, ytm))

        curve_date = max((point[1].trade_date for point in points), default=None)
        members = [point for point in points if point[1].trade_date == curve_date]
        counts["curve_date_member_count"] = len(members)
        counts["excluded_curve_date_mismatch_count"] = len(points) - len(members)
        grouped = defaultdict(list)
        for point in members:
            grouped[point[2]].append(point)
        nodes = []
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            for duration, components in sorted(grouped.items()):
                components.sort(key=lambda p: (p[0].id, p[1].id))
                yields = sorted(p[3] for p in components)
                middle = len(yields) // 2
                median = (yields[middle] if len(yields) % 2
                          else (yields[middle - 1] + yields[middle]) / Decimal(2))
                nodes.append(OfzCurveNode(
                    duration_years=duration, yield_to_maturity_pct=median,
                    aggregation_method="SINGLE" if len(components) == 1 else "MEDIAN",
                    component_bond_ids=[p[0].id for p in components],
                    component_snapshot_ids=[p[1].id for p in components],
                    component_secids=[p[0].secid for p in components],
                    component_isins=[p[0].isin for p in components],
                    component_yields_pct=[p[3] for p in components],
                ))
        counts["distinct_duration_node_count"] = len(nodes)
        if not counts["security_master_eligible_count"]:
            status = "NO_ELIGIBLE_OFZ"
        elif not points:
            status = "NO_FRESH_MARKET_DATA"
        elif len(nodes) < 2:
            status = "INSUFFICIENT_DISTINCT_DURATIONS"
        else:
            status = "READY"
        return OfzReferenceCurveView(
            as_of_date=as_of_date, market_source=market_source, status=status,
            curve_trade_date=curve_date, node_count=len(nodes), nodes=nodes,
            min_duration_years=nodes[0].duration_years if nodes else None,
            max_duration_years=nodes[-1].duration_years if nodes else None,
            diagnostics=OfzCurveDiagnostics(**counts),
        )

    def evaluate_bond(
        self, bond_id: int, as_of_date: date, *, market_source: str = "moex",
        max_market_age_days: int = 7, max_curve_age_days: int = 7,
    ) -> BondRelativeValueView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        _validate(as_of_date, market_source, max_market_age_days=max_market_age_days,
                  max_curve_age_days=max_curve_age_days)
        with self.db.no_autoflush:
            target = BondMarketFeatureService(self.db).build_for_bond(
                bond_id, as_of_date, market_source=market_source,
                max_market_age_days=max_market_age_days,
            )
            curve = self.build_curve(as_of_date, market_source=market_source,
                                     max_curve_age_days=max_curve_age_days)
        return evaluate_market_against_ofz_curve(
            target, curve, as_of_date=as_of_date, market_source=market_source,
        )
