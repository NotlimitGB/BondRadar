from __future__ import annotations

import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_import as import_script  # noqa: E402
import issuer_identity_duplicate_export as duplicate_export  # noqa: E402
import issuer_identity_duplicate_review as duplicate_review  # noqa: E402


def test_duplicate_review_templates_parse() -> None:
    csv_rows = duplicate_review.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_duplicate_review_template.csv",
        "csv",
    )
    json_rows = duplicate_review.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_duplicate_review_template.json",
        "json",
    )

    assert len(csv_rows) == 1
    assert len(json_rows) == 1
    assert duplicate_review.validate_rows(csv_rows)["status"] == "passed"
    assert duplicate_review.validate_rows(json_rows)["status"] == "passed"


def test_duplicate_review_synthetic_examples_parse() -> None:
    csv_rows = duplicate_review.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_duplicate_review_example_synthetic.csv",
        "csv",
    )
    json_rows = duplicate_review.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_duplicate_review_example_synthetic.json",
        "json",
    )

    assert duplicate_review.validate_rows(csv_rows)["status"] == "passed"
    assert duplicate_review.validate_rows(json_rows)["status"] == "passed"


def test_duplicate_export_writes_json_csv_markdown(tmp_path: Path) -> None:
    args = duplicate_export.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--limit",
            "10",
            "--min-score",
            "0.50",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        assert method == "GET"
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={
                "status": "warning",
                "candidate_group_count": 1,
                "candidate_pair_count": 1,
                "high_confidence_count": 0,
                "medium_confidence_count": 1,
                "low_confidence_count": 0,
                "groups": [
                    {
                        "group_key": "bond_phrase:rzd",
                        "canonical_company_id": 18,
                        "canonical_company_name": "Synthetic Canonical",
                        "canonical_identity_status": "matched",
                        "candidates": [
                            {
                                "company_id": 289,
                                "company_name": "Unknown issuer for RU000SYN289",
                                "match_type": "bond_name_phrase",
                                "match_score": "0.7500",
                                "match_reasons": ["Synthetic bond name contains display name"],
                                "sample_secids": ["RU000SYN289"],
                                "sample_bond_names": ["Synthetic Canonical BO 001"],
                                "recommended_action": "review",
                            }
                        ],
                    }
                ],
                "warnings": [{"message": "Potential same-issuer company rows require review."}],
            },
        )

    report = duplicate_export.build_report(args, http_request=fake_http)
    json_output = tmp_path / "duplicates.json"
    csv_output = tmp_path / "duplicates.csv"
    markdown_output = tmp_path / "duplicates.md"

    import_script.write_json_report(report, json_output)
    duplicate_export.write_csv_report(report, csv_output)
    duplicate_export.write_markdown_report(report, markdown_output)

    assert report["candidate_pair_count"] == 1
    assert json.loads(json_output.read_text(encoding="utf-8"))["rows"][0]["candidate_company_id"] == 289
    assert "canonical_company_id" in csv_output.read_text(encoding="utf-8")
    assert "# BondRadar Issuer Duplicate Candidates" in markdown_output.read_text(encoding="utf-8")


def test_duplicate_review_dry_run_calls_preview_only(tmp_path: Path) -> None:
    input_path = tmp_path / "duplicate_review.csv"
    _write_review_csv(input_path)
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "passed", "errors": [], "warnings": []},
        )

    report = duplicate_review.run_flow(
        input_path=input_path,
        format_value="csv",
        backend_url="http://testserver",
        dry_run=True,
        execute_apply="no",
        confirm_apply="no",
        allow_conflicts=False,
        allow_weak_canonical=False,
        http_request=fake_http,
    )

    assert report["status"] == "passed"
    assert any(url.endswith("/duplicates/preview") for url in calls)
    assert not any(url.endswith("/duplicates/apply") for url in calls)


def test_duplicate_review_execute_requires_confirmation(tmp_path: Path) -> None:
    input_path = tmp_path / "duplicate_review.csv"
    _write_review_csv(input_path)
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "passed"})

    report = duplicate_review.run_flow(
        input_path=input_path,
        format_value="csv",
        backend_url="http://testserver",
        dry_run=False,
        execute_apply="yes",
        confirm_apply="no",
        allow_conflicts=False,
        allow_weak_canonical=False,
        http_request=fake_http,
    )

    assert report["status"] == "failed"
    assert any("requires --confirm-apply yes" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/duplicates/apply") for url in calls)


def test_duplicate_review_confirmed_execute_calls_apply_and_markdown_has_rollback(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "duplicate_review.csv"
    _write_review_csv(input_path)
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        if url.endswith("/duplicates/apply"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "completed",
                    "affected_rows_summary": {
                        "created_candidate_count": 1,
                        "updated_candidate_count": 0,
                        "skipped_count": 0,
                        "conflict_count": 0,
                    },
                    "rows": [
                        {
                            "canonical_company_id": 18,
                            "candidate_company_id": 289,
                            "action": "created",
                            "warnings": [],
                        }
                    ],
                },
            )
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "passed", "errors": [], "warnings": []},
        )

    report = duplicate_review.run_flow(
        input_path=input_path,
        format_value="csv",
        backend_url="http://testserver",
        dry_run=False,
        execute_apply="yes",
        confirm_apply="yes",
        allow_conflicts=False,
        allow_weak_canonical=False,
        http_request=fake_http,
    )
    markdown_output = tmp_path / "duplicate_apply.md"
    duplicate_review.write_markdown_report(report, markdown_output)

    assert report["status"] == "passed"
    assert any(url.endswith("/duplicates/apply") for url in calls)
    assert "## Rollback Note" in markdown_output.read_text(encoding="utf-8")


def _write_review_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=duplicate_review.DUPLICATE_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "canonical_company_id": "18",
                "canonical_company_name": "Synthetic Canonical",
                "candidate_company_id": "289",
                "candidate_company_name": "Unknown issuer for RU000SYN289",
                "match_type": "bond_name_phrase",
                "match_score": "0.7500",
                "match_reasons": "Synthetic bond name contains display name",
                "sample_secids": "RU000SYN289",
                "sample_bond_names": "Synthetic Canonical BO 001",
                "status": "accepted",
                "review_status": "reviewed",
                "review_notes": "Synthetic duplicate review.",
            }
        )
