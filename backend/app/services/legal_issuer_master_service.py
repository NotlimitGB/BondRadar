from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond_legal_issuer_evidence import BondLegalIssuerEvidence
from app.models.bond_legal_issuer_profile import (
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    LEGAL_ISSUER_MAPPING_SOURCE,
    LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES,
    BondLegalIssuerProfile,
)
from app.models.legal_issuer import (
    LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
    LEGAL_ISSUER_MASTER_SOURCE,
    LegalIssuer,
)
from app.models.legal_issuer_evidence import LegalIssuerEvidence
from app.services.bond_legal_issuer_service import BondLegalIssuerService


_SECURITY_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMPLETENESS_CODES = (
    "SOURCE_ISSUER_ID_PRESENT",
    "ISSUER_TITLE_PRESENT",
    "ISSUER_INN_PRESENT",
    "ISSUER_OKPO_PRESENT",
    "MASTER_VERIFIED",
    "MASTER_CONFLICT",
)


@dataclass(frozen=True)
class LegalIssuerMasterResolution:
    legal_issuer: LegalIssuer | None
    blockers: tuple[str, ...]
    resolved: bool


class LegalIssuerMasterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ingest_task243_evidence(
        self,
        upstream: BondLegalIssuerEvidence,
    ) -> tuple[LegalIssuer, LegalIssuerEvidence, bool]:
        canonical = BondLegalIssuerService._validated_persisted_evidence(upstream)
        source = canonical["source"]
        source_issuer_id = canonical["source_issuer_id"]
        source_security_secid = canonical["matched_secid"]
        if source != LEGAL_ISSUER_MASTER_SOURCE:
            raise ValueError("Unsupported Legal Issuer Master source")
        if source_issuer_id is None:
            raise ValueError("Task243 evidence has no source issuer ID")
        if source_security_secid is None:
            raise ValueError("Task243 evidence has no matched SECID")
        if not isinstance(canonical["bond_id"], int) or canonical["bond_id"] < 1:
            raise ValueError("Task243 evidence has an invalid Bond ID")

        upstream_fingerprint = canonical["fingerprint"]
        self._require_sha256(
            upstream_fingerprint,
            field_name="upstream_evidence_fingerprint",
        )
        evidence_fingerprint = self._evidence_fingerprint(
            identity_source=source,
            source_issuer_id=source_issuer_id,
            upstream_contract_version=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
            upstream_evidence_fingerprint=upstream_fingerprint,
        )
        existing = self._evidence_by_upstream_fingerprint(upstream_fingerprint)
        if existing is not None:
            issuer = self.db.get(LegalIssuer, existing.legal_issuer_id)
            if (
                issuer is None
                or issuer.identity_source != source
                or issuer.source_issuer_id != source_issuer_id
                or existing.evidence_fingerprint != evidence_fingerprint
            ):
                raise ValueError("Task243 evidence lineage is bound to another issuer")
            return self.resolve_issuer(issuer), existing, False

        issuer = self._get_or_create_issuer(
            identity_source=source,
            source_issuer_id=source_issuer_id,
            observed_at=canonical["observed_at"],
            ingestion_at=canonical["ingestion_at"],
        )
        created = False
        evidence = LegalIssuerEvidence(
            legal_issuer_id=issuer.id,
            contract_version=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
            source=source,
            source_issuer_id=source_issuer_id,
            upstream_contract_version=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
            upstream_evidence_fingerprint=upstream_fingerprint,
            source_bond_id=canonical["bond_id"],
            source_security_secid=source_security_secid,
            source_security_isin=canonical["matched_isin"],
            security_match_status=canonical["security_match_status"],
            issuer_title=canonical["issuer_title"],
            issuer_inn=canonical["issuer_inn"],
            issuer_okpo=canonical["issuer_okpo"],
            observed_at=canonical["observed_at"],
            effective_at=canonical["effective_at"],
            upstream_ingestion_at=canonical["ingestion_at"],
            ingestion_at=datetime.now(timezone.utc),
            evidence_fingerprint=evidence_fingerprint,
        )
        try:
            with self.db.begin_nested():
                self.db.add(evidence)
                self.db.flush()
            created = True
        except IntegrityError:
            existing = self._evidence_by_upstream_fingerprint(upstream_fingerprint)
            if existing is None:
                raise
            evidence = existing

        if (
            evidence.legal_issuer_id != issuer.id
            or evidence.evidence_fingerprint != evidence_fingerprint
        ):
            raise ValueError("Task243 evidence lineage is bound to another issuer")
        issuer = self.resolve_issuer(issuer)
        return issuer, evidence, created

    def resolve_issuer(self, issuer: LegalIssuer) -> LegalIssuer:
        if issuer.id is None:
            raise ValueError("LegalIssuer must be persisted before resolution")
        rows = list(
            self.db.execute(
                select(LegalIssuerEvidence)
                .where(LegalIssuerEvidence.legal_issuer_id == issuer.id)
                .order_by(
                    LegalIssuerEvidence.source_security_secid,
                    LegalIssuerEvidence.observed_at,
                    LegalIssuerEvidence.evidence_fingerprint,
                )
            ).scalars()
        )
        canonical = [self._validated_evidence(issuer, row) for row in rows]
        current = self.latest_per_security(canonical)

        titles = self._non_null_values(current, "issuer_title")
        inns = self._non_null_values(current, "issuer_inn")
        okpos = self._non_null_values(current, "issuer_okpo")
        title = next(iter(titles)) if len(titles) == 1 else None
        issuer.resolution_state = (
            "conflict"
            if len(inns) > 1
            else "verified"
            if title is not None
            else "observed"
        )
        issuer.issuer_title = title
        issuer.issuer_inn = next(iter(inns)) if len(inns) == 1 else None
        issuer.issuer_okpo = next(iter(okpos)) if len(okpos) == 1 else None
        if canonical:
            issuer.first_observed_at = min(row["observed_at"] for row in canonical)
            issuer.last_observed_at = max(row["observed_at"] for row in canonical)
            issuer.last_resolved_at = max(row["ingestion_at"] for row in canonical)
        self.db.add(issuer)
        self.db.flush()
        return issuer

    def resolve_for_bond_profile(
        self,
        profile: BondLegalIssuerProfile | None,
    ) -> LegalIssuerMasterResolution:
        issuer: LegalIssuer | None = None
        if (
            profile is not None
            and profile.contract_version == LEGAL_ISSUER_MAPPING_CONTRACT_VERSION
            and profile.mapping_state == "verified"
            and profile.mapping_source == LEGAL_ISSUER_MAPPING_SOURCE
            and profile.source_issuer_id is not None
        ):
            issuer = self._issuer_by_identity(
                profile.mapping_source,
                profile.source_issuer_id,
            )
        blockers = tuple(legal_issuer_master_blockers(profile, issuer))
        return LegalIssuerMasterResolution(
            legal_issuer=issuer if not blockers else None,
            blockers=blockers,
            resolved=not blockers,
        )

    def _get_or_create_issuer(
        self,
        *,
        identity_source: str,
        source_issuer_id: str,
        observed_at: datetime,
        ingestion_at: datetime,
    ) -> LegalIssuer:
        existing = self._issuer_by_identity(identity_source, source_issuer_id)
        if existing is not None:
            return existing
        issuer = LegalIssuer(
            contract_version=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
            identity_source=identity_source,
            source_issuer_id=source_issuer_id,
            resolution_state="observed",
            first_observed_at=observed_at,
            last_observed_at=observed_at,
            last_resolved_at=ingestion_at,
        )
        try:
            with self.db.begin_nested():
                self.db.add(issuer)
                self.db.flush()
        except IntegrityError:
            existing = self._issuer_by_identity(identity_source, source_issuer_id)
            if existing is None:
                raise
            return existing
        return issuer

    def _validated_evidence(
        self,
        issuer: LegalIssuer,
        evidence: LegalIssuerEvidence,
    ) -> dict[str, Any]:
        if evidence.contract_version != LEGAL_ISSUER_MASTER_CONTRACT_VERSION:
            raise ValueError("Invalid persisted Legal Issuer Master contract")
        if evidence.source != issuer.identity_source:
            raise ValueError("Persisted issuer evidence source mismatch")
        if evidence.source_issuer_id != issuer.source_issuer_id:
            raise ValueError("Persisted issuer evidence identity mismatch")
        if evidence.upstream_contract_version != LEGAL_ISSUER_MAPPING_CONTRACT_VERSION:
            raise ValueError("Invalid persisted upstream contract")
        if evidence.security_match_status not in LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES:
            raise ValueError("Invalid persisted upstream match status")
        if not isinstance(evidence.source_bond_id, int) or evidence.source_bond_id < 1:
            raise ValueError("Invalid persisted source Bond ID")
        self._require_sha256(
            evidence.upstream_evidence_fingerprint,
            field_name="upstream_evidence_fingerprint",
        )
        source_security_secid = self._security_identifier(
            evidence.source_security_secid,
            field_name="source_security_secid",
            required=True,
        )
        source_security_isin = self._security_identifier(
            evidence.source_security_isin,
            field_name="source_security_isin",
            required=False,
        )
        expected_fingerprint = self._evidence_fingerprint(
            identity_source=evidence.source,
            source_issuer_id=evidence.source_issuer_id,
            upstream_contract_version=evidence.upstream_contract_version,
            upstream_evidence_fingerprint=evidence.upstream_evidence_fingerprint,
        )
        if evidence.evidence_fingerprint != expected_fingerprint:
            raise ValueError("Invalid persisted Legal Issuer evidence fingerprint")
        return {
            "source_security_secid": source_security_secid,
            "source_security_isin": source_security_isin,
            "issuer_title": evidence.issuer_title,
            "issuer_inn": evidence.issuer_inn,
            "issuer_okpo": evidence.issuer_okpo,
            "observed_at": self.persisted_timestamp(evidence.observed_at),
            "ingestion_at": self.persisted_timestamp(evidence.ingestion_at),
            "fingerprint": evidence.evidence_fingerprint,
        }

    @staticmethod
    def latest_per_security(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest: dict[str, datetime] = {}
        current: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            secid = row["source_security_secid"]
            observed_at = row["observed_at"]
            latest_at = latest.get(secid)
            if latest_at is None or observed_at > latest_at:
                latest[secid] = observed_at
                current[secid] = [row]
            elif observed_at == latest_at:
                current[secid].append(row)
        return [
            row
            for secid in sorted(current)
            for row in sorted(current[secid], key=lambda value: value["fingerprint"])
        ]

    def _issuer_by_identity(
        self,
        identity_source: str,
        source_issuer_id: str,
    ) -> LegalIssuer | None:
        return self.db.execute(
            select(LegalIssuer).where(
                LegalIssuer.identity_source == identity_source,
                LegalIssuer.source_issuer_id == source_issuer_id,
            )
        ).scalar_one_or_none()

    def _evidence_by_upstream_fingerprint(
        self,
        fingerprint: str,
    ) -> LegalIssuerEvidence | None:
        return self.db.execute(
            select(LegalIssuerEvidence).where(
                LegalIssuerEvidence.upstream_contract_version
                == LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
                LegalIssuerEvidence.upstream_evidence_fingerprint == fingerprint,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _evidence_fingerprint(
        *,
        identity_source: str,
        source_issuer_id: str,
        upstream_contract_version: str,
        upstream_evidence_fingerprint: str,
    ) -> str:
        payload = {
            "contract_version": LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
            "identity_source": identity_source,
            "source_issuer_id": source_issuer_id,
            "upstream_contract_version": upstream_contract_version,
            "upstream_evidence_fingerprint": upstream_evidence_fingerprint,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_sha256(value: str, *, field_name: str) -> None:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ValueError(f"{field_name} is invalid")

    @staticmethod
    def _security_identifier(
        value: str | None,
        *,
        field_name: str,
        required: bool,
    ) -> str | None:
        text = str(value or "").strip().upper()
        if not text:
            if required:
                raise ValueError(f"{field_name} is required")
            return None
        if not _SECURITY_IDENTIFIER.fullmatch(text):
            raise ValueError(f"{field_name} is invalid")
        return text

    @staticmethod
    def persisted_timestamp(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("Invalid persisted Legal Issuer timestamp")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _non_null_values(
        rows: list[dict[str, Any]],
        field_name: str,
    ) -> set[str]:
        return {row[field_name] for row in rows if row[field_name] is not None}


def legal_issuer_master_blockers(
    profile: BondLegalIssuerProfile | None,
    issuer: LegalIssuer | None,
) -> list[str]:
    if profile is None:
        return ["BOND_LEGAL_ISSUER_PROFILE_MISSING"]
    blockers: list[str] = []
    if profile.contract_version != LEGAL_ISSUER_MAPPING_CONTRACT_VERSION:
        blockers.append("BOND_LEGAL_ISSUER_PROFILE_INVALID")
    if profile.mapping_state == "unknown":
        blockers.append("BOND_LEGAL_ISSUER_MAPPING_UNKNOWN")
    elif profile.mapping_state == "conflict":
        blockers.append("BOND_LEGAL_ISSUER_MAPPING_CONFLICT")
    elif profile.mapping_state != "verified":
        blockers.append("BOND_LEGAL_ISSUER_MAPPING_NOT_VERIFIED")
    if profile.source_issuer_id is None:
        blockers.append("SOURCE_ISSUER_ID_MISSING")
    if profile.mapping_source != LEGAL_ISSUER_MAPPING_SOURCE:
        blockers.append("LEGAL_ISSUER_MASTER_SOURCE_MISMATCH")
    if blockers:
        return blockers
    if issuer is None:
        return ["LEGAL_ISSUER_MASTER_MISSING"]
    if issuer.identity_source != profile.mapping_source:
        blockers.append("LEGAL_ISSUER_MASTER_SOURCE_MISMATCH")
    if issuer.source_issuer_id != profile.source_issuer_id:
        blockers.append("LEGAL_ISSUER_MASTER_IDENTITY_MISMATCH")
    if issuer.resolution_state == "conflict":
        blockers.append("LEGAL_ISSUER_MASTER_CONFLICT")
    elif issuer.resolution_state != "verified":
        blockers.append("LEGAL_ISSUER_MASTER_NOT_VERIFIED")
    return blockers


def legal_issuer_master_completeness(
    issuer: LegalIssuer | None,
) -> dict[str, bool]:
    if issuer is None:
        return {code: False for code in _COMPLETENESS_CODES}
    return {
        "SOURCE_ISSUER_ID_PRESENT": bool(issuer.source_issuer_id),
        "ISSUER_TITLE_PRESENT": issuer.issuer_title is not None,
        "ISSUER_INN_PRESENT": issuer.issuer_inn is not None,
        "ISSUER_OKPO_PRESENT": issuer.issuer_okpo is not None,
        "MASTER_VERIFIED": issuer.resolution_state == "verified",
        "MASTER_CONFLICT": issuer.resolution_state == "conflict",
    }
