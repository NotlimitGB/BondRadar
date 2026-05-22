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


def test_batch_summary_for_tmk_like_partial_report(
    db_session: Session,
) -> None:
    company = create_company(db_session, "BTMK", name="Batch TMK")
    create_report(db_session, company)
    db_session.commit()

    report = FinancialScoringPreviewService(
        db_session
    ).get_batch_financial_scoring_preview([company.id])
    summary = report["summary"]

    assert report["company_count"] == 1
    assert summary["has_report_count"] == 1
    assert summary["missing_report_count"] == 0
    assert summary["ready_count"] == 0
    assert summary["partial_count"] == 1
    assert summary["not_ready_count"] == 0
    assert summary["negative_factor_count"] > 0
    assert summary["fallback_metric_company_count"] == 1
    assert summary["preview_only_adjustment_count"] == 0
    assert report["missing_fields_summary"]["interest_expense"] == 1
    assert report["missing_fields_summary"]["net_debt"] == 1
    assert report["risk_factor_summary"]["gross_debt_to_ebitda"]["high"] == 1
    assert report["risk_factor_summary"]["net_debt_to_ebitda_fallback"]["elevated"] == 1
    assert report["risk_factor_summary"]["interest_coverage"]["warning"] == 1
    assert report["top_negative_preview_companies"][0]["company_id"] == company.id
    assert report["read_only"] is True
    assert report["would_mutate_scores"] is False


def test_batch_summary_for_missing_report_company(
    db_session: Session,
) -> None:
    company = create_company(db_session, "BMISS")
    db_session.commit()

    report = FinancialScoringPreviewService(
        db_session
    ).get_batch_financial_scoring_preview([company.id])
    summary = report["summary"]

    assert summary["has_report_count"] == 0
    assert summary["missing_report_count"] == 1
    assert summary["not_ready_count"] == 1
    assert summary["negative_factor_count"] == 0
    assert report["missing_fields_summary"] == {}
    assert report["risk_factor_summary"] == {}
    assert report["companies"][0]["financial_risk_factors"] == []
    assert "financial report is missing" in report["companies"][0]["blocking_reasons"]


def test_mixed_batch_summary_counts_and_top_negative_sorting(
    db_session: Session,
) -> None:
    missing = create_company(db_session, "MISS")
    partial = create_company(db_session, "PART")
    ready = create_company(db_session, "RDY")
    create_report(db_session, partial)
    create_report(
        db_session,
        ready,
        net_debt=Decimal("100"),
        total_debt=Decimal("200"),
        cash=Decimal("100"),
        ebitda=Decimal("200"),
        equity=Decimal("500"),
        short_term_debt=Decimal("40"),
        operating_cash_flow=Decimal("80"),
        net_profit=Decimal("80"),
        interest_expense=Decimal("50"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db_session.commit()

    report = FinancialScoringPreviewService(
        db_session
    ).get_batch_financial_scoring_preview([missing.id, partial.id, ready.id])
    summary = report["summary"]

    assert summary["has_report_count"] == 2
    assert summary["missing_report_count"] == 1
    assert summary["ready_count"] == 1
    assert summary["partial_count"] == 1
    assert summary["not_ready_count"] == 1
    assert report["missing_fields_summary"]["interest_expense"] == 1
    assert report["missing_fields_summary"]["net_debt"] == 1
    assert report["top_negative_preview_companies"][0]["company_id"] == partial.id
    assert report["top_negative_preview_companies"][0]["high_factor_count"] >= 1


def test_batch_api_endpoint_returns_stable_shape_and_rejects_empty_list(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "BAPI")
    create_report(db_session, company)
    db_session.commit()

    response = client.post(
        "/api/financial-reports/scoring-preview/batch",
        json={
            "company_ids": [company.id],
            "include_diagnostics": False,
            "include_bond_context": False,
        },
    )
    empty_response = client.post(
        "/api/financial-reports/scoring-preview/batch",
        json={"company_ids": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_count"] == 1
    assert payload["summary"]["has_report_count"] == 1
    assert payload["companies"][0]["diagnostics"] is None
    assert payload["companies"][0]["bond_context"] is None
    assert payload["read_only"] is True
    assert payload["dry_run_only"] is True
    assert payload["would_mutate_scores"] is False
    assert empty_response.status_code == 422


def test_cli_script_writes_json_markdown_and_uses_only_safe_endpoints(
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
        if url.endswith("/api/financial-reports/scoring-preview/batch"):
            assert method == "POST"
            assert payload == {
                "company_ids": [125],
                "include_diagnostics": True,
                "include_bond_context": True,
            }
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "passed",
                    "company_count": 1,
                    "summary": {
                        "has_report_count": 1,
                        "missing_report_count": 0,
                        "ready_count": 0,
                        "partial_count": 1,
                        "not_ready_count": 0,
                        "negative_factor_count": 1,
                        "fallback_metric_company_count": 1,
                        "preview_only_adjustment_count": 0,
                    },
                    "top_negative_preview_companies": [
                        {
                            "company_id": 125,
                            "company_name": "TMK",
                            "risk_scoring_readiness": "partial",
                            "negative_factor_count": 1,
                            "high_factor_count": 1,
                            "blocking_reasons": ["interest_expense is missing"],
                        }
                    ],
                    "missing_fields_summary": {
                        "interest_expense": 1,
                    },
                    "risk_factor_summary": {
                        "gross_debt_to_ebitda": {"high": 1},
                    },
                    "companies": [
                        {
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
                        }
                    ],
                    "read_only": True,
                    "dry_run_only": True,
                    "import_executed": False,
                    "paper_trading_called": False,
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
    assert "## Batch Summary" in markdown
    assert "## Suggested Adjustments" in markdown
    assert all(
        method == "GET" or url.endswith("/api/financial-reports/scoring-preview/batch")
        for method, url in calls
    )


def test_cli_mixed_source_uses_safe_target_sources_and_batch_endpoint(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "batch.json"
    markdown_output = tmp_path / "batch.md"
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/paper-trading" not in url
        assert "/ingest" not in url
        assert "/api/financial-reports/preview" not in url
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
        if "/api/ml/predictions?" in url:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "predictions": [
                        {
                            "bond_id": 9001,
                            "company_id": 125,
                            "probability_positive": 0.9,
                        }
                    ]
                },
            )
        if url.endswith("/api/bonds/9001"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "id": 9001,
                    "company_id": 125,
                    "secid": "TMK001",
                    "name": "TMK Bond",
                    "isin": "RU000TMK001",
                },
            )
        if "/api/bonds?skip=" in url:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=[
                    {
                        "id": 9001,
                        "company_id": 125,
                        "secid": "TMK001",
                        "name": "TMK Bond",
                        "isin": "RU000TMK001",
                    }
                ],
            )
        if url.endswith("/api/companies/125"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "id": 125,
                    "name": "TMK",
                    "ticker": "TMK",
                    "inn": "7710373095",
                },
            )
        if url.endswith("/api/companies/125/financial-report-coverage"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "has_financial_report": True,
                    "canonical_has_financial_report": True,
                    "coverage_effective_status": "covered_by_canonical",
                    "needs_financial_report": False,
                },
            )
        if url.endswith("/api/companies/identity/profiles/125"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "identity_status": "matched",
                    "confidence": "high",
                    "legal_name": "TMK Synthetic",
                    "short_name": "TMK",
                },
            )
        if url.endswith("/api/companies/identity/duplicate-candidates"):
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if url.endswith("/api/companies/identity/duplicate-diagnostics"):
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if url.endswith("/api/financial-reports/scoring-preview/batch"):
            assert payload["company_ids"] == [125]
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "passed",
                    "company_count": 1,
                    "summary": {
                        "has_report_count": 1,
                        "missing_report_count": 0,
                        "ready_count": 0,
                        "partial_count": 1,
                        "not_ready_count": 0,
                        "negative_factor_count": 1,
                        "fallback_metric_company_count": 1,
                        "preview_only_adjustment_count": 0,
                    },
                    "top_negative_preview_companies": [],
                    "missing_fields_summary": {},
                    "risk_factor_summary": {},
                    "companies": [
                        {
                            "company_id": 125,
                            "company_name": "TMK",
                            "canonical_company_id": 125,
                            "canonical_company_name": "TMK",
                            "has_financial_report": True,
                            "latest_report": {},
                            "diagnostics_readiness": {
                                "risk_scoring_readiness": "partial",
                            },
                            "financial_risk_factors": [],
                            "fallback_metrics_used": {},
                            "blocking_reasons": [],
                            "recommended_next_fields": [],
                            "suggested_adjustments": {
                                "risk_penalty_points": 0,
                                "risk_penalty_label": "preview_only",
                                "score_adjustment_points": 0,
                                "score_adjustment_label": "preview_only",
                            },
                            "dry_run_only": True,
                            "would_mutate_scores": False,
                            "would_trigger_paper_trading": False,
                        }
                    ],
                    "read_only": True,
                    "dry_run_only": True,
                    "import_executed": False,
                    "paper_trading_called": False,
                    "would_mutate_scores": False,
                    "would_trigger_paper_trading": False,
                },
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = preview_script.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--source",
            "mixed",
            "--model-run-id",
            "2",
            "--as-of-date",
            "2026-05-19",
            "--limit",
            "50",
            "--use-duplicate-mapping",
            "--rollup-duplicates",
            "--include-duplicate-members",
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
    assert report["target_source_report"]["safe_sources"] == [
        "top-predictions",
        "bond-universe",
    ]
    assert report["requested_company_ids"] == [125]
    assert any("/api/ml/predictions?" in url for _method, url in calls)
    assert any("/api/bonds?skip=" in url for _method, url in calls)
    assert not any("/paper-trading" in url for _method, url in calls)
    assert "## Target Source" in markdown_output.read_text(encoding="utf-8")


def test_cli_exits_nonzero_only_for_real_errors() -> None:
    warning_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="125",
        company_names="",
        source="company-id-list",
        limit=50,
        model_run_id=None,
        as_of_date=None,
        use_duplicate_mapping=False,
        rollup_duplicates=False,
        include_duplicate_members=False,
    )
    error_args = argparse.Namespace(
        backend_url="http://testserver",
        company_ids="",
        company_names="",
        source="company-id-list",
        limit=50,
        model_run_id=None,
        as_of_date=None,
        use_duplicate_mapping=False,
        rollup_duplicates=False,
        include_duplicate_members=False,
    )

    def fake_http(method: str, url: str, payload=None):
        if url.endswith("/stats"):
            return import_script.HttpResult(ok=False, status_code=404, data={})
        if url.endswith("/scoring-preview/batch"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "passed",
                    "company_count": 1,
                    "summary": {},
                    "top_negative_preview_companies": [],
                    "missing_fields_summary": {},
                    "risk_factor_summary": {},
                    "companies": [
                        {
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
                        }
                    ],
                    "read_only": True,
                    "dry_run_only": True,
                    "import_executed": False,
                    "paper_trading_called": False,
                    "would_mutate_scores": False,
                    "would_trigger_paper_trading": False,
                },
            )
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
