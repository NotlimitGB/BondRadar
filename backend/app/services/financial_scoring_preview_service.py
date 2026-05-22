from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.financial_report import FinancialReport
from app.services.company_identity_resolution_service import (
    ACCEPTED_DUPLICATE_STATUS,
    ACCEPTED_REVIEW_STATUSES,
)
from app.services.financial_report_diagnostics_service import (
    FinancialReportDiagnosticsService,
)


PREVIEW_ONLY_ADJUSTMENTS = {
    "risk_penalty_points": 0,
    "risk_penalty_label": "preview_only",
    "score_adjustment_points": 0,
    "score_adjustment_label": "preview_only",
}


class FinancialScoringPreviewService:
    def __init__(
        self,
        db: Session,
        *,
        diagnostics_service: FinancialReportDiagnosticsService | None = None,
    ) -> None:
        self.db = db
        self.diagnostics_service = diagnostics_service or FinancialReportDiagnosticsService(db)

    def get_company_financial_scoring_preview(
        self,
        company_id: int,
        *,
        include_diagnostics: bool = True,
        include_bond_context: bool = True,
    ) -> dict[str, Any]:
        diagnostics = self.diagnostics_service.get_company_financial_report_diagnostics(
            company_id,
            include_duplicate_context=True,
            include_derived_metrics=True,
        )
        readiness = {
            "safe_for_feature_pipeline": diagnostics.get("safe_for_feature_pipeline"),
            "safe_for_risk_scoring": diagnostics.get("safe_for_risk_scoring"),
            "risk_scoring_readiness": diagnostics.get("risk_scoring_readiness"),
        }
        latest_report = diagnostics.get("latest_report")
        factors = self._risk_factors(diagnostics)
        blocking_reasons = self._blocking_reasons(diagnostics, factors)
        preview: dict[str, Any] = {
            "company_id": diagnostics.get("company_id"),
            "company_name": diagnostics.get("company_name"),
            "canonical_company_id": diagnostics.get("canonical_company_id"),
            "canonical_company_name": diagnostics.get("canonical_company_name"),
            "has_financial_report": diagnostics.get("has_financial_report"),
            "latest_report": self._latest_report_preview(latest_report),
            "diagnostics_readiness": readiness,
            "financial_risk_factors": factors,
            "fallback_metrics_used": self._fallback_metrics(diagnostics),
            "suggested_adjustments": dict(PREVIEW_ONLY_ADJUSTMENTS),
            "blocking_reasons": blocking_reasons,
            "recommended_next_fields": diagnostics.get("recommended_next_fields") or [],
            "dry_run_only": True,
            "would_mutate_scores": False,
            "would_trigger_paper_trading": False,
        }
        preview["diagnostics"] = diagnostics if include_diagnostics else None
        preview["bond_context"] = (
            self._bond_context(diagnostics) if include_bond_context else None
        )
        return preview

    def get_many_company_financial_scoring_previews(
        self,
        company_ids: list[int],
        *,
        include_diagnostics: bool = True,
        include_bond_context: bool = True,
    ) -> dict[str, Any]:
        companies = [
            self.get_company_financial_scoring_preview(
                company_id,
                include_diagnostics=include_diagnostics,
                include_bond_context=include_bond_context,
            )
            for company_id in company_ids
        ]
        return {
            "status": "passed",
            "company_count": len(companies),
            "companies": companies,
            "read_only": True,
            "dry_run_only": True,
            "import_executed": False,
            "paper_trading_called": False,
        }

    def _risk_factors(self, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
        if not diagnostics.get("has_financial_report"):
            return []
        factors: list[dict[str, Any]] = []
        derived = diagnostics.get("derived_metrics") or {}
        computed = derived.get("computed") or {}
        fallback = derived.get("fallback") or {}
        missing_metrics = derived.get("missing") or []
        signal = diagnostics.get("signal_explanation") or {}

        gross_debt = self._number(computed.get("gross_debt_to_ebitda"))
        if gross_debt is not None:
            severity = self._debt_to_ebitda_severity(gross_debt)
            factors.append(
                {
                    "factor": "gross_debt_to_ebitda",
                    "value": self._json_number(gross_debt),
                    "severity": severity,
                    "impact": "negative" if severity in {"elevated", "high"} else "neutral",
                    "reason": self._gross_debt_reason(severity),
                }
            )

        net_debt_fallback = self._number(fallback.get("net_debt_to_ebitda_fallback"))
        if net_debt_fallback is not None:
            severity = self._debt_to_ebitda_severity(net_debt_fallback)
            factors.append(
                {
                    "factor": "net_debt_to_ebitda_fallback",
                    "value": self._json_number(net_debt_fallback),
                    "severity": severity,
                    "impact": "negative" if severity in {"elevated", "high"} else "neutral",
                    "reason": (
                        "fallback net debt to EBITDA is "
                        f"{severity}; fallback metric used because reported net_debt is missing"
                    ),
                    "fallback": True,
                }
            )

        self._append_threshold_factor(
            factors,
            "cash_to_total_debt",
            computed.get("cash_to_total_debt"),
            self._cash_to_total_debt_severity,
            supportive_positive=True,
        )
        self._append_threshold_factor(
            factors,
            "short_term_debt_ratio",
            computed.get("short_term_debt_ratio"),
            self._short_term_debt_ratio_severity,
            supportive_positive=False,
        )
        self._append_threshold_factor(
            factors,
            "operating_cash_flow_to_debt",
            computed.get("operating_cash_flow_to_debt"),
            self._operating_cash_flow_to_debt_severity,
            supportive_positive=True,
        )

        interest_missing = next(
            (
                item
                for item in missing_metrics
                if item.get("metric") == "interest_coverage"
            ),
            None,
        )
        interest_value = self._number(computed.get("interest_coverage"))
        if interest_value is None and interest_missing is not None:
            factors.append(
                {
                    "factor": "interest_coverage",
                    "value": None,
                    "severity": "warning",
                    "impact": "unknown",
                    "reason": interest_missing.get("reason") or "interest coverage is missing",
                }
            )
        elif interest_value is not None:
            factors.append(
                {
                    "factor": "interest_coverage",
                    "value": self._json_number(interest_value),
                    "severity": "computed",
                    "impact": "informational",
                    "reason": "interest coverage can be computed from EBITDA and interest expense",
                }
            )

        if "net_profit is negative" in (signal.get("warnings") or []):
            factors.append(
                {
                    "factor": "net_profit",
                    "value": self._net_profit_value(diagnostics),
                    "severity": "warning",
                    "impact": "negative",
                    "reason": "net profit is negative",
                }
            )
        for critical in signal.get("critical") or []:
            if "ebitda" in critical:
                factors.append(
                    {
                        "factor": "ebitda",
                        "value": None,
                        "severity": "critical",
                        "impact": "negative",
                        "reason": critical,
                    }
                )
        return factors

    def _append_threshold_factor(
        self,
        factors: list[dict[str, Any]],
        factor: str,
        raw_value: Any,
        classifier: Any,
        *,
        supportive_positive: bool,
    ) -> None:
        value = self._number(raw_value)
        if value is None:
            return
        severity = classifier(value)
        if severity in {"supportive", "low"} and supportive_positive:
            impact = "positive"
        elif severity in {"weak", "elevated", "high"}:
            impact = "negative"
        else:
            impact = "neutral"
        factors.append(
            {
                "factor": factor,
                "value": self._json_number(value),
                "severity": severity,
                "impact": impact,
                "reason": f"{factor} preview severity is {severity}",
            }
        )

    def _blocking_reasons(
        self,
        diagnostics: dict[str, Any],
        factors: list[dict[str, Any]],
    ) -> list[str]:
        if not diagnostics.get("has_financial_report"):
            return ["financial report is missing"]
        reasons: list[str] = []
        signal = diagnostics.get("signal_explanation") or {}
        for reason in signal.get("reasons") or []:
            if reason == "interest_coverage cannot be computed":
                continue
            if reason not in reasons:
                reasons.append(reason)
        for critical in signal.get("critical") or []:
            if critical not in reasons:
                reasons.append(critical)
        if diagnostics.get("safe_for_risk_scoring") is False:
            reasons.append("safe_for_risk_scoring is false")
        for factor in factors:
            if factor.get("factor") == "interest_coverage" and factor.get("value") is None:
                message = factor.get("reason") or "interest_coverage is missing"
                if message not in reasons:
                    reasons.append(message)
        return reasons

    @staticmethod
    def _latest_report_preview(report: dict[str, Any] | None) -> dict[str, Any] | None:
        if report is None:
            return None
        return {
            "id": report.get("id"),
            "company_id": report.get("company_id"),
            "period_year": report.get("period_year"),
            "period_quarter": report.get("period_quarter"),
            "period_end_date": report.get("period_end_date"),
            "source": report.get("source"),
            "signal": report.get("signal"),
        }

    @staticmethod
    def _fallback_metrics(diagnostics: dict[str, Any]) -> dict[str, Any]:
        derived = diagnostics.get("derived_metrics") or {}
        return dict(derived.get("fallback") or {})

    def _bond_context(self, diagnostics: dict[str, Any]) -> dict[str, Any]:
        canonical_id = self._int_or_none(diagnostics.get("canonical_company_id"))
        requested_id = self._int_or_none(diagnostics.get("company_id"))
        if canonical_id is None:
            return {
                "status": "not_available",
                "reason": "canonical company id is unavailable",
            }
        company_ids = {canonical_id}
        if requested_id is not None:
            company_ids.add(requested_id)
        company_ids.update(self._accepted_duplicate_ids(canonical_id))
        bonds = list(
            self.db.execute(
                select(Bond)
                .where(Bond.company_id.in_(sorted(company_ids)))
                .order_by(Bond.id.asc())
            ).scalars()
        )
        return {
            "status": "available",
            "bond_count": len(bonds),
            "sample_bonds": [
                {
                    "id": bond.id,
                    "company_id": bond.company_id,
                    "secid": bond.secid,
                    "isin": bond.isin,
                    "name": bond.name,
                    "source_reason": (
                        "corporate bond universe"
                        if bond.company_id == canonical_id
                        else "accepted duplicate candidate bond context"
                    ),
                }
                for bond in bonds[:5]
            ],
        }

    def _accepted_duplicate_ids(self, canonical_company_id: int) -> set[int]:
        return set(
            self.db.execute(
                select(CompanyIdentityDuplicateCandidate.candidate_company_id).where(
                    CompanyIdentityDuplicateCandidate.canonical_company_id
                    == canonical_company_id,
                    CompanyIdentityDuplicateCandidate.status
                    == ACCEPTED_DUPLICATE_STATUS,
                    CompanyIdentityDuplicateCandidate.review_status.in_(
                        ACCEPTED_REVIEW_STATUSES
                    ),
                )
            ).scalars()
        )

    @staticmethod
    def _debt_to_ebitda_severity(value: Decimal) -> str:
        if value <= Decimal("2.0"):
            return "low"
        if value <= Decimal("3.5"):
            return "moderate"
        if value <= Decimal("5.0"):
            return "elevated"
        return "high"

    @staticmethod
    def _cash_to_total_debt_severity(value: Decimal) -> str:
        if value >= Decimal("0.30"):
            return "supportive"
        if value >= Decimal("0.15"):
            return "neutral"
        return "weak"

    @staticmethod
    def _short_term_debt_ratio_severity(value: Decimal) -> str:
        if value <= Decimal("0.25"):
            return "low"
        if value <= Decimal("0.50"):
            return "moderate"
        return "elevated"

    @staticmethod
    def _operating_cash_flow_to_debt_severity(value: Decimal) -> str:
        if value >= Decimal("0.15"):
            return "supportive"
        if value >= Decimal("0.05"):
            return "neutral"
        return "weak"

    @staticmethod
    def _gross_debt_reason(severity: str) -> str:
        if severity == "high":
            return "gross debt to EBITDA is above conservative threshold"
        return f"gross debt to EBITDA preview severity is {severity}"

    def _net_profit_value(self, diagnostics: dict[str, Any]) -> Any:
        report = diagnostics.get("latest_report") or {}
        report_id = self._int_or_none(report.get("id"))
        if report_id is None:
            return None
        financial_report = self.db.get(FinancialReport, report_id)
        if financial_report is None or financial_report.net_profit is None:
            return None
        return self._json_number(Decimal(financial_report.net_profit))

    @staticmethod
    def _number(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _json_number(value: Decimal) -> int | float:
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
