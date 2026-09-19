"""Pure coverage reducer for already-composed Task282 feature views."""

from collections.abc import Mapping, Sequence, Set
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from app.schemas.bond_m3_feature_view import BondM3FeatureView
from app.schemas.m3_coverage_audit import (
    M3CoverageAuditProvenance,
    M3CoverageAuditView,
    M3CoverageBottleneck,
    M3FeatureCoverage,
    M3FeatureCoverageSummary,
    M3StatusBreakdownEntry,
    M3StatusBreakdowns,
)


_FEATURE_NAMES = (
    "market_fresh",
    "relative_value_ready",
    "has_credit_rating_evidence",
    "has_credit_comparable_cohort",
    "liquidity_score_ready",
    "modified_duration_ready",
    "dv01_ready",
    "liquidity_relative_value_ready",
    "has_peer_distribution_context",
    "peer_distribution_ready",
    "all_supplied_evidence_consistent",
)

_CORE_FEATURE_NAMES = (
    "all_supplied_evidence_consistent",
    "market_fresh",
    "relative_value_ready",
    "has_credit_rating_evidence",
    "has_credit_comparable_cohort",
    "liquidity_score_ready",
    "modified_duration_ready",
    "dv01_ready",
    "liquidity_relative_value_ready",
)

_BOTTLENECKS = (
    ("MARKET_NOT_FRESH", "market_fresh"),
    ("RELATIVE_VALUE_NOT_READY", "relative_value_ready"),
    ("CREDIT_RATING_EVIDENCE_MISSING", "has_credit_rating_evidence"),
    ("CREDIT_COMPARABLE_COHORT_MISSING", "has_credit_comparable_cohort"),
    ("LIQUIDITY_SCORE_NOT_READY", "liquidity_score_ready"),
    ("MODIFIED_DURATION_NOT_READY", "modified_duration_ready"),
    ("DV01_NOT_READY", "dv01_ready"),
    ("LIQUIDITY_RELATIVE_VALUE_NOT_READY", "liquidity_relative_value_ready"),
    ("COMPOSITE_EVIDENCE_INVALID", "all_supplied_evidence_consistent"),
)


def _percentage(count: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return Decimal(count) * Decimal("100") / Decimal(denominator)


def _require_status(value: object, family: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{family} status must be a nonblank string")
    return value


def _feature_coverage(
    composites: tuple[BondM3FeatureView, ...], feature: str
) -> M3FeatureCoverage:
    available = [
        composite.bond_id
        for composite in composites
        if getattr(composite.availability, feature)
    ]
    missing = [
        composite.bond_id
        for composite in composites
        if not getattr(composite.availability, feature)
    ]
    return M3FeatureCoverage(
        available_count=len(available),
        missing_count=len(missing),
        coverage_pct=_percentage(len(available), len(composites)),
        available_bond_ids=available,
        missing_bond_ids=missing,
    )


def _status_breakdown(
    rows: tuple[tuple[int, str], ...], denominator: int
) -> list[M3StatusBreakdownEntry]:
    grouped: dict[str, list[int]] = {}
    for bond_id, status in rows:
        grouped.setdefault(status, []).append(bond_id)
    return [
        M3StatusBreakdownEntry(
            status=status,
            count=len(grouped[status]),
            pct=_percentage(len(grouped[status]), denominator),
            bond_ids=grouped[status],
        )
        for status in sorted(grouped)
    ]


class M3CoverageAuditReducer:
    """Aggregate factual Task282 coverage for one date/source context."""

    @staticmethod
    def build(composites: Sequence[BondM3FeatureView]) -> M3CoverageAuditView:
        if (
            isinstance(composites, (str, bytes, bytearray, Mapping, Set))
            or not isinstance(composites, Sequence)
        ):
            raise ValueError("composites must be a deterministic sequence")

        snapshot = tuple(composites)
        if not snapshot:
            raise ValueError("composites must not be empty")

        for composite in snapshot:
            if type(composite) is not BondM3FeatureView:
                raise ValueError("every composite must be BondM3FeatureView")
            if composite.contract_version != "bond-m3-feature-view-v1":
                raise ValueError("unsupported Task282 contract version")
            if composite.pit_ready is not False:
                raise ValueError("Task282 PIT contract must be false")
            if type(composite.bond_id) is not int or composite.bond_id <= 0:
                raise ValueError("bond_id must be an exact positive int")
            if type(composite.as_of_date) is not date:
                raise ValueError("as_of_date must be an exact date")
            if (
                type(composite.market_source) is not str
                or not composite.market_source.strip()
            ):
                raise ValueError("market_source must be a nonblank string")
            if composite.status not in ("CONSISTENT", "EVIDENCE_INVALID"):
                raise ValueError("unsupported Task282 status")

            availability = composite.availability
            for feature in _FEATURE_NAMES:
                if type(getattr(availability, feature, None)) is not bool:
                    raise ValueError("Task282 availability fields must be bool")
            consistent = composite.status == "CONSISTENT"
            if availability.all_supplied_evidence_consistent is not consistent:
                raise ValueError("Task282 status and consistency flag disagree")
            has_peer = composite.peer_distribution is not None
            if availability.has_peer_distribution_context is not has_peer:
                raise ValueError("Task282 peer context flag disagrees with evidence")
            if (
                availability.peer_distribution_ready
                and not availability.has_peer_distribution_context
            ):
                raise ValueError("READY peer distribution requires peer context")

            _require_status(composite.market.market_status, "market")
            _require_status(composite.relative_value.status, "relative value")
            _require_status(composite.liquidity.score_status, "liquidity")
            _require_status(composite.modified_duration.status, "modified duration")
            _require_status(composite.dv01.status, "DV01")
            _require_status(
                composite.liquidity_relative_value.status,
                "liquidity relative value",
            )
            if has_peer:
                _require_status(composite.peer_distribution.status, "peer distribution")

        ordered = tuple(sorted(snapshot, key=lambda composite: composite.bond_id))
        bond_ids = [composite.bond_id for composite in ordered]
        if len(bond_ids) != len(set(bond_ids)):
            raise ValueError("duplicate bond IDs are not allowed")
        as_of_date = ordered[0].as_of_date
        market_source = ordered[0].market_source
        if any(composite.as_of_date != as_of_date for composite in ordered):
            raise ValueError("all composites must share one as-of date")
        if any(composite.market_source != market_source for composite in ordered):
            raise ValueError("all composites must share one market source")

        coverage = {
            feature: _feature_coverage(ordered, feature) for feature in _FEATURE_NAMES
        }
        feature_coverage = M3FeatureCoverageSummary(**coverage)

        consistent_count = sum(
            composite.status == "CONSISTENT" for composite in ordered
        )
        invalid_count = len(ordered) - consistent_count

        core_ids = [
            composite.bond_id
            for composite in ordered
            if all(
                getattr(composite.availability, feature)
                for feature in _CORE_FEATURE_NAMES
            )
        ]
        core_id_set = set(core_ids)
        extended_ids = [
            composite.bond_id
            for composite in ordered
            if composite.bond_id in core_id_set
            and composite.availability.has_peer_distribution_context
            and composite.availability.peer_distribution_ready
        ]
        bottlenecks = []
        for key, feature in _BOTTLENECKS:
            affected_ids = coverage[feature].missing_bond_ids
            if affected_ids:
                bottlenecks.append(
                    M3CoverageBottleneck(
                        key=key,
                        missing_count=len(affected_ids),
                        missing_pct=_percentage(len(affected_ids), len(ordered)),
                        affected_bond_ids=affected_ids,
                    )
                )
        bottlenecks.sort(key=lambda item: (-item.missing_count, item.key))

        peer_rows = tuple(
            (composite.bond_id, composite.peer_distribution.status)
            for composite in ordered
            if composite.peer_distribution is not None
        )
        peer_context_count = len(peer_rows)
        status_breakdowns = M3StatusBreakdowns(
            market=_status_breakdown(
                tuple((c.bond_id, c.market.market_status) for c in ordered),
                len(ordered),
            ),
            relative_value=_status_breakdown(
                tuple((c.bond_id, c.relative_value.status) for c in ordered),
                len(ordered),
            ),
            liquidity=_status_breakdown(
                tuple((c.bond_id, c.liquidity.score_status) for c in ordered),
                len(ordered),
            ),
            modified_duration=_status_breakdown(
                tuple((c.bond_id, c.modified_duration.status) for c in ordered),
                len(ordered),
            ),
            dv01=_status_breakdown(
                tuple((c.bond_id, c.dv01.status) for c in ordered),
                len(ordered),
            ),
            liquidity_relative_value=_status_breakdown(
                tuple(
                    (c.bond_id, c.liquidity_relative_value.status) for c in ordered
                ),
                len(ordered),
            ),
            peer_distribution=_status_breakdown(peer_rows, peer_context_count),
        )

        peer_ready_count = coverage["peer_distribution_ready"].available_count
        return M3CoverageAuditView(
            as_of_date=as_of_date,
            market_source=market_source,
            universe_size=len(ordered),
            bond_ids=bond_ids,
            consistent_composite_count=consistent_count,
            invalid_composite_count=invalid_count,
            consistent_composite_pct=_percentage(consistent_count, len(ordered)),
            invalid_composite_pct=_percentage(invalid_count, len(ordered)),
            feature_coverage=feature_coverage,
            peer_context_count=peer_context_count,
            peer_context_pct=_percentage(peer_context_count, len(ordered)),
            peer_distribution_ready_count=peer_ready_count,
            peer_distribution_ready_pct=_percentage(
                peer_ready_count, peer_context_count
            ),
            core_m3_complete_count=len(core_ids),
            core_m3_complete_pct=_percentage(len(core_ids), len(ordered)),
            core_m3_complete_bond_ids=core_ids,
            core_m3_incomplete_bond_ids=[
                bond_id for bond_id in bond_ids if bond_id not in core_id_set
            ],
            extended_m3_complete_count=len(extended_ids),
            extended_m3_complete_pct=_percentage(len(extended_ids), len(ordered)),
            bottlenecks=bottlenecks,
            status_breakdowns=status_breakdowns,
            provenance=M3CoverageAuditProvenance(
                as_of_date=as_of_date,
                market_source=market_source,
                universe_size=len(ordered),
                bond_ids=bond_ids,
            ),
        )
