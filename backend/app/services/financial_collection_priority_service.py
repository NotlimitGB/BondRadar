from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.services.financial_scoring_preview_service import (
    FinancialScoringPreviewService,
)


FULL_ANNUAL_IFRS_REQUIRED_FIELDS = [
    "revenue",
    "ebitda",
    "total_debt",
    "cash",
    "equity",
    "net_profit",
    "operating_cash_flow",
    "interest_expense",
    "net_debt",
]
FULL_ANNUAL_IFRS_OPTIONAL_FIELDS = ["debt_to_ebitda", "interest_coverage"]
SOURCE_LABEL_ORDER = [
    "top-predictions",
    "bond-universe",
    "manual-id",
    "company-name",
]


class FinancialCollectionPriorityService:
    def __init__(
        self,
        db: Session,
        *,
        scoring_preview_service: FinancialScoringPreviewService | None = None,
    ) -> None:
        self.db = db
        self.scoring_preview_service = (
            scoring_preview_service or FinancialScoringPreviewService(db)
        )

    def get_company_collection_priority(
        self,
        company_id: int,
        *,
        source_presence: list[str] | None = None,
        include_covered: bool = True,
        exclude_government_like: bool = True,
    ) -> dict[str, Any]:
        report = self.get_batch_collection_priority(
            [company_id],
            source_presence={company_id: source_presence or ["manual-id"]},
            include_covered=include_covered,
            exclude_government_like=exclude_government_like,
        )
        for key in ("priority_queue", "already_covered", "excluded_or_deprioritized"):
            if report.get(key):
                return report[key][0]
        return {
            "company_id": company_id,
            "status": "not_found",
            "read_only": True,
        }

    def get_batch_collection_priority(
        self,
        company_ids: list[int],
        *,
        source_presence: dict[int | str, list[str]] | None = None,
        include_covered: bool = True,
        exclude_government_like: bool = True,
    ) -> dict[str, Any]:
        normalized_source_presence = self._normalize_source_presence(
            company_ids,
            source_presence,
        )
        preview_report = self.scoring_preview_service.get_batch_financial_scoring_preview(
            company_ids,
            include_diagnostics=True,
            include_bond_context=True,
        )
        rows = [
            self._priority_row(
                company,
                self._labels_for_company(company, normalized_source_presence),
            )
            for company in preview_report.get("companies") or []
        ]

        priority_queue: list[dict[str, Any]] = []
        already_covered: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for row in rows:
            is_excluded_type = row["issuer_type"] in {
                "government_like",
                "municipal_or_regional_government",
            }
            if is_excluded_type and exclude_government_like:
                excluded.append(
                    {
                        **self._excluded_payload(row),
                        "reason": "issuer type is excluded from corporate collection queue",
                    }
                )
                continue
            if row.get("has_financial_report"):
                if include_covered:
                    already_covered.append(self._covered_payload(row))
                continue
            priority_queue.append(self._queue_payload(row))

        priority_queue = sorted(priority_queue, key=self._sort_key)
        for rank, row in enumerate(priority_queue, start=1):
            row["rank"] = rank

        summary = self._summary(
            rows,
            priority_queue,
            already_covered,
            excluded,
        )
        return {
            "status": "passed",
            "company_count": len(rows),
            "queue_count": len(priority_queue),
            "summary": summary,
            "priority_queue": priority_queue,
            "already_covered": already_covered,
            "excluded_or_deprioritized": excluded,
            "read_only": True,
            "dry_run_only": True,
            "import_executed": False,
            "paper_trading_called": False,
            "would_mutate_scores": False,
            "would_trigger_paper_trading": False,
        }

    def _priority_row(
        self,
        preview: dict[str, Any],
        labels: list[str],
    ) -> dict[str, Any]:
        canonical_id = self._int_or_none(preview.get("canonical_company_id"))
        company_id = self._int_or_none(preview.get("company_id"))
        profile = self._identity_profile(canonical_id)
        company = self.db.get(Company, canonical_id) if canonical_id is not None else None
        classification = self._classify_issuer(preview, company, profile)
        source_presence = self._source_presence(labels)
        bond_context = preview.get("bond_context") or {}
        readiness = preview.get("diagnostics_readiness") or {}
        diagnostics = preview.get("diagnostics") or {}
        raw_fields = diagnostics.get("raw_fields") or {}
        missing_fields = list(raw_fields.get("missing") or [])
        recommended_next_fields = list(preview.get("recommended_next_fields") or [])
        score, score_reasons = self._priority_score(
            preview=preview,
            source_presence=source_presence,
            classification=classification,
            bond_context=bond_context,
            missing_fields=missing_fields,
        )
        return {
            "company_id": company_id,
            "company_name": preview.get("company_name"),
            "canonical_company_id": canonical_id,
            "canonical_company_name": preview.get("canonical_company_name"),
            "issuer_type": classification["issuer_type"],
            "classification_confidence": classification["classification_confidence"],
            "classification_reasons": classification["classification_reasons"],
            "priority_level": self._priority_level(score),
            "priority_score": score,
            "has_financial_report": bool(preview.get("has_financial_report")),
            "risk_scoring_readiness": readiness.get("risk_scoring_readiness")
            or "not_ready",
            "coverage_status": (
                "has_report" if preview.get("has_financial_report") else "missing_report"
            ),
            "source_presence": source_presence,
            "bond_context": {
                "status": bond_context.get("status"),
                "bond_count": int(bond_context.get("bond_count") or 0),
                "sample_bonds": bond_context.get("sample_bonds") or [],
            },
            "recommended_collection": self._recommended_collection(preview),
            "recommended_next_fields": recommended_next_fields,
            "priority_reasons": self._priority_reasons(
                preview,
                classification,
                source_presence,
                score_reasons,
            ),
            "blocking_reasons": preview.get("blocking_reasons") or [],
            "safety": self._safety(),
        }

    def _priority_score(
        self,
        *,
        preview: dict[str, Any],
        source_presence: dict[str, Any],
        classification: dict[str, Any],
        bond_context: dict[str, Any],
        missing_fields: list[str],
    ) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        readiness = (preview.get("diagnostics_readiness") or {}).get(
            "risk_scoring_readiness"
        )
        if not preview.get("has_financial_report"):
            score += 40
            reasons.append("missing financial report")
        elif readiness == "partial":
            score += 25
            reasons.append("partial financial report with missing key fields")

        if source_presence["from_top_predictions"]:
            score += 20
            reasons.append("issuer appears in top predictions")
        if source_presence["from_bond_universe"]:
            score += 10
            reasons.append("issuer appears in bond universe")
        if source_presence["from_top_predictions"] and source_presence["from_bond_universe"]:
            score += 10
            reasons.append("issuer appears in both target sources")

        issuer_type = classification["issuer_type"]
        if issuer_type == "corporate":
            score += 20
            reasons.append("corporate issuer")
        elif issuer_type == "government_like":
            score -= 80
            reasons.append("government-like issuer")
        elif issuer_type == "municipal_or_regional_government":
            score -= 50
            reasons.append("municipal or regional government issuer")
        elif classification.get("suspicious_government_like"):
            score -= 30
            reasons.append("issuer classification requires review")

        bond_count = int(bond_context.get("bond_count") or 0)
        if bond_count > 0:
            score += 10
            reasons.append("issuer has bond context")
        if bond_count >= 3:
            score += 5
            reasons.append("issuer has multiple bonds")
        if any(
            "duplicate" in str(item.get("source_reason") or "").casefold()
            for item in bond_context.get("sample_bonds") or []
        ):
            score += 5
            reasons.append("accepted duplicate member bonds included")

        if readiness == "not_ready":
            score += 20
            reasons.append("financial-aware risk scoring is not ready")
        elif readiness == "partial":
            score += 10
            reasons.append("financial-aware risk scoring is partial")

        for field, points in (
            ("interest_expense", 10),
            ("net_debt", 10),
            ("ebitda", 5),
            ("total_debt", 5),
        ):
            if field in missing_fields or field in (preview.get("recommended_next_fields") or []):
                score += points
                reasons.append(f"{field} should be collected")

        return max(0, min(100, score)), reasons

    def _classify_issuer(
        self,
        preview: dict[str, Any],
        company: Company | None,
        profile: CompanyIdentityProfile | None,
    ) -> dict[str, Any]:
        texts = [
            preview.get("company_name"),
            preview.get("canonical_company_name"),
            company.name if company is not None else None,
            company.ticker if company is not None else None,
            profile.legal_name if profile is not None else None,
            profile.short_name if profile is not None else None,
            profile.issuer_group_name if profile is not None else None,
        ]
        bond_names = [
            item.get("name")
            for item in ((preview.get("bond_context") or {}).get("sample_bonds") or [])
        ]
        normalized = self._normalize_text(" ".join(str(item or "") for item in texts))
        all_text = self._normalize_text(
            " ".join(str(item or "") for item in [*texts, *bond_names])
        )
        corporate_reasons = self._corporate_reasons(normalized, company, profile)
        hard_government_reasons = self._hard_government_reasons(all_text)
        regional_reasons = self._regional_reasons(all_text)

        if corporate_reasons:
            reasons = list(corporate_reasons)
            if hard_government_reasons:
                reasons.append("corporate evidence overrides government-like text")
            return {
                "issuer_type": "corporate",
                "classification_confidence": "high"
                if len(corporate_reasons) >= 2
                else "medium",
                "classification_reasons": reasons,
                "suspicious_government_like": False,
            }
        if hard_government_reasons:
            return {
                "issuer_type": "government_like",
                "classification_confidence": "high",
                "classification_reasons": hard_government_reasons,
                "suspicious_government_like": True,
            }
        if regional_reasons:
            return {
                "issuer_type": "municipal_or_regional_government",
                "classification_confidence": "medium",
                "classification_reasons": regional_reasons,
                "suspicious_government_like": True,
            }
        return {
            "issuer_type": "unknown",
            "classification_confidence": "low",
            "classification_reasons": ["issuer classification requires review"],
            "suspicious_government_like": False,
        }

    @staticmethod
    def _corporate_reasons(
        text: str,
        company: Company | None,
        profile: CompanyIdentityProfile | None,
    ) -> list[str]:
        reasons: list[str] = []
        legal_forms = (
            " ПАО ",
            " ОАО ",
            " АО ",
            " ООО ",
            " ЗАО ",
            " PJSC ",
            " JSC ",
            " LLC ",
            "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО",
            "АКЦИОНЕРНОЕ ОБЩЕСТВО",
            "LIMITED LIABILITY COMPANY",
        )
        padded = f" {text} "
        if any(form in padded for form in legal_forms):
            reasons.append("corporate legal form")
        if profile is not None and profile.issuer_role in {
            "legal_issuer",
            "operating_company",
            "parent_group",
            "finance_subsidiary",
        }:
            reasons.append(f"issuer role is {profile.issuer_role}")
        if (company is not None and company.inn) or (
            profile is not None and (profile.inn or profile.ogrn)
        ):
            reasons.append("issuer has INN/OGRN")
        return reasons

    @staticmethod
    def _hard_government_reasons(text: str) -> list[str]:
        patterns = (
            ("ОФЗ", "OFZ / government bond marker"),
            ("OFZ", "OFZ / government bond marker"),
            ("МИНФИН", "Ministry of Finance marker"),
            ("МИНИСТЕРСТВО ФИНАНСОВ", "Ministry of Finance marker"),
            ("РОССИЙСКАЯ ФЕДЕРАЦИЯ", "Russian Federation marker"),
            ("БАНК РОССИИ", "Bank of Russia marker"),
            ("ЦЕНТРАЛЬНЫЙ БАНК", "central bank marker"),
            ("ГОСУДАРСТВЕННЫЙ ОБЛИГАЦИОННЫЙ", "government bond marker"),
        )
        return [reason for pattern, reason in patterns if pattern in text]

    @staticmethod
    def _regional_reasons(text: str) -> list[str]:
        patterns = (
            ("СУБЪЕКТ РОССИЙСКОЙ ФЕДЕРАЦИИ", "regional government marker"),
            (" ОБЛАСТЬ ", "regional government marker"),
            (" КРАЙ ", "regional government marker"),
            (" РЕСПУБЛИКА ", "regional government marker"),
            ("ГОРОД МОСКВА", "municipal government marker"),
            ("ПРАВИТЕЛЬСТВО", "government administration marker"),
            ("АДМИНИСТРАЦИЯ", "government administration marker"),
        )
        padded = f" {text} "
        return [reason for pattern, reason in patterns if pattern in padded]

    def _identity_profile(
        self,
        company_id: int | None,
    ) -> CompanyIdentityProfile | None:
        if company_id is None:
            return None
        return self.db.execute(
            select(CompanyIdentityProfile).where(
                CompanyIdentityProfile.company_id == company_id
            )
        ).scalar_one_or_none()

    @staticmethod
    def _normalize_source_presence(
        company_ids: list[int],
        source_presence: dict[int | str, list[str]] | None,
    ) -> dict[int, list[str]]:
        source_presence = source_presence or {}
        result: dict[int, list[str]] = {}
        for company_id in company_ids:
            labels = (
                source_presence.get(company_id)
                or source_presence.get(str(company_id))
                or ["manual-id"]
            )
            result[int(company_id)] = FinancialCollectionPriorityService._sort_labels(
                labels
            )
        return result

    @staticmethod
    def _labels_for_company(
        company: dict[str, Any],
        source_presence: dict[int, list[str]],
    ) -> list[str]:
        canonical_id = FinancialCollectionPriorityService._int_or_none(
            company.get("canonical_company_id")
        )
        requested_id = FinancialCollectionPriorityService._int_or_none(
            company.get("company_id")
        )
        return (
            (source_presence.get(canonical_id) if canonical_id is not None else None)
            or (source_presence.get(requested_id) if requested_id is not None else None)
            or ["manual-id"]
        )

    @staticmethod
    def _source_presence(labels: list[str]) -> dict[str, Any]:
        labels = FinancialCollectionPriorityService._sort_labels(labels)
        return {
            "from_top_predictions": "top-predictions" in labels,
            "from_bond_universe": "bond-universe" in labels,
            "from_manual_ids": "manual-id" in labels or "manual" in labels,
            "from_company_names": "company-name" in labels,
            "source_labels": labels,
        }

    @staticmethod
    def _sort_labels(labels: list[str]) -> list[str]:
        cleaned = []
        for label in labels:
            normalized = str(label).strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return sorted(
            cleaned,
            key=lambda item: (
                SOURCE_LABEL_ORDER.index(item)
                if item in SOURCE_LABEL_ORDER
                else len(SOURCE_LABEL_ORDER),
                item,
            ),
        )

    @staticmethod
    def _recommended_collection(preview: dict[str, Any]) -> dict[str, Any]:
        if preview.get("has_financial_report"):
            required = list(preview.get("recommended_next_fields") or [])
            return {
                "collection_type": "missing_key_fields"
                if required
                else "no_collection_needed",
                "period_preference": "latest_annual",
                "required_fields": required,
                "optional_fields": FULL_ANNUAL_IFRS_OPTIONAL_FIELDS,
            }
        return {
            "collection_type": "full_annual_ifrs_report",
            "period_preference": "latest_annual",
            "required_fields": FULL_ANNUAL_IFRS_REQUIRED_FIELDS,
            "optional_fields": FULL_ANNUAL_IFRS_OPTIONAL_FIELDS,
        }

    @staticmethod
    def _priority_reasons(
        preview: dict[str, Any],
        classification: dict[str, Any],
        source_presence: dict[str, Any],
        score_reasons: list[str],
    ) -> list[str]:
        reasons = list(dict.fromkeys(score_reasons))
        if source_presence["source_labels"] and "issuer appears in target universe" not in reasons:
            reasons.append("issuer appears in target universe")
        if classification["issuer_type"] == "unknown":
            reasons.append("issuer classification requires review")
        if not preview.get("has_financial_report"):
            reasons.append("financial-aware risk scoring is not ready")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _queue_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": None,
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "canonical_company_id": row["canonical_company_id"],
            "canonical_company_name": row["canonical_company_name"],
            "issuer_type": row["issuer_type"],
            "classification_confidence": row["classification_confidence"],
            "classification_reasons": row["classification_reasons"],
            "priority_level": row["priority_level"],
            "priority_score": row["priority_score"],
            "has_financial_report": row["has_financial_report"],
            "risk_scoring_readiness": row["risk_scoring_readiness"],
            "coverage_status": row["coverage_status"],
            "source_presence": row["source_presence"],
            "bond_context": row["bond_context"],
            "recommended_collection": row["recommended_collection"],
            "priority_reasons": row["priority_reasons"],
            "blocking_reasons": row["blocking_reasons"],
            "safety": row["safety"],
        }

    @staticmethod
    def _covered_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "canonical_company_id": row["canonical_company_id"],
            "canonical_company_name": row["canonical_company_name"],
            "issuer_type": row["issuer_type"],
            "risk_scoring_readiness": row["risk_scoring_readiness"],
            "has_financial_report": row["has_financial_report"],
            "recommended_next_fields": row["recommended_next_fields"],
            "recommended_collection": row["recommended_collection"],
            "priority_reasons": row["priority_reasons"],
            "source_presence": row["source_presence"],
        }

    @staticmethod
    def _excluded_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "canonical_company_id": row["canonical_company_id"],
            "canonical_company_name": row["canonical_company_name"],
            "issuer_type": row["issuer_type"],
            "classification_confidence": row["classification_confidence"],
            "classification_reasons": row["classification_reasons"],
            "priority_score": row["priority_score"],
            "priority_level": row["priority_level"],
            "source_presence": row["source_presence"],
        }

    @staticmethod
    def _summary(
        rows: list[dict[str, Any]],
        priority_queue: list[dict[str, Any]],
        already_covered: list[dict[str, Any]],
        excluded: list[dict[str, Any]],
    ) -> dict[str, int]:
        return {
            "high_priority_count": sum(
                1 for row in priority_queue if row["priority_level"] == "high"
            ),
            "medium_priority_count": sum(
                1 for row in priority_queue if row["priority_level"] == "medium"
            ),
            "low_priority_count": sum(
                1 for row in priority_queue if row["priority_level"] == "low"
            ),
            "already_covered_count": len(already_covered),
            "excluded_count": len(excluded),
            "corporate_count": sum(1 for row in rows if row["issuer_type"] == "corporate"),
            "government_like_count": sum(
                1 for row in rows if row["issuer_type"] == "government_like"
            ),
            "unknown_issuer_type_count": sum(
                1 for row in rows if row["issuer_type"] == "unknown"
            ),
            "missing_report_count": sum(
                1 for row in rows if not row["has_financial_report"]
            ),
            "partial_report_count": sum(
                1 for row in rows if row["risk_scoring_readiness"] == "partial"
            ),
            "ready_report_count": sum(
                1 for row in rows if row["risk_scoring_readiness"] == "ready"
            ),
        }

    @staticmethod
    def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        source = row.get("source_presence") or {}
        source_rank = (
            0
            if source.get("from_top_predictions") and source.get("from_bond_universe")
            else 1
            if source.get("from_top_predictions")
            else 2
            if source.get("from_bond_universe")
            else 3
        )
        level_rank = {"high": 0, "medium": 1, "low": 2}.get(
            row.get("priority_level"),
            3,
        )
        bond_count = int((row.get("bond_context") or {}).get("bond_count") or 0)
        return (
            -int(row.get("priority_score") or 0),
            level_rank,
            source_rank,
            -bond_count,
            str(row.get("company_name") or ""),
            int(row.get("company_id") or 0),
        )

    @staticmethod
    def _priority_level(score: int) -> str:
        if score >= 75:
            return "high"
        if score >= 40:
            return "medium"
        return "low"

    @staticmethod
    def _safety() -> dict[str, bool]:
        return {
            "read_only": True,
            "would_import_report": False,
            "would_mutate_scores": False,
            "would_trigger_paper_trading": False,
        }

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.upper().replace("Ё", "Е").split())

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except ValueError:
            return None
