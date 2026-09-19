"""Load Task275 and Task268 once, then delegate pure Task276 composition."""

from datetime import date
from typing import get_args

from sqlalchemy.orm import Session

from app.schemas.bond_credit_cohort_relative_value import BondCreditCohortRelativeValueMemberView
from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.bond_credit_features import RatingAgency
from app.services.bond_credit_cohort_relative_value_composer import (
    compose_credit_cohort_relative_value_member,
)
from app.services.bond_credit_comparability_service import BondCreditComparabilityService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService


class BondCreditCohortRelativeValueMemberService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(
        self, bond_id: int, as_of_date: date, *, target_kind: RatingTargetKind,
        rating_agency: RatingAgency, market_source: str = "moex",
        max_market_age_days: int = 7, max_curve_age_days: int = 7,
    ) -> BondCreditCohortRelativeValueMemberView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        if type(target_kind) is not str or target_kind not in get_args(RatingTargetKind):
            raise ValueError("target_kind must be BOND or LEGAL_ISSUER")
        if type(rating_agency) is not str or rating_agency not in get_args(RatingAgency):
            raise ValueError("rating_agency must be an exact supported agency")
        if not isinstance(market_source, str) or not market_source.strip():
            raise ValueError("market_source must be a nonblank string")
        for name, value in (("max_market_age_days", max_market_age_days),
                            ("max_curve_age_days", max_curve_age_days)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        with self.db.no_autoflush:
            credit = BondCreditComparabilityService(self.db).build_for_bond(bond_id, as_of_date)
            relative = OfzReferenceCurveService(self.db).evaluate_bond(
                bond_id, as_of_date, market_source=market_source,
                max_market_age_days=max_market_age_days, max_curve_age_days=max_curve_age_days,
            )
        return compose_credit_cohort_relative_value_member(
            credit, relative, bond_id=bond_id, as_of_date=as_of_date,
            target_kind=target_kind, rating_agency=rating_agency, market_source=market_source,
        )
