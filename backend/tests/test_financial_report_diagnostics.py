from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.services.financial_report_diagnostics_service import (
    FinancialReportDiagnosticsService,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_diagnostics as diagnostics_script  # noqa: E402
import financial_report_import as import_script  # noqa: E402


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(
    db: Session,
    ticker: str = "DIAG",
    *,
    name: str | None = None,
    inn: str | None = None,
) -> Company:
    company = Company(
        name=name or f"{ticker} Company",
        ticker=ticker,
        inn=inn or f"77{abs(hash(ticker)) % 100000000:08d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_report(
    db: Session,
    company: Company,
    *,
    period_year: int = 2025,
    period_quarter: int = 0,
    period_end_date: date | None = date(2025, 12, 31),
    published_at: datetime | None = None,
    signal: str = AnalysisSignal.INSUFFICIENT_DATA.value,
    revenue: Decimal | None = Decimal("404359000000"),
    ebitda: Decimal | None = Decimal("74677000000"),
    net_debt: Decimal | None = None,
    total_debt: Decimal | None = Decimal("390544000000"),
    cash: Decimal | None = Decimal("95772000000"),
    equity: Decimal | None = Decimal("32596000000"),
    short_term_debt: Decimal | None = Decimal("144888000000"),
    operating_cash_flow: Decimal | None = Decimal("19095000000"),
    net_profit: Decimal | None = Decimal("-24458000000"),
    interest_expense: Decimal | None = None,
    created_at: datetime | None = None,
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=period_year,
        period_quarter=period_quarter,
        period_start_date=date(period_year, 1, 1),
        period_end_date=period_end_date,
        published_at=published_at,
        currency="RUB",
        source="official_issuer_report",
        signal=signal,
        revenue=revenue,
        ebitda=ebitda,
        net_debt=net_debt,
        total_debt=total_debt,
        cash=cash,
        equity=equity,
        short_term_debt=short_term_debt,
        operating_cash_flow=operating_cash_flow,
        net_profit=net_profit,
        interest_expense=interest_expense,
        created_at=created_at,
    )
    db.add(report)
    db.flush()
    return report


def test_company_without_report_returns_readable_gap(
    db_session: Session,
) -> None:
    company = create_company(db_session, "NONE")
    db_session.commit()

    result = FinancialReportDiagnosticsService(
        db_session
    ).get_company_financial_report_diagnostics(company.id)

    assert result["has_financial_report"] is False
    assert result["latest_report"] is None
    assert result["safe_for_feature_pipeline"] is False
    assert result["risk_scoring_readiness"] == "not_ready"
    assert result["signal_explanation"]["severity"] == "critical"


def test_latest_report_selection_prefers_year_annual_period_end_and_created_at(
    db_session: Session,
) -> None:
    company = create_company(db_session, "LATE")
    create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=0,
        period_end_date=date(2024, 12, 31),
    )
    q4 = create_report(
        db_session,
        company,
        period_year=2025,
        period_quarter=4,
        period_end_date=date(2025, 12, 31),
    )
    annual = create_report(
        db_session,
        company,
        period_year=2025,
        period_quarter=0,
        period_end_date=date(2025, 12, 31),
        created_at=dt(2026, 3, 1),
    )
    create_report(
        db_session,
        company,
        period_year=2025,
        period_quarter=3,
        period_end_date=date(2025, 9, 30),
        created_at=dt(2026, 4, 1),
    )
    db_session.commit()

    result = FinancialReportDiagnosticsService(
        db_session
    ).get_company_financial_report_diagnostics(company.id)

    assert result["latest_report"]["id"] == annual.id
    assert result["latest_report"]["id"] != q4.id


def test_raw_coverage_and_derived_metrics_explain_tmk_like_partial_report(
    db_session: Session,
) -> None:
    company = create_company(db_session, "TMK", name="ТМК")
    create_report(db_session, company)
    db_session.commit()

    result = FinancialReportDiagnosticsService(
        db_session
    ).get_company_financial_report_diagnostics(company.id)

    assert result["has_financial_report"] is True
    assert "interest_expense" in result["raw_fields"]["missing"]
    assert "net_debt" in result["raw_fields"]["missing"]
    assert result["derived_metrics"]["fallback"]["net_debt_fallback"] == 294772000000
    assert result["derived_metrics"]["fallback"]["net_debt_to_ebitda_fallback"] > 3.9
    assert any(
        item["metric"] == "interest_coverage"
        and item["reason"] == "interest_expense is missing"
        for item in result["derived_metrics"]["missing"]
    )
    assert result["signal_explanation"]["signal"] == "insufficient_data"
    assert "net_profit is negative" in result["signal_explanation"]["warnings"]
    assert result["safe_for_feature_pipeline"] is True
    assert result["safe_for_risk_scoring"] is False
    assert result["risk_scoring_readiness"] == "partial"
    assert result["recommended_next_fields"][:2] == ["interest_expense", "net_debt"]


def test_full_data_report_is_ready_and_zero_division_does_not_crash(
    db_session: Session,
) -> None:
    ready = create_company(db_session, "FULL")
    create_report(
        db_session,
        ready,
        net_debt=Decimal("300"),
        total_debt=Decimal("400"),
        cash=Decimal("100"),
        ebitda=Decimal("200"),
        equity=Decimal("500"),
        net_profit=Decimal("70"),
        interest_expense=Decimal("50"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    zero = create_company(db_session, "ZERO")
    create_report(
        db_session,
        zero,
        ebitda=Decimal("0"),
        total_debt=Decimal("400"),
        cash=Decimal("100"),
        equity=Decimal("500"),
        interest_expense=Decimal("50"),
    )
    db_session.commit()

    service = FinancialReportDiagnosticsService(db_session)
    ready_result = service.get_company_financial_report_diagnostics(ready.id)
    zero_result = service.get_company_financial_report_diagnostics(zero.id)

    assert ready_result["signal_explanation"]["severity"] == "healthy"
    assert ready_result["safe_for_risk_scoring"] is True
    assert ready_result["risk_scoring_readiness"] == "ready"
    assert zero_result["risk_scoring_readiness"] == "not_ready"
    assert "ebitda is zero or negative" in zero_result["signal_explanation"]["critical"]
    assert any(
        item["reason"] == "ebitda is zero"
        for item in zero_result["derived_metrics"]["missing"]
    )


def test_missing_ebitda_is_critical_and_missing_interest_expense_is_warning(
    db_session: Session,
) -> None:
    company = create_company(db_session, "MISS")
    create_report(db_session, company, ebitda=None, interest_expense=None)
    db_session.commit()

    result = FinancialReportDiagnosticsService(
        db_session
    ).get_company_financial_report_diagnostics(company.id)

    assert result["risk_scoring_readiness"] == "not_ready"
    assert "ebitda is missing" in result["signal_explanation"]["critical"]
    assert "interest_expense is missing" in result["signal_explanation"]["reasons"]


def test_duplicate_candidate_resolves_to_canonical_report_without_duplication(
    db_session: Session,
) -> None:
    canonical = create_company(db_session, "CAN", name="Canonical")
    duplicate = create_company(db_session, "DUP", name="Unknown issuer for RU000DUP")
    create_report(db_session, canonical)
    db_session.add(
        CompanyIdentityDuplicateCandidate(
            canonical_company_id=canonical.id,
            candidate_company_id=duplicate.id,
            group_key="canonical",
            match_type="manual_review",
            match_score=Decimal("0.9500"),
            match_reasons=["test"],
            status="accepted",
            review_status="reviewed",
            source="test",
        )
    )
    db_session.commit()

    result = FinancialReportDiagnosticsService(
        db_session
    ).get_company_financial_report_diagnostics(duplicate.id)

    assert result["company_id"] == duplicate.id
    assert result["canonical_company_id"] == canonical.id
    assert result["is_duplicate_candidate"] is True
    assert result["latest_report"]["company_id"] == canonical.id
    assert (
        db_session.scalar(
            select(func.count()).select_from(FinancialReport).where(
                FinancialReport.company_id == duplicate.id
            )
        )
        == 0
    )


def test_diagnostics_api_returns_shape_and_query_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "API")
    create_report(db_session, company)
    db_session.commit()

    response = client.get(
        f"/api/financial-reports/diagnostics/company/{company.id}",
        params={
            "include_duplicate_context": "false",
            "include_derived_metrics": "false",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_id"] == company.id
    assert payload["has_financial_report"] is True
    assert payload["derived_metrics"] is None
    assert payload["duplicate_context"] is None


def test_cli_script_writes_json_markdown_and_never_calls_mutating_endpoints(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "diagnostics.json"
    markdown_output = tmp_path / "diagnostics.md"
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/ingest" not in url
        assert "/paper-trading" not in url
        if url.endswith("/api/financial-reports/stats"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "financial_reports_count": 1,
                    "financial_report_source_documents_count": 1,
                    "financial_report_import_runs_count": 1,
                },
            )
        if "/api/financial-reports/diagnostics/company/125" in url:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "company_id": 125,
                    "company_name": "ТМК",
                    "canonical_company_id": 125,
                    "canonical_company_name": "ТМК",
                    "is_duplicate_candidate": False,
                    "has_financial_report": True,
                    "latest_report": {
                        "id": 1,
                        "company_id": 125,
                        "period_year": 2025,
                        "period_quarter": 0,
                        "period_end_date": "2025-12-31",
                        "source": "official_issuer_report",
                        "currency": "RUB",
                        "signal": "insufficient_data",
                    },
                    "raw_fields": {
                        "present": ["revenue", "ebitda"],
                        "missing": ["net_debt", "interest_expense"],
                    },
                    "derived_metrics": {
                        "computed": {},
                        "fallback": {"net_debt_fallback": 294772000000},
                        "missing": [
                            {
                                "metric": "interest_coverage",
                                "reason": "interest_expense is missing",
                            }
                        ],
                    },
                    "signal_explanation": {
                        "signal": "insufficient_data",
                        "severity": "warning",
                        "reasons": ["interest_expense is missing"],
                        "critical": [],
                        "warnings": [],
                    },
                    "recommended_next_fields": ["interest_expense", "net_debt"],
                    "safe_for_feature_pipeline": True,
                    "safe_for_risk_scoring": False,
                    "risk_scoring_readiness": "partial",
                },
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = diagnostics_script.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--company-ids",
            "125",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    report, exit_code = diagnostics_script.run_diagnostics(args, http_request=fake_http)
    diagnostics_script.write_json_report(report, json_output)
    diagnostics_script.write_markdown_report(report, markdown_output)

    assert exit_code == 0
    assert report["read_only"] is True
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False
    assert report["financial_reports_count"] == 1
    assert json.loads(json_output.read_text(encoding="utf-8"))["company_count"] == 1
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Financial Report Diagnostics" in markdown
    assert "## Coverage vs Scoring Readiness" in markdown
    assert all(method == "GET" for method, _url in calls)


def test_cli_warning_diagnostics_exit_zero_but_real_errors_exit_one() -> None:
    warning_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="125",
        company_names="",
        source="company-id-list",
        model_run_id=None,
        as_of_date=None,
        limit=50,
        use_duplicate_mapping=False,
    )
    error_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="",
        company_names="",
        source="company-id-list",
        model_run_id=None,
        as_of_date=None,
        limit=50,
        use_duplicate_mapping=False,
    )

    def fake_http(method: str, url: str, payload=None):
        if url.endswith("/stats"):
            return import_script.HttpResult(ok=False, status_code=404, data={})
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={
                "company_id": 125,
                "company_name": "ТМК",
                "canonical_company_id": 125,
                "canonical_company_name": "ТМК",
                "has_financial_report": False,
                "raw_fields": {"present": [], "missing": []},
                "derived_metrics": {"computed": {}, "fallback": {}, "missing": []},
                "signal_explanation": {"signal": "insufficient_data"},
                "recommended_next_fields": [],
                "safe_for_feature_pipeline": False,
                "safe_for_risk_scoring": False,
                "risk_scoring_readiness": "not_ready",
            },
        )

    warning_report, warning_exit = diagnostics_script.run_diagnostics(
        warning_args,
        http_request=fake_http,
    )
    error_report, error_exit = diagnostics_script.run_diagnostics(
        error_args,
        http_request=fake_http,
    )

    assert warning_report["status"] == "warning"
    assert warning_exit == 0
    assert error_report["status"] == "failed"
    assert error_exit == 1
