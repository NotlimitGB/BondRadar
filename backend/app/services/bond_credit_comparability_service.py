"""One Task269 read, followed only by deterministic equality descriptors."""

from collections import Counter
from datetime import date
from typing import get_args

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.schemas.bond_credit_comparability import (
    BondCreditComparabilityAvailability, BondCreditComparabilityProvenance,
    BondCreditComparabilityView, RatingComparabilityEntry, SourceNativeRatingCohortKey,
)
from app.schemas.bond_credit_features import (
    BOND_CREDIT_FEATURE_CONTRACT_VERSION, CreditRatingEventEvidence,
    IssuerLinkStatus, RatingAgency,
)
from app.services.bond_credit_feature_service import BondCreditFeatureService


def _positive_id(value):
    return value if type(value) is int and value > 0 else None


def _calendar_date(value):
    return value if type(value) is date else None


def _present(value):
    return type(value) is str and bool(value.strip())


def _entry(group, target_kind, duplicate, family_invalid):
    flags = set()
    agency = group.rating_agency if group.rating_agency in get_args(RatingAgency) else None
    latest = _calendar_date(group.latest_event_date)
    count = group.event_count if type(group.event_count) is int else None
    events = group.events
    event_ids = sorted(e.event_id for e in events if _positive_id(e.event_id) is not None)
    if family_invalid:
        flags.add("DEPENDENCY_FAMILY_INVALID")
    if duplicate:
        flags.add("DUPLICATE_AGENCY_GROUP")
    if agency is None or latest is None:
        flags.add("GROUP_METADATA_INVALID")
    if not events or count != len(events):
        flags.add("EVENT_COUNT_INVALID")
    validated = []
    for e in events:
        if (e.rating_agency != agency or e.target_kind != target_kind
                or type(e.event_date) is not date or e.event_date != latest):
            flags.add("EVENT_GROUP_MISMATCH")
        if (not _present(e.source_provider)
                or (e.rating_scale_raw is not None and type(e.rating_scale_raw) is not str)
                or (e.rating_value_raw is not None and type(e.rating_value_raw) is not str)):
            flags.add("RAW_KEY_FIELDS_INVALID")
        if _positive_id(e.event_id) is None or _positive_id(e.artifact_id) is None:
            flags.add("EVENT_PROVENANCE_INVALID")
        try:
            validated.append(CreditRatingEventEvidence.model_validate(e.model_dump(warnings=False), strict=True))
        except ValidationError:
            flags.add("EVENT_PROVENANCE_INVALID")
    invalid = bool(flags)
    multiple = len(events) > 1
    if multiple:
        flags.add("MULTIPLE_LATEST_EVENTS")
    if any(e.publication_precision == "UNKNOWN" for e in events):
        flags.add("PUBLICATION_TIME_UNKNOWN")
    if any(e.rating_value_raw is None or (type(e.rating_value_raw) is str
                                         and not _present(e.rating_value_raw)) for e in events):
        flags.add("RATING_VALUE_MISSING")
    # No event is selected from an ambiguous or structurally contradictory group.
    selected = validated[0] if not invalid and not multiple else None
    value_present = selected is not None and _present(selected.rating_value_raw)
    status = ("EVIDENCE_INVALID" if invalid else "MULTIPLE_LATEST_EVENTS" if multiple
              else "RATING_VALUE_MISSING" if not value_present else "READY")
    key = SourceNativeRatingCohortKey(
        target_kind=target_kind, rating_agency=agency,
        source_provider=selected.source_provider, rating_scale_raw=selected.rating_scale_raw,
        rating_value_raw=selected.rating_value_raw,
    ) if status == "READY" else None
    return RatingComparabilityEntry(
        target_kind=target_kind, rating_agency=agency, status=status,
        latest_event_date=latest, event_count=count, event_ids=event_ids,
        selected_event_id=selected.event_id if selected else None,
        source_provider=selected.source_provider if selected else None,
        rating_scale_raw=selected.rating_scale_raw if selected else None,
        rating_value_raw=selected.rating_value_raw if selected else None,
        has_declared_rating_scale=selected is not None and _present(selected.rating_scale_raw),
        has_rating_value=value_present, cohort_key=key, selected_event=selected,
        quality_flags=sorted(flags),
    )


def _family(groups, target_kind, invalid):
    counts = Counter(g.rating_agency for g in groups)
    entries = [_entry(g, target_kind, counts[g.rating_agency] > 1, invalid) for g in groups]
    # Diagnostic order only; raw rating values never participate in sorting.
    return sorted(entries, key=lambda e: (
        e.rating_agency or "", e.latest_event_date or date.min, tuple(e.event_ids),
    ))


def _agencies(entries, ready_only=False):
    return sorted({e.rating_agency for e in entries if e.rating_agency is not None
                   and (not ready_only or e.status == "READY")})


class BondCreditComparabilityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(self, bond_id: int, as_of_date: date) -> BondCreditComparabilityView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        with self.db.no_autoflush:
            evidence = BondCreditFeatureService(self.db).build_for_bond(bond_id, as_of_date)
            flags = set()
            request_matches = (type(evidence.bond_id) is int and evidence.bond_id == bond_id
                               and type(evidence.as_of_date) is date and evidence.as_of_date == as_of_date)
            contract_matches = (evidence.contract_version == BOND_CREDIT_FEATURE_CONTRACT_VERSION
                                and evidence.pit_ready is False)
            if not request_matches:
                flags.add("DEPENDENCY_REQUEST_IDENTITY_MISMATCH")
            if not contract_matches:
                flags.add("DEPENDENCY_CONTRACT_INVALID")
            verified = evidence.issuer_link_status == "VERIFIED"
            issuer_consistent = (
                evidence.issuer_link_status in get_args(IssuerLinkStatus)
                and evidence.availability.has_verified_legal_issuer is verified
                and ((_positive_id(evidence.legal_issuer_id) is not None
                      and _positive_id(evidence.provenance.bond_legal_issuer_profile_id) is not None)
                     if verified else evidence.legal_issuer_id is None and not evidence.issuer_ratings)
            )
            if not issuer_consistent:
                flags.add("ISSUER_LINK_EVIDENCE_INVALID")
            global_invalid = not request_matches or not contract_matches
            bond_entries = _family(evidence.bond_ratings, "BOND", global_invalid)
            issuer_entries = _family(evidence.issuer_ratings, "LEGAL_ISSUER",
                                     global_invalid or not issuer_consistent)
            entries = bond_entries + issuer_entries
            invalid = bool(flags) or any(e.status == "EVIDENCE_INVALID" for e in entries)
            if invalid:
                flags.add("DEPENDENCY_EVIDENCE_INVALID")
            bond_agencies = _agencies(bond_entries, ready_only=True)
            issuer_agencies = _agencies(issuer_entries, ready_only=True)
            for label, family, comparable in (("BOND", bond_entries, bond_agencies),
                                               ("ISSUER", issuer_entries, issuer_agencies)):
                if not comparable:
                    flags.add(label + "_COMPARABLE_RATING_MISSING")
                if any("MULTIPLE_LATEST_EVENTS" in e.quality_flags for e in family):
                    flags.add(label + "_MULTIPLE_LATEST_RATING_EVENTS")
                if any("RATING_VALUE_MISSING" in e.quality_flags for e in family):
                    flags.add(label + "_RATING_VALUE_MISSING")
                if any("PUBLICATION_TIME_UNKNOWN" in e.quality_flags for e in family):
                    flags.add("RATING_PUBLICATION_UNKNOWN")
            # Carry only the two rating-specific Task269 diagnostics, never bank flags.
            for label in ("BOND", "ISSUER"):
                if "MULTIPLE_LATEST_" + label + "_RATING_EVENTS" in evidence.quality_flags:
                    flags.add(label + "_MULTIPLE_LATEST_RATING_EVENTS")
                if label + "_RATING_PUBLICATION_UNKNOWN" in evidence.quality_flags:
                    flags.add("RATING_PUBLICATION_UNKNOWN")
            any_cohort = bool(bond_agencies or issuer_agencies)
            return BondCreditComparabilityView(
                bond_id=bond_id, as_of_date=as_of_date,
                isin=evidence.isin if request_matches else None,
                secid=evidence.secid if request_matches else None,
                status="DEPENDENCY_EVIDENCE_INVALID" if invalid else "READY" if any_cohort else "NO_COMPARABLE_RATING",
                bond_rating_entries=bond_entries, issuer_rating_entries=issuer_entries,
                availability=BondCreditComparabilityAvailability(
                    has_bond_rating_evidence=any(e.event_ids for e in bond_entries),
                    has_issuer_rating_evidence=any(e.event_ids for e in issuer_entries),
                    has_bond_comparable_cohort=bool(bond_agencies),
                    has_issuer_comparable_cohort=bool(issuer_agencies),
                    bond_comparable_agencies=bond_agencies, issuer_comparable_agencies=issuer_agencies,
                    has_any_comparable_cohort=any_cohort,
                ),
                provenance=BondCreditComparabilityProvenance(
                    credit_feature_contract_version=evidence.contract_version,
                    credit_feature_bond_id=_positive_id(evidence.bond_id),
                    credit_feature_as_of_date=_calendar_date(evidence.as_of_date), as_of_date=as_of_date,
                    bond_legal_issuer_profile_id=_positive_id(evidence.provenance.bond_legal_issuer_profile_id),
                    legal_issuer_id=_positive_id(evidence.legal_issuer_id),
                    issuer_link_status=evidence.issuer_link_status if evidence.issuer_link_status in get_args(IssuerLinkStatus) else None,
                    bond_rating_event_ids=sorted(i for e in bond_entries for i in e.event_ids),
                    issuer_rating_event_ids=sorted(i for e in issuer_entries for i in e.event_ids),
                    selected_bond_rating_event_ids=sorted(e.selected_event_id for e in bond_entries if e.selected_event_id is not None),
                    selected_issuer_rating_event_ids=sorted(e.selected_event_id for e in issuer_entries if e.selected_event_id is not None),
                    bond_rating_agencies=_agencies(bond_entries), issuer_rating_agencies=_agencies(issuer_entries),
                ), quality_flags=sorted(flags),
            )
