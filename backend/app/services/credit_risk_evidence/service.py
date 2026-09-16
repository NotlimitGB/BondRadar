from __future__ import annotations

from contextlib import nullcontext
from datetime import timezone
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.credit_risk_evidence import (
    CreditDefaultEvent,
    CreditRatingEvent,
    CreditRiskSourceArtifact,
)
from app.models.legal_issuer import LegalIssuer
from app.services.credit_risk_evidence.contracts import (
    CreditRiskEvidenceCollision,
    CreditRiskEvidenceError,
    DefaultEventInput,
    DefaultClass,
    IdentityResolution,
    IdentityResolutionState,
    PersistResult,
    PublicationPrecision,
    RatingEventInput,
    RatingTarget,
    SourceArtifactInput,
    SourceKind,
    SourceProvider,
    ObligationType,
    aware_utc,
    canonical_inn,
    canonical_isin,
    canonical_json_sha256,
    date_text,
    nonempty,
    optional_text,
    real_date,
    timestamp_text,
    validate_publication,
)


class CreditRiskIdentityResolver:
    def __init__(self, session: Session):
        self.session = session

    def resolve_issuer_inn(self, issuer_inn: str) -> IdentityResolution:
        value = canonical_inn(issuer_inn)
        with self.session.no_autoflush:
            rows = list(
                self.session.execute(
                    select(LegalIssuer.id)
                    .where(
                        LegalIssuer.issuer_inn == value,
                        LegalIssuer.resolution_state == "verified",
                    )
                    .order_by(LegalIssuer.id)
                ).scalars()
            )
        if len(rows) == 1:
            return IdentityResolution(
                IdentityResolutionState.RESOLVED, legal_issuer_id=rows[0]
            )
        if len(rows) > 1:
            return IdentityResolution(IdentityResolutionState.AMBIGUOUS)
        return IdentityResolution(IdentityResolutionState.UNRESOLVED)

    def resolve_bond_isin(self, isin: str) -> IdentityResolution:
        value = canonical_isin(isin)
        with self.session.no_autoflush:
            rows = list(
                self.session.execute(
                    select(Bond.id).where(Bond.isin == value).order_by(Bond.id)
                ).scalars()
            )
        if len(rows) == 1:
            return IdentityResolution(IdentityResolutionState.RESOLVED, bond_id=rows[0])
        if len(rows) > 1:
            return IdentityResolution(IdentityResolutionState.AMBIGUOUS)
        return IdentityResolution(IdentityResolutionState.UNRESOLVED)


class CreditRiskEvidenceStore:
    def __init__(self, session: Session):
        self.session = session
        self.resolver = CreditRiskIdentityResolver(session)

    def persist_source_artifact(self, value: SourceArtifactInput) -> PersistResult:
        try:
            provider = SourceProvider(value.provider)
            kind = SourceKind(value.kind).value
        except (TypeError, ValueError) as exc:
            raise CreditRiskEvidenceError("unsupported source artifact enum") from exc
        url = nonempty(value.source_url, "source_url", limit=2048)
        content_type = nonempty(value.content_type, "content_type", limit=255)
        if not isinstance(value.content_bytes, bytes) or not value.content_bytes:
            raise CreditRiskEvidenceError("content_bytes must be non-empty exact bytes")
        retrieved_at = aware_utc(value.retrieved_at, "retrieved_at")
        digest = hashlib.sha256(value.content_bytes).hexdigest()
        identity = (provider.value, url, digest)
        with self.session.no_autoflush:
            row = self.session.execute(
                select(CreditRiskSourceArtifact).where(
                    CreditRiskSourceArtifact.source_provider == identity[0],
                    CreditRiskSourceArtifact.source_url == identity[1],
                    CreditRiskSourceArtifact.content_sha256 == identity[2],
                )
            ).scalar_one_or_none()
        expected = {
            "source_provider": provider.value,
            "source_kind": kind,
            "source_url": url,
            "content_type": content_type,
            "content_bytes": value.content_bytes,
            "content_sha256": digest,
            "retrieved_at": retrieved_at,
        }
        if row is not None:
            self._assert_match(row, expected, ignore={"retrieved_at"})
            return PersistResult(row=row, inserted=False)
        row = CreditRiskSourceArtifact(**expected)
        return self._insert_or_reload(
            row,
            select(CreditRiskSourceArtifact).where(
                CreditRiskSourceArtifact.source_provider == identity[0],
                CreditRiskSourceArtifact.source_url == identity[1],
                CreditRiskSourceArtifact.content_sha256 == identity[2],
            ),
            expected,
            ignore={"retrieved_at"},
        )

    def persist_rating_event(
        self, artifact: CreditRiskSourceArtifact, value: RatingEventInput
    ) -> PersistResult:
        if artifact.id is None:
            raise CreditRiskEvidenceError("artifact must be persisted first")
        try:
            agency = SourceProvider(value.agency)
            target = RatingTarget(value.target)
        except (TypeError, ValueError) as exc:
            raise CreditRiskEvidenceError("unsupported rating enum") from exc
        if agency is SourceProvider.MOEX or artifact.source_provider != agency.value:
            raise CreditRiskEvidenceError("rating agency must match the source artifact")
        source_object_id = nonempty(value.source_object_id, "source_object_id", limit=256)
        source_name = optional_text(value.source_name_raw, "source_name_raw", limit=512)
        event_date = real_date(value.event_date, "event_date")
        publication_date, publication_at = validate_publication(
            value.publication_precision, value.publication_date, value.publication_at
        )
        scale = nonempty(value.rating_scale_raw, "rating_scale_raw", limit=128)
        rating_value = optional_text(value.rating_value_raw, "rating_value_raw", limit=128)
        outlook = optional_text(value.rating_outlook_raw, "rating_outlook_raw", limit=256)
        watch = optional_text(value.rating_watch_raw, "rating_watch_raw", limit=256)
        action = optional_text(value.rating_action_raw, "rating_action_raw", limit=256)
        if rating_value is None and outlook is None and watch is None and action is None:
            raise CreditRiskEvidenceError("rating event has no raw rating semantics")
        if target is RatingTarget.LEGAL_ISSUER:
            if value.source_issuer_inn is None or value.source_bond_isin is not None:
                raise CreditRiskEvidenceError("issuer rating requires only source INN")
            inn = canonical_inn(value.source_issuer_inn)
            isin = None
            resolution = self.resolver.resolve_issuer_inn(inn)
        else:
            if value.source_bond_isin is None or value.source_issuer_inn is not None:
                raise CreditRiskEvidenceError("bond rating requires only source ISIN")
            inn = None
            isin = canonical_isin(value.source_bond_isin)
            resolution = self.resolver.resolve_bond_isin(isin)
        semantic = {
            "artifact": {
                "provider": artifact.source_provider,
                "url": artifact.source_url,
                "sha256": artifact.content_sha256,
            },
            "agency": agency.value,
            "target": target.value,
            "source_object_id": source_object_id,
            "source_issuer_inn": inn,
            "source_bond_isin": isin,
            "source_name_raw": source_name,
            "event_date": event_date.isoformat(),
            "publication_precision": PublicationPrecision(value.publication_precision).value,
            "publication_date": date_text(publication_date),
            "publication_at": timestamp_text(publication_at),
            "rating_scale_raw": scale,
            "rating_value_raw": rating_value,
            "rating_outlook_raw": outlook,
            "rating_watch_raw": watch,
            "rating_action_raw": action,
        }
        expected = {
            "artifact_id": artifact.id,
            "rating_agency": agency.value,
            "target_kind": target.value,
            "resolution_state": resolution.state.value,
            "source_object_id": source_object_id,
            "source_issuer_inn": inn,
            "source_bond_isin": isin,
            "source_name_raw": source_name,
            "legal_issuer_id": resolution.legal_issuer_id,
            "bond_id": resolution.bond_id,
            "event_date": event_date,
            "publication_precision": PublicationPrecision(value.publication_precision).value,
            "publication_date": publication_date,
            "publication_at": publication_at,
            "rating_scale_raw": scale,
            "rating_value_raw": rating_value,
            "rating_outlook_raw": outlook,
            "rating_watch_raw": watch,
            "rating_action_raw": action,
            "event_fingerprint": canonical_json_sha256(semantic),
        }
        return self._persist_event(CreditRatingEvent, expected)

    def persist_default_event(
        self, artifact: CreditRiskSourceArtifact, value: DefaultEventInput
    ) -> PersistResult:
        if artifact.id is None:
            raise CreditRiskEvidenceError("artifact must be persisted first")
        try:
            provider = SourceProvider(value.provider)
            default_class = DefaultClass(value.default_class)
            obligation_type = ObligationType(value.obligation_type)
        except (TypeError, ValueError) as exc:
            raise CreditRiskEvidenceError("unsupported default event enum") from exc
        if provider is not SourceProvider.MOEX or artifact.source_provider != "MOEX":
            raise CreditRiskEvidenceError("default event provider must be MOEX")
        source_object_id = nonempty(value.source_object_id, "source_object_id", limit=256)
        isin = canonical_isin(value.source_bond_isin)
        source_name = optional_text(value.source_name_raw, "source_name_raw", limit=512)
        event_date = real_date(value.event_date, "event_date")
        publication_date, publication_at = validate_publication(
            value.publication_precision, value.publication_date, value.publication_at
        )
        resolution = self.resolver.resolve_bond_isin(isin)
        raw_status = optional_text(value.default_status_raw, "default_status_raw", limit=256)
        raw_reason = optional_text(value.default_reason_raw, "default_reason_raw", limit=8192)
        amount = optional_text(value.amount_raw, "amount_raw", limit=128)
        semantic = {
            "artifact": {
                "provider": artifact.source_provider,
                "url": artifact.source_url,
                "sha256": artifact.content_sha256,
            },
            "provider": "MOEX",
            "source_object_id": source_object_id,
            "source_bond_isin": isin,
            "source_name_raw": source_name,
            "event_date": event_date.isoformat(),
            "publication_precision": PublicationPrecision(value.publication_precision).value,
            "publication_date": date_text(publication_date),
            "publication_at": timestamp_text(publication_at),
            "default_class": default_class.value,
            "obligation_type": obligation_type.value,
            "default_status_raw": raw_status,
            "default_reason_raw": raw_reason,
            "amount_raw": amount,
        }
        expected = {
            "artifact_id": artifact.id,
            "source_provider": "MOEX",
            "resolution_state": resolution.state.value,
            "source_object_id": source_object_id,
            "source_bond_isin": isin,
            "source_name_raw": source_name,
            "bond_id": resolution.bond_id,
            "event_date": event_date,
            "publication_precision": PublicationPrecision(value.publication_precision).value,
            "publication_date": publication_date,
            "publication_at": publication_at,
            "default_class": default_class.value,
            "obligation_type": obligation_type.value,
            "default_status_raw": raw_status,
            "default_reason_raw": raw_reason,
            "amount_raw": amount,
            "event_fingerprint": canonical_json_sha256(semantic),
        }
        return self._persist_event(CreditDefaultEvent, expected)

    def _persist_event(self, model, expected: dict) -> PersistResult:
        query = select(model).where(model.event_fingerprint == expected["event_fingerprint"])
        with self.session.no_autoflush:
            row = self.session.execute(query).scalar_one_or_none()
        if row is not None:
            self._assert_match(row, expected)
            return PersistResult(row=row, inserted=False)
        return self._insert_or_reload(model(**expected), query, expected)

    def _insert_or_reload(
        self, row, query, expected: dict, *, ignore: set[str] | None = None
    ) -> PersistResult:
        try:
            savepoint = (
                nullcontext()
                if self.session.get_bind().dialect.name == "sqlite"
                else self.session.begin_nested()
            )
            with savepoint:
                self.session.add(row)
                self.session.flush()
            return PersistResult(row=row, inserted=True)
        except IntegrityError:
            if self.session.get_bind().dialect.name == "sqlite":
                raise
            with self.session.no_autoflush:
                raced = self.session.execute(query).scalar_one_or_none()
            if raced is None:
                raise
            self._assert_match(raced, expected, ignore=ignore)
            return PersistResult(row=raced, inserted=False)

    @staticmethod
    def _assert_match(row, expected: dict, *, ignore: set[str] | None = None) -> None:
        ignore = ignore or set()
        for key, value in expected.items():
            if key in ignore:
                continue
            actual = getattr(row, key)
            if isinstance(value, type(None)):
                equal = actual is None
            elif hasattr(value, "tzinfo") and getattr(value, "tzinfo", None) is not None:
                if getattr(actual, "tzinfo", None) is None:
                    actual = actual.replace(tzinfo=timezone.utc)
                equal = aware_utc(actual, key) == aware_utc(value, key)
            else:
                equal = actual == value
            if not equal:
                raise CreditRiskEvidenceCollision(
                    f"immutable credit-risk evidence collision on {key}"
                )
