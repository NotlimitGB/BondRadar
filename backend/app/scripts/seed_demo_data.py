from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.models.ml_model_run import MLModelRun
from app.schemas.cashflow import BondCashflowEventCreate, BondTotalReturnLabelBuildRequest
from app.schemas.ml_dataset import BondMarketSnapshotCreate, DatasetBuildRequest
from app.schemas.ml_model import MLTrainRequest, MLPredictionRequest
from app.services.bond_cashflow_service import BondCashflowService
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.dataset_build_service import DatasetBuildService
from app.services.market_snapshot_service import MarketSnapshotService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.total_return_label_service import TotalReturnLabelService


DEMO_SOURCE = "demo"
DEMO_SCORE_SOURCE = "demo"
DEMO_MARKET_FROM = date(2025, 1, 1)
DEMO_MARKET_TO = date(2025, 4, 30)
DEMO_CREATED_AT = datetime(2024, 12, 31, 12, 0, 0)
DEMO_SCORE_AS_OF_DATE = date(2024, 12, 31)
DEMO_RISK_DATES = [date(2025, 1, 10), date(2025, 2, 10), date(2025, 3, 10)]
HIGH_YIELD_WARNING = "High yield may reflect elevated credit/default risk"

DEMO_COMPANY_TICKERS = [
    "DEMO_STABLE",
    "DEMO_WATCH",
    "DEMO_STRESS",
    "DEMO_NODATA",
]
DEMO_BOND_SECIDS = [
    "DEMO_STABLE_MOD",
    "DEMO_STABLE_LOW",
    "DEMO_WATCH_HIGH",
    "DEMO_STRESS_HIGH",
    "DEMO_WATCH_LIQ",
    "DEMO_STRESS_STRUCT",
]


@dataclass(frozen=True)
class DemoSeedOptions:
    with_ml: bool = False
    with_evaluation: bool = False
    horizon_days: int = 30
    date_from: date = date(2025, 1, 10)
    date_to: date = date(2025, 3, 15)
    benchmark_return: Decimal = Decimal("0.005")
    transaction_cost_rate: Decimal = Decimal("0.001")


@dataclass
class DemoSeedSummary:
    companies_created: int = 0
    companies_updated: int = 0
    bonds_created: int = 0
    bonds_updated: int = 0
    financial_reports_created: int = 0
    financial_reports_updated: int = 0
    company_scores_created: int = 0
    company_scores_updated: int = 0
    bond_scores_created: int = 0
    bond_scores_updated: int = 0
    market_snapshots_created: int = 0
    market_snapshots_updated: int = 0
    cashflow_events_created: int = 0
    cashflow_events_updated: int = 0
    company_credit_health_calculated: int = 0
    bond_risk_assessments_calculated: int = 0
    feature_snapshots_created: int = 0
    feature_snapshots_updated: int = 0
    price_labels_created: int = 0
    price_labels_updated: int = 0
    total_return_labels_created: int = 0
    total_return_labels_updated: int = 0
    total_return_labels_skipped: int = 0
    risk_adjusted_labels_created: int = 0
    risk_adjusted_labels_updated: int = 0
    risk_adjusted_labels_skipped: int = 0
    cashflow_impact_example_found: bool = False
    high_yield_weak_issuer_warning_found: bool = False
    risk_adjusted_label_distribution: dict[str, int] = field(default_factory=dict)
    ml_run_id: int | None = None
    predictions_created_or_updated: int = 0
    ml_message: str = "ML training skipped. Run with --with-ml to enable."
    evaluation: dict[str, Any] | None = None
    evaluation_message: str | None = None
    warnings: list[str] = field(default_factory=list)


COMPANIES: list[dict[str, Any]] = [
    {
        "name": "Demo Stable Retail",
        "ticker": "DEMO_STABLE",
        "sector": "Retail",
        "inn": "990100000001",
        "country": "RU",
        "credit_rating": "A",
        "signal": AnalysisSignal.NEUTRAL.value,
        "notes": "Demo issuer: stable credit profile.",
    },
    {
        "name": "Demo Watchlist Logistics",
        "ticker": "DEMO_WATCH",
        "sector": "Logistics",
        "inn": "990100000002",
        "country": "RU",
        "credit_rating": "BBB-",
        "signal": AnalysisSignal.NEUTRAL.value,
        "notes": "Demo issuer: moderate leverage and watchlist profile.",
    },
    {
        "name": "Demo Stressed Development",
        "ticker": "DEMO_STRESS",
        "sector": "Real estate",
        "inn": "990100000003",
        "country": "RU",
        "credit_rating": "B-",
        "signal": AnalysisSignal.INCREASED_RISK.value,
        "notes": "Demo issuer: weak credit profile and high-yield bonds.",
    },
    {
        "name": "Demo Insufficient Data Holding",
        "ticker": "DEMO_NODATA",
        "sector": "Holding",
        "inn": "990100000004",
        "country": "RU",
        "credit_rating": None,
        "signal": AnalysisSignal.INSUFFICIENT_DATA.value,
        "notes": "Demo issuer: intentionally sparse reporting.",
    },
]


BONDS: list[dict[str, Any]] = [
    {
        "company_ticker": "DEMO_STABLE",
        "isin": "RUDEMO000001",
        "secid": "DEMO_STABLE_MOD",
        "name": "Demo Stable Retail BO-01",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("99.000"),
        "coupon_rate": Decimal("11.500"),
        "yield_to_maturity": Decimal("11.200"),
        "duration_years": Decimal("2.100"),
        "volume": Decimal("24000000.00"),
        "maturity_date": date(2028, 8, 15),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 86,
        "signal": AnalysisSignal.NEUTRAL.value,
        "risk_notes": "Demo data: stable issuer, moderate yield.",
    },
    {
        "company_ticker": "DEMO_STABLE",
        "isin": "RUDEMO000002",
        "secid": "DEMO_STABLE_LOW",
        "name": "Demo Stable Retail BO-02",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("102.000"),
        "coupon_rate": Decimal("6.500"),
        "yield_to_maturity": Decimal("7.400"),
        "duration_years": Decimal("3.000"),
        "volume": Decimal("18000000.00"),
        "maturity_date": date(2029, 6, 10),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 80,
        "signal": AnalysisSignal.NEUTRAL.value,
        "risk_notes": "Demo data: stable issuer, low yield.",
    },
    {
        "company_ticker": "DEMO_WATCH",
        "isin": "RUDEMO000003",
        "secid": "DEMO_WATCH_HIGH",
        "name": "Demo Watchlist Logistics BO-01",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("96.000"),
        "coupon_rate": Decimal("15.000"),
        "yield_to_maturity": Decimal("15.800"),
        "duration_years": Decimal("2.600"),
        "volume": Decimal("7500000.00"),
        "maturity_date": date(2028, 12, 20),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 63,
        "signal": AnalysisSignal.NEUTRAL.value,
        "risk_notes": "Demo data: watchlist issuer, high-ish yield.",
    },
    {
        "company_ticker": "DEMO_STRESS",
        "isin": "RUDEMO000004",
        "secid": "DEMO_STRESS_HIGH",
        "name": "Demo Stressed Development BO-01",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("88.000"),
        "coupon_rate": Decimal("22.000"),
        "yield_to_maturity": Decimal("24.000"),
        "duration_years": Decimal("1.700"),
        "volume": Decimal("2800000.00"),
        "maturity_date": date(2027, 9, 25),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 52,
        "signal": AnalysisSignal.INCREASED_RISK.value,
        "risk_notes": "Demo data: stressed issuer, very high yield.",
    },
    {
        "company_ticker": "DEMO_WATCH",
        "isin": "RUDEMO000005",
        "secid": "DEMO_WATCH_LIQ",
        "name": "Demo Watchlist Logistics BO-02",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("94.000"),
        "coupon_rate": Decimal("16.000"),
        "yield_to_maturity": Decimal("16.500"),
        "duration_years": Decimal("2.200"),
        "volume": Decimal("65000.00"),
        "maturity_date": date(2028, 4, 5),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 25,
        "signal": AnalysisSignal.INCREASED_RISK.value,
        "risk_notes": "Demo data: low liquidity bond.",
    },
    {
        "company_ticker": "DEMO_STRESS",
        "isin": "RUDEMO000006",
        "secid": "DEMO_STRESS_STRUCT",
        "name": "Demo Stressed Development BO-SUB",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("86.000"),
        "coupon_rate": Decimal("24.000"),
        "yield_to_maturity": Decimal("23.000"),
        "duration_years": Decimal("4.800"),
        "volume": Decimal("950000.00"),
        "maturity_date": date(2030, 2, 15),
        "offer_date": date(2027, 2, 15),
        "is_floating_coupon": False,
        "is_subordinated": True,
        "is_perpetual": False,
        "amortization": True,
        "liquidity_score": 45,
        "signal": AnalysisSignal.INCREASED_RISK.value,
        "risk_notes": "Demo data: structure-risk bond with amortization.",
    },
]


REPORTS: dict[str, dict[str, Any]] = {
    "DEMO_STABLE": {
        "period_year": 2024,
        "period_quarter": 4,
        "revenue": Decimal("220000000000.00"),
        "ebitda": Decimal("34000000000.00"),
        "net_debt": Decimal("41000000000.00"),
        "total_debt": Decimal("59000000000.00"),
        "cash": Decimal("18000000000.00"),
        "equity": Decimal("102000000000.00"),
        "short_term_debt": Decimal("12000000000.00"),
        "operating_cash_flow": Decimal("28000000000.00"),
        "net_profit": Decimal("17600000000.00"),
        "interest_expense": Decimal("6200000000.00"),
        "debt_to_ebitda": Decimal("1.206"),
        "interest_coverage": Decimal("5.484"),
        "source": "demo",
        "signal": AnalysisSignal.NEUTRAL.value,
    },
    "DEMO_WATCH": {
        "period_year": 2024,
        "period_quarter": 4,
        "revenue": Decimal("96000000000.00"),
        "ebitda": Decimal("15000000000.00"),
        "net_debt": Decimal("48000000000.00"),
        "total_debt": Decimal("56000000000.00"),
        "cash": Decimal("8000000000.00"),
        "equity": Decimal("36000000000.00"),
        "short_term_debt": Decimal("15000000000.00"),
        "operating_cash_flow": Decimal("6200000000.00"),
        "net_profit": Decimal("3100000000.00"),
        "interest_expense": Decimal("6000000000.00"),
        "debt_to_ebitda": Decimal("3.200"),
        "interest_coverage": Decimal("2.500"),
        "source": "demo",
        "signal": AnalysisSignal.NEUTRAL.value,
    },
    "DEMO_STRESS": {
        "period_year": 2024,
        "period_quarter": 4,
        "revenue": Decimal("42000000000.00"),
        "ebitda": Decimal("5200000000.00"),
        "net_debt": Decimal("33000000000.00"),
        "total_debt": Decimal("38000000000.00"),
        "cash": Decimal("5000000000.00"),
        "equity": Decimal("7000000000.00"),
        "short_term_debt": Decimal("18000000000.00"),
        "operating_cash_flow": Decimal("-2100000000.00"),
        "net_profit": Decimal("-3400000000.00"),
        "interest_expense": Decimal("6700000000.00"),
        "debt_to_ebitda": Decimal("6.346"),
        "interest_coverage": Decimal("0.776"),
        "source": "demo",
        "signal": AnalysisSignal.INCREASED_RISK.value,
    },
    "DEMO_NODATA": {
        "period_year": 2024,
        "period_quarter": 4,
        "revenue": None,
        "ebitda": None,
        "net_debt": None,
        "total_debt": None,
        "cash": None,
        "equity": None,
        "short_term_debt": None,
        "operating_cash_flow": None,
        "net_profit": None,
        "interest_expense": None,
        "debt_to_ebitda": None,
        "interest_coverage": None,
        "source": "demo",
        "signal": AnalysisSignal.INSUFFICIENT_DATA.value,
    },
}


COMPANY_SCORES: dict[str, int] = {
    "DEMO_STABLE": 88,
    "DEMO_WATCH": 55,
    "DEMO_STRESS": 35,
}
BOND_SCORES: dict[str, int] = {
    "DEMO_STABLE_MOD": 82,
    "DEMO_STABLE_LOW": 78,
    "DEMO_WATCH_HIGH": 62,
    "DEMO_STRESS_HIGH": 30,
    "DEMO_WATCH_LIQ": 48,
    "DEMO_STRESS_STRUCT": 25,
}


MARKET_PROFILES: dict[str, dict[str, Decimal | int]] = {
    "DEMO_STABLE_MOD": {
        "base": Decimal("99.000"),
        "slope": Decimal("0.035"),
        "wave": Decimal("0.050"),
        "ytm": Decimal("11.200"),
        "duration": Decimal("2.100"),
        "volume": Decimal("24000000.00"),
        "liquidity": 86,
    },
    "DEMO_STABLE_LOW": {
        "base": Decimal("102.000"),
        "slope": Decimal("-0.018"),
        "wave": Decimal("0.030"),
        "ytm": Decimal("7.400"),
        "duration": Decimal("3.000"),
        "volume": Decimal("18000000.00"),
        "liquidity": 80,
    },
    "DEMO_WATCH_HIGH": {
        "base": Decimal("96.000"),
        "slope": Decimal("0.006"),
        "wave": Decimal("0.060"),
        "ytm": Decimal("15.800"),
        "duration": Decimal("2.600"),
        "volume": Decimal("7500000.00"),
        "liquidity": 63,
    },
    "DEMO_STRESS_HIGH": {
        "base": Decimal("88.000"),
        "slope": Decimal("-0.055"),
        "wave": Decimal("0.070"),
        "ytm": Decimal("24.000"),
        "duration": Decimal("1.700"),
        "volume": Decimal("2800000.00"),
        "liquidity": 52,
    },
    "DEMO_WATCH_LIQ": {
        "base": Decimal("94.000"),
        "slope": Decimal("-0.010"),
        "wave": Decimal("0.080"),
        "ytm": Decimal("16.500"),
        "duration": Decimal("2.200"),
        "volume": Decimal("65000.00"),
        "liquidity": 25,
    },
    "DEMO_STRESS_STRUCT": {
        "base": Decimal("86.000"),
        "slope": Decimal("-0.018"),
        "wave": Decimal("0.050"),
        "ytm": Decimal("23.000"),
        "duration": Decimal("4.800"),
        "volume": Decimal("950000.00"),
        "liquidity": 45,
    },
}


CASHFLOW_AMOUNTS: dict[str, Decimal] = {
    "DEMO_STABLE_MOD": Decimal("22.00"),
    "DEMO_STABLE_LOW": Decimal("8.00"),
    "DEMO_WATCH_HIGH": Decimal("28.00"),
    "DEMO_STRESS_HIGH": Decimal("35.00"),
    "DEMO_WATCH_LIQ": Decimal("30.00"),
    "DEMO_STRESS_STRUCT": Decimal("40.00"),
}


def seed_demo_data(db: Session, options: DemoSeedOptions | None = None) -> DemoSeedSummary:
    options = options or DemoSeedOptions()
    summary = DemoSeedSummary()

    companies = _seed_companies(db, summary)
    reports = _seed_reports(db, companies, summary)
    _seed_company_scores(db, companies, reports, summary)
    bonds = _seed_bonds(db, companies, summary)
    _seed_bond_scores(db, bonds, summary)
    db.commit()

    _seed_market_snapshots(db, bonds, summary)
    _seed_cashflows(db, bonds, summary)
    _calculate_credit_risk(db, companies, bonds, summary)
    _build_datasets_and_labels(db, options, bonds, summary)
    _calculate_business_checks(db, options, list(bonds.values()), summary)

    if options.with_ml:
        _run_ml(db, options, list(bonds.values()), summary)
    if options.with_evaluation:
        _run_evaluation(db, summary)

    return summary


def _seed_companies(db: Session, summary: DemoSeedSummary) -> dict[str, Company]:
    companies: dict[str, Company] = {}
    for payload in COMPANIES:
        company, action = _upsert(
            db,
            Company,
            [Company.ticker == payload["ticker"]],
            payload,
        )
        _count_action(summary, "companies", action)
        companies[company.ticker] = company
    db.flush()
    return companies


def _seed_bonds(
    db: Session,
    companies: dict[str, Company],
    summary: DemoSeedSummary,
) -> dict[str, Bond]:
    bonds: dict[str, Bond] = {}
    for payload in BONDS:
        data = payload.copy()
        company_ticker = data.pop("company_ticker")
        data["company_id"] = companies[company_ticker].id
        bond, action = _upsert(
            db,
            Bond,
            [Bond.secid == data["secid"]],
            data,
        )
        _count_action(summary, "bonds", action)
        bonds[bond.secid] = bond
    db.flush()
    return bonds


def _seed_reports(
    db: Session,
    companies: dict[str, Company],
    summary: DemoSeedSummary,
) -> dict[str, FinancialReport]:
    reports: dict[str, FinancialReport] = {}
    for ticker, report_data in REPORTS.items():
        company = companies[ticker]
        data = report_data.copy()
        data["company_id"] = company.id
        data["created_at"] = DEMO_CREATED_AT
        data["updated_at"] = DEMO_CREATED_AT
        report, action = _upsert(
            db,
            FinancialReport,
            [
                FinancialReport.company_id == company.id,
                FinancialReport.period_year == data["period_year"],
                FinancialReport.period_quarter == data["period_quarter"],
            ],
            data,
        )
        _count_action(summary, "financial_reports", action)
        reports[ticker] = report
    db.flush()
    return reports


def _seed_company_scores(
    db: Session,
    companies: dict[str, Company],
    reports: dict[str, FinancialReport],
    summary: DemoSeedSummary,
) -> None:
    for ticker, score in COMPANY_SCORES.items():
        company = companies[ticker]
        report = reports[ticker]
        data = {
            "company_id": company.id,
            "report_id": report.id,
            "score": Decimal(score),
            "signal": AnalysisSignal.NEUTRAL.value,
            "factors": {"demo": True, "profile": ticker},
            "explanation": {"demo": True, "summary": "Demo company score snapshot."},
            "debt_score": score,
            "profitability_score": score,
            "liquidity_score": score,
            "cashflow_score": score,
            "stability_score": score,
            "final_company_score": score,
            "risk_level": "low" if score >= 80 else "medium" if score >= 50 else "high",
            "summary": "Demo company score snapshot.",
            "as_of_date": DEMO_SCORE_AS_OF_DATE,
            "source": DEMO_SCORE_SOURCE,
            "created_at": DEMO_CREATED_AT,
            "updated_at": DEMO_CREATED_AT,
        }
        _, action = _upsert(
            db,
            CompanyScore,
            [
                CompanyScore.company_id == company.id,
                CompanyScore.as_of_date == DEMO_SCORE_AS_OF_DATE,
                CompanyScore.source == DEMO_SCORE_SOURCE,
            ],
            data,
        )
        _count_action(summary, "company_scores", action)
    db.flush()


def _seed_bond_scores(
    db: Session,
    bonds: dict[str, Bond],
    summary: DemoSeedSummary,
) -> None:
    for secid, score in BOND_SCORES.items():
        bond = bonds[secid]
        data = {
            "bond_id": bond.id,
            "company_score_id": None,
            "score": Decimal(score),
            "signal": AnalysisSignal.NEUTRAL.value,
            "factors": {"demo": True, "profile": secid},
            "explanation": {"demo": True, "summary": "Demo bond score snapshot."},
            "yield_score": score,
            "duration_score": score,
            "liquidity_score": score,
            "spread_score": score,
            "risk_penalty": max(0, 100 - score),
            "final_bond_score": score,
            "summary": "Demo bond score snapshot.",
            "as_of_date": DEMO_SCORE_AS_OF_DATE,
            "source": DEMO_SCORE_SOURCE,
            "created_at": DEMO_CREATED_AT,
            "updated_at": DEMO_CREATED_AT,
        }
        _, action = _upsert(
            db,
            BondScore,
            [
                BondScore.bond_id == bond.id,
                BondScore.as_of_date == DEMO_SCORE_AS_OF_DATE,
                BondScore.source == DEMO_SCORE_SOURCE,
            ],
            data,
        )
        _count_action(summary, "bond_scores", action)
    db.flush()


def _seed_market_snapshots(
    db: Session,
    bonds: dict[str, Bond],
    summary: DemoSeedSummary,
) -> None:
    service = MarketSnapshotService(db)
    for secid, bond in bonds.items():
        profile = MARKET_PROFILES[secid]
        for index, trade_date in enumerate(_business_days(DEMO_MARKET_FROM, DEMO_MARKET_TO)):
            clean_price = _market_price(profile, index)
            nkd = Decimal(index % 20) * Decimal("0.35")
            dirty_price = clean_price + (nkd / Decimal("10"))
            snapshot = BondMarketSnapshotCreate(
                bond_id=bond.id,
                trade_date=trade_date,
                price=clean_price,
                clean_price=clean_price,
                dirty_price=dirty_price,
                nkd=nkd,
                yield_to_maturity=profile["ytm"],
                duration_years=profile["duration"],
                volume=profile["volume"],
                liquidity_score=int(profile["liquidity"]),
                spread_to_ofz=Decimal("0.025"),
                source=DEMO_SOURCE,
                raw_payload={"demo": True, "secid": secid, "business_day_index": index},
            )
            _, action = service.create_or_update_with_action(
                snapshot,
                rebuild_existing=True,
            )
            _count_action(summary, "market_snapshots", action)


def _seed_cashflows(
    db: Session,
    bonds: dict[str, Bond],
    summary: DemoSeedSummary,
) -> None:
    service = BondCashflowService(db)
    coupon_dates = [date(2025, 2, 14), date(2025, 3, 14), date(2025, 4, 14)]
    for secid, bond in bonds.items():
        for event_date in coupon_dates:
            action = _cashflow_action(db, bond.id, event_date, "coupon")
            service.create_or_update_event(
                BondCashflowEventCreate(
                    bond_id=bond.id,
                    event_date=event_date,
                    event_type="coupon",
                    amount=CASHFLOW_AMOUNTS[secid],
                    currency="RUB",
                    source=DEMO_SOURCE,
                    raw_payload={"demo": True, "secid": secid},
                )
            )
            _count_action(summary, "cashflow_events", action)

    structure_bond = bonds["DEMO_STRESS_STRUCT"]
    action = _cashflow_action(db, structure_bond.id, date(2025, 3, 10), "amortization")
    service.create_or_update_event(
        BondCashflowEventCreate(
            bond_id=structure_bond.id,
            event_date=date(2025, 3, 10),
            event_type="amortization",
            amount=Decimal("50.00"),
            amount_percent=Decimal("5.0"),
            currency="RUB",
            source=DEMO_SOURCE,
            raw_payload={"demo": True, "secid": structure_bond.secid},
        )
    )
    _count_action(summary, "cashflow_events", action)


def _calculate_credit_risk(
    db: Session,
    companies: dict[str, Company],
    bonds: dict[str, Bond],
    summary: DemoSeedSummary,
) -> None:
    company_service = CompanyCreditHealthService(db)
    bond_service = BondRiskAssessmentService(db, company_health_service=company_service)
    for target_date in DEMO_RISK_DATES:
        for company in companies.values():
            company_service.calculate_for_company(company.id, as_of_date=target_date)
            summary.company_credit_health_calculated += 1
        for bond in bonds.values():
            bond_service.assess_bond(
                bond.id,
                as_of_date=target_date,
                recalculate_company_health=False,
            )
            summary.bond_risk_assessments_calculated += 1


def _build_datasets_and_labels(
    db: Session,
    options: DemoSeedOptions,
    bonds: dict[str, Bond],
    summary: DemoSeedSummary,
) -> None:
    bond_ids = [bond.id for bond in bonds.values()]
    dataset_result = DatasetBuildService(db).build(
        DatasetBuildRequest(
            as_of_date_from=options.date_from,
            as_of_date_to=options.date_to,
            horizon_days=options.horizon_days,
            bond_ids=bond_ids,
            return_method="price",
            benchmark_return=options.benchmark_return,
            transaction_cost_rate=options.transaction_cost_rate,
            rebuild_existing=True,
        )
    )
    summary.feature_snapshots_created += dataset_result.features_created
    summary.feature_snapshots_updated += dataset_result.features_updated
    summary.price_labels_created += dataset_result.labels_created
    summary.price_labels_updated += dataset_result.labels_updated

    total_service = TotalReturnLabelService(db)
    total_result = total_service.build_labels(
        BondTotalReturnLabelBuildRequest(
            as_of_date_from=options.date_from,
            as_of_date_to=options.date_to,
            horizon_days=options.horizon_days,
            bond_ids=bond_ids,
            return_method="total_return",
            benchmark_return=options.benchmark_return,
            transaction_cost_rate=options.transaction_cost_rate,
            rebuild_existing=True,
        )
    )
    summary.total_return_labels_created += total_result.created
    summary.total_return_labels_updated += total_result.updated
    summary.total_return_labels_skipped += total_result.skipped
    summary.warnings.extend(total_result.warnings)

    risk_result = total_service.build_labels(
        BondTotalReturnLabelBuildRequest(
            as_of_date_from=options.date_from,
            as_of_date_to=options.date_to,
            horizon_days=options.horizon_days,
            bond_ids=bond_ids,
            return_method="risk_adjusted",
            benchmark_return=options.benchmark_return,
            transaction_cost_rate=options.transaction_cost_rate,
            rebuild_existing=True,
        )
    )
    summary.risk_adjusted_labels_created += risk_result.created
    summary.risk_adjusted_labels_updated += risk_result.updated
    summary.risk_adjusted_labels_skipped += risk_result.skipped
    summary.warnings.extend(
        warning for warning in risk_result.warnings if warning not in summary.warnings
    )


def _calculate_business_checks(
    db: Session,
    options: DemoSeedOptions,
    bonds: list[Bond],
    summary: DemoSeedSummary,
) -> None:
    bond_ids = [bond.id for bond in bonds]
    impact_label = db.execute(
        select(BondReturnLabel)
        .where(
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.horizon_days == options.horizon_days,
            BondReturnLabel.price_return < 0,
            BondReturnLabel.net_total_return > 0,
        )
        .limit(1)
    ).scalar_one_or_none()
    summary.cashflow_impact_example_found = impact_label is not None

    assessments = list(
        db.execute(
            select(BondRiskAssessment).where(BondRiskAssessment.bond_id.in_(bond_ids))
        ).scalars()
    )
    summary.high_yield_weak_issuer_warning_found = any(
        HIGH_YIELD_WARNING in (assessment.warnings or [])
        for assessment in assessments
    )

    counts = {
        "positive_return": 0,
        "negative_return": 0,
        "insufficient_data": 0,
    }
    rows = db.execute(
        select(BondReturnLabel.label, func.count())
        .where(
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.horizon_days == options.horizon_days,
            BondReturnLabel.return_method == "risk_adjusted",
        )
        .group_by(BondReturnLabel.label)
    ).all()
    for label, count in rows:
        counts[label] = int(count)
    summary.risk_adjusted_label_distribution = counts


def _run_ml(
    db: Session,
    options: DemoSeedOptions,
    bonds: list[Bond],
    summary: DemoSeedSummary,
) -> None:
    bond_ids = [bond.id for bond in bonds]
    try:
        train_result = MLTrainingService(db).train(
            MLTrainRequest(
                horizon_days=options.horizon_days,
                return_method="risk_adjusted",
                include_credit_risk_features=True,
                as_of_date_from=options.date_from,
                as_of_date_to=options.date_to,
                bond_ids=bond_ids,
                model_type="logistic_regression",
                test_size=0.2,
                min_rows=30,
                random_state=42,
            )
        )
        summary.ml_run_id = train_result.run_id
        summary.ml_message = f"ML training completed. Run id: {train_result.run_id}"

        run = db.get(MLModelRun, train_result.run_id)
        if run is not None:
            params = dict(run.params or {})
            params["demo"] = True
            run.params = params
            db.add(run)
            db.commit()

        prediction_service = MLPredictionService(db)
        for bond in bonds:
            prediction_result = prediction_service.predict(
                MLPredictionRequest(
                    model_run_id=train_result.run_id,
                    bond_id=bond.id,
                    as_of_date_from=options.date_from,
                    as_of_date_to=options.date_to,
                    limit=5000,
                    offset=0,
                    save_predictions=True,
                )
            )
            summary.predictions_created_or_updated += len(prediction_result.predictions)
    except HTTPException as exc:
        summary.ml_message = f"ML training skipped: {exc.detail}"
    except Exception as exc:
        summary.ml_message = f"ML training skipped: {exc}"


def _run_evaluation(db: Session, summary: DemoSeedSummary) -> None:
    if summary.ml_run_id is None:
        summary.evaluation_message = "Evaluation skipped because no ML run was created."
        return
    try:
        from app.services.ml_evaluation_service import MLEvaluationFilters, MLEvaluationService

        report = MLEvaluationService(db).evaluate_run(
            summary.ml_run_id,
            filters=MLEvaluationFilters(),
        )
        summary.evaluation = {
            "model_run_id": report.model_run_id,
            "return_method": report.return_method,
            "total_predictions": report.coverage["total_predictions"],
            "evaluable_predictions": report.coverage["evaluable_predictions"],
            "accuracy": report.evaluation_metrics.accuracy,
            "precision": report.evaluation_metrics.precision,
            "recall": report.evaluation_metrics.recall,
            "f1": report.evaluation_metrics.f1,
            "roc_auc": report.evaluation_metrics.roc_auc,
            "brier_score": report.calibration.brier_score,
            "warnings": report.warnings,
        }
        summary.evaluation_message = "Evaluation completed."
    except ImportError:
        summary.evaluation_message = "ML evaluation service is not available yet"
    except HTTPException as exc:
        summary.evaluation_message = f"Evaluation skipped: {exc.detail}"
    except Exception as exc:
        summary.evaluation_message = f"Evaluation skipped: {exc}"


def _upsert(
    db: Session,
    model: type,
    conditions: list[Any],
    data: dict[str, Any],
) -> tuple[Any, str]:
    instance = db.execute(select(model).where(*conditions)).scalar_one_or_none()
    if instance is None:
        instance = model(**data)
        db.add(instance)
        db.flush()
        return instance, "created"
    for field, value in data.items():
        setattr(instance, field, value)
    db.add(instance)
    db.flush()
    return instance, "updated"


def _cashflow_action(
    db: Session,
    bond_id: int,
    event_date: date,
    event_type: str,
) -> str:
    existing = db.execute(
        select(BondCashflowEvent).where(
            BondCashflowEvent.bond_id == bond_id,
            BondCashflowEvent.event_date == event_date,
            BondCashflowEvent.event_type == event_type,
            BondCashflowEvent.source == DEMO_SOURCE,
        )
    ).scalar_one_or_none()
    return "updated" if existing is not None else "created"


def _count_action(summary: DemoSeedSummary, prefix: str, action: str) -> None:
    field_name = f"{prefix}_{action}"
    if hasattr(summary, field_name):
        setattr(summary, field_name, getattr(summary, field_name) + 1)


def _business_days(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _market_price(profile: dict[str, Decimal | int], index: int) -> Decimal:
    wave_offset = Decimal((index % 9) - 4) * profile["wave"]
    value = profile["base"] + (profile["slope"] * Decimal(index)) + wave_offset
    return value.quantize(Decimal("0.000001"))


def _parse_args(argv: list[str] | None) -> DemoSeedOptions:
    parser = argparse.ArgumentParser(description="Seed deterministic BondRadar demo data.")
    parser.add_argument("--with-ml", action="store_true")
    parser.add_argument("--with-evaluation", action="store_true")
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument("--date-from", type=date.fromisoformat, default=date(2025, 1, 10))
    parser.add_argument("--date-to", type=date.fromisoformat, default=date(2025, 3, 15))
    parser.add_argument("--benchmark-return", type=Decimal, default=Decimal("0.005"))
    parser.add_argument("--transaction-cost-rate", type=Decimal, default=Decimal("0.001"))
    args = parser.parse_args(argv)
    return DemoSeedOptions(
        with_ml=args.with_ml,
        with_evaluation=args.with_evaluation,
        horizon_days=args.horizon_days,
        date_from=args.date_from,
        date_to=args.date_to,
        benchmark_return=args.benchmark_return,
        transaction_cost_rate=args.transaction_cost_rate,
    )


def _print_summary(summary: DemoSeedSummary) -> None:
    print("Demo seed completed")
    print()
    print(f"Companies created/updated: {summary.companies_created}/{summary.companies_updated}")
    print(f"Bonds created/updated: {summary.bonds_created}/{summary.bonds_updated}")
    print(
        "Financial reports created/updated: "
        f"{summary.financial_reports_created}/{summary.financial_reports_updated}"
    )
    print(
        f"Company scores created/updated: "
        f"{summary.company_scores_created}/{summary.company_scores_updated}"
    )
    print(
        f"Bond scores created/updated: "
        f"{summary.bond_scores_created}/{summary.bond_scores_updated}"
    )
    print(
        f"Market snapshots created/updated: "
        f"{summary.market_snapshots_created}/{summary.market_snapshots_updated}"
    )
    print(
        f"Cashflow events created/updated: "
        f"{summary.cashflow_events_created}/{summary.cashflow_events_updated}"
    )
    print(f"Company credit health snapshots calculated: {summary.company_credit_health_calculated}")
    print(f"Bond risk assessments calculated: {summary.bond_risk_assessments_calculated}")
    print(
        f"Feature snapshots created/updated: "
        f"{summary.feature_snapshots_created}/{summary.feature_snapshots_updated}"
    )
    print(f"Price labels created/updated: {summary.price_labels_created}/{summary.price_labels_updated}")
    print(
        f"Total return labels created/updated: "
        f"{summary.total_return_labels_created}/{summary.total_return_labels_updated}"
    )
    print(
        f"Risk-adjusted labels created/updated: "
        f"{summary.risk_adjusted_labels_created}/{summary.risk_adjusted_labels_updated}"
    )
    print()
    print(
        "Cashflow impact example found: "
        f"{'yes' if summary.cashflow_impact_example_found else 'no'}"
    )
    print(
        "High-yield weak issuer warning found: "
        f"{'yes' if summary.high_yield_weak_issuer_warning_found else 'no'}"
    )
    distribution = summary.risk_adjusted_label_distribution
    print(
        "Risk-adjusted labels: "
        f"positive={distribution.get('positive_return', 0)}, "
        f"negative={distribution.get('negative_return', 0)}, "
        f"insufficient={distribution.get('insufficient_data', 0)}"
    )
    print(summary.ml_message)
    if summary.ml_run_id is not None:
        print(f"ML run id: {summary.ml_run_id}")
        print(f"Predictions created/updated: {summary.predictions_created_or_updated}")
    if summary.evaluation_message is not None:
        print(summary.evaluation_message)
    if summary.evaluation is not None:
        print(f"Evaluation: {summary.evaluation}")


def main(argv: list[str] | None = None) -> int:
    options = _parse_args(argv)
    db = SessionLocal()
    try:
        summary = seed_demo_data(db, options)
        _print_summary(summary)
        return 0
    except Exception as exc:
        print(f"Demo seed failed: {exc}")
        print("Database connection failed or Alembic migrations may not be applied.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
