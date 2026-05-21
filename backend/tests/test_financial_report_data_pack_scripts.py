from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_collection_normalize as normalizer  # noqa: E402
import financial_report_data_pack_rehearsal as data_pack  # noqa: E402
import financial_report_import as import_script  # noqa: E402
import financial_report_target_issuers as targets  # noqa: E402


def test_target_export_empty_report_writes_outputs(tmp_path: Path) -> None:
    def fake_http(method: str, url: str, payload=None):
        if "/api/bonds" in url:
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        return import_script.HttpResult(ok=True, status_code=200, data=[])

    args = argparse.Namespace(
        backend_url="http://testserver",
        source="bond-universe",
        portfolio_id=None,
        model_run_id=None,
        as_of_date=None,
        limit=10,
        include_secids="",
        include_company_ids="",
    )
    report = targets.build_report(args, http_request=fake_http)
    json_output = tmp_path / "targets.json"
    csv_output = tmp_path / "targets.csv"
    markdown_output = tmp_path / "targets.md"

    import_script.write_json_report(report, json_output)
    targets.write_csv_report(report, csv_output)
    targets.write_markdown_report(report, markdown_output)

    assert report["status"] == "passed"
    assert report["targets"] == []
    assert json.loads(json_output.read_text(encoding="utf-8"))["total_targets"] == 0
    assert "company_id" in csv_output.read_text(encoding="utf-8")
    assert "# BondRadar Financial Report Target Issuers" in markdown_output.read_text(encoding="utf-8")


def test_target_export_deduplicates_companies_and_enriches_coverage() -> None:
    responses = {
        "/api/bonds?skip=0&limit=200": [
            {"id": 1, "company_id": 10, "secid": "AAA1", "name": "Issuer A Bond 1"},
            {"id": 2, "company_id": 10, "secid": "AAA2", "name": "Issuer A Bond 2"},
            {"id": 3, "company_id": 20, "secid": "BBB1", "name": "Issuer B Bond"},
            {"id": 4, "company_id": 30, "secid": "SU262", "name": "OFZ Control"},
        ],
        "/api/companies/10": {"id": 10, "name": "Issuer A", "ticker": "A", "inn": "1"},
        "/api/companies/20": {"id": 20, "name": "Issuer B", "ticker": "B", "inn": "2"},
        "/api/companies/identity/profiles/10": {
            "company_id": 10,
            "identity_status": "matched",
            "identity_confidence": "0.8",
            "legal_name": "Issuer A",
            "short_name": "Issuer A",
            "ogrn": None,
            "issuer_group_name": None,
            "issuer_role": "legal_issuer",
        },
        "/api/companies/identity/profiles/20": {
            "company_id": 20,
            "identity_status": "matched",
            "identity_confidence": "0.8",
            "legal_name": "Issuer B",
            "short_name": "Issuer B",
            "ogrn": None,
            "issuer_group_name": None,
            "issuer_role": "legal_issuer",
        },
        "/api/companies/10/reports?limit=1": [],
        "/api/companies/20/reports?limit=1": [
            {
                "period_year": 2025,
                "period_quarter": 0,
                "period_end_date": "2025-12-31",
            }
        ],
    }

    def fake_http(method: str, url: str, payload=None):
        path = url.replace("http://testserver", "")
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data=responses.get(path, []),
        )

    args = argparse.Namespace(
        backend_url="http://testserver",
        source="bond-universe",
        portfolio_id=None,
        model_run_id=None,
        as_of_date=None,
        limit=10,
        include_secids="",
        include_company_ids="",
    )

    report = targets.build_report(args, http_request=fake_http)

    assert report["total_targets"] == 2
    by_id = {row["company_id"]: row for row in report["targets"]}
    assert by_id[10]["bonds_count"] == 2
    assert by_id[10]["coverage_status"] == "missing_report"
    assert by_id[10]["identity_status"] == "matched"
    assert by_id[10]["needs_identity_review"] is False
    assert by_id[20]["coverage_status"] == "has_report"


def test_collection_templates_parse() -> None:
    csv_rows = normalizer.load_collection_rows(
        ROOT / "docs/examples/financial_reports/financial_reports_collection_template.csv",
        "csv",
    )
    json_rows = normalizer.load_collection_rows(
        ROOT / "docs/examples/financial_reports/financial_reports_collection_template.json",
        "json",
    )

    assert len(csv_rows) == 1
    assert len(json_rows) == 1


def test_collection_scale_conversion_and_empty_values() -> None:
    rows = [
        _row(value_scale="raw", revenue="1", ebitda="2", total_debt="4", interest_expense="1"),
        _row(value_scale="thousand", company_ticker="S2", revenue="1", ebitda="2", total_debt="4", interest_expense="1"),
        _row(value_scale="million", company_ticker="S3", revenue="1", ebitda="2", total_debt="4", interest_expense="1"),
        _row(value_scale="billion", company_ticker="S4", revenue="1", ebitda="2", total_debt="4", interest_expense="1"),
        _row(company_ticker="S5", revenue=""),
    ]

    report = normalizer.normalize_rows(rows, default_currency="RUB", default_source="operator_collection")
    normalized = report["normalized_rows"]

    assert normalized[0]["revenue"] == "1"
    assert normalized[1]["revenue"] == "1000"
    assert normalized[2]["revenue"] == "1000000"
    assert normalized[3]["revenue"] == "1000000000"
    assert normalized[4]["revenue"] is None


def test_collection_validation_errors_and_warnings() -> None:
    rows = [
        _row(revenue="bad-decimal"),
        _row(company_ticker="BAD_SCALE", value_scale="mega"),
        _row(company_ticker="NEG_REV", revenue="-1"),
        _row(company_ticker="NEG_EQ", equity="-1"),
        _row(company_ticker="MISS_EBITDA", ebitda=""),
    ]

    report = normalizer.normalize_rows(rows, default_currency="RUB", default_source="operator_collection")
    errors = [item["message"] for item in report["errors"]]
    warnings = [item["message"] for item in report["warnings"]]

    assert "invalid decimal value for revenue" in errors
    assert "value_scale must be raw, thousand, million, or billion" in errors
    assert "revenue cannot be negative" in errors
    assert "equity is negative" in warnings
    assert "ebitda is missing" in warnings


def test_collection_computes_ratios_and_warns_on_conflict() -> None:
    rows = [
        _row(total_debt="400", ebitda="200", interest_expense="50"),
        _row(
            company_ticker="CONFLICT",
            total_debt="400",
            ebitda="200",
            interest_expense="50",
            debt_to_ebitda="9",
        ),
    ]

    report = normalizer.normalize_rows(rows, default_currency="RUB", default_source="operator_collection")

    assert report["normalized_rows"][0]["debt_to_ebitda"] == "2"
    assert report["normalized_rows"][0]["interest_coverage"] == "4"
    assert any("debt_to_ebitda differs from computed value" in item["message"] for item in report["warnings"])


def test_data_pack_execute_no_never_calls_ingest(tmp_path: Path) -> None:
    collection = tmp_path / "collection.csv"
    _write_collection_csv(collection, [_row()])
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        if url.endswith("/preview"):
            return import_script.HttpResult(ok=True, status_code=200, data={"status": "passed", "errors": [], "warnings": []})
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "warning", "warnings": []})

    args = argparse.Namespace(
        input=collection,
        format="csv",
        backend_url="http://testserver",
        as_of_date="2026-05-19",
        stale_after_days=540,
        normalized_output=tmp_path / "normalized.csv",
        execute_import="no",
        confirm_import=None,
        rebuild_existing=False,
    )

    report, exit_code = data_pack.run_rehearsal(args, http_request=fake_http)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert not any(url.endswith("/ingest") for url in calls)


def test_data_pack_execute_yes_requires_confirmation(tmp_path: Path) -> None:
    collection = tmp_path / "collection.csv"
    _write_collection_csv(collection, [_row()])
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "passed"})

    args = argparse.Namespace(
        input=collection,
        format="csv",
        backend_url="http://testserver",
        as_of_date="2026-05-19",
        stale_after_days=540,
        normalized_output=tmp_path / "normalized.csv",
        execute_import="yes",
        confirm_import=None,
        rebuild_existing=False,
    )

    report, exit_code = data_pack.run_rehearsal(args, http_request=fake_http)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert not any(url.endswith("/ingest") for url in calls)


def test_data_pack_report_outputs_are_written(tmp_path: Path) -> None:
    report = {
        "status": "passed",
        "execute_import": "no",
        "input": "input.csv",
        "normalized_output": "normalized.csv",
        "coverage_before": {"status_code": 200},
        "coverage_after": {"status_code": 200},
        "normalize_report": {"status": "passed", "total_rows": 1},
        "import_report": {"status": "passed"},
        "warnings": [],
        "errors": [],
        "next_steps": ["Review output."],
    }
    json_output = tmp_path / "data_pack.json"
    markdown_output = tmp_path / "data_pack.md"

    import_script.write_json_report(report, json_output)
    data_pack.write_markdown_report(report, markdown_output)

    assert json.loads(json_output.read_text(encoding="utf-8"))["status"] == "passed"
    assert "# BondRadar Financial Report Data Pack Rehearsal" in markdown_output.read_text(encoding="utf-8")


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "company_ticker": "SYNTH",
        "period_year": "2025",
        "period_quarter": "0",
        "currency": "RUB",
        "value_scale": "raw",
        "source": "operator_collection",
        "revenue": "1000",
        "ebitda": "200",
        "total_debt": "400",
        "cash": "50",
        "equity": "500",
        "short_term_debt": "80",
        "operating_cash_flow": "150",
        "net_profit": "70",
        "interest_expense": "40",
    }
    row.update(overrides)
    return row


def _write_collection_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=normalizer.COLLECTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
