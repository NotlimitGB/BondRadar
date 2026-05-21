from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.company_score import CompanyScore
from app.models.financial_report import FinancialReport
from app.services.financial_ratios import FinancialRatiosService


IMPORTANT_FIELDS = [
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
    "company_score",
]


class CompanyCreditHealthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def calculate_for_company(
        self,
        company_id: int,
        *,
        as_of_date: date | None = None,
    ) -> CompanyCreditHealthSnapshot:
        target_date = as_of_date or date.today()
        company = self.db.get(Company, company_id)
        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        report, report_note = self._latest_report(company_id, target_date)
        company_score = self._latest_company_score(company_id, target_date)
        payload = self._build_payload(
            company=company,
            report=report,
            company_score=company_score,
            as_of_date=target_date,
            report_note=report_note,
        )
        snapshot = self._upsert_snapshot(company.id, target_date, payload)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            snapshot = self._upsert_snapshot(company.id, target_date, payload)
            self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def get_latest(
        self,
        company_id: int,
        *,
        as_of_date: date | None = None,
    ) -> CompanyCreditHealthSnapshot:
        if self.db.get(Company, company_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )
        stmt = select(CompanyCreditHealthSnapshot).where(
            CompanyCreditHealthSnapshot.company_id == company_id
        )
        if as_of_date is not None:
            stmt = stmt.where(CompanyCreditHealthSnapshot.as_of_date <= as_of_date)
        snapshot = self.db.execute(
            stmt.order_by(
                CompanyCreditHealthSnapshot.as_of_date.desc(),
                CompanyCreditHealthSnapshot.created_at.desc(),
                CompanyCreditHealthSnapshot.id.desc(),
            ).limit(1)
        ).scalar_one_or_none()
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company credit health snapshot not found",
            )
        return snapshot

    def _latest_report(
        self,
        company_id: int,
        as_of_date: date,
    ) -> tuple[FinancialReport | None, str | None]:
        end_of_day = datetime.combine(as_of_date, time.max)
        period_priority = case(
            (FinancialReport.period_quarter == 0, 5),
            (FinancialReport.period_quarter == 4, 4),
            (FinancialReport.period_quarter == 3, 3),
            (FinancialReport.period_quarter == 2, 2),
            (FinancialReport.period_quarter == 1, 1),
            else_=0,
        )
        base = select(FinancialReport).where(FinancialReport.company_id == company_id)
        published = self.db.execute(
            base.where(
                FinancialReport.published_at.is_not(None),
                FinancialReport.published_at <= end_of_day,
            )
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.published_at.desc(),
                FinancialReport.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if published is not None:
            return published, None

        legacy = self.db.execute(
            base.where(
                FinancialReport.published_at.is_(None),
                FinancialReport.created_at <= end_of_day,
            )
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.created_at.desc(),
                FinancialReport.id.desc(),
            ).limit(1)
        ).scalar_one_or_none()
        if legacy is not None:
            return (
                legacy,
                "Financial report publication date is missing, fallback selection was used",
            )

        legacy_period = self.db.execute(
            base.where(FinancialReport.published_at.is_(None))
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.created_at.desc(),
                FinancialReport.id.desc(),
            ).limit(1)
        ).scalar_one_or_none()
        if legacy_period is None:
            return None, None
        return (
            legacy_period,
            "Financial report publication date is missing, fallback selection was used",
        )

    def _latest_company_score(
        self,
        company_id: int,
        as_of_date: date,
    ) -> CompanyScore | None:
        end_of_day = datetime.combine(as_of_date, time.max)
        return self.db.execute(
            select(CompanyScore)
            .where(
                CompanyScore.company_id == company_id,
                CompanyScore.created_at <= end_of_day,
            )
            .order_by(CompanyScore.created_at.desc(), CompanyScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _build_payload(
        self,
        *,
        company: Company,
        report: FinancialReport | None,
        company_score: CompanyScore | None,
        as_of_date: date,
        report_note: str | None,
    ) -> dict[str, Any]:
        missing_data: list[str] = []
        risk_factors: list[str] = []
        positive_factors: list[str] = []

        if report is None:
            missing_data.append("Financial report is missing")
        if company_score is None:
            missing_data.append("Company score is missing")

        company_score_value = self._company_score_value(company_score)
        values = self._report_values(report)
        for field in IMPORTANT_FIELDS:
            if field == "company_score":
                if company_score_value is None:
                    missing_data.append("company_score is missing")
            elif values.get(field) is None:
                missing_data.append(f"{field} is missing")

        ratios = self._ratios(values)
        score = self._credit_health_score(
            ratios=ratios,
            values=values,
            company_score_value=company_score_value,
            missing_count=len(missing_data),
            risk_factors=risk_factors,
            positive_factors=positive_factors,
        )
        critical_red_flags = self._critical_red_flags(
            ratios=ratios,
            values=values,
            company_score_value=company_score_value,
            missing_count=len(missing_data),
        )
        for flag in critical_red_flags:
            if flag not in risk_factors:
                risk_factors.append(flag)

        data_quality_level = self._data_quality_level(len(missing_data))
        credit_status = self._credit_status(
            score=score,
            missing_count=len(missing_data),
            critical_red_flags=critical_red_flags,
        )
        risk_level = self._risk_level(credit_status)
        if report_note is not None:
            risk_factors.append(report_note)

        explanation = {
            "summary": self._summary(credit_status),
            "credit_status_reason": self._status_reason(
                credit_status,
                score,
                critical_red_flags,
                len(missing_data),
            ),
            "risk_factors": risk_factors,
            "positive_factors": positive_factors,
            "missing_data": missing_data,
            "ratios": self._json_numbers(ratios),
            "source_data": {
                "financial_report_id": report.id if report is not None else None,
                "company_score_id": (
                    company_score.id if company_score is not None else None
                ),
                "as_of_date": as_of_date.isoformat(),
                "company_id": company.id,
            },
        }
        return {
            "financial_report_id": report.id if report is not None else None,
            "company_score_id": company_score.id if company_score is not None else None,
            "credit_health_score": score,
            "credit_status": credit_status,
            "risk_level": risk_level,
            "data_quality_level": data_quality_level,
            **ratios,
            **values,
            "risk_factors": risk_factors,
            "positive_factors": positive_factors,
            "missing_data": missing_data,
            "explanation": explanation,
        }

    @staticmethod
    def _report_values(report: FinancialReport | None) -> dict[str, Decimal | None]:
        fields = [
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
        ]
        return {
            field: getattr(report, field) if report is not None else None
            for field in fields
        }

    @staticmethod
    def _ratios(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        safe_divide = FinancialRatiosService.safe_divide
        return {
            "debt_to_ebitda": safe_divide(values["net_debt"], values["ebitda"]),
            "interest_coverage": safe_divide(
                values["ebitda"], values["interest_expense"]
            ),
            "cash_to_short_term_debt": safe_divide(
                values["cash"], values["short_term_debt"]
            ),
            "ocf_to_total_debt": safe_divide(
                values["operating_cash_flow"], values["total_debt"]
            ),
            "debt_to_equity": safe_divide(values["total_debt"], values["equity"]),
            "net_profit_margin": safe_divide(
                values["net_profit"], values["revenue"]
            ),
        }

    @staticmethod
    def _company_score_value(company_score: CompanyScore | None) -> Decimal | None:
        if company_score is None:
            return None
        if company_score.final_company_score is not None:
            return Decimal(company_score.final_company_score)
        return Decimal(company_score.score)

    @classmethod
    def _credit_health_score(
        cls,
        *,
        ratios: dict[str, Decimal | None],
        values: dict[str, Decimal | None],
        company_score_value: Decimal | None,
        missing_count: int,
        risk_factors: list[str],
        positive_factors: list[str],
    ) -> int:
        score = Decimal("50")
        score += cls._company_score_adjustment(company_score_value, risk_factors, positive_factors)
        score += cls._ratio_adjustment(
            ratios["interest_coverage"],
            Decimal("4"),
            Decimal("2"),
            Decimal("1.5"),
            Decimal("1"),
            "Interest coverage is strong",
            "Interest coverage is weak",
            positive_factors,
            risk_factors,
            higher_is_better=True,
        )
        score += cls._ratio_adjustment(
            ratios["debt_to_ebitda"],
            Decimal("2"),
            Decimal("3.5"),
            Decimal("3.5"),
            Decimal("5"),
            "Debt load is moderate relative to EBITDA",
            "Debt load is high relative to EBITDA",
            positive_factors,
            risk_factors,
            higher_is_better=False,
        )
        if ratios["cash_to_short_term_debt"] is not None:
            if ratios["cash_to_short_term_debt"] >= Decimal("1"):
                score += Decimal("10")
                positive_factors.append("Cash covers short-term debt")
            elif ratios["cash_to_short_term_debt"] < Decimal("0.5"):
                score -= Decimal("15")
                risk_factors.append("Cash coverage of short-term debt is weak")
        if ratios["ocf_to_total_debt"] is not None:
            if ratios["ocf_to_total_debt"] >= Decimal("0.2"):
                score += Decimal("10")
                positive_factors.append("Operating cash flow supports total debt")
            elif ratios["ocf_to_total_debt"] < Decimal("0"):
                score -= Decimal("10")
                risk_factors.append("Operating cash flow is negative relative to debt")
        if ratios["net_profit_margin"] is not None and ratios["net_profit_margin"] > 0:
            score += Decimal("5")
            positive_factors.append("Net profit margin is positive")
        if values["equity"] is not None:
            if values["equity"] > 0:
                score += Decimal("5")
                positive_factors.append("Equity is positive")
            elif values["equity"] < 0:
                score -= Decimal("25")
                risk_factors.append("Equity is negative")
        if values["net_profit"] is not None and values["net_profit"] < 0:
            score -= Decimal("10")
            risk_factors.append("Net profit is negative")
        if missing_count >= 6:
            score -= Decimal("20")
            risk_factors.append("Important credit data is largely missing")
        elif missing_count >= 3:
            score -= Decimal("10")
            risk_factors.append("Important credit data is partially missing")
        return cls._clamp_score(score)

    @staticmethod
    def _company_score_adjustment(
        company_score_value: Decimal | None,
        risk_factors: list[str],
        positive_factors: list[str],
    ) -> Decimal:
        if company_score_value is None:
            return Decimal("0")
        if company_score_value >= Decimal("80"):
            positive_factors.append("Company score is strong")
            return Decimal("15")
        if company_score_value >= Decimal("60"):
            positive_factors.append("Company score is acceptable")
            return Decimal("8")
        if company_score_value < Decimal("40"):
            risk_factors.append("Company score is weak")
            return Decimal("-25")
        risk_factors.append("Company score is below target range")
        return Decimal("-10")

    @staticmethod
    def _ratio_adjustment(
        value: Decimal | None,
        good: Decimal,
        okay: Decimal,
        weak: Decimal,
        critical: Decimal,
        positive_text: str,
        negative_text: str,
        positive_factors: list[str],
        risk_factors: list[str],
        *,
        higher_is_better: bool,
    ) -> Decimal:
        if value is None:
            return Decimal("0")
        if higher_is_better:
            if value >= good:
                positive_factors.append(positive_text)
                return Decimal("15")
            if value >= okay:
                return Decimal("8")
            if value < critical:
                risk_factors.append(negative_text)
                return Decimal("-25")
            if value < weak:
                risk_factors.append(negative_text)
                return Decimal("-15")
        else:
            if value <= good:
                positive_factors.append(positive_text)
                return Decimal("15")
            if value <= okay:
                return Decimal("8")
            if value > critical:
                risk_factors.append(negative_text)
                return Decimal("-25")
            if value > weak:
                risk_factors.append(negative_text)
                return Decimal("-15")
        return Decimal("0")

    @staticmethod
    def _critical_red_flags(
        *,
        ratios: dict[str, Decimal | None],
        values: dict[str, Decimal | None],
        company_score_value: Decimal | None,
        missing_count: int,
    ) -> list[str]:
        flags: list[str] = []
        if values["equity"] is not None and values["equity"] < 0:
            flags.append("Equity is negative")
        if ratios["interest_coverage"] is not None and ratios["interest_coverage"] < Decimal("1"):
            flags.append("Interest coverage is below 1")
        if ratios["debt_to_ebitda"] is not None and ratios["debt_to_ebitda"] > Decimal("5"):
            flags.append("Debt to EBITDA is above 5")
        if company_score_value is not None and company_score_value < Decimal("30"):
            flags.append("Company score is below 30")
        return flags

    @staticmethod
    def _credit_status(
        *,
        score: int,
        missing_count: int,
        critical_red_flags: list[str],
    ) -> str:
        if missing_count >= 7:
            return "insufficient_data"
        if critical_red_flags:
            return "credit_distressed"
        if missing_count >= 5:
            return "insufficient_data"
        if missing_count >= 3 and score < 60:
            return "credit_watchlist"
        if score >= 80:
            return "credit_stable"
        if score >= 60:
            return "credit_watchlist"
        if score >= 40:
            return "credit_stressed"
        return "credit_stressed"

    @staticmethod
    def _risk_level(credit_status: str) -> str:
        return {
            "credit_stable": "low",
            "credit_watchlist": "medium",
            "credit_stressed": "high",
            "credit_distressed": "critical",
            "insufficient_data": "unknown",
        }[credit_status]

    @staticmethod
    def _data_quality_level(missing_count: int) -> str:
        if missing_count <= 2:
            return "high"
        if missing_count <= 5:
            return "medium"
        if missing_count <= 8:
            return "low"
        return "insufficient"

    @staticmethod
    def _summary(credit_status: str) -> str:
        return {
            "credit_stable": "Issuer credit health looks stable for further analysis.",
            "credit_watchlist": "Issuer credit health requires watchlist-level review.",
            "credit_stressed": "Issuer credit health shows elevated stress.",
            "credit_distressed": "Issuer credit health shows critical risk factors.",
            "insufficient_data": "Issuer credit health cannot be fully assessed due to insufficient data.",
        }[credit_status]

    @staticmethod
    def _status_reason(
        credit_status: str,
        score: int,
        critical_red_flags: list[str],
        missing_count: int,
    ) -> str:
        if credit_status == "insufficient_data":
            return f"{missing_count} important credit fields are missing."
        if critical_red_flags:
            return "Critical red flags are present: " + "; ".join(critical_red_flags)
        return f"Credit health score is {score}."

    def _upsert_snapshot(
        self,
        company_id: int,
        as_of_date: date,
        payload: dict[str, Any],
    ) -> CompanyCreditHealthSnapshot:
        snapshot = self.db.execute(
            select(CompanyCreditHealthSnapshot).where(
                CompanyCreditHealthSnapshot.company_id == company_id,
                CompanyCreditHealthSnapshot.as_of_date == as_of_date,
            )
        ).scalar_one_or_none()
        data = {"company_id": company_id, "as_of_date": as_of_date, **payload}
        if snapshot is None:
            snapshot = CompanyCreditHealthSnapshot(**data)
            self.db.add(snapshot)
            return snapshot
        for field, value in data.items():
            setattr(snapshot, field, value)
        self.db.add(snapshot)
        return snapshot

    @staticmethod
    def _json_numbers(values: dict[str, Decimal | None]) -> dict[str, float | None]:
        return {
            key: None if value is None else float(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
            for key, value in values.items()
        }

    @staticmethod
    def _clamp_score(value: Decimal) -> int:
        value = min(Decimal("100"), max(Decimal("0"), value))
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
