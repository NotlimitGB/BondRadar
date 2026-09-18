"""Pure reduction of caller-supplied Task276 members; no evidence discovery."""

from collections import Counter
from collections.abc import Mapping
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Sequence, get_args

from app.schemas.bond_credit_comparability import RatingTargetKind, SourceNativeRatingCohortKey
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.bond_credit_cohort_relative_value import (
    BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION,
    BondCreditCohortRelativeValueMemberView,
)
from app.schemas.credit_cohort_peer_spread_distribution import (
    CreditCohortPeerSpreadAvailability, CreditCohortPeerSpreadDistributionView,
    CreditCohortPeerSpreadEvidence, CreditCohortPeerSpreadExclusions,
    CreditCohortPeerSpreadProvenance,
)


def _positive_int(value):
    return type(value) is int and value > 0


def _finite_decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _safe_id(value):
    return value if _positive_int(value) else None


def _safe_string(value):
    return value if isinstance(value, str) else None


def _safe_key(key):
    """Inspect source values before making a detached, validated output key."""
    if not isinstance(key, SourceNativeRatingCohortKey):
        return None
    if (type(key.target_kind) is not str or key.target_kind not in get_args(RatingTargetKind)
            or type(key.rating_agency) is not str or key.rating_agency not in get_args(RatingAgency)
            or not _nonblank(key.source_provider) or not _nonblank(key.rating_value_raw)
            or (key.rating_scale_raw is not None and not isinstance(key.rating_scale_raw, str))):
        return None
    return SourceNativeRatingCohortKey(**key.model_dump())


def _valid_member(member):
    key = _safe_key(member.cohort_key)
    return (
        member.contract_version == BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION
        and member.status == "READY" and member.pit_ready is False
        and _positive_int(member.bond_id) and type(member.as_of_date) is date
        and _nonblank(member.market_source) and _finite_decimal(member.spread_to_ofz_bps)
        and key is not None
        and type(member.requested_target_kind) is str
        and type(member.requested_rating_agency) is str
        and member.requested_target_kind == key.target_kind
        and member.requested_rating_agency == key.rating_agency
    )


class CreditCohortPeerSpreadDistributionService:
    @staticmethod
    def build(
        target: BondCreditCohortRelativeValueMemberView,
        candidates: Sequence[BondCreditCohortRelativeValueMemberView],
        *, min_peer_count: int = 1,
    ) -> CreditCohortPeerSpreadDistributionView:
        if not _positive_int(min_peer_count):
            raise ValueError("min_peer_count must be an exact positive int")
        if not isinstance(target, BondCreditCohortRelativeValueMemberView):
            raise ValueError("target must be a Task276 member view")
        if isinstance(candidates, (str, bytes, bytearray, Mapping)):
            raise ValueError("candidates must be an iterable of Task276 member views")
        try:
            snapshot = tuple(candidates)
        except TypeError as exc:
            raise ValueError("candidates must be an iterable of Task276 member views") from exc
        if any(not isinstance(row, BondCreditCohortRelativeValueMemberView) for row in snapshot):
            raise ValueError("candidates must contain only Task276 member views")

        valid_target = _valid_member(target)
        counts = dict.fromkeys((
            "excluded_target_self_count", "excluded_not_ready_count", "invalid_candidate_count",
            "excluded_as_of_mismatch_count", "excluded_market_source_mismatch_count",
            "excluded_cohort_mismatch_count", "unprocessed_candidate_count",
        ), 0)
        flags = set()
        peers = []
        if not valid_target:
            counts["unprocessed_candidate_count"] = len(snapshot)
        else:
            for row in snapshot:
                if type(row.bond_id) is int and row.bond_id == target.bond_id:
                    counts["excluded_target_self_count"] += 1
                    flags.add("TARGET_SELF_EXCLUDED")
                elif row.status != "READY":
                    counts["excluded_not_ready_count"] += 1
                    flags.add("NON_READY_CANDIDATES_EXCLUDED")
                elif not _valid_member(row):
                    counts["invalid_candidate_count"] += 1
                elif row.as_of_date != target.as_of_date:
                    counts["excluded_as_of_mismatch_count"] += 1
                    flags.add("AS_OF_MISMATCH_CANDIDATES_EXCLUDED")
                elif row.market_source != target.market_source:
                    counts["excluded_market_source_mismatch_count"] += 1
                    flags.add("MARKET_SOURCE_MISMATCH_CANDIDATES_EXCLUDED")
                elif row.cohort_key != target.cohort_key:
                    counts["excluded_cohort_mismatch_count"] += 1
                    flags.add("COHORT_MISMATCH_CANDIDATES_EXCLUDED")
                else:
                    peers.append(CreditCohortPeerSpreadEvidence(
                        bond_id=row.bond_id, isin=_safe_string(row.isin), secid=_safe_string(row.secid),
                        spread_to_ofz_bps=row.spread_to_ofz_bps,
                        rating_event_id=_safe_id(row.rating_event_id),
                        market_snapshot_id=_safe_id(row.market_snapshot_id),
                    ))
        # Full compact evidence is a diagnostic tie-break, never a duplicate selection.
        peers.sort(key=lambda row: (row.bond_id, row.model_dump_json()))
        duplicate_ids = sorted(bond_id for bond_id, count in Counter(row.bond_id for row in peers).items() if count > 1)
        n = len(peers)
        if not valid_target:
            status = "TARGET_MEMBER_INVALID"
        elif counts["invalid_candidate_count"] or duplicate_ids:
            status = "PEER_INPUT_INVALID"
        elif not n:
            status = "NO_ELIGIBLE_PEERS"
        elif n < min_peer_count:
            status = "INSUFFICIENT_PEERS"
        else:
            status = "READY"
        if status != "READY":
            flags.add(status)
        if duplicate_ids:
            flags.add("DUPLICATE_PEER_IDS")

        minimum = median = mean = maximum = difference = percentile = None
        has_distribution = status in ("READY", "INSUFFICIENT_PEERS")
        if has_distribution:
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                spreads = sorted(row.spread_to_ofz_bps for row in peers)
                minimum, maximum = spreads[0], spreads[-1]
                middle = n // 2
                median = spreads[middle] if n % 2 else (spreads[middle - 1] + spreads[middle]) / Decimal("2")
                mean = sum(spreads, Decimal("0")) / Decimal(n)
                difference = target.spread_to_ofz_bps - median
                if status == "READY":
                    lower = sum(value < target.spread_to_ofz_bps for value in spreads)
                    equal = sum(value == target.spread_to_ofz_bps for value in spreads)
                    percentile = Decimal("100") * (Decimal(lower) + Decimal(equal) / Decimal("2")) / Decimal(n)

        return CreditCohortPeerSpreadDistributionView(
            target_bond_id=_safe_id(target.bond_id),
            as_of_date=target.as_of_date if type(target.as_of_date) is date else None,
            market_source=target.market_source if _nonblank(target.market_source) else None,
            cohort_key=_safe_key(target.cohort_key),
            target_spread_to_ofz_bps=target.spread_to_ofz_bps if _finite_decimal(target.spread_to_ofz_bps) else None,
            status=status, candidate_count=len(snapshot), eligible_peer_count=n, min_peer_count=min_peer_count,
            peer_min_spread_bps=minimum, peer_median_spread_bps=median, peer_mean_spread_bps=mean,
            peer_max_spread_bps=maximum, spread_minus_peer_median_bps=difference,
            target_spread_percentile=percentile, eligible_peers=peers,
            exclusions=CreditCohortPeerSpreadExclusions(**counts, duplicate_peer_bond_ids=duplicate_ids),
            availability=CreditCohortPeerSpreadAvailability(
                has_valid_target=valid_target, has_eligible_peers=bool(n),
                has_minimum_peer_count=status == "READY", has_peer_distribution=has_distribution,
                has_peer_median=has_distribution, has_target_spread_percentile=percentile is not None,
                has_spread_vs_peer_median=has_distribution, has_ready_peer_distribution=status == "READY",
            ),
            quality_flags=sorted(flags),
            provenance=CreditCohortPeerSpreadProvenance(
                target_member_contract_version=_safe_string(target.contract_version),
                target_rating_event_id=_safe_id(target.rating_event_id),
                target_market_snapshot_id=_safe_id(target.market_snapshot_id),
                candidate_count=len(snapshot), eligible_peer_bond_ids=[row.bond_id for row in peers],
                min_peer_count=min_peer_count,
            ),
        )
