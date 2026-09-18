"""SELECT-only M3 projection of canonical identity and existing M2 evidence."""

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.cbr_bank_financial_evidence import (
    CbrBankCreditMetric, CbrBankNormalizedObservation, CbrBankRawObservation,
    CbrBankReportSnapshot, CbrBankReportingSubject, CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerProfile,
)
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact
from app.models.legal_issuer import LegalIssuer
from app.schemas.bond_credit_features import (
    BankCreditMetricEvidence, BondCreditFeatureAvailability, BondCreditFeatureProvenance,
    BondCreditFeatureView, CreditRatingEventEvidence, RatingAgencyLatestEvidence,
)


def _utc_date(value: datetime) -> date:
    # SQLite drops timezone metadata from UTC timestamps persisted by M2 stores.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc)).date()


class BondCreditFeatureService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond(self, bond_id: int, as_of_date: date) -> BondCreditFeatureView:
        if type(bond_id) is not int or bond_id <= 0:
            raise ValueError("bond_id must be a positive integer")
        if type(as_of_date) is not date:
            raise ValueError("as_of_date must be a calendar date")
        with self.db.no_autoflush:
            bond = self.db.get(Bond, bond_id)
            if bond is None:
                raise HTTPException(status_code=404, detail="Bond not found")
            profile = self.db.execute(select(BondLegalIssuerProfile).where(
                BondLegalIssuerProfile.bond_id == bond_id,
            )).scalar_one_or_none()
            issuer_status, issuer = self._issuer(profile)
            flags: set[str] = set()
            if issuer_status != "VERIFIED":
                flags.add("LEGAL_ISSUER_" + issuer_status if issuer_status.startswith("MAPPING_")
                          else issuer_status)
            bond_ratings = self._ratings("BOND", bond_id, as_of_date, flags)
            issuer_ratings = (self._ratings("LEGAL_ISSUER", issuer.id, as_of_date, flags)
                              if issuer is not None else [])
            bridges = (list(self.db.execute(select(CbrBankSubjectLegalIssuerProfile).where(
                CbrBankSubjectLegalIssuerProfile.legal_issuer_id == issuer.id,
                CbrBankSubjectLegalIssuerProfile.bridge_state == "VERIFIED",
            ).order_by(CbrBankSubjectLegalIssuerProfile.reporting_subject_id)).scalars())
                       if issuer is not None else [])
            subject = bridge = None
            if issuer is None:
                bank_status = "ISSUER_NOT_VERIFIED"
            elif not bridges:
                bank_status = "NO_VERIFIED_SUBJECT"
            elif len(bridges) > 1:
                bank_status = "AMBIGUOUS_VERIFIED_SUBJECT"
            else:
                bank_status = "VERIFIED"
                bridge = bridges[0]
                subject = self.db.get(CbrBankReportingSubject, bridge.reporting_subject_id)
                if subject is None:
                    raise ValueError("Verified bank bridge has no reporting subject")
            if bank_status != "VERIFIED":
                flags.add("BANK_SUBJECT_AMBIGUOUS" if len(bridges) > 1
                          else "BANK_SUBJECT_NOT_VERIFIED")
            metrics = self._metrics(subject, as_of_date, flags) if subject is not None else []
            report_date = metrics[0].report_date if metrics else None
            if not bond_ratings:
                flags.add("BOND_RATING_MISSING")
            if not issuer_ratings:
                flags.add("ISSUER_RATING_MISSING")
            if not metrics:
                flags.add("BANK_CREDIT_METRICS_MISSING")
            return BondCreditFeatureView(
                bond_id=bond.id, isin=bond.isin, secid=bond.secid, as_of_date=as_of_date,
                issuer_link_status=issuer_status,
                legal_issuer_id=issuer.id if issuer else None,
                legal_issuer_source_issuer_id=issuer.source_issuer_id if issuer else None,
                legal_issuer_inn=issuer.issuer_inn if issuer else None,
                bond_ratings=bond_ratings, issuer_ratings=issuer_ratings,
                bank_subject_status=bank_status,
                bank_reporting_subject_id=subject.id if subject else None,
                bank_subject_regn=subject.subject_regn if subject else None,
                bank_report_date=report_date,
                bank_report_age_days=(as_of_date - report_date).days if report_date else None,
                bank_metrics=metrics, quality_flags=sorted(flags),
                availability=BondCreditFeatureAvailability(
                    has_verified_legal_issuer=issuer is not None,
                    has_bond_rating_event=bool(bond_ratings),
                    has_bond_rating_value=any(r.has_rating_value for r in bond_ratings),
                    has_issuer_rating_event=bool(issuer_ratings),
                    has_issuer_rating_value=any(r.has_rating_value for r in issuer_ratings),
                    has_verified_bank_subject=subject is not None,
                    has_bank_credit_metrics=bool(metrics),
                    bond_rating_agencies=[r.rating_agency for r in bond_ratings],
                    issuer_rating_agencies=[r.rating_agency for r in issuer_ratings],
                ),
                provenance=BondCreditFeatureProvenance(
                    bond_legal_issuer_profile_id=profile.id if profile else None,
                    mapping_state=profile.mapping_state if profile else None,
                    mapping_source=profile.mapping_source if profile else None,
                    mapping_source_issuer_id=profile.source_issuer_id if profile else None,
                    legal_issuer_identity_source=issuer.identity_source if issuer else None,
                    verified_bank_reporting_subject_ids=[b.reporting_subject_id for b in bridges],
                    bank_bridge_current_evidence_id=bridge.current_evidence_id if bridge else None,
                ),
            )

    def _issuer(self, profile: BondLegalIssuerProfile | None) -> tuple[str, LegalIssuer | None]:
        if profile is None:
            return "MAPPING_MISSING", None
        if profile.mapping_state == "conflict":
            return "MAPPING_CONFLICT", None
        if (profile.mapping_state != "verified"
                or profile.mapping_source != "moex_security_reference"
                or profile.source_issuer_id is None):
            return "MAPPING_NOT_VERIFIED", None
        issuer = self.db.execute(select(LegalIssuer).where(
            LegalIssuer.identity_source == profile.mapping_source,
            LegalIssuer.source_issuer_id == profile.source_issuer_id,
            LegalIssuer.resolution_state == "verified",
        )).scalar_one_or_none()
        return ("VERIFIED", issuer) if issuer else ("LEGAL_ISSUER_NOT_VERIFIED", None)

    def _ratings(
        self, target: str, target_id: int, boundary: date, flags: set[str],
    ) -> list[RatingAgencyLatestEvidence]:
        target_column = (CreditRatingEvent.bond_id if target == "BOND"
                         else CreditRatingEvent.legal_issuer_id)
        rows = self.db.execute(select(
            CreditRatingEvent, CreditRiskSourceArtifact.source_provider,
            CreditRiskSourceArtifact.content_sha256, CreditRiskSourceArtifact.retrieved_at,
        ).outerjoin(CreditRiskSourceArtifact,
                    CreditRiskSourceArtifact.id == CreditRatingEvent.artifact_id).where(
            CreditRatingEvent.target_kind == target,
            CreditRatingEvent.resolution_state == "RESOLVED",
            target_column == target_id, CreditRatingEvent.event_date <= boundary,
        ).order_by(CreditRatingEvent.rating_agency, CreditRatingEvent.event_date,
                   CreditRatingEvent.id)).all()
        grouped = defaultdict(list)
        for event, provider, sha256, retrieved_at in rows:
            if event.publication_precision == "DATE" and event.publication_date > boundary:
                continue
            if event.publication_precision == "TIMESTAMP" and _utc_date(event.publication_at) > boundary:
                continue
            if provider is None or sha256 is None or retrieved_at is None:
                raise ValueError("Rating event has incomplete source artifact lineage")
            grouped[event.rating_agency].append((event, provider, sha256, retrieved_at))
        result = []
        prefix = "BOND" if target == "BOND" else "ISSUER"
        for agency, members in sorted(grouped.items()):
            latest = max(member[0].event_date for member in members)
            members = sorted((m for m in members if m[0].event_date == latest), key=lambda m: m[0].id)
            if len(members) > 1:
                flags.add(f"MULTIPLE_LATEST_{prefix}_RATING_EVENTS")
            evidence = []
            for event, provider, sha256, retrieved_at in members:
                if event.publication_precision == "UNKNOWN":
                    flags.add(f"{prefix}_RATING_PUBLICATION_UNKNOWN")
                evidence.append(CreditRatingEventEvidence(
                    event_id=event.id, artifact_id=event.artifact_id,
                    source_provider=provider, source_artifact_sha256=sha256,
                    artifact_retrieved_at=retrieved_at,
                    **{field: getattr(event, field) for field in (
                        "source_object_id", "rating_agency", "target_kind", "event_date",
                        "publication_precision", "publication_date", "publication_at",
                        "rating_scale_raw", "rating_value_raw", "rating_outlook_raw",
                        "rating_watch_raw", "rating_action_raw", "source_issuer_inn",
                        "source_bond_isin", "event_fingerprint",
                    )},
                ))
            result.append(RatingAgencyLatestEvidence(
                rating_agency=agency, latest_event_date=latest, event_count=len(evidence),
                has_rating_value=any(e.rating_value_raw is not None and bool(e.rating_value_raw.strip())
                                     for e in evidence), events=evidence,
            ))
        return result

    def _metrics(
        self, subject: CbrBankReportingSubject, boundary: date, flags: set[str],
    ) -> list[BankCreditMetricEvidence]:
        rows = self.db.execute(select(
            CbrBankCreditMetric, CbrBankNormalizedObservation, CbrBankRawObservation,
            CbrBankReportSnapshot, CbrBankSourceArtifact.id,
            CbrBankSourceArtifact.content_sha256, CbrBankSourceArtifact.form,
            CbrBankSourceArtifact.report_date,
        ).outerjoin(CbrBankNormalizedObservation,
                    CbrBankNormalizedObservation.id == CbrBankCreditMetric.normalized_observation_id
        ).outerjoin(CbrBankRawObservation,
                    CbrBankRawObservation.id == CbrBankNormalizedObservation.raw_observation_id
        ).outerjoin(CbrBankReportSnapshot,
                    CbrBankReportSnapshot.id == CbrBankRawObservation.snapshot_id
        ).outerjoin(CbrBankSourceArtifact,
                    CbrBankSourceArtifact.id == CbrBankReportSnapshot.artifact_id
        ).where(CbrBankCreditMetric.subject_regn == subject.subject_regn,
                CbrBankCreditMetric.report_date <= boundary)).all()
        eligible = []
        for metric, normalized, raw, snapshot, artifact_id, sha256, form, report_date in rows:
            if normalized is None or raw is None or snapshot is None or artifact_id is None:
                flags.add("BANK_METRIC_LINEAGE_MISMATCH")
                continue
            values = (metric.metric_value, normalized.normalized_value)
            consistent = (
                all(isinstance(v, Decimal) and v.is_finite() for v in values)
                and metric.metric_value == normalized.normalized_value
                and metric.metric_unit == normalized.normalized_unit
                and normalized.value_state == "VALUE"
                and normalized.source_disclosure_state == raw.disclosure_state == "PUBLIC_VALUE"
                and metric.subject_regn == normalized.subject_regn == raw.subject_regn == subject.subject_regn
                and raw.reporting_subject_id == subject.id
                and metric.report_date == normalized.report_date == raw.report_date == snapshot.report_date == report_date
                and metric.source_form == normalized.form == raw.form == snapshot.form == form
                and metric.source_code == normalized.source_code
                and sha256 is not None
            )
            if not consistent:
                flags.add("BANK_METRIC_LINEAGE_MISMATCH")
                continue
            if snapshot.publication_status == "KNOWN" and _utc_date(snapshot.publication_at) > boundary:
                continue
            eligible.append(BankCreditMetricEvidence(
                metric_id=metric.id, normalized_observation_id=normalized.id,
                raw_observation_id=raw.id, report_snapshot_id=snapshot.id,
                source_artifact_id=artifact_id, source_artifact_sha256=sha256,
                source_dimensions=normalized.source_dimensions,
                source_disclosure_state=normalized.source_disclosure_state,
                normalization_fingerprint=normalized.normalization_fingerprint,
                snapshot_publication_status=snapshot.publication_status,
                snapshot_publication_at=snapshot.publication_at,
                snapshot_retrieved_at=snapshot.retrieved_at,
                **{field: getattr(metric, field) for field in (
                    "metric_key", "metric_family", "metric_value", "metric_unit",
                    "metric_fingerprint", "source_form", "source_code", "report_date", "subject_regn",
                )},
            ))
        if not eligible:
            return []
        latest = max(e.report_date for e in eligible)
        selected = sorted((e for e in eligible if e.report_date == latest), key=lambda e: (
            e.metric_key, e.source_form, e.source_code, e.normalized_observation_id, e.metric_id,
        ))
        if any(count > 1 for count in Counter(e.metric_key for e in selected).values()):
            flags.add("BANK_METRIC_DUPLICATE_KEY_EVIDENCE")
        if any(e.snapshot_publication_status == "UNKNOWN" for e in selected):
            flags.add("BANK_METRIC_PUBLICATION_UNKNOWN")
        return selected
