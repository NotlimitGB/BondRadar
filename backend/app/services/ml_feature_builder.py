from __future__ import annotations

from decimal import Decimal
from math import nan
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot


BASELINE_FEATURES = [
    "bond_score",
    "company_score",
    "yield_to_maturity",
    "duration_years",
    "liquidity_score",
    "volume",
    "spread_to_ofz",
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
    "cash_to_short_term_debt",
    "ocf_to_total_debt",
    "net_profit_margin",
    "days_to_maturity",
    "has_offer",
    "has_amortization",
    "missing_data_count",
]

FINANCIAL_REPORT_FEATURES = [
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
    "cash_to_short_term_debt",
    "ocf_to_total_debt",
    "net_profit_margin",
    "missing_data_count",
]

CREDIT_RISK_FEATURES = [
    "credit_health_score",
    "credit_status_encoded",
    "company_credit_risk_level_encoded",
    "data_quality_level_encoded",
    "assessment_score",
    "required_risk_premium",
    "decision_status_encoded",
    "bond_risk_level_encoded",
]

RETURN_METHODS = {"price", "total_return", "risk_adjusted"}

CREDIT_STATUS_ENCODING = {
    "credit_stable": 4,
    "credit_watchlist": 3,
    "credit_stressed": 2,
    "credit_distressed": 1,
    "insufficient_data": 0,
}

RISK_LEVEL_ENCODING = {
    "low": 4,
    "medium": 3,
    "high": 2,
    "critical": 1,
    "unknown": 0,
}

DATA_QUALITY_ENCODING = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "insufficient": 0,
}

DECISION_STATUS_ENCODING = {
    "eligible_for_analysis": 3,
    "watchlist": 2,
    "blocked_by_risk": 1,
    "insufficient_data": 0,
}


class MLFeatureBuilder:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def feature_names(*, include_credit_risk_features: bool) -> list[str]:
        if include_credit_risk_features:
            return [*BASELINE_FEATURES, *CREDIT_RISK_FEATURES]
        return list(BASELINE_FEATURES)

    @staticmethod
    def feature_groups(*, include_credit_risk_features: bool) -> dict[str, list[str]]:
        groups = {
            "baseline": list(BASELINE_FEATURES),
            "financial_report": list(FINANCIAL_REPORT_FEATURES),
            "credit_risk": (
                list(CREDIT_RISK_FEATURES) if include_credit_risk_features else []
            ),
        }
        return groups

    def vector(
        self,
        feature: BondFeatureSnapshot,
        feature_names: list[str],
    ) -> list[float]:
        return [
            self._model_value(self.value(feature, feature_name))
            for feature_name in feature_names
        ]

    def payload(
        self,
        feature: BondFeatureSnapshot,
        feature_names: list[str],
    ) -> dict[str, float | None]:
        return {
            feature_name: self._json_value(self.value(feature, feature_name))
            for feature_name in feature_names
        }

    def value(self, feature: BondFeatureSnapshot, feature_name: str) -> Any:
        if feature_name in BASELINE_FEATURES:
            return getattr(feature, feature_name)

        health = None
        assessment = None
        if feature_name in {
            "credit_health_score",
            "credit_status_encoded",
            "company_credit_risk_level_encoded",
            "data_quality_level_encoded",
        }:
            health = self.latest_company_credit_health(feature)
        if feature_name in {
            "assessment_score",
            "required_risk_premium",
            "decision_status_encoded",
            "bond_risk_level_encoded",
        }:
            assessment = self.latest_bond_risk_assessment(feature)

        if feature_name == "credit_health_score":
            return None if health is None else health.credit_health_score
        if feature_name == "credit_status_encoded":
            return (
                None
                if health is None
                else CREDIT_STATUS_ENCODING.get(health.credit_status)
            )
        if feature_name == "company_credit_risk_level_encoded":
            return None if health is None else RISK_LEVEL_ENCODING.get(health.risk_level)
        if feature_name == "data_quality_level_encoded":
            return (
                None
                if health is None
                else DATA_QUALITY_ENCODING.get(health.data_quality_level)
            )
        if feature_name == "assessment_score":
            return None if assessment is None else assessment.assessment_score
        if feature_name == "required_risk_premium":
            return None if assessment is None else assessment.required_risk_premium
        if feature_name == "decision_status_encoded":
            return (
                None
                if assessment is None
                else DECISION_STATUS_ENCODING.get(assessment.decision_status)
            )
        if feature_name == "bond_risk_level_encoded":
            return (
                None
                if assessment is None
                else RISK_LEVEL_ENCODING.get(assessment.risk_level)
            )
        return None

    def latest_company_credit_health(
        self,
        feature: BondFeatureSnapshot,
    ) -> CompanyCreditHealthSnapshot | None:
        return self.db.execute(
            select(CompanyCreditHealthSnapshot)
            .where(
                CompanyCreditHealthSnapshot.company_id == feature.company_id,
                CompanyCreditHealthSnapshot.as_of_date <= feature.as_of_date,
            )
            .order_by(
                CompanyCreditHealthSnapshot.as_of_date.desc(),
                CompanyCreditHealthSnapshot.created_at.desc(),
                CompanyCreditHealthSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def latest_bond_risk_assessment(
        self,
        feature: BondFeatureSnapshot,
    ) -> BondRiskAssessment | None:
        return self.db.execute(
            select(BondRiskAssessment)
            .where(
                BondRiskAssessment.bond_id == feature.bond_id,
                BondRiskAssessment.as_of_date <= feature.as_of_date,
            )
            .order_by(
                BondRiskAssessment.as_of_date.desc(),
                BondRiskAssessment.created_at.desc(),
                BondRiskAssessment.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _model_value(value: Any) -> float:
        if value is None:
            return nan
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    @staticmethod
    def _json_value(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
