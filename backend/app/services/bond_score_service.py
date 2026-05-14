from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal


KEY_FACTOR_WEIGHTS = {
    "yield_score": Decimal("0.25"),
    "company_score": Decimal("0.30"),
    "liquidity_score": Decimal("0.20"),
    "duration_score": Decimal("0.15"),
    "spread_score": Decimal("0.10"),
}


class BondScoreService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def calculate_for_bond(self, bond_id: int) -> BondScore:
        bond = self.db.get(Bond, bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )

        if bond.company_id is None or self.db.get(Company, bond.company_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        company_score = self.get_latest_company_score(bond.company_id)
        payload = self.calculate_scores(bond, company_score)
        score = self._upsert_score(bond, company_score, payload)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            score = self._upsert_score(bond, company_score, payload)
            self.db.commit()
        self.db.refresh(score)
        return score

    def get_latest_score(self, bond_id: int) -> BondScore:
        bond = self.db.get(Bond, bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )
        score = self.db.execute(
            select(BondScore)
            .where(BondScore.bond_id == bond_id)
            .order_by(BondScore.created_at.desc(), BondScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if score is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond score not found",
            )
        return score

    def recalculate_all(self) -> dict[str, Any]:
        bonds = list(self.db.execute(select(Bond).order_by(Bond.id)).scalars())
        calculated = 0
        errors: list[dict[str, Any]] = []

        for bond in bonds:
            try:
                self.calculate_for_bond(bond.id)
                calculated += 1
            except HTTPException as exc:
                self.db.rollback()
                errors.append({"bond_id": bond.id, "error": str(exc.detail)})
            except Exception as exc:
                self.db.rollback()
                errors.append({"bond_id": bond.id, "error": str(exc)})

        return {
            "total_bonds": len(bonds),
            "calculated": calculated,
            "failed": len(errors),
            "errors": errors,
        }

    def get_latest_company_score(self, company_id: int) -> CompanyScore | None:
        return self.db.execute(
            select(CompanyScore)
            .where(CompanyScore.company_id == company_id)
            .order_by(CompanyScore.created_at.desc(), CompanyScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def calculate_scores(
        self, bond: Bond, company_score: CompanyScore | None
    ) -> dict[str, Any]:
        missing_data: list[str] = []
        risk_warnings: list[str] = []

        yield_score = self._score_yield(bond.yield_to_maturity, missing_data)
        duration_score = self._score_duration(bond.duration_years, missing_data)
        liquidity_score = self._score_liquidity(
            bond.liquidity_score,
            bond.volume,
            missing_data,
        )
        company_score_value = self._company_score_value(company_score, missing_data)
        spread_score = self._score_spread(missing_data)

        key_factors = {
            "yield_score": yield_score,
            "company_score": company_score_value,
            "liquidity_score": liquidity_score,
            "duration_score": duration_score,
            "spread_score": spread_score,
        }
        available_factor_count = sum(
            value is not None for value in key_factors.values()
        )
        risk_penalty = self._risk_penalty(
            bond=bond,
            company_score_value=company_score_value,
            duration_score=duration_score,
            liquidity_score=liquidity_score,
            missing_factor_count=5 - available_factor_count,
            risk_warnings=risk_warnings,
        )
        if (
            bond.yield_to_maturity is not None
            and bond.yield_to_maturity >= Decimal("15")
            and company_score_value is not None
            and company_score_value < Decimal("60")
        ):
            risk_warnings.append(
                "High yield may reflect elevated issuer credit risk"
            )

        final_bond_score = self._final_score(key_factors, risk_penalty)
        signal = self._signal(final_bond_score, available_factor_count)
        explanation = self._build_explanation(
            bond=bond,
            company_score_value=company_score_value,
            yield_score=yield_score,
            duration_score=duration_score,
            liquidity_score=liquidity_score,
            spread_score=spread_score,
            risk_penalty=risk_penalty,
            final_bond_score=final_bond_score,
            signal=signal,
            missing_data=missing_data,
            risk_warnings=risk_warnings,
        )

        return {
            "company_score_id": company_score.id if company_score is not None else None,
            "yield_score": yield_score,
            "duration_score": duration_score,
            "liquidity_score": liquidity_score,
            "spread_score": spread_score,
            "risk_penalty": risk_penalty,
            "final_bond_score": final_bond_score,
            "signal": signal,
            "explanation": explanation,
        }

    def _upsert_score(
        self,
        bond: Bond,
        company_score: CompanyScore | None,
        payload: dict[str, Any],
    ) -> BondScore:
        as_of_date = date.today()
        source = "bond_score_service"
        score = self.db.execute(
            select(BondScore).where(
                BondScore.bond_id == bond.id,
                BondScore.as_of_date == as_of_date,
                BondScore.source == source,
            )
        ).scalar_one_or_none()
        score_data = {
            "bond_id": bond.id,
            "company_score_id": company_score.id if company_score is not None else None,
            "score": Decimal(payload["final_bond_score"]),
            "signal": payload["signal"],
            "factors": payload["explanation"]["scores"],
            "summary": payload["explanation"]["summary"],
            "as_of_date": as_of_date,
            "source": source,
            **payload,
        }
        if score is None:
            score = BondScore(**score_data)
            self.db.add(score)
            return score

        for field, value in score_data.items():
            setattr(score, field, value)
        self.db.add(score)
        return score

    @staticmethod
    def _score_yield(
        yield_to_maturity: Decimal | None, missing_data: list[str]
    ) -> int | None:
        if yield_to_maturity is None:
            missing_data.append("Yield to maturity is missing")
            return None
        if yield_to_maturity >= Decimal("18"):
            return 100
        if yield_to_maturity >= Decimal("15"):
            return 85
        if yield_to_maturity >= Decimal("12"):
            return 70
        if yield_to_maturity >= Decimal("9"):
            return 50
        return 30

    @staticmethod
    def _score_duration(
        duration_years: Decimal | None, missing_data: list[str]
    ) -> int | None:
        if duration_years is None:
            missing_data.append("Duration is missing")
            return None
        if duration_years <= Decimal("1"):
            return 90
        if duration_years <= Decimal("3"):
            return 80
        if duration_years <= Decimal("5"):
            return 60
        if duration_years <= Decimal("8"):
            return 40
        return 25

    @staticmethod
    def _score_liquidity(
        existing_liquidity_score: int | None,
        volume: Decimal | None,
        missing_data: list[str],
    ) -> int | None:
        if existing_liquidity_score is not None:
            return existing_liquidity_score
        if volume is None:
            missing_data.append("Liquidity data is missing")
            return None
        if volume >= Decimal("50000000"):
            return 100
        if volume >= Decimal("10000000"):
            return 80
        if volume >= Decimal("1000000"):
            return 55
        if volume >= Decimal("100000"):
            return 35
        return 20

    @staticmethod
    def _company_score_value(
        company_score: CompanyScore | None, missing_data: list[str]
    ) -> Decimal | None:
        if company_score is None:
            missing_data.append("Company score is missing")
            return None
        if company_score.final_company_score is not None:
            return Decimal(company_score.final_company_score)
        return Decimal(company_score.score)

    @staticmethod
    def _score_spread(missing_data: list[str]) -> None:
        missing_data.append("Spread data is missing")
        return None

    @staticmethod
    def _risk_penalty(
        *,
        bond: Bond,
        company_score_value: Decimal | None,
        duration_score: int | None,
        liquidity_score: int | None,
        missing_factor_count: int,
        risk_warnings: list[str],
    ) -> int:
        risk_penalty = 0

        if company_score_value is not None:
            if company_score_value < Decimal("40"):
                risk_penalty += 25
            elif company_score_value < Decimal("60"):
                risk_penalty += 15
            elif company_score_value < Decimal("80"):
                risk_penalty += 5

        if bond.duration_years is not None:
            if bond.duration_years > Decimal("8"):
                risk_penalty += 15
                risk_warnings.append(
                    "Long duration increases sensitivity to rate changes"
                )
            elif bond.duration_years > Decimal("5"):
                risk_penalty += 10
                risk_warnings.append(
                    "Long duration increases sensitivity to rate changes"
                )
            elif bond.duration_years > Decimal("3"):
                risk_penalty += 5
                risk_warnings.append(
                    "Duration creates moderate sensitivity to rate changes"
                )

        if liquidity_score is not None:
            if liquidity_score < 40:
                risk_penalty += 15
                risk_warnings.append("Bond liquidity is limited")
            elif liquidity_score < 60:
                risk_penalty += 8
                risk_warnings.append("Bond liquidity is below average")

        if (
            bond.offer_date is not None
            and bond.maturity_date is not None
            and bond.offer_date < bond.maturity_date
        ):
            risk_penalty += 5
            risk_warnings.append("Bond has an offer date before maturity")

        if bond.amortization is True:
            risk_penalty += 3
            risk_warnings.append("Bond has amortization schedule")

        if missing_factor_count >= 4:
            risk_penalty += 20

        return risk_penalty

    @classmethod
    def _final_score(
        cls,
        key_factors: dict[str, int | Decimal | None],
        risk_penalty: int,
    ) -> int:
        weighted_sum = Decimal("0")
        available_weight_sum = Decimal("0")

        for factor, weight in KEY_FACTOR_WEIGHTS.items():
            value = key_factors[factor]
            if value is None:
                continue
            weighted_sum += Decimal(value) * weight
            available_weight_sum += weight

        if available_weight_sum == 0:
            weighted_score = Decimal("0")
        else:
            weighted_score = weighted_sum / available_weight_sum

        final_score = weighted_score - Decimal(risk_penalty)
        final_score = min(Decimal("100"), max(Decimal("0"), final_score))
        return cls._round_score(final_score)

    @staticmethod
    def _signal(final_bond_score: int, available_factor_count: int) -> str:
        if available_factor_count < 3:
            return AnalysisSignal.INSUFFICIENT_DATA.value
        if final_bond_score >= 80:
            return AnalysisSignal.INTERESTING_FOR_ANALYSIS.value
        if final_bond_score >= 60:
            return AnalysisSignal.NEUTRAL.value
        if final_bond_score >= 40:
            return AnalysisSignal.INCREASED_RISK.value
        return AnalysisSignal.HIGH_RISK.value

    def _build_explanation(
        self,
        *,
        bond: Bond,
        company_score_value: Decimal | None,
        yield_score: int | None,
        duration_score: int | None,
        liquidity_score: int | None,
        spread_score: int | None,
        risk_penalty: int,
        final_bond_score: int,
        signal: str,
        missing_data: list[str],
        risk_warnings: list[str],
    ) -> dict[str, Any]:
        positive_factors: list[str] = []
        negative_factors: list[str] = []

        if yield_score is not None:
            if yield_score >= 70:
                positive_factors.append("Yield is above the baseline range")
            elif yield_score <= 50:
                negative_factors.append("Yield is below the target analysis range")

        if company_score_value is not None:
            if company_score_value >= Decimal("80"):
                positive_factors.append("Issuer financial score is strong")
            elif company_score_value >= Decimal("60"):
                positive_factors.append("Issuer financial score is normal")
            elif company_score_value < Decimal("60"):
                negative_factors.append("Issuer financial score is weak")

        if liquidity_score is not None:
            if liquidity_score >= 80:
                positive_factors.append("Liquidity is acceptable")
            elif liquidity_score < 60:
                negative_factors.append("Liquidity is limited")

        if duration_score is not None:
            if duration_score >= 80:
                positive_factors.append("Duration is relatively short")
            elif duration_score <= 40:
                negative_factors.append("Duration is long")

        if spread_score is None:
            negative_factors.append("No spread data is available")

        return {
            "summary": self._summary(signal),
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "missing_data": missing_data,
            "risk_warnings": risk_warnings,
            "scores": {
                "yield_score": yield_score,
                "company_score": self._decimal_for_json(company_score_value),
                "liquidity_score": liquidity_score,
                "duration_score": duration_score,
                "spread_score": spread_score,
                "risk_penalty": risk_penalty,
                "final_bond_score": final_bond_score,
            },
            "source_data": {
                "yield_to_maturity": self._decimal_for_json(bond.yield_to_maturity),
                "duration_years": self._decimal_for_json(bond.duration_years),
                "volume": self._decimal_for_json(bond.volume),
                "offer_date": bond.offer_date.isoformat()
                if bond.offer_date is not None
                else None,
                "amortization": bond.amortization,
            },
        }

    @staticmethod
    def _summary(signal: str) -> str:
        if signal == AnalysisSignal.INSUFFICIENT_DATA.value:
            return (
                "There is not enough data for a full bond assessment. "
                "Issuer score, liquidity, duration, yield, or spread data may be missing."
            )
        if signal == AnalysisSignal.INTERESTING_FOR_ANALYSIS.value:
            return (
                "The bond looks interesting for additional analysis: yield, "
                "issuer score, liquidity, and duration are broadly supportive."
            )
        if signal == AnalysisSignal.NEUTRAL.value:
            return (
                "The bond looks neutral for analysis: core factors are mixed "
                "and require additional review."
            )
        if signal == AnalysisSignal.INCREASED_RISK.value:
            return (
                "The bond has increased risk: yield may be elevated, while "
                "issuer quality, liquidity, or duration require attention."
            )
        return (
            "The bond has high risk based on the available issuer, liquidity, "
            "duration, and yield factors."
        )

    @staticmethod
    def _decimal_for_json(value: Decimal | None) -> float | None:
        if value is None:
            return None
        rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return float(rounded)

    @staticmethod
    def _round_score(value: Decimal) -> int:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
