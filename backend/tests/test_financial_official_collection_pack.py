from __future__ import annotations

import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_official_collection_pack as pack  # noqa: E402
import financial_report_import as import_script  # noqa: E402


def test_template_generation_selects_only_collection_ready_issuers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    financial_template = tmp_path / "collection_ready_financial_template.csv"
    evidence_template = tmp_path / "official_source_evidence_template.json"
    checklist = tmp_path / "official_source_checklist.csv"
    json_report = tmp_path / "official_collection_pack.json"
    markdown_report = tmp_path / "official_collection_pack.md"
    calls: list[tuple[str, str]] = []
    _mock_mixed_targets(monkeypatch)

    args = pack.parse_args(
        [
            "--mode",
            "template",
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
            "--period-year",
            "2025",
            "--period-quarter",
            "0",
            "--report-type",
            "annual",
            "--currency",
            "RUB",
            "--accounting-standard",
            "IFRS",
            "--consolidation-scope",
            "consolidated",
            "--value-scale",
            "million",
            "--max-issuers",
            "2",
            "--financial-template-output",
            str(financial_template),
            "--evidence-template-output",
            str(evidence_template),
            "--source-checklist-output",
            str(checklist),
            "--json-output",
            str(json_report),
            "--markdown-output",
            str(markdown_report),
        ]
    )
    report, exit_code = pack.run_pack(args, http_request=_fake_http(calls))
    pack.write_json_report(report, json_report)
    pack.write_markdown_report(report, markdown_report)

    assert exit_code == 0
    assert report["selected_issuer_count"] == 2
    assert [item["company_id"] for item in report["selected_issuers"]] == [18, 67]
    assert all("Unknown issuer" not in item["company_name"] for item in report["selected_issuers"])
    assert all(item["company_id"] != 125 for item in report["selected_issuers"])
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False
    assert report["identity_apply_executed"] is False

    rows = list(csv.DictReader(financial_template.open(encoding="utf-8")))
    assert [row["company_id"] for row in rows] == ["18", "67"]
    assert rows[0]["period_year"] == "2025"
    assert rows[0]["period_quarter"] == "0"
    assert rows[0]["period_start_date"] == "2025-01-01"
    assert rows[0]["period_end_date"] == "2025-12-31"
    assert rows[0]["source"] == "operator_collection"
    assert rows[0]["value_scale"] == "million"
    for field in pack.FINANCIAL_FIELDS:
        assert rows[0][field] == ""

    evidence = json.loads(evidence_template.read_text(encoding="utf-8"))
    assert evidence["status"] == "template"
    assert len(evidence["issuers"]) == 2
    assert evidence["issuers"][0]["identity"]["inn"] == "7708503727"
    assert evidence["issuers"][0]["field_evidence"]["revenue"]["value"] is None
    assert evidence["issuers"][0]["recommended_sources"][0]["url"] == ""

    checklist_rows = list(csv.DictReader(checklist.open(encoding="utf-8")))
    assert len(checklist_rows) == 6
    assert {
        row["recommended_source_type"] for row in checklist_rows[:3]
    } == {
        "issuer_investor_relations",
        "official_disclosure",
        "issuer_annual_report_pdf",
    }
    assert all(row["official_source_url"] == "" for row in checklist_rows)
    assert all(row["source_status"] == "operator_to_find" for row in checklist_rows)
    assert "# Official-Source Financial Collection Pack" in markdown_report.read_text(
        encoding="utf-8"
    )
    assert not any("/paper-trading" in url or url.endswith("/ingest") for _method, url in calls)


def test_include_partial_adds_tmk_followup_to_template(
    tmp_path: Path,
) -> None:
    financial_template = tmp_path / "with_partial.csv"
    args = pack.parse_args(
        [
            "--mode",
            "template",
            "--backend-url",
            "http://testserver",
            "--company-ids",
            "18,67,125",
            "--include-covered",
            "--include-partial",
            "--period-year",
            "2025",
            "--financial-template-output",
            str(financial_template),
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_fake_http([]))
    rows = list(csv.DictReader(financial_template.open(encoding="utf-8")))

    assert exit_code == 0
    assert report["selected_issuer_count"] == 2
    assert report["partial_followup_count"] == 1
    assert [row["company_id"] for row in rows] == ["18", "67", "125"]
    assert rows[-1]["recommended_collection_type"] == "missing_key_fields"


def test_queue_mode_does_not_write_templates_by_default(
    tmp_path: Path,
) -> None:
    args = pack.parse_args(
        [
            "--mode",
            "queue",
            "--backend-url",
            "http://testserver",
            "--company-ids",
            "18,67",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_fake_http([]))

    assert exit_code == 0
    assert report["mode"] == "queue"
    assert report["financial_template_rows"] == 0
    assert report["selected_issuer_count"] == 2


def test_preview_blocks_wikipedia_and_never_calls_ingest(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(
        reviewed,
        [
            _reviewed_row(
                canonical_company_id="18",
                source_url="https://wikipedia.org/wiki/RZD",
                revenue="1000",
            )
        ],
    )
    calls: list[tuple[str, str]] = []
    args = pack.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_preview_http(calls))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["import_executed"] is False
    assert any("blocked source" in item["message"] for item in report["errors"])
    assert calls == []


def test_preview_fails_values_without_source_and_preserves_nulls(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(
        reviewed,
        [
            _reviewed_row(
                canonical_company_id="18",
                source_url="",
                source_file_name="",
                revenue="1000",
                cash="",
            )
        ],
    )
    args = pack.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_preview_http([]))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("financial values require source_url" in item["message"] for item in report["errors"])
    assert report["validation"]["rows"][0]["cash"] in (None, "")


def test_preview_calls_backend_preview_only_for_official_source(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_reviewed_csv(
        reviewed,
        [
            _reviewed_row(
                canonical_company_id="18",
                source_url="https://rzd.ru/report.pdf",
                source_file_name="rzd-report.pdf",
                source_page="12",
                source_table="IFRS statement",
                revenue="1000",
                ebitda="200",
                total_debt="400",
                cash="100",
                interest_expense="50",
            )
        ],
    )
    calls: list[tuple[str, str]] = []
    args = pack.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_preview_http(calls))

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["import_executed"] is False
    assert any(url.endswith("/api/financial-reports/preview") for _method, url in calls)
    assert not any(url.endswith("/api/financial-reports/ingest") for _method, url in calls)
    normalized = report["normalize_report"]["normalized_rows"][0]
    assert normalized["company_id"] == 18
    assert normalized["revenue"] == "1000000000"
    assert normalized["cash"] == "100000000"


def test_preview_blocks_fake_zero_rows(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "zeros.csv"
    _write_reviewed_csv(
        reviewed,
        [
            _reviewed_row(
                canonical_company_id="18",
                source_url="https://rzd.ru/report.pdf",
                source_file_name="rzd-report.pdf",
                revenue="0",
                ebitda="0",
                total_debt="0",
                cash="0",
            )
        ],
    )
    args = pack.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=_preview_http([]))

    assert exit_code == 1
    assert any("many financial fields are zero" in item["message"] for item in report["errors"])


def _fake_http(calls: list[tuple[str, str]]):
    def fake(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/paper-trading" not in url
        assert not url.endswith("/api/financial-reports/ingest")
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
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data=_identity_first_response(),
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    return fake


def _mock_mixed_targets(monkeypatch) -> None:
    def fake_collect(args, http_request):
        return (
            [18, 67, 125],
            {
                18: ["top-predictions", "bond-universe"],
                67: ["top-predictions", "bond-universe"],
                125: ["manual-id"],
            },
            {
                "source": "mixed",
                "safe_sources": ["top-predictions", "bond-universe"],
                "target_count": 3,
                "warnings": [],
                "errors": [],
            },
        )

    monkeypatch.setattr(
        pack.identity_queue.priority_script,
        "_collect_target_company_ids",
        fake_collect,
    )


def _preview_http(calls: list[tuple[str, str]]):
    def fake(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/paper-trading" not in url
        if url.endswith("/api/financial-reports/ingest"):
            raise AssertionError("Task 95 preview must never call ingest")
        if url.endswith("/api/financial-reports/preview"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": []},
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    return fake


def _identity_first_response() -> dict:
    return {
        "status": "warning",
        "company_count": 5,
        "collection_ready_count": 2,
        "identity_review_required_count": 1,
        "already_covered_count": 1,
        "excluded_count": 1,
        "collection_ready": [
            _ready_issuer(18, "РЖД", "7708503727", 0.9),
            _ready_issuer(67, "Мостотрест", "7701045732", 0.85),
        ],
        "identity_review_required": [
            {
                "company_id": 62,
                "company_name": "Unknown issuer for RU000A0JWK74",
            }
        ],
        "already_covered": [
            {
                **_ready_issuer(125, "ТМК", "7710373095", 0.95),
                "has_financial_report": True,
                "risk_scoring_readiness": "partial",
                "recommended_next_fields": ["interest_expense", "net_debt"],
                "recommended_collection": {
                    "collection_type": "missing_key_fields",
                    "required_fields": ["interest_expense", "net_debt"],
                    "optional_fields": ["debt_to_ebitda", "interest_coverage"],
                },
            }
        ],
        "excluded_or_deprioritized": [
            {
                "company_id": 900,
                "company_name": "OFZ Ministry of Finance",
                "issuer_type": "government_like",
            }
        ],
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "identity_apply_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
        "warnings": [],
        "errors": [],
    }


def _ready_issuer(company_id: int, name: str, inn: str, confidence: float) -> dict:
    return {
        "rank": 1 if company_id == 18 else 2,
        "company_id": company_id,
        "company_name": name,
        "canonical_company_id": company_id,
        "canonical_company_name": name,
        "issuer_type": "corporate",
        "classification_confidence": "high",
        "identity_status": "matched",
        "identity_confidence": confidence,
        "priority_score": 100,
        "priority_level": "high",
        "has_financial_report": False,
        "risk_scoring_readiness": "not_ready",
        "source_presence": {"source_labels": ["top-predictions", "bond-universe"]},
        "bond_context": {
            "bond_count": 1,
            "sample_bonds": [{"secid": f"RU{company_id}", "name": f"{name} bond"}],
        },
        "recommended_collection": {
            "collection_type": "full_annual_ifrs_report",
            "required_fields": list(pack.FIELDS_TO_COLLECT),
            "optional_fields": ["debt_to_ebitda", "interest_coverage"],
        },
        "identity": {
            "legal_name": f"PJSC {name}",
            "short_name": name,
            "display_name": name,
            "inn": inn,
            "ogrn": f"10277{company_id:07d}",
            "issuer_role": "legal_issuer",
            "identity_status": "matched",
            "identity_confidence": confidence,
            "review_status": "reviewed",
            "identity_source": "manual_review",
            "source_url": "https://e-disclosure.ru",
        },
        "operator_next_action": "collect_official_financial_report",
    }


def _write_reviewed_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _reviewed_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in pack.CSV_FIELDS}
    row.update(
        {
            "canonical_company_id": "18",
            "company_id": "18",
            "company_name": "РЖД",
            "canonical_company_name": "РЖД",
            "inn": "7708503727",
            "period_year": "2025",
            "period_quarter": "0",
            "period_start_date": "2025-01-01",
            "period_end_date": "2025-12-31",
            "report_type": "annual",
            "currency": "RUB",
            "accounting_standard": "IFRS",
            "consolidation_scope": "consolidated",
            "value_scale": "million",
            "source": "official_issuer_report",
            "source_file_name": "report.pdf",
            "source_page": "1",
            "source_table": "IFRS table",
            "review_status": "reviewed",
        }
    )
    row.update(overrides)
    return row
