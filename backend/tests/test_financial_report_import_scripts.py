from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_import as fri  # noqa: E402
import financial_report_import_rehearsal as rehearsal  # noqa: E402
import financial_report_post_ingest_rebuild as rebuild  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    header = fri.SUPPORTED_FIELDS
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(field, "")) for field in header))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "company_ticker": "SYNTH",
        "period_year": "2025",
        "period_quarter": "0",
        "currency": "RUB",
        "source": "operator_csv",
        "revenue": "1000",
        "ebitda": "200",
        "total_debt": "300",
        "cash": "50",
        "equity": "500",
        "short_term_debt": "80",
        "net_profit": "60",
        "interest_expense": "30",
    }
    row.update(overrides)
    return row


def test_csv_and_json_templates_parse() -> None:
    csv_rows = fri.load_rows(
        ROOT / "docs/examples/financial_reports/financial_reports_template.csv",
        "csv",
    )
    json_rows = fri.load_rows(
        ROOT / "docs/examples/financial_reports/financial_reports_template.json",
        "json",
    )

    assert len(csv_rows) == 1
    assert len(json_rows) == 1


def test_local_validation_catches_empty_missing_identifier_invalid_decimal_and_duplicate(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "reports.csv"
    write_csv(
        csv_path,
        [
            valid_row(company_ticker="", revenue="100"),
            valid_row(revenue="bad-decimal"),
            valid_row(company_ticker="DUP", revenue="100"),
            valid_row(company_ticker="DUP", revenue="100"),
        ],
    )

    rows = fri.load_rows(csv_path, "csv")
    report = fri.validate_rows(rows, "operator_csv")

    messages = [error["message"] for error in report["errors"]]
    assert "company identifier is required" in messages
    assert "invalid decimal value for revenue" in messages
    assert any("duplicate company-period row" in message for message in messages)
    assert report["status"] == "failed"


def test_negative_revenue_fails_and_negative_equity_warns(tmp_path: Path) -> None:
    csv_path = tmp_path / "reports.csv"
    write_csv(
        csv_path,
        [
            valid_row(company_ticker="NEGREV", revenue="-1"),
            valid_row(company_ticker="NEGEQ", period_quarter="1", equity="-10"),
        ],
    )

    report = fri.validate_rows(fri.load_rows(csv_path, "csv"), "operator_csv")

    assert any(error["message"] == "revenue cannot be negative" for error in report["errors"])
    assert any(warning["message"] == "equity is negative" for warning in report["warnings"])


def test_import_script_writes_json_and_markdown(tmp_path: Path) -> None:
    csv_path = tmp_path / "reports.csv"
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"
    write_csv(csv_path, [valid_row()])

    exit_code = fri.main(
        [
            "--input",
            str(csv_path),
            "--format",
            "csv",
            "--source",
            "operator_csv",
            "--dry-run",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    assert json.loads(json_output.read_text(encoding="utf-8"))["status"] == "passed"
    assert "# BondRadar Financial Report Import" in markdown_output.read_text(encoding="utf-8")


def test_rehearsal_execute_no_never_calls_ingest(tmp_path: Path) -> None:
    csv_path = tmp_path / "reports.csv"
    write_csv(csv_path, [valid_row()])
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        if url.endswith("/preview"):
            return fri.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": []},
            )
        return fri.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "warning", "warnings": []},
        )

    args = argparse.Namespace(
        input=csv_path,
        format="csv",
        source="operator_csv",
        backend_url="http://127.0.0.1:8000",
        as_of_date="2026-05-19",
        stale_after_days=540,
        execute="no",
        confirm_import=None,
        rebuild_existing=False,
        limit=None,
    )

    report, exit_code = rehearsal.run_rehearsal(args, http_request=fake_http)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert not any(url.endswith("/ingest") for _method, url in calls)


def test_rehearsal_execute_yes_requires_confirmation(tmp_path: Path) -> None:
    csv_path = tmp_path / "reports.csv"
    write_csv(csv_path, [valid_row()])
    calls: list[tuple[str, str]] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append((method, url))
        return fri.HttpResult(ok=True, status_code=200, data={"status": "passed"})

    args = argparse.Namespace(
        input=csv_path,
        format="csv",
        source="operator_csv",
        backend_url="http://127.0.0.1:8000",
        as_of_date="2026-05-19",
        stale_after_days=540,
        execute="yes",
        confirm_import=None,
        rebuild_existing=False,
        limit=None,
    )

    report, exit_code = rehearsal.run_rehearsal(args, http_request=fake_http)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert not any(url.endswith("/ingest") for _method, url in calls)


def test_post_ingest_rebuild_renders_plan_outputs(tmp_path: Path) -> None:
    json_output = tmp_path / "rebuild.json"
    markdown_output = tmp_path / "rebuild.md"

    exit_code = rebuild.main(
        [
            "--as-of-date-from",
            "2026-05-13",
            "--as-of-date-to",
            "2026-05-19",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["status"] == "planned"
    assert any(step["name"] == "rebuild_company_credit_health" for step in payload["steps"])
    assert "# BondRadar Post-Ingest Rebuild Plan" in markdown_output.read_text(encoding="utf-8")
