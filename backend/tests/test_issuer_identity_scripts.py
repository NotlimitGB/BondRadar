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
import issuer_identity_import as identity_import  # noqa: E402
import issuer_identity_moex_enrich as moex_enrich  # noqa: E402
import issuer_identity_target_export as target_export  # noqa: E402


def test_identity_templates_parse() -> None:
    csv_rows = identity_import.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_template.csv",
        "csv",
    )
    json_rows = identity_import.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_template.json",
        "json",
    )

    assert len(csv_rows) == 1
    assert len(json_rows) == 1


def test_identity_synthetic_examples_validate() -> None:
    csv_rows = identity_import.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_example_synthetic.csv",
        "csv",
    )
    json_rows = identity_import.load_rows(
        ROOT / "docs/examples/issuer_identity/issuer_identity_example_synthetic.json",
        "json",
    )

    assert identity_import.validate_rows(csv_rows)["status"] == "passed"
    assert identity_import.validate_rows(json_rows)["status"] == "passed"


def test_identity_import_dry_run_never_calls_apply(tmp_path: Path) -> None:
    input_path = tmp_path / "identity.csv"
    _write_identity_csv(input_path)
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "passed", "errors": [], "warnings": []},
        )

    report = identity_import.run_flow(
        input_path=input_path,
        format_value="csv",
        backend_url="http://testserver",
        dry_run=True,
        execute="no",
        confirm_apply="no",
        rebuild_existing=False,
        allow_conflicts=False,
        http_request=fake_http,
    )

    assert report["status"] == "passed"
    assert any(url.endswith("/preview") for url in calls)
    assert not any(url.endswith("/apply") for url in calls)


def test_identity_import_execute_requires_confirmation(tmp_path: Path) -> None:
    input_path = tmp_path / "identity.csv"
    _write_identity_csv(input_path)
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "passed"})

    report = identity_import.run_flow(
        input_path=input_path,
        format_value="csv",
        backend_url="http://testserver",
        dry_run=False,
        execute="yes",
        confirm_apply="no",
        rebuild_existing=False,
        allow_conflicts=False,
        http_request=fake_http,
    )

    assert report["status"] == "failed"
    assert any("requires --confirm-apply yes" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/apply") for url in calls)


def test_identity_import_writes_json_and_markdown(tmp_path: Path) -> None:
    report = {
        "status": "passed",
        "validation": {"total_rows": 1, "valid_rows": 1, "invalid_rows": 0},
        "preview": {"status": "passed"},
        "apply": None,
        "errors": [],
        "warnings": [],
        "next_steps": ["Review preview."],
    }
    json_output = tmp_path / "identity.json"
    markdown_output = tmp_path / "identity.md"

    import_script.write_json_report(report, json_output)
    identity_import.write_markdown_report(report, markdown_output)

    assert json.loads(json_output.read_text(encoding="utf-8"))["status"] == "passed"
    assert "# BondRadar Issuer Identity Import" in markdown_output.read_text(encoding="utf-8")


def test_identity_target_export_deduplicates_and_writes_outputs(tmp_path: Path) -> None:
    args = target_export.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--source",
            "unknown-companies",
            "--limit",
            "10",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        assert method == "GET"
        if "/duplicates/diagnostics" in url:
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "groups": [
                        {
                            "canonical_company_id": 18,
                            "canonical_company_name": "Synthetic Canonical",
                            "candidates": [
                                {
                                    "company_id": 1,
                                    "match_score": "0.7500",
                                    "match_reasons": ["Synthetic duplicate hint"],
                                }
                            ],
                        }
                    ],
                    "warnings": [],
                },
            )
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={
                "status": "warning",
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

    report = target_export.build_report(args, http_request=fake_http)
    json_output = tmp_path / "targets.json"
    csv_output = tmp_path / "targets.csv"
    markdown_output = tmp_path / "targets.md"

    import_script.write_json_report(report, json_output)
    target_export.write_csv_report(report, csv_output)
    target_export.write_markdown_report(report, markdown_output)

    assert report["total_targets"] == 1
    assert report["targets"][0]["suggested_search_query"] == '"Synthetic Bond" issuer INN'
    assert report["targets"][0]["needs_duplicate_review"] is True
    assert report["targets"][0]["possible_canonical_company_id"] == 18
    assert json.loads(json_output.read_text(encoding="utf-8"))["targets"][0]["company_id"] == 1
    assert "company_id" in csv_output.read_text(encoding="utf-8")
    assert "# BondRadar Issuer Identity Targets" in markdown_output.read_text(encoding="utf-8")


def test_moex_enrich_defaults_to_preview_only() -> None:
    calls: list[str] = []

    class FakeMoex:
        def fetch_bond_description(self, secid: str):
            return (
                {
                    "secid": secid,
                    "issuer_name": "Synthetic MOEX Issuer",
                    "issuer_inn": "7700000001",
                },
                [],
            )

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        if url.endswith("/api/companies/1"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"id": 1, "name": "Unknown issuer for RU000SYN001"},
            )
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "passed", "errors": [], "warnings": []},
        )

    args = moex_enrich.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--company-id",
            "1",
            "--secid",
            "RU000SYN001",
        ]
    )

    report = moex_enrich.build_report(args, http_request=fake_http, moex_client=FakeMoex())

    assert report["status"] == "passed"
    assert report["rows"][0]["legal_name"] == "Synthetic MOEX Issuer"
    assert any(url.endswith("/preview") for url in calls)
    assert not any(url.endswith("/apply") for url in calls)


def test_moex_enrich_missing_metadata_warns() -> None:
    class FakeMoex:
        def fetch_bond_description(self, secid: str):
            return ({"secid": secid, "issuer_name": None, "issuer_inn": None}, [])

    def fake_http(method: str, url: str, payload=None):
        if url.endswith("/api/companies/1"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"id": 1, "name": "Unknown issuer for RU000SYN001"},
            )
        return import_script.HttpResult(ok=True, status_code=200, data={})

    args = moex_enrich.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--company-id",
            "1",
            "--secid",
            "RU000SYN001",
        ]
    )

    report = moex_enrich.build_report(args, http_request=fake_http, moex_client=FakeMoex())

    assert report["status"] == "warning"
    assert any("MOEX issuer metadata missing" in item["message"] for item in report["warnings"])


def _write_identity_csv(path: Path) -> None:
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
                "identity_source": "operator_csv",
                "review_status": "pending",
            }
        )
