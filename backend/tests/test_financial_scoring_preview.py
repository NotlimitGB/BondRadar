from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.services.financial_scoring_preview_service import (
    FinancialScoringPreviewService,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_import as import_script  # noqa: E402
import financial_scoring_preview as preview_script  # noqa: E402


def create_company(db: Session, ticker: str, *, name: str | None = None) -> Company:
    company = Company(
        name=name or f"{ticker} Company",
        ticker=ticker,
        inn=f"78{abs(hash(ticker)) % 100000000:08d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(db: Session, company: Company, secid: str = "RU000TEST") -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"{secid[:12]:<12}".replace(" ", "0"),
        secid=secid,
        name=f"{company.name} Bond",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def create_report(
    db: Session,
    company: Company,
    *,
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
    signal: str = AnalysisSignal.INSUFFICIENT_DATA.value,
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=2025,
        period_quarter=0,
        period_start_date=date(2025, 1, 1),
        period_end_date=date(2025, 12, 31),
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
    )
    db.add(report)
    db.flush()
    return report


def factor_by_name(preview: dict, name: str) -> dict:
    return {
        factor["factor"]: factor
        for factor in preview.get("financial_risk_factors", [])
    }[name]


def test_company_without_report_has_no_financial_risk_impact(
    db_session: Session,
) -> None:
    company = create_company(db_session, "NRPT")
    db_session.commit()

    preview = FinancialScoringPreviewService(
        db_session
    ).get_company_financial_scoring_preview(company.id)

    assert preview["has_financial_report"] is False
    assert preview["financial_risk_factors"] == []
    assert preview["diagnostics_readiness"]["safe_for_feature_pipeline"] is False
    assert preview["diagnostics_readiness"]["safe_for_risk_scoring"] is False
    assert preview["dry_run_only"] is True
    assert preview["would_mutate_scores"] is False


def test_tmk_like_partial_report_has_preview_only_risk_factors(
    db_session: Session,
) -> None:
    company = create_company(db_session, "TMK", name="TMK")
    create_bond(db_session, company, "TMK001")
    create_report(db_session, company)
    db_session.commit()

    preview = FinancialScoringPreviewService(
        db_session
    ).get_company_financial_scoring_preview(company.id)
    factors = {item["factor"]: item for item in preview["financial_risk_factors"]}

    assert preview["has_financial_report"] is True
    assert preview["diagnostics_readiness"]["safe_for_feature_pipeline"] is True
    assert preview["diagnostics_readiness"]["safe_for_risk_scoring"] is False
    assert preview["diagnostics_readiness"]["risk_scoring_readiness"] == "partial"
    assert factors["gross_debt_to_ebitda"]["severity"] == "high"
    assert factors["gross_debt_to_ebitda"]["impact"] == "negative"
    assert factors["net_debt_to_ebitda_fallback"]["severity"] == "elevated"
    assert factors["net_debt_to_ebitda_fallback"]["fallback"] is True
    assert factors["interest_coverage"]["severity"] == "warning"
    assert factors["interest_coverage"]["impact"] == "unknown"
    assert factors["net_profit"]["value"] == -24458000000
    assert factors["net_profit"]["severity"] == "warning"
    assert preview["fallback_metrics_used"]["net_debt_fallback"] == 294772000000
    assert "interest_expense is missing" in preview["blocking_reasons"]
    assert "safe_for_risk_scoring is false" in preview["blocking_reasons"]
    assert preview["recommended_next_fields"][:2] == ["interest_expense", "net_debt"]
    assert preview["suggested_adjustments"] == {
        "risk_penalty_points": 0,
        "risk_penalty_label": "preview_only",
        "score_adjustment_points": 0,
        "score_adjustment_label": "preview_only",
    }
    assert preview["bond_context"]["bond_count"] == 1


def test_full_ready_report_computes_interest_coverage_but_keeps_zero_adjustments(
    db_session: Session,
) -> None:
    company = create_company(db_session, "FULL")
    create_report(
        db_session,
        company,
        net_debt=Decimal("300"),
        total_debt=Decimal("400"),
        cash=Decimal("100"),
        ebitda=Decimal("200"),
        equity=Decimal("500"),
        net_profit=Decimal("80"),
        interest_expense=Decimal("50"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db_session.commit()

    preview = FinancialScoringPreviewService(
        db_session
    ).get_company_financial_scoring_preview(company.id)
    interest = factor_by_name(preview, "interest_coverage")

    assert preview["diagnostics_readiness"]["safe_for_risk_scoring"] is True
    assert preview["diagnostics_readiness"]["risk_scoring_readiness"] == "ready"
    assert interest["value"] == 4
    assert interest["severity"] == "computed"
    assert preview["suggested_adjustments"]["risk_penalty_points"] == 0
    assert preview["suggested_adjustments"]["score_adjustment_points"] == 0


def test_negative_or_zero_ebitda_is_critical_and_blocks_readiness(
    db_session: Session,
) -> None:
    company = create_company(db_session, "ZERO")
    create_report(
        db_session,
        company,
        ebitda=Decimal("0"),
        interest_expense=Decimal("50"),
    )
    db_session.commit()

    preview = FinancialScoringPreviewService(
        db_session
    ).get_company_financial_scoring_preview(company.id)
    ebitda = factor_by_name(preview, "ebitda")

    assert preview["diagnostics_readiness"]["safe_for_risk_scoring"] is False
    assert preview["diagnostics_readiness"]["risk_scoring_readiness"] == "not_ready"
    assert ebitda["severity"] == "critical"
    assert "ebitda is zero or negative" in preview["blocking_reasons"]
    assert any(
        item["metric"] == "gross_debt_to_ebitda"
        and item["reason"] == "ebitda is zero"
        for item in preview["diagnostics"]["derived_metrics"]["missing"]
    )


def test_duplicate_candidate_preview_uses_canonical_report_and_bonds(
    db_session: Session,
) -> None:
    canonical = create_company(db_session, "CAN", name="Canonical")
    duplicate = create_company(db_session, "DUP", name="Unknown issuer for RU000DUP")
    create_bond(db_session, canonical, "CAN001")
    create_bond(db_session, duplicate, "DUP001")
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

    preview = FinancialScoringPreviewService(
        db_session
    ).get_company_financial_scoring_preview(duplicate.id)

    assert preview["company_id"] == duplicate.id
    assert preview["canonical_company_id"] == canonical.id
    assert preview["latest_report"]["company_id"] == canonical.id
    assert preview["bond_context"]["bond_count"] == 2
    assert (
        db_session.scalar(
            select(func.count()).select_from(FinancialReport).where(
                FinancialReport.company_id == duplicate.id
            )
        )
        == 0
    )


def test_scoring_preview_api_returns_flags_and_query_options(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "API")
    create_report(db_session, company)
    db_session.commit()

    response = client.get(
        f"/api/financial-reports/scoring-preview/company/{company.id}",
        params={"include_diagnostics": "false", "include_bond_context": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_id"] == company.id
    assert payload["diagnostics"] is None
    assert payload["bond_context"] is None
    assert payload["dry_run_only"] is True
    assert payload["would_mutate_scores"] is False


def test_cli_script_writes_json_markdown_and_uses_only_get_endpoints(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "preview.json"
    markdown_output = tmp_path / "preview.md"
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/ingest" not in url
        assert "/preview" not in url.replace("scoring-preview", "scoring_preview")
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
        if "/api/financial-reports/scoring-preview/company/125" in url:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "company_id": 125,
                    "company_name": "TMK",
                    "canonical_company_id": 125,
                    "canonical_company_name": "TMK",
                    "has_financial_report": True,
                    "latest_report": {
                        "id": 1,
                        "period_year": 2025,
                        "period_quarter": 0,
                        "signal": "insufficient_data",
                        "source": "official_issuer_report",
                    },
                    "diagnostics_readiness": {
                        "safe_for_feature_pipeline": True,
                        "safe_for_risk_scoring": False,
                        "risk_scoring_readiness": "partial",
                    },
                    "financial_risk_factors": [
                        {
                            "factor": "gross_debt_to_ebitda",
                            "value": 5.2,
                            "severity": "high",
                            "impact": "negative",
                            "reason": "gross debt to EBITDA is above conservative threshold",
                        }
                    ],
                    "fallback_metrics_used": {
                        "net_debt_to_ebitda_fallback": 3.9,
                    },
                    "blocking_reasons": ["interest_expense is missing"],
                    "recommended_next_fields": ["interest_expense", "net_debt"],
                    "suggested_adjustments": {
                        "risk_penalty_points": 0,
                        "risk_penalty_label": "preview_only",
                        "score_adjustment_points": 0,
                        "score_adjustment_label": "preview_only",
                    },
                    "dry_run_only": True,
                    "would_mutate_scores": False,
                    "would_trigger_paper_trading": False,
                },
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = preview_script.parse_args(
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
    report, exit_code = preview_script.run_preview(args, http_request=fake_http)
    preview_script.write_json_report(report, json_output)
    preview_script.write_markdown_report(report, markdown_output)

    assert exit_code == 0
    assert report["read_only"] is True
    assert report["dry_run_only"] is True
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False
    assert report["financial_reports_count"] == 1
    assert json.loads(json_output.read_text(encoding="utf-8"))["company_count"] == 1
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Financial Scoring Preview" in markdown
    assert "## Suggested Adjustments" in markdown
    assert all(method == "GET" for method, _url in calls)


def test_cli_exits_nonzero_only_for_real_errors() -> None:
    warning_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="125",
        company_names="",
    )
    error_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="",
        company_names="",
    )

    def fake_http(method: str, url: str, payload=None):
        if url.endswith("/stats"):
            return import_script.HttpResult(ok=False, status_code=404, data={})
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={
                "company_id": 125,
                "company_name": "TMK",
                "canonical_company_id": 125,
                "canonical_company_name": "TMK",
                "has_financial_report": False,
                "financial_risk_factors": [],
                "diagnostics_readiness": {
                    "safe_for_feature_pipeline": False,
                    "safe_for_risk_scoring": False,
                    "risk_scoring_readiness": "not_ready",
                },
                "suggested_adjustments": {
                    "risk_penalty_points": 0,
                    "risk_penalty_label": "preview_only",
                    "score_adjustment_points": 0,
                    "score_adjustment_label": "preview_only",
                },
                "blocking_reasons": ["financial report is missing"],
                "recommended_next_fields": [],
                "dry_run_only": True,
                "would_mutate_scores": False,
                "would_trigger_paper_trading": False,
            },
        )

    warning_report, warning_exit = preview_script.run_preview(
        warning_args,
        http_request=fake_http,
    )
    error_report, error_exit = preview_script.run_preview(
        error_args,
        http_request=fake_http,
    )

    assert warning_report["status"] == "warning"
    assert warning_exit == 0
    assert error_report["status"] == "failed"
    assert error_exit == 1
