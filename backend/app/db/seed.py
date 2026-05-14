from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.bond import Bond
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


COMPANIES = [
    {
        "name": "North Retail Group",
        "ticker": "NRG",
        "sector": "Retail",
        "inn": "7701000001",
        "country": "RU",
        "credit_rating": "A-",
        "signal": AnalysisSignal.INTERESTING_FOR_ANALYSIS.value,
        "notes": "Demo issuer with stable operating metrics for analysis screens.",
    },
    {
        "name": "Volga Infrastructure",
        "ticker": "VINF",
        "sector": "Infrastructure",
        "inn": "7701000002",
        "country": "RU",
        "credit_rating": "BBB+",
        "signal": AnalysisSignal.NEUTRAL.value,
        "notes": "Demo issuer with moderate leverage.",
    },
    {
        "name": "Baltic Leasing",
        "ticker": "BLSG",
        "sector": "Financial services",
        "inn": "7701000003",
        "country": "RU",
        "credit_rating": None,
        "signal": AnalysisSignal.ELEVATED_RISK.value,
        "notes": "Demo issuer with incomplete public financial coverage.",
    },
]


BONDS = [
    {
        "company_ticker": "NRG",
        "isin": "RU000A100001",
        "name": "North Retail BO-01",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("98.450"),
        "coupon_rate": Decimal("10.250"),
        "yield_to_maturity": Decimal("11.100"),
        "duration_years": Decimal("1.850"),
        "volume": Decimal("25000000.00"),
        "maturity_date": date(2028, 8, 15),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 72,
        "signal": AnalysisSignal.INTERESTING_FOR_ANALYSIS.value,
        "risk_notes": "Demo signal: reasonable duration and visible issuer metrics.",
    },
    {
        "company_ticker": "VINF",
        "isin": "RU000A100002",
        "name": "Volga Infrastructure BO-02",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("101.200"),
        "coupon_rate": Decimal("9.750"),
        "yield_to_maturity": Decimal("10.050"),
        "duration_years": Decimal("2.400"),
        "volume": Decimal("8500000.00"),
        "maturity_date": date(2029, 3, 20),
        "offer_date": date(2027, 3, 20),
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": True,
        "liquidity_score": 54,
        "signal": AnalysisSignal.NEUTRAL.value,
        "risk_notes": "Demo signal: balanced metrics, no direct recommendation.",
    },
    {
        "company_ticker": "BLSG",
        "isin": "RU000A100003",
        "name": "Baltic Leasing BO-P01",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": Decimal("92.800"),
        "coupon_rate": Decimal("13.500"),
        "yield_to_maturity": Decimal("16.200"),
        "duration_years": Decimal("1.100"),
        "volume": Decimal("350000.00"),
        "maturity_date": date(2027, 11, 5),
        "offer_date": None,
        "is_floating_coupon": True,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": 28,
        "signal": AnalysisSignal.ELEVATED_RISK.value,
        "risk_notes": "Demo signal: low liquidity and limited disclosure.",
    },
]


REPORTS = [
    {
        "company_ticker": "NRG",
        "period_year": 2025,
        "period_quarter": 0,
        "revenue": Decimal("184500000000.00"),
        "ebitda": Decimal("24500000000.00"),
        "net_debt": Decimal("38200000000.00"),
        "total_debt": Decimal("52600000000.00"),
        "cash": Decimal("14400000000.00"),
        "equity": Decimal("84500000000.00"),
        "short_term_debt": Decimal("9800000000.00"),
        "operating_cash_flow": Decimal("21300000000.00"),
        "net_profit": Decimal("12800000000.00"),
        "interest_expense": Decimal("5100000000.00"),
        "debt_to_ebitda": Decimal("1.559"),
        "interest_coverage": Decimal("4.804"),
        "source": "Demo annual report",
        "signal": AnalysisSignal.INTERESTING_FOR_ANALYSIS.value,
    },
    {
        "company_ticker": "VINF",
        "period_year": 2025,
        "period_quarter": 0,
        "revenue": Decimal("91200000000.00"),
        "ebitda": Decimal("17600000000.00"),
        "net_debt": Decimal("42100000000.00"),
        "total_debt": Decimal("50300000000.00"),
        "cash": Decimal("8200000000.00"),
        "equity": Decimal("38800000000.00"),
        "short_term_debt": Decimal("13700000000.00"),
        "operating_cash_flow": Decimal("7400000000.00"),
        "net_profit": Decimal("5200000000.00"),
        "interest_expense": Decimal("4600000000.00"),
        "debt_to_ebitda": Decimal("2.392"),
        "interest_coverage": Decimal("3.826"),
        "source": "Demo annual report",
        "signal": AnalysisSignal.NEUTRAL.value,
    },
    {
        "company_ticker": "BLSG",
        "period_year": 2025,
        "period_quarter": 0,
        "revenue": Decimal("28700000000.00"),
        "ebitda": Decimal("4100000000.00"),
        "net_debt": Decimal("22900000000.00"),
        "total_debt": Decimal("25100000000.00"),
        "cash": Decimal("2200000000.00"),
        "equity": Decimal("5900000000.00"),
        "short_term_debt": Decimal("11800000000.00"),
        "operating_cash_flow": Decimal("-900000000.00"),
        "net_profit": Decimal("-1300000000.00"),
        "interest_expense": Decimal("2100000000.00"),
        "debt_to_ebitda": Decimal("5.585"),
        "interest_coverage": Decimal("1.952"),
        "source": "Demo annual report",
        "signal": AnalysisSignal.ELEVATED_RISK.value,
    },
]


COMPANY_SCORES = [
    {
        "company_ticker": "NRG",
        "score": Decimal("78.50"),
        "signal": AnalysisSignal.INTERESTING_FOR_ANALYSIS.value,
        "factors": {
            "leverage": "moderate",
            "coverage": "strong",
            "disclosure": "sufficient",
        },
        "summary": "Demo snapshot: issuer metrics are suitable for deeper analysis.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
    {
        "company_ticker": "VINF",
        "score": Decimal("61.00"),
        "signal": AnalysisSignal.NEUTRAL.value,
        "factors": {
            "leverage": "acceptable",
            "coverage": "acceptable",
            "disclosure": "sufficient",
        },
        "summary": "Demo snapshot: no strong informational signal.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
    {
        "company_ticker": "BLSG",
        "score": Decimal("34.00"),
        "signal": AnalysisSignal.ELEVATED_RISK.value,
        "factors": {
            "leverage": "high",
            "coverage": "weak",
            "disclosure": "limited",
        },
        "summary": "Demo snapshot: elevated risk signal due to leverage and disclosure.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
]


BOND_SCORES = [
    {
        "isin": "RU000A100001",
        "score": Decimal("76.00"),
        "signal": AnalysisSignal.INTERESTING_FOR_ANALYSIS.value,
        "factors": {
            "issuer": "stable",
            "liquidity": "good",
            "duration": "short",
        },
        "summary": "Demo snapshot: bond is interesting for further analysis.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
    {
        "isin": "RU000A100002",
        "score": Decimal("58.50"),
        "signal": AnalysisSignal.NEUTRAL.value,
        "factors": {
            "issuer": "balanced",
            "liquidity": "moderate",
            "duration": "medium",
        },
        "summary": "Demo snapshot: neutral informational signal.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
    {
        "isin": "RU000A100003",
        "score": Decimal("29.00"),
        "signal": AnalysisSignal.ELEVATED_RISK.value,
        "factors": {
            "issuer": "weak",
            "liquidity": "low",
            "coupon": "floating",
        },
        "summary": "Demo snapshot: elevated risk signal from issuer and liquidity factors.",
        "as_of_date": date(2026, 5, 14),
        "source": "Demo seed",
    },
]


def upsert_by_attr(db: Session, model: type, attr_name: str, data: dict) -> object:
    attr = getattr(model, attr_name)
    instance = db.execute(select(model).where(attr == data[attr_name])).scalar_one_or_none()
    if instance is None:
        instance = model(**data)
        db.add(instance)
    else:
        for field, value in data.items():
            setattr(instance, field, value)
    return instance


def seed(db: Session) -> None:
    companies_by_ticker: dict[str, Company] = {}
    for company_data in COMPANIES:
        company = upsert_by_attr(db, Company, "ticker", company_data)
        companies_by_ticker[company.ticker] = company
    db.flush()

    bonds_by_isin: dict[str, Bond] = {}
    for bond_data in BONDS:
        data = bond_data.copy()
        company_ticker = data.pop("company_ticker")
        data["company_id"] = companies_by_ticker[company_ticker].id
        bond = upsert_by_attr(db, Bond, "isin", data)
        bonds_by_isin[bond.isin] = bond
    db.flush()

    for report_data in REPORTS:
        data = report_data.copy()
        company_ticker = data.pop("company_ticker")
        company = companies_by_ticker[company_ticker]
        report = db.execute(
            select(FinancialReport).where(
                FinancialReport.company_id == company.id,
                FinancialReport.period_year == data["period_year"],
                FinancialReport.period_quarter == data["period_quarter"],
            )
        ).scalar_one_or_none()
        data["company_id"] = company.id
        if report is None:
            db.add(FinancialReport(**data))
        else:
            for field, value in data.items():
                setattr(report, field, value)

    for score_data in COMPANY_SCORES:
        data = score_data.copy()
        company_ticker = data.pop("company_ticker")
        company = companies_by_ticker[company_ticker]
        score = db.execute(
            select(CompanyScore).where(
                CompanyScore.company_id == company.id,
                CompanyScore.as_of_date == data["as_of_date"],
                CompanyScore.source == data["source"],
            )
        ).scalar_one_or_none()
        data["company_id"] = company.id
        if score is None:
            db.add(CompanyScore(**data))
        else:
            for field, value in data.items():
                setattr(score, field, value)

    for score_data in BOND_SCORES:
        data = score_data.copy()
        isin = data.pop("isin")
        bond = bonds_by_isin[isin]
        score = db.execute(
            select(BondScore).where(
                BondScore.bond_id == bond.id,
                BondScore.as_of_date == data["as_of_date"],
                BondScore.source == data["source"],
            )
        ).scalar_one_or_none()
        data["bond_id"] = bond.id
        if score is None:
            db.add(BondScore(**data))
        else:
            for field, value in data.items():
                setattr(score, field, value)

    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
