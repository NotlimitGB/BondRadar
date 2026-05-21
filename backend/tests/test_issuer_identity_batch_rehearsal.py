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

import financial_report_import as import_script  # noqa: E402
import issuer_identity_batch_rehearsal as batch  # noqa: E402
import issuer_identity_import as identity_import  # noqa: E402


def test_batch_no_reviewed_input_generates_template_without_preview_or_apply(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    args = _args(
        source="mixed",
        review_template_output=tmp_path / "review_template.csv",
        json_output=tmp_path / "batch.json",
        markdown_output=tmp_path / "batch.md",
    )

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))
    import_script.write_json_report(report, args.json_output)
    batch.write_markdown_report(report, args.markdown_output)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["review_template"]["rows_written"] == 1
    assert args.review_template_output.exists()
    assert any("/diagnostics" in url for url in calls)
    assert not any(url.endswith("/preview") for url in calls)
    assert not any(url.endswith("/apply") for url in calls)
    assert "Fill identity review CSV manually" in report["next_steps"][1]
    assert json.loads(args.json_output.read_text(encoding="utf-8"))["status"] == "warning"
    assert "Recommended Next Steps" in args.markdown_output.read_text(encoding="utf-8")


def test_batch_reviewed_input_preview_only_calls_preview_not_apply(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(reviewed)
    calls: list[str] = []
    args = _args(reviewed_input=reviewed, execute_apply="no")

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["validation"]["valid_rows"] == 1
    assert report["preview"]["status"] == "passed"
    assert report["diagnostics_diff"]["delta_unknown_company_count"] == 0
    assert any(url.endswith("/preview") for url in calls)
    assert not any(url.endswith("/apply") for url in calls)


def test_batch_execute_apply_requires_confirmation_before_apply(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(reviewed)
    calls: list[str] = []
    args = _args(reviewed_input=reviewed, execute_apply="yes", confirm_apply="no")

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("--confirm-apply yes" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/apply") for url in calls)


def test_batch_execute_apply_requires_reviewed_input() -> None:
    calls: list[str] = []
    args = _args(execute_apply="yes", confirm_apply="yes")

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("--reviewed-input" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/apply") for url in calls)


def test_batch_confirm_without_execute_does_not_apply(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(reviewed)
    calls: list[str] = []
    args = _args(reviewed_input=reviewed, execute_apply="no", confirm_apply="yes")

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))

    assert exit_code == 0
    assert report["status"] == "passed"
    assert any(url.endswith("/preview") for url in calls)
    assert not any(url.endswith("/apply") for url in calls)


def test_batch_confirmed_apply_includes_affected_summary_and_rollback_note(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed.csv"
    markdown = tmp_path / "apply.md"
    _write_reviewed_csv(reviewed)
    calls: list[str] = []
    args = _args(
        reviewed_input=reviewed,
        execute_apply="yes",
        confirm_apply="yes",
        markdown_output=markdown,
    )

    report, exit_code = batch.run_rehearsal(args, http_request=_fake_http(calls))
    batch.write_markdown_report(report, markdown)

    assert exit_code == 0
    assert report["apply_executed"] is True
    assert report["affected_rows_summary"]["affected_company_ids"] == [1]
    assert any(url.endswith("/apply") for url in calls)
    markdown_text = markdown.read_text(encoding="utf-8")
    assert "## Affected Companies" in markdown_text
    assert "## Rollback Note" in markdown_text


def _args(**overrides) -> argparse.Namespace:
    values = {
        "backend_url": "http://testserver",
        "source": "unknown-companies",
        "portfolio_id": None,
        "model_run_id": 2,
        "as_of_date": "2026-05-19",
        "limit": 20,
        "review_template_output": None,
        "reviewed_input": None,
        "format": "csv",
        "execute_apply": "no",
        "confirm_apply": "no",
        "rebuild_existing": False,
        "allow_conflicts": False,
        "json_output": None,
        "markdown_output": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _fake_http(calls: list[str]):
    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        path = url.replace("http://testserver", "")
        if "/api/companies/identity/diagnostics" in path:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "warning",
                    "company_count": 1,
                    "unknown_company_count": 1,
                    "weak_identity_count": 1,
                    "verified_identity_count": 0,
                    "top_unknown_issuers": [
                        {
                            "company_id": 1,
                            "company_name": "Unknown issuer for RU000SYN001",
                            "ticker": "MOEX_RU000SYN001",
                            "inn": None,
                            "bonds_count": 2,
                            "sample_secids": ["RU000SYN001"],
                            "sample_bond_names": ["Synthetic Bond"],
                            "identity_status": "unknown",
                        }
                    ],
                    "warnings": [],
                },
            )
        if path == "/api/paper-trading/portfolios?limit=1":
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if path.startswith("/api/ml/predictions"):
            return import_script.HttpResult(ok=True, status_code=200, data={"predictions": []})
        if path == "/api/bonds?skip=0&limit=200":
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if path.endswith("/preview"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": [], "rows": []},
            )
        if path.endswith("/apply"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "completed",
                    "total_rows": 1,
                    "created": 1,
                    "updated": 0,
                    "company_updates": 1,
                    "skipped": 0,
                    "failed": 0,
                    "affected_rows_summary": {
                        "affected_company_ids": [1],
                        "created_profile_count": 1,
                        "updated_profile_count": 0,
                        "updated_company_count": 1,
                        "skipped_count": 0,
                        "conflict_count": 0,
                        "warning_count": 0,
                    },
                    "rows": [
                        {
                            "row_index": 1,
                            "company_id": 1,
                            "action": "created",
                            "company_updated": True,
                            "warnings": [],
                            "conflicts": [],
                            "errors": [],
                        }
                    ],
                    "errors": [],
                    "warnings": [],
                },
            )
        return import_script.HttpResult(ok=True, status_code=200, data={})

    return fake_http


def _write_reviewed_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=identity_import.IDENTITY_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "company_id": "1",
                "current_company_name": "Unknown issuer for RU000SYN001",
                "legal_name": "Synthetic Issuer LLC",
                "short_name": "Synthetic Issuer",
                "display_name": "Synthetic Issuer",
                "inn": "7700000001",
                "ogrn": "1027700000001",
                "country": "RU",
                "issuer_role": "legal_issuer",
                "identity_status": "matched",
                "identity_source": "operator_csv",
                "review_status": "pending",
            }
        )
