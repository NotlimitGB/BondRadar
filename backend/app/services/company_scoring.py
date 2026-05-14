from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.services.financial_ratios import FinancialRatiosService


RATIO_LABELS = {
    "net_debt_to_ebitda": "Net Debt / EBITDA",
    "debt_to_equity": "Debt / Equity",
    "interest_coverage": "Interest Coverage",
    "cash_to_short_term_debt": "Cash / Short Term Debt",
    "operating_cash_flow_to_total_debt": "Operating Cash Flow / Total Debt",
    "net_profit_margin": "Net Profit Margin",
}


class CompanyScoreService:
    def __init__(
        self,
        db: Session,
        ratios_service: FinancialRatiosService | None = None,
    ) -> None:
        self.db = db
        self.ratios_service = ratios_service or FinancialRatiosService()

    def calculate_for_company(self, company_id: int) -> CompanyScore:
        company = self.db.get(Company, company_id)
        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        report = self.get_latest_report(company_id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Financial report for company not found",
            )

        ratios = self.ratios_service.calculate(report)
        score_payload = self.calculate_scores(ratios, report)
        score = self._upsert_score(company, report, ratios, score_payload)
        self.db.commit()
        self.db.refresh(score)
        return score

    def get_latest_report(self, company_id: int) -> FinancialReport | None:
        period_priority = case(
            (FinancialReport.period_quarter == 0, 5),
            (FinancialReport.period_quarter == 4, 4),
            (FinancialReport.period_quarter == 3, 3),
            (FinancialReport.period_quarter == 2, 2),
            (FinancialReport.period_quarter == 1, 1),
            else_=0,
        )
        stmt = (
            select(FinancialReport)
            .where(FinancialReport.company_id == company_id)
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.created_at.desc(),
                FinancialReport.id.desc(),
            )
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def calculate_scores(
        self,
        ratios: dict[str, Decimal | None],
        report: FinancialReport,
    ) -> dict[str, Any]:
        debt_score = self._calculate_debt_score(
            ratios["net_debt_to_ebitda"],
            ratios["debt_to_equity"],
        )
        profitability_score = self._score_profitability(ratios["net_profit_margin"])
        liquidity_score = self._score_liquidity(ratios["cash_to_short_term_debt"])
        cashflow_score = self._score_cashflow(
            ratios["operating_cash_flow_to_total_debt"]
        )
        stability_score = self._score_stability(report)
        final_company_score = self._round_score(
            Decimal(debt_score) * Decimal("0.35")
            + Decimal(profitability_score) * Decimal("0.20")
            + Decimal(liquidity_score) * Decimal("0.20")
            + Decimal(cashflow_score) * Decimal("0.15")
            + Decimal(stability_score) * Decimal("0.10")
        )
        missing_ratio_count = sum(value is None for value in ratios.values())
        risk_level = self._risk_level(final_company_score, missing_ratio_count)
        explanation = self._build_explanation(
            ratios=ratios,
            debt_score=debt_score,
            profitability_score=profitability_score,
            liquidity_score=liquidity_score,
            cashflow_score=cashflow_score,
            stability_score=stability_score,
            final_company_score=final_company_score,
            risk_level=risk_level,
        )

        return {
            "debt_score": debt_score,
            "profitability_score": profitability_score,
            "liquidity_score": liquidity_score,
            "cashflow_score": cashflow_score,
            "stability_score": stability_score,
            "final_company_score": final_company_score,
            "risk_level": risk_level,
            "explanation": explanation,
        }

    def _upsert_score(
        self,
        company: Company,
        report: FinancialReport,
        ratios: dict[str, Decimal | None],
        score_payload: dict[str, Any],
    ) -> CompanyScore:
        as_of_date = date.today()
        source = "financial_score_service"
        score = self.db.execute(
            select(CompanyScore).where(
                CompanyScore.company_id == company.id,
                CompanyScore.as_of_date == as_of_date,
                CompanyScore.source == source,
            )
        ).scalar_one_or_none()
        signal = (
            AnalysisSignal.INSUFFICIENT_DATA.value
            if score_payload["risk_level"] == "insufficient_data"
            else AnalysisSignal.NEUTRAL.value
        )
        score_data = {
            "company_id": company.id,
            "report_id": report.id,
            "score": Decimal(score_payload["final_company_score"]),
            "signal": signal,
            "factors": {
                "ratios": self._ratios_for_json(ratios),
                "scores": {
                    "debt_score": score_payload["debt_score"],
                    "profitability_score": score_payload["profitability_score"],
                    "liquidity_score": score_payload["liquidity_score"],
                    "cashflow_score": score_payload["cashflow_score"],
                    "stability_score": score_payload["stability_score"],
                },
            },
            "summary": score_payload["explanation"]["summary"],
            "as_of_date": as_of_date,
            "source": source,
            **score_payload,
        }
        if score is None:
            score = CompanyScore(**score_data)
            self.db.add(score)
            return score

        for field, value in score_data.items():
            setattr(score, field, value)
        self.db.add(score)
        return score

    @classmethod
    def _calculate_debt_score(
        cls,
        net_debt_to_ebitda: Decimal | None,
        debt_to_equity: Decimal | None,
    ) -> int:
        available_scores = [
            score
            for score in (
                cls._score_net_debt_to_ebitda(net_debt_to_ebitda),
                cls._score_debt_to_equity(debt_to_equity),
            )
            if score is not None
        ]
        if not available_scores:
            return 50
        return cls._round_score(
            sum(Decimal(score) for score in available_scores)
            / Decimal(len(available_scores))
        )

    @staticmethod
    def _score_net_debt_to_ebitda(value: Decimal | None) -> int | None:
        if value is None:
            return None
        if value < Decimal("2"):
            return 100
        if value < Decimal("4"):
            return 75
        if value < Decimal("6"):
            return 45
        return 20

    @staticmethod
    def _score_debt_to_equity(value: Decimal | None) -> int | None:
        if value is None:
            return None
        if value < Decimal("1"):
            return 100
        if value < Decimal("2"):
            return 75
        if value < Decimal("4"):
            return 45
        return 20

    @staticmethod
    def _score_profitability(value: Decimal | None) -> int:
        if value is None:
            return 50
        if value > Decimal("0.15"):
            return 100
        if value >= Decimal("0.05"):
            return 75
        if value >= Decimal("0"):
            return 45
        return 20

    @staticmethod
    def _score_liquidity(value: Decimal | None) -> int:
        if value is None:
            return 50
        if value > Decimal("1"):
            return 100
        if value >= Decimal("0.5"):
            return 75
        if value >= Decimal("0.2"):
            return 45
        return 20

    @staticmethod
    def _score_cashflow(value: Decimal | None) -> int:
        if value is None:
            return 50
        if value > Decimal("0.3"):
            return 100
        if value >= Decimal("0.15"):
            return 75
        if value >= Decimal("0"):
            return 45
        return 20

    @staticmethod
    def _score_stability(report: FinancialReport) -> int:
        if report.revenue is None or report.revenue <= 0:
            return 30
        net_profit_positive = (
            report.net_profit is not None and report.net_profit > 0
        )
        cashflow_positive = (
            report.operating_cash_flow is not None
            and report.operating_cash_flow > 0
        )
        if net_profit_positive and cashflow_positive:
            return 90
        if net_profit_positive or cashflow_positive:
            return 70
        return 45

    @staticmethod
    def _risk_level(final_company_score: int, missing_ratio_count: int) -> str:
        if missing_ratio_count >= 4:
            return "insufficient_data"
        if final_company_score >= 80:
            return "low"
        if final_company_score >= 60:
            return "medium"
        if final_company_score >= 40:
            return "high"
        return "critical"

    def _build_explanation(
        self,
        *,
        ratios: dict[str, Decimal | None],
        debt_score: int,
        profitability_score: int,
        liquidity_score: int,
        cashflow_score: int,
        stability_score: int,
        final_company_score: int,
        risk_level: str,
    ) -> dict[str, Any]:
        positive_factors: list[str] = []
        negative_factors: list[str] = []

        self._append_factor(
            debt_score,
            "Долговая нагрузка находится в приемлемом диапазоне",
            "Долговая нагрузка находится на повышенном уровне",
            positive_factors,
            negative_factors,
        )
        self._append_factor(
            profitability_score,
            "Рентабельность находится на хорошем уровне",
            "Рентабельность находится на слабом уровне",
            positive_factors,
            negative_factors,
        )
        self._append_factor(
            liquidity_score,
            "Ликвидность покрывает краткосрочный долг",
            "Ликвидность относительно краткосрочного долга слабая",
            positive_factors,
            negative_factors,
        )
        self._append_factor(
            cashflow_score,
            "Операционный денежный поток поддерживает долговую нагрузку",
            "Операционный денежный поток относительно общего долга слабый",
            positive_factors,
            negative_factors,
        )
        self._append_factor(
            stability_score,
            "Выручка, прибыль и денежный поток выглядят устойчиво",
            "Стабильность финансовых результатов требует внимания",
            positive_factors,
            negative_factors,
        )

        missing_data = [
            label for key, label in RATIO_LABELS.items() if ratios.get(key) is None
        ]
        summary = self._summary_for_risk(risk_level, final_company_score)

        return {
            "summary": summary,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "missing_data": missing_data,
            "ratios": self._ratios_for_json(ratios),
        }

    @staticmethod
    def _append_factor(
        score: int,
        positive_text: str,
        negative_text: str,
        positive_factors: list[str],
        negative_factors: list[str],
    ) -> None:
        if score >= 75:
            positive_factors.append(positive_text)
        elif score <= 45:
            negative_factors.append(negative_text)

    @staticmethod
    def _summary_for_risk(risk_level: str, final_company_score: int) -> str:
        if risk_level == "insufficient_data":
            return (
                "Для полной оценки финансового состояния эмитента не хватает данных."
            )
        if risk_level == "low":
            return (
                "Финансовое состояние эмитента оценивается как устойчивое. "
                "Ключевые финансовые показатели находятся на сильном уровне."
            )
        if risk_level == "medium":
            return (
                "Финансовое состояние эмитента оценивается как нормальное. "
                "Часть показателей требует дополнительного анализа."
            )
        if risk_level == "high":
            return (
                "Финансовое состояние эмитента содержит повышенные риски. "
                "Отдельные финансовые коэффициенты находятся на слабом уровне."
            )
        return (
            "Финансовое состояние эмитента содержит критические риски. "
            f"Итоговый информационный скоринг составляет {final_company_score}."
        )

    @classmethod
    def _ratios_for_json(
        cls, ratios: dict[str, Decimal | None]
    ) -> dict[str, float | None]:
        return {
            key: cls._decimal_for_json(value)
            for key, value in ratios.items()
        }

    @staticmethod
    def _decimal_for_json(value: Decimal | None) -> float | None:
        if value is None:
            return None
        rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return float(rounded)

    @staticmethod
    def _round_score(value: Decimal) -> int:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

