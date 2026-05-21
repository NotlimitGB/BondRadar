from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_import as import_script  # noqa: E402
import financial_report_target_issuers as targets  # noqa: E402


def test_financial_targets_roll_up_duplicate_members_and_coverage_warning(
    tmp_path: Path,
) -> None:
    args = targets.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--source",
            "bond-universe",
            "--limit",
            "10",
            "--use-duplicate-mapping",
            "--rollup-duplicates",
            "--include-duplicate-members",
            "--compare-rollup",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        path = url.replace("http://testserver", "")
        if path == "/api/companies/identity/canonical-groups?active_only=true":
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "status": "passed",
                    "groups": [
                        {
                            "canonical_company_id": 18,
                            "canonical_company_name": "Synthetic Canonical",
                            "canonical_ticker": "CAN",
                            "canonical_inn": "7700000001",
                            "canonical_identity_status": "matched",
                            "duplicate_members": [
                                {
                                    "company_id": 289,
                                    "company_name": "Unknown issuer for RU000SYN289",
                                    "duplicate_mapping_status": "accepted",
                                    "duplicate_review_status": "reviewed",
                                    "duplicate_match_type": "bond_name_phrase",
                                    "duplicate_match_score": "0.7500",
                                }
                            ],
                        }
                    ],
                    "warnings": [],
                },
            )
        if "/api/companies/identity/duplicates/diagnostics" in path:
            return import_script.HttpResult(ok=True, status_code=200, data={"groups": []})
        responses = {
            "/api/bonds?skip=0&limit=200": [
                {
                    "id": 1,
                    "company_id": 18,
                    "secid": "RU000CAN001",
                    "name": "Synthetic Canonical BO 001",
                },
                {
                    "id": 2,
                    "company_id": 289,
                    "secid": "RU000DUP001",
                    "name": "Synthetic Canonical BO 002",
                },
            ],
            "/api/companies/18": {
                "id": 18,
                "name": "Synthetic Canonical",
                "ticker": "CAN",
                "inn": "7700000001",
            },
            "/api/companies/identity/profiles/18": {
                "company_id": 18,
                "identity_status": "matched",
                "identity_confidence": "0.9",
                "legal_name": "Synthetic Canonical LLC",
                "short_name": "Synthetic Canonical",
                "ogrn": "1027700000001",
                "issuer_group_name": None,
                "issuer_role": "legal_issuer",
            },
            "/api/companies/18/reports?limit=1": [],
            "/api/companies/289/reports?limit=1": [
                {
                    "period_year": 2025,
                    "period_quarter": 0,
                    "period_end_date": "2025-12-31",
                }
            ],
        }
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data=responses.get(path, []),
        )

    report = targets.build_report(args, http_request=fake_http)
    collection_output = tmp_path / "collection.csv"
    targets.write_collection_template(report, collection_output)

    assert report["total_targets"] == 1
    assert report["rollup_comparison"] == {
        "raw_target_count": 2,
        "canonical_target_count": 1,
        "deduplicated_count": 1,
        "duplicate_member_count": 1,
    }
    row = report["targets"][0]
    assert row["company_id"] == 18
    assert row["canonical_company_id"] == 18
    assert row["duplicate_company_ids"] == [289]
    assert row["sample_secids"] == ["RU000CAN001", "RU000DUP001"]
    assert row["duplicate_sample_secids"] == ["RU000DUP001"]
    assert row["legal_name"] == "Synthetic Canonical LLC"
    assert row["canonical_has_financial_report"] is False
    assert row["duplicate_has_financial_report"] is True
    assert row["coverage_effective_status"] == "covered_by_duplicate_warning"
    assert any(
        item.get("code") == "financial_report_attached_to_duplicate_candidate"
        for item in report["warnings"]
    )
    assert "canonical_company_id" in collection_output.read_text(encoding="utf-8")


def test_financial_targets_canonical_report_covers_duplicates() -> None:
    args = targets.parse_args(
        [
            "--backend-url",
            "http://testserver",
            "--source",
            "bond-universe",
            "--limit",
            "10",
            "--use-duplicate-mapping",
            "--rollup-duplicates",
            "--compare-rollup",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        path = url.replace("http://testserver", "")
        if path == "/api/companies/identity/canonical-groups?active_only=true":
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "groups": [
                        {
                            "canonical_company_id": 18,
                            "canonical_company_name": "Synthetic Canonical",
                            "duplicate_members": [
                                {
                                    "company_id": 289,
                                    "company_name": "Unknown issuer for RU000SYN289",
                                    "duplicate_mapping_status": "accepted",
                                    "duplicate_review_status": "reviewed",
                                    "duplicate_match_type": "bond_name_phrase",
                                    "duplicate_match_score": "0.7500",
                                }
                            ],
                        }
                    ],
                    "warnings": [],
                },
            )
        if "/api/companies/identity/duplicates/diagnostics" in path:
            return import_script.HttpResult(ok=True, status_code=200, data={"groups": []})
        responses = {
            "/api/bonds?skip=0&limit=200": [
                {"id": 1, "company_id": 289, "secid": "RU000DUP001", "name": "Synthetic BO"}
            ],
            "/api/companies/18": {"id": 18, "name": "Synthetic Canonical", "ticker": "CAN"},
            "/api/companies/identity/profiles/18": {"identity_status": "matched"},
            "/api/companies/18/reports?limit=1": [
                {
                    "period_year": 2025,
                    "period_quarter": 0,
                    "period_end_date": "2025-12-31",
                }
            ],
            "/api/companies/289/reports?limit=1": [],
        }
        return import_script.HttpResult(ok=True, status_code=200, data=responses.get(path, []))

    report = targets.build_report(args, http_request=fake_http)

    assert report["targets"][0]["coverage_effective_status"] == "covered_by_canonical"
    assert report["targets"][0]["needs_financial_report"] is False
    assert not any(
        item.get("code") == "financial_report_attached_to_duplicate_candidate"
        for item in report["warnings"]
    )


def test_financial_targets_outputs_write_with_rollup(tmp_path: Path) -> None:
    report = {
        "status": "passed",
        "source": "mixed",
        "total_targets": 1,
        "rollup_comparison": {
            "raw_target_count": 2,
            "canonical_target_count": 1,
            "deduplicated_count": 1,
            "duplicate_member_count": 1,
        },
        "targets": [
            {
                "company_id": 18,
                "company_name": "Synthetic Canonical",
                "canonical_company_id": 18,
                "canonical_company_name": "Synthetic Canonical",
                "company_ticker": "CAN",
                "company_inn": "7700000001",
                "identity_status": "matched",
                "bonds_count": 2,
                "duplicate_count": 1,
                "duplicate_company_ids": [289],
                "duplicate_company_names": ["Unknown issuer for RU000SYN289"],
                "sample_secids": ["RU000CAN001"],
                "sample_bond_names": ["Synthetic Canonical BO 001"],
                "source_reason": "corporate bond universe",
                "has_financial_report": False,
                "canonical_has_financial_report": False,
                "duplicate_has_financial_report": False,
                "coverage_effective_status": "missing_report",
                "coverage_status": "missing_report",
                "needs_financial_report": True,
                "latest_report_period_year": None,
                "latest_report_period_quarter": None,
                "latest_report_period_end_date": None,
                "legal_name": "Synthetic Canonical LLC",
                "short_name": "Synthetic Canonical",
                "ogrn": "1027700000001",
                "issuer_group_name": None,
                "issuer_role": "legal_issuer",
            }
        ],
        "warnings": [],
        "errors": [],
        "next_steps": ["Review."],
    }
    json_output = tmp_path / "targets.json"
    csv_output = tmp_path / "targets.csv"
    markdown_output = tmp_path / "targets.md"
    collection_output = tmp_path / "collection.csv"

    import_script.write_json_report(report, json_output)
    targets.write_csv_report(report, csv_output)
    targets.write_markdown_report(report, markdown_output)
    targets.write_collection_template(report, collection_output)

    assert json.loads(json_output.read_text(encoding="utf-8"))["total_targets"] == 1
    assert "duplicate_company_ids" in csv_output.read_text(encoding="utf-8")
    assert "# BondRadar Financial Report Target Issuers" in markdown_output.read_text(encoding="utf-8")
    assert "operator_notes" in collection_output.read_text(encoding="utf-8")
