"""Pure orchestration from a validated Task280 batch to the Task277 reducer."""

from datetime import date
from typing import get_args

from app.schemas.bond_credit_cohort_relative_value import (
    BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION,
    BondCreditCohortRelativeValueMemberView,
    CreditCohortRelativeValueMemberStatus,
)
from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.credit_cohort_batch_members import (
    CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION,
    CreditCohortBatchItemStatus,
    CreditCohortBatchMemberItem,
    CreditCohortBatchMemberProvenance,
    CreditCohortBatchMemberView,
    CreditCohortBatchStatus,
)
from app.schemas.credit_cohort_peer_distribution_orchestration import (
    CreditCohortPeerDistributionOrchestrationAvailability,
    CreditCohortPeerDistributionOrchestrationProvenance,
    CreditCohortPeerDistributionOrchestrationView,
)
from app.schemas.ofz_reference_curve import (
    OFZ_CURVE_CONTRACT_VERSION,
    CurveStatus,
    OfzReferenceCurveView,
)
from app.services.credit_cohort_peer_spread_distribution_service import (
    CreditCohortPeerSpreadDistributionService,
)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _strict_ids(value: object) -> bool:
    return (
        type(value) is list
        and all(_positive_int(item) for item in value)
        and value == sorted(value)
        and len(value) == len(set(value))
    )


def _safe_ids(value: object) -> list[int]:
    return list(value) if type(value) is list and all(
        _positive_int(item) for item in value
    ) else []


def _safe_date(value: object) -> date | None:
    return value if type(value) is date else None


def _safe_string(value: object) -> str | None:
    return value if type(value) is str else None


def _safe_nonblank(value: object) -> str | None:
    return value if _nonblank(value) else None


def _safe_count(value: object) -> int | None:
    return value if _nonnegative_int(value) else None


def _safe_literal(value: object, literal_type):
    return value if type(value) is str and value in get_args(literal_type) else None


def _valid_member(
    member: object,
    item_id: int,
    batch: CreditCohortBatchMemberView,
) -> bool:
    return (
        isinstance(member, BondCreditCohortRelativeValueMemberView)
        and member.contract_version
        == BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION
        and member.pit_ready is False
        and type(member.bond_id) is int
        and member.bond_id == item_id
        and type(member.as_of_date) is date
        and member.as_of_date == batch.as_of_date
        and type(member.market_source) is str
        and member.market_source == batch.market_source
        and member.requested_target_kind == batch.requested_target_kind
        and member.requested_rating_agency == batch.requested_rating_agency
        and type(member.status) is str
        and member.status in get_args(CreditCohortRelativeValueMemberStatus)
    )


def _valid_batch(batch: CreditCohortBatchMemberView) -> bool:
    if (
        batch.contract_version != CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION
        or batch.pit_ready is not False
        or type(batch.as_of_date) is not date
        or not _nonblank(batch.market_source)
        or type(batch.requested_target_kind) is not str
        or batch.requested_target_kind not in get_args(RatingTargetKind)
        or type(batch.requested_rating_agency) is not str
        or batch.requested_rating_agency not in get_args(RatingAgency)
        or batch.status not in get_args(CreditCohortBatchStatus)
        or not _strict_ids(batch.requested_bond_ids)
        or not batch.requested_bond_ids
        or not _strict_ids(batch.existing_bond_ids)
        or not _strict_ids(batch.missing_bond_ids)
    ):
        return False

    requested = set(batch.requested_bond_ids)
    existing = set(batch.existing_bond_ids)
    missing = set(batch.missing_bond_ids)
    if (
        not existing.issubset(requested)
        or not missing.issubset(requested)
        or existing & missing
        or existing | missing != requested
    ):
        return False

    counts = (
        batch.requested_bond_count,
        batch.existing_bond_count,
        batch.missing_bond_count,
        batch.built_member_count,
        batch.ready_member_count,
        batch.unavailable_member_count,
    )
    if not all(_nonnegative_int(value) for value in counts):
        return False
    if (
        batch.requested_bond_count != len(batch.requested_bond_ids)
        or batch.existing_bond_count != len(batch.existing_bond_ids)
        or batch.missing_bond_count != len(batch.missing_bond_ids)
        or batch.built_member_count != batch.existing_bond_count
        or batch.ready_member_count + batch.unavailable_member_count
        != batch.built_member_count
    ):
        return False

    expected_status = (
        "NO_EXISTING_BONDS"
        if not existing
        else "PARTIAL"
        if missing
        else "COMPLETE"
    )
    if batch.status != expected_status:
        return False
    if type(batch.items) is not list or len(batch.items) != len(batch.requested_bond_ids):
        return False
    if any(not isinstance(item, CreditCohortBatchMemberItem) for item in batch.items):
        return False
    if [item.bond_id for item in batch.items] != batch.requested_bond_ids:
        return False

    ready_count = 0
    unavailable_count = 0
    for item in batch.items:
        if item.bond_id in existing:
            if item.build_status != "BUILT" or not _valid_member(
                item.member, item.bond_id, batch
            ):
                return False
            if item.member.status == "READY":
                ready_count += 1
            else:
                unavailable_count += 1
        elif item.build_status != "BOND_NOT_FOUND" or item.member is not None:
            return False
    if (
        ready_count != batch.ready_member_count
        or unavailable_count != batch.unavailable_member_count
    ):
        return False

    curve = batch.shared_curve
    if existing:
        if (
            batch.curve_built is not True
            or not isinstance(curve, OfzReferenceCurveView)
            or curve.contract_version != OFZ_CURVE_CONTRACT_VERSION
            or curve.pit_ready is not False
            or type(curve.as_of_date) is not date
            or curve.as_of_date != batch.as_of_date
            or type(curve.market_source) is not str
            or curve.market_source != batch.market_source
            or type(curve.status) is not str
            or curve.status not in get_args(CurveStatus)
            or not _nonnegative_int(curve.node_count)
        ):
            return False
    elif batch.curve_built is not False or curve is not None:
        return False

    provenance = batch.provenance
    if not isinstance(provenance, CreditCohortBatchMemberProvenance):
        return False
    provenance_counts = (
        provenance.curve_build_count,
        provenance.market_feature_call_count,
        provenance.credit_comparability_call_count,
        provenance.relative_evaluator_call_count,
        provenance.member_composer_call_count,
    )
    if not all(_nonnegative_int(value) for value in provenance_counts):
        return False
    if (
        provenance.as_of_date != batch.as_of_date
        or provenance.market_source != batch.market_source
        or provenance.target_kind != batch.requested_target_kind
        or provenance.rating_agency != batch.requested_rating_agency
        or provenance.requested_bond_ids != batch.requested_bond_ids
        or provenance.existing_bond_ids != batch.existing_bond_ids
        or provenance.missing_bond_ids != batch.missing_bond_ids
        or provenance.curve_build_count != (1 if existing else 0)
        or provenance.market_feature_call_count != len(existing)
        or provenance.credit_comparability_call_count != len(existing)
        or provenance.relative_evaluator_call_count != len(existing)
        or provenance.member_composer_call_count != len(existing)
    ):
        return False
    if existing:
        return (
            provenance.shared_curve_contract_version == curve.contract_version
            and provenance.shared_curve_status == curve.status
            and provenance.shared_curve_trade_date == curve.curve_trade_date
            and _nonnegative_int(provenance.shared_curve_node_count)
            and provenance.shared_curve_node_count == curve.node_count
        )
    return (
        provenance.shared_curve_contract_version is None
        and provenance.shared_curve_status is None
        and provenance.shared_curve_trade_date is None
        and provenance.shared_curve_node_count is None
    )


def _result(
    batch: CreditCohortBatchMemberView,
    target_bond_id: int,
    min_peer_count: int,
    *,
    status: str,
    valid_batch: bool,
    candidates: tuple[BondCreditCohortRelativeValueMemberView, ...] = (),
    target_item: CreditCohortBatchMemberItem | None = None,
    distribution=None,
) -> CreditCohortPeerDistributionOrchestrationView:
    batch_status = _safe_literal(batch.status, CreditCohortBatchStatus)
    target_requested = valid_batch and target_bond_id in batch.requested_bond_ids
    target_exists = valid_batch and target_bond_id in batch.existing_bond_ids
    target_member = target_item.member if target_item is not None else None
    reducer_called = distribution is not None
    flags = [] if status == "READY" else [status]
    return CreditCohortPeerDistributionOrchestrationView(
        target_bond_id=target_bond_id,
        min_peer_count=min_peer_count,
        as_of_date=_safe_date(batch.as_of_date),
        market_source=_safe_nonblank(batch.market_source),
        requested_target_kind=_safe_literal(
            batch.requested_target_kind, RatingTargetKind
        ),
        requested_rating_agency=_safe_literal(
            batch.requested_rating_agency, RatingAgency
        ),
        status=status,
        batch_status=batch_status,
        requested_bond_count=_safe_count(batch.requested_bond_count),
        existing_bond_count=_safe_count(batch.existing_bond_count),
        missing_bond_count=_safe_count(batch.missing_bond_count),
        built_member_count=_safe_count(batch.built_member_count),
        candidate_member_count=len(candidates),
        distribution=distribution,
        availability=CreditCohortPeerDistributionOrchestrationAvailability(
            has_valid_batch=valid_batch,
            target_requested=target_requested,
            target_exists=target_exists,
            has_target_member=isinstance(
                target_member, BondCreditCohortRelativeValueMemberView
            ),
            has_candidate_members=bool(candidates),
            has_distribution_result=reducer_called,
            has_ready_distribution=(
                status == "READY"
                and reducer_called
                and distribution.status == "READY"
            ),
        ),
        quality_flags=flags,
        provenance=CreditCohortPeerDistributionOrchestrationProvenance(
            batch_contract_version=_safe_string(batch.contract_version),
            batch_status=batch_status,
            batch_as_of_date=_safe_date(batch.as_of_date),
            batch_market_source=_safe_nonblank(batch.market_source),
            batch_target_kind=_safe_literal(
                batch.requested_target_kind, RatingTargetKind
            ),
            batch_rating_agency=_safe_literal(
                batch.requested_rating_agency, RatingAgency
            ),
            requested_bond_ids=_safe_ids(batch.requested_bond_ids),
            existing_bond_ids=_safe_ids(batch.existing_bond_ids),
            missing_bond_ids=_safe_ids(batch.missing_bond_ids),
            target_bond_id=target_bond_id,
            target_item_build_status=(
                target_item.build_status
                if target_item is not None
                and target_item.build_status in get_args(CreditCohortBatchItemStatus)
                else None
            ),
            candidate_member_bond_ids=[member.bond_id for member in candidates],
            min_peer_count=min_peer_count,
            reducer_contract_version=(
                distribution.contract_version if reducer_called else None
            ),
            reducer_status=distribution.status if reducer_called else None,
            reducer_call_count=1 if reducer_called else 0,
        ),
    )


class CreditCohortPeerDistributionOrchestrator:
    @staticmethod
    def build(
        batch: CreditCohortBatchMemberView,
        target_bond_id: int,
        *,
        min_peer_count: int = 1,
    ) -> CreditCohortPeerDistributionOrchestrationView:
        if not isinstance(batch, CreditCohortBatchMemberView):
            raise ValueError("batch must be a Task280 batch view")
        if not _positive_int(target_bond_id):
            raise ValueError("target_bond_id must be an exact positive int")
        if not _positive_int(min_peer_count):
            raise ValueError("min_peer_count must be an exact positive int")

        if not _valid_batch(batch):
            return _result(
                batch,
                target_bond_id,
                min_peer_count,
                status="BATCH_EVIDENCE_INVALID",
                valid_batch=False,
            )

        built_items = tuple(
            item for item in batch.items if item.build_status == "BUILT"
        )
        candidates = tuple(item.member for item in built_items)
        target_item = next(
            (item for item in batch.items if item.bond_id == target_bond_id), None
        )
        if target_item is None:
            return _result(
                batch,
                target_bond_id,
                min_peer_count,
                status="TARGET_NOT_REQUESTED",
                valid_batch=True,
                candidates=candidates,
            )
        if target_item.build_status == "BOND_NOT_FOUND":
            return _result(
                batch,
                target_bond_id,
                min_peer_count,
                status="TARGET_BOND_NOT_FOUND",
                valid_batch=True,
                candidates=candidates,
                target_item=target_item,
            )

        distribution = CreditCohortPeerSpreadDistributionService.build(
            target_item.member,
            candidates,
            min_peer_count=min_peer_count,
        )
        return _result(
            batch,
            target_bond_id,
            min_peer_count,
            status=distribution.status,
            valid_batch=True,
            candidates=candidates,
            target_item=target_item,
            distribution=distribution,
        )
