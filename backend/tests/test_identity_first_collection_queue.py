from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.services.identity_first_collection_service import IdentityFirstCollectionService


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_import as import_script  # noqa: E402
import identity_first_collection_queue as identity_script  # noqa: E402


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
    identity_confidence: Decimal | None = Decimal("0.9000"),
) -> CompanyIdentityProfile:
    profile = CompanyIdentityProfile(
        company_id=company.id,
        legal_name=legal_name,
        short_name=short_name,
        inn=company.inn,
        ogrn="1027700000000" if company.inn else None,
        issuer_role=issuer_role,
        identity_status=identity_status,
        identity_confidence=identity_confidence,
        identity_source="manual_review",
        review_status="reviewed",
    )
    db.add(profile)
    db.flush()
    return profile


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


def test_ready_corporate_issuer_goes_to_collection_ready(
    db_session: Session,
) -> None:
    company = create_company(db_session, "RZDQ", name="PJSC RZD")
    create_profile(db_session, company, legal_name="PJSC RZD", short_name="RZD")
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue(
        [company.id],
        source_presence={company.id: ["top-predictions", "bond-universe"]},
    )

    assert report["collection_ready_count"] == 1
    assert report["identity_review_required_count"] == 0
    row = report["collection_ready"][0]
    assert row["company_id"] == company.id
    assert row["issuer_type"] == "corporate"
    assert row["identity_status"] == "matched"
    assert row["identity_readiness"] == "ready_for_financial_collection"
    assert row["operator_next_action"] == "collect_official_financial_report"


def test_unknown_issuer_goes_to_identity_review(
    db_session: Session,
) -> None:
    company = create_company(
        db_session,
        "UNK94",
        name="Unknown issuer for RU000A0JWK74",
        inn=None,
    )
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue([company.id])

    assert report["collection_ready"] == []
    assert report["identity_review_required_count"] == 1
    row = report["identity_review_required"][0]
    assert row["company_id"] == company.id
    assert row["issuer_type"] == "unknown"
    assert "generated unknown issuer name" in row["review_reasons"]
    assert "legal_name" in row["recommended_identity_fields"]
    assert "inn" in row["recommended_identity_fields"]
    assert "ogrn" in row["recommended_identity_fields"]
    assert "official_source_url" in row["recommended_identity_fields"]
    assert (
        row["operator_next_action"]
        == "review_issuer_identity_before_financial_collection"
    )


def test_weak_identity_goes_to_identity_review(
    db_session: Session,
) -> None:
    company = create_company(db_session, "WEAK94", name="PJSC Weak Identity")
    create_profile(
        db_session,
        company,
        legal_name="PJSC Weak Identity",
        identity_status="weak",
        identity_confidence=Decimal("0.5000"),
    )
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue([company.id])
    row = report["identity_review_required"][0]

    assert report["collection_ready"] == []
    assert "identity status is weak or missing" in row["review_reasons"]
    assert "identity confidence is below 0.8" in row["review_reasons"]


def test_already_covered_partial_tmk_stays_covered(
    db_session: Session,
) -> None:
    company = create_company(db_session, "TMK94", name="PJSC TMK")
    create_profile(db_session, company, legal_name="PJSC TMK", short_name="TMK")
    create_report(db_session, company)
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue([company.id], include_covered=True)

    assert report["collection_ready"] == []
    assert report["identity_review_required"] == []
    assert report["already_covered_count"] == 1
    row = report["already_covered"][0]
    assert row["risk_scoring_readiness"] == "partial"
    assert row["identity_readiness"] == "already_covered_partial"
    assert "interest_expense" in row["recommended_next_fields"]
    assert "net_debt" in row["recommended_next_fields"]


def test_government_like_issuer_is_excluded(
    db_session: Session,
) -> None:
    company = create_company(
        db_session,
        "OFZ94",
        name="OFZ Ministry of Finance",
        inn=None,
    )
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue(
        [company.id],
        exclude_government_like=True,
    )

    assert report["collection_ready"] == []
    assert report["identity_review_required"] == []
    assert report["excluded_count"] == 1
    assert report["excluded_or_deprioritized"][0]["issuer_type"] == "government_like"


def test_mixed_batch_summary_counts(
    db_session: Session,
) -> None:
    ready = create_company(db_session, "READY94", name="PJSC Ready")
    unknown = create_company(
        db_session,
        "UNK94B",
        name="Unknown issuer for RU000A0XYZ94",
        inn=None,
    )
    covered = create_company(db_session, "COV94", name="PJSC Covered")
    gov = create_company(db_session, "GOV94", name="OFZ Ministry of Finance", inn=None)
    create_profile(db_session, ready, legal_name="PJSC Ready")
    create_profile(db_session, covered, legal_name="PJSC Covered")
    create_report(db_session, covered)
    db_session.commit()

    report = IdentityFirstCollectionService(
        db_session
    ).get_identity_first_collection_queue(
        [ready.id, unknown.id, covered.id, gov.id],
        include_covered=True,
        exclude_government_like=True,
    )
    summary = report["summary"]

    assert report["collection_ready_count"] == 1
    assert report["identity_review_required_count"] == 1
    assert report["already_covered_count"] == 1
    assert report["excluded_count"] == 1
    assert summary["known_corporate_missing_report_count"] == 1
    assert summary["unknown_identity_count"] == 1
    assert summary["weak_identity_count"] == 1
    assert summary["partial_report_count"] == 1
    assert summary["government_excluded_count"] == 1


def test_identity_first_api_respects_flags_and_rejects_empty(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "API94", name="PJSC API")
    create_profile(db_session, company, legal_name="PJSC API")
    create_report(db_session, company)
    db_session.commit()

    included = client.post(
        "/api/financial-reports/identity-first-collection/batch",
        json={
            "company_ids": [company.id],
            "source_presence": {str(company.id): ["manual-id"]},
            "include_covered": True,
            "exclude_government_like": True,
        },
    )
    not_included = client.post(
        "/api/financial-reports/identity-first-collection/batch",
        json={
            "company_ids": [company.id],
            "include_covered": False,
            "exclude_government_like": True,
        },
    )
    empty = client.post(
        "/api/financial-reports/identity-first-collection/batch",
        json={"company_ids": []},
    )

    assert included.status_code == 200
    assert included.json()["already_covered"][0]["company_id"] == company.id
    assert not_included.status_code == 200
    assert not_included.json()["already_covered"] == []
    assert empty.status_code == 422


def test_cli_writes_json_markdown_and_csv_outputs(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "identity.json"
    markdown_output = tmp_path / "identity.md"
    review_csv = tmp_path / "review.csv"
    ready_csv = tmp_path / "ready.csv"
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
        if url.endswith("/api/financial-reports/identity-first-collection/batch"):
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
                data=_fake_identity_first_response(125),
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = identity_script.parse_args(
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
            "--identity-review-csv-output",
            str(review_csv),
            "--collection-ready-csv-output",
            str(ready_csv),
        ]
    )
    report, exit_code = identity_script.run_queue(args, http_request=fake_http)
    identity_script.write_json_report(report, json_output)
    identity_script.write_markdown_report(report, markdown_output)
    identity_script.write_identity_review_csv(report, review_csv)
    identity_script.write_collection_ready_csv(report, ready_csv)

    assert exit_code == 0
    assert report["read_only"] is True
    assert report["identity_apply_executed"] is False
    assert json.loads(json_output.read_text(encoding="utf-8"))[
        "collection_ready_count"
    ] == 1
    assert "# Identity-First Financial Collection Queue" in markdown_output.read_text(
        encoding="utf-8"
    )
    assert "review_reasons" in review_csv.read_text(encoding="utf-8")
    assert "recommended_collection_type" in ready_csv.read_text(encoding="utf-8")
    assert all(
        method == "GET"
        or url.endswith("/api/financial-reports/identity-first-collection/batch")
        for method, url in calls
    )


def test_cli_mixed_source_preserves_safe_source_labels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_collect(args, http_request):
        return (
            [125],
            {125: ["top-predictions", "bond-universe"]},
            {
                "source": "mixed",
                "safe_sources": ["top-predictions", "bond-universe"],
                "target_count": 1,
                "warnings": [],
                "errors": [],
            },
        )

    monkeypatch.setattr(
        identity_script.priority_script,
        "_collect_target_company_ids",
        fake_collect,
    )

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/paper-trading" not in url
        assert "/ingest" not in url
        assert "/api/financial-reports/preview" not in url
        if url.endswith("/api/financial-reports/stats"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"financial_reports_count": 1},
            )
        if url.endswith("/api/financial-reports/identity-first-collection/batch"):
            assert payload["company_ids"] == [125]
            assert payload["source_presence"] == {
                "125": ["top-predictions", "bond-universe"]
            }
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=_fake_identity_first_response(125),
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    args = identity_script.parse_args(
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
        ]
    )
    report, exit_code = identity_script.run_queue(args, http_request=fake_http)

    assert exit_code == 0
    assert report["target_source_report"]["safe_sources"] == [
        "top-predictions",
        "bond-universe",
    ]
    assert report["requested_source_presence"] == {
        "125": ["top-predictions", "bond-universe"]
    }
    assert not any("/paper-trading" in url for _method, url in calls)


def test_identity_first_service_does_not_mutate_reports_or_identity(
    db_session: Session,
) -> None:
    company = create_company(db_session, "SAFE94", name="PJSC Safe")
    create_profile(db_session, company, legal_name="PJSC Safe")
    db_session.commit()
    before_reports = db_session.scalar(select(func.count()).select_from(FinancialReport))
    before_profiles = db_session.scalar(
        select(func.count()).select_from(CompanyIdentityProfile)
    )

    IdentityFirstCollectionService(db_session).get_identity_first_collection_queue(
        [company.id]
    )

    after_reports = db_session.scalar(select(func.count()).select_from(FinancialReport))
    after_profiles = db_session.scalar(
        select(func.count()).select_from(CompanyIdentityProfile)
    )
    assert after_reports == before_reports
    assert after_profiles == before_profiles


def _fake_identity_first_response(company_id: int) -> dict:
    return {
        "status": "passed",
        "company_count": 1,
        "collection_ready_count": 1,
        "identity_review_required_count": 0,
        "already_covered_count": 0,
        "excluded_count": 0,
        "summary": {
            "known_corporate_missing_report_count": 1,
            "unknown_identity_count": 0,
            "weak_identity_count": 0,
            "partial_report_count": 0,
            "ready_report_count": 0,
            "government_excluded_count": 0,
            "collection_ready_high_priority_count": 1,
        },
        "collection_ready": [
            {
                "rank": 1,
                "company_id": company_id,
                "company_name": "TMK",
                "canonical_company_id": company_id,
                "canonical_company_name": "TMK",
                "issuer_type": "corporate",
                "classification_confidence": "medium",
                "identity_status": "matched",
                "identity_confidence": 0.9,
                "identity_readiness": "ready_for_financial_collection",
                "priority_level": "high",
                "priority_score": 90,
                "has_financial_report": False,
                "risk_scoring_readiness": "not_ready",
                "source_presence": {"source_labels": ["manual-id"]},
                "bond_context": {
                    "bond_count": 1,
                    "sample_bonds": [{"secid": "TMK001", "name": "TMK Bond"}],
                },
                "recommended_collection": {
                    "collection_type": "full_annual_ifrs_report",
                    "period_preference": "latest_annual",
                    "required_fields": ["revenue", "ebitda"],
                    "optional_fields": ["debt_to_ebitda"],
                },
                "priority_reasons": ["missing financial report"],
                "identity_reasons": ["issuer is corporate"],
                "operator_next_action": "collect_official_financial_report",
            }
        ],
        "identity_review_required": [],
        "already_covered": [],
        "excluded_or_deprioritized": [],
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "identity_apply_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
