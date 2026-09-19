"""Build Task276-equivalent members with one shared Task268 OFZ curve."""

from collections.abc import Mapping, Sequence, Set
from datetime import date
from typing import get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.credit_cohort_batch_members import (
    CreditCohortBatchMemberItem,
    CreditCohortBatchMemberProvenance,
    CreditCohortBatchMemberView,
)
from app.services.bond_credit_cohort_relative_value_composer import (
    compose_credit_cohort_relative_value_member,
)
from app.services.bond_credit_comparability_service import (
    BondCreditComparabilityService,
)
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.ofz_relative_value_evaluator import (
    evaluate_market_against_ofz_curve,
)


def _validate_bond_ids(bond_ids: Sequence[int]) -> tuple[int, ...]:
    if (
        not isinstance(bond_ids, Sequence)
        or isinstance(bond_ids, (str, bytes, bytearray, memoryview, Mapping, Set))
    ):
        raise ValueError("bond_ids must be a deterministic sequence")
    values = tuple(bond_ids)
    if not values:
        raise ValueError("bond_ids must be nonempty")
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("bond_ids must contain exact positive integers")
    if len(set(values)) != len(values):
        raise ValueError("bond_ids must not contain duplicates")
    return tuple(sorted(values))


def _validate_request(
    bond_ids: Sequence[int],
    as_of_date: date,
    target_kind: RatingTargetKind,
    rating_agency: RatingAgency,
    market_source: str,
    max_market_age_days: int,
    max_curve_age_days: int,
) -> tuple[int, ...]:
    requested_ids = _validate_bond_ids(bond_ids)
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if type(target_kind) is not str or target_kind not in get_args(RatingTargetKind):
        raise ValueError("target_kind must be BOND or LEGAL_ISSUER")
    if type(rating_agency) is not str or rating_agency not in get_args(RatingAgency):
        raise ValueError("rating_agency must be an exact supported agency")
    if not isinstance(market_source, str) or not market_source.strip():
        raise ValueError("market_source must be a nonblank string")
    for name, value in (
        ("max_market_age_days", max_market_age_days),
        ("max_curve_age_days", max_curve_age_days),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    return requested_ids


class CreditCohortBatchMemberService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bonds(
        self,
        bond_ids: Sequence[int],
        as_of_date: date,
        *,
        target_kind: RatingTargetKind,
        rating_agency: RatingAgency,
        market_source: str = "moex",
        max_market_age_days: int = 7,
        max_curve_age_days: int = 7,
    ) -> CreditCohortBatchMemberView:
        requested_ids = _validate_request(
            bond_ids,
            as_of_date,
            target_kind,
            rating_agency,
            market_source,
            max_market_age_days,
            max_curve_age_days,
        )

        members = {}
        curve = None
        curve_build_count = 0
        market_feature_call_count = 0
        credit_comparability_call_count = 0
        relative_evaluator_call_count = 0
        member_composer_call_count = 0

        with self.db.no_autoflush:
            existing_ids = tuple(
                self.db.execute(
                    select(Bond.id)
                    .where(Bond.id.in_(requested_ids))
                    .order_by(Bond.id)
                ).scalars().all()
            )
            if existing_ids:
                curve = OfzReferenceCurveService(self.db).build_curve(
                    as_of_date,
                    market_source=market_source,
                    max_curve_age_days=max_curve_age_days,
                )
                curve_build_count = 1
                market_service = BondMarketFeatureService(self.db)
                credit_service = BondCreditComparabilityService(self.db)
                for bond_id in existing_ids:
                    market = market_service.build_for_bond(
                        bond_id,
                        as_of_date,
                        market_source=market_source,
                        max_market_age_days=max_market_age_days,
                    )
                    market_feature_call_count += 1
                    credit = credit_service.build_for_bond(bond_id, as_of_date)
                    credit_comparability_call_count += 1
                    relative = evaluate_market_against_ofz_curve(
                        market,
                        curve,
                        as_of_date=as_of_date,
                        market_source=market_source,
                    )
                    relative_evaluator_call_count += 1
                    members[bond_id] = compose_credit_cohort_relative_value_member(
                        credit,
                        relative,
                        bond_id=bond_id,
                        as_of_date=as_of_date,
                        target_kind=target_kind,
                        rating_agency=rating_agency,
                        market_source=market_source,
                    )
                    member_composer_call_count += 1

        existing_set = set(existing_ids)
        missing_ids = tuple(
            bond_id for bond_id in requested_ids if bond_id not in existing_set
        )
        items = [
            CreditCohortBatchMemberItem(
                bond_id=bond_id,
                build_status="BUILT" if bond_id in existing_set else "BOND_NOT_FOUND",
                member=members.get(bond_id),
            )
            for bond_id in requested_ids
        ]
        built_member_count = len(existing_ids)
        ready_member_count = sum(
            member.status == "READY" for member in members.values()
        )
        status = (
            "NO_EXISTING_BONDS"
            if not existing_ids
            else "PARTIAL"
            if missing_ids
            else "COMPLETE"
        )

        return CreditCohortBatchMemberView(
            as_of_date=as_of_date,
            market_source=market_source,
            requested_target_kind=target_kind,
            requested_rating_agency=rating_agency,
            status=status,
            requested_bond_ids=list(requested_ids),
            existing_bond_ids=list(existing_ids),
            missing_bond_ids=list(missing_ids),
            requested_bond_count=len(requested_ids),
            existing_bond_count=len(existing_ids),
            missing_bond_count=len(missing_ids),
            built_member_count=built_member_count,
            ready_member_count=ready_member_count,
            unavailable_member_count=built_member_count - ready_member_count,
            curve_built=curve is not None,
            items=items,
            shared_curve=curve,
            provenance=CreditCohortBatchMemberProvenance(
                as_of_date=as_of_date,
                market_source=market_source,
                target_kind=target_kind,
                rating_agency=rating_agency,
                max_market_age_days=max_market_age_days,
                max_curve_age_days=max_curve_age_days,
                requested_bond_ids=list(requested_ids),
                existing_bond_ids=list(existing_ids),
                missing_bond_ids=list(missing_ids),
                shared_curve_contract_version=(
                    curve.contract_version if curve is not None else None
                ),
                shared_curve_status=curve.status if curve is not None else None,
                shared_curve_trade_date=(
                    curve.curve_trade_date if curve is not None else None
                ),
                shared_curve_node_count=curve.node_count if curve is not None else None,
                curve_build_count=curve_build_count,
                market_feature_call_count=market_feature_call_count,
                credit_comparability_call_count=credit_comparability_call_count,
                relative_evaluator_call_count=relative_evaluator_call_count,
                member_composer_call_count=member_composer_call_count,
            ),
        )
