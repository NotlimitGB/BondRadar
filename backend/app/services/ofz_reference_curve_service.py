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
    BondRelativeValueProvenance,
    BondRelativeValueView,
    OfzCurveDiagnostics,
    OfzCurveNode,
    OfzReferenceCurveView,
)
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.moex_duration_semantics import normalize_moex_duration


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


def _identity_text(bond: Bond) -> str:
    return " ".join((bond.name, bond.secid or "", bond.isin or "")).upper()


def _is_ofz(bond: Bond) -> bool:
    text = _identity_text(bond)
    return (any(marker in text for marker in ("ОФЗ", "OFZ", "FEDERAL LOAN BOND"))
            or (bond.isin or "").upper().startswith("SU"))


def _eligible(bond: Bond, profile: BondSecurityMasterProfile | None, day: date) -> bool:
    text = _identity_text(bond)
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
                if not _is_ofz(bond):
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
        ytm = _finite(target.yield_to_maturity_pct)
        duration = _finite(target.duration_years)
        failures = []
        flags = set()
        conditions = (
            (target.market_status == "MISSING", "TARGET_MARKET_MISSING"),
            (target.market_status == "STALE", "TARGET_MARKET_STALE"),
            (ytm is None, "TARGET_YIELD_MISSING"),
            (duration is None or duration <= 0, "TARGET_DURATION_MISSING"),
            (curve.status != "READY", "CURVE_NOT_READY"),
            (target.market_trade_date is not None and curve.curve_trade_date is not None
             and target.market_trade_date != curve.curve_trade_date,
             "TARGET_CURVE_DATE_MISMATCH"),
            (curve.status == "READY" and duration is not None and duration > 0
             and (duration < curve.min_duration_years or duration > curve.max_duration_years),
             "TARGET_DURATION_OUTSIDE_CURVE"),
        )
        for condition, status in conditions:
            if condition:
                failures.append(status)
                flags.add(status)
        if duration is not None and duration <= 0:
            flags.add("TARGET_DURATION_NONPOSITIVE")
        if target.yield_to_maturity_pct is not None and ytm is None:
            flags.add("TARGET_YIELD_INVALID")
        if target.duration_years is not None and duration is None:
            flags.add("TARGET_DURATION_INVALID")
        if curve.status != "READY":
            flags.add(f"CURVE_{curve.status}")
        if any(node.aggregation_method == "MEDIAN" for node in curve.nodes):
            flags.add("CURVE_DUPLICATE_DURATION_AGGREGATED")
        provenance = dict(
            target_market_snapshot_id=target.market_snapshot_id,
            target_market_trade_date=target.market_trade_date,
            curve_trade_date=curve.curve_trade_date,
        )
        reference = spread = bps = method = None
        if not failures:
            exact = next((n for n in curve.nodes if n.duration_years == duration), None)
            if exact is not None:
                lower = upper = exact
                method = "EXACT_NODE"
                reference = exact.yield_to_maturity_pct
            else:
                lower = next(n for n in reversed(curve.nodes) if n.duration_years < duration)
                upper = next(n for n in curve.nodes if n.duration_years > duration)
                method = "LINEAR_INTERPOLATION"
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                if reference is None:
                    reference = lower.yield_to_maturity_pct + (
                        (duration - lower.duration_years)
                        * (upper.yield_to_maturity_pct - lower.yield_to_maturity_pct)
                        / (upper.duration_years - lower.duration_years)
                    )
                spread = ytm - reference
                bps = spread * Decimal(100)
            for side, node in (("lower", lower), ("upper", upper)):
                provenance[f"{side}_curve_duration_years"] = node.duration_years
                provenance[f"{side}_curve_yield_pct"] = node.yield_to_maturity_pct
                provenance[f"{side}_component_snapshot_ids"] = node.component_snapshot_ids
        return BondRelativeValueView(
            bond_id=target.bond_id, isin=target.isin, secid=target.secid,
            as_of_date=as_of_date, market_source=market_source,
            status=failures[0] if failures else "READY",
            target_yield_to_maturity_pct=ytm, target_duration_years=duration,
            reference_ofz_yield_pct=reference, spread_to_ofz_pp=spread,
            spread_to_ofz_bps=bps, interpolation_method=method,
            curve=curve, provenance=BondRelativeValueProvenance(**provenance),
            quality_flags=sorted(flags),
        )
