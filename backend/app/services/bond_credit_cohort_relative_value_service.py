"""Compose one explicitly selected Task275 cohort and one Task268 result."""

from datetime import date
from decimal import Decimal
from typing import get_args

from sqlalchemy.orm import Session

from app.schemas.bond_credit_cohort_relative_value import (
    BondCreditCohortRelativeValueMemberAvailability,
    BondCreditCohortRelativeValueMemberProvenance,
    BondCreditCohortRelativeValueMemberView, CreditCohortDependencyIdentity,
)
from app.schemas.bond_credit_comparability import (
    BOND_CREDIT_COMPARABILITY_CONTRACT_VERSION, CreditComparabilityStatus,
    RatingComparabilityStatus, RatingTargetKind, SourceNativeRatingCohortKey,
)
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.ofz_reference_curve import (
    OFZ_CURVE_CONTRACT_VERSION, RELATIVE_VALUE_CONTRACT_VERSION, RelativeValueStatus,
)
from app.services.bond_credit_comparability_service import BondCreditComparabilityService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService


def _finite(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _positive_id(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _date(value: object) -> date | None:
    return value if type(value) is date else None


def _string(value: object) -> str | None:
    return value if type(value) is str else None


def _present(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _key_valid(entry, target_kind: RatingTargetKind, rating_agency: RatingAgency) -> bool:
    key = entry.cohort_key
    return (
        isinstance(key, SourceNativeRatingCohortKey)
        and entry.target_kind == key.target_kind == target_kind
        and entry.rating_agency == key.rating_agency == rating_agency
        and _positive_id(entry.selected_event_id) is not None
        and _present(entry.source_provider) and _present(entry.rating_value_raw)
        and (entry.rating_scale_raw is None or type(entry.rating_scale_raw) is str)
        and type(key.source_provider) is str and type(key.rating_value_raw) is str
        and (key.rating_scale_raw is None or type(key.rating_scale_raw) is str)
        and (key.source_provider, key.rating_scale_raw, key.rating_value_raw)
        == (entry.source_provider, entry.rating_scale_raw, entry.rating_value_raw)
    )


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
        credit_identity_matches = (type(credit.bond_id) is int and credit.bond_id == bond_id
                                   and type(credit.as_of_date) is date and credit.as_of_date == as_of_date)
        relative_identity_matches = (
            type(relative.bond_id) is int and relative.bond_id == bond_id
            and type(relative.as_of_date) is date and relative.as_of_date == as_of_date
            and relative.market_source == market_source
        )
        credit_contract_valid = (
            credit.contract_version == BOND_CREDIT_COMPARABILITY_CONTRACT_VERSION
            and credit.status in ("READY", "NO_COMPARABLE_RATING") and credit.pit_ready is False
        )
        family = credit.bond_rating_entries if target_kind == "BOND" else credit.issuer_rating_entries
        matches = [entry for entry in family if entry.rating_agency == rating_agency]
        selected = matches[0] if len(matches) == 1 else None
        key_invalid = (len(matches) > 1 or (selected is not None and selected.status == "READY"
                                         and not _key_valid(selected, target_kind, rating_agency)))
        cohort_ready = (credit_identity_matches and credit_contract_valid and selected is not None
                        and selected.status == "READY" and not key_invalid)
        relative_contract_valid = (
            relative.contract_version == RELATIVE_VALUE_CONTRACT_VERSION
            and relative.curve.contract_version == OFZ_CURVE_CONTRACT_VERSION
            and relative.pit_ready is False and relative.curve.pit_ready is False
        )
        spread_valid = (_finite(relative.spread_to_ofz_pp) is not None
                        and _finite(relative.spread_to_ofz_bps) is not None)
        relative_invalid = relative.status == "READY" and (not spread_valid or not relative_contract_valid)
        relative_ready = (relative_identity_matches and relative.status == "READY"
                          and spread_valid and relative_contract_valid)
        conditions = (
            (not credit_identity_matches or not relative_identity_matches, "DEPENDENCY_IDENTITY_MISMATCH"),
            (not credit_contract_valid, "CREDIT_COMPARABILITY_UNAVAILABLE"),
            (key_invalid, "CREDIT_COHORT_EVIDENCE_INVALID"),
            (not matches, "SELECTED_COHORT_MISSING"),
            (bool(matches) and (selected is None or selected.status != "READY"), "SELECTED_COHORT_UNAVAILABLE"),
            (relative.status != "READY", "RELATIVE_VALUE_UNAVAILABLE"),
            (relative_invalid, "RELATIVE_VALUE_EVIDENCE_INVALID"),
        )
        failures = [flag for blocked, flag in conditions if blocked]
        status = failures[0] if failures else "READY"
        flags = set(failures)
        if credit.status == "DEPENDENCY_EVIDENCE_INVALID":
            flags.add("CREDIT_COMPARABILITY_DEPENDENCY_EVIDENCE_INVALID")
        if credit.contract_version != BOND_CREDIT_COMPARABILITY_CONTRACT_VERSION:
            flags.add("CREDIT_COMPARABILITY_CONTRACT_INVALID")
        if not relative_contract_valid:
            flags.add("RELATIVE_VALUE_CONTRACT_INVALID")
        if relative.status != "READY":
            flags.add(f"RELATIVE_VALUE_{relative.status}")
        for entry in matches:
            flags.update(flag for flag in entry.quality_flags if flag in (
                "MULTIPLE_LATEST_EVENTS", "RATING_VALUE_MISSING", "PUBLICATION_TIME_UNKNOWN",
            ))
        # Keep independent correctly attributed inputs; a blocked join is never a member.
        attributed = (selected is not None and credit_identity_matches
                      and selected.target_kind == target_kind)

        def rating_input(value):
            return value if attributed else None

        def relative_input(value):
            return value if relative_identity_matches else None

        return BondCreditCohortRelativeValueMemberView(
            bond_id=bond_id, as_of_date=as_of_date, market_source=market_source,
            isin=credit.isin if credit_identity_matches and relative_identity_matches else None,
            secid=credit.secid if credit_identity_matches and relative_identity_matches else None,
            requested_target_kind=target_kind, requested_rating_agency=rating_agency, status=status,
            credit_comparability_status=credit.status if credit.status in get_args(CreditComparabilityStatus) else None,
            relative_value_status=relative.status if relative.status in get_args(RelativeValueStatus) else None,
            cohort_key=SourceNativeRatingCohortKey.model_validate(selected.cohort_key.model_dump(), strict=True) if cohort_ready else None,
            rating_event_id=rating_input(_positive_id(selected.selected_event_id)) if selected else None,
            rating_event_date=rating_input(_date(selected.latest_event_date)) if selected else None,
            rating_source_provider=rating_input(_string(selected.source_provider)) if selected else None,
            rating_scale_raw=rating_input(_string(selected.rating_scale_raw)) if selected else None,
            rating_value_raw=rating_input(_string(selected.rating_value_raw)) if selected else None,
            target_yield_to_maturity_pct=relative_input(_finite(relative.target_yield_to_maturity_pct)),
            target_duration_years=relative_input(_finite(relative.target_duration_years)),
            reference_ofz_yield_pct=relative_input(_finite(relative.reference_ofz_yield_pct)),
            spread_to_ofz_pp=relative_input(_finite(relative.spread_to_ofz_pp)),
            spread_to_ofz_bps=relative_input(_finite(relative.spread_to_ofz_bps)),
            interpolation_method=relative_input(relative.interpolation_method),
            market_snapshot_id=relative_input(_positive_id(relative.provenance.target_market_snapshot_id)),
            market_trade_date=relative_input(_date(relative.provenance.target_market_trade_date)),
            curve_trade_date=relative_input(_date(relative.curve.curve_trade_date)),
            availability=BondCreditCohortRelativeValueMemberAvailability(
                has_credit_comparability=credit_identity_matches and credit_contract_valid,
                has_selected_rating_entry=credit_identity_matches and selected is not None,
                has_selected_cohort_key=cohort_ready, has_relative_value=relative_ready,
                has_spread_to_ofz=relative_ready, has_credit_cohort_relative_value_member=status == "READY",
            ), quality_flags=sorted(flags),
            provenance=BondCreditCohortRelativeValueMemberProvenance(
                credit_comparability_contract_version=_string(credit.contract_version),
                credit_identity=CreditCohortDependencyIdentity(bond_id=_positive_id(credit.bond_id), as_of_date=_date(credit.as_of_date)),
                relative_value_identity=CreditCohortDependencyIdentity(
                    bond_id=_positive_id(relative.bond_id), as_of_date=_date(relative.as_of_date), market_source=_string(relative.market_source)),
                requested_target_kind=target_kind, requested_rating_agency=rating_agency,
                selected_entry_count=len(matches),
                selected_entry_status=selected.status if selected and selected.status in get_args(RatingComparabilityStatus) else None,
                selected_rating_event_id=_positive_id(selected.selected_event_id) if selected else None,
                selected_rating_event_date=_date(selected.latest_event_date) if selected else None,
                selected_source_provider=_string(selected.source_provider) if selected else None,
                selected_rating_scale_raw=_string(selected.rating_scale_raw) if selected else None,
                selected_rating_value_raw=_string(selected.rating_value_raw) if selected else None,
                bond_legal_issuer_profile_id=_positive_id(credit.provenance.bond_legal_issuer_profile_id),
                legal_issuer_id=_positive_id(credit.provenance.legal_issuer_id),
                relative_value_contract_version=_string(relative.contract_version),
                ofz_curve_contract_version=_string(relative.curve.contract_version),
                target_market_snapshot_id=_positive_id(relative.provenance.target_market_snapshot_id),
                target_market_trade_date=_date(relative.provenance.target_market_trade_date),
                curve_trade_date=_date(relative.curve.curve_trade_date), market_source=market_source, as_of_date=as_of_date,
            ),
        )
