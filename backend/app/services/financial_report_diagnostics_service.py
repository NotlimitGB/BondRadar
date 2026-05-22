from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.financial_report import FinancialReport
from app.services.company_identity_resolution_service import (
    CompanyIdentityResolutionService,
)
from app.services.financial_ratios import FinancialRatiosService


RAW_FIELDS = (
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
    "debt_to_ebitda",
    "interest_coverage",
)
CORE_INCOME_FIELDS = ("revenue", "ebitda", "net_profit")
CORE_BALANCE_FIELDS = ("total_debt", "cash", "equity", "short_term_debt")
CORE_CASHFLOW_FIELDS = ("operating_cash_flow",)
RISK_REQUIRED_FIELDS = (
    "ebitda",
    "total_debt",
    "cash",
    "equity",
    "interest_expense",
)
OPTIONAL_FIELDS = ("net_debt", "debt_to_ebitda", "interest_coverage")


class FinancialReportDiagnosticsService:
    def __init__(
        self,
        db: Session,
        *,
        ratios_service: FinancialRatiosService | None = None,
        identity_service: CompanyIdentityResolutionService | None = None,
    ) -> None:
        self.db = db
        self.ratios_service = ratios_service or FinancialRatiosService()
        self.identity_service = identity_service or CompanyIdentityResolutionService(db)

    def get_company_financial_report_diagnostics(
        self,
        company_id: int,
        *,
        include_duplicate_context: bool = True,
        include_derived_metrics: bool = True,
    ) -> dict[str, Any]:
        resolution = self.identity_service.resolve_company(company_id)
        company = self.db.get(Company, company_id)
        canonical = self.db.get(Company, resolution.canonical_company_id)
        canonical_id = resolution.canonical_company_id
        report = self._latest_report(canonical_id) if canonical is not None else None
        warnings = [
            {"code": item.code, "message": item.message, "company_id": item.company_id}
            for item in resolution.warnings
        ]

        diagnostics: dict[str, Any] = {
            "company_id": company_id,
            "company_name": company.name if company is not None else resolution.company_name,
            "canonical_company_id": canonical_id,
            "canonical_company_name": (
                canonical.name if canonical is not None else resolution.canonical_company_name
            ),
            "is_duplicate_candidate": resolution.is_duplicate_candidate,
            "has_financial_report": report is not None,
            "latest_report": self._report_payload(report),
            "raw_fields": self._raw_field_coverage(report),
            "derived_metrics": (
                self._derived_metrics(report) if include_derived_metrics else None
            ),
            "signal_explanation": self._signal_explanation(report),
            "recommended_next_fields": self._recommended_next_fields(report),
            "safe_for_feature_pipeline": report is not None,
            "safe_for_risk_scoring": self._safe_for_risk_scoring(report),
            "risk_scoring_readiness": self._risk_scoring_readiness(report),
            "warnings": warnings,
        }
        if include_duplicate_context:
            diagnostics["duplicate_context"] = {
                "requested_company_id": resolution.company_id,
                "requested_company_name": resolution.company_name,
                "canonical_company_id": resolution.canonical_company_id,
                "canonical_company_name": resolution.canonical_company_name,
                "is_canonical": resolution.is_canonical,
                "is_duplicate_candidate": resolution.is_duplicate_candidate,
                "duplicate_mapping_status": resolution.duplicate_mapping_status,
                "duplicate_review_status": resolution.duplicate_review_status,
                "duplicate_match_type": resolution.duplicate_match_type,
                "duplicate_match_score": self._json_decimal(
                    resolution.duplicate_match_score
                ),
            }
        else:
            diagnostics["duplicate_context"] = None
        return diagnostics

    def get_many_company_financial_report_diagnostics(
        self,
        company_ids: list[int],
        *,
        include_duplicate_context: bool = True,
        include_derived_metrics: bool = True,
    ) -> dict[str, Any]:
        companies = [
            self.get_company_financial_report_diagnostics(
                company_id,
                include_duplicate_context=include_duplicate_context,
                include_derived_metrics=include_derived_metrics,
            )
            for company_id in company_ids
        ]
        return {
            "status": "passed",
            "company_count": len(companies),
            "companies": companies,
            "read_only": True,
            "import_executed": False,
            "paper_trading_called": False,
        }

    def _latest_report(self, company_id: int) -> FinancialReport | None:
        period_priority = case(
            (FinancialReport.period_quarter == 0, 5),
            (FinancialReport.period_quarter == 4, 4),
            (FinancialReport.period_quarter == 3, 3),
            (FinancialReport.period_quarter == 2, 2),
            (FinancialReport.period_quarter == 1, 1),
            else_=0,
        )
        return self.db.execute(
            select(FinancialReport)
            .where(FinancialReport.company_id == company_id)
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.period_end_date.is_(None).asc(),
                FinancialReport.period_end_date.desc(),
                FinancialReport.published_at.is_(None).asc(),
                FinancialReport.published_at.desc(),
                FinancialReport.created_at.is_(None).asc(),
                FinancialReport.created_at.desc(),
                FinancialReport.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _report_payload(self, report: FinancialReport | None) -> dict[str, Any] | None:
        if report is None:
            return None
        return {
            "id": report.id,
            "company_id": report.company_id,
            "period_year": report.period_year,
            "period_quarter": report.period_quarter,
            "period_start_date": self._date_for_json(report.period_start_date),
            "period_end_date": self._date_for_json(report.period_end_date),
            "published_at": self._datetime_for_json(report.published_at),
            "currency": report.currency,
            "source": report.source,
            "signal": report.signal,
        }

    def _raw_field_coverage(self, report: FinancialReport | None) -> dict[str, Any]:
        values = self._report_values(report)
        present = [field for field in RAW_FIELDS if values.get(field) is not None]
        missing = [field for field in RAW_FIELDS if values.get(field) is None]
        return {
            "present": present,
            "missing": missing,
            "groups": {
                "core_income_fields": {
                    "present": [field for field in CORE_INCOME_FIELDS if field in present],
                    "missing": [field for field in CORE_INCOME_FIELDS if field in missing],
                },
                "core_balance_fields": {
                    "present": [field for field in CORE_BALANCE_FIELDS if field in present],
                    "missing": [field for field in CORE_BALANCE_FIELDS if field in missing],
                },
                "core_cashflow_fields": {
                    "present": [field for field in CORE_CASHFLOW_FIELDS if field in present],
                    "missing": [field for field in CORE_CASHFLOW_FIELDS if field in missing],
                },
                "risk_required_fields": {
                    "present": [field for field in RISK_REQUIRED_FIELDS if field in present],
                    "missing": [field for field in RISK_REQUIRED_FIELDS if field in missing],
                },
                "optional_fields": {
                    "present": [field for field in OPTIONAL_FIELDS if field in present],
                    "missing": [field for field in OPTIONAL_FIELDS if field in missing],
                },
            },
        }

    def _derived_metrics(self, report: FinancialReport | None) -> dict[str, Any]:
        if report is None:
            return {
                "computed": {},
                "fallback": {},
                "missing": [
                    {"metric": "all_derived_metrics", "reason": "financial report is missing"}
                ],
            }
        computed: dict[str, Any] = {}
        fallback: dict[str, Any] = {}
        missing: list[dict[str, str]] = []
        values = self._report_values(report)

        self._add_ratio(
            computed,
            missing,
            "gross_debt_to_ebitda",
            values["total_debt"],
            values["ebitda"],
            "total_debt",
            "ebitda",
        )

        net_debt_value = values["net_debt"]
        if net_debt_value is not None:
            computed["net_debt_reported"] = self._json_decimal(net_debt_value)
            self._add_ratio(
                computed,
                missing,
                "net_debt_to_ebitda",
                net_debt_value,
                values["ebitda"],
                "net_debt",
                "ebitda",
            )
        else:
            net_debt_fallback = self._subtract(values["total_debt"], values["cash"])
            if net_debt_fallback is None:
                missing.append(
                    {
                        "metric": "net_debt_fallback",
                        "reason": self._missing_reason(
                            "total_debt", values["total_debt"], "cash", values["cash"]
                        ),
                    }
                )
            else:
                fallback["net_debt_fallback"] = self._json_decimal(net_debt_fallback)
                ratio = self.ratios_service.safe_divide(
                    net_debt_fallback,
                    values["ebitda"],
                )
                if ratio is None:
                    missing.append(
                        {
                            "metric": "net_debt_to_ebitda_fallback",
                            "reason": self._missing_denominator_reason(
                                "ebitda", values["ebitda"]
                            ),
                        }
                    )
                else:
                    fallback["net_debt_to_ebitda_fallback"] = self._json_decimal(ratio)

        self._add_ratio(
            computed,
            missing,
            "cash_to_total_debt",
            values["cash"],
            values["total_debt"],
            "cash",
            "total_debt",
        )
        self._add_ratio(
            computed,
            missing,
            "short_term_debt_ratio",
            values["short_term_debt"],
            values["total_debt"],
            "short_term_debt",
            "total_debt",
        )
        self._add_ratio(
            computed,
            missing,
            "equity_ratio_proxy",
            values["equity"],
            values["total_debt"],
            "equity",
            "total_debt",
        )
        self._add_ratio(
            computed,
            missing,
            "operating_cash_flow_to_debt",
            values["operating_cash_flow"],
            values["total_debt"],
            "operating_cash_flow",
            "total_debt",
        )
        self._add_ratio(
            computed,
            missing,
            "interest_coverage",
            values["ebitda"],
            values["interest_expense"],
            "ebitda",
            "interest_expense",
        )

        feature_ratios = self.ratios_service.calculate(report)
        computed["feature_visible_ratios"] = {
            key: self._json_decimal(value) for key, value in feature_ratios.items()
        }
        return {"computed": computed, "fallback": fallback, "missing": missing}

    def _signal_explanation(self, report: FinancialReport | None) -> dict[str, Any]:
        if report is None:
            return {
                "signal": "insufficient_data",
                "reasons": ["financial report is missing"],
                "severity": "critical",
                "warnings": [],
            }
        values = self._report_values(report)
        reasons: list[str] = []
        warnings: list[str] = []
        critical: list[str] = []

        for field in RISK_REQUIRED_FIELDS:
            if values[field] is None:
                reasons.append(f"{field} is missing")
        if values["net_debt"] is None:
            reasons.append("net_debt is missing")
        if values["interest_expense"] is None:
            reasons.append("interest_coverage cannot be computed")
        if values["ebitda"] is None:
            critical.append("ebitda is missing")
        elif values["ebitda"] <= 0:
            critical.append("ebitda is zero or negative")
        if values["net_profit"] is not None and values["net_profit"] < 0:
            warnings.append("net_profit is negative")

        severity = "critical" if critical else "warning" if reasons or warnings else "healthy"
        return {
            "signal": report.signal,
            "reasons": reasons or ["required financial report fields are present"],
            "severity": severity,
            "critical": critical,
            "warnings": warnings,
        }

    def _recommended_next_fields(self, report: FinancialReport | None) -> list[str]:
        if report is None:
            return list(RISK_REQUIRED_FIELDS)
        values = self._report_values(report)
        recommendations: list[str] = []
        for field in ("interest_expense", "net_debt", "ebitda", "total_debt", "cash", "equity"):
            if values.get(field) is None:
                recommendations.append(field)
        return recommendations

    def _safe_for_risk_scoring(self, report: FinancialReport | None) -> bool:
        return self._risk_scoring_readiness(report) == "ready"

    def _risk_scoring_readiness(self, report: FinancialReport | None) -> str:
        if report is None:
            return "not_ready"
        values = self._report_values(report)
        if values["ebitda"] is None or values["ebitda"] <= 0:
            return "not_ready"
        missing_required = [
            field for field in RISK_REQUIRED_FIELDS if values.get(field) is None
        ]
        if missing_required:
            return "partial"
        return "ready"

    def _report_values(self, report: FinancialReport | None) -> dict[str, Decimal | None]:
        return {
            field: getattr(report, field) if report is not None else None
            for field in RAW_FIELDS
        }

    def _add_ratio(
        self,
        computed: dict[str, Any],
        missing: list[dict[str, str]],
        metric: str,
        numerator: Decimal | None,
        denominator: Decimal | None,
        numerator_name: str,
        denominator_name: str,
    ) -> None:
        value = self.ratios_service.safe_divide(numerator, denominator)
        if value is None:
            missing.append(
                {
                    "metric": metric,
                    "reason": self._missing_reason(
                        numerator_name, numerator, denominator_name, denominator
                    ),
                }
            )
            return
        computed[metric] = self._json_decimal(value)

    @staticmethod
    def _missing_reason(
        numerator_name: str,
        numerator: Decimal | None,
        denominator_name: str,
        denominator: Decimal | None,
    ) -> str:
        if numerator is None:
            return f"{numerator_name} is missing"
        if denominator is None:
            return f"{denominator_name} is missing"
        if denominator == 0:
            return f"{denominator_name} is zero"
        return "inputs are missing"

    @staticmethod
    def _missing_denominator_reason(
        denominator_name: str,
        denominator: Decimal | None,
    ) -> str:
        if denominator is None:
            return f"{denominator_name} is missing"
        if denominator == 0:
            return f"{denominator_name} is zero"
        return "denominator is missing"

    @staticmethod
    def _subtract(left: Decimal | None, right: Decimal | None) -> Decimal | None:
        if left is None or right is None:
            return None
        return left - right

    @staticmethod
    def _json_decimal(value: Decimal | None) -> int | float | None:
        if value is None:
            return None
        if value == value.to_integral_value():
            return int(value)
        rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return float(rounded)

    @staticmethod
    def _date_for_json(value: date | None) -> str | None:
        return None if value is None else value.isoformat()

    @staticmethod
    def _datetime_for_json(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()
