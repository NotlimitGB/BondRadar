from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_score import BondScore
from app.models.company_score import CompanyScore
from app.models.financial_report import FinancialReport
from app.services.financial_ratios import FinancialRatiosService
from app.services.market_snapshot_service import MarketSnapshotService


@dataclass(frozen=True)
class FeatureBuildOutcome:
    snapshot: BondFeatureSnapshot
    action: str


class FeatureSnapshotService:
    IMPORTANT_FEATURES = (
        "yield_to_maturity",
        "duration_years",
        "liquidity_score",
        "volume",
        "company_score",
        "bond_score",
        "net_debt_to_ebitda",
        "interest_coverage",
    )

    def __init__(
        self,
        db: Session,
        market_service: MarketSnapshotService | None = None,
        ratios_service: FinancialRatiosService | None = None,
    ) -> None:
        self.db = db
        self.market_service = market_service or MarketSnapshotService(db)
        self.ratios_service = ratios_service or FinancialRatiosService()

    def build_for_bond_date(
        self,
        bond_id: int,
        as_of_date: date,
        *,
        rebuild_existing: bool = False,
    ) -> FeatureBuildOutcome:
        bond = self.db.get(Bond, bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )

        existing = self.db.execute(
            select(BondFeatureSnapshot).where(
                BondFeatureSnapshot.bond_id == bond_id,
                BondFeatureSnapshot.as_of_date == as_of_date,
            )
        ).scalar_one_or_none()
        if existing is not None and not rebuild_existing:
            return FeatureBuildOutcome(snapshot=existing, action="skipped")

        payload = self._build_payload(bond, as_of_date)
        if existing is None:
            snapshot = BondFeatureSnapshot(**payload)
            self.db.add(snapshot)
            self.db.flush()
            return FeatureBuildOutcome(snapshot=snapshot, action="created")

        for field, value in payload.items():
            setattr(existing, field, value)
        self.db.add(existing)
        self.db.flush()
        return FeatureBuildOutcome(snapshot=existing, action="updated")

    def _build_payload(self, bond: Bond, as_of_date: date) -> dict[str, Any]:
        cutoff = datetime.combine(as_of_date, time.max)
        market_snapshot = self.market_service.get_latest_for_bond(bond.id, as_of_date)
        bond_score = self._latest_bond_score(bond.id, cutoff)
        company_score = self._latest_company_score(bond.company_id, cutoff)
        report, leakage_warning = self._latest_financial_report(
            bond.company_id,
            as_of_date,
            cutoff,
        )

        ratios = self.ratios_service.calculate(report) if report is not None else {}
        bond_score_value = self._score_value(bond_score, "final_bond_score")
        company_score_value = self._score_value(company_score, "final_company_score")

        yield_to_maturity = self._prefer_market(
            market_snapshot.yield_to_maturity if market_snapshot else None,
            bond.yield_to_maturity,
        )
        duration_years = self._prefer_market(
            market_snapshot.duration_years if market_snapshot else None,
            bond.duration_years,
        )
        liquidity_score = (
            market_snapshot.liquidity_score
            if market_snapshot is not None
            and market_snapshot.liquidity_score is not None
            else bond.liquidity_score
        )
        volume = self._prefer_market(
            market_snapshot.volume if market_snapshot else None,
            bond.volume,
        )
        spread_to_ofz = market_snapshot.spread_to_ofz if market_snapshot else None

        feature_values = {
            "yield_to_maturity": yield_to_maturity,
            "duration_years": duration_years,
            "liquidity_score": liquidity_score,
            "volume": volume,
            "company_score": company_score_value,
            "bond_score": bond_score_value,
            "net_debt_to_ebitda": ratios.get("net_debt_to_ebitda"),
            "interest_coverage": ratios.get("interest_coverage"),
        }
        missing_data = [
            feature for feature in self.IMPORTANT_FEATURES if feature_values[feature] is None
        ]
        features_json: dict[str, Any] = {
            "missing_data": missing_data,
            "source_ids": {
                "market_snapshot_id": market_snapshot.id if market_snapshot else None,
                "bond_score_id": bond_score.id if bond_score else None,
                "company_score_id": company_score.id if company_score else None,
                "financial_report_id": report.id if report else None,
            },
        }
        if leakage_warning is not None:
            features_json["leakage_warning"] = leakage_warning

        return {
            "bond_id": bond.id,
            "company_id": bond.company_id,
            "as_of_date": as_of_date,
            "market_snapshot_id": market_snapshot.id if market_snapshot else None,
            "bond_score_id": bond_score.id if bond_score else None,
            "company_score_id": company_score.id if company_score else None,
            "financial_report_id": report.id if report else None,
            "bond_score": bond_score_value,
            "company_score": company_score_value,
            "yield_to_maturity": yield_to_maturity,
            "duration_years": duration_years,
            "liquidity_score": liquidity_score,
            "volume": volume,
            "spread_to_ofz": spread_to_ofz,
            "net_debt_to_ebitda": ratios.get("net_debt_to_ebitda"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "interest_coverage": ratios.get("interest_coverage"),
            "cash_to_short_term_debt": ratios.get("cash_to_short_term_debt"),
            "ocf_to_total_debt": ratios.get("operating_cash_flow_to_total_debt"),
            "net_profit_margin": ratios.get("net_profit_margin"),
            "days_to_maturity": self._days_to_maturity(bond, as_of_date),
            "has_offer": bond.offer_date is not None,
            "has_amortization": bond.amortization,
            "missing_data_count": len(missing_data),
            "features_json": features_json,
        }

    def _latest_bond_score(
        self, bond_id: int, cutoff: datetime
    ) -> BondScore | None:
        return self.db.execute(
            select(BondScore)
            .where(BondScore.bond_id == bond_id, BondScore.created_at <= cutoff)
            .order_by(BondScore.created_at.desc(), BondScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _latest_company_score(
        self, company_id: int, cutoff: datetime
    ) -> CompanyScore | None:
        return self.db.execute(
            select(CompanyScore)
            .where(
                CompanyScore.company_id == company_id,
                CompanyScore.created_at <= cutoff,
            )
            .order_by(CompanyScore.created_at.desc(), CompanyScore.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _latest_financial_report(
        self,
        company_id: int,
        as_of_date: date,
        cutoff: datetime,
    ) -> tuple[FinancialReport | None, str | None]:
        period_priority = self._period_priority()
        report = self.db.execute(
            select(FinancialReport)
            .where(
                FinancialReport.company_id == company_id,
                FinancialReport.created_at <= cutoff,
            )
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.created_at.desc(),
                FinancialReport.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if report is not None:
            return report, None

        current_quarter = ((as_of_date.month - 1) // 3) + 1
        report = self.db.execute(
            select(FinancialReport)
            .where(
                FinancialReport.company_id == company_id,
                or_(
                    FinancialReport.period_year < as_of_date.year,
                    and_(
                        FinancialReport.period_year == as_of_date.year,
                        FinancialReport.period_quarter != 0,
                        FinancialReport.period_quarter <= current_quarter,
                    ),
                ),
            )
            .order_by(
                FinancialReport.period_year.desc(),
                period_priority.desc(),
                FinancialReport.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if report is None:
            return None, None
        return (
            report,
            "Financial report publication date is unknown; period-based fallback was used.",
        )

    @staticmethod
    def _period_priority():
        return case(
            (FinancialReport.period_quarter == 0, 5),
            (FinancialReport.period_quarter == 4, 4),
            (FinancialReport.period_quarter == 3, 3),
            (FinancialReport.period_quarter == 2, 2),
            (FinancialReport.period_quarter == 1, 1),
            else_=0,
        )

    @staticmethod
    def _score_value(
        score: BondScore | CompanyScore | None,
        final_field: str,
    ) -> Decimal | None:
        if score is None:
            return None
        final_value = getattr(score, final_field)
        if final_value is not None:
            return Decimal(str(final_value))
        return Decimal(score.score)

    @staticmethod
    def _prefer_market(
        market_value: Decimal | int | None,
        bond_value: Decimal | int | None,
    ):
        return market_value if market_value is not None else bond_value

    @staticmethod
    def _days_to_maturity(bond: Bond, as_of_date: date) -> int | None:
        if bond.maturity_date is None:
            return None
        return (bond.maturity_date - as_of_date).days
