from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_legal_issuer_evidence import BondLegalIssuerEvidence
from app.models.bond_legal_issuer_profile import (
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    LEGAL_ISSUER_MAPPING_SOURCE,
    LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES,
    BondLegalIssuerProfile,
)
from app.services.moex_issuer_identity_source_service import (
    MoexIssuerIdentitySourceResolution,
)


_SECURITY_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_MATCH_STATUS_RANK = {
    "EXACT_ISIN_RECOVERED": 0,
    "EXACT_SECID": 1,
    "EXACT_SECID_ISIN_CORROBORATED": 2,
}
_COMPLETENESS_CODES = (
    "SOURCE_ISSUER_ID_PRESENT",
    "ISSUER_TITLE_PRESENT",
    "ISSUER_INN_PRESENT",
    "ISSUER_OKPO_PRESENT",
    "SECID_MATCH_EXACT",
    "ISIN_CORROBORATED",
)


class BondLegalIssuerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_evidence(
        self,
        *,
        bond: Bond,
        requested_secid: str | None,
        expected_isin: str | None,
        matched_secid: str,
        matched_isin: str | None,
        source_issuer_id: str | None,
        issuer_title: str | None,
        issuer_inn: str | None,
        issuer_okpo: str | None,
        security_match_status: str,
        observed_at: datetime,
        effective_at: datetime | None = None,
        source: str = LEGAL_ISSUER_MAPPING_SOURCE,
    ) -> tuple[BondLegalIssuerEvidence, bool]:
        if bond.id is None:
            raise ValueError("Bond must be persisted before legal-issuer ingestion")
        if source != LEGAL_ISSUER_MAPPING_SOURCE:
            raise ValueError("Unsupported legal-issuer evidence source")
        if security_match_status not in LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES:
            raise ValueError("Unsupported legal-issuer security match status")

        normalized_requested_secid = self._security_identifier(
            requested_secid,
            field_name="requested_secid",
            required=False,
        )
        normalized_expected_isin = self._security_identifier(
            expected_isin,
            field_name="expected_isin",
            required=False,
        )
        normalized_matched_secid = self._security_identifier(
            matched_secid,
            field_name="matched_secid",
            required=True,
        )
        normalized_matched_isin = self._security_identifier(
            matched_isin,
            field_name="matched_isin",
            required=False,
        )
        self._validate_match_identity(
            requested_secid=normalized_requested_secid,
            expected_isin=normalized_expected_isin,
            matched_secid=normalized_matched_secid,
            matched_isin=normalized_matched_isin,
            security_match_status=security_match_status,
        )
        normalized_source_issuer_id = self._text(
            source_issuer_id,
            field_name="source_issuer_id",
            max_length=64,
        )
        normalized_issuer_title = self._title(issuer_title)
        normalized_issuer_inn = self._text(
            issuer_inn,
            field_name="issuer_inn",
            max_length=32,
        )
        normalized_issuer_okpo = self._text(
            issuer_okpo,
            field_name="issuer_okpo",
            max_length=32,
        )
        normalized_observed_at = self._aware_timestamp(
            observed_at,
            field_name="observed_at",
        )
        normalized_effective_at = (
            self._aware_timestamp(effective_at, field_name="effective_at")
            if effective_at is not None
            else None
        )
        fingerprint = self._fingerprint(
            bond_id=bond.id,
            source=source,
            requested_secid=normalized_requested_secid,
            expected_isin=normalized_expected_isin,
            matched_secid=normalized_matched_secid,
            matched_isin=normalized_matched_isin,
            source_issuer_id=normalized_source_issuer_id,
            issuer_title=normalized_issuer_title,
            issuer_inn=normalized_issuer_inn,
            issuer_okpo=normalized_issuer_okpo,
            security_match_status=security_match_status,
            observed_at=normalized_observed_at,
            effective_at=normalized_effective_at,
        )
        existing = self._evidence_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False

        evidence = BondLegalIssuerEvidence(
            bond_id=bond.id,
            source=source,
            requested_secid=normalized_requested_secid,
            expected_isin=normalized_expected_isin,
            matched_secid=normalized_matched_secid,
            matched_isin=normalized_matched_isin,
            source_issuer_id=normalized_source_issuer_id,
            issuer_title=normalized_issuer_title,
            issuer_inn=normalized_issuer_inn,
            issuer_okpo=normalized_issuer_okpo,
            security_match_status=security_match_status,
            observed_at=normalized_observed_at,
            effective_at=normalized_effective_at,
            ingestion_at=datetime.now(timezone.utc),
            evidence_fingerprint=fingerprint,
            contract_version=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
        )
        try:
            with self.db.begin_nested():
                self.db.add(evidence)
                self.db.flush()
        except IntegrityError:
            existing = self._evidence_by_fingerprint(fingerprint)
            if existing is None:
                raise
            return existing, False
        return evidence, True

    def ingest_moex_security_reference(
        self,
        bond: Bond,
        resolution: MoexIssuerIdentitySourceResolution,
        *,
        observed_at: datetime,
    ) -> BondLegalIssuerProfile:
        values = self._validate_task242_resolution(bond, resolution)
        self.record_evidence(
            bond=bond,
            requested_secid=values["requested_secid"],
            expected_isin=values["expected_isin"],
            matched_secid=values["matched_secid"],
            matched_isin=values["matched_isin"],
            source_issuer_id=values["source_issuer_id"],
            issuer_title=values["issuer_title"],
            issuer_inn=values["issuer_inn"],
            issuer_okpo=values["issuer_okpo"],
            security_match_status=resolution.security_match_status,
            observed_at=observed_at,
            effective_at=None,
        )
        return self.resolve_profile(bond)

    def resolve_profile(self, bond: Bond) -> BondLegalIssuerProfile:
        if bond.id is None:
            raise ValueError("Bond must be persisted before legal-issuer resolution")
        evidence_rows = list(
            self.db.execute(
                select(BondLegalIssuerEvidence)
                .where(BondLegalIssuerEvidence.bond_id == bond.id)
                .order_by(
                    BondLegalIssuerEvidence.source,
                    BondLegalIssuerEvidence.observed_at,
                    BondLegalIssuerEvidence.evidence_fingerprint,
                )
            ).scalars()
        )
        canonical_rows = [
            self._validated_persisted_evidence(row) for row in evidence_rows
        ]
        current_rows = self._latest_rows_by_source(canonical_rows)
        resolved = self._resolve_current(bond, current_rows)

        profile = self._profile_for_bond(bond.id)
        if profile is None:
            profile = BondLegalIssuerProfile(bond_id=bond.id)
            self.db.add(profile)

        profile.contract_version = LEGAL_ISSUER_MAPPING_CONTRACT_VERSION
        profile.mapping_state = resolved["mapping_state"]
        profile.mapping_source = (
            LEGAL_ISSUER_MAPPING_SOURCE if canonical_rows else None
        )
        profile.source_issuer_id = resolved["source_issuer_id"]
        profile.issuer_title = resolved["issuer_title"]
        profile.issuer_inn = resolved["issuer_inn"]
        profile.issuer_okpo = resolved["issuer_okpo"]
        profile.security_match_status = resolved["security_match_status"]
        if canonical_rows:
            profile.last_observed_at = max(
                row["observed_at"] for row in canonical_rows
            )
            profile.last_resolved_at = max(
                row["ingestion_at"] for row in canonical_rows
            )
        self.db.add(profile)
        self.db.flush()
        return profile

    def _validate_task242_resolution(
        self,
        bond: Bond,
        resolution: MoexIssuerIdentitySourceResolution,
    ) -> dict[str, str | None]:
        if bond.id is None:
            raise ValueError("Bond must be persisted before legal-issuer ingestion")
        if resolution.security_match_status not in (
            LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES
        ):
            raise ValueError("Task242 resolution is not a successful security match")
        if (
            not isinstance(resolution.candidate_count, int)
            or isinstance(resolution.candidate_count, bool)
            or not isinstance(resolution.matched_candidate_count, int)
            or isinstance(resolution.matched_candidate_count, bool)
            or resolution.candidate_count < 1
            or resolution.matched_candidate_count < 1
            or resolution.matched_candidate_count > resolution.candidate_count
        ):
            raise ValueError("Task242 resolution candidate counts are invalid")

        bond_secid = self._security_identifier(
            bond.secid,
            field_name="bond.secid",
            required=False,
        )
        bond_isin = self._security_identifier(
            bond.isin,
            field_name="bond.isin",
            required=False,
        )
        requested_secid = self._security_identifier(
            resolution.requested_secid,
            field_name="requested_secid",
            required=False,
        )
        expected_isin = self._security_identifier(
            resolution.expected_isin,
            field_name="expected_isin",
            required=False,
        )
        matched_secid = self._security_identifier(
            resolution.matched_secid,
            field_name="matched_secid",
            required=True,
        )
        matched_isin = self._security_identifier(
            resolution.matched_isin,
            field_name="matched_isin",
            required=False,
        )
        if requested_secid != bond_secid or expected_isin != bond_isin:
            raise ValueError("Task242 resolution does not bind exact Bond identifiers")

        status = resolution.security_match_status
        if status in {"EXACT_SECID", "EXACT_SECID_ISIN_CORROBORATED"}:
            if bond_secid is None or matched_secid != bond_secid:
                raise ValueError("Task242 resolution is not an exact SECID match")
        if status == "EXACT_SECID_ISIN_CORROBORATED":
            if bond_isin is None or matched_isin != bond_isin:
                raise ValueError("Task242 resolution does not corroborate Bond ISIN")
        if status == "EXACT_ISIN_RECOVERED":
            if bond_isin is None or matched_isin != bond_isin:
                raise ValueError("Task242 resolution is not an exact ISIN recovery")

        source_issuer_id = self._text(
            resolution.issuer_id,
            field_name="source_issuer_id",
            max_length=64,
        )
        issuer_title = self._title(resolution.issuer_title)
        issuer_inn = self._text(
            resolution.issuer_inn,
            field_name="issuer_inn",
            max_length=32,
        )
        issuer_okpo = self._text(
            resolution.issuer_okpo,
            field_name="issuer_okpo",
            max_length=32,
        )
        issuer_values = (source_issuer_id, issuer_title, issuer_inn, issuer_okpo)
        if all(value is not None for value in issuer_values):
            expected_metadata_status = "ISSUER_COMPLETE"
        elif any(value is not None for value in issuer_values):
            expected_metadata_status = "ISSUER_PARTIAL"
        else:
            expected_metadata_status = "ISSUER_MISSING"
        if resolution.issuer_metadata_status != expected_metadata_status:
            raise ValueError("Task242 issuer metadata status is inconsistent")
        return {
            "requested_secid": requested_secid,
            "expected_isin": expected_isin,
            "matched_secid": matched_secid,
            "matched_isin": matched_isin,
            "source_issuer_id": source_issuer_id,
            "issuer_title": issuer_title,
            "issuer_inn": issuer_inn,
            "issuer_okpo": issuer_okpo,
        }

    @classmethod
    def _validated_persisted_evidence(
        cls,
        evidence: BondLegalIssuerEvidence,
    ) -> dict[str, Any]:
        if evidence.contract_version != LEGAL_ISSUER_MAPPING_CONTRACT_VERSION:
            raise ValueError("Invalid persisted legal-issuer contract version")
        if evidence.source != LEGAL_ISSUER_MAPPING_SOURCE:
            raise ValueError("Invalid persisted legal-issuer source")
        if evidence.security_match_status not in (
            LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES
        ):
            raise ValueError("Invalid persisted legal-issuer match status")
        canonical = {
            "bond_id": evidence.bond_id,
            "source": evidence.source,
            "requested_secid": cls._security_identifier(
                evidence.requested_secid,
                field_name="requested_secid",
                required=False,
            ),
            "expected_isin": cls._security_identifier(
                evidence.expected_isin,
                field_name="expected_isin",
                required=False,
            ),
            "matched_secid": cls._security_identifier(
                evidence.matched_secid,
                field_name="matched_secid",
                required=True,
            ),
            "matched_isin": cls._security_identifier(
                evidence.matched_isin,
                field_name="matched_isin",
                required=False,
            ),
            "source_issuer_id": cls._text(
                evidence.source_issuer_id,
                field_name="source_issuer_id",
                max_length=64,
            ),
            "issuer_title": cls._title(evidence.issuer_title),
            "issuer_inn": cls._text(
                evidence.issuer_inn,
                field_name="issuer_inn",
                max_length=32,
            ),
            "issuer_okpo": cls._text(
                evidence.issuer_okpo,
                field_name="issuer_okpo",
                max_length=32,
            ),
            "security_match_status": evidence.security_match_status,
            "observed_at": cls._persisted_timestamp(evidence.observed_at),
            "effective_at": (
                cls._persisted_timestamp(evidence.effective_at)
                if evidence.effective_at is not None
                else None
            ),
            "ingestion_at": cls._persisted_timestamp(evidence.ingestion_at),
            "fingerprint": evidence.evidence_fingerprint,
        }
        expected_fingerprint = cls._fingerprint(
            **{
                key: canonical[key]
                for key in (
                    "bond_id",
                    "source",
                    "requested_secid",
                    "expected_isin",
                    "matched_secid",
                    "matched_isin",
                    "source_issuer_id",
                    "issuer_title",
                    "issuer_inn",
                    "issuer_okpo",
                    "security_match_status",
                    "observed_at",
                    "effective_at",
                )
            }
        )
        if evidence.evidence_fingerprint != expected_fingerprint:
            raise ValueError("Invalid persisted legal-issuer evidence fingerprint")
        return canonical

    @staticmethod
    def _latest_rows_by_source(
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        latest: dict[str, datetime] = {}
        current: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            source = row["source"]
            observed_at = row["observed_at"]
            latest_observed = latest.get(source)
            if latest_observed is None or observed_at > latest_observed:
                latest[source] = observed_at
                current[source] = [row]
            elif observed_at == latest_observed:
                current[source].append(row)
        return [
            row
            for source in sorted(current)
            for row in sorted(current[source], key=lambda item: item["fingerprint"])
        ]

    @classmethod
    def _resolve_current(
        cls,
        bond: Bond,
        rows: list[dict[str, Any]],
    ) -> dict[str, str | None]:
        empty = {
            "mapping_state": "unknown",
            "source_issuer_id": None,
            "issuer_title": None,
            "issuer_inn": None,
            "issuer_okpo": None,
            "security_match_status": None,
        }
        if not rows:
            return empty
        issuer_ids = cls._non_null_values(rows, "source_issuer_id")
        if not issuer_ids:
            return empty
        if len(issuer_ids) > 1:
            return {**empty, "mapping_state": "conflict"}

        for field_name in (
            "requested_secid",
            "expected_isin",
            "matched_secid",
            "matched_isin",
        ):
            if len(cls._all_values(rows, field_name)) > 1:
                return {**empty, "mapping_state": "conflict"}
        issuer_inns = cls._non_null_values(rows, "issuer_inn")
        if len(issuer_inns) > 1:
            return {**empty, "mapping_state": "conflict"}

        issuer_titles = cls._non_null_values(rows, "issuer_title")
        issuer_okpos = cls._non_null_values(rows, "issuer_okpo")
        statuses = cls._non_null_values(rows, "security_match_status")
        selected_status = min(statuses, key=lambda value: _MATCH_STATUS_RANK[value])
        issuer_title = next(iter(issuer_titles)) if len(issuer_titles) == 1 else None
        issuer_okpo = next(iter(issuer_okpos)) if len(issuer_okpos) == 1 else None
        strict = issuer_title is not None and all(
            cls._strict_match_for_bond(bond, row) for row in rows
        )
        return {
            "mapping_state": "verified" if strict else "observed",
            "source_issuer_id": next(iter(issuer_ids)),
            "issuer_title": issuer_title,
            "issuer_inn": next(iter(issuer_inns)) if issuer_inns else None,
            "issuer_okpo": issuer_okpo,
            "security_match_status": selected_status,
        }

    @classmethod
    def _strict_match_for_bond(cls, bond: Bond, row: dict[str, Any]) -> bool:
        bond_secid = cls._security_identifier(
            bond.secid,
            field_name="bond.secid",
            required=False,
        )
        bond_isin = cls._security_identifier(
            bond.isin,
            field_name="bond.isin",
            required=False,
        )
        if bond_secid is None or row["matched_secid"] != bond_secid:
            return False
        if bond_isin is None:
            return row["security_match_status"] == "EXACT_SECID"
        return (
            row["security_match_status"]
            == "EXACT_SECID_ISIN_CORROBORATED"
            and row["expected_isin"] == bond_isin
            and row["matched_isin"] == bond_isin
        )

    def _profile_for_bond(self, bond_id: int) -> BondLegalIssuerProfile | None:
        return self.db.execute(
            select(BondLegalIssuerProfile).where(
                BondLegalIssuerProfile.bond_id == bond_id
            )
        ).scalar_one_or_none()

    def _evidence_by_fingerprint(
        self,
        fingerprint: str,
    ) -> BondLegalIssuerEvidence | None:
        return self.db.execute(
            select(BondLegalIssuerEvidence).where(
                BondLegalIssuerEvidence.evidence_fingerprint == fingerprint
            )
        ).scalar_one_or_none()

    @staticmethod
    def _non_null_values(rows: list[dict[str, Any]], field_name: str) -> set[str]:
        return {row[field_name] for row in rows if row[field_name] is not None}

    @staticmethod
    def _all_values(rows: list[dict[str, Any]], field_name: str) -> set[Any]:
        return {row[field_name] for row in rows}

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
    def _validate_match_identity(
        *,
        requested_secid: str | None,
        expected_isin: str | None,
        matched_secid: str,
        matched_isin: str | None,
        security_match_status: str,
    ) -> None:
        if security_match_status in {
            "EXACT_SECID",
            "EXACT_SECID_ISIN_CORROBORATED",
        } and (requested_secid is None or matched_secid != requested_secid):
            raise ValueError("Legal-issuer evidence is not an exact SECID match")
        if security_match_status == "EXACT_SECID_ISIN_CORROBORATED" and (
            expected_isin is None or matched_isin != expected_isin
        ):
            raise ValueError("Legal-issuer evidence does not corroborate ISIN")
        if security_match_status == "EXACT_ISIN_RECOVERED" and (
            expected_isin is None or matched_isin != expected_isin
        ):
            raise ValueError("Legal-issuer evidence is not an exact ISIN recovery")
        if (
            security_match_status == "EXACT_SECID"
            and expected_isin is not None
            and matched_isin is not None
            and matched_isin != expected_isin
        ):
            raise ValueError("Legal-issuer evidence contains an ISIN conflict")

    @staticmethod
    def _text(
        value: str | None,
        *,
        field_name: str,
        max_length: int,
    ) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if len(text) > max_length:
            raise ValueError(f"{field_name} is too long")
        return text

    @classmethod
    def _title(cls, value: str | None) -> str | None:
        text = cls._text(value, field_name="issuer_title", max_length=512)
        return None if text is None else " ".join(text.split())

    @staticmethod
    def _aware_timestamp(value: datetime, *, field_name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _persisted_timestamp(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("Invalid persisted legal-issuer timestamp")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _fingerprint(
        **values: Any,
    ) -> str:
        payload = {
            "contract_version": LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
            **values,
        }
        for field_name in ("observed_at", "effective_at"):
            value = payload[field_name]
            payload[field_name] = value.isoformat() if value is not None else None
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def legal_issuer_mapping_blockers(
    bond: Bond,
    profile: BondLegalIssuerProfile | None,
) -> list[str]:
    if profile is None:
        return ["PROFILE_MISSING"]
    blockers: list[str] = []
    if profile.mapping_state == "unknown":
        blockers.append("MAPPING_UNKNOWN")
    elif profile.mapping_state == "conflict":
        blockers.append("MAPPING_CONFLICT")
    elif profile.mapping_state != "verified":
        blockers.append("MAPPING_NOT_VERIFIED")
    if profile.source_issuer_id is None:
        blockers.append("SOURCE_ISSUER_ID_MISSING")
    if profile.issuer_title is None:
        blockers.append("ISSUER_TITLE_MISSING")
    if profile.security_match_status not in {
        "EXACT_SECID",
        "EXACT_SECID_ISIN_CORROBORATED",
    }:
        blockers.append("SECID_MATCH_NOT_EXACT")
    if bond.isin is not None and (
        profile.security_match_status != "EXACT_SECID_ISIN_CORROBORATED"
    ):
        blockers.append("ISIN_NOT_CORROBORATED")
    return blockers


def legal_issuer_mapping_completeness(
    profile: BondLegalIssuerProfile | None,
) -> dict[str, bool]:
    if profile is None:
        return {code: False for code in _COMPLETENESS_CODES}
    return {
        "SOURCE_ISSUER_ID_PRESENT": profile.source_issuer_id is not None,
        "ISSUER_TITLE_PRESENT": profile.issuer_title is not None,
        "ISSUER_INN_PRESENT": profile.issuer_inn is not None,
        "ISSUER_OKPO_PRESENT": profile.issuer_okpo is not None,
        "SECID_MATCH_EXACT": profile.security_match_status
        in {"EXACT_SECID", "EXACT_SECID_ISIN_CORROBORATED"},
        "ISIN_CORROBORATED": profile.security_match_status
        == "EXACT_SECID_ISIN_CORROBORATED",
    }
