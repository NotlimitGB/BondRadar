"""Pure evaluation of a preselected target against a prebuilt OFZ curve."""

from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from app.schemas.bond_market_features import CONTRACT_VERSION, BondMarketFeatureView
from app.schemas.ofz_reference_curve import (
    OFZ_CURVE_CONTRACT_VERSION,
    BondRelativeValueProvenance,
    BondRelativeValueView,
    OfzCurveNode,
    OfzReferenceCurveView,
)


def _finite(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _positive_id(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _calendar_date(value: object) -> date | None:
    return value if type(value) is date else None


def _target_contract_valid(
    target: BondMarketFeatureView, as_of_date: date, market_source: str,
) -> bool:
    if (
        target.contract_version != CONTRACT_VERSION
        or target.pit_ready is not False
        or type(target.as_of_date) is not date
        or target.as_of_date != as_of_date
        or not isinstance(target.market_source, str)
        or target.market_source != market_source
        or target.market_status not in ("MISSING", "FRESH", "STALE")
    ):
        return False
    if target.market_status == "FRESH":
        return (
            _positive_id(target.market_snapshot_id) is not None
            and type(target.market_trade_date) is date
            and target.market_trade_date <= as_of_date
        )
    return True


def _curve_contract_valid(
    curve: OfzReferenceCurveView, as_of_date: date, market_source: str,
) -> bool:
    return (
        curve.contract_version == OFZ_CURVE_CONTRACT_VERSION
        and curve.pit_ready is False
        and type(curve.as_of_date) is date
        and curve.as_of_date == as_of_date
        and isinstance(curve.market_source, str)
        and curve.market_source == market_source
    )


def _ready_curve_integrity(curve: OfzReferenceCurveView) -> bool:
    if curve.status != "READY":
        return True
    if (
        not isinstance(curve.nodes, list)
        or type(curve.as_of_date) is not date
        or type(curve.curve_trade_date) is not date
        or curve.curve_trade_date > curve.as_of_date
        or type(curve.node_count) is not int
        or curve.node_count != len(curve.nodes)
        or curve.node_count < 2
        or not isinstance(curve.min_duration_years, Decimal)
        or not isinstance(curve.max_duration_years, Decimal)
    ):
        return False
    previous = None
    for node in curve.nodes:
        if not isinstance(node, OfzCurveNode):
            return False
        duration = _finite(node.duration_years)
        ytm = _finite(node.yield_to_maturity_pct)
        if duration is None or duration <= 0 or ytm is None:
            return False
        if previous is not None and duration <= previous:
            return False
        previous = duration
    return (
        curve.min_duration_years.is_finite()
        and curve.max_duration_years.is_finite()
        and curve.min_duration_years == curve.nodes[0].duration_years
        and curve.max_duration_years == curve.nodes[-1].duration_years
    )


def evaluate_market_against_ofz_curve(
    target: BondMarketFeatureView,
    curve: OfzReferenceCurveView,
    *,
    as_of_date: date,
    market_source: str,
) -> BondRelativeValueView:
    """Apply Task268 status, interpolation, spread, and provenance semantics."""
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if not isinstance(market_source, str) or not market_source.strip():
        raise ValueError("market_source must be a nonempty string")
    if not isinstance(target, BondMarketFeatureView):
        raise ValueError("target must be a BondMarketFeatureView")
    if not isinstance(curve, OfzReferenceCurveView):
        raise ValueError("curve must be an OfzReferenceCurveView")

    target_contract_valid = _target_contract_valid(target, as_of_date, market_source)
    curve_contract_valid = _curve_contract_valid(curve, as_of_date, market_source)
    curve_integrity_valid = _ready_curve_integrity(curve)
    curve_ready = curve_contract_valid and curve_integrity_valid and curve.status == "READY"
    ytm = _finite(target.yield_to_maturity_pct)
    duration = _finite(target.duration_years)
    failures: list[str] = []
    flags: set[str] = set()
    conditions = (
        (not target_contract_valid, "TARGET_MARKET_MISSING"),
        (target.market_status == "MISSING", "TARGET_MARKET_MISSING"),
        (target.market_status == "STALE", "TARGET_MARKET_STALE"),
        (ytm is None, "TARGET_YIELD_MISSING"),
        (duration is None or duration <= 0, "TARGET_DURATION_MISSING"),
        (not curve_ready, "CURVE_NOT_READY"),
        (target.market_trade_date is not None and curve.curve_trade_date is not None
         and target.market_trade_date != curve.curve_trade_date,
         "TARGET_CURVE_DATE_MISMATCH"),
        (curve_ready and duration is not None and duration > 0
         and (duration < curve.min_duration_years or duration > curve.max_duration_years),
         "TARGET_DURATION_OUTSIDE_CURVE"),
    )
    for condition, status in conditions:
        if condition:
            failures.append(status)
            flags.add(status)
    if not target_contract_valid:
        flags.add("TARGET_CONTRACT_INVALID")
    if not curve_contract_valid or not curve_integrity_valid:
        flags.add("CURVE_INTEGRITY_INVALID")
    if duration is not None and duration <= 0:
        flags.add("TARGET_DURATION_NONPOSITIVE")
    if target.yield_to_maturity_pct is not None and ytm is None:
        flags.add("TARGET_YIELD_INVALID")
    if target.duration_years is not None and duration is None:
        flags.add("TARGET_DURATION_INVALID")
    if curve_contract_valid and curve_integrity_valid and curve.status != "READY":
        flags.add(f"CURVE_{curve.status}")
    curve_nodes = curve.nodes if isinstance(curve.nodes, list) else []
    if any(node.aggregation_method == "MEDIAN" for node in curve_nodes
           if isinstance(node, OfzCurveNode)):
        flags.add("CURVE_DUPLICATE_DURATION_AGGREGATED")

    provenance = dict(
        target_market_snapshot_id=_positive_id(target.market_snapshot_id),
        target_market_trade_date=_calendar_date(target.market_trade_date),
        curve_trade_date=_calendar_date(curve.curve_trade_date),
    )
    reference = spread = bps = method = None
    if not failures:
        exact = next((node for node in curve.nodes if node.duration_years == duration), None)
        if exact is not None:
            lower = upper = exact
            method = "EXACT_NODE"
            reference = exact.yield_to_maturity_pct
        else:
            lower = next(node for node in reversed(curve.nodes) if node.duration_years < duration)
            upper = next(node for node in curve.nodes if node.duration_years > duration)
            method = "LINEAR_INTERPOLATION"
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            if reference is None:
                reference = lower.yield_to_maturity_pct + (
                    (duration - lower.duration_years)
                    * (upper.yield_to_maturity_pct - lower.yield_to_maturity_pct)
                    / (upper.duration_years - lower.duration_years)
                )
            spread = ytm - reference
            bps = spread * Decimal("100")
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
