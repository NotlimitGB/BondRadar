from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.company_score import CompanyScore
from app.services.company_credit_health_service import CompanyCreditHealthService


class BondRiskAssessmentService:
    HIGH_YIELD_WARNING = "High yield may reflect elevated credit/default risk"

    def __init__(
        self,
        db: Session,
        company_health_service: CompanyCreditHealthService | None = None,
    ) -> None:
        self.db = db
        self.company_health_service = company_health_service or CompanyCreditHealthService(db)

    def assess_bond(
        self,
        bond_id: int,
        *,
        as_of_date: date | None = None,
        recalculate_company_health: bool = True,
    ) -> BondRiskAssessment:
        target_date = as_of_date or date.today()
        bond = self.db.get(Bond, bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )
        company = self.db.get(Company, bond.company_id)
        if company is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found",
            )

        credit_health = self._company_credit_health(
            company.id,
            target_date,
            recalculate_company_health=recalculate_company_health,
        )
        bond_score = self._latest_bond_score(bond.id, target_date)
        market_snapshot = self._latest_market_snapshot(bond.id, target_date)
        company_score_value = self._company_score_from_health(credit_health)
        payload = self._build_payload(
            bond=bond,
            credit_health=credit_health,
            bond_score=bond_score,
            market_snapshot=market_snapshot,
            company_score_value=company_score_value,
            as_of_date=target_date,
        )
        assessment = self._upsert_assessment(bond.id, target_date, payload)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            assessment = self._upsert_assessment(bond.id, target_date, payload)
            self.db.commit()
        self.db.refresh(assessment)
        return assessment

    def get_latest(
        self,
        bond_id: int,
        *,
        as_of_date: date | None = None,
    ) -> BondRiskAssessment:
        if self.db.get(Bond, bond_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )
        stmt = select(BondRiskAssessment).where(BondRiskAssessment.bond_id == bond_id)
        if as_of_date is not None:
            stmt = stmt.where(BondRiskAssessment.as_of_date <= as_of_date)
        assessment = self.db.execute(
            stmt.order_by(
                BondRiskAssessment.as_of_date.desc(),
                BondRiskAssessment.created_at.desc(),
                BondRiskAssessment.id.desc(),
            ).limit(1)
        ).scalar_one_or_none()
        if assessment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond risk assessment not found",
            )
        return assessment

    def recalculate_all(self) -> dict[str, Any]:
        companies = list(self.db.execute(select(Company).order_by(Company.id)).scalars())
        bonds = list(self.db.execute(select(Bond).order_by(Bond.id)).scalars())
        calculated = 0
        errors: list[dict[str, Any]] = []

        for company in companies:
            try:
                self.company_health_service.calculate_for_company(company.id)
                calculated += 1
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    {
                        "entity_type": "company",
                        "entity_id": company.id,
                        "message": self._error_detail(exc),
                    }
                )

        for bond in bonds:
            try:
                self.assess_bond(bond.id, recalculate_company_health=False)
                calculated += 1
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    {
                        "entity_type": "bond",
                        "entity_id": bond.id,
                        "message": self._error_detail(exc),
                    }
                )

        return {
            "total": len(companies) + len(bonds),
            "calculated": calculated,
            "failed": len(errors),
            "errors": errors,
        }

    def _company_credit_health(
        self,
        company_id: int,
        as_of_date: date,
        *,
        recalculate_company_health: bool,
    ) -> CompanyCreditHealthSnapshot:
        if recalculate_company_health:
            return self.company_health_service.calculate_for_company(
                company_id,
                as_of_date=as_of_date,
            )
        try:
            return self.company_health_service.get_latest(
                company_id,
                as_of_date=as_of_date,
            )
        except HTTPException:
            return self.company_health_service.calculate_for_company(
                company_id,
                as_of_date=as_of_date,
            )

    def _latest_bond_score(self, bond_id: int, as_of_date: date) -> BondScore | None:
        end_of_day = datetime.combine(as_of_date, time.max)
        return self.db.execute(
            select(BondScore)
            .where(BondScore.bond_id == bond_id, BondScore.created_at <= end_of_day)
            .order_by(BondScore.created_at.desc(), BondScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _latest_market_snapshot(
        self,
        bond_id: int,
        as_of_date: date,
    ) -> BondMarketSnapshot | None:
        source_priority = case((BondMarketSnapshot.source == "moex", 0), else_=1)
        return self.db.execute(
            select(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id == bond_id,
                BondMarketSnapshot.trade_date <= as_of_date,
            )
            .order_by(
                BondMarketSnapshot.trade_date.desc(),
                source_priority.asc(),
                BondMarketSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _build_payload(
        self,
        *,
        bond: Bond,
        credit_health: CompanyCreditHealthSnapshot,
        bond_score: BondScore | None,
        market_snapshot: BondMarketSnapshot | None,
        company_score_value: Decimal | None,
        as_of_date: date,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        blocking_reasons: list[str] = []
        positive_factors: list[str] = []
        negative_factors: list[str] = []
        missing_data: list[str] = []
        gates: dict[str, str] = {}

        yield_to_maturity = self._prefer_snapshot(
            market_snapshot.yield_to_maturity if market_snapshot is not None else None,
            bond.yield_to_maturity,
        )
        duration_years = self._prefer_snapshot(
            market_snapshot.duration_years if market_snapshot is not None else None,
            bond.duration_years,
        )
        volume = self._prefer_snapshot(
            market_snapshot.volume if market_snapshot is not None else None,
            bond.volume,
        )
        liquidity_score = self._prefer_snapshot(
            market_snapshot.liquidity_score if market_snapshot is not None else None,
            bond.liquidity_score,
        )
        bond_score_value = self._bond_score_value(bond_score)
        if bond_score is None:
            missing_data.append("Bond score is missing")
        if market_snapshot is None:
            missing_data.append("Market snapshot is missing")

        self._credit_gate(credit_health, gates, warnings, blocking_reasons)
        self._liquidity_gate(liquidity_score, volume, gates, warnings, blocking_reasons, missing_data)
        self._data_quality_gate(credit_health, gates, warnings, blocking_reasons)
        self._duration_gate(duration_years, gates, warnings, blocking_reasons, missing_data)
        self._structure_gate(bond, credit_health, gates, warnings, blocking_reasons)
        self._high_yield_gate(
            yield_to_maturity,
            credit_health,
            gates,
            warnings,
            blocking_reasons,
        )

        required_risk_premium = self._required_risk_premium(
            credit_health=credit_health,
            gates=gates,
            duration_years=duration_years,
            bond=bond,
        )
        assessment_score = self._assessment_score(
            credit_health=credit_health,
            bond_score_value=bond_score_value,
            liquidity_score=liquidity_score,
            yield_to_maturity=yield_to_maturity,
            duration_years=duration_years,
            warnings=warnings,
            blocking_reasons=blocking_reasons,
            missing_data=missing_data,
        )
        decision_status = self._decision_status(
            gates=gates,
            warnings=warnings,
            blocking_reasons=blocking_reasons,
            credit_health=credit_health,
        )
        risk_level = self._assessment_risk_level(decision_status, credit_health)
        if credit_health.credit_status == "credit_stable":
            positive_factors.append("Issuer credit health is stable")
        else:
            negative_factors.append(
                f"Issuer credit status is {credit_health.credit_status}"
            )
        if liquidity_score is not None and liquidity_score >= 60:
            positive_factors.append("Liquidity gate is supportive")
        if yield_to_maturity is not None and Decimal("9") <= yield_to_maturity < Decimal("18"):
            positive_factors.append("Yield is in a moderate analysis range")
        if warnings:
            negative_factors.extend(warnings)
        if blocking_reasons:
            negative_factors.extend(blocking_reasons)

        explanation = {
            "summary": self._summary(decision_status),
            "decision_reason": self._decision_reason(
                decision_status,
                warnings,
                blocking_reasons,
            ),
            "gates": gates,
            "warnings": warnings,
            "blocking_reasons": blocking_reasons,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "missing_data": missing_data,
            "source_data": {
                "company_credit_health_id": credit_health.id,
                "bond_score_id": bond_score.id if bond_score is not None else None,
                "market_snapshot_id": (
                    market_snapshot.id if market_snapshot is not None else None
                ),
                "as_of_date": as_of_date.isoformat(),
            },
        }
        return {
            "company_id": bond.company_id,
            "company_credit_health_id": credit_health.id,
            "bond_score_id": bond_score.id if bond_score is not None else None,
            "market_snapshot_id": market_snapshot.id if market_snapshot is not None else None,
            "assessment_score": assessment_score,
            "decision_status": decision_status,
            "risk_level": risk_level,
            "required_risk_premium": required_risk_premium,
            "yield_to_maturity": yield_to_maturity,
            "coupon_rate": bond.coupon_rate,
            "duration_years": duration_years,
            "liquidity_score": liquidity_score,
            "volume": volume,
            "company_credit_status": credit_health.credit_status,
            "company_credit_health_score": credit_health.credit_health_score,
            "company_score": company_score_value,
            "bond_score": bond_score_value,
            "gates": gates,
            "warnings": warnings,
            "blocking_reasons": blocking_reasons,
            "positive_factors": positive_factors,
            "negative_factors": negative_factors,
            "missing_data": missing_data,
            "explanation": explanation,
        }

    @staticmethod
    def _credit_gate(
        credit_health: CompanyCreditHealthSnapshot,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
    ) -> None:
        status_value = credit_health.credit_status
        if status_value == "credit_distressed":
            gates["credit_gate"] = "blocked"
            blocking_reasons.append(f"Issuer credit status is {status_value}")
        elif status_value == "insufficient_data":
            gates["credit_gate"] = "warning"
            warnings.append("Issuer credit status has insufficient data")
        elif status_value == "credit_stressed" and credit_health.credit_health_score < 50:
            gates["credit_gate"] = "blocked"
            blocking_reasons.append("Issuer credit health is stressed")
        elif status_value in {"credit_stressed", "credit_watchlist"}:
            gates["credit_gate"] = "warning"
            warnings.append(f"Issuer credit status is {status_value}")
        else:
            gates["credit_gate"] = "passed"

    @staticmethod
    def _liquidity_gate(
        liquidity_score: int | None,
        volume: Decimal | None,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
        missing_data: list[str],
    ) -> None:
        if liquidity_score is None and volume is None:
            gates["liquidity_gate"] = "warning"
            missing_data.append("Liquidity data is missing")
            warnings.append("Liquidity data is missing")
            return
        if liquidity_score is not None:
            if liquidity_score < 40:
                gates["liquidity_gate"] = "blocked"
                blocking_reasons.append("Liquidity score is below 40")
            elif liquidity_score < 60:
                gates["liquidity_gate"] = "warning"
                warnings.append("Liquidity score is below 60")
            else:
                gates["liquidity_gate"] = "passed"
            return
        if volume is not None and volume < Decimal("100000"):
            gates["liquidity_gate"] = "blocked"
            blocking_reasons.append("Low trading volume may make execution difficult")
        elif volume is not None and volume < Decimal("1000000"):
            gates["liquidity_gate"] = "warning"
            warnings.append("Low trading volume may make execution difficult")
        else:
            gates["liquidity_gate"] = "passed"

    @staticmethod
    def _data_quality_gate(
        credit_health: CompanyCreditHealthSnapshot,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
    ) -> None:
        if credit_health.data_quality_level == "insufficient":
            gates["data_quality_gate"] = "blocked"
            blocking_reasons.append("Insufficient credit health data")
        elif credit_health.data_quality_level == "low":
            gates["data_quality_gate"] = "warning"
            warnings.append("Credit health data quality is low")
        else:
            gates["data_quality_gate"] = "passed"

    @staticmethod
    def _duration_gate(
        duration_years: Decimal | None,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
        missing_data: list[str],
    ) -> None:
        if duration_years is None:
            gates["duration_gate"] = "warning"
            missing_data.append("Duration is missing")
            warnings.append("Duration is missing")
        elif duration_years > Decimal("12"):
            gates["duration_gate"] = "blocked"
            blocking_reasons.append("Duration is above 12 years")
        elif duration_years > Decimal("8"):
            gates["duration_gate"] = "warning"
            warnings.append("Duration is above 8 years")
        else:
            gates["duration_gate"] = "passed"

    @staticmethod
    def _structure_gate(
        bond: Bond,
        credit_health: CompanyCreditHealthSnapshot,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
    ) -> None:
        gate_result = "passed"
        weak_credit = credit_health.credit_status in {
            "credit_stressed",
            "credit_distressed",
        }
        if bond.is_subordinated:
            warnings.append("Bond is subordinated")
            gate_result = "warning"
            if weak_credit:
                blocking_reasons.append("Subordinated bond with weak issuer credit")
                gate_result = "blocked"
        if bond.is_perpetual:
            warnings.append("Bond is perpetual")
            gate_result = "warning"
            if weak_credit:
                blocking_reasons.append("Perpetual bond with weak issuer credit")
                gate_result = "blocked"
        if bond.amortization is True:
            warnings.append("Bond has amortization schedule")
            if gate_result == "passed":
                gate_result = "warning"
        if (
            bond.offer_date is not None
            and bond.maturity_date is not None
            and bond.offer_date < bond.maturity_date
        ):
            warnings.append("Bond has offer date before maturity")
            if gate_result == "passed":
                gate_result = "warning"
        gates["structure_gate"] = gate_result

    def _high_yield_gate(
        self,
        yield_to_maturity: Decimal | None,
        credit_health: CompanyCreditHealthSnapshot,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
    ) -> None:
        if yield_to_maturity is None:
            gates["high_yield_gate"] = "warning"
            warnings.append("Yield to maturity is missing")
            return
        if yield_to_maturity < Decimal("18"):
            gates["high_yield_gate"] = "passed"
            return
        warnings.append(self.HIGH_YIELD_WARNING)
        if (
            credit_health.credit_status in {"credit_stressed", "credit_distressed"}
            or credit_health.credit_health_score < 60
        ):
            gates["high_yield_gate"] = "blocked"
            blocking_reasons.append("High yield is combined with weak issuer credit")
        else:
            gates["high_yield_gate"] = "warning"

    @staticmethod
    def _required_risk_premium(
        *,
        credit_health: CompanyCreditHealthSnapshot,
        gates: dict[str, str],
        duration_years: Decimal | None,
        bond: Bond,
    ) -> Decimal:
        premium = {
            "credit_stable": Decimal("0.005"),
            "credit_watchlist": Decimal("0.015"),
            "credit_stressed": Decimal("0.030"),
            "credit_distressed": Decimal("0.050"),
            "insufficient_data": Decimal("0.040"),
        }[credit_health.credit_status]
        if gates.get("liquidity_gate") == "warning":
            premium += Decimal("0.005")
        if gates.get("liquidity_gate") == "blocked":
            premium += Decimal("0.020")
        if duration_years is not None and duration_years > Decimal("8"):
            premium += Decimal("0.010")
        if bond.is_subordinated:
            premium += Decimal("0.010")
        if bond.is_perpetual:
            premium += Decimal("0.020")
        return premium

    @classmethod
    def _assessment_score(
        cls,
        *,
        credit_health: CompanyCreditHealthSnapshot,
        bond_score_value: Decimal | None,
        liquidity_score: int | None,
        yield_to_maturity: Decimal | None,
        duration_years: Decimal | None,
        warnings: list[str],
        blocking_reasons: list[str],
        missing_data: list[str],
    ) -> int:
        score = Decimal("50")
        score += Decimal(credit_health.credit_health_score) * Decimal("0.25")
        if bond_score_value is not None:
            score += Decimal(bond_score_value) * Decimal("0.15")
        if liquidity_score is not None:
            score += Decimal(liquidity_score) * Decimal("0.10")
        score += cls._yield_contribution(yield_to_maturity, credit_health)
        if duration_years is not None and duration_years <= Decimal("5"):
            score += Decimal("5")
        score -= Decimal(len(warnings) * 5)
        score -= Decimal(len(blocking_reasons) * 25)
        if missing_data:
            score -= Decimal(min(20, len(missing_data) * 5))
        return cls._clamp_score(score)

    @staticmethod
    def _yield_contribution(
        yield_to_maturity: Decimal | None,
        credit_health: CompanyCreditHealthSnapshot,
    ) -> Decimal:
        if yield_to_maturity is None:
            return Decimal("0")
        if Decimal("9") <= yield_to_maturity < Decimal("15"):
            return Decimal("8")
        if Decimal("15") <= yield_to_maturity < Decimal("18"):
            return Decimal("5")
        if yield_to_maturity >= Decimal("18"):
            if credit_health.credit_status in {"credit_stable", "credit_watchlist"}:
                return Decimal("3")
            return Decimal("-10")
        return Decimal("1")

    @staticmethod
    def _decision_status(
        *,
        gates: dict[str, str],
        warnings: list[str],
        blocking_reasons: list[str],
        credit_health: CompanyCreditHealthSnapshot,
    ) -> str:
        data_only_block = (
            gates.get("data_quality_gate") == "blocked"
            and len(blocking_reasons) == 1
            and blocking_reasons[0] == "Insufficient credit health data"
        )
        if blocking_reasons and not data_only_block:
            return "blocked_by_risk"
        if data_only_block or credit_health.credit_status == "insufficient_data":
            return "insufficient_data"
        if warnings or credit_health.risk_level in {"high", "critical"}:
            return "watchlist"
        return "eligible_for_analysis"

    @staticmethod
    def _assessment_risk_level(
        decision_status: str,
        credit_health: CompanyCreditHealthSnapshot,
    ) -> str:
        if decision_status == "blocked_by_risk":
            return "critical"
        if decision_status == "insufficient_data":
            return "unknown"
        if decision_status == "watchlist":
            return "high" if credit_health.risk_level == "high" else "medium"
        return "low"

    @staticmethod
    def _summary(decision_status: str) -> str:
        return {
            "eligible_for_analysis": "Bond passed risk gates for further analysis.",
            "watchlist": "Bond has warnings and should remain on analytical watchlist.",
            "blocked_by_risk": "Bond is blocked by risk gates.",
            "insufficient_data": "Bond assessment has insufficient data.",
        }[decision_status]

    @staticmethod
    def _decision_reason(
        decision_status: str,
        warnings: list[str],
        blocking_reasons: list[str],
    ) -> str:
        if blocking_reasons:
            return "Blocking risk gates: " + "; ".join(blocking_reasons)
        if warnings:
            return "Warnings are present: " + "; ".join(warnings)
        return f"Decision status is {decision_status}."

    def _upsert_assessment(
        self,
        bond_id: int,
        as_of_date: date,
        payload: dict[str, Any],
    ) -> BondRiskAssessment:
        assessment = self.db.execute(
            select(BondRiskAssessment).where(
                BondRiskAssessment.bond_id == bond_id,
                BondRiskAssessment.as_of_date == as_of_date,
            )
        ).scalar_one_or_none()
        data = {"bond_id": bond_id, "as_of_date": as_of_date, **payload}
        if assessment is None:
            assessment = BondRiskAssessment(**data)
            self.db.add(assessment)
            return assessment
        for field, value in data.items():
            setattr(assessment, field, value)
        self.db.add(assessment)
        return assessment

    @staticmethod
    def _bond_score_value(bond_score: BondScore | None) -> Decimal | None:
        if bond_score is None:
            return None
        if bond_score.final_bond_score is not None:
            return Decimal(bond_score.final_bond_score)
        return Decimal(bond_score.score)

    def _company_score_from_health(
        self,
        credit_health: CompanyCreditHealthSnapshot,
    ) -> Decimal | None:
        if credit_health.company_score_id is None:
            return None
        company_score = self.db.get(CompanyScore, credit_health.company_score_id)
        if company_score is None:
            return None
        if company_score.final_company_score is not None:
            return Decimal(company_score.final_company_score)
        return Decimal(company_score.score)

    @staticmethod
    def _prefer_snapshot(snapshot_value, static_value):
        return snapshot_value if snapshot_value is not None else static_value

    @staticmethod
    def _clamp_score(value: Decimal) -> int:
        value = min(Decimal("100"), max(Decimal("0"), value))
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)
