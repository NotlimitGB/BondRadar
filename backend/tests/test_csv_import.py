from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.financial_report import FinancialReport


def upload(client: TestClient, endpoint: str, content: str, filename: str = "data.csv"):
    return client.post(
        endpoint,
        files={"file": (filename, content.encode("utf-8"), "text/csv")},
    )


def create_company(
    db: Session,
    *,
    inn: str = "7701000100",
    name: str = "Existing Company",
    ticker: str = "EXCO",
) -> Company:
    company = Company(name=name, ticker=ticker, inn=inn, country="RU")
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def test_bonds_csv_creates_company_and_bond(
    client: TestClient, db_session: Session
) -> None:
    response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,bond_name,isin,currency,current_price",
                "7701000200,New Issuer,NISS,New Bond,RU000A100111,RUB,98.5",
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert response.json()["companies_created"] == 1

    company = db_session.execute(
        select(Company).where(Company.inn == "7701000200")
    ).scalar_one()
    bond = db_session.execute(
        select(Bond).where(Bond.isin == "RU000A100111")
    ).scalar_one()
    assert company.name == "New Issuer"
    assert bond.company_id == company.id
    assert bond.current_price == Decimal("98.500")


def test_bonds_csv_updates_existing_company_and_bond_by_inn_and_isin(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session)
    bond = Bond(
        company_id=company.id,
        isin="RU000A100112",
        name="Old Bond",
        currency="RUB",
        current_price=Decimal("95.000"),
    )
    db_session.add(bond)
    db_session.commit()

    response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,bond_name,isin,current_price",
                "7701000100,Updated Company,UPCO,Updated Bond,RU000A100112,101.25",
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"] == 1
    assert payload["companies_updated"] == 1

    db_session.refresh(company)
    db_session.refresh(bond)
    assert company.name == "Updated Company"
    assert bond.name == "Updated Bond"
    assert bond.current_price == Decimal("101.250")


def test_bonds_csv_creates_and_updates_bond_by_secid_without_isin(
    client: TestClient, db_session: Session
) -> None:
    first_response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,bond_name,secid,current_price",
                "7701000300,Secid Issuer,SID,Secid Bond,SECID001,99.1",
            ]
        ),
    )
    second_response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,bond_name,secid,current_price",
                "7701000300,Secid Bond Updated,SECID001,100.2",
            ]
        ),
    )

    assert first_response.status_code == 200
    assert first_response.json()["created"] == 1
    assert second_response.status_code == 200
    assert second_response.json()["updated"] == 1

    bond = db_session.execute(select(Bond).where(Bond.secid == "SECID001")).scalar_one()
    assert bond.isin is None
    assert bond.name == "Secid Bond Updated"
    assert bond.current_price == Decimal("100.200")


def test_bonds_csv_partial_success_rolls_back_invalid_row(
    client: TestClient, db_session: Session
) -> None:
    response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,bond_name,isin,current_price",
                "7701000400,Good Issuer,GOOD,Good Bond,RU000A100113,99.9",
                "7701000401,Bad Issuer,BAD,Bad Bond,RU000A100114,not-a-number",
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["skipped"] == 1
    assert payload["failed_rows"] == 1
    assert "Invalid decimal value for current_price" in payload["errors"][0]["error"]

    assert db_session.execute(
        select(Company).where(Company.inn == "7701000401")
    ).scalar_one_or_none() is None
    assert db_session.execute(
        select(Bond).where(Bond.isin == "RU000A100114")
    ).scalar_one_or_none() is None


def test_reports_csv_creates_report(client: TestClient, db_session: Session) -> None:
    response = upload(
        client,
        "/api/import/reports-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,period_year,revenue,ebitda",
                "7701000500,Report Issuer,RISS,2025,1000,250",
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["companies_created"] == 1

    company = db_session.execute(
        select(Company).where(Company.inn == "7701000500")
    ).scalar_one()
    report = db_session.execute(
        select(FinancialReport).where(FinancialReport.company_id == company.id)
    ).scalar_one()
    assert report.period_year == 2025
    assert report.period_quarter == 0
    assert report.revenue == Decimal("1000.00")


def test_reports_csv_updates_report_by_company_year_and_quarter(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, inn="7701000600", ticker="RUPD")
    report = FinancialReport(
        company_id=company.id,
        period_year=2025,
        period_quarter=4,
        revenue=Decimal("100.00"),
    )
    db_session.add(report)
    db_session.commit()

    response = upload(
        client,
        "/api/import/reports-csv",
        "\n".join(
            [
                "company_inn,period_year,period_quarter,revenue,source",
                "7701000600,2025,4,555,updated csv",
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    db_session.refresh(report)
    assert report.revenue == Decimal("555.00")
    assert report.source == "updated csv"


def test_reports_csv_creates_company_only_with_enough_company_fields(
    client: TestClient, db_session: Session
) -> None:
    response = upload(
        client,
        "/api/import/reports-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,period_year",
                "7701000700,Enough Fields,EFLD,2025",
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json()["companies_created"] == 1
    assert db_session.execute(
        select(Company).where(Company.inn == "7701000700")
    ).scalar_one_or_none() is not None


def test_reports_csv_row_error_when_company_cannot_be_resolved(
    client: TestClient, db_session: Session
) -> None:
    response = upload(
        client,
        "/api/import/reports-csv",
        "\n".join(
            [
                "company_inn,period_year,revenue",
                "7701000800,2025,100",
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped"] == 1
    assert payload["errors"][0]["error"] == (
        "Company with provided inn not found and company_name is missing"
    )
    assert db_session.execute(
        select(Company).where(Company.inn == "7701000800")
    ).scalar_one_or_none() is None


def test_import_csv_invalid_numeric_date_and_bool_values(
    client: TestClient,
) -> None:
    response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,company_name,company_ticker,bond_name,isin,current_price,maturity_date,is_floating_coupon",
                "7701000900,Bad Decimal,BD1,Bond 1,RU000A100115,bad,2028-01-01,true",
                "7701000901,Bad Date,BD2,Bond 2,RU000A100116,99.1,not-a-date,true",
                "7701000902,Bad Bool,BD3,Bond 3,RU000A100117,99.1,2028-01-01,maybe",
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped"] == 3
    errors = [error["error"] for error in payload["errors"]]
    assert "Invalid decimal value for current_price" in errors
    assert "Invalid date value for maturity_date" in errors
    assert "Invalid boolean value for is_floating_coupon" in errors


def test_import_csv_missing_required_headers_returns_400(
    client: TestClient,
) -> None:
    response = upload(
        client,
        "/api/import/bonds-csv",
        "\n".join(
            [
                "company_inn,isin",
                "7701001000,RU000A100118",
            ]
        ),
    )

    assert response.status_code == 400
    assert "Missing required headers" in response.json()["detail"]


def test_import_csv_non_csv_filename_returns_400(client: TestClient) -> None:
    response = upload(
        client,
        "/api/import/bonds-csv",
        "company_inn,bond_name\n7701001100,Bond",
        filename="data.txt",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only CSV files are supported"
