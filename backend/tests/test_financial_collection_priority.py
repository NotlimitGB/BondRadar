from __future__ import annotations

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
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.services.financial_collection_priority_service import (
    FinancialCollectionPriorityService,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_collection_priority_queue as queue_script  # noqa: E402
import financial_report_import as import_script  # noqa: E402


DEFAULT_INN = object()


def create_company(
    db: Session,
    ticker: str,
    *,
    name: str | None = None,
    inn: str | None | object = DEFAULT_INN,
) -> Company:
    company_inn = (
        f"77{abs(hash(ticker)) % 100000000:08d}" if inn is DEFAULT_INN else inn
    )
    company = Company(
        name=name or f"{ticker} Company",
        ticker=ticker,
        inn=company_inn,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_profile(
    db: Session,
    company: Company,
    *,
    legal_name: str | None = None,
    short_name: str | None = None,
    issuer_role: str = "legal_issuer",
    identity_status: str = "matched",
) -> CompanyIdentityProfile:
    profile = CompanyIdentityProfile(
        company_id=company.id,
        legal_name=legal_name,
        short_name=short_name,
        inn=company.inn,
        ogrn="1027700000000" if company.inn else None,
        issuer_role=issuer_role,
        identity_status=identity_status,
        identity_source="manual_review",
        review_status="reviewed",
    )
    db.add(profile)
    db.flush()
    return profile


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
    net_debt: Decimal | None = None,
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
        revenue=Decimal("404359000000"),
        ebitda=Decimal("74677000000"),
        net_debt=net_debt,
        total_debt=Decimal("390544000000"),
        cash=Decimal("95772000000"),
        equity=Decimal("32596000000"),
        short_term_debt=Decimal("144888000000"),
        operating_cash_flow=Decimal("19095000000"),
        net_profit=Decimal("-24458000000"),
        interest_expense=interest_expense,
    )
    db.add(report)
    db.flush()
    return report


def test_missing_report_corporate_issuer_ranks_high(
    db_session: Session,
) -> None:
    company = create_company(db_session, "CORP", name="ПАО Корпоративный Эмитент")
    create_profile(db_session, company, legal_name="ПАО Корпоративный Эмитент")
    create_bond(db_session, company, "CORP001")
    db_session.commit()

    report = FinancialCollectionPriorityService(
        db_session
    ).get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["top-predictions", "bond-universe"]},
    )
    row = report["priority_queue"][0]

    assert row["issuer_type"] == "corporate"
    assert row["has_financial_report"] is False
    assert row["risk_scoring_readiness"] == "not_ready"
    assert row["priority_level"] == "high"
    assert row["priority_score"] == 100
    assert row["recommended_collection"]["collection_type"] == "full_annual_ifrs_report"
    assert "interest_expense" in row["recommended_collection"]["required_fields"]
    assert row["safety"]["read_only"] is True
    assert row["safety"]["would_import_report"] is False


def test_already_covered_partial_issuer_is_reported_separately(
    db_session: Session,
) -> None:
    company = create_company(db_session, "TMK", name="ТМК")
    create_profile(db_session, company, legal_name="ПАО ТМК", short_name="ТМК")
    create_report(db_session, company)
    db_session.commit()

    report = FinancialCollectionPriorityService(
        db_session
    ).get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["manual-id"]},
        include_covered=True,
    )

    assert report["priority_queue"] == []
    assert report["summary"]["already_covered_count"] == 1
    covered = report["already_covered"][0]
    assert covered["company_id"] == company.id
    assert covered["risk_scoring_readiness"] == "partial"
    assert "interest_expense" in covered["recommended_next_fields"]
    assert "net_debt" in covered["recommended_next_fields"]


def test_government_like_issuer_is_excluded_or_deprioritized(
    db_session: Session,
) -> None:
    company = create_company(db_session, "OFZ", name="Минфин Российская Федерация", inn=None)
    create_bond(db_session, company, "SU26240")
    db_session.commit()
    service = FinancialCollectionPriorityService(db_session)

    excluded_report = service.get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["bond-universe"]},
        exclude_government_like=True,
    )
    deprioritized_report = service.get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["bond-universe"]},
        exclude_government_like=False,
    )

    assert excluded_report["priority_queue"] == []
    assert excluded_report["excluded_or_deprioritized"][0]["issuer_type"] == "government_like"
    row = deprioritized_report["priority_queue"][0]
    assert row["issuer_type"] == "government_like"
    assert row["priority_score"] < 40
    assert row["priority_level"] == "low"


def test_state_owned_corporate_issuer_is_not_excluded(
    db_session: Session,
) -> None:
    company = create_company(db_session, "RZD", name="ОАО РЖД")
    create_profile(db_session, company, legal_name="ОАО РЖД", short_name="РЖД")
    create_bond(db_session, company, "RZD001")
    db_session.commit()

    report = FinancialCollectionPriorityService(
        db_session
    ).get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["top-predictions"]},
        exclude_government_like=True,
    )

    assert report["excluded_or_deprioritized"] == []
    assert report["priority_queue"][0]["issuer_type"] == "corporate"
    assert report["priority_queue"][0]["priority_level"] == "high"


def test_source_presence_and_deterministic_ranking(
    db_session: Session,
) -> None:
    both = create_company(db_session, "BOTH", name="ПАО Источник Оба")
    top = create_company(db_session, "TOP", name="ПАО Источник Топ")
    manual = create_company(db_session, "MAN", name="ПАО Источник Ручной")
    for company in (both, top, manual):
        create_profile(db_session, company, legal_name=company.name)
        create_bond(db_session, company, f"{company.ticker}001")
    db_session.commit()

    report = FinancialCollectionPriorityService(
        db_session
    ).get_batch_collection_priority(
        [manual.id, top.id, both.id],
        source_presence={
            manual.id: ["manual-id"],
            top.id: ["top-predictions"],
            both.id: ["top-predictions", "bond-universe"],
        },
    )
    queue = report["priority_queue"]

    assert queue[0]["company_id"] == both.id
    assert queue[0]["source_presence"]["source_labels"] == [
        "top-predictions",
        "bond-universe",
    ]
    assert queue[1]["company_id"] == top.id
    assert queue[2]["source_presence"]["source_labels"] == ["manual-id"]


def test_batch_summary_counts(
    db_session: Session,
) -> None:
    missing = create_company(db_session, "MISSQ", name="ПАО Нет Отчета")
    partial = create_company(db_session, "PARTQ", name="ПАО Частичный")
    ready = create_company(db_session, "READYQ", name="ПАО Готовый")
    government = create_company(db_session, "GOVQ", name="Минфин РФ", inn=None)
    unknown = create_company(db_session, "UNKQ", name="Unknown Issuer", inn=None)
    for company in (missing, partial, ready):
        create_profile(db_session, company, legal_name=company.name)
    create_report(db_session, partial)
    create_report(
        db_session,
        ready,
        net_debt=Decimal("100"),
        interest_expense=Decimal("50"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db_session.commit()

    report = FinancialCollectionPriorityService(
        db_session
    ).get_batch_collection_priority(
        [missing.id, partial.id, ready.id, government.id, unknown.id],
        include_covered=True,
        exclude_government_like=True,
    )
    summary = report["summary"]

    assert summary["high_priority_count"] >= 1
    assert summary["already_covered_count"] == 2
    assert summary["excluded_count"] == 1
    assert summary["missing_report_count"] == 3
    assert summary["partial_report_count"] == 1
    assert summary["ready_report_count"] == 1
    assert summary["corporate_count"] == 3
    assert summary["government_like_count"] == 1
    assert summary["unknown_issuer_type_count"] == 1


def test_collection_priority_api_respects_flags_and_rejects_empty(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "APIQ", name="ПАО API Queue")
    create_profile(db_session, company, legal_name=company.name)
    create_report(db_session, company)
    db_session.commit()

    included = client.post(
        "/api/financial-reports/collection-priority/batch",
        json={
            "company_ids": [company.id],
            "source_presence": {str(company.id): ["manual-id"]},
            "include_covered": True,
            "exclude_government_like": True,
        },
    )
    not_included = client.post(
        "/api/financial-reports/collection-priority/batch",
        json={
            "company_ids": [company.id],
            "include_covered": False,
            "exclude_government_like": True,
        },
    )
    empty = client.post(
        "/api/financial-reports/collection-priority/batch",
        json={"company_ids": []},
    )

    assert included.status_code == 200
    assert included.json()["already_covered"][0]["company_id"] == company.id
    assert not_included.status_code == 200
    assert not_included.json()["already_covered"] == []
    assert empty.status_code == 422


def test_cli_writes_json_markdown_and_uses_only_safe_endpoints(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "queue.json"
    markdown_output = tmp_path / "queue.md"
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
        if url.endswith("/api/financial-reports/collection-priority/batch"):
            assert method == "POST"
            assert payload == {
                "company_ids": [125],
                "source_presence": {"125": ["manual-id"]},
                "include_covered": True,
                "exclude_government_like": True,
            }
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=_fake_priority_response(125),
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = queue_script.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--company-ids",
            "125",
            "--include-covered",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    report, exit_code = queue_script.run_queue(args, http_request=fake_http)
    queue_script.write_json_report(report, json_output)
    queue_script.write_markdown_report(report, markdown_output)

    assert exit_code == 0
    assert report["read_only"] is True
    assert report["dry_run_only"] is True
    assert report["financial_reports_count"] == 1
    assert json.loads(json_output.read_text(encoding="utf-8"))["queue_count"] == 1
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Financial Report Collection Priority Queue" in markdown
    assert "## Top Priority Queue" in markdown
    assert all(
        method == "GET"
        or url.endswith("/api/financial-reports/collection-priority/batch")
        for method, url in calls
    )


def test_cli_mixed_source_preserves_safe_source_labels(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "mixed_queue.json"
    markdown_output = tmp_path / "mixed_queue.md"
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
        if url.endswith("/api/bonds/9001") or "/api/bonds?skip=" in url:
            data = {
                "id": 9001,
                "company_id": 125,
                "secid": "TMK001",
                "name": "TMK Bond",
                "isin": "RU000TMK001",
            }
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=[data] if "/api/bonds?skip=" in url else data,
            )
        if url.endswith("/api/companies/125"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"id": 125, "name": "TMK", "ticker": "TMK", "inn": "7710373095"},
            )
        if url.endswith("/api/companies/identity/profiles/125"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "identity_status": "matched",
                    "identity_confidence": 0.95,
                    "legal_name": "ПАО ТМК",
                    "short_name": "ТМК",
                },
            )
        if url.endswith("/api/companies/125/reports?limit=1"):
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if url.endswith("/api/companies/125/financial-report-coverage"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "has_financial_report": False,
                    "coverage_effective_status": "missing_report",
                },
            )
        if url.endswith("/api/companies/identity/duplicate-candidates"):
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if url.endswith("/api/companies/identity/duplicate-diagnostics"):
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if url.endswith("/api/financial-reports/collection-priority/batch"):
            assert payload["company_ids"] == [125]
            assert payload["source_presence"] == {
                "125": ["top-predictions", "bond-universe"]
            }
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=_fake_priority_response(125),
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = queue_script.parse_args(
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
            "--include-covered",
            "--exclude-government-like",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    report, exit_code = queue_script.run_queue(args, http_request=fake_http)

    assert exit_code == 0
    assert report["target_source_report"]["safe_sources"] == [
        "top-predictions",
        "bond-universe",
    ]
    assert report["requested_source_presence"] == {
        "125": ["top-predictions", "bond-universe"]
    }
    assert any("/api/ml/predictions?" in url for _method, url in calls)
    assert any("/api/bonds?skip=" in url for _method, url in calls)
    assert not any("/paper-trading" in url for _method, url in calls)


def test_priority_service_does_not_mutate_financial_reports(
    db_session: Session,
) -> None:
    company = create_company(db_session, "SAFEQ", name="ПАО Безопасность")
    before = db_session.scalar(select(func.count()).select_from(FinancialReport))

    FinancialCollectionPriorityService(db_session).get_batch_collection_priority(
        [company.id],
        source_presence={company.id: ["manual-id"]},
    )
    after = db_session.scalar(select(func.count()).select_from(FinancialReport))

    assert after == before


def _fake_priority_response(company_id: int) -> dict:
    return {
        "status": "passed",
        "company_count": 1,
        "queue_count": 1,
        "summary": {
            "high_priority_count": 1,
            "medium_priority_count": 0,
            "low_priority_count": 0,
            "already_covered_count": 0,
            "excluded_count": 0,
            "corporate_count": 1,
            "government_like_count": 0,
            "unknown_issuer_type_count": 0,
            "missing_report_count": 1,
            "partial_report_count": 0,
            "ready_report_count": 0,
        },
        "priority_queue": [
            {
                "rank": 1,
                "company_id": company_id,
                "company_name": "TMK",
                "canonical_company_id": company_id,
                "canonical_company_name": "TMK",
                "issuer_type": "corporate",
                "classification_confidence": "medium",
                "priority_level": "high",
                "priority_score": 90,
                "has_financial_report": False,
                "risk_scoring_readiness": "not_ready",
                "coverage_status": "missing_report",
                "source_presence": {"source_labels": ["manual-id"]},
                "bond_context": {"bond_count": 1, "sample_bonds": []},
                "recommended_collection": {
                    "collection_type": "full_annual_ifrs_report",
                    "period_preference": "latest_annual",
                    "required_fields": ["revenue", "ebitda"],
                    "optional_fields": ["debt_to_ebitda"],
                },
                "priority_reasons": ["missing financial report"],
                "blocking_reasons": ["financial report is missing"],
                "safety": {
                    "read_only": True,
                    "would_import_report": False,
                    "would_mutate_scores": False,
                    "would_trigger_paper_trading": False,
                },
            }
        ],
        "already_covered": [],
        "excluded_or_deprioritized": [],
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
