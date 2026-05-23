from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.services.company_identity_resolution_service import (
    CompanyIdentityResolutionService,
)
from app.services.financial_collection_priority_service import (
    FinancialCollectionPriorityService,
)


READY_IDENTITY_STATUSES = {"matched", "verified", "confirmed"}
WEAK_IDENTITY_STATUSES = {"missing", "unknown", "weak", "conflict"}
LOW_CONFIDENCE_THRESHOLD = Decimal("0.8")
RECOMMENDED_IDENTITY_FIELDS = [
    "legal_name",
    "short_name",
    "display_name",
    "inn",
    "ogrn",
    "official_source_url",
]
GOVERNMENT_ISSUER_TYPES = {
    "government_like",
    "municipal_or_regional_government",
}


class IdentityFirstCollectionService:
    def __init__(
        self,
        db: Session,
        *,
        priority_service: FinancialCollectionPriorityService | None = None,
        identity_resolution_service: CompanyIdentityResolutionService | None = None,
    ) -> None:
        self.db = db
        self.priority_service = priority_service or FinancialCollectionPriorityService(db)
        self.identity_resolution_service = (
            identity_resolution_service or CompanyIdentityResolutionService(db)
        )

    def get_identity_first_collection_queue(
        self,
        company_ids: list[int],
        *,
        source_presence: dict[int | str, list[str]] | None = None,
        include_covered: bool = True,
        exclude_government_like: bool = True,
    ) -> dict[str, Any]:
        priority_report = self.priority_service.get_batch_collection_priority(
            company_ids,
            source_presence=source_presence,
            include_covered=include_covered,
            exclude_government_like=exclude_government_like,
        )

        collection_ready: list[dict[str, Any]] = []
        identity_review_required: list[dict[str, Any]] = []
        already_covered = [
            self._already_covered_row(row)
            for row in priority_report.get("already_covered") or []
        ]
        excluded_or_deprioritized = [
            self._excluded_row(row)
            for row in priority_report.get("excluded_or_deprioritized") or []
        ]

        for row in priority_report.get("priority_queue") or []:
            if row.get("issuer_type") in GOVERNMENT_ISSUER_TYPES:
                excluded_or_deprioritized.append(
                    self._excluded_row(
                        {
                            **row,
                            "reason": (
                                "issuer type is outside the corporate financial "
                                "collection flow"
                            ),
                        }
                    )
                )
                continue

            identity = self._identity_block(row)
            review_reasons = self._review_reasons(row, identity)
            if self._is_collection_ready(row, identity, review_reasons):
                collection_ready.append(
                    self._collection_ready_row(row, identity)
                )
            else:
                identity_review_required.append(
                    self._identity_review_row(row, identity, review_reasons)
                )

        for rank, row in enumerate(collection_ready, start=1):
            row["rank"] = rank
        for rank, row in enumerate(identity_review_required, start=1):
            row["rank"] = rank

        summary = self._summary(
            collection_ready,
            identity_review_required,
            already_covered,
            excluded_or_deprioritized,
        )
        status = (
            "warning"
            if identity_review_required or excluded_or_deprioritized
            else priority_report.get("status", "passed")
        )
        return {
            "status": status,
            "company_count": priority_report.get("company_count", len(company_ids)),
            "collection_ready_count": len(collection_ready),
            "identity_review_required_count": len(identity_review_required),
            "already_covered_count": len(already_covered),
            "excluded_count": len(excluded_or_deprioritized),
            "summary": summary,
            "collection_ready": collection_ready,
            "identity_review_required": identity_review_required,
            "already_covered": already_covered,
            "excluded_or_deprioritized": excluded_or_deprioritized,
            "read_only": True,
            "dry_run_only": True,
            "import_executed": False,
            "identity_apply_executed": False,
            "paper_trading_called": False,
            "would_mutate_scores": False,
            "would_trigger_paper_trading": False,
        }

    def _collection_ready_row(
        self,
        row: dict[str, Any],
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **self._base_row(row),
            "rank": None,
            "identity": identity,
            "identity_status": identity.get("identity_status"),
            "identity_confidence": identity.get("identity_confidence"),
            "identity_readiness": "ready_for_financial_collection",
            "identity_reasons": self._identity_reasons(row, identity),
            "operator_next_action": "collect_official_financial_report",
        }

    def _identity_review_row(
        self,
        row: dict[str, Any],
        identity: dict[str, Any],
        review_reasons: list[str],
    ) -> dict[str, Any]:
        return {
            **self._base_row(row),
            "rank": None,
            "identity": identity,
            "identity_status": identity.get("identity_status"),
            "identity_confidence": identity.get("identity_confidence"),
            "identity_readiness": "requires_identity_review",
            "review_reasons": review_reasons,
            "recommended_identity_fields": list(RECOMMENDED_IDENTITY_FIELDS),
            "operator_next_action": "review_issuer_identity_before_financial_collection",
        }

    def _already_covered_row(self, row: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity_block(row)
        readiness = row.get("risk_scoring_readiness") or "not_ready"
        return {
            **row,
            "identity": identity,
            "identity_status": identity.get("identity_status"),
            "identity_confidence": identity.get("identity_confidence"),
            "identity_readiness": (
                "already_covered_ready"
                if readiness == "ready"
                else "already_covered_partial"
            ),
            "operator_next_action": (
                "collect_missing_financial_fields_from_official_report"
                if row.get("recommended_next_fields")
                else "no_financial_collection_needed"
            ),
            "safety": self._safety(),
        }

    def _excluded_row(self, row: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity_block(row)
        return {
            **row,
            "identity": identity,
            "identity_status": identity.get("identity_status"),
            "identity_confidence": identity.get("identity_confidence"),
            "identity_readiness": "excluded",
            "operator_next_action": "excluded_from_financial_collection",
            "safety": self._safety(),
        }

    def _base_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name"),
            "canonical_company_id": row.get("canonical_company_id"),
            "canonical_company_name": row.get("canonical_company_name"),
            "issuer_type": row.get("issuer_type"),
            "classification_confidence": row.get("classification_confidence"),
            "classification_reasons": row.get("classification_reasons") or [],
            "priority_level": row.get("priority_level"),
            "priority_score": row.get("priority_score"),
            "has_financial_report": bool(row.get("has_financial_report")),
            "risk_scoring_readiness": row.get("risk_scoring_readiness") or "not_ready",
            "coverage_status": row.get("coverage_status"),
            "source_presence": row.get("source_presence") or {},
            "bond_context": row.get("bond_context") or {},
            "recommended_collection": row.get("recommended_collection") or {},
            "priority_reasons": row.get("priority_reasons") or [],
            "blocking_reasons": row.get("blocking_reasons") or [],
            "safety": self._safety(),
        }

    def _identity_block(self, row: dict[str, Any]) -> dict[str, Any]:
        canonical_id = self._int_or_none(row.get("canonical_company_id"))
        requested_id = self._int_or_none(row.get("company_id"))
        company = self.db.get(Company, canonical_id) if canonical_id is not None else None
        profile = self._profile(canonical_id)
        resolution_warnings: list[dict[str, Any]] = []
        if requested_id is not None:
            resolution = self.identity_resolution_service.resolve_company(requested_id)
            resolution_warnings = [
                {
                    "code": warning.code,
                    "message": warning.message,
                    "company_id": warning.company_id,
                }
                for warning in resolution.warnings
            ]
        return {
            "profile_exists": profile is not None,
            "legal_name": profile.legal_name if profile is not None else None,
            "short_name": profile.short_name if profile is not None else None,
            "display_name": profile.display_name if profile is not None else None,
            "inn": (
                profile.inn
                if profile is not None and profile.inn
                else company.inn
                if company is not None
                else None
            ),
            "ogrn": profile.ogrn if profile is not None else None,
            "issuer_role": profile.issuer_role if profile is not None else None,
            "identity_status": profile.identity_status if profile is not None else "missing",
            "identity_confidence": self._json_decimal(
                profile.identity_confidence if profile is not None else None
            ),
            "review_status": profile.review_status if profile is not None else None,
            "identity_source": profile.identity_source if profile is not None else None,
            "source_url": profile.source_url if profile is not None else None,
            "resolution_warnings": resolution_warnings,
        }

    def _review_reasons(
        self,
        row: dict[str, Any],
        identity: dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        if self._is_generated_unknown(row.get("company_name")) or self._is_generated_unknown(
            row.get("canonical_company_name")
        ):
            reasons.append("generated unknown issuer name")
        if row.get("issuer_type") == "unknown":
            reasons.append("issuer type is unknown")
        if row.get("classification_confidence") == "low":
            reasons.append("issuer classification confidence is low")

        identity_status = str(identity.get("identity_status") or "missing")
        if identity_status in WEAK_IDENTITY_STATUSES:
            reasons.append("identity status is weak or missing")
        confidence = self._decimal_or_none(identity.get("identity_confidence"))
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            reasons.append("identity confidence is below 0.8")
        if not identity.get("legal_name"):
            reasons.append("legal identity is incomplete")
        if not identity.get("inn") and not identity.get("ogrn"):
            reasons.append("INN and OGRN are missing")
        for warning in identity.get("resolution_warnings") or []:
            message = warning.get("message") or warning.get("code")
            if message:
                reasons.append(f"duplicate/canonical resolution warning: {message}")
        return list(dict.fromkeys(reasons))

    def _is_collection_ready(
        self,
        row: dict[str, Any],
        identity: dict[str, Any],
        review_reasons: list[str],
    ) -> bool:
        if row.get("has_financial_report"):
            return False
        if row.get("issuer_type") != "corporate":
            return False
        if row.get("classification_confidence") not in {"medium", "high"}:
            return False
        if self._is_generated_unknown(row.get("company_name")) or self._is_generated_unknown(
            row.get("canonical_company_name")
        ):
            return False
        identity_status = str(identity.get("identity_status") or "missing")
        confidence = self._decimal_or_none(identity.get("identity_confidence"))
        has_legal_identifier = bool(identity.get("legal_name")) and bool(
            identity.get("inn") or identity.get("ogrn")
        )
        status_is_ready = identity_status in READY_IDENTITY_STATUSES
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            return False
        if identity_status in {"weak", "conflict"}:
            return False
        if any(reason.startswith("duplicate/canonical resolution warning") for reason in review_reasons):
            return False
        return status_is_ready or has_legal_identifier

    @staticmethod
    def _identity_reasons(row: dict[str, Any], identity: dict[str, Any]) -> list[str]:
        reasons = [
            "canonical issuer identity is known",
            "issuer is corporate",
        ]
        if identity.get("legal_name"):
            reasons.append("legal name is available")
        if identity.get("inn") or identity.get("ogrn"):
            reasons.append("INN or OGRN is available")
        status = identity.get("identity_status")
        if status in READY_IDENTITY_STATUSES:
            reasons.append(f"identity status is {status}")
        if row.get("classification_confidence") in {"medium", "high"}:
            reasons.append("issuer classification confidence is sufficient")
        return reasons

    @staticmethod
    def _summary(
        collection_ready: list[dict[str, Any]],
        identity_review_required: list[dict[str, Any]],
        already_covered: list[dict[str, Any]],
        excluded_or_deprioritized: list[dict[str, Any]],
    ) -> dict[str, int]:
        return {
            "known_corporate_missing_report_count": len(collection_ready),
            "unknown_identity_count": sum(
                1
                for row in identity_review_required
                if row.get("issuer_type") == "unknown"
                or any(
                    reason == "generated unknown issuer name"
                    for reason in row.get("review_reasons") or []
                )
            ),
            "weak_identity_count": sum(
                1
                for row in identity_review_required
                if any(
                    reason
                    in {
                        "identity status is weak or missing",
                        "identity confidence is below 0.8",
                        "legal identity is incomplete",
                        "INN and OGRN are missing",
                    }
                    for reason in row.get("review_reasons") or []
                )
            ),
            "partial_report_count": sum(
                1
                for row in already_covered
                if row.get("risk_scoring_readiness") == "partial"
            ),
            "ready_report_count": sum(
                1
                for row in already_covered
                if row.get("risk_scoring_readiness") == "ready"
            ),
            "government_excluded_count": sum(
                1
                for row in excluded_or_deprioritized
                if row.get("issuer_type") in GOVERNMENT_ISSUER_TYPES
            ),
            "collection_ready_high_priority_count": sum(
                1 for row in collection_ready if row.get("priority_level") == "high"
            ),
        }

    def _profile(self, company_id: int | None) -> CompanyIdentityProfile | None:
        if company_id is None:
            return None
        return self.db.execute(
            select(CompanyIdentityProfile).where(
                CompanyIdentityProfile.company_id == company_id
            )
        ).scalar_one_or_none()

    @staticmethod
    def _safety() -> dict[str, bool]:
        return {
            "read_only": True,
            "would_import_report": False,
            "would_mutate_identity": False,
            "would_mutate_scores": False,
            "would_trigger_paper_trading": False,
        }

    @staticmethod
    def _is_generated_unknown(value: Any) -> bool:
        return str(value or "").casefold().startswith("unknown issuer for")

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _json_decimal(value: Decimal | None) -> int | float | None:
        if value is None:
            return None
        if value == value.to_integral_value():
            return int(value)
        rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return float(rounded)

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except ValueError:
            return None
