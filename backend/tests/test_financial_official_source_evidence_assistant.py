from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_official_collection_pack as pack  # noqa: E402
import financial_official_source_evidence_assistant as assistant  # noqa: E402
import financial_report_import as import_script  # noqa: E402


def test_source_template_creates_intake_for_collection_ready_issuers(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    args = assistant.parse_args(
        [
            "--mode",
            "source-template",
            "--financial-template-input",
            str(financial_template),
            "--evidence-template-input",
            str(evidence_template),
            "--source-checklist-input",
            str(checklist),
            "--source-intake-output",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["issuer_count"] == 2
    assert intake.is_file()
    payload = json.loads(intake.read_text(encoding="utf-8"))
    assert [item["company_id"] for item in payload["issuer_sources"]] == [18, 67]
    assert all("Unknown issuer" not in item["company_name"] for item in payload["issuer_sources"])
    for item in payload["issuer_sources"]:
        assert len(item["source_candidates"]) == 3
        assert all(source["url"] == "" for source in item["source_candidates"])
        assert all(source["status"] == "operator_to_fill" for source in item["source_candidates"])
    assert report["import_executed"] is False


def test_source_discover_creates_official_like_candidates_without_values(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    discovered = tmp_path / "official_source_intake_discovered.json"
    _build_source_template(financial_template, evidence_template, checklist, intake)
    args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--source-intake-output",
            str(discovered),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["issuer_count"] == 2
    assert report["candidate_count"] == 6
    assert report["needs_operator_review_count"] == 4
    assert report["valid_official_source_count"] == 0
    assert report["blocked_source_count"] == 0
    assert report["import_executed"] is False

    payload = json.loads(discovered.read_text(encoding="utf-8"))
    assert [item["company_id"] for item in payload["issuer_sources"]] == [18, 67]
    rzd_sources = payload["issuer_sources"][0]["source_candidates"]
    assert rzd_sources[0]["source_type"] == "issuer_investor_relations"
    assert rzd_sources[0]["url"] == "https://rzd.ru/"
    assert rzd_sources[0]["status"] == "needs_operator_review"
    assert rzd_sources[0]["confidence"] == "medium"
    assert rzd_sources[1]["url"] == "https://www.e-disclosure.ru/"
    assert rzd_sources[1]["status"] == "needs_operator_review"
    assert rzd_sources[2]["source_type"] == "issuer_annual_report_pdf"
    assert rzd_sources[2]["url"] == ""
    assert rzd_sources[2]["status"] == "operator_to_find"
    for issuer in payload["issuer_sources"]:
        for source in issuer["source_candidates"]:
            assert "revenue" not in source
            assert "values" not in source


def test_source_discover_unknown_issuer_does_not_invent_url(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "unknown_intake.json"
    _write_source_intake(
        intake,
        [
            {
                "company_id": 777,
                "company_name": "Unknown issuer for RU000TEST",
                "canonical_company_id": 777,
                "canonical_company_name": "Unknown issuer for RU000TEST",
                "source_candidates": [
                    {
                        "source_type": "issuer_investor_relations",
                        "url": "",
                        "document_title": "",
                        "document_date": "",
                        "report_period": "2025",
                        "status": "operator_to_fill",
                        "notes": "Operator must identify issuer first.",
                    }
                ],
            }
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert any("no official source discovery hints" in item["message"] for item in report["warnings"])
    for source in report["issuer_sources"][0]["source_candidates"]:
        assert source["url"] == ""
        assert source["status"] in {"operator_to_fill", "operator_to_find"}


def test_source_discover_blocks_existing_wikipedia_source(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "blocked_intake.json"
    _write_source_intake(
        intake,
        [
            _source_issuer(
                18,
                "RZD",
                "issuer_investor_relations",
                "https://wikipedia.org/wiki/RZD",
                "Wikipedia page",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["blocked_source_count"] == 1
    blocked = report["issuer_sources"][0]["source_candidates"][0]
    assert blocked["status"] == "blocked_source"
    assert "Blocked source" in blocked["notes"]


def test_source_discover_probe_is_optional_and_records_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    _build_source_template(financial_template, evidence_template, checklist, intake)
    calls: list[str] = []

    def fake_probe(url: str, *, timeout_seconds: float, max_bytes: int) -> dict:
        calls.append(url)
        return {
            "status": "ok",
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "error": None,
        }

    monkeypatch.setattr(assistant, "_probe_url", fake_probe)
    no_probe_args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
        ]
    )
    no_probe_report, _exit_code = assistant.run_assistant(no_probe_args)
    assert calls == []
    assert no_probe_report["discovered_candidate_count"] == 0

    probe_args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--probe-urls",
        ]
    )
    probe_report, exit_code = assistant.run_assistant(probe_args)

    assert exit_code == 0
    assert calls
    assert probe_report["discovered_candidate_count"] == 4
    first = probe_report["issuer_sources"][0]["source_candidates"][0]
    assert first["status"] == "discovered_candidate"
    assert first["probe_status"] == "ok"
    assert first["probe_content_type"] == "text/html; charset=utf-8"


def test_source_discover_probe_failure_warns_not_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    _build_source_template(financial_template, evidence_template, checklist, intake)

    def fake_probe(url: str, *, timeout_seconds: float, max_bytes: int) -> dict:
        return {
            "status": "failed",
            "http_status": None,
            "content_type": None,
            "error": "timeout",
        }

    monkeypatch.setattr(assistant, "_probe_url", fake_probe)
    args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--probe-urls",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    first = report["issuer_sources"][0]["source_candidates"][0]
    assert first["status"] == "needs_operator_review"
    assert first["probe_status"] == "failed"
    assert "operator review required" in first["notes"]
    assert any("source probe failed" in item["message"] for item in report["warnings"])


def test_source_validate_accepts_discovery_candidates_as_warnings(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    discovered = tmp_path / "official_source_intake_discovered.json"
    _build_source_template(financial_template, evidence_template, checklist, intake)
    discover_args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--source-intake-output",
            str(discovered),
        ]
    )
    assistant.run_assistant(discover_args)
    validate_args = assistant.parse_args(
        [
            "--mode",
            "source-validate",
            "--source-intake-input",
            str(discovered),
        ]
    )

    report, exit_code = assistant.run_assistant(validate_args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["invalid_source_count"] == 0
    assert any("discovery candidate" in item["message"] for item in report["warnings"])


def test_source_validate_exact_official_source_passes(
    tmp_path: Path,
) -> None:
    source_intake = tmp_path / "exact_source.json"
    _write_source_intake(
        source_intake,
        [
            _source_issuer(
                18,
                "RZD",
                "official_issuer_report",
                "https://rzd.ru/investors/ifrs-2025.pdf",
                "IFRS consolidated statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "source-validate",
            "--source-intake-input",
            str(source_intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["valid_source_count"] == 1
    assert report["invalid_source_count"] == 0


def test_document_resolve_from_discovered_intake_does_not_invent_pdf(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    resolved = tmp_path / "official_source_intake_resolved.json"
    document_output = tmp_path / "official_report_documents.json"
    checklist = tmp_path / "official_report_document_checklist.csv"
    args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--source-intake-output",
            str(resolved),
            "--document-output",
            str(document_output),
            "--document-checklist-output",
            str(checklist),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["issuer_count"] == 2
    assert report["document_candidate_count"] == 6
    assert report["resolved_document_count"] == 0
    assert report["import_executed"] is False
    assert resolved.is_file()
    assert document_output.is_file()
    assert checklist.is_file()
    payload = json.loads(document_output.read_text(encoding="utf-8"))
    assert [item["company_id"] for item in payload["issuers"]] == [18, 67]
    for issuer in payload["issuers"]:
        for document in issuer["document_candidates"]:
            assert "revenue" not in document
            assert "values" not in document
            if document["source_type"] == "issuer_annual_report_pdf":
                assert document["document_url"] == ""
                assert document["document_status"] == "operator_to_find"
                assert document["resolution_method"] == "exact_pdf_not_invented"
    rows = list(csv.DictReader(checklist.open(encoding="utf-8")))
    assert len(rows) == 6
    assert rows[0]["document_url"] == ""
    assert rows[0]["operator_action"] == "select_exact_official_report_document"


def test_document_intake_template_creates_one_fill_row_per_issuer(
    tmp_path: Path,
) -> None:
    document_input = tmp_path / "official_report_documents.json"
    intake_output = tmp_path / "exact_document_intake.json"
    csv_output = tmp_path / "exact_document_intake.csv"
    _write_document_report(
        document_input,
        [
            _document_report_issuer(18, "RZD", "https://rzd.ru/"),
            _document_report_issuer(67, "Mostotrest", "https://mostotrest.ru/"),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-template",
            "--document-input",
            str(document_input),
            "--document-intake-output",
            str(intake_output),
            "--document-intake-csv-output",
            str(csv_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "template"
    assert report["issuer_count"] == 2
    assert report["document_template_count"] == 2
    assert report["import_executed"] is False
    assert intake_output.is_file()
    assert csv_output.is_file()
    payload = json.loads(intake_output.read_text(encoding="utf-8"))
    assert [item["company_id"] for item in payload["documents"]] == [18, 67]
    for item in payload["documents"]:
        assert item["source_type"] == "official_issuer_report"
        assert item["document_url"] == ""
        assert item["document_title"] == ""
        assert item["operator_review_status"] == "operator_to_fill"
        assert "e-disclosure.ru" in item["source_url_context"]
        assert "revenue" not in item
        assert "values" not in item
    rows = list(csv.DictReader(csv_output.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["document_url"] == ""


def test_document_intake_validate_accepts_reviewed_exact_official_document(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    item = _document_item(
        18,
        "https://rzd.ru/investors/annual-ifrs-2025.pdf",
        "Annual audited consolidated IFRS financial statements 2025",
    )
    item["operator_review_status"] = "operator_reviewed"
    _write_document_intake(intake, [item])
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-validate",
            "--document-intake-input",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["valid_document_count"] == 1
    assert report["invalid_document_count"] == 0
    assert report["import_executed"] is False


def test_document_intake_validate_rejects_unreviewed_missing_metadata(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "empty_document_intake.json"
    item = _document_item(18, "", "")
    item["operator_review_status"] = "operator_to_fill"
    _write_document_intake(intake, [item])
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-validate",
            "--document-intake-input",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("operator_review_status must be reviewed" in item["message"] for item in report["errors"])
    assert any("document_url is required" in item["message"] for item in report["errors"])
    assert any("document_title is required" in item["message"] for item in report["errors"])


def test_document_intake_validate_blocks_bad_and_financial_fields(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "bad_document_intake.json"
    item = _document_item(
        18,
        "https://wikipedia.org/wiki/RZD",
        "Annual audited consolidated IFRS financial statements 2025",
    )
    item["debt"] = "100"
    _write_document_intake(intake, [item])
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-validate",
            "--document-intake-input",
            str(intake),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert any("blocked unofficial source domain" in item["message"] for item in report["errors"])
    assert any("financial values are forbidden" in item["message"] for item in report["errors"])


def test_document_intake_validate_unknown_allow_remains_review_required(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "unknown_document_intake.json"
    _write_document_intake(
        intake,
        [
            _document_item(
                18,
                "https://example.com/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-validate",
            "--document-intake-input",
            str(intake),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["valid_document_count"] == 0
    assert report["needs_operator_review_count"] == 1


def test_document_intake_fill_without_candidates_keeps_rows_unfilled(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    output = tmp_path / "exact_document_intake_filled.json"
    csv_output = tmp_path / "exact_document_intake_filled.csv"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--document-intake-output",
            str(output),
            "--document-intake-csv-output",
            str(csv_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["issuer_count"] == 2
    assert report["filled_document_count"] == 0
    assert report["valid_document_count"] == 0
    assert report["needs_operator_review_count"] == 2
    assert any("candidate file not provided" in item["message"] for item in report["warnings"])
    payload = json.loads(output.read_text(encoding="utf-8"))
    for item in payload["documents"]:
        assert item["document_url"] == ""
        assert item["operator_review_status"] == "operator_to_fill"
        assert "revenue" not in item
        assert "values" not in item
    rows = list(csv.DictReader(csv_output.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["document_url"] == ""


def test_document_intake_fill_with_valid_candidates_passes_validation(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    output = tmp_path / "exact_document_intake_filled.json"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            ),
            _document_item(
                67,
                "https://mostotrest.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            ),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
            "--document-intake-output",
            str(output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["filled_document_count"] == 2
    assert report["valid_document_count"] == 2
    assert report["needs_operator_review_count"] == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert all(item["operator_review_status"] == "operator_reviewed" for item in payload["documents"])
    validate_args = assistant.parse_args(
        [
            "--mode",
            "document-intake-validate",
            "--document-intake-input",
            str(output),
        ]
    )
    validate_report, validate_exit = assistant.run_assistant(validate_args)
    assert validate_exit == 0
    assert validate_report["status"] == "passed"
    assert validate_report["valid_document_count"] == 2


def test_document_intake_fill_unknown_allow_remains_review_required(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "unknown_candidates.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://example.com/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["valid_document_count"] == 0
    assert report["needs_operator_review_count"] == 1
    assert report["documents"][0]["operator_review_status"] == "needs_operator_review"


def test_document_intake_fill_blocks_bad_source_and_financial_values(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "bad_candidates.json"
    bad = _document_item(
        18,
        "https://wikipedia.org/wiki/RZD",
        "Annual audited consolidated IFRS financial statements 2025",
    )
    bad["revenue"] = "1000"
    bad["financial_values"] = {"cash": "100"}
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(candidates, [bad])
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("blocked unofficial source domain" in item["message"] for item in report["errors"])
    assert any("financial values are forbidden" in item["message"] for item in report["errors"])


def test_document_intake_fill_output_works_with_document_resolve(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    filled = tmp_path / "exact_document_intake_filled.json"
    resolved = tmp_path / "resolved_source_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    fill_args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
            "--document-intake-output",
            str(filled),
        ]
    )
    fill_report, fill_exit = assistant.run_assistant(fill_args)
    assert fill_exit == 0
    assert fill_report["valid_document_count"] == 1

    resolve_args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(filled),
            "--source-intake-output",
            str(resolved),
        ]
    )
    resolve_report, resolve_exit = assistant.run_assistant(resolve_args)

    assert resolve_exit == 0
    assert resolve_report["resolved_document_count"] == 1
    resolved_payload = json.loads(resolved.read_text(encoding="utf-8"))
    assert any(
        source.get("status") == "valid_official_source"
        for source in resolved_payload["issuer_sources"][0]["source_candidates"]
    )


def test_document_intake_fill_probe_and_download_are_optional(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    probes: list[str] = []
    downloads: list[str] = []

    def fake_probe(url: str, *, timeout_seconds: float, max_bytes: int) -> dict:
        probes.append(url)
        return {
            "status": "ok",
            "http_status": 200,
            "content_type": "application/pdf",
            "error": None,
        }

    def fake_download(document: dict, download_dir: Path) -> dict:
        downloads.append(document["document_url"])
        return {
            "url": document["document_url"],
            "local_path": str(download_dir / "annual-ifrs-2025.pdf"),
            "sha256": "abc123",
            "size_bytes": 123,
            "content_type": "application/pdf",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(assistant, "_probe_url", fake_probe)
    monkeypatch.setattr(assistant, "_download_valid_document", fake_download)
    no_network_args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
        ]
    )
    no_network_report, _exit = assistant.run_assistant(no_network_args)
    assert no_network_report["valid_document_count"] == 1
    assert probes == []
    assert downloads == []

    network_args = assistant.parse_args(
        [
            "--mode",
            "document-intake-fill",
            "--document-intake-input",
            str(intake),
            "--exact-document-candidates-input",
            str(candidates),
            "--probe-urls",
            "--download-documents",
            "--document-download-dir",
            str(tmp_path / "downloads"),
        ]
    )
    network_report, exit_code = assistant.run_assistant(network_args)

    assert exit_code == 0
    assert probes == ["https://rzd.ru/investors/annual-ifrs-2025.pdf"]
    assert downloads == ["https://rzd.ru/investors/annual-ifrs-2025.pdf"]
    assert network_report["documents"][0]["probe_status"] == "ok"
    assert network_report["documents"][0]["download"]["sha256"] == "abc123"


def test_document_quality_gate_without_candidates_fails_safely(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    output = tmp_path / "gate_report.json"
    markdown = tmp_path / "gate_report.md"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    exit_code = assistant.main(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18,67",
            "--json-output",
            str(output),
            "--markdown-output",
            str(markdown),
        ]
    )

    assert exit_code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["gate_passed"] is False
    assert report["ready_for_value_extraction"] is False
    assert report["ready_for_import"] is False
    assert report["required_issuer_count"] == 2
    assert report["covered_required_issuer_count"] == 0
    assert report["valid_document_count"] == 0
    assert report["resolved_document_count"] == 0
    assert report["needs_operator_review_count"] == 2
    assert report["read_only"] is True
    assert any("candidate file is required" in item["message"] for item in report["errors"])
    assert "Exact Document Quality Gate" in markdown.read_text(encoding="utf-8")
    assert "revenue" not in json.dumps(report, ensure_ascii=False)


def test_document_quality_gate_with_valid_candidates_passes(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    filled = tmp_path / "exact_document_intake_gate.json"
    resolved_source = tmp_path / "resolved_source_intake.json"
    document_output = tmp_path / "resolved_documents.json"
    checklist = tmp_path / "document_checklist.csv"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            ),
            _document_item(
                67,
                "https://mostotrest.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            ),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18,67",
            "--document-intake-output",
            str(filled),
            "--source-intake-output",
            str(resolved_source),
            "--document-output",
            str(document_output),
            "--document-checklist-output",
            str(checklist),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["gate_passed"] is True
    assert report["ready_for_value_extraction"] is True
    assert report["ready_for_import"] is False
    assert report["covered_required_issuer_count"] == 2
    assert report["filled_document_count"] == 2
    assert report["valid_document_count"] == 2
    assert report["resolved_document_count"] == 2
    assert report["needs_operator_review_count"] == 0
    assert report["invalid_document_count"] == 0
    assert report["fill_report"]["status"] == "passed"
    assert report["validation_report"]["status"] == "passed"
    assert report["resolve_report"]["status"] == "passed"
    assert filled.is_file()
    assert resolved_source.is_file()
    assert document_output.is_file()
    assert checklist.is_file()


def test_document_quality_gate_missing_required_issuer_fails(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/"),
        ],
    )
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18,67",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["covered_required_issuer_count"] == 1
    missing = [item for item in report["required_issuers"] if item["company_id"] == 67][0]
    assert missing["gate_status"] == "failed"
    assert "missing" in missing["reason"]


def test_document_quality_gate_unknown_source_cannot_pass(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "unknown_candidates.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://example.com/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    default_args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18",
        ]
    )
    allow_args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )

    default_report, default_exit = assistant.run_assistant(default_args)
    allow_report, allow_exit = assistant.run_assistant(allow_args)

    assert default_exit == 1
    assert default_report["status"] == "failed"
    assert any("official allowlist" in item["message"] for item in default_report["errors"])
    assert allow_exit == 1
    assert allow_report["status"] == "failed"
    assert allow_report["valid_document_count"] == 0
    assert allow_report["ready_for_value_extraction"] is False
    assert allow_report["required_issuers"][0]["document_status"] != "valid_official_document"


def test_document_quality_gate_blocks_bad_source_and_financial_values(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "bad_candidates.json"
    bad = _document_item(
        18,
        "https://wikipedia.org/wiki/RZD",
        "Annual audited consolidated IFRS financial statements 2025",
    )
    bad["values"] = {"revenue": {"value": "1"}}
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_document_intake(candidates, [bad])
    args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["ready_for_value_extraction"] is False
    assert any("blocked unofficial source domain" in item["message"] for item in report["errors"])
    assert any("financial values are forbidden" in item["message"] for item in report["errors"])


def test_document_quality_gate_partial_gate_warns_only(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidates = tmp_path / "exact_document_candidates.json"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/"),
        ],
    )
    _write_document_intake(
        candidates,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-quality-gate",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--exact-document-candidates-input",
            str(candidates),
            "--required-company-ids",
            "18,67",
            "--allow-partial-gate",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["gate_passed"] is False
    assert report["ready_for_value_extraction"] is False
    assert report["ready_for_import"] is False
    assert report["covered_required_issuer_count"] == 1
    assert report["errors"] == []
    assert any("missing" in item["message"] for item in report["warnings"])


def test_document_candidate_discover_mocked_official_html_runs_quality_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidate_output = tmp_path / "exact_document_candidates.json"
    candidate_csv = tmp_path / "exact_document_candidates.csv"
    gate_output = tmp_path / "quality_gate.json"
    gate_markdown = tmp_path / "quality_gate.md"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/": '<a href="/reports/rzd-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>',
            "https://mostotrest.ru/": '<a href="/reports/mostotrest-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>',
            "https://www.e-disclosure.ru/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-candidate-discover",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18,67",
            "--candidate-output",
            str(candidate_output),
            "--candidate-csv-output",
            str(candidate_csv),
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
            "--quality-gate-markdown-output",
            str(gate_markdown),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["candidate_count"] == 2
    assert report["reviewed_candidate_count"] == 2
    assert report["blocked_candidate_count"] == 0
    assert all(item["operator_review_status"] == "operator_reviewed" for item in report["documents"])
    assert all(item["candidate_score"] >= 90 for item in report["documents"])
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    candidate_payload = json.loads(candidate_output.read_text(encoding="utf-8"))
    assert len(candidate_payload["documents"]) == 2
    rows = list(csv.DictReader(candidate_csv.open(encoding="utf-8")))
    assert len(rows) == 2
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["ready_for_value_extraction"] is True
    assert gate["ready_for_import"] is False
    assert "Exact Document Quality Gate" in gate_markdown.read_text(encoding="utf-8")


def test_document_candidate_discover_without_exact_links_warns_and_gate_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/"),
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/": '<a href="/investors/">Investors</a>',
            "https://mostotrest.ru/": '<a href="/investors/">Investors</a>',
            "https://www.e-disclosure.ru/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-candidate-discover",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18,67",
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0
    assert any("no exact official document candidates found" in item["message"] for item in report["warnings"])
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_document_candidate_discover_blocks_and_quarantines_external_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/": """
                <a href="https://wikipedia.org/wiki/RZD">Annual audited consolidated IFRS financial statements 2025</a>
                <a href="https://example.com/report-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>
            """,
            "https://www.e-disclosure.ru/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-candidate-discover",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["blocked_candidate_count"] == 1
    assert report["candidate_count"] == 1
    assert report["documents"][0]["operator_review_status"] == "needs_operator_review"
    assert report["documents"][0]["document_url"].startswith("https://example.com/")


def test_document_candidate_discover_scoring_rejects_bad_documents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/": """
                <a href="/reports/presentation-ifrs-2025.pdf">Annual IFRS presentation 2025</a>
                <a href="/reports/q4-ifrs-2025.pdf">Quarterly IFRS financial statements 2025</a>
                <a href="/reports/annual-ifrs-2024.pdf">Annual audited consolidated IFRS financial statements 2024</a>
                <a href="/reports/prospectus-2025.pdf">Bond prospectus 2025</a>
            """,
            "https://www.e-disclosure.ru/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-candidate-discover",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0


def test_document_candidate_discover_network_failures_warn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])

    def fake_fetch(url: str, *, timeout_seconds: float, max_bytes: int, user_agent: str) -> dict:
        return {"status": "error", "url": url, "error": "timeout"}

    monkeypatch.setattr(assistant, "_fetch_candidate_page", fake_fetch)
    args = assistant.parse_args(
        [
            "--mode",
            "document-candidate-discover",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0
    assert any("failed to fetch official seed page" in item["message"] for item in report["warnings"])


def test_exact_document_discover_from_reviewed_seed_finds_annual_ifrs_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    candidate_output = tmp_path / "exact_document_candidates_from_seeds.json"
    candidate_csv = tmp_path / "exact_document_candidates_from_seeds.csv"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/mostotrest-annual-ifrs-financial-statements-2025.pdf">
                    Annual audited consolidated IFRS financial statements 2025
                </a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-availability-current-date",
            "2026-05-25",
            "--exact-document-candidate-output",
            str(candidate_output),
            "--exact-document-candidate-csv-output",
            str(candidate_csv),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["mode"] == "exact-document-discover-from-seeds"
    assert report["candidate_count"] == 1
    assert report["reviewed_candidate_count"] == 1
    document = report["documents"][0]
    assert document["operator_review_status"] == "operator_reviewed"
    assert document["document_status"] == "valid_official_document"
    assert document["candidate_score"] >= 95
    assert document["document_url"].endswith("mostotrest-annual-ifrs-financial-statements-2025.pdf")
    assert document["document_period_year"] == "2025"
    assert document["document_period_status"] == "target_period"
    assert document["report_type_match_status"] == "annual_match"
    assert document["accounting_standard_match_status"] == "standard_match"
    assert document["can_use_as_target_period_evidence"] is True
    availability = _availability_for(report)
    assert availability["availability_status"] == "exact_target_period_document_found"
    assert availability["can_use_as_target_period_evidence"] is True
    assert availability["historical_fallback_scope"] == "none"
    assert availability["reporting_window_policy"]["primary_expected_deadline_date"] == "2026-04-30"
    assert availability["reporting_window_policy"]["deadline_status"] == "after_primary_deadline_within_grace_window"
    assert report["target_reporting_period_availability_count"] == 1
    assert report["availability_status_counts"]["exact_target_period_document_found"] == 1
    operator_row = report["availability_operator_rows"][0]
    assert operator_row["recommended_next_step"] == "proceed_to_quality_gate_or_extraction_preview"
    assert operator_row["gate_status"] == "quality_gate_not_run"
    assert operator_row["ready_for_value_extraction"] is False
    coverage = _coverage_for(report)
    assert coverage["coverage_status"] == "strong_target_evidence_available"
    assert coverage["coverage_operator_action"] == "no_source_action_required"
    assert coverage["can_use_as_target_period_evidence"] is True
    assert coverage["ready_for_value_extraction"] is False
    queue_action = report["operator_review_queue"][0]
    assert queue_action["queue_action_type"] == "no_operator_action_required"
    assert queue_action["queue_priority"] == "low"
    assert queue_action["queue_status"] == "resolved_or_not_required"
    assert queue_action["is_blocking_next_stage"] is False
    assert queue_action["ready_for_value_extraction"] is False
    assert queue_action["action_id"] == "financial_report:67:2025:annual:IFRS:no_operator_action_required"
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    payload = json.loads(candidate_output.read_text(encoding="utf-8"))
    assert payload["documents"][0]["document_url"] == document["document_url"]
    rows = list(csv.DictReader(candidate_csv.open(encoding="utf-8")))
    assert rows[0]["document_url"] == document["document_url"]
    assert report["read_only"] is True
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False


def test_exact_document_discover_from_reviewed_seed_without_exact_docs_warns_and_gate_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/ru/invest/">Investors</a>
                <a href="/ru/invest/information-disclosure/">Information disclosure</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0
    assert any(item["document_status"] == "not_found" for item in report["documents"])
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_exact_document_discover_from_reviewed_seed_filters_bad_document_types(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>
                <a href="/reports/presentation-ifrs-2025.pdf">Annual IFRS presentation 2025</a>
                <a href="/reports/q1-ifrs-2025.pdf">Quarterly IFRS financial statements 2025</a>
                <a href="/reports/prospectus-2025.pdf">Bond prospectus 2025</a>
                <a href="https://wikipedia.org/wiki/Mostotrest">Annual IFRS financial statements 2025</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    kept = [item for item in report["documents"] if item.get("filter_status") == "kept" and item.get("document_url")]
    assert len(kept) == 1
    assert kept[0]["document_url"].endswith("annual-ifrs-financial-statements-2025.pdf")
    assert kept[0]["candidate_rank"] == 1
    filtered = [item for item in report["documents"] if str(item.get("filter_status", "")).startswith("filtered")]
    assert filtered
    assert report["blocked_candidate_count"] == 1
    assert not any("presentation" in item["document_url"] and item["filter_status"] == "kept" for item in report["documents"])
    assert not any("prospectus" in item["document_url"] and item["filter_status"] == "kept" for item in report["documents"])


def test_exact_document_discover_filters_legal_policy_pdfs_from_seed_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    filled_output = tmp_path / "exact_document_intake_filled.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/upload/files/policy_conf.pdf">Политика конфиденциальности</a>
                <a href="/upload/files/user_agreement.pdf">Пользовательское соглашение</a>
                <a href="/upload/files/policy_cookies.pdf">Политика использования cookie-файлов</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
            "--run-document-intake-fill",
            "true",
            "--document-intake-output",
            str(filled_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    assert report["filtered_wrong_document_type_count"] >= 3
    assert report["privacy_policy_document_count"] >= 1
    assert report["cookie_policy_document_count"] >= 1
    assert report["user_agreement_document_count"] >= 1
    legal_docs = [item for item in report["documents"] if item.get("document_url")]
    assert {item["document_kind"] for item in legal_docs} >= {
        "privacy_policy_document",
        "cookie_policy_document",
        "user_agreement_document",
    }
    assert all(item["filter_status"] == "filtered_wrong_document_type" for item in legal_docs)
    filled = json.loads(filled_output.read_text(encoding="utf-8"))
    assert filled["documents"][0]["document_url"] == ""
    assert "revenue" not in json.dumps(report, ensure_ascii=False)


def test_exact_document_discover_follows_category_pages_to_depth_two_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/ru/invest/information-disclosure/issuer-reports/">Годовые отчеты эмитента</a>
                <a href="/ru/invest/information-disclosure/accounting-statements/">Годовая бухгалтерская (финансовая) отчетность</a>
            """,
            "https://mostotrest.ru/ru/invest/information-disclosure/issuer-reports/": """
                <a href="/upload/reports/annual-ifrs-financial-statements-2025.pdf">
                    Годовая консолидированная финансовая отчетность по МСФО за 2025 год
                </a>
            """,
            "https://mostotrest.ru/ru/invest/information-disclosure/accounting-statements/": """
                <a href="/upload/reports/annual-ifrs-financial-statements-2025.pdf">
                    Годовая консолидированная финансовая отчетность по МСФО за 2025 год
                </a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-category-pages",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 1
    assert report["exact_report_document_count"] >= 1
    assert report["category_page_count"] >= 2
    assert report["category_pages_followed"]
    category_docs = [item for item in report["documents"] if item.get("is_category_page")]
    assert category_docs
    assert all(item["document_status"] == "category_page" for item in category_docs)
    exact_docs = [item for item in report["documents"] if item.get("document_kind") == "exact_report_document"]
    assert len(exact_docs) == 1
    exact = exact_docs[0]
    assert exact["crawl_depth"] == 2
    assert exact["parent_seed_url"].endswith("/information-disclosure/issuer-reports/") or exact["parent_seed_url"].endswith("/information-disclosure/accounting-statements/")
    assert exact["source_chain"][0] == "https://mostotrest.ru/ru/invest/financial-results/"
    assert exact["source_chain"][-1].endswith("annual-ifrs-financial-statements-2025.pdf")
    assert exact["operator_review_status"] == "operator_reviewed"


def test_exact_document_discover_category_pages_alone_do_not_flow_to_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    filled_output = tmp_path / "exact_document_intake_filled.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/ru/invest/information-disclosure/accounting-statements/">Годовая бухгалтерская (финансовая) отчетность</a>
            """,
            "https://mostotrest.ru/ru/invest/information-disclosure/accounting-statements/": """
                <a href="/ru/invest/">Инвесторам</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--run-document-intake-fill",
            "true",
            "--document-intake-output",
            str(filled_output),
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    filled = json.loads(filled_output.read_text(encoding="utf-8"))
    assert filled["documents"][0]["document_url"] == ""
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_exact_document_discover_depth_two_pdf_can_pass_single_issuer_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    filled_output = tmp_path / "exact_document_intake_filled.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/ru/invest/information-disclosure/accounting-statements/">Годовая бухгалтерская (финансовая) отчетность</a>
            """,
            "https://mostotrest.ru/ru/invest/information-disclosure/accounting-statements/": """
                <a href="/upload/reports/annual-ifrs-financial-statements-2025.pdf">
                    Annual audited consolidated IFRS financial statements 2025
                </a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--run-document-intake-fill",
            "true",
            "--document-intake-output",
            str(filled_output),
            "--run-document-intake-validate",
            "true",
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 1
    assert report["document_intake_fill_report"]["valid_document_count"] == 1
    assert report["document_intake_validation_report"]["valid_document_count"] == 1
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["ready_for_value_extraction"] is True
    assert gate["ready_for_import"] is False


def test_exact_document_discover_second_level_crawl_is_controlled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    pages = {
        "https://mostotrest.ru/ru/invest/financial-results/": """
            <a href="/ru/invest/information-disclosure/accounting-statements/">Годовая бухгалтерская (финансовая) отчетность</a>
            <a href="https://rzd.ru/reports/accounting-statements/">Годовая бухгалтерская (финансовая) отчетность</a>
            <a href="/upload/files/user_agreement.pdf">Пользовательское соглашение</a>
        """,
        "https://mostotrest.ru/ru/invest/information-disclosure/accounting-statements/": """
            <a href="/upload/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>
        """,
        "https://rzd.ru/reports/accounting-statements/": """
            <a href="https://rzd.ru/reports/rzd-annual-ifrs-2025.pdf">Annual IFRS financial statements 2025</a>
        """,
    }
    fetched: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float, max_bytes: int, user_agent: str) -> dict:
        fetched.append(url)
        return {
            "status": "ok",
            "url": url,
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "body": pages.get(url, "<html></html>"),
            "size_bytes": len(pages.get(url, "")),
        }

    monkeypatch.setattr(assistant, "_fetch_candidate_page", fake_fetch)
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert "https://rzd.ru/reports/accounting-statements/" not in fetched
    assert "https://mostotrest.ru/ru/invest/information-disclosure/accounting-statements/" in fetched
    assert not any(item.get("document_url", "").startswith("https://rzd.ru/") and item.get("filter_status") == "kept" for item in report["documents"])
    assert not any("user_agreement" in item.get("document_url", "") and item.get("filter_status") == "kept" for item in report["documents"])


def test_exact_document_discover_filters_wrong_year_reports_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    filled_output = tmp_path / "exact_document_intake_filled.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/2019_12_Mostotrest_IFRS_Accounts.pdf">2019_12_Mostotrest_IFRS_Accounts.pdf</a>
                <a href="/reports/Financial_Statements_2018_MOSTOTREST_RUS.pdf">IFRS annual financial statements 2018</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-wrong-period",
            "true",
            "--run-document-intake-fill",
            "true",
            "--document-intake-output",
            str(filled_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    assert report["filtered_wrong_period_count"] >= 2
    wrong = [item for item in report["documents"] if item.get("filter_status") == "filtered_wrong_period"]
    assert {item["document_period_year"] for item in wrong} >= {"2019", "2018"}
    assert all(item["document_period_status"] == "wrong_period" for item in wrong)
    filled = json.loads(filled_output.read_text(encoding="utf-8"))
    assert filled["documents"][0]["document_url"] == ""


def test_exact_document_discover_filters_interim_reports_for_annual_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/IFRS_1H2021.pdf">IFRS 1H2021</a>
                <a href="/reports/IFRS_30062019_RUS.pdf">IFRS 30062019 RUS</a>
                <a href="/reports/6m2017_Condensed_IFRS_FS_Mostotrest_RUS.pdf">6m2017 Condensed IFRS FS</a>
                <a href="/reports/quarterly-ifrs-2025.pdf">Quarterly IFRS financial statements 2025</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-wrong-report-type",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    interim = [item for item in report["documents"] if item.get("filter_status") == "filtered_wrong_report_type"]
    assert len(interim) >= 4
    assert all(item["report_type_match_status"] == "interim_or_quarterly_mismatch" for item in interim)
    assert not any(item["filter_status"] == "kept" for item in report["documents"] if item.get("document_url"))


def test_exact_document_discover_unknown_period_is_not_downstream_eligible_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/ifrs-financial-statements.pdf">Annual IFRS financial statements</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    document = next(item for item in report["documents"] if item.get("document_url"))
    assert document["document_period_status"] == "unknown_period"
    assert document["filter_status"] == "filtered_unknown_period"
    assert document["operator_review_status"] == "operator_to_fill"
    assert report["filtered_unknown_period_count"] >= 1


def test_exact_document_discover_prior_year_fallback_is_disabled_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-wrong-period",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    document = next(item for item in report["documents"] if item.get("document_url"))
    assert document["document_period_year"] == "2024"
    assert document["document_period_status"] == "wrong_period"
    assert document["filter_status"] == "filtered_wrong_period"


def test_exact_document_discover_prior_year_fallback_is_diagnostic_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-period-policy",
            "target-or-prior-year-fallback",
            "--exact-document-allow-prior-year-fallback",
            "true",
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    fallback = next(item for item in report["documents"] if item.get("document_url"))
    assert fallback["document_period_year"] == "2024"
    assert fallback["document_period_status"] == "prior_period_fallback_candidate"
    assert fallback["fallback_status"] == "fallback_candidate"
    assert fallback["filter_status"] == "kept_fallback"
    assert fallback["operator_review_status"] == "needs_operator_review"
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_exact_document_discover_filters_wrong_accounting_standard_for_ifrs_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ras-financial-statements-2025.pdf">Годовая бухгалтерская отчетность по РСБУ за 2025 год</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 0
    document = next(item for item in report["documents"] if item.get("document_url"))
    assert document["document_period_status"] == "target_period"
    assert document["accounting_standard_match_status"] == "standard_mismatch"
    assert document["filter_status"] == "filtered_wrong_standard"
    assert report["filtered_wrong_standard_count"] >= 1


def test_exact_document_discover_from_reviewed_seeds_gate_required_issuers_remains_strict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate_all.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18,67",
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_count"] == 1
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False
    assert any(item["company_id"] == 18 and item["gate_status"] == "failed" for item in gate["required_issuers"])


def test_exact_document_discover_from_reviewed_seeds_gate_can_pass_for_single_resolved_issuer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate_mostotrest.json"
    filled_output = tmp_path / "exact_document_intake_filled.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--run-document-intake-fill",
            "true",
            "--document-intake-output",
            str(filled_output),
            "--run-document-intake-validate",
            "true",
            "--run-document-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["document_intake_fill_report"]["valid_document_count"] == 1
    assert report["document_intake_validation_report"]["valid_document_count"] == 1
    filled = json.loads(filled_output.read_text(encoding="utf-8"))
    assert filled["documents"][0]["document_url"].endswith("annual-ifrs-financial-statements-2025.pdf")
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["ready_for_value_extraction"] is True
    assert gate["ready_for_import"] is False
    assert report["extraction_ready_count"] == 1
    assert report["import_ready_count"] == 0
    operator_row = report["availability_operator_rows"][0]
    assert operator_row["gate_status"] == "passed"
    assert operator_row["ready_for_value_extraction"] is True
    readiness = _readiness_for(report)
    assert readiness["reporting_readiness_status"] == "ready_for_extraction_preview"
    assert readiness["reporting_readiness_grade"] == "ready"
    assert readiness["extraction_allowed"] is True
    assert readiness["import_allowed"] is False
    assert readiness["scoring_allowed"] is False
    assert readiness["paper_trading_allowed"] is False
    assert readiness["next_required_action"] == "proceed_to_controlled_extraction_preview"
    resolution = _resolution_for(report)
    assert resolution["resolution_action_type"] == "no_operator_resolution_required"
    assert resolution["resolution_status"] == "resolved_or_not_required"
    assert resolution["resolution_priority"] == "low"
    assert resolution["can_unblock_extraction_if_completed"] is False
    assert resolution["operator_input_required"] is False
    assert resolution["extraction_allowed"] is True
    assert resolution["import_allowed"] is False
    assert resolution["scoring_allowed"] is False
    assert resolution["paper_trading_allowed"] is False


def test_exact_document_availability_policy_before_primary_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-03-01",
    )

    availability = _availability_for(report)
    policy = availability["reporting_window_policy"]
    assert availability["availability_status"] == "target_period_likely_not_yet_published_before_primary_deadline"
    assert availability["can_use_as_target_period_evidence"] is False
    assert policy["primary_expected_deadline_date"] == "2026-04-30"
    assert policy["before_primary_deadline"] is True
    assert policy["after_primary_deadline"] is False
    assert policy["within_grace_window"] is True
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "wait_until_primary_deadline"
    assert action["queue_priority"] == "low"
    assert action["queue_status"] == "waiting"
    assert action["manual_review_required"] is False


def test_exact_document_availability_policy_after_primary_inside_grace_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-05-25",
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "target_period_not_found_after_primary_deadline_within_grace_window"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["historical_fallback_allowed"] is True
    assert availability["historical_fallback_scope"] == "diagnostic_only"
    assert availability["latest_available_period"] == "2024"
    assert availability["reporting_window_policy"]["primary_expected_deadline_date"] == "2026-04-30"
    assert availability["reporting_window_policy"]["expected_availability_date"] == "2026-06-29"
    assert availability["reporting_window_policy"]["after_primary_deadline"] is True
    assert availability["reporting_window_policy"]["within_grace_window"] is True
    assert availability["reporting_window_policy"]["after_conservative_grace_window"] is False
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "review_sources_or_wait_grace"
    assert action["queue_priority"] == "medium"
    assert action["queue_status"] == "open"
    assert action["manual_review_required"] is True
    assert action["is_blocking_next_stage"] is True
    assert action["blocked_stage"] == "value_extraction"
    assert action["can_unblock_extraction"] is True
    assert action["expected_availability_date"] == "2026-06-29"


def test_exact_document_availability_policy_historical_after_grace_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-07-15",
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "only_historical_annual_ifrs_available"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["historical_fallback_allowed"] is True
    assert availability["historical_fallback_scope"] == "diagnostic_only"
    assert availability["latest_available_document_url"].endswith("annual-ifrs-financial-statements-2024.pdf")
    assert availability["reporting_window_policy"]["after_primary_deadline"] is True
    assert availability["reporting_window_policy"]["within_grace_window"] is False
    assert availability["reporting_window_policy"]["after_conservative_grace_window"] is True
    action = _queue_action_for(report)
    assert action["historical_fallback_scope"] == "diagnostic_only"
    assert action["target_evidence_available"] is False
    assert action["queue_action_type"] == "continue_target_period_search"
    assert "extract" not in action["operator_instruction"].lower()
    assert "import" not in action["operator_instruction"].lower()


def test_exact_document_availability_policy_only_interim_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="""
            <a href="/reports/IFRS_1H2025.pdf">IFRS 1H2025</a>
            <a href="/reports/quarterly-ifrs-2025.pdf">Quarterly IFRS financial statements 2025</a>
        """,
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "only_interim_or_quarterly_available"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["interim_or_quarterly_document_count"] >= 2
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "search_annual_report"
    assert action["queue_priority"] == "high"
    assert action["manual_review_required"] is True
    assert "must not be used as annual evidence" in action["operator_instruction"]


def test_exact_document_availability_policy_only_wrong_standard_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ras-financial-statements-2025.pdf">Annual RAS financial statements 2025</a>',
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "only_wrong_standard_available"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["wrong_standard_document_count"] >= 1
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "search_ifrs_report"
    assert action["queue_priority"] == "high"
    assert "not accepted when IFRS is required" in action["operator_instruction"]


def test_exact_document_availability_policy_operator_review_required_for_ambiguous_target_doc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/ifrs-financial-statements-2025.pdf">IFRS financial statements 2025</a>',
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "operator_exact_document_review_required"
    assert availability["operator_action"] == "review_exact_document_candidate"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["operator_review_required_count"] >= 1
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "review_exact_document_candidate"
    assert action["queue_priority"] == "high"
    assert action["manual_review_required"] is True


def test_exact_document_availability_policy_placeholder_not_found_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="",
        current_date="2026-05-25",
    )

    availability = _availability_for(report)
    placeholder = next(item for item in report["documents"] if item.get("document_status") == "not_found")
    assert placeholder["filter_status"] == "placeholder_not_found"
    assert placeholder["availability_status"] == "placeholder_not_found"
    assert placeholder["can_use_as_target_period_evidence"] is False
    assert availability["availability_status"] == "placeholder_not_found"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["reporting_window_policy"]["after_primary_deadline"] is True
    assert availability["reporting_window_policy"]["within_grace_window"] is True
    assert "after_primary_deadline" in availability["availability_reason_codes"]
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "fill_exact_document_url"
    assert action["queue_priority"] == "high"
    assert action["queue_status"] == "open"
    assert action["manual_review_required"] is True
    assert action["is_blocking_next_stage"] is True
    assert action["blocked_stage"] == "value_extraction"
    assert action["can_unblock_extraction"] is True
    assert "exact official annual IFRS report page or PDF URL" in action["operator_instruction"]


def test_exact_document_availability_policy_no_usable_official_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="https://wikipedia.org/wiki/Fictional">Annual report 2025</a>',
    )

    availability = _availability_for(report)
    assert availability["availability_status"] == "target_period_not_found_after_conservative_grace_window"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["historical_fallback_allowed"] is False
    assert availability["historical_fallback_scope"] == "none"
    assert availability["reporting_window_policy"]["after_conservative_grace_window"] is True
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "escalate_missing_target_report"
    assert action["queue_priority"] == "high"
    assert action["manual_review_required"] is True


def test_exact_document_historical_fallback_registry_none_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="",
        current_date="2026-05-25",
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "no_historical_fallback_available"
    assert registry["historical_fallback_scope"] == "none"
    assert registry["historical_fallback_allowed"] is False
    assert registry["can_use_as_target_period_evidence"] is False
    assert registry["can_use_for_value_extraction"] is False
    assert registry["can_use_for_import"] is False
    assert registry["can_use_for_scoring"] is False
    assert registry["can_use_for_paper_trading"] is False
    assert "no_historical_annual_ifrs_available" in registry["historical_fallback_reason_codes"]


def test_exact_document_historical_fallback_registry_latest_annual_ifrs_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/2019_12_Mostotrest_IFRS_Accounts.pdf">Annual IFRS accounts 2019</a>',
        current_date="2026-05-25",
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "latest_historical_annual_ifrs_available"
    assert registry["latest_available_period"] == "2019"
    assert registry["latest_available_report_type"] == "annual"
    assert registry["latest_available_standard"] == "IFRS"
    assert registry["latest_available_document_url"].endswith("2019_12_Mostotrest_IFRS_Accounts.pdf")
    assert registry["historical_fallback_scope"] == "diagnostic_only"
    assert registry["can_use_as_target_period_evidence"] is False
    assert registry["can_use_for_value_extraction"] is False
    assert registry["can_use_for_import"] is False
    assert registry["can_use_for_scoring"] is False
    assert registry["can_use_for_paper_trading"] is False
    assert "historical_fallback_diagnostic_only" in registry["historical_fallback_reason_codes"]
    assert report["historical_fallback_registry_report_count"] == 1
    assert report["historical_fallback_registry_latest_report_count"] == 1
    assert report["historical_fallback_registry_diagnostic_only_count"] == 1
    assert report["historical_fallback_registry_target_evidence_count"] == 0
    assert report["historical_fallback_registry_extraction_ready_count"] == 0
    assert report["historical_fallback_registry_import_ready_count"] == 0


def test_exact_document_historical_fallback_registry_selects_latest_period(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="""
            <a href="/reports/annual-ifrs-financial-statements-2018.pdf">Annual IFRS financial statements 2018</a>
            <a href="/reports/annual-ifrs-financial-statements-2019.pdf">Annual IFRS financial statements 2019</a>
            <a href="/reports/annual-ifrs-financial-statements-2020.pdf">Annual IFRS financial statements 2020</a>
        """,
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "latest_historical_annual_ifrs_available"
    assert registry["latest_available_period"] == "2020"
    assert registry["historical_annual_ifrs_latest_period"] == 2020
    assert registry["historical_annual_ifrs_oldest_period"] == 2018
    assert registry["historical_annual_ifrs_periods"] == [2018, 2019, 2020]
    assert registry["can_use_as_target_period_evidence"] is False


def test_exact_document_historical_fallback_registry_only_interim_historical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/IFRS_1H2024.pdf">IFRS 1H2024</a>',
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "only_interim_or_quarterly_historical_available"
    assert registry["historical_fallback_scope"] == "none"
    assert registry["can_use_as_target_period_evidence"] is False
    assert registry["can_use_for_value_extraction"] is False


def test_exact_document_historical_fallback_registry_only_wrong_standard_historical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ras-financial-statements-2024.pdf">Annual RAS financial statements 2024</a>',
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "only_wrong_standard_historical_available"
    assert registry["can_use_as_target_period_evidence"] is False
    assert registry["can_use_for_import"] is False
    assert "historical_wrong_standard_available" in registry["historical_fallback_reason_codes"]


def test_exact_document_historical_fallback_registry_exact_target_no_fallback_needed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>',
        current_date="2026-05-25",
    )

    registry = _historical_fallback_for(report)
    assert registry["historical_fallback_status"] == "exact_target_period_available_no_fallback_needed"
    assert registry["historical_fallback_scope"] == "none"
    assert registry["can_use_as_target_period_evidence"] is True
    assert registry["can_use_for_value_extraction"] is False
    assert registry["ready_for_value_extraction"] is False
    assert report["historical_fallback_registry_target_evidence_count"] == 1


def test_exact_document_reporting_readiness_placeholder_not_found_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="",
        current_date="2026-05-25",
    )

    readiness = _readiness_for(report)
    assert readiness["reporting_readiness_status"] == "blocked_placeholder_not_found"
    assert readiness["reporting_readiness_grade"] == "operator_required"
    assert readiness["primary_blocker"] == "placeholder_not_found"
    assert "availability" in readiness["blocking_layers"]
    assert "quality_gate" in readiness["blocking_layers"]
    assert "operator_queue" in readiness["blocking_layers"]
    assert "placeholder_not_found" in readiness["reporting_readiness_reason_codes"]
    assert readiness["extraction_allowed"] is False
    assert readiness["import_allowed"] is False
    assert readiness["scoring_allowed"] is False
    assert readiness["paper_trading_allowed"] is False
    assert readiness["next_required_action"] == "fill_exact_official_document_url_or_improve_official_sources"


def test_exact_document_reporting_readiness_weak_source_coverage_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")],
    )
    _mock_candidate_fetch(monkeypatch, {})
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18",
            "--required-company-names",
            "RZD",
            "--exact-document-availability-current-date",
            "2026-05-25",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    readiness = _readiness_for(report, company_id=18)
    assert readiness["reporting_readiness_status"] in {
        "blocked_placeholder_not_found",
        "blocked_weak_source_coverage",
    }
    assert "weak_source_coverage" in readiness["reporting_readiness_reason_codes"]
    assert "source_coverage" in readiness["blocking_layers"]
    assert readiness["extraction_allowed"] is False


def test_exact_document_reporting_readiness_historical_fallback_only_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/2019_12_Mostotrest_IFRS_Accounts.pdf">Annual IFRS accounts 2019</a>',
        current_date="2026-05-25",
    )

    readiness = _readiness_for(report)
    assert readiness["reporting_readiness_status"] == "blocked_missing_target_evidence"
    assert "historical_fallback" in readiness["blocking_layers"]
    assert "historical_fallback_diagnostic_only" in readiness["reporting_readiness_reason_codes"]
    assert readiness["historical_fallback_scope"] == "diagnostic_only"
    assert readiness["can_use_for_value_extraction"] is False
    assert readiness["extraction_allowed"] is False
    assert readiness["import_allowed"] is False
    assert readiness["scoring_allowed"] is False
    assert readiness["paper_trading_allowed"] is False


def test_exact_document_reporting_readiness_target_evidence_gate_not_run_is_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>',
        current_date="2026-05-25",
    )

    readiness = _readiness_for(report)
    assert readiness["target_evidence_available"] is True
    assert readiness["reporting_readiness_status"] == "blocked_quality_gate_failed"
    assert readiness["gate_status"] == "quality_gate_not_run"
    assert readiness["extraction_allowed"] is False
    assert readiness["import_allowed"] is False


def test_exact_document_reporting_readiness_after_primary_deadline_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-05-25",
    )

    readiness = _readiness_for(report)
    assert readiness["deadline_status"] == "after_primary_deadline_within_grace_window"
    assert "deadline_after_primary_within_grace" in readiness["reporting_readiness_reason_codes"]
    assert readiness["extraction_allowed"] is False


def test_exact_document_operator_resolution_placeholder_fill_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html="",
        current_date="2026-05-25",
    )

    resolution = _resolution_for(report)
    assert resolution["resolution_action_type"] == "fill_exact_document_url"
    assert resolution["resolution_priority"] == "high"
    assert resolution["resolution_status"] == "open"
    assert resolution["requires_exact_document_url"] is True
    assert resolution["can_unblock_extraction_if_completed"] is True
    assert resolution["operator_fill_exact_document_url"] == ""
    assert resolution["operator_fill_report_period"] == "2025"
    assert resolution["operator_fill_report_type"] == "annual"
    assert resolution["operator_fill_accounting_standard"] == "IFRS"
    assert resolution["extraction_allowed"] is False
    assert resolution["import_allowed"] is False
    assert resolution["scoring_allowed"] is False
    assert resolution["paper_trading_allowed"] is False


def test_exact_document_operator_resolution_weak_source_review_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")],
    )
    _mock_candidate_fetch(monkeypatch, {})
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18",
            "--required-company-names",
            "RZD",
            "--exact-document-availability-current-date",
            "2026-05-25",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    resolution = _resolution_for(report, company_id=18)
    assert resolution["requires_official_seed_review"] is True
    assert "weak_source_coverage" in resolution["resolution_reason_codes"]
    assert "no_valid_reviewed_official_seed" in resolution["resolution_reason_codes"]


def test_exact_document_operator_resolution_strong_coverage_verify_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-05-25",
    )

    resolution = _resolution_for(report)
    assert resolution["resolution_action_type"] == "verify_target_report_publication"
    assert resolution["resolution_priority"] == "medium"
    assert resolution["resolution_status"] == "open"
    assert resolution["requires_publication_verification"] is True
    assert resolution["can_unblock_extraction_if_completed"] is True


def test_exact_document_operator_resolution_historical_fallback_is_template_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    historical_url = "/reports/2019_12_Mostotrest_IFRS_Accounts.pdf"
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html=f'<a href="{historical_url}">Annual IFRS accounts 2019</a>',
        current_date="2026-05-25",
    )

    resolution = _resolution_for(report)
    assert resolution["latest_historical_document_url"].endswith("2019_12_Mostotrest_IFRS_Accounts.pdf")
    assert resolution["operator_fill_exact_document_url"] == ""
    assert "historical_fallback_diagnostic_only" in resolution["resolution_reason_codes"]
    assert "not target evidence" in resolution["safety_note"]


def test_exact_document_operator_resolution_before_primary_deadline_waits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = _run_availability_discovery(
        tmp_path,
        monkeypatch,
        seed_html='<a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>',
        current_date="2026-03-01",
    )

    resolution = _resolution_for(report)
    assert resolution["resolution_action_type"] == "review_sources_or_wait_grace"
    assert resolution["resolution_priority"] == "low"
    assert resolution["resolution_status"] == "waiting"
    assert resolution["is_wait_action"] is True
    assert resolution["operator_input_required"] is False


def test_operator_resolution_validation_empty_template_is_incomplete(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [_operator_resolution_validation_input_row()],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "incomplete_operator_input"
    assert "operator_decision_required" in row["validation_reason_codes"]
    assert "exact_document_url_required" in row["validation_reason_codes"]
    assert row["can_use_for_future_intake_review"] is False
    assert row["would_update_document_intake"] is False


def test_operator_resolution_validation_accepts_valid_exact_target_url(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url="https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2025.pdf",
                operator_fill_document_title="Mostotrest annual IFRS financial statements 2025",
                operator_fill_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "valid_for_future_controlled_intake_review"
    assert row["can_use_for_future_intake_review"] is True
    assert row["would_update_document_intake"] is False
    assert row["would_extract_values"] is False
    assert row["would_import_report"] is False


def test_operator_resolution_validation_rejects_historical_fallback_url(tmp_path: Path) -> None:
    historical_url = "https://mostotrest.ru/upload/iblock/9ac/9yburv7bvdemeg8v66pphfy9k5wphimk/2019_12_Mostotrest_IFRS_Accounts.pdf"
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url=historical_url,
                operator_fill_document_title="Mostotrest IFRS accounts 2019",
                latest_historical_document_url=historical_url,
                latest_historical_period="2019",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "invalid_operator_input"
    assert row["historical_fallback_url_used_as_exact_document"] is True
    assert "historical_fallback_url_used_as_exact_document" in row["validation_reason_codes"]
    assert row["can_use_for_future_intake_review"] is False


def test_operator_resolution_validation_rejects_wrong_period(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url="https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2024.pdf",
                operator_fill_document_title="Mostotrest annual IFRS financial statements 2024",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "invalid_operator_input"
    assert {"wrong_period", "not_target_reporting_period"} & set(row["validation_reason_codes"])


def test_operator_resolution_validation_rejects_interim_report_for_annual(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url="https://mostotrest.ru/reports/mostotrest-q1-ifrs-financial-statements-2025.pdf",
                operator_fill_document_title="Mostotrest Q1 IFRS financial statements 2025",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "invalid_operator_input"
    assert "interim_or_quarterly_not_allowed_for_annual" in row["validation_reason_codes"]


def test_operator_resolution_validation_rejects_wrong_standard(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url="https://mostotrest.ru/reports/mostotrest-annual-ras-financial-statements-2025.pdf",
                operator_fill_document_title="Mostotrest annual RAS financial statements 2025",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "invalid_operator_input"
    assert "wrong_accounting_standard" in row["validation_reason_codes"]


def test_operator_resolution_validation_rejects_landing_page(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                operator_fill_decision="exact_document_found",
                operator_fill_exact_document_url="https://mostotrest.ru/ru/invest/financial-results/",
                operator_fill_document_title="Mostotrest financial results IFRS 2025",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "invalid_operator_input"
    assert {"landing_page_not_allowed", "not_exact_report_document"} & set(row["validation_reason_codes"])


def test_operator_resolution_validation_wait_decision(tmp_path: Path) -> None:
    report = _run_operator_resolution_validation(
        tmp_path,
        [
            _operator_resolution_validation_input_row(
                resolution_action_type="review_sources_or_wait_grace",
                operator_fill_decision="wait_until_grace_date",
            )
        ],
    )

    row = report["validation_rows"][0]
    assert row["validation_status"] == "waiting"
    assert row["can_use_for_future_intake_review"] is False


def test_operator_resolution_validation_csv_and_markdown_outputs(tmp_path: Path) -> None:
    validation_json = tmp_path / "operator_resolution_validation.json"
    validation_csv = tmp_path / "operator_resolution_validation.csv"
    validation_md = tmp_path / "operator_resolution_validation.md"
    report = _run_operator_resolution_validation(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        extra_args=[
            "--operator-resolution-validation-output",
            str(validation_json),
            "--operator-resolution-validation-csv-output",
            str(validation_csv),
            "--operator-resolution-validation-markdown-output",
            str(validation_md),
        ],
    )

    assert report["operator_resolution_validation_row_count"] == 1
    assert validation_json.is_file()
    assert validation_csv.is_file()
    assert validation_md.is_file()
    exported = json.loads(validation_json.read_text(encoding="utf-8"))
    assert exported["mode"] == "operator-resolution-validation"
    csv_rows = list(csv.DictReader(validation_csv.open(encoding="utf-8")))
    assert len(csv_rows) == 1
    assert {
        "validation_status",
        "validation_reason_codes",
        "can_use_for_future_intake_review",
        "would_update_document_intake",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    }.issubset(set(csv_rows[0]))
    markdown = validation_md.read_text(encoding="utf-8")
    assert "Operator Resolution Validation" in markdown
    assert "Validation Status Counts" in markdown
    assert "Validation Error Counts" in markdown
    assert "This validation does not update exact document intake" in markdown


def test_operator_resolution_apply_preview_incomplete_validation_no_candidate(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row(validation_status="incomplete_operator_input")],
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "not_eligible_incomplete_validation"
    assert row["patch_action"] == "preview_noop"
    assert row["future_apply_allowed"] is False
    assert row["would_apply_to_document_intake"] is False


def test_operator_resolution_apply_preview_valid_row_becomes_eligible(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row()],
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "eligible_for_future_controlled_apply"
    assert row["future_apply_allowed"] is True
    assert row["proposed_document_url"].endswith("mostotrest-annual-ifrs-financial-statements-2025.pdf")
    assert row["would_apply_to_document_intake"] is False
    assert row["would_extract_values"] is False
    assert row["would_import_report"] is False


def test_operator_resolution_apply_preview_historical_fallback_rejected_stays_blocked(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [
            _operator_resolution_apply_validation_row(
                validation_status="invalid_operator_input",
                can_use_for_future_intake_review="false",
                validation_reason_codes="historical_fallback_url_used_as_exact_document",
                validation_errors="historical_fallback_url_used_as_exact_document",
                historical_fallback_url_used_as_exact_document="true",
            )
        ],
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "not_eligible_invalid_validation"
    assert row["future_apply_allowed"] is False
    assert "historical_fallback_url_used_as_exact_document" in row["patch_reason_codes"]


def test_operator_resolution_apply_preview_waiting_row_noops(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [
            _operator_resolution_apply_validation_row(
                validation_status="waiting",
                can_use_for_future_intake_review="false",
                operator_fill_decision="wait_until_grace_date",
            )
        ],
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "not_eligible_waiting"
    assert row["patch_action"] == "preview_noop"


def test_operator_resolution_apply_preview_replaces_not_found_placeholder(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    placeholder = _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")
    placeholder["document_status"] = "not_found"
    _write_document_intake(intake, [placeholder])

    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row()],
        document_intake_input=intake,
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "eligible_for_future_controlled_apply"
    assert row["patch_action"] == "preview_replace_not_found_placeholder"
    assert row["would_replace_placeholder"] is True
    assert row["would_update_existing_intake_row"] is True
    assert row["would_apply_to_document_intake"] is False


def test_operator_resolution_apply_preview_creates_when_no_intake_match(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")])

    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row()],
        document_intake_input=intake,
    )

    row = report["patch_rows"][0]
    assert row["patch_action"] == "preview_create_intake_row"
    assert row["would_create_intake_row"] is True
    assert row["would_apply_to_document_intake"] is False


def test_operator_resolution_apply_preview_strict_mismatch_blocks(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row(document_period_status="wrong_period")],
    )

    row = report["patch_rows"][0]
    assert row["patch_status"] == "blocked_strict_document_mismatch"
    assert row["future_apply_allowed"] is False


def test_operator_resolution_apply_preview_csv_and_markdown_outputs(tmp_path: Path) -> None:
    preview_json = tmp_path / "operator_resolution_apply_preview.json"
    preview_csv = tmp_path / "operator_resolution_apply_preview.csv"
    preview_md = tmp_path / "operator_resolution_apply_preview.md"
    report = _run_operator_resolution_apply_preview(
        tmp_path,
        [_operator_resolution_apply_validation_row(validation_status="incomplete_operator_input")],
        extra_args=[
            "--operator-resolution-apply-preview-output",
            str(preview_json),
            "--operator-resolution-apply-preview-csv-output",
            str(preview_csv),
            "--operator-resolution-apply-preview-markdown-output",
            str(preview_md),
        ],
    )

    assert report["operator_resolution_apply_preview_row_count"] == 1
    assert preview_json.is_file()
    assert preview_csv.is_file()
    assert preview_md.is_file()
    exported = json.loads(preview_json.read_text(encoding="utf-8"))
    assert exported["mode"] == "operator-resolution-apply-preview"
    csv_rows = list(csv.DictReader(preview_csv.open(encoding="utf-8")))
    assert len(csv_rows) == 1
    assert {
        "patch_status",
        "patch_action",
        "future_apply_allowed",
        "would_apply_to_document_intake",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    }.issubset(set(csv_rows[0]))
    markdown = preview_md.read_text(encoding="utf-8")
    assert "Operator Resolution Apply Preview" in markdown
    assert "Apply Preview Status Counts" in markdown
    assert "This preview does not update exact document intake" in markdown


def test_operator_resolution_apply_draft_skips_incomplete_preview_row(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row(
            patch_status="not_eligible_incomplete_validation",
            patch_action="preview_noop",
            future_apply_allowed=False,
            proposed_document_url="",
        )],
    )

    row = report["apply_draft_rows"][0]
    assert row["apply_draft_status"] == "skipped_not_eligible_incomplete_validation"
    assert row["apply_draft_action"] == "skip"
    assert row["would_change_draft_file"] is False
    assert row["would_update_original_intake"] is False


def test_operator_resolution_apply_draft_replaces_placeholder_in_draft_only(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    draft = tmp_path / "exact_document_intake_draft.json"
    placeholder = _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")
    placeholder["document_status"] = "not_found"
    _write_document_intake(intake, [placeholder])
    original = intake.read_text(encoding="utf-8")

    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row()],
        document_intake_input=intake,
        extra_args=["--document-intake-draft-output", str(draft)],
    )

    row = report["apply_draft_rows"][0]
    assert row["apply_draft_status"] == "draft_applied_replace_not_found_placeholder"
    assert row["would_change_draft_file"] is True
    assert row["would_update_original_intake"] is False
    assert intake.read_text(encoding="utf-8") == original
    draft_documents = json.loads(draft.read_text(encoding="utf-8"))["documents"]
    assert draft_documents[0]["document_url"].endswith("mostotrest-annual-ifrs-financial-statements-2025.pdf")
    assert draft_documents[0]["filter_status"] == "kept"
    assert draft_documents[0]["fallback_status"] == "not_fallback"


def test_operator_resolution_apply_draft_creates_row_in_draft_only(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    draft = tmp_path / "exact_document_intake_draft.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")])
    original = intake.read_text(encoding="utf-8")

    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row(patch_action="preview_create_intake_row")],
        document_intake_input=intake,
        extra_args=["--document-intake-draft-output", str(draft)],
    )

    row = report["apply_draft_rows"][0]
    assert row["apply_draft_status"] == "draft_applied_create_row"
    assert row["would_change_draft_file"] is True
    assert row["would_update_original_intake"] is False
    assert intake.read_text(encoding="utf-8") == original
    draft_documents = json.loads(draft.read_text(encoding="utf-8"))["documents"]
    assert len(draft_documents) == 2
    assert draft_documents[1]["draft_source"] == "operator_resolution_apply_draft"


def test_operator_resolution_apply_draft_rejects_output_equal_to_input(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(67, "Mostotrest", "")])
    original = intake.read_text(encoding="utf-8")

    report, exit_code = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row()],
        document_intake_input=intake,
        expected_exit_code=1,
        extra_args=["--document-intake-draft-output", str(intake)],
        return_exit_code=True,
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(error.get("message") == "draft_output_must_not_equal_input" for error in report["errors"])
    assert intake.read_text(encoding="utf-8") == original


def test_operator_resolution_apply_draft_blocks_unsafe_mutation_flags(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row(would_extract_values=True)],
    )

    row = report["apply_draft_rows"][0]
    assert row["apply_draft_status"] == "skipped_unsafe_mutation_flags"
    assert row["would_change_draft_file"] is False


def test_operator_resolution_apply_draft_blocks_strict_mismatch(tmp_path: Path) -> None:
    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row(document_period_status="wrong_period")],
    )

    row = report["apply_draft_rows"][0]
    assert row["apply_draft_status"] == "skipped_strict_document_mismatch"
    assert row["would_change_draft_file"] is False


def test_operator_resolution_apply_draft_outputs_reports_and_draft_files(tmp_path: Path) -> None:
    report_json = tmp_path / "operator_resolution_apply_draft.json"
    report_csv = tmp_path / "operator_resolution_apply_draft.csv"
    report_md = tmp_path / "operator_resolution_apply_draft.md"
    draft_json = tmp_path / "exact_document_intake_draft.json"
    draft_csv = tmp_path / "exact_document_intake_draft.csv"
    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [_operator_resolution_apply_draft_patch_row(
            patch_status="not_eligible_incomplete_validation",
            patch_action="preview_noop",
            future_apply_allowed=False,
            proposed_document_url="",
        )],
        extra_args=[
            "--document-intake-draft-output",
            str(draft_json),
            "--document-intake-draft-csv-output",
            str(draft_csv),
            "--operator-resolution-apply-draft-output",
            str(report_json),
            "--operator-resolution-apply-draft-csv-output",
            str(report_csv),
            "--operator-resolution-apply-draft-markdown-output",
            str(report_md),
        ],
    )

    assert report["operator_resolution_apply_draft_applied_count"] == 0
    assert report_json.is_file()
    assert report_csv.is_file()
    assert report_md.is_file()
    assert draft_json.is_file()
    assert draft_csv.is_file()
    csv_rows = list(csv.DictReader(report_csv.open(encoding="utf-8")))
    assert {
        "apply_draft_status",
        "apply_draft_action",
        "would_change_draft_file",
        "would_update_original_intake",
        "would_update_database",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    }.issubset(set(csv_rows[0]))
    markdown = report_md.read_text(encoding="utf-8")
    assert "Operator Resolution Apply Draft" in markdown
    assert "This task does not overwrite the original exact document intake" in markdown


def test_operator_resolution_apply_draft_unfilled_preview_preserves_intake(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    draft = tmp_path / "exact_document_intake_draft.json"
    placeholders = [
        _empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/"),
        _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
    ]
    _write_document_intake(intake, placeholders)

    report = _run_operator_resolution_apply_draft(
        tmp_path,
        [
            _operator_resolution_apply_draft_patch_row(
                company_id="18",
                company_name="RZD",
                canonical_company_id="18",
                canonical_company_name="RZD",
                patch_status="not_eligible_incomplete_validation",
                patch_action="preview_noop",
                future_apply_allowed=False,
                proposed_document_url="",
            ),
            _operator_resolution_apply_draft_patch_row(
                patch_status="not_eligible_incomplete_validation",
                patch_action="preview_noop",
                future_apply_allowed=False,
                proposed_document_url="",
            ),
        ],
        document_intake_input=intake,
        extra_args=["--document-intake-draft-output", str(draft)],
    )

    assert report["operator_resolution_apply_draft_applied_count"] == 0
    assert report["operator_resolution_apply_draft_skipped_count"] == 2
    assert report["operator_resolution_apply_draft_output_row_count"] == 2
    draft_documents = json.loads(draft.read_text(encoding="utf-8"))["documents"]
    assert all(not document.get("document_url") for document in draft_documents)


def test_document_intake_draft_gate_preview_blocks_unfilled_placeholders(tmp_path: Path) -> None:
    report = _run_document_intake_draft_gate_preview(
        tmp_path,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
        ],
    )

    assert report["document_intake_draft_gate_preview_row_count"] == 2
    assert report["document_intake_draft_gate_preview_ready_count"] == 0
    assert report["document_intake_draft_gate_preview_blocked_count"] == 2
    assert report["document_intake_draft_gate_preview_placeholder_count"] == 2
    assert report["document_intake_draft_gate_preview_gate_passed"] is False
    assert report["document_intake_draft_gate_preview_ready_for_value_extraction"] is False
    assert report["document_intake_draft_gate_preview_ready_for_import"] is False
    assert report["document_intake_draft_gate_preview_status_counts"] == {"draft_placeholder_not_ready": 2}
    assert all(row["would_extract_values"] is False for row in report["draft_gate_summary_rows"])
    assert all(row["would_import_report"] is False for row in report["draft_gate_summary_rows"])


def test_document_intake_draft_gate_preview_requires_draft_input() -> None:
    args = assistant.parse_args(["--mode", "document-intake-draft-gate-preview"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(error.get("message") == "document_intake_draft_input_required" for error in report["errors"])


def test_document_intake_draft_gate_preview_valid_exact_document_passes_full_gate_with_source_context(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    report = _run_document_intake_draft_gate_preview(
        tmp_path,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
        source_intake_input=discovered,
        extra_args=["--required-company-ids", "18"],
    )

    row = report["draft_gate_summary_rows"][0]
    assert report["status"] == "passed"
    assert report["document_intake_draft_gate_preview_gate_passed"] is True
    assert report["document_intake_draft_gate_preview_ready_for_value_extraction"] is True
    assert report["document_intake_draft_gate_preview_ready_for_import"] is False
    assert row["draft_row_status"] == "draft_ready_for_future_extraction_preview"
    assert row["has_exact_target_document"] is True
    assert row["ready_for_value_extraction"] is True
    assert row["ready_for_import"] is False


def test_document_intake_draft_gate_preview_valid_document_without_source_context_stays_blocked(
    tmp_path: Path,
) -> None:
    report = _run_document_intake_draft_gate_preview(
        tmp_path,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/annual-ifrs-2025.pdf",
                "Annual audited consolidated IFRS financial statements 2025",
            )
        ],
        extra_args=["--required-company-ids", "18"],
    )

    row = report["draft_gate_summary_rows"][0]
    assert report["status"] == "warning"
    assert row["draft_row_status"] == "draft_valid_but_gate_blocked"
    assert "quality_gate_source_context_missing" in row["blocked_reason_codes"]
    assert row["ready_for_value_extraction"] is False
    assert row["ready_for_import"] is False


def test_document_intake_draft_gate_preview_blocks_strict_document_mismatches(tmp_path: Path) -> None:
    cases = [
        (
            "wrong-period",
            _document_item(
                67,
                "https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2024.pdf",
                "Mostotrest annual IFRS financial statements 2024",
            ),
            "wrong_period",
        ),
        (
            "interim",
            _document_item(
                67,
                "https://mostotrest.ru/reports/mostotrest-q1-ifrs-financial-statements-2025.pdf",
                "Mostotrest Q1 interim IFRS financial statements 2025",
            ),
            "interim_or_quarterly_not_allowed_for_annual",
        ),
        (
            "wrong-standard",
            _document_item(
                67,
                "https://mostotrest.ru/reports/mostotrest-annual-ras-financial-statements-2025.pdf",
                "Mostotrest annual RAS financial statements 2025",
            ),
            "wrong_standard",
        ),
        (
            "historical-fallback",
            {
                **_document_item(
                    67,
                    "https://mostotrest.ru/upload/2019_12_Mostotrest_IFRS_Accounts.pdf",
                    "Mostotrest annual IFRS financial statements 2019",
                ),
                "fallback_status": "diagnostic_only",
            },
            "historical_fallback_not_target_evidence",
        ),
    ]
    for name, document, blocker in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        report = _run_document_intake_draft_gate_preview(case_dir, [document])
        row = report["draft_gate_summary_rows"][0]
        assert row["draft_row_status"] == "draft_invalid_not_ready"
        assert row["has_exact_target_document"] is False
        assert blocker in row["blocked_reason_codes"]
        assert row["ready_for_value_extraction"] is False


def test_document_intake_draft_gate_preview_rejects_output_path_collision(tmp_path: Path) -> None:
    draft = tmp_path / "exact_document_intake_draft.json"
    _write_document_intake(draft, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")])
    original = draft.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-draft-gate-preview",
            "--document-intake-draft-input",
            str(draft),
            "--json-output",
            str(draft),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(error.get("message") == "draft_gate_output_must_not_equal_input" for error in report["errors"])
    assert draft.read_bytes() == original


def test_document_intake_draft_gate_preview_outputs_reports_without_modifying_draft(tmp_path: Path) -> None:
    draft = tmp_path / "exact_document_intake_draft.json"
    validation_json = tmp_path / "draft_validation.json"
    validation_md = tmp_path / "draft_validation.md"
    gate_json = tmp_path / "draft_gate.json"
    gate_md = tmp_path / "draft_gate.md"
    summary_json = tmp_path / "draft_gate_summary.json"
    summary_csv = tmp_path / "draft_gate_summary.csv"
    summary_md = tmp_path / "draft_gate_summary.md"
    _write_document_intake(draft, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")])
    original = draft.read_bytes()

    report = _run_document_intake_draft_gate_preview(
        tmp_path,
        draft_input=draft,
        extra_args=[
            "--document-intake-draft-validation-output",
            str(validation_json),
            "--document-intake-draft-validation-markdown-output",
            str(validation_md),
            "--document-intake-draft-gate-output",
            str(gate_json),
            "--document-intake-draft-gate-markdown-output",
            str(gate_md),
            "--document-intake-draft-gate-summary-output",
            str(summary_json),
            "--document-intake-draft-gate-summary-csv-output",
            str(summary_csv),
            "--document-intake-draft-gate-summary-markdown-output",
            str(summary_md),
        ],
    )

    assert draft.read_bytes() == original
    assert validation_json.is_file()
    assert validation_md.is_file()
    assert gate_json.is_file()
    assert gate_md.is_file()
    assert summary_json.is_file()
    assert summary_csv.is_file()
    assert summary_md.is_file()
    csv_rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    assert {
        "draft_row_status",
        "blocked_reason_codes",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    }.issubset(set(csv_rows[0]))
    markdown = summary_md.read_text(encoding="utf-8")
    assert "Document Intake Draft Gate Preview" in markdown
    assert "This task does not overwrite original intake" in markdown
    assert "This task does not modify the draft intake" in markdown
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_happy_path_synthetic_reaches_ready_preview(tmp_path: Path) -> None:
    report = _run_operator_resolution_happy_path_synthetic(tmp_path)

    assert report["status"] == "passed"
    assert report["synthetic_only"] is True
    assert report["validation_valid_count"] == 1
    assert report["apply_preview_eligible_count"] == 1
    assert report["apply_draft_applied_count"] == 1
    assert report["draft_gate_ready_count"] == 1
    assert report["draft_gate_passed"] is True
    assert report["ready_for_value_extraction"] is True
    assert report["ready_for_import"] is False
    assert report["original_intake_modified"] is False
    assert report["draft_intake_created"] is True

    validation_row = report["stage_reports"]["validation"]["validation_rows"][0]
    assert validation_row["validation_status"] == "valid_for_future_controlled_intake_review"
    assert validation_row["can_use_for_future_intake_review"] is True
    patch_row = report["stage_reports"]["apply_preview"]["patch_rows"][0]
    assert patch_row["patch_status"] == "eligible_for_future_controlled_apply"
    assert patch_row["patch_action"] == "preview_replace_not_found_placeholder"
    assert patch_row["future_apply_allowed"] is True
    apply_row = report["stage_reports"]["apply_draft"]["apply_draft_rows"][0]
    assert apply_row["apply_draft_status"] == "draft_applied_replace_not_found_placeholder"
    assert apply_row["would_change_draft_file"] is True
    assert apply_row["would_update_original_intake"] is False
    gate_row = report["stage_reports"]["draft_gate"]["draft_gate_summary_rows"][0]
    assert gate_row["draft_row_status"] == "draft_ready_for_future_extraction_preview"
    assert gate_row["ready_for_value_extraction"] is True
    assert gate_row["ready_for_import"] is False


def test_operator_resolution_happy_path_synthetic_writes_artifacts_and_preserves_base_intake(tmp_path: Path) -> None:
    report = _run_operator_resolution_happy_path_synthetic(tmp_path)
    artifacts = {key: Path(value) for key, value in report["artifacts"].items()}

    assert set(artifacts) == set(assistant.SYNTHETIC_HAPPY_PATH_ARTIFACT_NAMES)
    assert all(path.is_file() for path in artifacts.values())
    base = json.loads(artifacts["exact_document_intake_base_json"].read_text(encoding="utf-8"))
    draft = json.loads(artifacts["exact_document_intake_draft_json"].read_text(encoding="utf-8"))
    assert base["synthetic_only"] is True
    assert base["documents"][0]["document_url"] == ""
    assert draft["documents"][0]["document_url"] == (
        "https://reports.synthetic-bondradar.test/issuer-900001/annual-ifrs-2025.pdf"
    )
    summary = json.loads(artifacts["chain_summary_json"].read_text(encoding="utf-8"))
    assert summary["synthetic_only"] is True
    validation = json.loads(artifacts["operator_resolution_validation_json"].read_text(encoding="utf-8"))
    assert validation["synthetic_only"] is True
    draft_gate = json.loads(artifacts["document_intake_draft_gate_json"].read_text(encoding="utf-8"))
    assert draft_gate["synthetic_only"] is True
    markdown = artifacts["chain_summary_markdown"].read_text(encoding="utf-8")
    assert "Operator Resolution Happy-Path Synthetic Chain" in markdown
    assert "This is a synthetic positive-control fixture" in markdown
    assert "No document is fetched or parsed" in markdown
    stage_markdown = artifacts["operator_resolution_validation_markdown"].read_text(encoding="utf-8")
    assert "Synthetic-only positive-control fixture" in stage_markdown


def test_operator_resolution_happy_path_synthetic_does_not_allow_test_domain_in_normal_draft_gate(tmp_path: Path) -> None:
    document = _document_item(
        900001,
        "https://reports.synthetic-bondradar.test/issuer-900001/annual-ifrs-2025.pdf",
        "Synthetic BondRadar Issuer annual audited consolidated IFRS financial statements 2025",
    )
    document["company_name"] = "Synthetic BondRadar Issuer"
    document["canonical_company_name"] = "Synthetic BondRadar Issuer"
    document["document_status"] = "valid_official_document"
    document["filter_status"] = "kept"
    document["fallback_status"] = "not_fallback"

    report = _run_document_intake_draft_gate_preview(tmp_path, [document])

    row = report["draft_gate_summary_rows"][0]
    assert row["draft_row_status"] == "draft_invalid_not_ready"
    assert "invalid_document_intake" in row["blocked_reason_codes"]
    assert any("source URL domain is not in the official allowlist" in error for error in row["validation_errors"])


def test_operator_resolution_happy_path_synthetic_never_calls_network_or_download_helpers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("synthetic happy-path mode must not call network or download helpers")

    monkeypatch.setattr(assistant, "_probe_url", fail)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", fail)
    monkeypatch.setattr(assistant, "_download_valid_document", fail)
    monkeypatch.setattr(assistant, "_download_source_document", fail)

    report = _run_operator_resolution_happy_path_synthetic(tmp_path)

    assert report["status"] == "passed"
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_happy_path_synthetic_fixtures_only_skips_chain(tmp_path: Path) -> None:
    report = _run_operator_resolution_happy_path_synthetic(
        tmp_path,
        extra_args=["--operator-resolution-happy-path-run-chain", "false"],
    )
    artifacts = {key: Path(value) for key, value in report["artifacts"].items()}

    assert report["status"] == "fixtures_generated"
    assert report["run_chain"] is False
    assert report["stage_reports"] == {}
    assert artifacts["operator_resolution_pack_json"].is_file()
    assert artifacts["operator_resolution_filled_csv"].is_file()
    assert artifacts["official_source_intake_json"].is_file()
    assert artifacts["exact_document_intake_base_json"].is_file()
    assert artifacts["chain_summary_json"].is_file()
    assert artifacts["chain_summary_markdown"].is_file()
    assert not artifacts["operator_resolution_validation_json"].exists()
    assert not artifacts["exact_document_intake_draft_json"].exists()


def test_operator_resolution_happy_path_synthetic_rerun_preserves_unrelated_files(tmp_path: Path) -> None:
    sentinel = tmp_path / "keep-me.txt"
    sentinel.write_text("unrelated", encoding="utf-8")
    first = _run_operator_resolution_happy_path_synthetic(tmp_path)
    summary_path = Path(first["artifacts"]["chain_summary_json"])
    summary_path.write_text("stale", encoding="utf-8")

    second = _run_operator_resolution_happy_path_synthetic(tmp_path)

    assert second["status"] == "passed"
    assert sentinel.read_text(encoding="utf-8") == "unrelated"
    assert json.loads(summary_path.read_text(encoding="utf-8"))["status"] == "passed"


def test_operator_resolution_happy_path_synthetic_requires_output_directory() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-happy-path-synthetic"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error.get("message") == "operator_resolution_happy_path_output_dir_required"
        for error in report["errors"]
    )


def test_operator_resolution_chain_preview_unfilled_pack_stays_blocked(tmp_path: Path) -> None:
    rows = [
        _operator_resolution_validation_input_row(
            resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
            company_id="18",
            company_name="RZD",
        ),
        _operator_resolution_validation_input_row(),
    ]
    placeholders = [
        _operator_resolution_chain_placeholder(18, "RZD", "https://rzd.ru/reports/"),
        _operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
    ]

    report = _run_operator_resolution_chain_preview(tmp_path, rows, placeholders)

    assert report["status"] == "warning"
    assert report["validation_row_count"] == 2
    assert report["validation_valid_count"] == 0
    assert report["validation_incomplete_count"] == 2
    assert report["apply_preview_eligible_count"] == 0
    assert report["apply_draft_applied_count"] == 0
    assert report["draft_gate_ready_count"] == 0
    assert report["draft_gate_passed"] is False
    assert report["ready_for_value_extraction"] is False
    assert report["ready_for_import"] is False
    assert report["draft_intake_created"] is True


def test_operator_resolution_chain_preview_valid_real_row_passes_with_source_context(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    source_intake = tmp_path / "official_source_intake.json"
    _write_document_intake(
        intake,
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _write_source_intake(
        source_intake,
        [
            _source_issuer(
                67,
                "Mostotrest",
                "official_issuer_report",
                "https://mostotrest.ru/ru/invest/financial-results/",
                "Mostotrest official reporting page",
            )
        ],
    )
    original = intake.read_bytes()

    report = _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_chain_valid_input_row()],
        document_intake_input=intake,
        source_intake_input=source_intake,
        extra_args=["--required-company-ids", "67"],
    )

    assert report["status"] == "passed"
    assert report["synthetic_only"] is False
    assert report["validation_valid_count"] == 1
    assert report["apply_preview_eligible_count"] == 1
    assert report["apply_draft_applied_count"] == 1
    assert report["draft_gate_ready_count"] == 1
    assert report["draft_gate_passed"] is True
    assert report["ready_for_value_extraction"] is True
    assert report["ready_for_import"] is False
    assert report["original_intake_modified"] is False
    assert intake.read_bytes() == original
    draft = Path(report["artifacts"]["exact_document_intake_draft_json"])
    assert "mostotrest-annual-ifrs-financial-statements-2025.pdf" in draft.read_text(encoding="utf-8")
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_chain_preview_rejects_synthetic_test_domain(tmp_path: Path) -> None:
    row = _operator_resolution_chain_valid_input_row(
        operator_fill_exact_document_url="https://reports.synthetic-bondradar.test/issuer-900001/annual-ifrs-2025.pdf",
        operator_fill_source_page_url="https://reports.synthetic-bondradar.test/issuer-900001/reports/",
    )

    report = _run_operator_resolution_chain_preview(
        tmp_path,
        [row],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )

    assert report["status"] == "warning"
    assert report["validation_invalid_count"] == 1
    assert report["apply_preview_eligible_count"] == 0
    assert report["ready_for_value_extraction"] is False


def test_operator_resolution_chain_preview_rejects_fixed_artifact_collision(tmp_path: Path) -> None:
    collision_input = tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_PREVIEW_ARTIFACT_NAMES["chain_summary_json"]
    _write_operator_resolution_validation_csv(collision_input, [_operator_resolution_validation_input_row()])
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_operator_resolution_chain_placeholder(67, "Mostotrest", "")])
    original = collision_input.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-preview",
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--operator-resolution-input",
            str(collision_input),
            "--document-intake-input",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(error.get("message") == "operator_resolution_chain_output_must_not_equal_input" for error in report["errors"])
    assert collision_input.read_bytes() == original


def test_operator_resolution_chain_preview_rejects_generic_output_collision(tmp_path: Path) -> None:
    resolution_input = tmp_path / "operator_resolution_input.csv"
    _write_operator_resolution_validation_csv(resolution_input, [_operator_resolution_validation_input_row()])
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_operator_resolution_chain_placeholder(67, "Mostotrest", "")])
    original = resolution_input.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-preview",
            "--operator-resolution-chain-output-dir",
            str(tmp_path / "out"),
            "--operator-resolution-input",
            str(resolution_input),
            "--document-intake-input",
            str(intake),
            "--json-output",
            str(resolution_input),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(error.get("message") == "operator_resolution_chain_output_must_not_equal_input" for error in report["errors"])
    assert assistant._generic_report_output_is_safe(args, args.json_output) is False
    assert resolution_input.read_bytes() == original


def test_operator_resolution_chain_preview_valid_row_without_source_context_stays_blocked(tmp_path: Path) -> None:
    report = _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_chain_valid_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
        extra_args=["--required-company-ids", "67"],
    )

    assert report["status"] == "warning"
    assert report["validation_valid_count"] == 1
    assert report["apply_draft_applied_count"] == 1
    assert report["draft_gate_ready_count"] == 0
    assert report["ready_for_value_extraction"] is False
    assert any(warning.get("message") == "quality_gate_source_context_missing" for warning in report["warnings"])


def test_operator_resolution_chain_preview_missing_source_pack_warns_and_continues(tmp_path: Path) -> None:
    report = _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
        include_source_pack=False,
    )

    assert report["validation_incomplete_count"] == 1
    assert report["draft_intake_created"] is True
    assert any(warning.get("message") == "source_pack_missing" for warning in report["warnings"])


def test_operator_resolution_chain_preview_fatal_validation_stops_downstream(tmp_path: Path) -> None:
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_operator_resolution_chain_placeholder(67, "Mostotrest", "")])
    output_dir = tmp_path / "out"
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-preview",
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--operator-resolution-input",
            str(tmp_path / "missing.csv"),
            "--document-intake-input",
            str(intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["failed_stage"] == "operator-resolution-validate"
    assert report["completed_stages"] == ["operator-resolution-validate"]
    assert not Path(report["artifacts"]["operator_resolution_apply_preview_json"]).exists()
    assert Path(report["artifacts"]["chain_summary_json"]).is_file()


def test_operator_resolution_chain_preview_writes_all_artifacts_and_preserves_unrelated_file(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    sentinel = output_dir / "keep.txt"
    output_dir.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    source_intake = tmp_path / "official_source_intake.json"
    _write_source_intake(
        source_intake,
        [
            _source_issuer(
                67,
                "Mostotrest",
                "official_issuer_report",
                "https://mostotrest.ru/ru/invest/financial-results/",
                "Mostotrest official reporting page",
            )
        ],
    )

    first = _run_operator_resolution_chain_preview(
        output_dir,
        [_operator_resolution_chain_valid_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
        source_intake_input=source_intake,
        extra_args=["--required-company-ids", "67"],
    )
    second = _run_operator_resolution_chain_preview(
        output_dir,
        [_operator_resolution_chain_valid_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
        source_intake_input=source_intake,
        extra_args=["--required-company-ids", "67"],
    )

    assert first["status"] == "passed"
    assert second["status"] == "passed"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert set(second["artifacts"]) == set(assistant.OPERATOR_RESOLUTION_CHAIN_PREVIEW_ARTIFACT_NAMES)
    assert all(Path(path).is_file() for path in second["artifacts"].values())
    csv_rows = list(csv.DictReader(Path(second["artifacts"]["chain_summary_csv"]).open(encoding="utf-8")))
    assert len(csv_rows) == 1
    markdown = Path(second["artifacts"]["chain_summary_markdown"]).read_text(encoding="utf-8")
    assert "Operator Resolution Chain Preview" in markdown
    assert "This is a real-input preview chain, not a synthetic fixture" in markdown


def test_operator_resolution_chain_preview_never_calls_network_or_download_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task124 chain preview must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )

    assert report["status"] == "warning"
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_chain_preview_requires_inputs() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-chain-preview"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert {error.get("message") for error in report["errors"]} == {
        "operator_resolution_chain_output_dir_required",
        "operator_resolution_input_required",
        "document_intake_input_required",
    }


def test_operator_resolution_chain_review_board_unfilled_rows_create_refill_workspace(tmp_path: Path) -> None:
    rows = [
        _operator_resolution_validation_input_row(
            resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
            company_id="18",
            company_name="RZD",
        ),
        _operator_resolution_validation_input_row(),
    ]
    _run_operator_resolution_chain_preview(
        tmp_path,
        rows,
        [
            _operator_resolution_chain_placeholder(18, "RZD", "https://rzd.ru/reports/"),
            _operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
        ],
    )

    report = _run_operator_resolution_chain_review_board(tmp_path)

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["ready_count"] == 0
    assert report["needs_operator_action_count"] == 2
    assert report["overall_status_counts"] == {"needs_operator_exact_document_url": 2}
    assert report["primary_blocker_counts"] == {"missing_exact_document_url": 2}
    assert all(row["would_extract_values"] is False for row in report["rows"])
    refill = Path(report["artifacts"]["refill_workspace_csv"])
    refill_rows = list(csv.DictReader(refill.open(encoding="utf-8")))
    assert len(refill_rows) == 2
    assert all(row["operator_fill_exact_document_url"] == "" for row in refill_rows)
    assert {"resolution_id", "resolution_action_type", "READONLY_primary_blocker"} <= set(refill_rows[0])
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    rerun = Path(report["artifacts"]["rerun_markdown"]).read_text(encoding="utf-8")
    assert "python3 scripts/financial_official_source_evidence_assistant.py" in rerun
    assert "operator-resolution-chain-preview" in rerun


def test_operator_resolution_chain_review_board_rejects_historical_fallback_guidance(tmp_path: Path) -> None:
    historical = "https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2019.pdf"
    row = _operator_resolution_chain_valid_input_row(
        operator_fill_exact_document_url=historical,
        latest_historical_document_url=historical,
        latest_historical_period="2019",
    )
    _run_operator_resolution_chain_preview(
        tmp_path,
        [row],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )

    report = _run_operator_resolution_chain_review_board(tmp_path)
    board_row = report["rows"][0]

    assert board_row["overall_status"] == "operator_input_invalid"
    assert board_row["primary_blocker"] == "historical_fallback_url_used_as_exact_document"
    assert "Historical fallback is diagnostic-only" in board_row["operator_instruction"]
    assert board_row["historical_fallback_allowed_as_target_evidence"] is False


def test_operator_resolution_chain_review_board_ready_row_is_ready(tmp_path: Path) -> None:
    source_intake = tmp_path / "official_source_intake.json"
    _write_source_intake(
        source_intake,
        [
            _source_issuer(
                67,
                "Mostotrest",
                "official_issuer_report",
                "https://mostotrest.ru/ru/invest/financial-results/",
                "Mostotrest official reporting page",
            )
        ],
    )
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_chain_valid_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
        source_intake_input=source_intake,
        extra_args=["--required-company-ids", "67"],
    )

    report = _run_operator_resolution_chain_review_board(tmp_path)

    assert report["status"] == "passed"
    assert report["ready_count"] == 1
    assert report["ready_for_value_extraction_count"] == 1
    assert report["rows"][0]["overall_status"] == "ready_for_future_extraction_preview"


def test_operator_resolution_chain_review_board_trusted_hosts_use_baseline_only(tmp_path: Path) -> None:
    row = _operator_resolution_validation_input_row(
        operator_fill_source_page_url="https://manual-fill.example/reports/",
    )
    _run_operator_resolution_chain_preview(
        tmp_path,
        [row],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )
    source_pack = tmp_path / "operator_resolution_chain_source_pack.json"
    payload = json.loads(source_pack.read_text(encoding="utf-8"))
    source_row = payload["resolutions"][0]
    source_row["current_known_document_url"] = "https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf"
    source_row["current_known_source_page_url"] = "https://mostotrest.ru/ru/invest/financial-results/"
    source_row["latest_historical_document_url"] = "https://archive.example/annual-ifrs-2019.pdf"
    source_row["operator_fill_source_page_url"] = "https://operator-pack.example/reports/"
    source_pack.write_text(json.dumps(payload), encoding="utf-8")

    report = _run_operator_resolution_chain_review_board(tmp_path)

    assert report["rows"][0]["trusted_source_hosts"] == ["docs.mostotrest.ru", "mostotrest.ru"]
    assert "manual-fill.example" not in report["rows"][0]["trusted_source_hosts"]
    assert "archive.example" not in report["rows"][0]["trusted_source_hosts"]
    assert "operator-pack.example" not in report["rows"][0]["trusted_source_hosts"]


def test_operator_resolution_chain_review_board_missing_source_pack_and_stage_are_warnings(tmp_path: Path) -> None:
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
        include_source_pack=False,
    )
    Path(
        tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_PREVIEW_ARTIFACT_NAMES["document_intake_draft_gate_summary_json"]
    ).unlink()

    report = _run_operator_resolution_chain_review_board(tmp_path, include_source_pack=False)

    assert report["status"] == "warning"
    assert report["rows"][0]["trusted_source_hosts"] == []
    warning_messages = {warning["message"] for warning in report["warnings"]}
    assert "source_pack_missing_trusted_hosts_unavailable" in warning_messages
    assert "chain_stage_artifact_missing:document_intake_draft_gate_summary_json" in warning_messages


def test_operator_resolution_chain_review_board_direct_summary_and_explicit_output(tmp_path: Path) -> None:
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )
    summary = tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_PREVIEW_ARTIFACT_NAMES["chain_summary_json"]
    explicit = tmp_path / "custom" / "board.json"
    report = _run_operator_resolution_chain_review_board(
        tmp_path,
        use_output_dir=False,
        extra_args=[
            "--operator-resolution-chain-summary-input",
            str(summary),
            "--operator-resolution-chain-review-board-output",
            str(explicit),
        ],
    )

    assert report["status"] == "warning"
    assert Path(report["artifacts"]["board_json"]) == explicit
    assert explicit.is_file()
    assert Path(report["artifacts"]["refill_workspace_csv"]).parent == summary.parent


def test_operator_resolution_chain_review_board_output_collision_fails_safely(tmp_path: Path) -> None:
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )
    summary = tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_PREVIEW_ARTIFACT_NAMES["chain_summary_json"]
    original = summary.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-review-board",
            "--operator-resolution-chain-summary-input",
            str(summary),
            "--operator-resolution-chain-review-board-output",
            str(summary),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_chain_review_board_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert summary.read_bytes() == original


def test_operator_resolution_chain_review_board_refill_csv_is_task119_rerunnable(tmp_path: Path) -> None:
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )
    board = _run_operator_resolution_chain_review_board(tmp_path)
    refill = Path(board["artifacts"]["refill_workspace_csv"])
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-validate",
            "--operator-resolution-input",
            str(refill),
            "--operator-resolution-source-pack-input",
            str(tmp_path / "operator_resolution_chain_source_pack.json"),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["operator_resolution_validation_incomplete_count"] == 1


def test_operator_resolution_chain_review_board_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    _run_operator_resolution_chain_preview(
        tmp_path,
        [_operator_resolution_validation_input_row()],
        [_operator_resolution_chain_placeholder(67, "Mostotrest", "")],
    )

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task125 review board must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_chain_review_board(tmp_path)

    assert report["status"] == "warning"
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_chain_review_board_requires_summary() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-chain-review-board"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "operator_resolution_chain_summary_required"}]


def test_operator_resolution_source_trust_workspace_classifies_missing_and_ready_rows(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [
            _operator_resolution_source_trust_board_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
            ),
            _operator_resolution_source_trust_board_row(),
        ],
        [
            _operator_resolution_source_trust_source_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
            ),
            _operator_resolution_source_trust_source_row(
                current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            ),
        ],
    )
    rows = {row["company_id"]: row for row in report["rows"]}

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["trusted_source_missing_count"] == 1
    assert report["ready_for_document_url_refill_count"] == 1
    assert rows["18"]["source_trust_status"] == "trusted_source_missing"
    assert rows["67"]["source_trust_status"] == "ready_for_document_url_refill"
    assert rows["67"]["trusted_source_hosts"] == ["mostotrest.ru"]


def test_operator_resolution_source_trust_workspace_uses_baseline_aliases_only(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [
            _operator_resolution_source_trust_board_row(
                trusted_source_hosts=["task125-derived.example"],
                operator_fill_source_page_url="https://manual-source.example/reports/",
                operator_fill_exact_document_url="https://manual-document.example/annual-ifrs-2025.pdf",
            )
        ],
        [
            _operator_resolution_source_trust_source_row(
                official_source_url="https://invest.mostotrest.ru/reports/",
                source_url="https://docs.mostotrest.ru/annual-ifrs-2025.pdf",
                operator_fill_source_page_url="https://operator-pack.example/reports/",
                latest_historical_document_url="https://history.example/annual-ifrs-2019.pdf",
            )
        ],
    )
    row = report["rows"][0]

    assert row["source_trust_status"] == "trusted_source_available"
    assert row["trusted_source_hosts"] == ["docs.mostotrest.ru", "invest.mostotrest.ru"]
    assert "task125-derived.example" not in row["trusted_source_hosts"]
    assert "manual-source.example" not in row["trusted_source_hosts"]
    assert "manual-document.example" not in row["trusted_source_hosts"]
    assert "operator-pack.example" not in row["trusted_source_hosts"]
    assert "history.example" not in row["trusted_source_hosts"]
    assert "manual_operator_fill_urls_not_trusted" in row["trusted_source_status_reason_codes"]


def test_operator_resolution_source_trust_workspace_historical_only_is_conflict(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [_operator_resolution_source_trust_board_row()],
        [
            _operator_resolution_source_trust_source_row(
                latest_historical_document_url="https://mostotrest.ru/archive/annual-ifrs-2019.pdf",
                latest_historical_period="2019",
            )
        ],
    )
    row = report["rows"][0]

    assert row["source_trust_status"] == "trusted_source_conflict"
    assert row["trusted_source_hosts"] == []
    assert row["historical_fallback_allowed_as_trusted_source"] is False
    assert row["historical_fallback_allowed_as_target_evidence"] is False
    assert "historical_fallback_only_not_trusted_source" in row["trusted_source_status_reason_codes"]


def test_operator_resolution_source_trust_workspace_compares_registrable_domains(tmp_path: Path) -> None:
    compatible = _run_operator_resolution_source_trust_workspace(
        tmp_path / "compatible",
        [_operator_resolution_source_trust_board_row()],
        [
            _operator_resolution_source_trust_source_row(
                current_known_source_page_url="https://mostotrest.ru/reports/",
                current_known_document_url="https://docs.mostotrest.ru/annual-ifrs-2025.pdf",
            )
        ],
    )
    conflict = _run_operator_resolution_source_trust_workspace(
        tmp_path / "conflict",
        [_operator_resolution_source_trust_board_row()],
        [
            _operator_resolution_source_trust_source_row(
                current_known_source_page_url="https://mostotrest.ru/reports/",
                current_known_document_url="https://archive.example/annual-ifrs-2025.pdf",
            )
        ],
    )

    assert compatible["rows"][0]["source_trust_status"] == "ready_for_document_url_refill"
    assert conflict["rows"][0]["source_trust_status"] == "trusted_source_conflict"
    assert "baseline_source_domain_conflict" in conflict["rows"][0]["trusted_source_status_reason_codes"]


def test_operator_resolution_source_trust_workspace_archive_baseline_needs_review(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [_operator_resolution_source_trust_board_row()],
        [
            _operator_resolution_source_trust_source_row(
                current_known_source_page_url="https://mostotrest.ru/archive/reports/",
            )
        ],
    )
    row = report["rows"][0]

    assert row["source_trust_status"] == "trusted_source_needs_review"
    assert "archive_or_history_baseline_source_needs_review" in row["trusted_source_status_reason_codes"]


def test_operator_resolution_source_trust_workspace_missing_source_pack_warns_and_writes_outputs(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [_operator_resolution_source_trust_board_row()],
        include_source_pack=False,
    )

    assert report["status"] == "warning"
    assert report["rows"][0]["trusted_source_hosts"] == []
    assert any(
        warning["message"] == "source_pack_missing_trusted_hosts_unavailable"
        for warning in report["warnings"]
    )
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    refill_rows = list(csv.DictReader(Path(report["artifacts"]["refill_csv"]).open(encoding="utf-8")))
    assert "operator_fill_current_known_source_page_url" in refill_rows[0]
    assert "READONLY_source_trust_status" in refill_rows[0]
    markdown = Path(report["artifacts"]["workspace_markdown"]).read_text(encoding="utf-8")
    assert "Operator Resolution Source Trust Workspace" in markdown
    assert "Trusted hosts come only from baseline source-pack fields" in markdown
    rerun = Path(report["artifacts"]["rerun_markdown"]).read_text(encoding="utf-8")
    assert "Task126 does not apply source trust changes" in rerun


def test_operator_resolution_source_trust_workspace_direct_board_and_explicit_output(tmp_path: Path) -> None:
    board = tmp_path / "inputs" / "board.json"
    source_pack = tmp_path / "inputs" / "source_pack.json"
    _write_operator_resolution_source_trust_board(board, [_operator_resolution_source_trust_board_row()])
    _write_operator_resolution_source_trust_source_pack(
        source_pack,
        [_operator_resolution_source_trust_source_row(current_known_source_page_url="https://mostotrest.ru/reports/")],
    )
    explicit = tmp_path / "custom" / "workspace.json"
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-workspace",
            "--operator-resolution-chain-review-board-input",
            str(board),
            "--operator-resolution-source-pack-input",
            str(source_pack),
            "--operator-resolution-source-trust-output",
            str(explicit),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert Path(report["artifacts"]["workspace_json"]) == explicit
    assert explicit.is_file()
    assert Path(report["artifacts"]["refill_csv"]).parent == board.parent


def test_operator_resolution_source_trust_workspace_output_collision_fails_safely(tmp_path: Path) -> None:
    board = tmp_path / "board.json"
    _write_operator_resolution_source_trust_board(board, [_operator_resolution_source_trust_board_row()])
    original = board.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-workspace",
            "--operator-resolution-chain-review-board-input",
            str(board),
            "--operator-resolution-source-trust-output",
            str(board),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_source_trust_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert board.read_bytes() == original


def test_operator_resolution_source_trust_workspace_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task126 source trust workspace must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_source_trust_workspace(
        tmp_path,
        [_operator_resolution_source_trust_board_row()],
        [_operator_resolution_source_trust_source_row(current_known_source_page_url="https://mostotrest.ru/reports/")],
    )

    assert report["would_update_source_pack"] is False
    assert report["would_update_operator_pack"] is False
    assert report["would_update_original_intake"] is False
    assert report["would_update_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_source_trust_workspace_requires_board() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-source-trust-workspace"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "operator_resolution_chain_review_board_required"}]


def test_operator_resolution_source_trust_refill_vds_like_rows_are_incomplete_or_not_required(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_refill_validate(
        tmp_path,
        [
            _operator_resolution_source_trust_refill_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
                canonical_company_id="18",
                canonical_company_name="RZD",
            ),
            _operator_resolution_source_trust_refill_row(),
        ],
        [
            _operator_resolution_source_trust_source_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
                canonical_company_id="18",
                canonical_company_name="RZD",
            ),
            _operator_resolution_source_trust_source_row(
                current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            ),
        ],
    )
    rows = {row["company_id"]: row for row in report["rows"]}

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["incomplete_count"] == 1
    assert report["not_required_count"] == 1
    assert report["source_pack_draft_candidate_count"] == 0
    assert rows["18"]["validation_status"] == "incomplete_source_refill"
    assert "source_page_url_required" in rows["18"]["validation_reason_codes"]
    assert rows["67"]["validation_status"] == "source_refill_not_required"


def test_operator_resolution_source_trust_refill_valid_unknown_host_creates_pending_candidate_only(tmp_path: Path) -> None:
    source_pack = tmp_path / "source_pack.json"
    _write_operator_resolution_source_trust_source_pack(
        source_pack,
        [_operator_resolution_source_trust_source_row()],
    )
    original = source_pack.read_bytes()
    refill = _operator_resolution_source_trust_refill_row(
        operator_fill_current_known_source_page_url="https://invest.rzd-example.test/investors/reports/",
        operator_fill_source_review_status="operator_reviewed",
        operator_fill_source_notes="Candidate only.",
    )

    report = _run_operator_resolution_source_trust_refill_validate(
        tmp_path,
        [refill],
        source_pack_path=source_pack,
    )
    row = report["rows"][0]
    draft = json.loads(Path(report["artifacts"]["source_pack_draft_json"]).read_text(encoding="utf-8"))
    draft_row = draft["resolutions"][0]

    assert row["validation_status"] == "valid_source_candidate_for_future_review"
    assert "unknown_source_host_requires_future_review" in row["validation_warnings"]
    assert report["valid_candidate_count"] == 1
    assert report["source_pack_draft_candidate_count"] == 1
    assert draft_row["current_known_source_page_url"] == ""
    assert draft_row["current_known_document_url"] == ""
    assert draft_row["candidate_current_known_source_page_url"] == refill[
        "operator_fill_current_known_source_page_url"
    ]
    assert draft_row["operator_source_review_status"] == "pending_future_controlled_review"
    assert draft_row["trusted_host_status"] == "not_trusted_until_future_review"
    assert source_pack.read_bytes() == original
    assert report["original_source_pack_modified"] is False


def test_operator_resolution_source_trust_refill_rejects_unsafe_source_pages(tmp_path: Path) -> None:
    cases = {
        "blocked": ("https://news.example/reports/", "blocked_source_url"),
        "landing": ("https://mostotrest.ru/", "ambiguous_source_page"),
        "archive": ("https://mostotrest.ru/archive/reports/", "archive_or_history_source_not_allowed"),
        "pdf": ("https://mostotrest.ru/reports/annual-ifrs-2025.pdf", "source_page_expected_but_document_url_provided"),
        "historical": (
            "https://mostotrest.ru/reports/annual-ifrs-2019.pdf",
            "historical_fallback_url_not_allowed",
        ),
    }
    for name, (url, reason) in cases.items():
        report = _run_operator_resolution_source_trust_refill_validate(
            tmp_path / name,
            [_operator_resolution_source_trust_refill_row(operator_fill_current_known_source_page_url=url)],
            [
                _operator_resolution_source_trust_source_row(
                    latest_historical_document_url="https://mostotrest.ru/reports/annual-ifrs-2019.pdf",
                )
            ],
        )
        row = report["rows"][0]

        assert row["validation_status"] == "invalid_source_refill"
        assert reason in row["validation_errors"]
        assert report["source_pack_draft_candidate_count"] == 0


def test_operator_resolution_source_trust_refill_validates_optional_document_domain(tmp_path: Path) -> None:
    accepted = _run_operator_resolution_source_trust_refill_validate(
        tmp_path / "accepted",
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_current_known_source_page_url="https://invest.mostotrest.ru/reports/",
                operator_fill_current_known_document_url="https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf",
            )
        ],
    )
    rejected = _run_operator_resolution_source_trust_refill_validate(
        tmp_path / "rejected",
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_current_known_source_page_url="https://invest.mostotrest.ru/reports/",
                operator_fill_current_known_document_url="https://docs.other-issuer.ru/reports/annual-ifrs-2025.pdf",
            )
        ],
    )

    assert accepted["rows"][0]["validation_status"] == "valid_source_candidate_for_future_review"
    assert rejected["rows"][0]["validation_status"] == "invalid_source_refill"
    assert "source_document_host_conflict" in rejected["rows"][0]["validation_errors"]


def test_operator_resolution_source_trust_refill_notes_only_are_diagnostic(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_refill_validate(
        tmp_path,
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_source_review_status="needs_review",
                operator_fill_source_notes="Need an issuer IR page.",
            )
        ],
    )

    assert report["rows"][0]["validation_status"] == "source_refill_diagnostic_only"
    assert report["diagnostic_only_count"] == 1
    assert report["source_pack_draft_candidate_count"] == 0


def test_operator_resolution_source_trust_refill_missing_optional_context_warns_and_writes_candidate_draft(
    tmp_path: Path,
) -> None:
    report = _run_operator_resolution_source_trust_refill_validate(
        tmp_path,
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            )
        ],
        include_source_pack=False,
        include_board=False,
    )

    assert report["status"] == "warning"
    assert report["valid_candidate_count"] == 1
    assert any(warning["message"] == "review_board_context_missing" for warning in report["warnings"])
    assert any(warning["message"] == "source_pack_missing" for warning in report["warnings"])
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    draft = json.loads(Path(report["artifacts"]["source_pack_draft_json"]).read_text(encoding="utf-8"))
    assert draft["resolutions"][0]["source_context_status"] == "operator_source_candidate_for_future_controlled_review"
    markdown = Path(report["artifacts"]["validation_markdown"]).read_text(encoding="utf-8")
    assert "Operator Resolution Source Trust Refill Validation" in markdown
    assert "Baseline source trust remains unchanged" in markdown


def test_operator_resolution_source_trust_refill_output_collision_fails_safely(tmp_path: Path) -> None:
    refill = tmp_path / "refill.csv"
    _write_operator_resolution_source_trust_refill(refill, [_operator_resolution_source_trust_refill_row()])
    original = refill.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-refill-validate",
            "--operator-resolution-source-trust-refill-input",
            str(refill),
            "--operator-resolution-source-trust-validation-output",
            str(refill),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_source_trust_refill_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert refill.read_bytes() == original


def test_operator_resolution_source_trust_refill_preserves_rows_shape_and_explicit_output(tmp_path: Path) -> None:
    refill = tmp_path / "inputs" / "refill.csv"
    source_pack = tmp_path / "inputs" / "source_pack.json"
    explicit_draft = tmp_path / "custom" / "source_pack_draft.json"
    _write_operator_resolution_source_trust_refill(
        refill,
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            )
        ],
    )
    source_pack.parent.mkdir(parents=True, exist_ok=True)
    source_pack.write_text(
        json.dumps({"status": "template", "mode": "operator-resolution-pack", "rows": []}),
        encoding="utf-8",
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-refill-validate",
            "--operator-resolution-source-trust-refill-input",
            str(refill),
            "--operator-resolution-source-pack-input",
            str(source_pack),
            "--operator-resolution-source-pack-draft-output",
            str(explicit_draft),
        ]
    )

    report, exit_code = assistant.run_assistant(args)
    draft = json.loads(explicit_draft.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert Path(report["artifacts"]["source_pack_draft_json"]) == explicit_draft
    assert "rows" in draft
    assert "resolutions" not in draft
    assert draft["rows"][0]["candidate_current_known_source_page_url"] == (
        "https://mostotrest.ru/ru/invest/financial-results/"
    )


def test_operator_resolution_source_trust_refill_generic_output_collision_fails_safely(tmp_path: Path) -> None:
    refill = tmp_path / "refill.csv"
    _write_operator_resolution_source_trust_refill(refill, [_operator_resolution_source_trust_refill_row()])
    original = refill.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-refill-validate",
            "--operator-resolution-source-trust-refill-input",
            str(refill),
            "--json-output",
            str(refill),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_source_trust_refill_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert refill.read_bytes() == original


def test_operator_resolution_source_trust_refill_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task127 source trust refill validation must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_source_trust_refill_validate(
        tmp_path,
        [
            _operator_resolution_source_trust_refill_row(
                operator_fill_current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
            )
        ],
    )

    assert report["would_update_original_source_pack"] is False
    assert report["would_trust_manual_source"] is False
    assert report["would_promote_source"] is False
    assert report["would_update_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_source_trust_refill_requires_input() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-source-trust-refill-validate"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "operator_resolution_source_trust_refill_input_required"}]


def test_operator_resolution_source_trust_draft_review_vds_like_rows_have_no_candidates(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [
            _operator_resolution_source_pack_draft_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
            ),
            _operator_resolution_source_pack_draft_row(),
        ],
    )

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["candidate_count"] == 0
    assert report["no_candidate_count"] == 2
    assert report["eligible_for_future_promotion_count"] == 0
    assert report["promote_draft_candidate_count"] == 0
    assert all(row["review_status"] == "no_source_candidate_present" for row in report["rows"])


def test_operator_resolution_source_trust_draft_review_pending_candidate_remains_pending(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [
            _operator_resolution_source_pack_draft_row(
                candidate_current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
                source_context_status="operator_source_candidate_for_future_controlled_review",
                operator_source_review_status="pending_future_controlled_review",
                trusted_host_status="not_trusted_until_future_review",
            )
        ],
    )

    assert report["rows"][0]["review_status"] == "source_candidate_pending_review"
    promote = _load_task128_promote_rows(report)[0]
    assert promote["promote_preview_status"] == "not_eligible_pending_review"
    assert promote["would_promote_source_now"] is False


def test_operator_resolution_source_trust_draft_review_approved_candidate_writes_promote_draft_only(tmp_path: Path) -> None:
    draft = tmp_path / "source_pack_draft.json"
    row = _operator_resolution_source_pack_draft_row(
        candidate_current_known_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
        candidate_current_known_document_url="https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf",
        source_context_status="operator_source_candidate_for_future_controlled_review",
        operator_source_review_status="approved_for_future_promotion",
        trusted_host_status="not_trusted_until_future_review",
    )
    _write_operator_resolution_source_pack_draft(draft, [row])
    original = draft.read_bytes()

    report = _run_operator_resolution_source_trust_draft_review(tmp_path, [row], draft_path=draft)
    reviewed = report["rows"][0]
    promote = _load_task128_promote_rows(report)[0]
    promote_draft = json.loads(Path(report["artifacts"]["source_pack_promote_draft_json"]).read_text(encoding="utf-8"))
    promoted = promote_draft["resolutions"][0]

    assert reviewed["review_status"] == "source_candidate_eligible_for_future_promotion"
    assert promote["promote_preview_status"] == "eligible_for_source_pack_promote_draft"
    assert promoted["current_known_source_page_url"] == row["candidate_current_known_source_page_url"]
    assert promoted["current_known_document_url"] == row["candidate_current_known_document_url"]
    assert promoted["trusted_host_status"] == "candidate_reviewed_not_yet_baseline_trusted"
    assert promoted["candidate_current_known_source_page_url"] == row["candidate_current_known_source_page_url"]
    assert draft.read_bytes() == original
    assert report["source_pack_draft_modified"] is False
    assert report["would_update_original_source_pack"] is False


def test_operator_resolution_source_trust_draft_review_unknown_host_approved_still_needs_decision(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [
            _operator_resolution_source_pack_draft_row(
                candidate_current_known_source_page_url="https://invest.issuer-example.test/investors/reports/",
                source_context_status="operator_source_candidate_for_future_controlled_review",
                operator_source_review_status="approved_for_future_promotion",
                trusted_host_status="not_trusted_until_future_review",
            )
        ],
    )

    assert report["rows"][0]["review_status"] == "source_candidate_already_reviewed_but_not_promoted"
    assert "unknown_source_host_requires_future_review" in report["rows"][0]["review_warnings"]
    assert _load_task128_promote_rows(report)[0]["promote_preview_status"] == "not_eligible_warning_requires_manual_decision"


def test_operator_resolution_source_trust_draft_review_rejects_invalid_candidates(tmp_path: Path) -> None:
    cases = {
        "non_http": ("ftp://mostotrest.ru/reports/", "invalid_or_missing_http_url"),
        "archive": ("https://mostotrest.ru/archive/reports/", "archive_or_history_source_not_allowed"),
        "landing": ("https://mostotrest.ru/", "ambiguous_source_page"),
        "pdf": ("https://mostotrest.ru/reports/annual-ifrs-2025.pdf", "source_page_expected_but_document_url_provided"),
        "historical": (
            "https://mostotrest.ru/reports/annual-ifrs-2019.pdf",
            "historical_fallback_url_not_allowed",
        ),
    }
    for name, (url, reason) in cases.items():
        report = _run_operator_resolution_source_trust_draft_review(
            tmp_path / name,
            [
                _operator_resolution_source_pack_draft_row(
                    candidate_current_known_source_page_url=url,
                    latest_historical_document_url="https://mostotrest.ru/reports/annual-ifrs-2019.pdf",
                    source_context_status="operator_source_candidate_for_future_controlled_review",
                    operator_source_review_status="approved_for_future_promotion",
                )
            ],
        )

        assert report["rows"][0]["review_status"] == "source_candidate_invalid"
        assert reason in report["rows"][0]["review_errors"]
        assert _load_task128_promote_rows(report)[0]["promote_preview_status"] == "not_eligible_invalid_candidate"


def test_operator_resolution_source_trust_draft_review_blocks_candidate_domain_conflicts(tmp_path: Path) -> None:
    document_conflict = _run_operator_resolution_source_trust_draft_review(
        tmp_path / "document",
        [
            _operator_resolution_source_pack_draft_row(
                candidate_current_known_source_page_url="https://mostotrest.ru/reports/",
                candidate_current_known_document_url="https://other-issuer.ru/reports/annual-ifrs-2025.pdf",
                operator_source_review_status="approved_for_future_promotion",
            )
        ],
    )
    baseline_conflict = _run_operator_resolution_source_trust_draft_review(
        tmp_path / "baseline",
        [
            _operator_resolution_source_pack_draft_row(
                current_known_source_page_url="https://mostotrest.ru/reports/",
                candidate_current_known_source_page_url="https://other-issuer.ru/reports/",
                operator_source_review_status="approved_for_future_promotion",
            )
        ],
    )

    assert document_conflict["rows"][0]["review_status"] == "source_candidate_conflict"
    assert "source_document_host_conflict" in document_conflict["rows"][0]["review_reason_codes"]
    assert baseline_conflict["rows"][0]["review_status"] == "source_candidate_conflict"
    assert "candidate_baseline_source_domain_conflict" in baseline_conflict["rows"][0]["review_reason_codes"]


def test_operator_resolution_source_trust_draft_review_blocks_task127_artifact_drift(tmp_path: Path) -> None:
    row = _operator_resolution_source_pack_draft_row(
        candidate_current_known_source_page_url="https://mostotrest.ru/reports/",
        operator_source_review_status="approved_for_future_promotion",
    )
    validation = {
        "resolution_id": row["resolution_id"],
        "operator_fill_current_known_source_page_url": "https://mostotrest.ru/old-reports/",
        "operator_fill_current_known_document_url": "",
        "validation_status": "valid_source_candidate_for_future_review",
    }
    patch = {
        "resolution_id": row["resolution_id"],
        "candidate_current_known_source_page_url": row["candidate_current_known_source_page_url"],
        "candidate_current_known_document_url": "",
        "patch_status": "eligible_for_source_pack_draft",
    }

    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [row],
        validation_rows=[validation],
        patch_rows=[patch],
    )

    assert report["rows"][0]["review_status"] == "source_candidate_conflict"
    assert "task127_source_candidate_artifact_drift" in report["rows"][0]["review_reason_codes"]


def test_operator_resolution_source_trust_draft_review_missing_optional_artifacts_warns_and_writes_outputs(
    tmp_path: Path,
) -> None:
    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [_operator_resolution_source_pack_draft_row()],
    )

    assert report["status"] == "warning"
    assert any(warning["message"] == "task127_validation_context_missing" for warning in report["warnings"])
    assert any(warning["message"] == "task127_patch_preview_context_missing" for warning in report["warnings"])
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    markdown = Path(report["artifacts"]["review_markdown"]).read_text(encoding="utf-8")
    assert "Operator Source Trust Draft Review" in markdown
    assert "does not make manual URLs trusted automatically" in markdown


def test_operator_resolution_source_trust_draft_review_preserves_rows_shape_and_explicit_output(tmp_path: Path) -> None:
    draft = tmp_path / "inputs" / "source_pack_draft.json"
    explicit = tmp_path / "custom" / "promote_draft.json"
    row = _operator_resolution_source_pack_draft_row(
        candidate_current_known_source_page_url="https://mostotrest.ru/reports/",
        operator_source_review_status="approved_for_future_promotion",
    )
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(json.dumps({"rows": [row]}), encoding="utf-8")
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-draft-review",
            "--operator-resolution-source-pack-draft-input",
            str(draft),
            "--operator-resolution-source-pack-promote-draft-output",
            str(explicit),
        ]
    )

    report, exit_code = assistant.run_assistant(args)
    promoted = json.loads(explicit.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert Path(report["artifacts"]["source_pack_promote_draft_json"]) == explicit
    assert "rows" in promoted
    assert "resolutions" not in promoted


def test_operator_resolution_source_trust_draft_review_preserves_raw_list_shape(tmp_path: Path) -> None:
    draft = tmp_path / "source_pack_draft.json"
    row = _operator_resolution_source_pack_draft_row(
        candidate_current_known_source_page_url="https://mostotrest.ru/reports/",
        operator_source_review_status="approved_for_future_promotion",
    )
    draft.write_text(json.dumps([row]), encoding="utf-8")

    report = _run_operator_resolution_source_trust_draft_review(tmp_path, [row], draft_path=draft)
    promoted = json.loads(Path(report["artifacts"]["source_pack_promote_draft_json"]).read_text(encoding="utf-8"))

    assert isinstance(promoted, list)
    assert promoted[0]["current_known_source_page_url"] == row["candidate_current_known_source_page_url"]


def test_operator_resolution_source_trust_draft_review_output_collision_fails_safely(tmp_path: Path) -> None:
    draft = tmp_path / "source_pack_draft.json"
    _write_operator_resolution_source_pack_draft(draft, [_operator_resolution_source_pack_draft_row()])
    original = draft.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-draft-review",
            "--operator-resolution-source-pack-draft-input",
            str(draft),
            "--json-output",
            str(draft),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_source_trust_draft_review_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert draft.read_bytes() == original


def test_operator_resolution_source_trust_draft_review_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task128 source trust draft review must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_source_trust_draft_review(
        tmp_path,
        [_operator_resolution_source_pack_draft_row()],
    )

    assert report["would_update_original_source_pack"] is False
    assert report["would_trust_manual_source"] is False
    assert report["would_promote_source"] is False
    assert report["would_update_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_source_trust_draft_review_requires_input() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-source-trust-draft-review"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "operator_resolution_source_pack_draft_input_required"}]


def test_operator_resolution_source_trust_promote_apply_vds_like_preview_applies_nothing(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [
            _operator_resolution_source_trust_promote_preview_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
                promote_preview_status="not_eligible_no_candidate",
                promote_preview_action="preview_noop",
            ),
            _operator_resolution_source_trust_promote_preview_row(
                promote_preview_status="not_eligible_no_candidate",
                promote_preview_action="preview_noop",
            ),
        ],
        [
            _operator_resolution_source_pack_draft_row(
                resolution_id="financial_report_resolution:18:2025:annual:IFRS:fill_exact_document_url",
                company_id="18",
                company_name="RZD",
            ),
            _operator_resolution_source_pack_draft_row(),
        ],
    )

    assert report["row_count"] == 2
    assert report["applied_count"] == 0
    assert report["skipped_count"] == 2
    assert report["failed_count"] == 0
    assert report["apply_status_counts"] == {"skipped_not_eligible_no_candidate": 2}


def test_operator_resolution_source_trust_promote_apply_eligible_row_updates_new_draft_only(tmp_path: Path) -> None:
    preview = _operator_resolution_source_trust_promote_preview_row()
    draft = _operator_resolution_source_pack_promote_draft_row()
    report = _run_operator_resolution_source_trust_promote_apply(tmp_path, [preview], [draft])
    applied = report["apply_rows"][0]
    promoted = json.loads(
        Path(report["artifacts"]["source_pack_promoted_apply_draft_json"]).read_text(encoding="utf-8")
    )["resolutions"][0]

    assert applied["apply_status"] == "applied_to_promoted_source_pack_draft"
    assert applied["would_change_promoted_apply_draft"] is True
    assert report["applied_count"] == 1
    assert promoted["source_context_status"] == "source_context_promoted_apply_draft_for_future_merge"
    assert promoted["trusted_host_status"] == "promoted_apply_draft_not_baseline_trusted"
    assert promoted["candidate_current_known_source_page_url"] == preview["proposed_current_known_source_page_url"]
    assert report["would_update_original_source_pack"] is False
    assert report["would_promote_source_now"] is False


def test_operator_resolution_source_trust_promote_apply_eligible_missing_source_fails_row(tmp_path: Path) -> None:
    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [_operator_resolution_source_trust_promote_preview_row(proposed_current_known_source_page_url="")],
        [_operator_resolution_source_pack_promote_draft_row(current_known_source_page_url="")],
    )

    assert report["status"] == "warning"
    assert report["failed_count"] == 1
    assert report["apply_rows"][0]["apply_status"] == "failed_apply_missing_proposed_source"


def test_operator_resolution_source_trust_promote_apply_skips_noneligible_statuses(tmp_path: Path) -> None:
    statuses = {
        "not_eligible_pending_review": "skipped_not_eligible_pending_review",
        "not_eligible_invalid_candidate": "skipped_not_eligible_invalid_candidate",
        "not_eligible_candidate_conflict": "skipped_not_eligible_candidate_conflict",
        "not_eligible_warning_requires_manual_decision": "skipped_not_eligible_warning_requires_manual_decision",
    }
    preview_rows = [
        _operator_resolution_source_trust_promote_preview_row(
            resolution_id=f"resolution:{index}",
            company_id=str(index),
            promote_preview_status=status,
            promote_preview_action="preview_noop",
        )
        for index, status in enumerate(statuses, start=1)
    ]
    draft_rows = [
        _operator_resolution_source_pack_promote_draft_row(
            resolution_id=str(row["resolution_id"]),
            company_id=str(row["company_id"]),
        )
        for row in preview_rows
    ]

    report = _run_operator_resolution_source_trust_promote_apply(tmp_path, preview_rows, draft_rows)

    assert [row["apply_status"] for row in report["apply_rows"]] == list(statuses.values())
    assert report["skipped_count"] == 4


def test_operator_resolution_source_trust_promote_apply_blocks_missing_match_drift_and_unsafe_flags(tmp_path: Path) -> None:
    missing = _run_operator_resolution_source_trust_promote_apply(
        tmp_path / "missing",
        [_operator_resolution_source_trust_promote_preview_row()],
        [],
    )
    drift = _run_operator_resolution_source_trust_promote_apply(
        tmp_path / "drift",
        [_operator_resolution_source_trust_promote_preview_row()],
        [_operator_resolution_source_pack_promote_draft_row(current_known_source_page_url="https://mostotrest.ru/other-reports/")],
    )
    unsafe = _run_operator_resolution_source_trust_promote_apply(
        tmp_path / "unsafe",
        [_operator_resolution_source_trust_promote_preview_row(would_extract_values=True)],
        [_operator_resolution_source_pack_promote_draft_row()],
    )

    assert missing["apply_rows"][0]["apply_status"] == "failed_apply_missing_matching_promote_draft_row"
    assert drift["apply_rows"][0]["apply_status"] == "failed_apply_task128_artifact_drift"
    assert unsafe["apply_rows"][0]["apply_status"] == "failed_apply_unsafe_preview_flags"


def test_operator_resolution_source_trust_promote_apply_blocks_duplicate_resolution_ids(tmp_path: Path) -> None:
    preview = _operator_resolution_source_trust_promote_preview_row()
    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [preview, dict(preview)],
        [_operator_resolution_source_pack_promote_draft_row()],
    )

    assert report["failed_count"] == 2
    assert all(row["apply_status"] == "failed_apply_duplicate_resolution_id" for row in report["apply_rows"])


def test_operator_resolution_source_trust_promote_apply_blocks_duplicate_draft_resolution_ids(tmp_path: Path) -> None:
    draft = _operator_resolution_source_pack_promote_draft_row()
    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [_operator_resolution_source_trust_promote_preview_row()],
        [draft, dict(draft)],
    )

    assert report["failed_count"] == 1
    assert report["apply_rows"][0]["apply_status"] == "failed_apply_duplicate_resolution_id"


def test_operator_resolution_source_trust_promote_apply_preserves_inputs_and_raw_list_shape(tmp_path: Path) -> None:
    preview_path = tmp_path / "preview.json"
    draft_path = tmp_path / "promote_draft.json"
    preview = _operator_resolution_source_trust_promote_preview_row()
    draft = _operator_resolution_source_pack_promote_draft_row()
    _write_operator_resolution_source_trust_promote_preview(preview_path, [preview])
    draft_path.write_text(json.dumps([draft]), encoding="utf-8")
    preview_original = preview_path.read_bytes()
    draft_original = draft_path.read_bytes()

    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [preview],
        [draft],
        preview_path=preview_path,
        draft_path=draft_path,
    )
    promoted = json.loads(
        Path(report["artifacts"]["source_pack_promoted_apply_draft_json"]).read_text(encoding="utf-8")
    )

    assert isinstance(promoted, list)
    assert preview_path.read_bytes() == preview_original
    assert draft_path.read_bytes() == draft_original
    assert report["promote_preview_input_modified"] is False
    assert report["task128_promote_draft_modified"] is False


def test_operator_resolution_source_trust_promote_apply_preserves_rows_shape_and_explicit_output(tmp_path: Path) -> None:
    preview_path = tmp_path / "preview.json"
    draft_path = tmp_path / "promote_draft.json"
    output_path = tmp_path / "custom" / "promoted_apply_draft.json"
    _write_operator_resolution_source_trust_promote_preview(
        preview_path,
        [_operator_resolution_source_trust_promote_preview_row()],
    )
    draft_path.write_text(
        json.dumps({"rows": [_operator_resolution_source_pack_promote_draft_row()]}),
        encoding="utf-8",
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-promote-apply-draft",
            "--operator-resolution-source-trust-promote-preview-input",
            str(preview_path),
            "--operator-resolution-source-pack-promote-draft-input",
            str(draft_path),
            "--operator-resolution-source-pack-promoted-apply-draft-output",
            str(output_path),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["applied_count"] == 1
    assert report["artifacts"]["source_pack_promoted_apply_draft_json"] == str(output_path)
    promoted = json.loads(output_path.read_text(encoding="utf-8"))
    assert "rows" in promoted
    assert "resolutions" not in promoted


def test_operator_resolution_source_trust_promote_apply_output_dir_defaults_write_artifacts(tmp_path: Path) -> None:
    preview_path = tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_DRAFT_REVIEW_ARTIFACT_NAMES["promote_preview_json"]
    draft_path = tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_DRAFT_REVIEW_ARTIFACT_NAMES[
        "source_pack_promote_draft_json"
    ]
    _write_operator_resolution_source_trust_promote_preview(
        preview_path,
        [_operator_resolution_source_trust_promote_preview_row()],
    )
    _write_operator_resolution_source_pack_draft(
        draft_path,
        [_operator_resolution_source_pack_promote_draft_row()],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-promote-apply-draft",
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["applied_count"] == 1
    for path in report["artifacts"].values():
        assert Path(path).is_file()
    assert "# Operator Source Trust Promote Apply Draft" in Path(report["artifacts"]["apply_markdown"]).read_text(
        encoding="utf-8"
    )


def test_operator_resolution_source_trust_promote_apply_output_collision_fails_safely(tmp_path: Path) -> None:
    preview_path = tmp_path / "preview.json"
    draft_path = tmp_path / "promote_draft.json"
    _write_operator_resolution_source_trust_promote_preview(preview_path, [])
    _write_operator_resolution_source_pack_draft(draft_path, [])
    original = draft_path.read_bytes()
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-promote-apply-draft",
            "--operator-resolution-source-trust-promote-preview-input",
            str(preview_path),
            "--operator-resolution-source-pack-promote-draft-input",
            str(draft_path),
            "--json-output",
            str(draft_path),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(
        error["message"] == "operator_resolution_source_trust_promote_apply_output_must_not_equal_input"
        for error in report["errors"]
    )
    assert draft_path.read_bytes() == original


def test_operator_resolution_source_trust_promote_apply_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task129 promote apply draft must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_operator_resolution_source_trust_promote_apply(
        tmp_path,
        [_operator_resolution_source_trust_promote_preview_row()],
        [_operator_resolution_source_pack_promote_draft_row()],
    )

    assert report["would_update_original_source_pack"] is False
    assert report["would_update_task127_draft"] is False
    assert report["would_update_task128_promote_draft"] is False
    assert report["would_trust_manual_source"] is False
    assert report["would_promote_source_now"] is False
    assert report["would_update_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_operator_resolution_source_trust_promote_apply_requires_inputs() -> None:
    args = assistant.parse_args(["--mode", "operator-resolution-source-trust-promote-apply-draft"])

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["errors"] == [{"message": "operator_resolution_source_trust_promote_preview_input_required"}]


def test_operator_resolution_source_trust_promote_apply_requires_draft_input(tmp_path: Path) -> None:
    preview = tmp_path / "preview.json"
    _write_operator_resolution_source_trust_promote_preview(preview, [])
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-promote-apply-draft",
            "--operator-resolution-source-trust-promote-preview-input",
            str(preview),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["errors"] == [{"message": "operator_resolution_source_pack_promote_draft_input_required"}]


def test_financial_metric_registry_preview_has_expected_core_metrics_and_safe_contract() -> None:
    report = _run_financial_metric_registry_preview()
    by_id = {row["metric_id"]: row for row in report["metrics"]}

    assert report["status"] == "passed"
    assert report["metric_count"] >= 30
    assert report["alias_count"] >= 40
    assert report["validation_rule_count"] >= 10
    assert report["feature_count"] >= 10
    for metric_id in (
        "revenue",
        "total_debt",
        "cash_and_cash_equivalents",
        "net_debt",
        "ebitda",
        "finance_costs",
    ):
        assert metric_id in by_id
    assert by_id["cash_and_cash_equivalents"]["canonical_financial_report_field"] == "cash"
    assert by_id["total_equity"]["canonical_financial_report_field"] == "equity"
    assert by_id["revenue"]["canonical_financial_report_field"] == "revenue"
    for metric in report["metrics"]:
        if metric["expected_value_type"] == "monetary":
            assert metric["default_currency"] == ""
            assert metric["default_scale"] == ""
    assert report["would_fetch_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False


def test_financial_metric_registry_preview_writes_default_and_explicit_outputs(tmp_path: Path) -> None:
    explicit_aliases = tmp_path / "custom" / "aliases.csv"
    report = _run_financial_metric_registry_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--financial-metric-registry-alias-csv-output",
            str(explicit_aliases),
        ]
    )

    assert report["artifacts"]["alias_csv"] == str(explicit_aliases)
    for path in report["artifacts"].values():
        assert Path(path).is_file()
    assert "# Financial Metric Registry" in Path(report["artifacts"]["registry_markdown"]).read_text(encoding="utf-8")
    assert "This task does not fetch or parse reports." in Path(report["artifacts"]["registry_markdown"]).read_text(
        encoding="utf-8"
    )
    registry_csv = Path(report["artifacts"]["registry_csv"]).read_text(encoding="utf-8")
    assert "metric_id" in registry_csv
    assert "canonical_financial_report_field" in registry_csv


def test_financial_metric_registry_preview_filters_debt_metrics_and_related_rows() -> None:
    report = _run_financial_metric_registry_preview(["--financial-metric-registry-category", "debt"])
    visible_ids = {row["metric_id"] for row in report["metrics"]}

    assert visible_ids
    assert all(row["metric_category"] == "debt" for row in report["metrics"])
    assert all(row["metric_id"] in visible_ids for row in report["aliases"])
    assert all(
        visible_ids.intersection([*row["required_metric_ids"], *row["optional_metric_ids"]])
        for row in report["validation_rules"]
    )
    assert all(
        visible_ids.intersection([*row["required_metric_ids"], *row["optional_metric_ids"]])
        for row in report["feature_map"]
    )


def test_financial_metric_registry_preview_filters_required_and_model_critical_metrics() -> None:
    critical = _run_financial_metric_registry_preview(["--financial-metric-registry-model-critical-only"])
    required = _run_financial_metric_registry_preview(["--financial-metric-registry-required-only"])
    combined = _run_financial_metric_registry_preview(
        [
            "--financial-metric-registry-category",
            "debt",
            "--financial-metric-registry-model-critical-only",
            "--financial-metric-registry-required-only",
        ]
    )

    assert critical["metrics"]
    assert all(row["model_critical"] is True for row in critical["metrics"])
    assert required["metrics"]
    assert all(row["required_for_model"] is True for row in required["metrics"])
    assert combined["metrics"]
    assert all(
        row["metric_category"] == "debt"
        and row["model_critical"] is True
        and row["required_for_model"] is True
        for row in combined["metrics"]
    )


def test_financial_metric_registry_preview_dependencies_reference_known_metrics() -> None:
    report = _run_financial_metric_registry_preview()
    metric_ids = {row["metric_id"] for row in report["metrics"]}

    assert all(row["metric_id"] in metric_ids for row in report["aliases"])
    for row in [*report["validation_rules"], *report["feature_map"]]:
        assert set([*row["required_metric_ids"], *row["optional_metric_ids"]]).issubset(metric_ids)


def test_financial_metric_registry_preview_integrity_failure_is_safe(monkeypatch) -> None:
    duplicate = dict(assistant.FINANCIAL_METRIC_REGISTRY[0])
    monkeypatch.setattr(assistant, "FINANCIAL_METRIC_REGISTRY", [*assistant.FINANCIAL_METRIC_REGISTRY, duplicate])

    report = _run_financial_metric_registry_preview()

    assert report["status"] == "failed"
    assert {"message": f"duplicate_metric_id:{duplicate['metric_id']}"} in report["errors"]
    assert report["would_fetch_documents"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False


def test_financial_metric_registry_preview_never_calls_network_helpers(monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task130 metric registry must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_financial_metric_registry_preview()

    assert report["status"] == "passed"
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False


def test_financial_extraction_evidence_schema_preview_has_typed_templates_and_safe_contract() -> None:
    report = _run_financial_extraction_evidence_schema_preview()

    assert report["status"] == "passed"
    assert report["registry_source"] == "in_code_registry"
    assert report["registry_metric_count"] >= 30
    assert report["value_candidate_field_count"] >= 60
    assert report["evidence_field_count"] >= 30
    assert report["normalized_fact_field_count"] >= 30
    assert report["validation_status_count"] >= 20
    assert report["template_candidate_count"] == 7
    assert report["template_evidence_count"] == 7
    assert report["template_normalized_fact_count"] == 7
    assert {row["metric_id"] for row in report["template_value_candidates"]} == set(
        assistant.FINANCIAL_EXTRACTION_TEMPLATE_METRIC_IDS
    )
    assert "template_only" in {row["status_id"] for row in report["validation_statuses"]}
    for key in (
        "would_fetch_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[key] is False


def test_financial_extraction_evidence_schema_template_references_and_fields_are_consistent() -> None:
    report = _run_financial_extraction_evidence_schema_preview()
    metric_ids = {row["metric_id"] for row in assistant.FINANCIAL_METRIC_REGISTRY}
    candidate_ids = {row["candidate_id"] for row in report["template_value_candidates"]}
    evidence_ids = {row["evidence_id"] for row in report["template_evidence_rows"]}

    for descriptors in (
        report["value_candidate_fields"],
        report["evidence_fields"],
        report["normalized_fact_fields"],
    ):
        names = [row["field_name"] for row in descriptors]
        assert len(names) == len(set(names))
        assert all({"field_name", "field_group", "field_type", "required", "description"} <= set(row) for row in descriptors)
    assert all(row["metric_id"] in metric_ids for row in report["template_value_candidates"])
    assert all(row["candidate_id"] in candidate_ids for row in report["template_evidence_rows"])
    assert all(row["candidate_id"] in candidate_ids for row in report["template_normalized_facts"])
    assert all(set(row["evidence_ids"]).issubset(evidence_ids) for row in report["template_normalized_facts"])
    for row in [
        *report["template_value_candidates"],
        *report["template_evidence_rows"],
        *report["template_normalized_facts"],
    ]:
        assert all(
            value is False
            for field, value in row.items()
            if field.startswith("would_") or field.startswith("ready_")
        )


def test_financial_extraction_evidence_schema_preview_writes_default_and_explicit_outputs(tmp_path: Path) -> None:
    custom_markdown = tmp_path / "custom" / "schema.md"
    report = _run_financial_extraction_evidence_schema_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--financial-extraction-evidence-schema-markdown-output",
            str(custom_markdown),
        ]
    )

    assert report["status"] == "warning"
    assert {"message": "financial_metric_registry_default_artifact_missing_using_in_code_registry"} in report["warnings"]
    assert report["artifacts"]["schema_markdown"] == str(custom_markdown)
    for path in report["artifacts"].values():
        assert Path(path).is_file()
    markdown = custom_markdown.read_text(encoding="utf-8")
    assert "# Financial Extraction Evidence Schema" in markdown
    assert "This task does not read, fetch, download, or parse reports." in markdown


def test_financial_extraction_evidence_schema_preview_loads_reduced_registry_intersection(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"metrics": [{"metric_id": "revenue", "metric_name": "Revenue"}]}),
        encoding="utf-8",
    )

    report = _run_financial_extraction_evidence_schema_preview(
        ["--financial-metric-registry-input", str(registry)]
    )

    assert report["status"] == "passed"
    assert report["registry_source"] == str(registry)
    assert report["registry_metric_count"] == 1
    assert report["template_candidate_count"] == 1
    assert report["template_value_candidates"][0]["metric_id"] == "revenue"


def test_financial_extraction_evidence_schema_preview_warns_for_empty_template_intersection(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"metrics": [{"metric_id": "custom_metric"}]}), encoding="utf-8")

    report = _run_financial_extraction_evidence_schema_preview(
        ["--financial-metric-registry-input", str(registry)]
    )

    assert report["status"] == "warning"
    assert report["template_candidate_count"] == 0
    assert {"message": "financial_extraction_template_metrics_unavailable"} in report["warnings"]


def test_financial_extraction_evidence_schema_preview_rejects_invalid_registry_input(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"metrics": "not-a-list"}), encoding="utf-8")

    report = _run_financial_extraction_evidence_schema_preview(
        ["--financial-metric-registry-input", str(registry)]
    )

    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "financial_metric_registry_input_invalid"}]


def test_financial_extraction_evidence_schema_preview_rejects_output_collision(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"metrics": [{"metric_id": "revenue"}]}), encoding="utf-8")
    original = registry.read_bytes()

    report = _run_financial_extraction_evidence_schema_preview(
        [
            "--financial-metric-registry-input",
            str(registry),
            "--json-output",
            str(registry),
        ]
    )

    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "financial_extraction_evidence_schema_output_must_not_equal_input"}]
    assert registry.read_bytes() == original


def test_financial_extraction_evidence_schema_preview_integrity_failure_is_safe(monkeypatch) -> None:
    duplicate = dict(assistant.FINANCIAL_EXTRACTION_VALIDATION_STATUSES[0])
    monkeypatch.setattr(
        assistant,
        "FINANCIAL_EXTRACTION_VALIDATION_STATUSES",
        [*assistant.FINANCIAL_EXTRACTION_VALIDATION_STATUSES, duplicate],
    )

    report = _run_financial_extraction_evidence_schema_preview()

    assert report["status"] == "failed"
    assert {"message": "duplicate_validation_status_id"} in report["errors"]
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False


def test_financial_extraction_evidence_schema_preview_bad_template_reference_is_safe(monkeypatch) -> None:
    original_builder = assistant._build_financial_extraction_evidence_templates

    def build_bad_reference(registry_metrics):
        candidates, evidence_rows, normalized_facts = original_builder(registry_metrics)
        evidence_rows[0]["candidate_id"] = "template_candidate:missing"
        return candidates, evidence_rows, normalized_facts

    monkeypatch.setattr(assistant, "_build_financial_extraction_evidence_templates", build_bad_reference)

    report = _run_financial_extraction_evidence_schema_preview()

    assert report["status"] == "failed"
    assert {"message": "unknown_template_evidence_candidate_id:template_candidate:missing"} in report["errors"]
    assert report["would_extract_values"] is False


def test_financial_extraction_evidence_schema_preview_never_calls_network_helpers(monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task131 evidence schema must not fetch, probe, or download documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_financial_extraction_evidence_schema_preview()

    assert report["status"] == "passed"
    assert report["import_executed"] is False
    assert report["paper_trading_called"] is False


def test_document_artifact_retention_preview_has_expected_policy_and_safe_contract(tmp_path: Path) -> None:
    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))
    by_class = {row["artifact_class"]: row for row in report["policy_rows"]}

    assert report["status"] in {"passed", "warning"}
    assert report["policy_row_count"] >= 9
    assert report["disk_snapshot_row_count"] == 6
    assert report["cleanup_plan_row_count"] > 0
    assert report["vds_disk_limit_gb"] == 50
    assert isinstance(report["download_allowed"], bool)
    assert isinstance(report["future_extraction_allowed"], bool)
    assert by_class["raw_report_cache"]["permanent_storage_allowed"] is False
    assert by_class["raw_report_cache"]["backup_allowed"] is False
    assert by_class["raw_report_cache"]["git_allowed"] is False
    assert by_class["debug_quarantine"]["permanent_storage_allowed"] is False
    assert "logs/financial_reports/document_artifacts/raw_cache/" in report["recommended_gitignore_patterns"]
    for key in (
        "cleanup_executed",
        "files_deleted",
        "documents_downloaded",
        "documents_parsed",
        "would_fetch_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[key] is False
    assert all(row["would_delete_files"] is False for row in report["cleanup_plan_rows"])
    assert all("rm " not in row["manual_command_hint"] for row in report["cleanup_plan_rows"])
    assert all("-delete" not in row["manual_command_hint"] for row in report["cleanup_plan_rows"])


def test_document_artifact_retention_preview_low_free_space_blocks_downloads(tmp_path: Path, monkeypatch) -> None:
    class FakeUsage:
        total = 50 * 1024**3
        used = 49 * 1024**3
        free = 1 * 1024**3

    monkeypatch.setattr(assistant.shutil, "disk_usage", lambda path: FakeUsage())

    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))

    assert report["disk_guard_status"] == "blocked"
    assert report["download_allowed"] is False
    assert report["future_extraction_allowed"] is False
    assert "free_disk_below_minimum_gb" in report["guard_reason_codes"]


def test_document_artifact_retention_preview_raw_cache_over_size_blocks_and_previews_cleanup(tmp_path: Path) -> None:
    raw_cache = tmp_path / "artifacts" / "raw_cache"
    raw_cache.mkdir(parents=True)
    (raw_cache / "report.downloaded.pdf").write_bytes(b"x" * 2048)

    report = _run_document_artifact_retention_preview(
        [
            *_document_artifact_retention_test_paths(tmp_path),
            "--document-artifact-raw-cache-max-gb",
            "0.0000001",
        ]
    )

    assert report["disk_guard_status"] == "blocked"
    assert report["download_allowed"] is False
    assert "raw_cache_over_size_limit" in report["guard_reason_codes"]
    raw_cleanup = [row for row in report["cleanup_plan_rows"] if row["path_role"] == "raw_cache"]
    assert raw_cleanup
    assert all(row["would_delete_files"] is False for row in raw_cleanup)


def test_document_artifact_retention_preview_ttl_expired_files_are_warning_only(tmp_path: Path) -> None:
    raw_cache = tmp_path / "artifacts" / "raw_cache"
    raw_cache.mkdir(parents=True)
    old_report = raw_cache / "old.downloaded.pdf"
    old_report.write_bytes(b"old")
    old_timestamp = time.time() - 96 * 60 * 60
    os.utime(old_report, (old_timestamp, old_timestamp))

    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))
    raw_snapshot = next(row for row in report["disk_snapshot_rows"] if row["path_role"] == "raw_cache")

    assert report["disk_guard_status"] == "warning"
    assert report["download_allowed"] is True
    assert "raw_cache_ttl_expired_files_present" in report["guard_reason_codes"]
    assert raw_snapshot["expired_file_count_estimate"] == 1
    assert old_report.is_file()
    assert report["files_deleted"] is False


def test_document_artifact_retention_preview_missing_paths_are_zero_sized(tmp_path: Path) -> None:
    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))
    by_role = {row["path_role"]: row for row in report["disk_snapshot_rows"]}

    assert by_role["raw_cache"]["exists"] is False
    assert by_role["raw_cache"]["size_bytes"] == 0
    assert by_role["raw_cache"]["file_count"] == 0
    assert by_role["debug_quarantine"]["exists"] is False


def test_document_artifact_retention_preview_filesystem_stat_failure_blocks_safely(tmp_path: Path, monkeypatch) -> None:
    def unavailable(path):
        raise OSError("stat unavailable")

    monkeypatch.setattr(assistant.shutil, "disk_usage", unavailable)

    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))

    assert report["status"] == "warning"
    assert report["disk_guard_status"] == "blocked"
    assert report["download_allowed"] is False
    assert {"message": "document_artifact_filesystem_stat_unavailable"} in report["warnings"]
    assert "filesystem_stat_unavailable" in report["guard_reason_codes"]


def test_document_artifact_retention_preview_does_not_follow_symlinks(tmp_path: Path) -> None:
    raw_cache = tmp_path / "artifacts" / "raw_cache"
    raw_cache.mkdir(parents=True)
    external = tmp_path / "external.downloaded.pdf"
    external.write_bytes(b"x" * 4096)
    link = raw_cache / "linked.downloaded.pdf"
    try:
        link.symlink_to(external)
    except OSError:
        return

    report = _run_document_artifact_retention_preview(
        [
            *_document_artifact_retention_test_paths(tmp_path),
            "--document-artifact-raw-cache-max-gb",
            "0",
        ]
    )
    raw_snapshot = next(row for row in report["disk_snapshot_rows"] if row["path_role"] == "raw_cache")

    assert raw_snapshot["file_count"] == 0
    assert raw_snapshot["size_bytes"] == 0
    assert raw_snapshot["over_size_limit"] is False


def test_document_artifact_retention_preview_writes_default_and_explicit_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    markdown = tmp_path / "custom" / "retention.md"
    report = _run_document_artifact_retention_preview(
        [
            *_document_artifact_retention_test_paths(tmp_path),
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--document-artifact-retention-markdown-output",
            str(markdown),
        ]
    )

    assert report["artifacts"]["policy_markdown"] == str(markdown)
    for path in report["artifacts"].values():
        assert Path(path).is_file()
    content = markdown.read_text(encoding="utf-8")
    assert "# Document Artifact Retention Policy" in content
    assert "This task does not download reports." in content
    assert "This task does not delete files." in content


def test_document_artifact_retention_preview_rejects_protected_output_collision(tmp_path: Path) -> None:
    raw_cache = tmp_path / "artifacts" / "raw_cache"
    raw_cache.mkdir(parents=True)
    output = raw_cache / "unsafe.json"

    report = _run_document_artifact_retention_preview(
        [
            *_document_artifact_retention_test_paths(tmp_path),
            "--document-artifact-retention-output",
            str(output),
        ]
    )

    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "document_artifact_retention_output_must_not_equal_protected_path"}]
    assert not output.exists()


def test_document_artifact_retention_preview_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task132 retention preview must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_document_artifact_retention_preview(_document_artifact_retention_test_paths(tmp_path))

    assert report["status"] in {"passed", "warning"}
    assert report["documents_downloaded"] is False
    assert report["documents_parsed"] is False


def test_backup_retention_preview_missing_dir_is_safe_warning(tmp_path: Path) -> None:
    report = _run_backup_retention_preview(["--backup-retention-backups-dir", str(tmp_path / "missing")])

    assert report["status"] == "warning"
    assert report["inventory_row_count"] == 0
    assert report["rotation_plan_row_count"] == 0
    assert {"message": "backup_retention_backups_dir_missing", "path": str(tmp_path / "missing")} in report["warnings"]
    assert report["files_deleted"] is False
    assert report["would_delete_files"] is False


def test_backup_retention_preview_below_threshold_passes(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "bondradar_20260101T000000Z.dump").write_bytes(b"backup")

    report = _run_backup_retention_preview(["--backup-retention-backups-dir", str(backups)])

    assert report["status"] == "passed"
    assert report["recognized_backup_file_count"] == 1
    assert report["recognized_backup_size_bytes"] == 6
    assert report["rotation_candidate_count"] == 0


def test_backup_retention_preview_near_and_over_limit_create_safe_rotation_candidates(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    for index in range(3):
        path = backups / f"bondradar_2026010{index + 1}T000000Z.dump"
        path.write_bytes(b"x" * 1024)
        os.utime(path, (1000 + index, 1000 + index))

    near = _run_backup_retention_preview(
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-size-gb",
            "0.000003",
            "--backup-retention-warning-threshold-percent",
            "90",
            "--backup-retention-keep-latest-count",
            "0",
            "--backup-retention-keep-daily-count",
            "0",
            "--backup-retention-keep-weekly-count",
            "0",
        ]
    )

    assert near["status"] == "warning"
    assert near["at_or_over_warning_threshold"] is True
    assert near["rotation_candidate_count"] == 3
    assert near["estimated_reclaimable_bytes"] == 3072
    assert all(row["rotation_action"] == "candidate_delete_old_backup" for row in near["rotation_plan_rows"])
    assert all(row["would_delete_files"] is False for row in near["rotation_plan_rows"])

    over = _run_backup_retention_preview(
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-size-gb",
            "0.000001",
            "--backup-retention-keep-latest-count",
            "0",
            "--backup-retention-keep-daily-count",
            "0",
            "--backup-retention-keep-weekly-count",
            "0",
        ]
    )
    assert over["over_max_size_limit"] is True
    assert {"message": "backup_retention_max_size_exceeded"} in over["warnings"]


def test_backup_retention_preview_protects_latest_daily_and_weekly_utc_buckets(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    timestamps = [
        datetime(2026, 1, 12, 12, tzinfo=timezone.utc).timestamp(),
        datetime(2026, 1, 12, 8, tzinfo=timezone.utc).timestamp(),
        datetime(2026, 1, 5, 12, tzinfo=timezone.utc).timestamp(),
        datetime(2025, 12, 29, 12, tzinfo=timezone.utc).timestamp(),
    ]
    for index, timestamp in enumerate(timestamps):
        path = backups / f"bondradar_{index}.dump"
        path.write_bytes(b"x" * 1024)
        os.utime(path, (timestamp, timestamp))

    report = _run_backup_retention_preview(
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-size-gb",
            "0.000001",
            "--backup-retention-keep-latest-count",
            "1",
            "--backup-retention-keep-daily-count",
            "1",
            "--backup-retention-keep-weekly-count",
            "2",
        ]
    )
    by_name = {row["file_name"]: row for row in report["inventory_rows"]}

    assert by_name["bondradar_0.dump"]["protected_latest"] is True
    assert by_name["bondradar_0.dump"]["protected_daily"] is True
    assert by_name["bondradar_0.dump"]["protected_weekly"] is True
    assert by_name["bondradar_1.dump"]["protection_reasons"] == []
    assert by_name["bondradar_2.dump"]["protected_weekly"] is True
    assert by_name["bondradar_3.dump"]["rotation_candidate"] is True


def test_backup_retention_preview_unknown_nested_and_symlink_entries_are_diagnostic_only(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "notes.txt").write_text("manual review", encoding="utf-8")
    nested = backups / "nested"
    nested.mkdir()
    (nested / "ignored.dump").write_bytes(b"nested")
    external = tmp_path / "outside.dump"
    external.write_bytes(b"outside")
    link = backups / "linked.dump"
    try:
        link.symlink_to(external)
    except OSError:
        link = None

    report = _run_backup_retention_preview(["--backup-retention-backups-dir", str(backups)])
    actions = {row["rotation_action"] for row in report["rotation_plan_rows"]}

    assert "candidate_manual_review_unknown_file" in actions
    assert report["estimated_reclaimable_bytes"] == 0
    assert report["nested_directory_count"] == 1
    assert {"message": "backup_retention_nested_directory_skipped", "path": str(nested)} in report["warnings"]
    if link is not None:
        link_row = next(row for row in report["inventory_rows"] if row["path"] == str(link))
        assert link_row["is_symlink"] is True
        assert link_row["rotation_candidate"] is False


def test_backup_retention_preview_filesystem_warning_and_projected_minimum_do_not_expand_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "bondradar.dump").write_bytes(b"x")

    class LowUsage:
        total = 50 * 1024**3
        used = 49 * 1024**3
        free = 1 * 1024**3

    monkeypatch.setattr(assistant.shutil, "disk_usage", lambda path: LowUsage())
    report = _run_backup_retention_preview(
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-min-free-gb-after-rotation",
            "10",
        ]
    )

    assert {"message": "backup_retention_projected_free_space_below_minimum"} in report["warnings"]
    assert report["rotation_candidate_count"] == 0

    monkeypatch.setattr(assistant.shutil, "disk_usage", lambda path: (_ for _ in ()).throw(OSError("unavailable")))
    unavailable = _run_backup_retention_preview(["--backup-retention-backups-dir", str(backups)])
    assert {"message": "backup_retention_filesystem_stat_unavailable"} in unavailable["warnings"]


def test_backup_retention_preview_writes_outputs_and_rejects_unsafe_paths(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    existing = backups / "bondradar.dump"
    existing.write_bytes(b"backup")
    outputs = tmp_path / "outputs"
    markdown = tmp_path / "custom" / "backup.md"

    report = _run_backup_retention_preview(
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--operator-resolution-chain-output-dir",
            str(outputs),
            "--backup-retention-markdown-output",
            str(markdown),
        ]
    )
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    assert "# Backup Retention Preview" in markdown.read_text(encoding="utf-8")
    assert "does not delete, move, compress, upload, or restore" in markdown.read_text(encoding="utf-8")

    for extra_args in (
        ["--backup-retention-backups-dir", str(Path.cwd())],
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-output",
            str(backups / "unsafe.json"),
        ],
        [
            "--backup-retention-backups-dir",
            str(backups),
            "--json-output",
            str(existing),
        ],
    ):
        blocked = _run_backup_retention_preview(extra_args)
        assert blocked["status"] == "failed"
        assert blocked["errors"]


def test_backup_retention_preview_rejects_invalid_policy_and_symlink_root(tmp_path: Path) -> None:
    invalid = _run_backup_retention_preview(["--backup-retention-max-size-gb", "-1"])
    assert invalid["status"] == "failed"
    assert {"message": "invalid_backup_retention_policy_value:backup_retention_max_size_gb"} in invalid["errors"]

    backups = tmp_path / "backups"
    backups.mkdir()
    linked = tmp_path / "linked_backups"
    try:
        linked.symlink_to(backups, target_is_directory=True)
    except OSError:
        return
    unsafe = _run_backup_retention_preview(["--backup-retention-backups-dir", str(linked)])
    assert unsafe["status"] == "failed"
    assert {"message": "backup_retention_backups_dir_unsafe"} in unsafe["errors"]


def test_backup_retention_preview_never_calls_network_helpers_and_flags_remain_false(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task138 backup retention preview must remain read-only")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_backup_retention_preview(["--backup-retention-backups-dir", str(tmp_path / "missing")])

    for field in (
        "cleanup_executed",
        "files_deleted",
        "files_moved",
        "files_compressed",
        "files_uploaded",
        "database_mutated",
        "documents_downloaded",
        "documents_parsed",
        "would_delete_files",
        "would_move_files",
        "would_compress_files",
        "would_upload_files",
        "would_mutate_database",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[field] is False


def test_backup_retention_apply_preview_builds_manifest_and_inert_script_without_mutation(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    first = _write_backup_retention_apply_file(backups / "bondradar_old.dump", b"old", timestamp=1000)
    second = _write_backup_retention_apply_file(backups / "bondradar_new.dump", b"newer", timestamp=2000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(first), _backup_retention_apply_rotation_row(second)])
    input_snapshots = {path: path.read_bytes() for path in output_dir.glob("*task138.json")}

    report = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )

    assert report["status"] == "warning"
    assert report["apply_row_count"] == 2
    assert report["eligible_manual_delete_preview_count"] == 2
    assert report["manifest_row_count"] == 2
    assert report["estimated_reclaimable_bytes"] == 8
    assert all(row["would_delete_file"] is False for row in report["apply_rows"])
    assert all(path.read_bytes() == content for path, content in input_snapshots.items())
    assert first.read_bytes() == b"old"
    assert second.read_bytes() == b"newer"
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    script = Path(report["artifacts"]["cleanup_script"]).read_text(encoding="utf-8")
    assert script.index("exit 0") < script.index("# rm --")
    assert "# rm --" in script
    assert not any(line.startswith("rm ") for line in script.splitlines())
    assert "find -delete" not in script
    markdown = Path(report["artifacts"]["apply_markdown"]).read_text(encoding="utf-8")
    assert "# Backup Retention Apply Draft Preview" in markdown
    assert "does not delete backup files" in markdown


def test_backup_retention_apply_preview_skips_missing_and_drifted_files(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    missing = backups / "missing.dump"
    size_drift = _write_backup_retention_apply_file(backups / "size.dump", b"size", timestamp=1000)
    mtime_drift = _write_backup_retention_apply_file(backups / "mtime.dump", b"mtime", timestamp=2000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(
        output_dir,
        [
            _backup_retention_apply_rotation_row(missing, size_bytes=1, mtime_utc="2026-01-01T00:00:00Z"),
            _backup_retention_apply_rotation_row(size_drift, size_bytes=999),
            _backup_retention_apply_rotation_row(mtime_drift, mtime_utc="2026-01-01T00:00:00Z"),
        ],
    )

    report = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    statuses = {row["file_name"]: row for row in report["apply_rows"]}

    assert statuses["missing.dump"]["apply_status"] == "skipped_file_missing"
    assert statuses["missing.dump"]["apply_reason_codes"] == ["file_missing"]
    assert statuses["size.dump"]["apply_reason_codes"] == ["file_size_drift"]
    assert statuses["mtime.dump"]["apply_reason_codes"] == ["file_mtime_drift"]
    assert report["manifest_row_count"] == 0


def test_backup_retention_apply_preview_blocks_symlink_outside_nested_unknown_and_protected_rows(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    outside = _write_backup_retention_apply_file(tmp_path / "outside.dump", b"outside", timestamp=1000)
    nested_dir = backups / "nested"
    nested_dir.mkdir()
    nested = _write_backup_retention_apply_file(nested_dir / "nested.dump", b"nested", timestamp=1100)
    unknown = _write_backup_retention_apply_file(backups / "notes.txt", b"notes", timestamp=1200)
    protected = _write_backup_retention_apply_file(backups / "protected.dump", b"protected", timestamp=1300)
    external = _write_backup_retention_apply_file(tmp_path / "external.dump", b"external", timestamp=1400)
    linked = backups / "linked.dump"
    try:
        linked.symlink_to(external)
    except OSError:
        linked = None
    rows = [
        _backup_retention_apply_rotation_row(outside),
        _backup_retention_apply_rotation_row(nested),
        _backup_retention_apply_rotation_row(
            unknown,
            rotation_action="candidate_manual_review_unknown_file",
            recognized_backup=False,
        ),
        _backup_retention_apply_rotation_row(protected, protection_reasons=["keep_latest"]),
    ]
    if linked is not None:
        rows.append(_backup_retention_apply_rotation_row(linked))
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, rows)

    report = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    statuses = {row["file_name"]: row["apply_status"] for row in report["apply_rows"]}

    assert statuses["outside.dump"] == "blocked_unsafe_path"
    assert statuses["nested.dump"] == "blocked_unsafe_path"
    assert statuses["notes.txt"] == "skipped_unknown_file_manual_review"
    assert statuses["protected.dump"] == "blocked_protected_by_policy"
    if linked is not None:
        assert statuses["linked.dump"] == "blocked_symlink"
    assert report["manifest_row_count"] == 0


def test_backup_retention_apply_preview_limits_oldest_first_and_preview_status_gate(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    files = [
        _write_backup_retention_apply_file(backups / f"{name}.dump", b"x" * 10, timestamp=timestamp)
        for name, timestamp in (("oldest", 1000), ("middle", 2000), ("newest", 3000))
    ]
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path) for path in reversed(files)])

    count_limited = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-delete-count",
            "1",
        ]
    )
    by_name = {row["file_name"]: row["apply_status"] for row in count_limited["apply_rows"]}
    assert by_name["oldest.dump"] == "eligible_manual_delete_preview"
    assert by_name["middle.dump"] == "blocked_delete_limit_exceeded"
    assert by_name["newest.dump"] == "blocked_delete_limit_exceeded"

    size_limited = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-delete-gb",
            str(15 / 1024**3),
        ]
    )
    by_name = {row["file_name"]: row["apply_status"] for row in size_limited["apply_rows"]}
    assert by_name["oldest.dump"] == "eligible_manual_delete_preview"
    assert by_name["middle.dump"] == "blocked_reclaim_limit_exceeded"

    _write_backup_retention_apply_inputs(
        output_dir,
        [_backup_retention_apply_rotation_row(files[0])],
        preview_status="warning",
    )
    status_blocked = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-require-preview-status",
            "passed",
        ]
    )
    assert status_blocked["apply_rows"][0]["apply_status"] == "blocked_preview_status_not_allowed"


def test_backup_retention_apply_preview_passed_is_safer_and_manual_review_flag_is_metadata_only(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path)], preview_status="passed")

    report = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-require-manual-review",
            "false",
        ]
    )

    assert report["status"] == "passed"
    assert report["manifest_row_count"] == 1
    assert report["apply_rows"][0]["manual_review_status"] == "not_required_by_configuration_preview_only"
    assert report["apply_rows"][0]["cleanup_script_line_enabled"] is False


def test_backup_retention_apply_preview_rejects_malformed_inputs_and_output_collisions(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path)])
    rotation = output_dir / "backup_retention_rotation_plan_task138.json"

    collision = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-apply-output",
            str(rotation),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "backup_retention_apply_output_must_not_equal_input"}]

    generic_collision = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--json-output",
            str(path),
        ]
    )
    assert generic_collision["status"] == "failed"
    assert generic_collision["errors"] == [{"message": "backup_retention_apply_output_must_not_equal_input"}]

    outside = _write_backup_retention_apply_file(tmp_path / "outside.dump", b"outside", timestamp=1000)
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(outside)])
    generic_args = assistant.parse_args(
        [
            "--mode",
            "backup-retention-apply-draft-preview",
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--json-output",
            str(outside),
        ]
    )
    outside_collision, _ = assistant.run_assistant(generic_args)
    assert outside_collision["status"] == "failed"
    assert assistant._generic_report_output_is_safe(generic_args, outside) is False
    assert outside.read_bytes() == b"outside"

    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path)])
    rotation.write_text("{", encoding="utf-8")
    malformed = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    assert malformed["status"] == "failed"
    assert {"message": "backup_retention_apply_preview_input_invalid", "path": str(rotation)} in malformed["errors"]

    missing = _run_backup_retention_apply_preview(["--backup-retention-backups-dir", str(backups)])
    assert missing["status"] == "failed"
    assert {"message": "backup_retention_apply_preview_input_required"} in missing["errors"]


def test_backup_retention_apply_preview_optional_inventory_warning_and_no_network_calls(tmp_path: Path, monkeypatch) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path)], include_inventory=False)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task139 backup apply preview must remain read-only")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )

    assert {"message": "backup_retention_apply_inventory_input_missing"} in report["warnings"]
    for field in (
        "cleanup_executed",
        "files_deleted",
        "files_moved",
        "files_compressed",
        "files_uploaded",
        "database_mutated",
        "documents_downloaded",
        "documents_parsed",
        "would_delete_files",
        "would_move_files",
        "would_compress_files",
        "would_upload_files",
        "would_mutate_database",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[field] is False

    _write_backup_retention_apply_inputs(output_dir, [_backup_retention_apply_rotation_row(path)])
    inventory = output_dir / "backup_retention_inventory_task138.json"
    inventory.write_text("{", encoding="utf-8")
    malformed_inventory = _run_backup_retention_apply_preview(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    assert {
        "message": "backup_retention_apply_inventory_input_unreadable",
        "path": str(inventory),
    } in malformed_inventory["warnings"]


def test_backup_retention_controlled_apply_dry_run_writes_ledger_and_snapshot_without_deletion(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    first = _write_backup_retention_apply_file(backups / "old.dump", b"old", timestamp=1000)
    second = _write_backup_retention_apply_file(backups / "new.dump", b"newer", timestamp=2000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_controlled_apply_inputs(output_dir, [first, second])

    report, exit_code = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["execute_requested"] is False
    assert report["deletion_execution_enabled"] is False
    assert report["controlled_apply_row_count"] == 2
    assert report["dry_run_eligible_count"] == 2
    assert report["deleted_count"] == 0
    assert report["cleanup_executed"] is False
    assert report["files_deleted"] is False
    assert report["would_delete_files"] is True
    assert {row["ledger_status"] for row in report["deletion_ledger_rows"]} == {"dry_run_noop"}
    assert first.read_bytes() == b"old"
    assert second.read_bytes() == b"newer"
    assert {row["file_name"] for row in report["post_apply_snapshot_rows"]} == {"old.dump", "new.dump"}
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    markdown = Path(report["artifacts"]["controlled_apply_markdown"]).read_text(encoding="utf-8")
    assert "# Backup Retention Controlled Apply" in markdown
    assert "Dry-run mode does not delete backup files." in markdown


def test_backup_retention_controlled_apply_execute_requires_exact_token_and_manifest_hash(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_controlled_apply_inputs(output_dir, [path])
    sha256, token = _backup_retention_controlled_apply_confirmation(manifest)

    missing, missing_exit = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
        ]
    )
    assert missing_exit == 1
    assert missing["status"] == "blocked"
    assert missing["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_confirmation_required"
    assert path.is_file()

    wrong_token, wrong_token_exit = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
            "--backup-retention-confirmation-token",
            "wrong",
            "--backup-retention-expected-manifest-sha256",
            sha256,
        ]
    )
    assert wrong_token_exit == 1
    assert wrong_token["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_confirmation_token_mismatch"
    assert path.is_file()

    wrong_hash, wrong_hash_exit = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
            "--backup-retention-confirmation-token",
            token,
            "--backup-retention-expected-manifest-sha256",
            "0" * 64,
        ]
    )
    assert wrong_hash_exit == 1
    assert wrong_hash["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_manifest_hash_mismatch"
    assert path.is_file()


def test_backup_retention_controlled_apply_execute_deletes_only_guarded_tmp_files(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    first = _write_backup_retention_apply_file(backups / "old.dump", b"old", timestamp=1000)
    second = _write_backup_retention_apply_file(backups / "new.sql.gz", b"newer", timestamp=2000)
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_controlled_apply_inputs(output_dir, [first, second])
    sha256, token = _backup_retention_controlled_apply_confirmation(manifest)
    input_snapshots = {path: path.read_bytes() for path in output_dir.glob("*task139.json")}

    report, exit_code = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
            "--backup-retention-confirmation-token",
            token,
            "--backup-retention-expected-manifest-sha256",
            sha256,
        ]
    )

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["deletion_execution_enabled"] is True
    assert report["deleted_count"] == 2
    assert report["actual_reclaimed_bytes"] == 8
    assert report["cleanup_executed"] is True
    assert report["files_deleted"] is True
    assert not first.exists()
    assert not second.exists()
    assert {row["ledger_status"] for row in report["deletion_ledger_rows"]} == {"deleted"}
    assert report["post_apply_snapshot_rows"] == []
    assert all(path.read_bytes() == content for path, content in input_snapshots.items())


def test_backup_retention_controlled_apply_blocks_drift_symlink_outside_and_limits_oldest_first(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    oldest = _write_backup_retention_apply_file(backups / "oldest.dump", b"oldest", timestamp=1000)
    middle = _write_backup_retention_apply_file(backups / "middle.dump", b"middle", timestamp=2000)
    newest = _write_backup_retention_apply_file(backups / "newest.dump", b"newest", timestamp=3000)
    outside = _write_backup_retention_apply_file(tmp_path / "outside.dump", b"outside", timestamp=4000)
    missing = backups / "missing.dump"
    external = _write_backup_retention_apply_file(tmp_path / "external.dump", b"external", timestamp=5000)
    linked = backups / "linked.dump"
    try:
        linked.symlink_to(external)
    except OSError:
        linked = None
    output_dir = tmp_path / "reports"
    rows = [
        _backup_retention_controlled_apply_manifest_row(newest),
        _backup_retention_controlled_apply_manifest_row(middle),
        _backup_retention_controlled_apply_manifest_row(oldest),
        _backup_retention_controlled_apply_manifest_row(outside),
        _backup_retention_controlled_apply_manifest_row(missing, size_bytes=1, mtime_utc="2026-01-01T00:00:00Z"),
    ]
    if linked is not None:
        rows.append(_backup_retention_controlled_apply_manifest_row(linked))
    _write_backup_retention_controlled_apply_manifest(output_dir, rows)

    report, _ = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-delete-count",
            "1",
        ]
    )
    statuses = {row["file_name"]: row["controlled_apply_status"] for row in report["controlled_apply_rows"]}

    assert statuses["oldest.dump"] == "dry_run_eligible_for_delete"
    assert statuses["middle.dump"] == "blocked_delete_count_limit_exceeded"
    assert statuses["newest.dump"] == "blocked_delete_count_limit_exceeded"
    assert statuses["outside.dump"] == "blocked_unsafe_path"
    assert statuses["missing.dump"] == "blocked_file_missing"
    if linked is not None:
        assert statuses["linked.dump"] == "blocked_symlink"

    drifted = _write_backup_retention_apply_file(backups / "drift.dump", b"before", timestamp=6000)
    _write_backup_retention_controlled_apply_manifest(
        output_dir,
        [_backup_retention_controlled_apply_manifest_row(drifted)],
    )
    drifted.write_bytes(b"after-size-change")
    drift_report, _ = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    assert drift_report["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_file_drift_detected"


def test_backup_retention_controlled_apply_reclaim_limit_manual_review_and_optional_warnings(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    first = _write_backup_retention_apply_file(backups / "first.dump", b"1234567890", timestamp=1000)
    second = _write_backup_retention_apply_file(backups / "second.dump", b"1234567890", timestamp=2000)
    output_dir = tmp_path / "reports"
    _write_backup_retention_controlled_apply_manifest(
        output_dir,
        [
            _backup_retention_controlled_apply_manifest_row(first),
            _backup_retention_controlled_apply_manifest_row(second, manual_review_required=False),
        ],
    )

    report, _ = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-max-delete-gb",
            str(5 / 1024**3),
        ]
    )
    statuses = {row["file_name"]: row["controlled_apply_status"] for row in report["controlled_apply_rows"]}

    assert statuses["first.dump"] == "blocked_reclaim_limit_exceeded"
    assert statuses["second.dump"] == "blocked_manual_review_required"
    assert {"message": "backup_retention_controlled_apply_optional_input_missing:apply_preview"} in report["warnings"]
    assert {"message": "backup_retention_controlled_apply_optional_input_missing:apply_blockers"} in report["warnings"]
    assert first.is_file()
    assert second.is_file()


def test_backup_retention_controlled_apply_aborts_after_delete_exception_and_persists_pending_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    first = _write_backup_retention_apply_file(backups / "first.dump", b"first", timestamp=1000)
    second = _write_backup_retention_apply_file(backups / "second.dump", b"second", timestamp=2000)
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_controlled_apply_inputs(output_dir, [first, second])
    sha256, token = _backup_retention_controlled_apply_confirmation(manifest)
    ledger_path = output_dir / "backup_retention_deletion_ledger_task140.json"

    def fail_after_ledger(*args, **kwargs):
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert payload["deletion_ledger_rows"][0]["ledger_status"] == "delete_attempt_pending"
        raise OSError("simulated guarded delete failure")

    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", fail_after_ledger)
    report, exit_code = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
            "--backup-retention-confirmation-token",
            token,
            "--backup-retention-expected-manifest-sha256",
            sha256,
        ]
    )

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["controlled_apply_rows"][0]["controlled_apply_status"] == "failed_delete_exception"
    assert report["controlled_apply_rows"][1]["controlled_apply_status"] == "blocked_execution_aborted_after_delete_exception"
    assert first.is_file()
    assert second.is_file()


def test_backup_retention_controlled_apply_detects_manifest_drift_before_unlink(tmp_path: Path, monkeypatch) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_controlled_apply_inputs(output_dir, [path])
    sha256, token = _backup_retention_controlled_apply_confirmation(manifest)
    original_persist = assistant._persist_backup_retention_controlled_apply_ledger
    persist_calls = 0

    def persist_then_mutate(*args, **kwargs):
        nonlocal persist_calls
        original_persist(*args, **kwargs)
        persist_calls += 1
        if persist_calls == 1:
            manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    monkeypatch.setattr(assistant, "_persist_backup_retention_controlled_apply_ledger", persist_then_mutate)
    report, exit_code = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-execute",
            "true",
            "--backup-retention-confirmation-token",
            token,
            "--backup-retention-expected-manifest-sha256",
            sha256,
        ]
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_file_drift_detected"
    assert {"message": "backup_retention_controlled_apply_input_changed"} in report["errors"]
    assert path.is_file()


def test_backup_retention_controlled_apply_rejects_manifest_mutation_collision_and_unrelated_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    path = _write_backup_retention_apply_file(backups / "safe.dump", b"safe", timestamp=1000)
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_controlled_apply_inputs(output_dir, [path])

    collision, collision_exit = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--backup-retention-controlled-apply-output",
            str(manifest),
        ]
    )
    assert collision_exit == 1
    assert collision["errors"] == [{"message": "backup_retention_controlled_apply_output_must_not_equal_input"}]

    generic_collision, generic_collision_exit = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
            "--json-output",
            str(path),
        ]
    )
    assert generic_collision_exit == 1
    assert generic_collision["errors"] == [{"message": "backup_retention_controlled_apply_output_must_not_equal_input"}]
    assert path.read_bytes() == b"safe"

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task140 must not call unrelated network or document helpers")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report, _ = _run_backup_retention_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(backups),
        ]
    )
    for field in (
        "files_moved",
        "files_compressed",
        "files_uploaded",
        "database_mutated",
        "documents_downloaded",
        "documents_parsed",
        "would_move_files",
        "would_compress_files",
        "would_upload_files",
        "would_mutate_database",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[field] is False


def test_backup_retention_execute_readiness_ready_for_operator_review_from_task140_dry_run(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "reports"
    _write_backup_retention_execute_readiness_inputs(output_dir, eligible_count=25, limit_blocked_count=53)

    report = _run_backup_retention_execute_readiness(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-backups-dir",
            str(tmp_path / "backups"),
        ]
    )

    assert report["readiness_status"] == "ready_for_operator_execute_review"
    assert report["ready_for_operator_execute_review"] is True
    assert report["execute_allowed_by_board"] is False
    assert report["operator_manual_approval_required"] is True
    assert report["dry_run_eligible_count"] == 25
    assert report["expected_limit_blocker_count"] == 53
    assert report["unsafe_blocker_count"] == 0
    assert "backup-retention-controlled-apply" in report["future_execute_command"]
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    markdown = Path(report["artifacts"]["readiness_markdown"]).read_text(encoding="utf-8")
    assert "# Backup Retention Execute Readiness Board" in markdown
    assert "DO NOT RUN UNTIL MANUAL APPROVAL" in markdown
    assert "This task does not delete backup files." in markdown


def test_backup_retention_execute_readiness_missing_required_inputs_fail_safely(tmp_path: Path) -> None:
    missing_task140 = _run_backup_retention_execute_readiness(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path / "missing"),
        ]
    )
    assert missing_task140["status"] == "failed"
    assert {"message": "backup_retention_execute_readiness_task140_input_required"} in missing_task140["errors"]
    assert {"message": "backup_retention_execute_readiness_manifest_input_required"} in missing_task140["errors"]

    output_dir = tmp_path / "reports"
    _write_backup_retention_execute_readiness_inputs(output_dir)
    (output_dir / "backup_retention_cleanup_manifest_task139.json").unlink()
    missing_manifest = _run_backup_retention_execute_readiness(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
        ]
    )
    assert missing_manifest["status"] == "failed"
    assert {"message": "backup_retention_execute_readiness_manifest_input_required"} in missing_manifest["errors"]


def test_backup_retention_execute_readiness_blocks_execute_or_deleted_task140_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports"
    _write_backup_retention_execute_readiness_inputs(
        output_dir,
        task140_updates={"execute_requested": True, "dry_run_only": False},
    )
    execute_report = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert execute_report["readiness_status"] == "blocked_task140_not_dry_run"
    assert execute_report["ready_for_operator_execute_review"] is False

    _write_backup_retention_execute_readiness_inputs(
        output_dir,
        task140_updates={
            "files_deleted": True,
            "deleted_count": 1,
            "actual_reclaimed_bytes": 10,
        },
        ledger_updates={"did_delete_file": True, "ledger_status": "deleted"},
    )
    deleted_report = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert deleted_report["readiness_status"] == "blocked_task140_deleted_files"
    assert deleted_report["ready_for_operator_execute_review"] is False


def test_backup_retention_execute_readiness_blocks_missing_hash_token_and_unsafe_blockers(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports"
    _write_backup_retention_execute_readiness_inputs(output_dir, omit_hash=True)
    missing_hash = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert missing_hash["readiness_status"] == "blocked_manifest_hash_missing"

    _write_backup_retention_execute_readiness_inputs(output_dir, omit_token=True)
    missing_token = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert missing_token["readiness_status"] == "blocked_token_missing"

    _write_backup_retention_execute_readiness_inputs(
        output_dir,
        unsafe_blocker_code="unsafe_path",
        unsafe_status="blocked_unsafe_path",
    )
    unsafe = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert unsafe["readiness_status"] == "blocked_unsafe_blockers_present"
    assert unsafe["unsafe_blocker_count"] == 1


def test_backup_retention_execute_readiness_blocks_limit_mismatch_and_no_eligible_rows(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports"
    _write_backup_retention_execute_readiness_inputs(output_dir, eligible_count=25)
    mismatch = _run_backup_retention_execute_readiness(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-proposed-max-delete-count",
            "10",
        ]
    )
    assert mismatch["readiness_status"] == "blocked_delete_limits_mismatch"

    _write_backup_retention_execute_readiness_inputs(output_dir, eligible_count=0, limit_blocked_count=0)
    no_eligible = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert no_eligible["readiness_status"] == "blocked_no_dry_run_eligible_rows"


def test_backup_retention_execute_readiness_output_collision_optional_warnings_and_no_mutation_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = tmp_path / "reports"
    manifest = _write_backup_retention_execute_readiness_inputs(output_dir, include_optional=False)

    collision = _run_backup_retention_execute_readiness(
        [
            "--operator-resolution-chain-output-dir",
            str(output_dir),
            "--backup-retention-execute-readiness-output",
            str(manifest),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "backup_retention_execute_readiness_output_must_not_equal_input"}]

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task141 readiness board must remain advisory")

    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)

    report = _run_backup_retention_execute_readiness(["--operator-resolution-chain-output-dir", str(output_dir)])
    assert {"message": "backup_retention_execute_readiness_optional_input_missing:task140_deletion_ledger"} in report["warnings"]
    assert report["readiness_status"] in {"blocked_execute_not_ready", "blocked_backup_pressure_missing"}
    for field in (
        "cleanup_executed",
        "files_deleted",
        "files_moved",
        "files_compressed",
        "files_uploaded",
        "database_mutated",
        "documents_downloaded",
        "documents_parsed",
        "would_delete_files",
        "would_move_files",
        "would_compress_files",
        "would_upload_files",
        "would_mutate_database",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[field] is False


def test_financial_document_fetch_plan_warns_without_candidates_and_uses_safe_retention_fallback() -> None:
    report = _run_financial_document_fetch_plan_preview()

    assert report["status"] == "warning"
    assert report["fetch_plan_row_count"] == 0
    assert report["disk_guard_status"] == "blocked"
    assert {"message": "document_artifact_retention_policy_missing_using_safe_defaults"} in report["warnings"]
    assert {"message": "financial_document_fetch_plan_no_candidate_rows"} in report["warnings"]


def test_financial_document_fetch_plan_strict_ready_row_is_manifest_eligible(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    summary = tmp_path / "draft_gate_summary.json"
    url = "https://mostotrest.ru/reports/annual-ifrs-2025.pdf"
    _write_financial_document_fetch_summary(summary, [_financial_document_fetch_summary_row(draft_document_url=url)])
    output_dir = tmp_path / "outputs"

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
            "--operator-resolution-chain-output-dir",
            str(output_dir),
        ]
    )
    row = report["fetch_plan_rows"][0]

    assert report["fetch_plan_eligible_count"] == 1
    assert report["download_attempt_allowed_now_count"] == 1
    assert row["fetch_plan_status"] == "eligible_for_future_controlled_download"
    assert row["download_attempt_allowed_now"] is True
    assert row["future_pre_write_size_check_required"] is True
    assert row["source_document_url_sha256"] == assistant.hashlib.sha256(url.encode("utf-8")).hexdigest()
    assert row["planned_raw_document_path"].endswith(f"{row['source_document_url_sha256']}.downloaded.pdf")
    assert not (tmp_path / "artifacts" / "raw_cache" / "67").exists()
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    markdown = Path(report["artifacts"]["fetch_plan_markdown"]).read_text(encoding="utf-8")
    assert "# Financial Document Fetch Plan" in markdown
    assert "This task does not download reports." in markdown


def test_financial_document_fetch_plan_disk_block_overrides_strict_ready_row(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path, disk_guard_status="blocked")
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(summary, [_financial_document_fetch_summary_row()])

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
        ]
    )

    assert report["fetch_plan_rows"][0]["fetch_plan_status"] == "blocked_disk_guard"
    assert report["download_attempt_allowed_now_count"] == 0


def test_financial_document_fetch_plan_disk_warning_keeps_future_eligibility_but_blocks_attempt_now(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path, disk_guard_status="warning")
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(summary, [_financial_document_fetch_summary_row()])

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
        ]
    )
    row = report["fetch_plan_rows"][0]

    assert row["fetch_plan_status"] == "eligible_for_future_controlled_download"
    assert row["ready_for_future_download"] is True
    assert row["download_attempt_allowed_now"] is False


def test_financial_document_fetch_plan_vds_like_board_rows_need_exact_urls(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    board = tmp_path / "board.json"
    _write_financial_document_fetch_board(
        board,
        [
            _financial_document_fetch_board_row(company_id="18", company_name="RZD"),
            _financial_document_fetch_board_row(company_id="67", company_name="Mostotrest"),
        ],
    )

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--operator-resolution-chain-review-board-input",
            str(board),
        ]
    )

    assert report["fetch_plan_row_count"] == 2
    assert report["fetch_plan_eligible_count"] == 0
    assert report["fetch_plan_status_counts"] == {"blocked_missing_exact_document_url": 2}


def test_financial_document_fetch_plan_blocks_historical_fallback_and_known_oversize(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(
        summary,
        [
            _financial_document_fetch_summary_row(
                company_id="18",
                draft_document_url="https://rzd.ru/reports/history/annual-ifrs-2024.pdf",
                draft_fallback_status="historical_fallback",
            ),
            _financial_document_fetch_summary_row(
                company_id="67",
                source_document_size_bytes_expected=300 * 1024**2,
            ),
        ],
    )

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
        ]
    )
    rows = {str(row["company_id"]): row for row in report["fetch_plan_rows"]}

    assert rows["18"]["fetch_plan_status"] == "blocked_historical_fallback_only"
    assert rows["67"]["fetch_plan_status"] == "blocked_single_file_size_exceeded"


def test_financial_document_fetch_plan_blocks_strict_mismatches_and_non_consolidated_board_fallback(
    tmp_path: Path,
) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(
        summary,
        [
            _financial_document_fetch_summary_row(
                company_id="18",
                canonical_company_id="18",
                target_reporting_period="2024",
            ),
            _financial_document_fetch_summary_row(
                company_id="19",
                canonical_company_id="19",
                required_report_type="quarterly",
            ),
            _financial_document_fetch_summary_row(
                company_id="20",
                canonical_company_id="20",
                required_standard="RAS",
            ),
        ],
    )

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
        ]
    )
    rows = {str(row["company_id"]): row for row in report["fetch_plan_rows"]}

    assert rows["18"]["fetch_plan_status"] == "blocked_wrong_period"
    assert rows["19"]["fetch_plan_status"] == "blocked_wrong_report_type"
    assert rows["20"]["fetch_plan_status"] == "blocked_wrong_accounting_standard"

    board = tmp_path / "board.json"
    _write_financial_document_fetch_board(
        board,
        [
            _financial_document_fetch_board_row(
                overall_status="ready_for_future_extraction_preview",
                draft_gate_status="draft_ready_for_future_extraction_preview",
                ready_for_value_extraction=True,
                operator_fill_exact_document_url="https://mostotrest.ru/reports/annual-ifrs-2025.pdf",
                source_document_consolidated=False,
            )
        ],
    )
    board_report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--operator-resolution-chain-review-board-input",
            str(board),
        ]
    )

    assert board_report["fetch_plan_rows"][0]["fetch_plan_status"] == "blocked_non_consolidated"


def test_financial_document_fetch_plan_caps_sorted_eligible_rows(tmp_path: Path) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(
        summary,
        [
            _financial_document_fetch_summary_row(company_id="67", draft_document_url="https://mostotrest.ru/reports/67.pdf"),
            _financial_document_fetch_summary_row(
                company_id="18",
                canonical_company_id="18",
                draft_document_url="https://rzd.ru/reports/18.pdf",
            ),
        ],
    )

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
            "--financial-document-fetch-max-planned-downloads",
            "1",
        ]
    )
    rows = {str(row["company_id"]): row for row in report["fetch_plan_rows"]}

    assert rows["18"]["fetch_plan_status"] == "eligible_for_future_controlled_download"
    assert rows["67"]["fetch_plan_status"] == "blocked_max_planned_downloads_exceeded"


def test_financial_document_fetch_plan_rejects_malformed_retention_and_output_collision(tmp_path: Path) -> None:
    malformed = tmp_path / "retention.json"
    malformed.write_text("{", encoding="utf-8")
    report = _run_financial_document_fetch_plan_preview(
        ["--document-artifact-retention-input", str(malformed)]
    )
    assert report["status"] == "failed"
    assert report["errors"][0]["message"] == "document_artifact_retention_input_invalid"

    retention = _write_financial_document_fetch_retention(tmp_path)
    collision = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--financial-document-fetch-plan-output",
            str(retention),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "financial_document_fetch_plan_output_must_not_equal_input"}]

    generic_collision = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--json-output",
            str(retention),
        ]
    )
    assert generic_collision["status"] == "failed"
    assert generic_collision["errors"] == [{"message": "financial_document_fetch_plan_output_must_not_equal_input"}]

    unsafe_raw_output = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--financial-document-fetch-plan-output",
            str(tmp_path / "artifacts" / "raw_cache" / "unsafe.json"),
        ]
    )
    assert unsafe_raw_output["status"] == "failed"
    assert unsafe_raw_output["errors"] == [{"message": "financial_document_fetch_plan_output_must_not_equal_input"}]


def test_financial_document_fetch_plan_prefers_task124_summary_and_falls_back_after_malformed_optional(
    tmp_path: Path,
) -> None:
    retention = _write_financial_document_fetch_retention(tmp_path)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    chain_summary = output_dir / "document_intake_draft_gate_summary_chain_task124.json"
    chain_summary.write_text("{", encoding="utf-8")
    direct_summary = output_dir / "document_intake_draft_gate_summary_task122.json"
    _write_financial_document_fetch_summary(
        direct_summary,
        [_financial_document_fetch_summary_row(company_id="18", company_name="Fallback issuer")],
    )

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--operator-resolution-chain-output-dir",
            str(output_dir),
        ]
    )

    assert report["fetch_plan_rows"][0]["company_name"] == "Fallback issuer"
    assert any(
        warning["message"] == "financial_document_fetch_optional_artifact_unreadable:document_intake_draft_gate_summary"
        for warning in report["warnings"]
    )


def test_financial_document_fetch_plan_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task133 fetch plan preview must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    retention = _write_financial_document_fetch_retention(tmp_path)
    summary = tmp_path / "draft_gate_summary.json"
    _write_financial_document_fetch_summary(summary, [_financial_document_fetch_summary_row()])

    report = _run_financial_document_fetch_plan_preview(
        [
            "--document-artifact-retention-input",
            str(retention),
            "--document-intake-draft-gate-summary-input",
            str(summary),
        ]
    )

    assert report["fetch_plan_eligible_count"] == 1
    for key in (
        "documents_downloaded",
        "documents_parsed",
        "would_fetch_documents",
        "would_create_download_directories",
        "would_write_raw_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
        "files_deleted",
    ):
        assert report[key] is False


def test_operator_exact_document_refill_workspace_splits_vds_like_rows_and_writes_artifacts(tmp_path: Path) -> None:
    fetch_plan = tmp_path / "financial_document_fetch_plan_task133.json"
    source_trust = tmp_path / "operator_resolution_source_trust_workspace_task126.json"
    _write_operator_exact_document_refill_fetch_plan(
        fetch_plan,
        [
            _operator_exact_document_refill_fetch_row(company_id="18", company_name="RZD"),
            _operator_exact_document_refill_fetch_row(company_id="67", company_name="Mostotrest"),
        ],
    )
    _write_operator_exact_document_refill_rows(
        source_trust,
        "operator-resolution-source-trust-workspace",
        [
            _operator_exact_document_refill_source_trust_row(
                company_id="18",
                company_name="RZD",
                source_trust_status="trusted_source_missing",
                trusted_source_hosts=[],
            ),
            _operator_exact_document_refill_source_trust_row(),
        ],
    )

    report = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-source-trust-workspace-input",
            str(source_trust),
        ]
    )
    rows = {str(row["company_id"]): row for row in report["rows"]}

    assert report["row_count"] == 2
    assert report["ready_for_exact_document_url_refill_count"] == 1
    assert report["blocked_source_trust_count"] == 1
    assert rows["18"]["workspace_status"] == "blocked_source_trust_required_before_exact_document_refill"
    assert rows["18"]["workspace_action"] == "fill_official_baseline_source_page_first"
    assert rows["67"]["workspace_status"] == "ready_for_exact_document_url_refill"
    assert rows["67"]["workspace_action"] == "fill_exact_target_annual_ifrs_document_url"
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    template = json.loads(Path(report["artifacts"]["template_json"]).read_text(encoding="utf-8"))
    assert len(template["template_rows"]) == 2
    assert "operator_fill_exact_document_url" in template["template_rows"][0]
    markdown = Path(report["artifacts"]["workspace_markdown"]).read_text(encoding="utf-8")
    assert "# Operator Exact Document URL Refill Workspace v2" in markdown
    assert "This task does not download reports." in markdown
    rerun = Path(report["artifacts"]["rerun_markdown"]).read_text(encoding="utf-8")
    assert "--mode operator-exact-document-refill-validate-v2" in rerun


def test_operator_exact_document_refill_workspace_blocks_unknown_without_source_context(tmp_path: Path) -> None:
    fetch_plan = tmp_path / "fetch.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])

    report = _run_operator_exact_document_refill_workspace(
        ["--financial-document-fetch-plan-input", str(fetch_plan)]
    )

    assert report["rows"][0]["workspace_status"] == "blocked_unknown_readiness"
    assert report["rows"][0]["workspace_action"] == "review_fetch_plan_blocker_first"
    assert any(
        warning["message"] == "operator_exact_document_refill_optional_artifact_missing:source_trust_workspace"
        for warning in report["warnings"]
    )


def test_operator_exact_document_refill_workspace_resolves_output_dir_defaults_and_warns_for_malformed_optional(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "task124_chain_preview"
    fetch_plan = output_dir / "financial_document_fetch_plan_task133.json"
    source_trust = output_dir / "operator_resolution_source_trust_workspace_task126.json"
    malformed_blockers = output_dir / "financial_document_fetch_blockers_task133.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    _write_operator_exact_document_refill_rows(
        source_trust,
        "operator-resolution-source-trust-workspace",
        [_operator_exact_document_refill_source_trust_row()],
    )
    malformed_blockers.write_text("{", encoding="utf-8")

    report = _run_operator_exact_document_refill_workspace(
        ["--operator-resolution-chain-output-dir", str(output_dir)]
    )

    assert report["rows"][0]["workspace_status"] == "ready_for_exact_document_url_refill"
    assert any(
        warning["message"] == "operator_exact_document_refill_optional_artifact_unreadable:fetch_blockers"
        for warning in report["warnings"]
    )
    assert Path(report["artifacts"]["workspace_json"]) == output_dir / "operator_exact_document_refill_workspace_task134.json"
    assert all(Path(path).is_file() for path in report["artifacts"].values())


def test_operator_exact_document_refill_workspace_blocks_historical_only_and_disk_guard(tmp_path: Path) -> None:
    historical = tmp_path / "historical.json"
    _write_operator_exact_document_refill_fetch_plan(
        historical,
        [
            _operator_exact_document_refill_fetch_row(
                historical_fallback_url="https://mostotrest.ru/reports/history/annual-ifrs-2024.pdf",
            )
        ],
    )
    historical_report = _run_operator_exact_document_refill_workspace(
        ["--financial-document-fetch-plan-input", str(historical)]
    )
    historical_row = historical_report["rows"][0]

    assert historical_row["workspace_status"] == "blocked_historical_fallback_only"
    assert historical_row["workspace_action"] == "do_not_copy_historical_fallback"
    assert "historical_fallback_diagnostic_only" in historical_row["workspace_reason_codes"]
    assert historical_row["historical_fallback_allowed_as_target_evidence"] is False
    assert historical_row["historical_fallback_allowed_as_trusted_source"] is False

    disk = tmp_path / "disk.json"
    _write_operator_exact_document_refill_fetch_plan(
        disk,
        [_operator_exact_document_refill_fetch_row(fetch_plan_status="blocked_disk_guard")],
        disk_guard_status="blocked",
    )
    disk_report = _run_operator_exact_document_refill_workspace(
        ["--financial-document-fetch-plan-input", str(disk)]
    )

    assert disk_report["rows"][0]["workspace_status"] == "blocked_disk_guard"
    assert disk_report["rows"][0]["workspace_action"] == "review_fetch_plan_blocker_first"


def test_operator_exact_document_refill_workspace_non_missing_fetch_status_is_diagnostic_block(tmp_path: Path) -> None:
    fetch_plan = tmp_path / "fetch.json"
    _write_operator_exact_document_refill_fetch_plan(
        fetch_plan,
        [_operator_exact_document_refill_fetch_row(fetch_plan_status="blocked_wrong_period")],
    )

    report = _run_operator_exact_document_refill_workspace(
        ["--financial-document-fetch-plan-input", str(fetch_plan)]
    )

    assert report["rows"][0]["workspace_status"] == "blocked_fetch_plan_not_missing_exact_url"
    assert report["rows"][0]["workspace_action"] == "review_fetch_plan_blocker_first"


def test_operator_exact_document_refill_workspace_uses_baseline_only_fallbacks(tmp_path: Path) -> None:
    fetch_plan = tmp_path / "fetch.json"
    validation = tmp_path / "validation.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    _write_operator_exact_document_refill_rows(
        validation,
        "operator-resolution-source-trust-refill-validate",
        [
            {
                "company_id": "67",
                "canonical_company_id": "67",
                "baseline_trusted_source_hosts": ["mostotrest.ru"],
                "baseline_current_known_source_page_url": "https://mostotrest.ru/reports/",
                "operator_fill_current_known_source_page_url": "https://manual.example/reports/",
            }
        ],
    )

    report = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-source-trust-validation-input",
            str(validation),
        ]
    )
    row = report["rows"][0]

    assert row["workspace_status"] == "ready_for_exact_document_url_refill"
    assert row["trusted_source_hosts"] == ["mostotrest.ru"]
    assert "manual.example" not in row["trusted_source_hosts"]

    candidate_only = tmp_path / "draft.json"
    candidate_only.write_text(
        json.dumps(
            {
                "resolutions": [
                    {
                        "company_id": "67",
                        "canonical_company_id": "67",
                        "candidate_current_known_source_page_url": "https://manual.example/reports/",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    blocked = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-source-pack-draft-input",
            str(candidate_only),
        ]
    )
    assert blocked["rows"][0]["workspace_status"] == "blocked_source_trust_required_before_exact_document_refill"
    assert blocked["rows"][0]["trusted_source_hosts"] == []


def test_operator_exact_document_refill_workspace_preserves_manual_values_as_unvalidated_candidates(
    tmp_path: Path,
) -> None:
    fetch_plan = tmp_path / "fetch.json"
    board = tmp_path / "board.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    _write_operator_exact_document_refill_rows(
        board,
        "operator-resolution-chain-review-board",
        [
            {
                "company_id": "67",
                "canonical_company_id": "67",
                "trusted_source_hosts": ["mostotrest.ru"],
                "operator_fill_exact_document_url": "https://mostotrest.ru/reports/new.pdf",
                "operator_fill_document_title": "Manual title",
                "operator_fill_document_date": "2026-04-30",
                "operator_fill_notes": "Needs validation",
            }
        ],
    )

    report = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-chain-review-board-input",
            str(board),
        ]
    )
    row = report["rows"][0]

    assert row["workspace_status"] == "ready_for_exact_document_url_refill"
    assert row["operator_fill_exact_document_url"] == "https://mostotrest.ru/reports/new.pdf"
    assert row["operator_fill_document_title"] == "Manual title"
    assert row["operator_fill_document_publication_date"] == "2026-04-30"
    assert "manual_url_requires_future_validation" in row["workspace_reason_codes"]
    assert "manual_url_requires_future_validation" in row["workspace_warnings"]


def test_operator_exact_document_refill_workspace_blocks_ambiguous_source_context_but_aggregates_fetch_blockers(
    tmp_path: Path,
) -> None:
    fetch_plan = tmp_path / "fetch.json"
    source_trust = tmp_path / "trust.json"
    blockers = tmp_path / "blockers.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    _write_operator_exact_document_refill_rows(
        source_trust,
        "operator-resolution-source-trust-workspace",
        [
            _operator_exact_document_refill_source_trust_row(current_known_source_page_url="https://mostotrest.ru/reports/"),
            _operator_exact_document_refill_source_trust_row(current_known_source_page_url="https://mostotrest.ru/investors/"),
        ],
    )
    blockers.write_text(
        json.dumps(
            {
                "fetch_blocker_rows": [
                    {
                        "fetch_plan_id": "financial_document_fetch_plan:67:2025:missing_exact_document_url",
                        "blocker_code": "missing_exact_document_url",
                    },
                    {
                        "fetch_plan_id": "financial_document_fetch_plan:67:2025:missing_exact_document_url",
                        "blocker_code": "manual_review_required",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-source-trust-workspace-input",
            str(source_trust),
            "--financial-document-fetch-blockers-input",
            str(blockers),
        ]
    )

    assert report["rows"][0]["workspace_status"] == "blocked_unknown_readiness"
    assert any(
        warning["message"] == "operator_exact_document_refill_ambiguous_context:source_trust_workspace"
        for warning in report["warnings"]
    )
    assert not any(
        warning["message"] == "operator_exact_document_refill_ambiguous_context:fetch_blockers"
        for warning in report["warnings"]
    )


def test_operator_exact_document_refill_workspace_rejects_invalid_input_and_output_collisions(tmp_path: Path) -> None:
    missing = _run_operator_exact_document_refill_workspace([])
    assert missing["status"] == "failed"
    assert missing["errors"] == [{"message": "operator_exact_document_refill_fetch_plan_input_required"}]

    malformed = tmp_path / "fetch.json"
    malformed.write_text("{}", encoding="utf-8")
    invalid = _run_operator_exact_document_refill_workspace(
        ["--financial-document-fetch-plan-input", str(malformed)]
    )
    assert invalid["status"] == "failed"
    assert invalid["errors"][0]["message"] == "operator_exact_document_refill_fetch_plan_input_invalid"

    fetch_plan = tmp_path / "valid.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    collision = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-exact-document-refill-workspace-output",
            str(fetch_plan),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "operator_exact_document_refill_output_must_not_equal_input"}]

    generic = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--json-output",
            str(fetch_plan),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "operator_exact_document_refill_output_must_not_equal_input"}]


def test_operator_exact_document_refill_workspace_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task134 refill workspace must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    fetch_plan = tmp_path / "fetch.json"
    source_trust = tmp_path / "trust.json"
    _write_operator_exact_document_refill_fetch_plan(fetch_plan, [_operator_exact_document_refill_fetch_row()])
    _write_operator_exact_document_refill_rows(
        source_trust,
        "operator-resolution-source-trust-workspace",
        [_operator_exact_document_refill_source_trust_row()],
    )

    report = _run_operator_exact_document_refill_workspace(
        [
            "--financial-document-fetch-plan-input",
            str(fetch_plan),
            "--operator-resolution-source-trust-workspace-input",
            str(source_trust),
        ]
    )

    assert report["status"] in {"passed", "warning"}
    for key in (
        "documents_downloaded",
        "documents_parsed",
        "files_deleted",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[key] is False


def test_operator_exact_document_refill_validation_splits_vds_like_rows_and_writes_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "task124_chain_preview"
    template = output_dir / "operator_exact_document_refill_template_task134.csv"
    workspace = output_dir / "operator_exact_document_refill_workspace_task134.json"
    fetch = output_dir / "financial_document_fetch_plan_task133.json"
    _write_operator_exact_document_refill_template(
        template,
        [
            _operator_exact_document_refill_validation_template_row(
                workspace_id="operator_exact_document_refill:18",
                company_id="18",
                company_name="RZD",
                workspace_status="blocked_source_trust_required_before_exact_document_refill",
                operator_fill_exact_document_url="",
            ),
            _operator_exact_document_refill_validation_template_row(operator_fill_exact_document_url=""),
        ],
    )
    _write_operator_exact_document_refill_rows(
        workspace,
        "operator-exact-document-refill-workspace-v2",
        [
            _operator_exact_document_refill_validation_workspace_row(
                workspace_id="operator_exact_document_refill:18",
                company_id="18",
                company_name="RZD",
                workspace_status="blocked_source_trust_required_before_exact_document_refill",
                source_trust_status="trusted_source_missing",
                trusted_source_hosts=[],
            ),
            _operator_exact_document_refill_validation_workspace_row(),
        ],
    )
    _write_operator_exact_document_refill_fetch_plan(fetch, [_operator_exact_document_refill_fetch_row()])

    report = _run_operator_exact_document_refill_validation(
        ["--operator-resolution-chain-output-dir", str(output_dir)]
    )
    rows = {str(row["company_id"]): row for row in report["validation_rows"]}

    assert report["row_count"] == 2
    assert report["valid_candidate_count"] == 0
    assert report["accepted_candidate_count"] == 0
    assert report["blocked_source_trust_count"] == 1
    assert report["incomplete_count"] == 1
    assert rows["18"]["validation_status"] == "blocked_source_trust_required"
    assert rows["67"]["validation_status"] == "incomplete_missing_exact_document_url"
    assert all(Path(path).is_file() for path in report["artifacts"].values())
    markdown = Path(report["artifacts"]["validation_markdown"]).read_text(encoding="utf-8")
    assert "# Operator Exact Document Refill Validation v2" in markdown
    assert "This task does not download reports." in markdown


def test_operator_exact_document_refill_validation_accepts_future_pdf_candidate_only(tmp_path: Path) -> None:
    template, workspace = _write_operator_exact_document_refill_validation_inputs(
        tmp_path,
        operator_fill_exact_document_url="https://docs.mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf",
        operator_fill_document_title="Mostotrest annual consolidated IFRS financial statements 2025",
    )

    report = _run_operator_exact_document_refill_validation(
        [
            "--operator-exact-document-refill-input",
            str(template),
            "--operator-exact-document-refill-workspace-input",
            str(workspace),
        ]
    )
    row = report["validation_rows"][0]
    candidate = report["accepted_candidate_rows"][0]

    assert row["validation_status"] == "valid_future_exact_document_candidate"
    assert row["accepted_for_future_apply_draft"] is True
    assert report["accepted_candidate_count"] == 1
    assert candidate["future_apply_draft_allowed"] is True
    assert candidate["document_url_registrable_domain"] == "mostotrest.ru"
    assert row["would_accept_url"] is False
    assert row["would_update_exact_document_intake"] is False
    assert row["would_download_document"] is False


def test_operator_exact_document_refill_validation_rejects_unsafe_urls(tmp_path: Path) -> None:
    cases = [
        (
            "https://mostotrest.ru/reports/history/annual-ifrs-2024.pdf",
            {"latest_historical_document_url": "https://mostotrest.ru/reports/history/annual-ifrs-2024.pdf"},
            "invalid_historical_fallback_url",
        ),
        ("https://mostotrest.ru/archive/annual-ifrs-2025.pdf", {}, "invalid_archive_or_history_url"),
        ("https://evil.example/reports/annual-ifrs-2025.pdf", {}, "invalid_untrusted_host"),
        ("not-a-url", {}, "invalid_malformed_url"),
        ("ftp://mostotrest.ru/reports/annual-ifrs-2025.pdf", {}, "invalid_non_http_url"),
    ]
    for index, (url, workspace_updates, expected) in enumerate(cases):
        template, workspace = _write_operator_exact_document_refill_validation_inputs(
            tmp_path / str(index),
            operator_fill_exact_document_url=url,
            operator_fill_document_title="Mostotrest annual consolidated IFRS financial statements 2025",
            workspace_updates=workspace_updates,
        )
        report = _run_operator_exact_document_refill_validation(
            [
                "--operator-exact-document-refill-input",
                str(template),
                "--operator-exact-document-refill-workspace-input",
                str(workspace),
            ]
        )
        assert report["validation_rows"][0]["validation_status"] == expected
        assert report["accepted_candidate_count"] == 0


def test_operator_exact_document_refill_validation_rejects_strict_mismatches(tmp_path: Path) -> None:
    cases = [
        (
            {
                "operator_fill_exact_document_url": "https://mostotrest.ru/reports/annual-ifrs-consolidated-2024.pdf",
                "operator_fill_document_title": "Mostotrest annual consolidated IFRS financial statements 2024",
            },
            "invalid_wrong_period",
        ),
        (
            {
                "operator_fill_exact_document_url": "https://mostotrest.ru/reports/q1-ifrs-consolidated-2025.pdf",
                "operator_fill_document_title": "Mostotrest Q1 IFRS financial statements 2025",
                "operator_fill_document_report_type": "quarterly",
            },
            "invalid_wrong_report_type",
        ),
        (
            {
                "operator_fill_exact_document_url": "https://mostotrest.ru/reports/annual-ras-consolidated-2025.pdf",
                "operator_fill_document_title": "Mostotrest annual RAS financial statements 2025",
                "operator_fill_document_accounting_standard": "RAS",
            },
            "invalid_wrong_standard",
        ),
        (
            {
                "operator_fill_exact_document_url": "https://mostotrest.ru/reports/annual-ifrs-2025.pdf",
                "operator_fill_document_title": "Mostotrest annual IFRS financial statements 2025",
                "operator_fill_document_consolidated": "false",
            },
            "invalid_non_consolidated",
        ),
    ]
    for index, (updates, expected) in enumerate(cases):
        template, workspace = _write_operator_exact_document_refill_validation_inputs(tmp_path / str(index), **updates)
        report = _run_operator_exact_document_refill_validation(
            [
                "--operator-exact-document-refill-input",
                str(template),
                "--operator-exact-document-refill-workspace-input",
                str(workspace),
            ]
        )
        assert report["validation_rows"][0]["validation_status"] == expected


def test_operator_exact_document_refill_validation_blocks_landing_forbidden_and_warns_for_non_pdf(tmp_path: Path) -> None:
    landing_template, landing_workspace = _write_operator_exact_document_refill_validation_inputs(
        tmp_path / "landing",
        operator_fill_exact_document_url="https://mostotrest.ru/reports",
        operator_fill_document_title="Mostotrest reports",
    )
    landing = _run_operator_exact_document_refill_validation(
        ["--operator-exact-document-refill-input", str(landing_template), "--operator-exact-document-refill-workspace-input", str(landing_workspace)]
    )
    assert landing["validation_rows"][0]["validation_status"] == "invalid_landing_page_url"

    forbidden_template, forbidden_workspace = _write_operator_exact_document_refill_validation_inputs(
        tmp_path / "forbidden",
        operator_fill_exact_document_url="https://mostotrest.ru/reports/prospectus-annual-ifrs-2025.pdf",
        operator_fill_document_title="Mostotrest prospectus annual IFRS 2025",
    )
    forbidden = _run_operator_exact_document_refill_validation(
        ["--operator-exact-document-refill-input", str(forbidden_template), "--operator-exact-document-refill-workspace-input", str(forbidden_workspace)]
    )
    assert forbidden["validation_rows"][0]["validation_status"] == "invalid_forbidden_document_type"

    for extension in ("xlsx", "xls", "zip"):
        template, workspace = _write_operator_exact_document_refill_validation_inputs(
            tmp_path / extension,
            operator_fill_exact_document_url=f"https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.{extension}",
            operator_fill_document_title="Mostotrest annual consolidated IFRS financial statements 2025",
        )
        report = _run_operator_exact_document_refill_validation(
            ["--operator-exact-document-refill-input", str(template), "--operator-exact-document-refill-workspace-input", str(workspace)]
        )
        assert report["validation_rows"][0]["validation_status"] == "valid_future_exact_document_candidate"
        assert "non_pdf_document_requires_future_content_type_check" in report["validation_rows"][0]["validation_warnings"]


def test_operator_exact_document_refill_validation_blocks_missing_workspace_requirement_drift_and_duplicates(tmp_path: Path) -> None:
    template = tmp_path / "missing" / "template.csv"
    _write_operator_exact_document_refill_template(template, [_operator_exact_document_refill_validation_template_row()])
    missing = _run_operator_exact_document_refill_validation(["--operator-exact-document-refill-input", str(template)])
    assert missing["validation_rows"][0]["validation_status"] == "blocked_unknown_readiness"
    assert any(warning["message"] == "operator_exact_document_refill_workspace_missing_validation_conservative" for warning in missing["warnings"])

    drift_template, drift_workspace = _write_operator_exact_document_refill_validation_inputs(
        tmp_path / "drift",
        workspace_updates={"target_reporting_period": "2024"},
    )
    drift = _run_operator_exact_document_refill_validation(
        ["--operator-exact-document-refill-input", str(drift_template), "--operator-exact-document-refill-workspace-input", str(drift_workspace)]
    )
    assert drift["validation_rows"][0]["validation_status"] == "blocked_workspace_not_ready"

    duplicate_template = tmp_path / "duplicate" / "template.csv"
    duplicate_workspace = tmp_path / "duplicate" / "workspace.json"
    shared_url = "https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf"
    _write_operator_exact_document_refill_template(
        duplicate_template,
        [
            _operator_exact_document_refill_validation_template_row(operator_fill_exact_document_url=shared_url),
            _operator_exact_document_refill_validation_template_row(
                workspace_id="operator_exact_document_refill:68",
                company_id="68",
                company_name="Other issuer",
                operator_fill_exact_document_url=shared_url,
            ),
        ],
    )
    _write_operator_exact_document_refill_rows(
        duplicate_workspace,
        "operator-exact-document-refill-workspace-v2",
        [
            _operator_exact_document_refill_validation_workspace_row(),
            _operator_exact_document_refill_validation_workspace_row(workspace_id="operator_exact_document_refill:68", company_id="68", company_name="Other issuer"),
        ],
    )
    duplicate = _run_operator_exact_document_refill_validation(
        ["--operator-exact-document-refill-input", str(duplicate_template), "--operator-exact-document-refill-workspace-input", str(duplicate_workspace)]
    )
    assert {row["validation_status"] for row in duplicate["validation_rows"]} == {"invalid_duplicate_candidate"}


def test_operator_exact_document_refill_validation_explicit_exports_and_collisions(tmp_path: Path) -> None:
    template, workspace = _write_operator_exact_document_refill_validation_inputs(tmp_path)
    accepted_json = tmp_path / "accepted.json"
    accepted_csv = tmp_path / "accepted.csv"
    report = _run_operator_exact_document_refill_validation(
        [
            "--operator-exact-document-refill-input", str(template),
            "--operator-exact-document-refill-workspace-input", str(workspace),
            "--operator-exact-document-refill-accepted-candidates-output", str(accepted_json),
            "--operator-exact-document-refill-accepted-candidates-csv-output", str(accepted_csv),
        ]
    )
    assert report["artifacts"] == {"accepted_candidates_json": str(accepted_json), "accepted_candidates_csv": str(accepted_csv)}
    assert len(json.loads(accepted_json.read_text(encoding="utf-8"))["accepted_candidate_rows"]) == 1
    assert accepted_csv.is_file()

    collision = _run_operator_exact_document_refill_validation(
        [
            "--operator-exact-document-refill-input", str(template),
            "--operator-exact-document-refill-workspace-input", str(workspace),
            "--operator-exact-document-refill-validation-output", str(template),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "operator_exact_document_refill_validation_output_must_not_equal_input"}]

    generic_collision = _run_operator_exact_document_refill_validation(
        [
            "--operator-exact-document-refill-input", str(template),
            "--operator-exact-document-refill-workspace-input", str(workspace),
            "--json-output", str(template),
        ]
    )
    assert generic_collision["status"] == "failed"
    assert generic_collision["errors"] == [{"message": "operator_exact_document_refill_validation_output_must_not_equal_input"}]


def test_operator_exact_document_refill_validation_never_calls_network_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task135 validation must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    template, workspace = _write_operator_exact_document_refill_validation_inputs(tmp_path)

    report = _run_operator_exact_document_refill_validation(
        ["--operator-exact-document-refill-input", str(template), "--operator-exact-document-refill-workspace-input", str(workspace)]
    )

    assert report["status"] in {"passed", "warning"}
    for key in (
        "documents_downloaded",
        "documents_parsed",
        "files_deleted",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[key] is False


def test_operator_exact_document_refill_apply_draft_vds_like_rows_create_minimal_placeholders(tmp_path: Path) -> None:
    _write_operator_exact_document_refill_apply_validation(
        tmp_path / "operator_exact_document_refill_validation_task135.json",
        [
            _operator_exact_document_refill_apply_validation_row(
                company_id="18",
                company_name="RZD",
                validation_status="blocked_source_trust_required",
                accepted_candidate_id="",
                accepted_for_future_apply_draft=False,
            ),
            _operator_exact_document_refill_apply_validation_row(
                validation_status="incomplete_missing_exact_document_url",
                accepted_candidate_id="",
                accepted_for_future_apply_draft=False,
                normalized_document_url="",
            ),
        ],
    )
    _write_operator_exact_document_refill_apply_candidates(
        tmp_path / "operator_exact_document_refill_accepted_candidates_task135.json",
        [],
    )

    report = _run_operator_exact_document_refill_apply_draft(
        ["--operator-resolution-chain-output-dir", str(tmp_path)]
    )

    assert report["status"] == "warning"
    assert report["applied_count"] == 0
    assert report["skipped_count"] == 2
    assert report["failed_count"] == 0
    assert report["exact_document_intake_apply_draft_row_count"] == 2
    assert report["apply_status_counts"] == {
        "skipped_blocked_source_trust_required": 1,
        "skipped_incomplete_missing_exact_document_url": 1,
    }
    draft = json.loads((tmp_path / "operator_exact_document_intake_apply_draft_task136.json").read_text(encoding="utf-8"))
    assert [row["document_status"] for row in draft["documents"]] == ["not_found", "not_found"]
    assert all(row["document_url"] == "" for row in draft["documents"])


def test_operator_exact_document_refill_apply_draft_updates_only_new_draft_and_writes_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task136 apply draft must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    validation = _operator_exact_document_refill_apply_validation_row()
    candidate = _operator_exact_document_refill_apply_candidate_row()
    validation_path = tmp_path / "operator_exact_document_refill_validation_task135.json"
    accepted_path = tmp_path / "operator_exact_document_refill_accepted_candidates_task135.json"
    base_path = tmp_path / "exact_document_intake_draft_chain_task124.json"
    _write_operator_exact_document_refill_apply_validation(validation_path, [validation])
    _write_operator_exact_document_refill_apply_candidates(accepted_path, [candidate])
    _write_document_intake(base_path, [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/reports/")])
    snapshots = {path: path.read_bytes() for path in (validation_path, accepted_path, base_path)}

    report = _run_operator_exact_document_refill_apply_draft(
        ["--operator-resolution-chain-output-dir", str(tmp_path)]
    )

    assert report["status"] == "passed"
    assert report["applied_count"] == 1
    assert report["skipped_count"] == 0
    assert report["failed_count"] == 0
    assert report["apply_rows"][0]["apply_status"] == "applied_to_exact_document_intake_draft"
    assert report["apply_rows"][0]["would_change_draft"] is True
    for path, content in snapshots.items():
        assert path.read_bytes() == content
    draft_path = tmp_path / "operator_exact_document_intake_apply_draft_task136.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    document = draft["documents"][0]
    assert document["document_url"] == candidate["exact_document_url"]
    assert document["document_status"] == "draft_candidate_pending_future_gate_validation"
    assert document["ready_for_document_download"] is False
    assert document["ready_for_value_extraction"] is False
    for filename in assistant.OPERATOR_EXACT_DOCUMENT_REFILL_APPLY_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    assert "Operator Exact Document Refill Apply Draft v2" in (
        tmp_path / "operator_exact_document_refill_apply_task136.md"
    ).read_text(encoding="utf-8")
    for key in (
        "documents_downloaded",
        "documents_parsed",
        "files_deleted",
        "would_fetch_documents",
        "would_download_documents",
        "would_parse_documents",
        "would_extract_values",
        "would_import_report",
        "would_mutate_database",
        "would_mutate_scores",
        "would_trigger_paper_trading",
    ):
        assert report[key] is False


def test_operator_exact_document_refill_apply_draft_requires_validation_cross_check_and_candidate_pair(
    tmp_path: Path,
) -> None:
    candidate = _operator_exact_document_refill_apply_candidate_row()
    accepted_only = tmp_path / "accepted_only.json"
    accepted_only_draft = tmp_path / "accepted_only_draft.json"
    _write_operator_exact_document_refill_apply_candidates(accepted_only, [candidate])

    accepted_report = _run_operator_exact_document_refill_apply_draft(
        [
            "--operator-exact-document-refill-accepted-candidates-input",
            str(accepted_only),
            "--operator-exact-document-intake-apply-draft-output",
            str(accepted_only_draft),
        ]
    )

    assert accepted_report["status"] == "warning"
    assert accepted_report["apply_rows"][0]["apply_status"] == "skipped_not_accepted_candidate"
    assert accepted_report["apply_rows"][0]["apply_reason_codes"] == ["validation_cross_check_required"]
    validation_only = tmp_path / "validation_only.json"
    _write_operator_exact_document_refill_apply_validation(
        validation_only,
        [_operator_exact_document_refill_apply_validation_row()],
    )
    validation_report = _run_operator_exact_document_refill_apply_draft(
        ["--operator-exact-document-refill-validation-input", str(validation_only)]
    )
    assert validation_report["apply_rows"][0]["apply_status"] == "skipped_not_accepted_candidate"


def test_operator_exact_document_refill_apply_draft_blocks_empty_duplicate_mismatch_unsafe_and_drift(
    tmp_path: Path,
) -> None:
    cases = [
        (
            "empty",
            [_operator_exact_document_refill_apply_validation_row()],
            [_operator_exact_document_refill_apply_candidate_row(exact_document_url="")],
            None,
            "failed_missing_exact_document_url",
        ),
        (
            "mismatch",
            [_operator_exact_document_refill_apply_validation_row()],
            [_operator_exact_document_refill_apply_candidate_row(document_report_type="interim")],
            None,
            "skipped_strict_mismatch",
        ),
        (
            "unsafe",
            [_operator_exact_document_refill_apply_validation_row()],
            [_operator_exact_document_refill_apply_candidate_row(would_extract_values=True)],
            None,
            "failed_input_drift",
        ),
        (
            "artifact_drift",
            [_operator_exact_document_refill_apply_validation_row()],
            [_operator_exact_document_refill_apply_candidate_row(workspace_id="operator_exact_document_refill:other")],
            None,
            "failed_input_drift",
        ),
        (
            "drift",
            [_operator_exact_document_refill_apply_validation_row()],
            [_operator_exact_document_refill_apply_candidate_row()],
            "https://mostotrest.ru/reports/different-annual-ifrs-consolidated-2025.pdf",
            "failed_input_drift",
        ),
    ]
    for name, validations, candidates, base_url, expected_status in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        validation_path = case_dir / "validation.json"
        accepted_path = case_dir / "accepted.json"
        draft_path = case_dir / "draft.json"
        _write_operator_exact_document_refill_apply_validation(validation_path, validations)
        _write_operator_exact_document_refill_apply_candidates(accepted_path, candidates)
        args = [
            "--operator-exact-document-refill-validation-input",
            str(validation_path),
            "--operator-exact-document-refill-accepted-candidates-input",
            str(accepted_path),
            "--operator-exact-document-intake-apply-draft-output",
            str(draft_path),
        ]
        if base_url:
            base_path = case_dir / "base.json"
            base_row = _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/reports/")
            base_row["document_url"] = base_url
            _write_document_intake(base_path, [base_row])
            args.extend(["--document-intake-draft-input", str(base_path)])
        report = _run_operator_exact_document_refill_apply_draft(args)
        assert report["apply_rows"][0]["apply_status"] == expected_status
        assert report["applied_count"] == 0

    duplicate_dir = tmp_path / "duplicate"
    duplicate_dir.mkdir()
    validation_path = duplicate_dir / "validation.json"
    accepted_path = duplicate_dir / "accepted.json"
    _write_operator_exact_document_refill_apply_validation(
        validation_path,
        [_operator_exact_document_refill_apply_validation_row()],
    )
    _write_operator_exact_document_refill_apply_candidates(
        accepted_path,
        [
            _operator_exact_document_refill_apply_candidate_row(),
            _operator_exact_document_refill_apply_candidate_row(),
        ],
    )
    duplicate = _run_operator_exact_document_refill_apply_draft(
        [
            "--operator-exact-document-refill-validation-input",
            str(validation_path),
            "--operator-exact-document-refill-accepted-candidates-input",
            str(accepted_path),
        ]
    )
    assert duplicate["apply_rows"][0]["apply_status"] == "skipped_duplicate_candidate"


def test_operator_exact_document_refill_apply_draft_uses_embedded_candidates_and_task124_base_priority(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "operator_exact_document_refill_validation_task135.json"
    candidate = _operator_exact_document_refill_apply_candidate_row()
    _write_operator_exact_document_refill_apply_validation(
        validation_path,
        [_operator_exact_document_refill_apply_validation_row()],
        accepted_rows=[candidate],
    )
    task124 = tmp_path / "exact_document_intake_draft_chain_task124.json"
    legacy = tmp_path / "exact_document_intake_draft_task121.json"
    task124_row = _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/task124/")
    task124_row["base_marker"] = "task124"
    legacy_row = _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/legacy/")
    legacy_row["base_marker"] = "legacy"
    _write_document_intake(task124, [task124_row])
    _write_document_intake(legacy, [legacy_row])

    report = _run_operator_exact_document_refill_apply_draft(
        ["--operator-resolution-chain-output-dir", str(tmp_path)]
    )

    assert report["applied_count"] == 1
    assert {"message": "operator_exact_document_refill_accepted_candidates_missing_using_embedded_validation_rows"} in report[
        "warnings"
    ]
    draft = json.loads((tmp_path / "operator_exact_document_intake_apply_draft_task136.json").read_text(encoding="utf-8"))
    assert draft["documents"][0]["base_marker"] == "task124"


def test_operator_exact_document_refill_apply_draft_output_collisions_fail_safely(tmp_path: Path) -> None:
    validation_path = tmp_path / "validation.json"
    accepted_path = tmp_path / "accepted.json"
    _write_operator_exact_document_refill_apply_validation(
        validation_path,
        [_operator_exact_document_refill_apply_validation_row()],
    )
    _write_operator_exact_document_refill_apply_candidates(accepted_path, [])
    original = validation_path.read_bytes()

    dedicated = _run_operator_exact_document_refill_apply_draft(
        [
            "--operator-exact-document-refill-validation-input",
            str(validation_path),
            "--operator-exact-document-refill-accepted-candidates-input",
            str(accepted_path),
            "--operator-exact-document-refill-apply-output",
            str(accepted_path),
        ]
    )
    assert dedicated["status"] == "failed"
    assert dedicated["errors"] == [{"message": "operator_exact_document_refill_apply_output_must_not_equal_input"}]
    assert accepted_path.is_file()
    assert validation_path.read_bytes() == original

    valid_validation_path = tmp_path / "valid_validation.json"
    _write_operator_exact_document_refill_apply_validation(
        valid_validation_path,
        [_operator_exact_document_refill_apply_validation_row()],
    )
    generic = _run_operator_exact_document_refill_apply_draft(
        [
            "--operator-exact-document-refill-validation-input",
            str(valid_validation_path),
            "--operator-exact-document-refill-accepted-candidates-input",
            str(accepted_path),
            "--json-output",
            str(valid_validation_path),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "operator_exact_document_refill_apply_output_must_not_equal_input"}]


def test_exact_document_draft_gate_vds_like_rows_remain_blocked(tmp_path: Path) -> None:
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[
            _exact_document_draft_gate_placeholder_document(company_id="18", company_name="RZD"),
            _exact_document_draft_gate_placeholder_document(),
        ],
        apply_rows=[
            _exact_document_draft_gate_apply_row(
                company_id="18",
                company_name="RZD",
                apply_id="operator_exact_document_refill_apply:rzd",
                apply_status="skipped_blocked_source_trust_required",
            ),
            _exact_document_draft_gate_apply_row(
                apply_status="skipped_incomplete_missing_exact_document_url",
            ),
        ],
        blocker_rows=[
            _exact_document_draft_gate_apply_blocker_row(
                apply_id="operator_exact_document_refill_apply:rzd",
                blocker_code="source_trust_required",
            ),
            _exact_document_draft_gate_apply_blocker_row(
                blocker_code="missing_exact_document_url",
            ),
        ],
    )

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["ready_count"] == 0
    assert report["blocked_count"] == 2
    assert report["blocker_row_count"] == 2
    assert report["ready_for_future_controlled_download"] is False
    statuses = {str(row["company_id"]): row["gate_status"] for row in report["gate_rows"]}
    assert statuses == {
        "18": "blocked_source_trust_required",
        "67": "blocked_incomplete_operator_refill",
    }


def test_exact_document_draft_gate_valid_row_is_download_preview_only_and_writes_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task137 must not fetch, probe, download, or parse documents")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
    )
    snapshot_paths = [
        tmp_path / "operator_exact_document_intake_apply_draft_task136.json",
        tmp_path / "operator_exact_document_refill_apply_task136.json",
        tmp_path / "operator_exact_document_refill_apply_blockers_task136.json",
        tmp_path / "document_artifact_retention_policy_task132.json",
        tmp_path / "financial_document_fetch_plan_task133.json",
    ]
    snapshots = {path: path.read_bytes() for path in snapshot_paths}

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["ready_count"] == 1
    assert report["blocked_count"] == 0
    row = report["gate_rows"][0]
    assert row["gate_status"] == "ready_for_future_controlled_download"
    assert row["ready_for_future_controlled_download"] is True
    assert row["ready_for_future_parse"] is False
    assert row["ready_for_future_extraction"] is False
    assert row["ready_for_future_import"] is False
    assert row["ready_for_future_scoring"] is False
    assert row["ready_for_future_paper_trading"] is False
    assert row["would_download_document"] is False
    for path, content in snapshots.items():
        assert path.read_bytes() == content
    for filename in assistant.EXACT_DOCUMENT_DRAFT_GATE_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    ready = json.loads((tmp_path / "exact_document_draft_gate_ready_task137.json").read_text(encoding="utf-8"))
    assert len(ready["ready_rows"]) == 1
    assert ready["ready_rows"][0]["would_write_raw_file"] is False
    markdown = (tmp_path / "exact_document_draft_gate_task137.md").read_text(encoding="utf-8")
    assert "Exact Document Draft Gate v2" in markdown
    assert "does not download reports" in markdown


def test_exact_document_draft_gate_prefers_task152_and_marks_rzd_page_ready_for_fetch_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task153 must not probe, fetch, download, parse, mutate, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    task152_draft = _write_exact_document_draft_gate_task152_inputs(tmp_path)
    source_pack = tmp_path / "source_trust_recovery_controlled_source_pack_task148.json"
    snapshots = {
        task152_draft: task152_draft.read_bytes(),
        source_pack: source_pack.read_bytes(),
    }

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["draft_input_path"] == str(task152_draft)
    assert report["draft_input_resolution_strategy"] == "chain_task152_rzd_intake_draft"
    assert report["draft_input_sha256"] == hashlib.sha256(snapshots[task152_draft]).hexdigest()
    assert report["task152_intake_draft_used"] is True
    assert report["task152_intake_draft_path"] == str(task152_draft)
    assert report["task152_intake_draft_sha256"] == report["draft_input_sha256"]
    assert report["input_bytes_unchanged"] is True
    assert report["ready_count"] == 1
    assert report["ready_for_future_fetch_plan_count"] == 1
    assert report["ready_for_future_controlled_download_count"] == 0
    assert report["ready_for_future_fetch_plan"] is True
    assert report["ready_for_future_controlled_download"] is False
    row = report["gate_rows"][0]
    assert row["gate_status"] == "ready_for_future_document_fetch_plan"
    assert row["task152_candidate_context"] is True
    assert row["candidate_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["candidate_document_url_source"] == "task152_intake_draft"
    assert row["candidate_document_context_status"] == "exact_document_url_apply_draft_for_future_gate"
    assert row["candidate_document_context_origin"] == "rzd_exact_document_refill_apply_draft_task152"
    assert row["document_url_from_task152_draft"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["task152_document_context_status"] == "exact_document_url_apply_draft_for_future_gate"
    assert row["task152_ready_for_document_download"] is False
    assert row["task152_ready_for_extraction"] is False
    assert row["task152_ready_for_import"] is False
    assert row["task152_ready_for_scoring"] is False
    assert row["task152_ready_for_paper_trading"] is False
    assert row["task152_download_allowed"] is False
    assert row["task152_parse_allowed"] is False
    assert row["task152_import_allowed"] is False
    assert row["candidate_document_host_trusted_by_source_pack"] is True
    assert row["ready_for_future_fetch_plan"] is True
    assert row["ready_for_future_controlled_download"] is False
    assert row["ready_for_future_download_plan"] is False
    assert row["ready_for_future_parse"] is False
    assert row["ready_for_future_extraction"] is False
    assert row["ready_for_future_import"] is False
    assert row["ready_for_future_scoring"] is False
    assert row["ready_for_future_paper_trading"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_document"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    assert row["would_mutate_document_intake"] is False
    assert row["would_mutate_source_pack"] is False
    ready = report["ready_rows"][0]
    assert ready["ready_status"] == "ready_for_future_document_fetch_plan"
    assert ready["future_fetch_plan_required"] is True
    assert ready["future_download_plan_required"] is False
    assert ready["future_hash_manifest_required"] is False
    assert ready["future_pre_write_size_check_required"] is False
    assert report["blocker_rows"] == []
    for path, content in snapshots.items():
        assert path.read_bytes() == content


def test_exact_document_draft_gate_task152_top_level_fetch_plan_summary_is_existential(tmp_path: Path) -> None:
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[
            _exact_document_draft_gate_apply_row(
                apply_status="skipped_incomplete_missing_exact_document_url",
            )
        ],
        blocker_rows=[_exact_document_draft_gate_apply_blocker_row(blocker_code="missing_exact_document_url")],
    )
    task152_draft = tmp_path / "rzd_exact_document_intake_draft_task152.json"
    task152_draft.write_text(
        json.dumps(
            {
                "documents": [
                    _exact_document_draft_gate_task152_document(),
                    _exact_document_draft_gate_placeholder_document(),
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_exact_document_draft_gate_source_pack(tmp_path)

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["ready_for_future_fetch_plan_count"] == 1
    assert report["ready_for_future_fetch_plan"] is True
    assert report["ready_for_future_controlled_download_count"] == 0
    assert report["ready_for_future_controlled_download"] is False
    assert report["blocked_count"] == 1


def test_exact_document_draft_gate_task152_missing_safety_fields_default_false(tmp_path: Path) -> None:
    document = _exact_document_draft_gate_task152_document()
    for field in (
        "ready_for_document_download",
        "ready_for_extraction",
        "ready_for_import",
        "ready_for_scoring",
        "ready_for_paper_trading",
        "download_allowed",
        "parse_allowed",
        "import_allowed",
    ):
        document.pop(field)
    _write_exact_document_draft_gate_task152_inputs(tmp_path, document=document)

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    row = report["gate_rows"][0]
    assert row["gate_status"] == "ready_for_future_document_fetch_plan"
    for field in (
        "task152_ready_for_document_download",
        "task152_ready_for_extraction",
        "task152_ready_for_import",
        "task152_ready_for_scoring",
        "task152_ready_for_paper_trading",
        "task152_download_allowed",
        "task152_parse_allowed",
        "task152_import_allowed",
    ):
        assert row[field] is False


def test_exact_document_draft_gate_explicit_draft_input_overrides_task152(tmp_path: Path) -> None:
    task152_draft = _write_exact_document_draft_gate_task152_inputs(tmp_path)
    explicit = tmp_path / "explicit_intake.json"
    _write_document_intake(explicit, [_exact_document_draft_gate_placeholder_document()])

    report = _run_exact_document_draft_gate(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--exact-document-draft-gate-input",
            str(explicit),
        ]
    )

    assert task152_draft.is_file()
    assert report["draft_input_path"] == str(explicit)
    assert report["draft_input_resolution_strategy"] == "explicit_cli_input"
    assert report["task152_intake_draft_used"] is False
    assert report["gate_rows"][0]["gate_status"] == "blocked_missing_exact_document_url"


@pytest.mark.parametrize("container_key", ["documents", "rows", None])
def test_exact_document_draft_gate_task152_supports_container_shapes(
    tmp_path: Path,
    container_key: str | None,
) -> None:
    _write_exact_document_draft_gate_task152_inputs(tmp_path, container_key=container_key)

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["gate_rows"][0]["gate_status"] == "ready_for_future_document_fetch_plan"


@pytest.mark.parametrize(
    ("updates", "expected_status", "expected_blocker"),
    [
        (
            {
                "official_document_page_url": "",
                "candidate_exact_document_url": "",
                "document_url": "",
                "exact_document_url": "",
            },
            "blocked_candidate_url_missing",
            "candidate_url_missing",
        ),
        (
            {
                "official_document_page_url": "not a url",
                "candidate_exact_document_url": "not a url",
                "document_url": "not a url",
                "exact_document_url": "not a url",
            },
            "blocked_candidate_url_malformed",
            "candidate_url_malformed",
        ),
        (
            {
                "official_document_page_url": "http://company.rzd.ru/ru/9397/page/104069?id=322745",
                "candidate_exact_document_url": "http://company.rzd.ru/ru/9397/page/104069?id=322745",
                "document_url": "http://company.rzd.ru/ru/9397/page/104069?id=322745",
                "exact_document_url": "http://company.rzd.ru/ru/9397/page/104069?id=322745",
            },
            "blocked_candidate_url_not_https",
            "candidate_url_not_https",
        ),
        (
            {
                "official_document_page_url": "https://example.com/ru/9397/page/104069?id=322745",
                "candidate_exact_document_url": "https://example.com/ru/9397/page/104069?id=322745",
                "document_url": "https://example.com/ru/9397/page/104069?id=322745",
                "exact_document_url": "https://example.com/ru/9397/page/104069?id=322745",
            },
            "blocked_candidate_host_not_trusted",
            "candidate_host_not_trusted",
        ),
        ({"target_reporting_period": "2024", "report_period": "2024"}, "blocked_candidate_year_mismatch", "candidate_year_mismatch"),
        ({"document_report_type": "interim", "report_type": "interim"}, "blocked_candidate_report_type_mismatch", "candidate_report_type_mismatch"),
        ({"document_accounting_standard": "RAS", "accounting_standard": "RAS"}, "blocked_candidate_standard_mismatch", "candidate_standard_mismatch"),
        ({"document_consolidated": False}, "blocked_candidate_consolidated_mismatch", "candidate_consolidated_mismatch"),
        ({"ready_for_extraction": True}, "blocked_downstream_ready_flag_leak", "downstream_ready_flag_leak"),
    ],
)
def test_exact_document_draft_gate_task152_blocks_unsafe_page_candidates(
    tmp_path: Path,
    updates: dict[str, object],
    expected_status: str,
    expected_blocker: str,
) -> None:
    document = _exact_document_draft_gate_task152_document(**updates)
    _write_exact_document_draft_gate_task152_inputs(tmp_path, document=document)

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["gate_rows"][0]["gate_status"] == expected_status
    assert report["blocker_rows"][0]["blocker_code"] == expected_blocker
    assert report["ready_count"] == 0


def test_rzd_exact_document_fetch_plan_valid_page_candidate_is_preview_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task154 must not probe, fetch, download, parse, mutate, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    paths = _write_rzd_exact_document_fetch_plan_inputs(tmp_path)
    snapshots = {path: path.read_bytes() for path in paths.values()}

    report = _run_rzd_exact_document_fetch_plan(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["gate_row_count"] == 1
    assert report["row_count"] == 1
    assert report["ready_count"] == 1
    assert report["blocked_count"] == 0
    assert report["page_fetch_plan_ready_count"] == 1
    assert report["document_download_plan_ready_count"] == 0
    assert report["rzd_ready_for_future_controlled_page_fetch_preview"] is True
    assert report["rzd_ready_for_future_document_download"] is False
    assert report["rzd_ready_for_future_parse"] is False
    assert report["gate_input_preserved"] is True
    assert report["intake_draft_input_preserved"] is True
    assert report["source_pack_input_preserved"] is True
    assert report["input_bytes_unchanged"] is True
    assert report["gate_input_bytes_unchanged"] is True
    assert report["intake_draft_input_bytes_unchanged"] is True
    assert report["source_pack_input_bytes_unchanged"] is True
    assert report["production_source_pack_modified"] is False
    assert report["controlled_source_pack_modified"] is False
    assert report["production_document_intake_modified"] is False
    assert report["document_intake_draft_modified"] is False
    row = report["fetch_plan_rows"][0]
    assert row["candidate_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["candidate_document_host"] == "company.rzd.ru"
    assert row["candidate_url_type"] == "official_source_page"
    assert row["fetch_plan_kind"] == "official_page_candidate_discovery_preview"
    assert row["fetch_plan_status"] == "ready_for_future_controlled_page_fetch_preview"
    assert row["future_controlled_page_fetch_required"] is True
    assert row["future_page_fetch_token_required"] is True
    assert row["future_page_fetch_expected_sha_required"] is True
    assert row["future_page_link_discovery_required"] is True
    assert row["future_document_download_plan_required"] is True
    assert row["future_document_download_required"] is False
    assert row["future_document_parse_required"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_page"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_pages"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_document_intake"] is False
    assert report["would_mutate_source_pack"] is False
    assert report["would_mutate_database"] is False
    assert report["would_delete_files"] is False
    for field in (
        "read_only",
        "dry_run_only",
        "rzd_ready_for_future_controlled_page_fetch_preview",
        "rzd_ready_for_future_document_download",
        "rzd_ready_for_future_parse",
        "gate_input_preserved",
        "intake_draft_input_preserved",
        "source_pack_input_preserved",
        "input_bytes_unchanged",
        "gate_input_bytes_unchanged",
        "intake_draft_input_bytes_unchanged",
        "source_pack_input_bytes_unchanged",
        "production_source_pack_modified",
        "controlled_source_pack_modified",
        "production_document_intake_modified",
        "document_intake_draft_modified",
        "would_probe_urls",
        "would_fetch_urls",
        "would_fetch_pages",
        "would_download_documents",
        "would_parse_documents",
        "would_write_raw_files",
        "would_write_hash_manifests",
        "would_mutate_document_intake",
        "would_mutate_database",
        "would_extract_values",
        "would_import_report",
        "would_mutate_scores",
        "would_trigger_paper_trading",
        "would_delete_files",
        "documents_downloaded",
        "documents_parsed",
        "files_deleted",
        "import_executed",
        "paper_trading_called",
    ):
        assert field in report
        assert isinstance(report[field], bool)
    for path, content in snapshots.items():
        assert path.read_bytes() == content
    for filename in assistant.RZD_EXACT_DOCUMENT_FETCH_PLAN_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    markdown = (tmp_path / "rzd_exact_document_fetch_plan_task154.md").read_text(encoding="utf-8")
    assert "RZD Exact Document Fetch Plan Preview v2" in markdown
    assert "Page-fetch preview readiness is not document-download readiness" in markdown
    rerun = (tmp_path / "rzd_exact_document_fetch_plan_rerun_task154.md").read_text(encoding="utf-8")
    assert "rzd-controlled-page-fetch-preview-v2" in rerun
    assert "<TASK154_PAGE_FETCH_CONFIRMATION_TOKEN>" in rerun
    assert "<EXPECTED_TASK153_GATE_SHA256>" in rerun


def test_rzd_exact_document_fetch_plan_keeps_unrelated_gate_blocker_visible(tmp_path: Path) -> None:
    _write_rzd_exact_document_fetch_plan_inputs(tmp_path, include_non_rzd_blocker=True)

    report = _run_rzd_exact_document_fetch_plan(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["gate_row_count"] == 2
    assert report["row_count"] == 1
    assert report["ready_count"] == 1
    assert report["blocked_count"] == 1
    assert report["rzd_ready_for_future_controlled_page_fetch_preview"] is True
    assert report["rzd_ready_for_future_document_download"] is False
    assert report["rzd_ready_for_future_parse"] is False
    assert report["blocker_rows"][0]["company_id"] == "67"
    assert report["blocker_rows"][0]["blocker_code"] == "gate_row_not_ready_for_future_fetch_plan"


@pytest.mark.parametrize(
    ("case_name", "expected_status"),
    [
        ("untrusted_host", "blocked_controlled_source_trust_missing_or_untrusted"),
        ("query_pdf", "blocked_candidate_url_direct_document"),
        ("downstream_leak", "blocked_downstream_ready_leak"),
        ("unsafe_flag", "blocked_unsafe_action_flags"),
        ("metadata_mismatch", "blocked_candidate_year_mismatch"),
        ("ambiguous_intake", "blocked_ambiguous_or_inconsistent_cross_check"),
    ],
)
def test_rzd_exact_document_fetch_plan_blocks_unsafe_or_inconsistent_context(
    tmp_path: Path,
    case_name: str,
    expected_status: str,
) -> None:
    case_dir = tmp_path / case_name
    case_dir.mkdir()
    paths = _write_rzd_exact_document_fetch_plan_inputs(case_dir)
    gate = json.loads(paths["gate"].read_text(encoding="utf-8"))
    intake = json.loads(paths["intake_draft"].read_text(encoding="utf-8"))
    gate_row = gate["gate_rows"][0]
    intake_row = intake["documents"][0]
    if case_name in {"untrusted_host", "query_pdf"}:
        url = (
            "https://example.com/ru/9397/page/104069?id=322745"
            if case_name == "untrusted_host"
            else "https://company.rzd.ru/ru/9397/page/104069?id=322745.pdf"
        )
        for field in ("candidate_document_url", "document_url_from_task152_draft", "document_url", "exact_document_url"):
            gate_row[field] = url
        for field in ("official_document_page_url", "candidate_exact_document_url", "document_url", "exact_document_url"):
            intake_row[field] = url
    elif case_name == "downstream_leak":
        gate_row["ready_for_future_controlled_download"] = True
    elif case_name == "unsafe_flag":
        gate_row["would_fetch_url"] = True
    elif case_name == "metadata_mismatch":
        intake_row["target_reporting_period"] = "2024"
        intake_row["report_period"] = "2024"
    elif case_name == "ambiguous_intake":
        intake["documents"].append(dict(intake_row))
    paths["gate"].write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["intake_draft"].write_text(json.dumps(intake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = _run_rzd_exact_document_fetch_plan(["--operator-resolution-chain-output-dir", str(case_dir)])

    assert report["status"] == "warning"
    assert report["ready_count"] == 0
    assert report["blocked_count"] == 1
    assert report["fetch_plan_rows"][0]["fetch_plan_status"] == expected_status
    assert report["blocker_rows"][0]["blocker_code"] == expected_status.removeprefix("blocked_")


def test_rzd_exact_document_fetch_plan_required_failures_write_safe_artifacts(tmp_path: Path) -> None:
    report = _run_rzd_exact_document_fetch_plan(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "failed"
    assert {error["message"] for error in report["errors"]} == {
        "rzd_exact_document_fetch_plan_gate_input_required",
        "rzd_exact_document_fetch_plan_intake_draft_input_required",
        "rzd_exact_document_fetch_plan_source_pack_input_required",
    }
    assert report["fetch_plan_rows"] == []
    assert report["ready_rows"] == []
    assert report["blocker_rows"] == []
    assert report["rzd_ready_for_future_controlled_page_fetch_preview"] is False
    assert report["rzd_ready_for_future_document_download"] is False
    assert report["rzd_ready_for_future_parse"] is False
    assert report["gate_input_preserved"] is False
    assert report["intake_draft_input_preserved"] is False
    assert report["source_pack_input_preserved"] is False
    assert report["input_bytes_unchanged"] is False
    assert report["gate_input_bytes_unchanged"] is False
    assert report["intake_draft_input_bytes_unchanged"] is False
    assert report["source_pack_input_bytes_unchanged"] is False
    assert report["production_source_pack_modified"] is False
    assert report["controlled_source_pack_modified"] is False
    assert report["production_document_intake_modified"] is False
    assert report["document_intake_draft_modified"] is False
    for filename in assistant.RZD_EXACT_DOCUMENT_FETCH_PLAN_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()

    malformed_dir = tmp_path / "malformed"
    malformed_dir.mkdir()
    paths = _write_rzd_exact_document_fetch_plan_inputs(malformed_dir)
    paths["gate"].write_text("{broken", encoding="utf-8")
    malformed = _run_rzd_exact_document_fetch_plan(
        ["--operator-resolution-chain-output-dir", str(malformed_dir)]
    )
    assert malformed["status"] == "failed"
    assert malformed["errors"][0]["message"] == "rzd_exact_document_fetch_plan_gate_input_required"


def test_rzd_exact_document_fetch_plan_output_collision_fails_before_write(tmp_path: Path) -> None:
    paths = _write_rzd_exact_document_fetch_plan_inputs(tmp_path)
    original = paths["gate"].read_bytes()

    report = _run_rzd_exact_document_fetch_plan(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-fetch-plan-output",
            str(paths["gate"]),
        ]
    )

    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "rzd_exact_document_fetch_plan_output_must_not_equal_input"}]
    assert paths["gate"].read_bytes() == original
    assert not (tmp_path / "rzd_exact_document_fetch_plan_task154.json").is_file()

    generic = _run_rzd_exact_document_fetch_plan(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--json-output",
            str(paths["intake_draft"]),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "rzd_exact_document_fetch_plan_output_must_not_equal_input"}]


def test_rzd_exact_document_fetch_plan_detects_per_input_drift_after_artifact_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _write_rzd_exact_document_fetch_plan_inputs(tmp_path)
    original_writer = assistant._rzd_exact_document_fetch_plan_write_safe_outputs
    write_count = 0

    def mutate_source_pack_after_first_write(report, artifacts):
        nonlocal write_count
        write_count += 1
        original_writer(report, artifacts)
        if write_count == 1:
            paths["source_pack"].write_bytes(paths["source_pack"].read_bytes() + b"\n")

    monkeypatch.setattr(
        assistant,
        "_rzd_exact_document_fetch_plan_write_safe_outputs",
        mutate_source_pack_after_first_write,
    )

    report = _run_rzd_exact_document_fetch_plan(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "failed"
    assert {"message": "rzd_exact_document_fetch_plan_input_drift_detected"} in report["errors"]
    assert report["gate_input_preserved"] is True
    assert report["intake_draft_input_preserved"] is True
    assert report["source_pack_input_preserved"] is False
    assert report["gate_input_bytes_unchanged"] is True
    assert report["intake_draft_input_bytes_unchanged"] is True
    assert report["source_pack_input_bytes_unchanged"] is False
    assert report["input_bytes_unchanged"] is False
    assert report["fetch_plan_rows"][0]["fetch_plan_status"] == "ready_for_future_controlled_page_fetch_preview"
    persisted = json.loads((tmp_path / "rzd_exact_document_fetch_plan_task154.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["source_pack_input_preserved"] is False
    assert persisted["input_bytes_unchanged"] is False


def test_exact_document_draft_gate_resolves_controlled_source_pack_and_unblocks_rzd_source_trust(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task149 must not fetch, probe, download, parse, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[_exact_document_draft_gate_rzd_applied_document()],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[_exact_document_draft_gate_rzd_source_trust_blocker_row()],
    )
    source_pack = _write_exact_document_draft_gate_source_pack(tmp_path)
    source_pack_before = source_pack.read_bytes()

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["source_pack_input_path"] == str(source_pack)
    assert report["source_pack_input_resolution_strategy"] == "chain_task148_controlled_source_pack"
    assert report["source_pack_trust_context_type"] == "controlled_applied_source_trust"
    assert report["controlled_source_pack_used"] is True
    assert report["controlled_source_pack_sha256"] == hashlib.sha256(source_pack_before).hexdigest()
    assert report["ready_count"] == 1
    assert report["blocked_count"] == 0
    row = report["gate_rows"][0]
    assert row["gate_status"] == "ready_for_future_controlled_download"
    assert row["trusted_source_context_found"] is True
    assert row["trusted_source_context_source"] == "controlled_source_pack_task148"
    assert row["trusted_source_context_status"] == "controlled_applied_source_trust"
    assert row["trusted_source_hosts"] == ["company.rzd.ru"]
    assert row["trusted_hosts"] == ["company.rzd.ru"]
    assert row["trusted_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert row["candidate_document_host"] == "company.rzd.ru"
    assert row["candidate_document_host_trusted_by_source_pack"] is True
    assert "controlled_source_pack_trust_context_used" in row["gate_reason_codes"]
    assert "controlled_source_host_matched" in row["gate_reason_codes"]
    assert "source_trust_recovery_task148" in row["gate_reason_codes"]
    assert "source_trust_required" not in row["apply_blocker_codes"]
    assert source_pack.read_bytes() == source_pack_before
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_exact_document_draft_gate_explicit_source_pack_input_wins(tmp_path: Path) -> None:
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[_exact_document_draft_gate_rzd_applied_document()],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[_exact_document_draft_gate_rzd_source_trust_blocker_row()],
    )
    _write_exact_document_draft_gate_source_pack(tmp_path)
    explicit = _write_exact_document_draft_gate_source_pack(
        tmp_path,
        filename="explicit_source_pack.json",
        source_trust_status="baseline_source_trust",
    )

    report = _run_exact_document_draft_gate(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--financial-official-source-pack-input",
            str(explicit),
        ]
    )

    assert report["source_pack_input_path"] == str(explicit)
    assert report["source_pack_input_resolution_strategy"] == "explicit_cli_input"
    assert report["source_pack_trust_context_type"] == "baseline_source_trust"


def test_exact_document_draft_gate_controlled_source_pack_preserves_non_source_trust_blockers(tmp_path: Path) -> None:
    _write_exact_document_draft_gate_inputs(
        tmp_path,
        documents=[
            _exact_document_draft_gate_placeholder_document(
                company_id="18",
                company_name="RZD",
                document_context_status="exact_document_url_apply_draft_for_future_gate",
                document_context_origin="operator_exact_document_refill_apply_draft_task136",
                manual_candidate_status="future_gate_validation_required",
            )
        ],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[_exact_document_draft_gate_rzd_source_trust_blocker_row()],
    )
    _write_exact_document_draft_gate_source_pack(tmp_path)

    report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(tmp_path)])

    row = report["gate_rows"][0]
    assert row["trusted_source_context_found"] is True
    assert row["gate_status"] == "blocked_missing_exact_document_url"
    assert row["apply_blocker_codes"] == []
    assert row["suppressed_apply_blocker_codes"] == ["source_trust_required"]
    assert "stale_source_trust_apply_blocker_suppressed" in row["gate_reason_codes"]
    assert [blocker["blocker_code"] for blocker in report["blocker_rows"]] == ["missing_exact_document_url"]
    assert report["blocker_rows"][0]["blocker_code"] == "missing_exact_document_url"

    with_non_source_dir = tmp_path / "non_source_blocker"
    with_non_source_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        with_non_source_dir,
        documents=[
            _exact_document_draft_gate_placeholder_document(
                company_id="18",
                company_name="RZD",
                document_context_status="exact_document_url_apply_draft_for_future_gate",
                document_context_origin="operator_exact_document_refill_apply_draft_task136",
                manual_candidate_status="future_gate_validation_required",
            )
        ],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[
            _exact_document_draft_gate_rzd_source_trust_blocker_row(),
            _exact_document_draft_gate_rzd_source_trust_blocker_row(blocker_code="missing_exact_document_url"),
        ],
    )
    _write_exact_document_draft_gate_source_pack(with_non_source_dir)

    preserved = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(with_non_source_dir)])

    preserved_row = preserved["gate_rows"][0]
    assert preserved_row["apply_blocker_codes"] == ["missing_exact_document_url"]
    assert preserved_row["suppressed_apply_blocker_codes"] == ["source_trust_required"]


def test_exact_document_draft_gate_keeps_source_trust_blockers_without_matching_trusted_host(tmp_path: Path) -> None:
    no_context_dir = tmp_path / "no_context"
    no_context_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        no_context_dir,
        documents=[_exact_document_draft_gate_rzd_applied_document()],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[_exact_document_draft_gate_rzd_source_trust_blocker_row()],
    )
    explicit = _write_exact_document_draft_gate_source_pack(
        no_context_dir,
        filename="baseline_without_rzd_trust.json",
        company_id="18",
        company_name="RZD",
        trusted_host="issuer.example",
        source_page_url="https://issuer.example/reports",
        source_trust_status="baseline_source_trust",
    )
    no_context = _run_exact_document_draft_gate(
        [
            "--operator-resolution-chain-output-dir",
            str(no_context_dir),
            "--financial-official-source-pack-input",
            str(explicit),
        ]
    )
    assert no_context["gate_rows"][0]["gate_status"] == "blocked_source_trust_required"
    assert no_context["gate_rows"][0]["candidate_document_host_trusted_by_source_pack"] is False
    assert "source_trust_required" in no_context["gate_rows"][0]["apply_blocker_codes"]
    assert no_context["gate_rows"][0]["suppressed_apply_blocker_codes"] == []
    assert no_context["blocker_rows"][0]["blocker_code"] == "candidate_document_host_not_trusted"

    untrusted_dir = tmp_path / "untrusted"
    untrusted_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        untrusted_dir,
        documents=[
            _exact_document_draft_gate_rzd_applied_document(
                document_url="https://example.com/reports/annual-ifrs-consolidated-2025.pdf"
            )
        ],
        apply_rows=[_exact_document_draft_gate_rzd_source_trust_apply_row()],
        blocker_rows=[_exact_document_draft_gate_rzd_source_trust_blocker_row()],
    )
    _write_exact_document_draft_gate_source_pack(untrusted_dir)
    untrusted = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(untrusted_dir)])
    assert untrusted["gate_rows"][0]["gate_status"] == "blocked_source_trust_required"
    assert untrusted["gate_rows"][0]["candidate_document_host"] == "example.com"
    assert untrusted["gate_rows"][0]["candidate_document_host_trusted_by_source_pack"] is False
    assert "source_trust_required" in untrusted["gate_rows"][0]["apply_blocker_codes"]
    assert untrusted["gate_rows"][0]["suppressed_apply_blocker_codes"] == []
    assert untrusted["blocker_rows"][0]["blocker_code"] == "candidate_document_host_not_trusted"


def test_rzd_exact_document_url_refill_accepts_controlled_source_pack_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task150 must not probe, fetch, download, parse, mutate, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    gate = _write_rzd_exact_document_refill_gate(tmp_path, [_rzd_exact_document_refill_gate_row()])
    source_pack = _write_exact_document_draft_gate_source_pack(tmp_path)

    report = _run_rzd_exact_document_refill(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["accepted_candidate_count"] == 1
    assert report["blocked_count"] == 0
    assert report["gate_input_sha256"] == hashlib.sha256(gate.read_bytes()).hexdigest()
    assert report["source_pack_input_sha256"] == hashlib.sha256(source_pack.read_bytes()).hexdigest()
    row = report["refill_rows"][0]
    assert row["refill_status"] == "accepted_future_exact_document_candidate"
    assert row["candidate_exact_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["candidate_exact_document_host"] == "company.rzd.ru"
    assert row["accepted_for_future_exact_document_validate"] is True
    assert row["future_validate_required"] is True
    assert row["future_apply_draft_required"] is True
    assert row["future_gate_required"] is True
    assert "rzd_exact_document_candidate_static_url_valid" in row["refill_reason_codes"]
    assert "candidate_host_matches_controlled_source_pack" in row["refill_reason_codes"]
    assert row["operator_fill_exact_document_url"] == row["candidate_exact_document_url"]
    assert row["operator_fill_document_report_type"] == "annual"
    assert row["operator_fill_document_accounting_standard"] == "IFRS"
    assert row["operator_fill_document_consolidated"] is True
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_document_intake"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False
    for filename in assistant.RZD_EXACT_DOCUMENT_REFILL_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    with (tmp_path / "rzd_exact_document_refill_task150.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert set(assistant.OPERATOR_EXACT_DOCUMENT_REFILL_TEMPLATE_FIELDS).issubset(set(reader.fieldnames or []))
        csv_row = next(reader)
    assert csv_row["operator_fill_exact_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    markdown = (tmp_path / "rzd_exact_document_refill_task150.md").read_text(encoding="utf-8")
    assert "RZD Exact Document URL Refill v2" in markdown
    assert "does not probe the URL" in markdown
    rerun = (tmp_path / "rzd_exact_document_refill_rerun_task150.md").read_text(encoding="utf-8")
    assert "operator-exact-document-refill-validate-v2" in rerun
    assert "--operator-exact-document-refill-input" in rerun


def test_rzd_exact_document_url_refill_blocks_missing_controlled_source_trust(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_gate(
        tmp_path,
        [
            _rzd_exact_document_refill_gate_row(
                trusted_source_context_found=False,
                trusted_source_context_status="",
                trusted_source_hosts=[],
                trusted_hosts=[],
                trusted_source_page_url="",
            )
        ],
    )
    _write_exact_document_draft_gate_source_pack(tmp_path, trusted_host="issuer.example")

    report = _run_rzd_exact_document_refill(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    row = report["refill_rows"][0]
    assert row["refill_status"] == "blocked_rzd_controlled_source_trust_context_missing"
    assert report["blocker_rows"][0]["blocker_code"] == "rzd_controlled_source_trust_context_missing"


def test_rzd_exact_document_url_refill_blocks_untrusted_host_and_direct_pdf(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_gate(tmp_path, [_rzd_exact_document_refill_gate_row()])
    _write_exact_document_draft_gate_source_pack(tmp_path)

    untrusted = _run_rzd_exact_document_refill(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-url",
            "https://example.com/rzd-report-2025",
        ]
    )
    assert untrusted["refill_rows"][0]["refill_status"] == "blocked_candidate_host_not_trusted"
    assert untrusted["blocker_rows"][0]["blocker_code"] == "candidate_host_not_trusted"

    direct_pdf = _run_rzd_exact_document_refill(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-url",
            "https://company.rzd.ru/report.pdf",
        ]
    )
    assert direct_pdf["refill_rows"][0]["refill_status"] == "blocked_candidate_url_direct_pdf_not_allowed_in_refill"
    assert direct_pdf["blocker_rows"][0]["blocker_code"] == "candidate_url_direct_pdf_not_allowed_in_refill"

    query_pdf = _run_rzd_exact_document_refill(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-url",
            "https://company.rzd.ru/ru/9397/page/104069?id=322745.pdf",
        ]
    )
    assert query_pdf["blocker_rows"][0]["blocker_code"] == "candidate_url_direct_pdf_not_allowed_in_refill"


def test_rzd_exact_document_url_refill_blocks_unrelated_gate_status_and_output_collision(tmp_path: Path) -> None:
    gate = _write_rzd_exact_document_refill_gate(
        tmp_path,
        [_rzd_exact_document_refill_gate_row(gate_status="blocked_wrong_period", gate_reason_codes=["wrong_period"])],
    )
    _write_exact_document_draft_gate_source_pack(tmp_path)

    report = _run_rzd_exact_document_refill(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["refill_rows"][0]["refill_status"] == "blocked_rzd_gate_not_waiting_for_exact_document_url"
    assert report["blocker_rows"][0]["blocker_code"] == "rzd_gate_not_waiting_for_exact_document_url"

    collision = _run_rzd_exact_document_refill(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-refill-output",
            str(gate),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"][0]["message"] == "rzd_exact_document_refill_output_must_not_equal_input"


def test_rzd_exact_document_refill_validate_accepts_task150_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task151 must not probe, fetch, download, parse, mutate, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    paths = _write_rzd_exact_document_refill_validation_inputs(tmp_path)

    report = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["valid_candidate_count"] == 1
    assert report["accepted_candidate_count"] == 1
    assert report["blocked_count"] == 0
    assert report["refill_input_sha256"] == hashlib.sha256(paths["refill"].read_bytes()).hexdigest()
    assert report["gate_input_sha256"] == hashlib.sha256(paths["gate"].read_bytes()).hexdigest()
    assert report["source_pack_input_sha256"] == hashlib.sha256(paths["source_pack"].read_bytes()).hexdigest()
    row = report["validation_rows"][0]
    assert row["validation_status"] == "valid_future_exact_document_candidate"
    assert row["candidate_exact_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["candidate_exact_document_host"] == "company.rzd.ru"
    assert row["accepted_for_future_exact_document_apply_draft"] is True
    assert row["future_apply_draft_required"] is True
    assert row["future_gate_required"] is True
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    accepted = report["accepted_candidate_rows"][0]
    assert accepted["accepted_candidate_status"] == "future_exact_document_apply_draft_candidate_only"
    assert accepted["official_document_page_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert accepted["official_document_page_host"] == "company.rzd.ru"
    assert accepted["would_download_document"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_document_intake"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False
    for filename in assistant.RZD_EXACT_DOCUMENT_REFILL_VALIDATION_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    markdown = (tmp_path / "rzd_exact_document_refill_validation_task151.md").read_text(encoding="utf-8")
    assert "RZD Exact Document Refill Validate v2" in markdown
    assert "does not probe the URL" in markdown
    rerun = (tmp_path / "rzd_exact_document_refill_validation_rerun_task151.md").read_text(encoding="utf-8")
    assert "rzd-exact-document-refill-apply-draft-v2" in rerun


def test_rzd_exact_document_refill_validate_blocks_task150_not_accepted(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_validation_inputs(tmp_path)
    _update_rzd_exact_document_refill_report(
        tmp_path,
        refill_status="blocked_candidate_host_not_trusted",
        accepted_for_future_exact_document_validate=False,
        future_validate_required=False,
        future_apply_draft_required=False,
        future_gate_required=False,
    )

    report = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["validation_rows"][0]["validation_status"] == "blocked_task150_candidate_not_accepted"
    assert report["blocker_rows"][0]["blocker_code"] == "task150_candidate_not_accepted"
    assert report["accepted_candidate_rows"] == []


def test_rzd_exact_document_refill_validate_blocks_untrusted_host_and_direct_pdf(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_validation_inputs(tmp_path)
    _update_rzd_exact_document_refill_report(
        tmp_path,
        candidate_exact_document_url="https://example.com/rzd-report-2025",
        candidate_exact_document_host="example.com",
        operator_fill_exact_document_url="https://example.com/rzd-report-2025",
    )

    untrusted = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert untrusted["validation_rows"][0]["validation_status"] == "blocked_candidate_host_not_trusted"
    assert untrusted["blocker_rows"][0]["blocker_code"] == "candidate_host_not_trusted"

    _update_rzd_exact_document_refill_report(
        tmp_path,
        candidate_exact_document_url="https://company.rzd.ru/report.pdf",
        candidate_exact_document_host="company.rzd.ru",
        operator_fill_exact_document_url="https://company.rzd.ru/report.pdf",
    )
    direct_pdf = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert direct_pdf["validation_rows"][0]["validation_status"] == "blocked_candidate_url_direct_pdf_not_allowed"
    assert direct_pdf["blocker_rows"][0]["blocker_code"] == "candidate_url_direct_pdf_not_allowed"

    _update_rzd_exact_document_refill_report(
        tmp_path,
        candidate_exact_document_url="https://company.rzd.ru/ru/9397/page/104069?id=322745.pdf",
        operator_fill_exact_document_url="https://company.rzd.ru/ru/9397/page/104069?id=322745.pdf",
    )
    query_pdf = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert query_pdf["blocker_rows"][0]["blocker_code"] == "candidate_url_direct_pdf_not_allowed"


def test_rzd_exact_document_refill_validate_blocks_metadata_mismatches(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_validation_inputs(tmp_path)
    _update_rzd_exact_document_refill_report(tmp_path, candidate_exact_document_year="2024")
    wrong_year = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert wrong_year["blocker_rows"][0]["blocker_code"] == "candidate_year_mismatch"

    _update_rzd_exact_document_refill_report(
        tmp_path,
        candidate_exact_document_year="2025",
        candidate_exact_document_standard="RAS",
        operator_fill_document_accounting_standard="RAS",
    )
    wrong_standard = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert wrong_standard["blocker_rows"][0]["blocker_code"] == "candidate_standard_mismatch"

    _update_rzd_exact_document_refill_report(
        tmp_path,
        candidate_exact_document_standard="IFRS",
        operator_fill_document_accounting_standard="IFRS",
        candidate_exact_document_consolidated=False,
        operator_fill_document_consolidated=False,
    )
    non_consolidated = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert non_consolidated["blocker_rows"][0]["blocker_code"] == "candidate_consolidated_mismatch"


def test_rzd_exact_document_refill_validate_blocks_gate_mismatch_and_unsafe_flags(tmp_path: Path) -> None:
    _write_rzd_exact_document_refill_validation_inputs(tmp_path)
    _update_rzd_exact_document_refill_report(tmp_path, gate_status_before_refill="blocked_wrong_period")

    wrong_gate = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert wrong_gate["validation_rows"][0]["validation_status"] == "blocked_gate_not_waiting_for_exact_document_url"
    assert wrong_gate["blocker_rows"][0]["blocker_code"] == "gate_not_waiting_for_exact_document_url"

    _update_rzd_exact_document_refill_report(tmp_path, gate_status_before_refill="blocked_missing_exact_document_url", would_fetch_url=True)
    unsafe = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert unsafe["validation_rows"][0]["validation_status"] == "blocked_candidate_safety_flags"
    assert unsafe["blocker_rows"][0]["blocker_code"] == "candidate_safety_flags"


def test_rzd_exact_document_refill_validate_missing_inputs_and_output_collision(tmp_path: Path) -> None:
    missing = _run_rzd_exact_document_refill_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert missing["status"] == "failed"
    messages = {error["message"] for error in missing["errors"]}
    assert "rzd_exact_document_refill_validation_input_required" in messages
    assert "rzd_exact_document_refill_validation_gate_input_required" in messages
    assert "rzd_exact_document_refill_validation_source_pack_input_required" in messages
    assert (tmp_path / "rzd_exact_document_refill_validation_task151.json").is_file()
    assert not (tmp_path / "rzd_exact_document_refill_accepted_candidates_task151.json").read_text(encoding="utf-8").count(
        "future_exact_document_apply_draft_candidate_only"
    )

    paths = _write_rzd_exact_document_refill_validation_inputs(tmp_path)
    collision = _run_rzd_exact_document_refill_validation(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--rzd-exact-document-refill-validation-output",
            str(paths["refill"]),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"][0]["message"] == "rzd_exact_document_refill_validation_output_must_not_equal_input"


def test_rzd_exact_document_refill_apply_creates_safe_draft(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task152 must not probe, fetch, download, parse, mutate production state, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    paths = _write_rzd_exact_document_refill_apply_inputs(tmp_path)
    snapshots = {role: path.read_bytes() for role, path in paths.items()}

    report = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["draft_applied_count"] == 1
    assert report["blocked_count"] == 0
    assert report["document_intake_draft_created"] is True
    row = report["apply_rows"][0]
    assert row["apply_status"] == "draft_exact_document_candidate_applied"
    assert row["draft_candidate_exact_document_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert row["draft_document_context_status"] == "exact_document_url_apply_draft_for_future_gate"
    assert row["future_gate_required"] is True
    assert row["future_fetch_plan_required"] is True
    assert row["future_download_required"] is False
    assert row["future_parse_required"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    assert row["would_mutate_production_document_intake"] is False
    assert row["would_create_document_intake_draft"] is True
    draft_path = tmp_path / "rzd_exact_document_intake_draft_task152.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    document = draft["documents"][0]
    assert document["official_document_page_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert document["candidate_exact_document_url"] == document["official_document_page_url"]
    assert document["document_url"] == document["official_document_page_url"]
    assert document["exact_document_url"] == document["official_document_page_url"]
    assert document["document_context_status"] == "exact_document_url_apply_draft_for_future_gate"
    assert document["document_context_origin"] == "rzd_exact_document_refill_apply_draft_task152"
    for field in (
        "ready_for_document_download",
        "ready_for_extraction",
        "ready_for_import",
        "ready_for_scoring",
        "ready_for_paper_trading",
        "download_allowed",
        "parse_allowed",
        "import_allowed",
    ):
        assert document[field] is False
    for role, path in paths.items():
        assert path.read_bytes() == snapshots[role]
    for filename in assistant.RZD_EXACT_DOCUMENT_REFILL_APPLY_ARTIFACT_NAMES.values():
        assert (tmp_path / filename).is_file()
    markdown = (tmp_path / "rzd_exact_document_refill_apply_draft_task152.md").read_text(encoding="utf-8")
    assert "RZD Exact Document Refill Apply Draft v2" in markdown
    assert "does not probe the URL" in markdown
    rerun = (tmp_path / "rzd_exact_document_refill_apply_rerun_task152.md").read_text(encoding="utf-8")
    assert "--exact-document-draft-gate-input" in rerun
    assert "Not run by Task152" in rerun


def test_rzd_exact_document_refill_apply_blocks_missing_and_invalid_upstream_rows(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(missing_dir)
    accepted_payload = json.loads(paths["accepted_candidates"].read_text(encoding="utf-8"))
    accepted_payload["accepted_candidate_rows"] = []
    paths["accepted_candidates"].write_text(json.dumps(accepted_payload, indent=2) + "\n", encoding="utf-8")
    missing = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(missing_dir)])
    assert missing["apply_rows"][0]["apply_status"] == "blocked_accepted_candidate_missing"
    assert missing["blocker_rows"][0]["blocker_code"] == "accepted_candidate_missing"
    assert missing["document_intake_draft_created"] is False
    assert not (missing_dir / "rzd_exact_document_intake_draft_task152.json").exists()

    invalid_dir = tmp_path / "invalid"
    invalid_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(invalid_dir)
    _update_json_first_row(
        paths["validation"],
        "validation_rows",
        validation_status="blocked_candidate_year_mismatch",
    )
    invalid = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(invalid_dir)])
    assert invalid["apply_rows"][0]["apply_status"] == "blocked_validation_not_valid"
    assert invalid["blocker_rows"][0]["blocker_code"] == "validation_not_valid"

    refill_dir = tmp_path / "refill"
    refill_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(refill_dir)
    _update_json_first_row(paths["refill"], "refill_rows", refill_status="blocked_candidate_host_not_trusted")
    refill = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(refill_dir)])
    assert refill["apply_rows"][0]["apply_status"] == "blocked_task150_refill_not_accepted"
    assert refill["blocker_rows"][0]["blocker_code"] == "task150_refill_not_accepted"


def test_rzd_exact_document_refill_apply_blocks_gate_host_pdf_and_unsafe_flags(tmp_path: Path) -> None:
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(gate_dir)
    _update_json_first_row(paths["gate"], "gate_rows", gate_status="blocked_wrong_period")
    gate = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(gate_dir)])
    assert gate["apply_rows"][0]["apply_status"] == "blocked_gate_not_waiting_for_exact_document_url"

    trust_dir = tmp_path / "trust"
    trust_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(trust_dir)
    _update_json_first_row(paths["source_pack"], "resolutions", trusted_source_hosts=[], trusted_hosts=[])
    trust = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(trust_dir)])
    assert trust["apply_rows"][0]["apply_status"] == "blocked_controlled_source_trust_context_missing"

    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(pdf_dir)
    _update_json_first_row(
        paths["accepted_candidates"],
        "accepted_candidate_rows",
        official_document_page_url="https://company.rzd.ru/report.pdf",
    )
    pdf = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(pdf_dir)])
    assert pdf["apply_rows"][0]["apply_status"] == "blocked_candidate_url_direct_pdf_not_allowed"

    unsafe_dir = tmp_path / "unsafe"
    unsafe_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(unsafe_dir)
    _update_json_first_row(paths["accepted_candidates"], "accepted_candidate_rows", would_download_document=True)
    unsafe = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(unsafe_dir)])
    assert unsafe["apply_rows"][0]["apply_status"] == "blocked_candidate_safety_flags"


def test_rzd_exact_document_refill_apply_preserves_container_shapes_and_is_idempotent(tmp_path: Path) -> None:
    candidate_url = "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    for shape in ("documents", "rows", "raw"):
        case_dir = tmp_path / shape
        case_dir.mkdir()
        _write_rzd_exact_document_refill_apply_inputs(case_dir)
        row = {
            "company_id": "18",
            "company_name": "RZD",
            "canonical_company_id": "18",
            "canonical_company_name": "RZD",
            "report_period": "2025",
            "report_type": "annual",
            "accounting_standard": "IFRS",
            "document_url": candidate_url,
        }
        payload: object = [row] if shape == "raw" else {shape: [row], "preserved": True}
        base = case_dir / f"base-{shape}.json"
        base.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        base_before = base.read_bytes()

        report = _run_rzd_exact_document_refill_apply(
            [
                "--operator-resolution-chain-output-dir",
                str(case_dir),
                "--operator-exact-document-apply-draft-base-input",
                str(base),
            ]
        )

        assert report["status"] == "passed"
        assert report["apply_rows"][0]["apply_status"] == "draft_exact_document_candidate_already_present"
        output = json.loads((case_dir / "rzd_exact_document_intake_draft_task152.json").read_text(encoding="utf-8"))
        output_rows = output if shape == "raw" else output[shape]
        assert output_rows[0]["candidate_exact_document_url"] == candidate_url
        assert base.read_bytes() == base_before
        if shape != "raw":
            assert output["preserved"] is True


def test_rzd_exact_document_refill_apply_prefers_task136_and_blocks_conflict(tmp_path: Path) -> None:
    paths = _write_rzd_exact_document_refill_apply_inputs(tmp_path)
    canonical = tmp_path / "operator_exact_document_intake_apply_draft_task136.json"
    canonical.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "company_id": "18",
                        "canonical_company_id": "18",
                        "report_period": "2025",
                        "report_type": "annual",
                        "accounting_standard": "IFRS",
                        "document_url": "https://example.com/conflict",
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "operator_exact_document_refill_workspace_task134.json").write_text(
        json.dumps({"workspace_rows": [_rzd_exact_document_refill_gate_row()]}, indent=2) + "\n",
        encoding="utf-8",
    )

    report = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["base_draft_input_path"] == str(canonical)
    assert report["apply_rows"][0]["apply_status"] == "blocked_duplicate_conflicting_draft_row"
    assert report["blocker_rows"][0]["blocker_code"] == "duplicate_conflicting_draft_row"
    assert report["document_intake_draft_created"] is False
    assert canonical.read_bytes()
    assert paths["source_pack"].is_file()

    workspace_dir = tmp_path / "workspace-fallback"
    workspace_dir.mkdir()
    _write_rzd_exact_document_refill_apply_inputs(workspace_dir)
    workspace = workspace_dir / "operator_exact_document_refill_workspace_task134.json"
    workspace.write_text(
        json.dumps(
            {
                "mode": "operator-exact-document-refill-workspace-v2",
                "workspace_rows": [
                    {
                        "workspace_id": "operator_exact_document_refill:rzd",
                        "company_id": "18",
                        "company_name": "RZD",
                        "canonical_company_id": "18",
                        "canonical_company_name": "RZD",
                        "target_reporting_period": "2025",
                        "required_report_type": "annual",
                        "required_standard": "IFRS",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace_report = _run_rzd_exact_document_refill_apply(
        ["--operator-resolution-chain-output-dir", str(workspace_dir)]
    )
    assert workspace_report["status"] == "passed"
    assert workspace_report["base_draft_input_path"] == str(workspace)
    assert workspace_report["apply_rows"][0]["draft_row_updated"] is True


def test_rzd_exact_document_refill_apply_required_failures_and_collisions(tmp_path: Path) -> None:
    missing = _run_rzd_exact_document_refill_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert missing["status"] == "failed"
    messages = {error["message"] for error in missing["errors"]}
    assert "rzd_exact_document_refill_apply_validation_input_required" in messages
    assert "rzd_exact_document_refill_apply_accepted_candidates_input_required" in messages
    assert "rzd_exact_document_refill_apply_refill_input_required" in messages
    assert "rzd_exact_document_refill_apply_gate_input_required" in messages
    assert "rzd_exact_document_refill_apply_source_pack_input_required" in messages
    assert not (tmp_path / "rzd_exact_document_intake_draft_task152.json").exists()
    for role in ("apply_json", "apply_csv", "apply_markdown", "blockers_json", "blockers_csv", "intake_draft_summary_csv", "rerun_markdown"):
        assert (tmp_path / assistant.RZD_EXACT_DOCUMENT_REFILL_APPLY_ARTIFACT_NAMES[role]).is_file()

    collision_dir = tmp_path / "collision"
    collision_dir.mkdir()
    paths = _write_rzd_exact_document_refill_apply_inputs(collision_dir)
    collision = _run_rzd_exact_document_refill_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(collision_dir),
            "--rzd-exact-document-refill-apply-output",
            str(paths["validation"]),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"][0]["message"] == "rzd_exact_document_refill_apply_output_must_not_equal_input"

    draft_collision = _run_rzd_exact_document_refill_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(collision_dir),
            "--rzd-exact-document-intake-draft-output",
            str(paths["source_pack"]),
        ]
    )
    assert draft_collision["status"] == "failed"
    assert draft_collision["errors"][0]["message"] == "rzd_exact_document_refill_apply_draft_must_not_overwrite_input"


def test_exact_document_draft_gate_blocks_leak_and_disk_guard(tmp_path: Path) -> None:
    leak_dir = tmp_path / "leak"
    leak_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        leak_dir,
        documents=[_exact_document_draft_gate_applied_document(ready_for_value_extraction=True)],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
    )
    leak = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(leak_dir)])
    assert leak["gate_rows"][0]["gate_status"] == "blocked_downstream_ready_flag_leak"
    assert leak["blocker_rows"][0]["blocker_code"] == "downstream_ready_flag_leak"

    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        blocked_dir,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
        retention_status="blocked",
    )
    blocked = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(blocked_dir)])
    assert blocked["gate_rows"][0]["gate_status"] == "blocked_disk_guard"
    assert blocked["ready_count"] == 0

    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        missing_dir,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
        write_retention=False,
    )
    missing = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(missing_dir)])
    assert missing["gate_rows"][0]["gate_status"] == "blocked_disk_guard"
    assert {"message": "exact_document_draft_gate_retention_policy_missing_using_safe_defaults"} in missing["warnings"]


def test_exact_document_draft_gate_disk_warning_allows_future_ready_and_malformed_retention_fails(tmp_path: Path) -> None:
    warning_dir = tmp_path / "warning"
    warning_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        warning_dir,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
        retention_status="warning",
    )
    warning = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(warning_dir)])
    assert warning["status"] == "warning"
    assert warning["ready_count"] == 1
    assert warning["gate_rows"][0]["gate_status"] == "ready_for_future_controlled_download"

    malformed_dir = tmp_path / "malformed"
    malformed_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        malformed_dir,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[_exact_document_draft_gate_apply_row()],
        blocker_rows=[],
    )
    retention = malformed_dir / "document_artifact_retention_policy_task132.json"
    retention.write_text("{broken", encoding="utf-8")
    malformed = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(malformed_dir)])
    assert malformed["status"] == "failed"
    assert malformed["errors"] == [{"message": "document_artifact_retention_input_invalid", "path": str(retention)}]


def test_exact_document_draft_gate_blocks_strict_mismatches_and_missing_context(tmp_path: Path) -> None:
    cases = [
        (
            "period",
            _exact_document_draft_gate_applied_document(
                report_period="2024",
                document_url="https://mostotrest.ru/reports/annual-ifrs-consolidated-2024.pdf",
            ),
            _exact_document_draft_gate_apply_row(target_reporting_period="2024"),
            "blocked_wrong_period",
        ),
        (
            "type",
            _exact_document_draft_gate_applied_document(report_type="interim"),
            _exact_document_draft_gate_apply_row(required_report_type="interim"),
            "blocked_wrong_report_type",
        ),
        (
            "standard",
            _exact_document_draft_gate_applied_document(accounting_standard="RAS"),
            _exact_document_draft_gate_apply_row(required_standard="RAS"),
            "blocked_wrong_standard",
        ),
        (
            "consolidated",
            _exact_document_draft_gate_applied_document(document_consolidated="false"),
            _exact_document_draft_gate_apply_row(),
            "blocked_non_consolidated",
        ),
        (
            "origin",
            _exact_document_draft_gate_applied_document(document_context_origin="unexpected_origin"),
            _exact_document_draft_gate_apply_row(),
            "blocked_draft_not_from_task136",
        ),
    ]
    for name, document, apply_row, expected in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        _write_exact_document_draft_gate_inputs(
            case_dir,
            documents=[document],
            apply_rows=[apply_row],
            blocker_rows=[],
        )
        report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(case_dir)])
        assert report["gate_rows"][0]["gate_status"] == expected

    missing_apply_dir = tmp_path / "missing_apply"
    missing_apply_dir.mkdir()
    _write_exact_document_draft_gate_inputs(
        missing_apply_dir,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[],
        blocker_rows=[],
    )
    missing_apply = _run_exact_document_draft_gate(
        ["--operator-resolution-chain-output-dir", str(missing_apply_dir)]
    )
    assert missing_apply["gate_rows"][0]["gate_status"] == "blocked_unknown_readiness"


def test_exact_document_draft_gate_collisions_and_invalid_input_fail_safely(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text("{broken", encoding="utf-8")
    invalid = _run_exact_document_draft_gate(["--exact-document-draft-gate-input", str(draft)])
    assert invalid["status"] == "failed"
    assert invalid["errors"] == [{"message": "exact_document_draft_gate_input_invalid", "path": str(draft)}]

    _write_document_intake(draft, [_exact_document_draft_gate_placeholder_document()])
    original = draft.read_bytes()
    collision = _run_exact_document_draft_gate(
        [
            "--exact-document-draft-gate-input",
            str(draft),
            "--exact-document-draft-gate-output",
            str(draft),
        ]
    )
    assert collision["status"] == "failed"
    assert collision["errors"] == [{"message": "exact_document_draft_gate_output_must_not_equal_input"}]
    assert draft.read_bytes() == original

    generic = _run_exact_document_draft_gate(
        [
            "--exact-document-draft-gate-input",
            str(draft),
            "--json-output",
            str(draft),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "exact_document_draft_gate_output_must_not_equal_input"}]


def test_source_trust_recovery_vds_like_split_writes_artifacts(tmp_path: Path) -> None:
    _write_source_trust_recovery_gate(
        tmp_path,
        [
            _source_trust_recovery_gate_row(
                company_id="18",
                company_name="RZD",
                canonical_company_id="18",
                canonical_company_name="RZD",
                gate_id="exact_document_draft_gate:18:2025",
            ),
            _source_trust_recovery_gate_row(
                gate_id="exact_document_draft_gate:67:2025",
                gate_status="blocked_incomplete_operator_refill",
                gate_reason_codes=["operator_refill_incomplete"],
                apply_status="skipped_incomplete_missing_exact_document_url",
                apply_blocker_codes=["missing_exact_document_url"],
            ),
        ],
        [
            _source_trust_recovery_blocker_row(
                gate_id="exact_document_draft_gate:18:2025",
                company_id="18",
                company_name="RZD",
                blocker_code="source_trust_required",
            ),
            _source_trust_recovery_blocker_row(
                gate_id="exact_document_draft_gate:67:2025",
                blocker_code="operator_refill_incomplete",
            ),
        ],
    )

    report = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["row_count"] == 2
    assert report["recovery_candidate_count"] == 1
    assert report["template_row_count"] == 1
    assert report["skipped_count"] == 1
    statuses = {row["company_id"]: row["recovery_status"] for row in report["recovery_rows"]}
    assert statuses["18"] == "needs_official_source_page_refill"
    assert statuses["67"] == "skipped_exact_document_refill_needed"
    assert report["template_rows"][0]["company_id"] == "18"
    assert report["template_rows"][0]["operator_fill_official_source_page_url"] == ""
    for key in assistant.SOURCE_TRUST_RECOVERY_ARTIFACT_NAMES:
        assert Path(report["artifacts"][key]).is_file()
    markdown = (tmp_path / "source_trust_recovery_workspace_task142.md").read_text(encoding="utf-8")
    assert "Source Trust Recovery Workspace v2" in markdown
    assert "does not probe URLs" in markdown
    rerun = (tmp_path / "source_trust_recovery_rerun_task142.md").read_text(encoding="utf-8")
    assert "source-trust-recovery-validate-v2" in rerun
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_update_source_pack"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_skips_when_trusted_source_already_available(tmp_path: Path) -> None:
    _write_source_trust_recovery_gate(
        tmp_path,
        [
            _source_trust_recovery_gate_row(
                gate_status="blocked_incomplete_operator_refill",
                gate_reason_codes=["operator_refill_incomplete"],
                apply_status="skipped_incomplete_missing_exact_document_url",
                trusted_source_hosts=["mostotrest.ru"],
            )
        ],
    )

    report = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["recovery_rows"][0]["recovery_status"] == "skipped_source_trust_already_available"
    assert report["blocker_rows"][0]["blocker_code"] == "source_trust_already_available"


def test_source_trust_recovery_skips_non_source_trust_gate_status(tmp_path: Path) -> None:
    _write_source_trust_recovery_gate(
        tmp_path,
        [
            _source_trust_recovery_gate_row(
                gate_status="blocked_wrong_period",
                gate_reason_codes=["wrong_period"],
                apply_status="applied_to_exact_document_intake_draft",
            )
        ],
    )

    report = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["recovery_rows"][0]["recovery_status"] == "skipped_not_blocked_by_source_trust"
    assert report["blocker_rows"][0]["blocker_code"] == "not_blocked_by_source_trust"


def test_source_trust_recovery_missing_company_identity_blocks(tmp_path: Path) -> None:
    _write_source_trust_recovery_gate(
        tmp_path,
        [
            _source_trust_recovery_gate_row(
                company_id="",
                company_name="",
                canonical_company_id="",
                canonical_company_name="",
            )
        ],
    )

    report = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["recovery_rows"][0]["recovery_status"] == "blocked_missing_company_identity"
    assert report["blocker_rows"][0]["blocker_code"] == "missing_company_identity"
    assert report["blocked_count"] == 1


def test_source_trust_recovery_missing_or_malformed_gate_fails_safely(tmp_path: Path) -> None:
    missing = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert missing["status"] == "failed"
    assert missing["errors"] == [{"message": "source_trust_recovery_gate_input_required"}]

    gate = tmp_path / "bad_gate.json"
    gate.write_text("{broken", encoding="utf-8")
    malformed = _run_source_trust_recovery(["--exact-document-draft-gate-input", str(gate)])
    assert malformed["status"] == "failed"
    assert malformed["errors"] == [{"message": "source_trust_recovery_gate_input_required", "path": str(gate)}]


def test_source_trust_recovery_output_collisions_fail_safely(tmp_path: Path) -> None:
    _write_source_trust_recovery_gate(tmp_path, [_source_trust_recovery_gate_row()])
    gate = tmp_path / "exact_document_draft_gate_task137.json"
    original = gate.read_bytes()

    dedicated = _run_source_trust_recovery(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-output",
            str(gate),
        ]
    )
    assert dedicated["status"] == "failed"
    assert dedicated["errors"] == [{"message": "source_trust_recovery_output_must_not_equal_input"}]
    assert gate.read_bytes() == original

    generic = _run_source_trust_recovery(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--json-output",
            str(gate),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "source_trust_recovery_output_must_not_equal_input"}]
    assert gate.read_bytes() == original


def test_source_trust_recovery_never_calls_network_or_delete_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task142 must not probe, fetch, download, parse, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _write_source_trust_recovery_gate(tmp_path, [_source_trust_recovery_gate_row()])

    report = _run_source_trust_recovery(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] in {"passed", "warning"}
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_update_source_pack"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_validation_unfilled_rzd_is_incomplete(tmp_path: Path) -> None:
    _write_source_trust_recovery_validation_inputs(tmp_path)

    report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "warning"
    assert report["row_count"] == 1
    assert report["valid_candidate_count"] == 0
    assert report["accepted_candidate_count"] == 0
    assert report["incomplete_count"] == 1
    assert report["blocker_row_count"] == 1
    assert report["validation_rows"][0]["validation_status"] == "incomplete_missing_official_source_page_url"
    assert report["blocker_rows"][0]["blocker_code"] == "missing_official_source_page_url"
    for key in assistant.SOURCE_TRUST_RECOVERY_VALIDATION_ARTIFACT_NAMES:
        assert Path(report["artifacts"][key]).is_file()
    markdown = (tmp_path / "source_trust_recovery_validation_task143.md").read_text(encoding="utf-8")
    assert "Source Trust Recovery Validation v2" in markdown
    assert "does not probe URLs" in markdown
    rerun = (tmp_path / "source_trust_recovery_validation_rerun_task143.md").read_text(encoding="utf-8")
    assert "source-trust-recovery-apply-draft-v2" in rerun


def test_source_trust_recovery_validation_accepts_rzd_source_page_candidate_only(tmp_path: Path) -> None:
    _write_source_trust_recovery_validation_inputs(
        tmp_path,
        template_updates={"operator_fill_official_source_page_url": "https://company.rzd.ru/ru/ir/reports"},
    )

    report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    row = report["validation_rows"][0]
    assert row["validation_status"] == "valid_future_source_page_candidate"
    assert row["accepted_for_future_source_pack_draft"] is True
    assert row["would_trust_source_url"] is False
    assert row["would_update_source_pack"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert report["accepted_candidate_count"] == 1
    accepted = report["accepted_candidate_rows"][0]
    assert accepted["official_source_page_url"] == "https://company.rzd.ru/ru/ir/reports"
    assert accepted["accepted_candidate_status"] == "future_source_pack_draft_candidate_only"
    assert accepted["would_trust_source_url"] is False
    assert accepted["would_update_source_pack"] is False


def test_source_trust_recovery_validation_accepts_rzd_reporting_hub_candidate_only(tmp_path: Path) -> None:
    _write_source_trust_recovery_validation_inputs(
        tmp_path,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9471",
            "operator_fill_source_page_title": "Отчетность РЖД",
            "operator_fill_source_page_language": "ru",
            "operator_fill_source_page_notes": (
                "Official RZD reporting hub/source page with links to IFRS/RAS reporting materials; "
                "source trust candidate only."
            ),
        },
    )

    report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] in {"passed", "warning"}
    assert report["row_count"] == 1
    assert report["valid_candidate_count"] == 1
    assert report["accepted_candidate_count"] == 1
    assert report["invalid_count"] == 0
    assert report["blocker_row_count"] == 0
    row = report["validation_rows"][0]
    assert row["validation_status"] == "valid_future_source_page_candidate"
    assert row["accepted_for_future_source_pack_draft"] is True
    assert "official_reporting_hub_source_page_candidate" in row["validation_reason_codes"]
    assert row["url_shape_validation_status"] == "official_reporting_hub_source_page_candidate"
    assert row["host_validation_status"] == "official_host_candidate"
    assert row["source_page_type_validation_status"] == "source_page_like"
    assert row["source_page_host"] == "company.rzd.ru"
    assert row["source_page_path"] == "/ru/9471"
    assert row["source_page_registrable_domain"] == "rzd.ru"
    assert row["would_trust_source_url"] is False
    assert row["would_update_source_pack"] is False
    assert row["would_update_document_intake"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False
    accepted = report["accepted_candidate_rows"][0]
    assert accepted["official_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert "official_reporting_hub_source_page_candidate" in accepted["accepted_candidate_reason_codes"]
    assert accepted["accepted_candidate_status"] == "future_source_pack_draft_candidate_only"
    assert accepted["accepted_for_future_source_pack_draft"] is True
    assert accepted["future_strict_validation_required"] is True
    assert accepted["future_source_pack_apply_draft_required"] is True
    assert accepted["would_trust_source_url"] is False
    assert accepted["would_update_source_pack"] is False
    assert accepted["would_update_document_intake"] is False
    assert accepted["would_probe_url"] is False
    assert accepted["would_fetch_url"] is False
    assert accepted["would_download_document"] is False
    assert accepted["would_parse_document"] is False


def test_source_trust_recovery_validation_rejects_reporting_hub_without_static_evidence(tmp_path: Path) -> None:
    blank_context_dir = tmp_path / "blank_context"
    _write_source_trust_recovery_validation_inputs(
        blank_context_dir,
        template_updates={"operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9471"},
    )
    blank_context = _run_source_trust_recovery_validation(
        ["--operator-resolution-chain-output-dir", str(blank_context_dir)]
    )
    assert blank_context["validation_rows"][0]["validation_status"] == "invalid_generic_landing_page_url"
    assert blank_context["blocker_rows"][0]["blocker_code"] == "generic_landing_page_not_allowed"

    home_dir = tmp_path / "home"
    _write_source_trust_recovery_validation_inputs(
        home_dir,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/",
            "operator_fill_source_page_title": "Отчетность РЖД",
            "operator_fill_source_page_notes": "Official RZD reporting hub/source page.",
        },
    )
    home = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(home_dir)])
    assert home["validation_rows"][0]["validation_status"] == "invalid_generic_landing_page_url"
    assert home["blocker_rows"][0]["blocker_code"] == "generic_landing_page_not_allowed"


def test_source_trust_recovery_validation_accepts_rzd_numeric_cms_source_page_candidate_only(tmp_path: Path) -> None:
    _write_source_trust_recovery_validation_inputs(
        tmp_path,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9397/page/104069?id=322745",
            "operator_fill_source_page_title": "Отчетность РЖД по МСФО за 2025 год",
            "operator_fill_source_page_language": "ru",
            "operator_fill_source_page_notes": (
                "Official RZD company page with IFRS 2025 reporting materials; "
                "non-PDF source page, candidate only."
            ),
        },
    )

    report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] in {"passed", "warning"}
    assert report["row_count"] == 1
    assert report["valid_candidate_count"] == 1
    assert report["accepted_candidate_count"] == 1
    assert report["invalid_count"] == 0
    assert report["blocker_row_count"] == 0
    row = report["validation_rows"][0]
    assert row["validation_status"] == "valid_future_source_page_candidate"
    assert row["accepted_for_future_source_pack_draft"] is True
    assert "official_numeric_cms_source_page_candidate" in row["validation_reason_codes"]
    assert "static_officiality_review_required" in row["validation_warnings"]
    assert row["url_shape_validation_status"] == "official_numeric_cms_source_page_candidate"
    assert row["host_validation_status"] == "official_host_candidate"
    assert row["source_page_type_validation_status"] == "source_page_like"
    assert row["would_trust_source_url"] is False
    assert row["would_update_source_pack"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    accepted = report["accepted_candidate_rows"][0]
    assert accepted["official_source_page_url"] == "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    assert "official_numeric_cms_source_page_candidate" in accepted["accepted_candidate_reason_codes"]
    assert accepted["accepted_for_future_source_pack_draft"] is True
    assert accepted["future_strict_validation_required"] is True
    assert accepted["future_source_pack_apply_draft_required"] is True
    assert accepted["would_trust_source_url"] is False
    assert accepted["would_update_source_pack"] is False
    assert accepted["would_probe_url"] is False
    assert accepted["would_fetch_url"] is False


def test_source_trust_recovery_validation_rejects_numeric_cms_without_static_evidence(tmp_path: Path) -> None:
    blank_context_dir = tmp_path / "blank_context"
    _write_source_trust_recovery_validation_inputs(
        blank_context_dir,
        template_updates={"operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9397/page/104069?id=322745"},
    )
    blank_context = _run_source_trust_recovery_validation(
        ["--operator-resolution-chain-output-dir", str(blank_context_dir)]
    )
    assert blank_context["validation_rows"][0]["validation_status"] == "invalid_generic_landing_page_url"
    assert blank_context["blocker_rows"][0]["blocker_code"] == "generic_landing_page_not_allowed"

    missing_id_dir = tmp_path / "missing_id"
    _write_source_trust_recovery_validation_inputs(
        missing_id_dir,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9397/page/104069",
            "operator_fill_source_page_title": "Отчетность РЖД по МСФО за 2025 год",
            "operator_fill_source_page_notes": "Official RZD company page with IFRS 2025 reporting materials.",
        },
    )
    missing_id = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(missing_id_dir)])
    assert missing_id["validation_rows"][0]["validation_status"] == "invalid_generic_landing_page_url"
    assert missing_id["blocker_rows"][0]["blocker_code"] == "generic_landing_page_not_allowed"


def test_source_trust_recovery_validation_rejects_pdf_and_historical_urls(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    _write_source_trust_recovery_validation_inputs(
        pdf_dir,
        template_updates={"operator_fill_official_source_page_url": "https://rzd.ru/reports/annual-ifrs-2025.pdf"},
    )
    pdf = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(pdf_dir)])
    assert pdf["validation_rows"][0]["validation_status"] == "invalid_pdf_or_document_url"
    assert pdf["blocker_rows"][0]["blocker_code"] == "pdf_or_document_url_not_allowed"

    historical_url = "https://rzd.ru/reports/annual-ifrs-2024.pdf"
    historical_dir = tmp_path / "historical"
    _write_source_trust_recovery_validation_inputs(
        historical_dir,
        template_updates={"operator_fill_official_source_page_url": historical_url},
        workspace_updates={"latest_historical_document_url": historical_url},
    )
    historical = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(historical_dir)])
    assert historical["validation_rows"][0]["validation_status"] == "invalid_historical_fallback_url"
    assert historical["blocker_rows"][0]["blocker_code"] == "historical_fallback_not_allowed"

    current_document_dir = tmp_path / "current_doc"
    _write_source_trust_recovery_validation_inputs(
        current_document_dir,
        template_updates={"operator_fill_official_source_page_url": historical_url},
        workspace_updates={"latest_historical_document_url": "", "current_known_document_url": historical_url},
    )
    current_document = _run_source_trust_recovery_validation(
        ["--operator-resolution-chain-output-dir", str(current_document_dir)]
    )
    assert current_document["validation_rows"][0]["validation_status"] == "invalid_historical_fallback_url"


def test_source_trust_recovery_validation_rejects_malformed_and_non_http_urls(tmp_path: Path) -> None:
    malformed_dir = tmp_path / "malformed"
    _write_source_trust_recovery_validation_inputs(
        malformed_dir,
        template_updates={"operator_fill_official_source_page_url": "not a url"},
    )
    malformed = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(malformed_dir)])
    assert malformed["validation_rows"][0]["validation_status"] == "invalid_malformed_url"

    non_http_dir = tmp_path / "ftp"
    _write_source_trust_recovery_validation_inputs(
        non_http_dir,
        template_updates={"operator_fill_official_source_page_url": "ftp://rzd.ru/ir/reports"},
    )
    non_http = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(non_http_dir)])
    assert non_http["validation_rows"][0]["validation_status"] == "invalid_non_http_url"


def test_source_trust_recovery_validation_rejects_landing_search_news_and_social_urls(tmp_path: Path) -> None:
    cases = [
        ("landing", "https://rzd.ru/", "invalid_generic_landing_page_url", "generic_landing_page_not_allowed"),
        ("company_home", "https://company.rzd.ru/", "invalid_generic_landing_page_url", "generic_landing_page_not_allowed"),
        ("news", "https://rzd.ru/news/financial-results", "invalid_search_or_news_url", "search_or_news_url_not_allowed"),
        ("social", "https://vk.com/rzd", "invalid_social_or_external_platform_url", "social_or_external_platform_not_allowed"),
    ]
    for name, url, expected_status, expected_blocker in cases:
        case_dir = tmp_path / name
        _write_source_trust_recovery_validation_inputs(
            case_dir,
            template_updates={"operator_fill_official_source_page_url": url},
        )
        report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(case_dir)])
        assert report["validation_rows"][0]["validation_status"] == expected_status
        assert report["blocker_rows"][0]["blocker_code"] == expected_blocker


def test_source_trust_recovery_validation_preserves_rejections_before_numeric_cms_acceptance(tmp_path: Path) -> None:
    cases = [
        (
            "hub_pdf",
            "https://company.rzd.ru/ru/9471.pdf",
            "invalid_pdf_or_document_url",
            "pdf_or_document_url_not_allowed",
        ),
        (
            "hub_news",
            "https://company.rzd.ru/ru/news",
            "invalid_search_or_news_url",
            "search_or_news_url_not_allowed",
        ),
        (
            "hub_archive",
            "https://company.rzd.ru/ru/archive",
            "invalid_archive_or_history_url",
            "archive_or_history_url_not_allowed",
        ),
        (
            "news",
            "https://company.rzd.ru/ru/news/page/104069?id=322745",
            "invalid_search_or_news_url",
            "search_or_news_url_not_allowed",
        ),
        (
            "archive",
            "https://company.rzd.ru/ru/archive/page/104069?id=322745",
            "invalid_archive_or_history_url",
            "archive_or_history_url_not_allowed",
        ),
        (
            "social",
            "https://vk.com/rzd",
            "invalid_social_or_external_platform_url",
            "social_or_external_platform_not_allowed",
        ),
        (
            "query_pdf",
            "https://company.rzd.ru/ru/9397/page/104069?id=322745.pdf",
            "invalid_pdf_or_document_url",
            "pdf_or_document_url_not_allowed",
        ),
    ]
    for name, url, expected_status, expected_blocker in cases:
        case_dir = tmp_path / name
        _write_source_trust_recovery_validation_inputs(
            case_dir,
            template_updates={
                "operator_fill_official_source_page_url": url,
                "operator_fill_source_page_title": "Отчетность РЖД по МСФО за 2025 год",
                "operator_fill_source_page_notes": "Official RZD company page with IFRS 2025 reporting materials.",
            },
        )
        report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(case_dir)])
        assert report["validation_rows"][0]["validation_status"] == expected_status
        assert report["blocker_rows"][0]["blocker_code"] == expected_blocker


def test_source_trust_recovery_validation_blocks_non_recovery_and_missing_identity(tmp_path: Path) -> None:
    non_recovery_dir = tmp_path / "non_recovery"
    _write_source_trust_recovery_validation_inputs(
        non_recovery_dir,
        template_updates={"operator_fill_official_source_page_url": "https://rzd.ru/ir/reports"},
        workspace_updates={"recovery_status": "skipped_exact_document_refill_needed"},
    )
    non_recovery = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(non_recovery_dir)])
    assert non_recovery["validation_rows"][0]["validation_status"] == "blocked_not_recovery_candidate"

    missing_identity_dir = tmp_path / "missing_identity"
    _write_source_trust_recovery_validation_inputs(
        missing_identity_dir,
        template_updates={
            "company_id": "",
            "company_name": "",
            "canonical_company_id": "",
            "canonical_company_name": "",
            "operator_fill_official_source_page_url": "https://rzd.ru/ir/reports",
        },
        workspace_updates={
            "company_id": "",
            "company_name": "",
            "canonical_company_id": "",
            "canonical_company_name": "",
        },
    )
    missing_identity = _run_source_trust_recovery_validation(
        ["--operator-resolution-chain-output-dir", str(missing_identity_dir)]
    )
    assert missing_identity["validation_rows"][0]["validation_status"] == "blocked_missing_company_identity"
    assert missing_identity["blocker_rows"][0]["blocker_code"] == "missing_company_identity"


def test_source_trust_recovery_validation_missing_input_and_collisions_fail_safely(tmp_path: Path) -> None:
    missing = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    assert missing["status"] == "failed"
    assert missing["errors"] == [{"message": "source_trust_recovery_template_input_required"}]

    _write_source_trust_recovery_validation_inputs(tmp_path)
    template = tmp_path / "source_trust_recovery_template_task142.csv"
    original = template.read_bytes()
    dedicated = _run_source_trust_recovery_validation(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-validation-output",
            str(template),
        ]
    )
    assert dedicated["status"] == "failed"
    assert dedicated["errors"] == [{"message": "source_trust_recovery_validation_output_must_not_equal_input"}]
    assert template.read_bytes() == original

    generic = _run_source_trust_recovery_validation(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--json-output",
            str(template),
        ]
    )
    assert generic["status"] == "failed"
    assert generic["errors"] == [{"message": "source_trust_recovery_validation_output_must_not_equal_input"}]
    assert template.read_bytes() == original


def test_source_trust_recovery_validation_never_calls_network_or_delete_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task143 must not probe, fetch, download, parse, or delete")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _write_source_trust_recovery_validation_inputs(
        tmp_path,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9471",
            "operator_fill_source_page_title": "Отчетность РЖД",
            "operator_fill_source_page_notes": "Official RZD reporting hub/source page with IFRS reporting materials.",
        },
    )

    report = _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] in {"passed", "warning"}
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_trust_source_urls"] is False
    assert report["would_update_source_pack"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_apply_adds_rzd_candidate_to_source_pack_draft_only(tmp_path: Path) -> None:
    _write_source_trust_recovery_apply_accepted_fixture(tmp_path)
    source_pack = tmp_path / "operator_resolution_pack_task118.json"
    original = source_pack.read_bytes()

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["draft_candidate_added_count"] == 1
    assert report["blocked_count"] == 0
    assert report["source_pack_draft_created"] is True
    assert report["source_pack_input_preserved"] is True
    assert report["production_source_pack_modified"] is False
    assert report["would_trust_source_urls"] is False
    assert report["would_update_production_source_pack"] is False
    assert report["would_update_source_pack_draft"] is True
    assert report["source_pack_input_path"] == str(source_pack)
    assert report["source_pack_input_resolution_strategy"] == "chain_task118"
    assert str(source_pack) in report["source_pack_input_resolution_candidates"]
    assert source_pack.read_bytes() == original
    row = report["apply_rows"][0]
    assert row["apply_status"] == "draft_candidate_added"
    assert row["candidate_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert row["candidate_source_host"] == "company.rzd.ru"
    assert row["candidate_source_status"] == "pending_future_controlled_review"
    assert row["would_trust_source_url"] is False
    draft = json.loads(Path(report["artifacts"]["source_pack_draft_json"]).read_text(encoding="utf-8"))
    draft_row = draft["resolutions"][0]
    assert draft_row["candidate_official_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert draft_row["candidate_source_status"] == "pending_future_controlled_review"
    assert draft_row["trusted"] is False
    assert draft_row["trusted_host"] is False
    assert "company.rzd.ru" not in draft_row.get("trusted_source_hosts", [])
    assert draft_row["current_known_source_page_url"] == ""
    for key in assistant.SOURCE_TRUST_RECOVERY_APPLY_ARTIFACT_NAMES:
        assert Path(report["artifacts"][key]).is_file()


def test_source_trust_recovery_apply_fallback_resolves_parent_task118_source_pack(tmp_path: Path) -> None:
    chain_dir = tmp_path / "logs" / "financial_reports" / "task124_chain_preview"
    _write_source_trust_recovery_apply_accepted_inputs_without_source_pack(chain_dir)
    source_pack = _write_source_trust_recovery_apply_source_pack(
        chain_dir.parent,
        [_source_trust_recovery_apply_source_pack_row()],
    )

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(chain_dir)])

    assert report["status"] == "passed"
    assert report["draft_candidate_added_count"] == 1
    assert report["source_pack_input_path"] == str(source_pack)
    assert report["source_pack_input_resolution_strategy"] == "chain_parent_task118"
    assert (
        str(chain_dir / "operator_resolution_pack_task118.json")
        in report["source_pack_input_resolution_candidates"]
    )
    assert any(
        warning["strategy"] == "chain_task118"
        for warning in report["source_pack_input_resolution_warnings"]
    )


def test_source_trust_recovery_apply_explicit_source_pack_input_wins(tmp_path: Path) -> None:
    chain_dir = tmp_path / "task124_chain_preview"
    _write_source_trust_recovery_apply_accepted_inputs_without_source_pack(chain_dir)
    _write_source_trust_recovery_apply_source_pack(chain_dir, [_source_trust_recovery_apply_source_pack_row()])
    explicit_dir = tmp_path / "explicit"
    explicit_dir.mkdir()
    explicit_pack = _write_source_trust_recovery_apply_source_pack(
        explicit_dir,
        [_source_trust_recovery_apply_source_pack_row()],
    )

    report = _run_source_trust_recovery_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(chain_dir),
            "--financial-official-source-pack-input",
            str(explicit_pack),
        ]
    )

    assert report["status"] == "passed"
    assert report["source_pack_input_path"] == str(explicit_pack)
    assert report["source_pack_input_resolution_strategy"] == "explicit_financial_official_source_pack_input"
    assert report["source_pack_input_resolution_candidates"] == [str(explicit_pack)]


def test_source_trust_recovery_apply_missing_required_inputs_write_failure_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    missing_pack_dir = tmp_path / "missing_pack"
    _write_source_trust_recovery_apply_accepted_inputs_without_source_pack(missing_pack_dir)
    missing_pack = _run_source_trust_recovery_apply(
        ["--operator-resolution-chain-output-dir", str(missing_pack_dir)]
    )
    assert missing_pack["status"] == "failed"
    assert missing_pack["errors"] == [{"message": "source_trust_recovery_apply_source_pack_input_required"}]
    assert missing_pack["source_pack_input_resolution_strategy"] == "unresolved"
    _assert_source_trust_recovery_apply_failure_artifacts(missing_pack)

    missing_validation_dir = tmp_path / "missing_validation"
    _write_source_trust_recovery_apply_accepted_fixture(missing_validation_dir)
    (missing_validation_dir / "source_trust_recovery_validation_task143.json").unlink()
    missing_validation = _run_source_trust_recovery_apply(
        ["--operator-resolution-chain-output-dir", str(missing_validation_dir)]
    )
    assert missing_validation["status"] == "failed"
    assert missing_validation["errors"] == [
        {"message": "source_trust_recovery_apply_validation_input_required"}
    ]
    _assert_source_trust_recovery_apply_failure_artifacts(missing_validation)

    missing_accepted_dir = tmp_path / "missing_accepted"
    _write_source_trust_recovery_apply_accepted_fixture(missing_accepted_dir)
    (missing_accepted_dir / "source_trust_recovery_accepted_candidates_task143.json").unlink()
    missing_accepted = _run_source_trust_recovery_apply(
        ["--operator-resolution-chain-output-dir", str(missing_accepted_dir)]
    )
    assert missing_accepted["status"] == "failed"
    assert missing_accepted["errors"] == [
        {"message": "source_trust_recovery_apply_accepted_candidates_input_required"}
    ]
    _assert_source_trust_recovery_apply_failure_artifacts(missing_accepted)


def test_source_trust_recovery_apply_accepted_count_none_still_uses_rows(tmp_path: Path) -> None:
    _write_source_trust_recovery_apply_accepted_fixture(tmp_path)
    accepted_path = tmp_path / "source_trust_recovery_accepted_candidates_task143.json"
    accepted_payload = json.loads(accepted_path.read_text(encoding="utf-8"))
    accepted_payload["accepted_candidate_count"] = None
    accepted_path.write_text(json.dumps(accepted_payload, indent=2) + "\n", encoding="utf-8")

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["row_count"] == 1
    assert report["draft_candidate_added_count"] == 1


def test_source_trust_recovery_apply_blocks_validation_not_accepted(tmp_path: Path) -> None:
    _write_source_trust_recovery_validation_inputs(tmp_path)
    _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(tmp_path)])
    _write_source_trust_recovery_apply_source_pack(tmp_path, [_source_trust_recovery_apply_source_pack_row()])

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["apply_rows"][0]["apply_status"] == "blocked_validation_not_accepted"
    assert report["blocker_rows"][0]["blocker_code"] == "validation_not_accepted"


def test_source_trust_recovery_apply_blocks_unsafe_flags_and_mismatch(tmp_path: Path) -> None:
    unsafe_dir = tmp_path / "unsafe"
    _write_source_trust_recovery_apply_accepted_fixture(unsafe_dir)
    accepted_path = unsafe_dir / "source_trust_recovery_accepted_candidates_task143.json"
    accepted_payload = json.loads(accepted_path.read_text(encoding="utf-8"))
    accepted_payload["accepted_candidate_rows"][0]["would_fetch_url"] = True
    accepted_path.write_text(json.dumps(accepted_payload, indent=2) + "\n", encoding="utf-8")
    unsafe = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(unsafe_dir)])
    assert unsafe["apply_rows"][0]["apply_status"] == "blocked_candidate_safety_flags"

    mismatch_dir = tmp_path / "mismatch"
    _write_source_trust_recovery_apply_accepted_fixture(mismatch_dir)
    accepted_path = mismatch_dir / "source_trust_recovery_accepted_candidates_task143.json"
    accepted_payload = json.loads(accepted_path.read_text(encoding="utf-8"))
    accepted_payload["accepted_candidate_rows"][0]["official_source_page_url"] = "https://company.rzd.ru/ru/9999"
    accepted_path.write_text(json.dumps(accepted_payload, indent=2) + "\n", encoding="utf-8")
    mismatch = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(mismatch_dir)])
    assert mismatch["apply_rows"][0]["apply_status"] == "blocked_candidate_mismatch"


def test_source_trust_recovery_apply_blocks_source_pack_and_candidate_conflicts(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing_company"
    _write_source_trust_recovery_apply_accepted_fixture(missing_dir)
    _write_source_trust_recovery_apply_source_pack(missing_dir, [])
    missing = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(missing_dir)])
    assert missing["apply_rows"][0]["apply_status"] == "blocked_source_pack_company_missing"

    conflict_dir = tmp_path / "candidate_conflict"
    _write_source_trust_recovery_apply_accepted_fixture(conflict_dir)
    _write_source_trust_recovery_apply_source_pack(
        conflict_dir,
        [
            _source_trust_recovery_apply_source_pack_row(
                candidate_official_source_page_url="https://company.rzd.ru/ru/1234",
                candidate_source_status="pending_future_controlled_review",
            )
        ],
    )
    conflict = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(conflict_dir)])
    assert conflict["apply_rows"][0]["apply_status"] == "blocked_duplicate_candidate_conflict"

    trusted_conflict_dir = tmp_path / "trusted_conflict"
    _write_source_trust_recovery_apply_accepted_fixture(trusted_conflict_dir)
    _write_source_trust_recovery_apply_source_pack(
        trusted_conflict_dir,
        [_source_trust_recovery_apply_source_pack_row(trusted_source_hosts=["different.example"])],
    )
    trusted_conflict = _run_source_trust_recovery_apply(
        ["--operator-resolution-chain-output-dir", str(trusted_conflict_dir)]
    )
    assert trusted_conflict["apply_rows"][0]["apply_status"] == "blocked_existing_trusted_host_conflict"


def test_source_trust_recovery_apply_is_idempotent_for_existing_candidate(tmp_path: Path) -> None:
    _write_source_trust_recovery_apply_accepted_fixture(tmp_path)
    _write_source_trust_recovery_apply_source_pack(
        tmp_path,
        [
            _source_trust_recovery_apply_source_pack_row(
                candidate_official_source_page_url="https://company.rzd.ru/ru/9471",
                candidate_source_status="pending_future_controlled_review",
            )
        ],
    )

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["apply_rows"][0]["apply_status"] == "draft_candidate_already_present"
    assert report["draft_candidate_added_count"] == 0
    assert report["draft_candidate_already_present_count"] == 1


def test_source_trust_recovery_apply_output_collisions_fail_safely(tmp_path: Path) -> None:
    _write_source_trust_recovery_apply_accepted_fixture(tmp_path)
    source_pack = tmp_path / "operator_resolution_pack_task118.json"
    original = source_pack.read_bytes()

    output_collision = _run_source_trust_recovery_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-apply-output",
            str(tmp_path / "source_trust_recovery_validation_task143.json"),
        ]
    )
    assert output_collision["status"] == "failed"
    assert output_collision["errors"] == [{"message": "source_trust_recovery_apply_output_must_not_equal_input"}]

    draft_collision = _run_source_trust_recovery_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-source-pack-draft-output",
            str(source_pack),
        ]
    )
    assert draft_collision["status"] == "failed"
    assert draft_collision["errors"] == [
        {"message": "source_trust_recovery_apply_draft_must_not_overwrite_source_pack"}
    ]
    assert source_pack.read_bytes() == original


def test_source_trust_recovery_apply_never_calls_network_or_delete_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task145 must not probe, fetch, download, parse, delete, or trade")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _write_source_trust_recovery_apply_accepted_fixture(tmp_path)

    report = _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["would_trust_source_urls"] is False
    assert report["would_update_production_source_pack"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_draft_review_rzd_candidate_ready_for_promote_preview(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_draft_review_fixture(tmp_path)

    report = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["ready_for_promote_review_count"] == 1
    assert report["promote_preview_row_count"] == 1
    assert report["blocked_count"] == 0
    row = report["review_rows"][0]
    assert row["review_status"] == "ready_for_controlled_source_trust_promote_review"
    assert row["candidate_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert row["candidate_source_host"] == "company.rzd.ru"
    assert row["would_trust_source_url"] is False
    assert row["would_update_production_source_pack"] is False
    promote = report["promote_preview_rows"][0]
    assert promote["promote_preview_status"] == "ready_for_future_controlled_promote"
    assert promote["operator_approval_required"] is True
    assert promote["future_controlled_apply_required"] is True
    assert promote["future_trusted_host"] == "company.rzd.ru"
    for key in assistant.SOURCE_TRUST_RECOVERY_DRAFT_REVIEW_ARTIFACT_NAMES:
        assert Path(report["artifacts"][key]).is_file()


def test_source_trust_recovery_draft_review_blocks_apply_and_draft_problems(tmp_path: Path) -> None:
    apply_dir = tmp_path / "apply_blocked"
    _prepare_source_trust_recovery_draft_review_fixture(apply_dir)
    _mutate_source_trust_recovery_apply_report(
        apply_dir,
        lambda payload: payload["apply_rows"][0].update({"apply_status": "blocked_validation_not_accepted"}),
    )
    apply_blocked = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(apply_dir)])
    assert apply_blocked["review_rows"][0]["review_status"] == "blocked_apply_not_successful"
    assert apply_blocked["blocker_rows"][0]["blocker_code"] == "apply_not_successful"

    missing_dir = tmp_path / "missing_draft_candidate"
    _prepare_source_trust_recovery_draft_review_fixture(missing_dir)
    _mutate_source_trust_recovery_draft_row(
        missing_dir,
        lambda row: [
            row.pop("candidate_official_source_page_url", None),
            row.pop("candidate_current_known_source_page_url", None),
        ],
    )
    missing = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(missing_dir)])
    assert missing["review_rows"][0]["review_status"] == "blocked_draft_candidate_missing"

    mismatch_dir = tmp_path / "draft_mismatch"
    _prepare_source_trust_recovery_draft_review_fixture(mismatch_dir)
    _mutate_source_trust_recovery_draft_row(
        mismatch_dir,
        lambda row: row.update({"candidate_official_source_page_url": "https://company.rzd.ru/ru/9999"}),
    )
    mismatch = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(mismatch_dir)])
    assert mismatch["review_rows"][0]["review_status"] == "blocked_draft_candidate_mismatch"


def test_source_trust_recovery_draft_review_blocks_validation_and_safety_mismatches(tmp_path: Path) -> None:
    validation_dir = tmp_path / "validation_mismatch"
    _prepare_source_trust_recovery_draft_review_fixture(validation_dir)
    _mutate_source_trust_recovery_accepted_candidates(
        validation_dir,
        lambda payload: payload["accepted_candidate_rows"][0].update(
            {"official_source_page_url": "https://company.rzd.ru/ru/9999"}
        ),
    )
    validation = _run_source_trust_recovery_draft_review(
        ["--operator-resolution-chain-output-dir", str(validation_dir)]
    )
    assert validation["review_rows"][0]["review_status"] == "blocked_validation_mismatch"

    unsafe_dir = tmp_path / "unsafe_flags"
    _prepare_source_trust_recovery_draft_review_fixture(unsafe_dir)
    _mutate_source_trust_recovery_accepted_candidates(
        unsafe_dir,
        lambda payload: payload["accepted_candidate_rows"][0].update({"would_fetch_url": True}),
    )
    unsafe = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(unsafe_dir)])
    assert unsafe["review_rows"][0]["review_status"] == "blocked_candidate_safety_flags"


def test_source_trust_recovery_draft_review_blocks_premature_trust_and_readiness(tmp_path: Path) -> None:
    trusted_dir = tmp_path / "trusted_too_early"
    _prepare_source_trust_recovery_draft_review_fixture(trusted_dir)
    _mutate_source_trust_recovery_draft_row(trusted_dir, lambda row: row.update({"trusted": True}))
    trusted = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(trusted_dir)])
    assert trusted["review_rows"][0]["review_status"] == "blocked_draft_marked_trusted_too_early"

    host_dir = tmp_path / "trusted_host_too_early"
    _prepare_source_trust_recovery_draft_review_fixture(host_dir)
    _mutate_source_trust_recovery_draft_row(
        host_dir,
        lambda row: row.update({"trusted_source_hosts": ["company.rzd.ru"]}),
    )
    host = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(host_dir)])
    assert host["review_rows"][0]["review_status"] == "blocked_draft_marked_trusted_too_early"

    ready_dir = tmp_path / "ready_too_early"
    _prepare_source_trust_recovery_draft_review_fixture(ready_dir)
    _mutate_source_trust_recovery_draft_row(ready_dir, lambda row: row.update({"ready_for_document_download": True}))
    ready = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(ready_dir)])
    assert ready["review_rows"][0]["review_status"] == "blocked_draft_marked_ready_for_download_too_early"


def test_source_trust_recovery_draft_review_blocks_baseline_already_trusting_candidate(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_draft_review_fixture(tmp_path)
    _mutate_source_trust_recovery_source_pack_row(
        tmp_path,
        lambda row: row.update({"trusted_source_hosts": ["company.rzd.ru"]}),
    )

    report = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["review_rows"][0]["review_status"] == "blocked_candidate_already_trusted_in_baseline"
    assert report["blocker_rows"][0]["blocker_code"] == "candidate_already_trusted_in_baseline"


def test_source_trust_recovery_draft_review_output_collision_fails_safely(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_draft_review_fixture(tmp_path)
    apply_input = tmp_path / "source_trust_recovery_apply_draft_task145.json"

    report = _run_source_trust_recovery_draft_review(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-draft-review-output",
            str(apply_input),
        ]
    )

    assert report["status"] == "failed"
    assert report["errors"] == [{"message": "source_trust_recovery_draft_review_output_must_not_equal_input"}]


def test_source_trust_recovery_draft_review_never_calls_network_or_delete_helpers(tmp_path: Path, monkeypatch) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task146 must not probe, fetch, download, parse, delete, or trade")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _prepare_source_trust_recovery_draft_review_fixture(tmp_path)

    report = _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["would_trust_source_urls"] is False
    assert report["would_update_production_source_pack"] is False
    assert report["would_update_source_pack_draft"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_promote_apply_creates_promoted_draft_only(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_promote_apply_fixture(tmp_path)
    baseline_path = tmp_path / "operator_resolution_pack_task118.json"
    task145_draft_path = tmp_path / "source_trust_recovery_source_pack_draft_task145.json"
    baseline_before = baseline_path.read_bytes()
    task145_before = task145_draft_path.read_bytes()

    report = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["promoted_draft_created_count"] == 1
    assert report["blocked_count"] == 0
    assert baseline_path.read_bytes() == baseline_before
    assert task145_draft_path.read_bytes() == task145_before
    row = report["promote_apply_rows"][0]
    assert row["promote_apply_status"] == "promoted_draft_created"
    assert row["future_trusted_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert row["future_trusted_host"] == "company.rzd.ru"
    assert row["would_create_promoted_source_pack_draft"] is True
    assert row["would_trust_source_url_in_production"] is False
    assert row["would_update_production_source_pack"] is False
    assert row["would_update_task145_source_pack_draft"] is False

    promoted_path = Path(report["artifacts"]["promoted_source_pack_draft_json"])
    promoted_payload = json.loads(promoted_path.read_text(encoding="utf-8"))
    promoted_row = promoted_payload["resolutions"][0]
    assert promoted_row["current_known_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert "company.rzd.ru" in promoted_row["trusted_source_hosts"]
    assert "company.rzd.ru" in promoted_row["trusted_hosts"]
    assert promoted_row["source_trust_status"] == "future_controlled_promoted_source_trust_draft"
    assert promoted_row["candidate_source_status"] == "promoted_in_task147_draft"
    assert promoted_row["trusted"] is True
    assert promoted_row["trusted_host"] is True
    assert promoted_row["ready_for_document_download"] is False
    assert promoted_row["ready_for_extraction"] is False
    for key in assistant.SOURCE_TRUST_RECOVERY_PROMOTE_APPLY_ARTIFACT_NAMES:
        assert Path(report["artifacts"][key]).is_file()


def test_source_trust_recovery_promote_apply_blocks_review_and_preview_not_ready(tmp_path: Path) -> None:
    review_dir = tmp_path / "review_not_ready"
    _prepare_source_trust_recovery_promote_apply_fixture(review_dir)
    _mutate_json_file(
        review_dir / "source_trust_recovery_draft_review_task146.json",
        lambda payload: payload["review_rows"][0].update({"review_status": "blocked_apply_not_successful"}),
    )
    review = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(review_dir)])
    assert review["promote_apply_rows"][0]["promote_apply_status"] == "blocked_review_not_ready"
    assert review["blocker_rows"][0]["blocker_code"] == "review_not_ready"

    preview_dir = tmp_path / "preview_not_ready"
    _prepare_source_trust_recovery_promote_apply_fixture(preview_dir)
    _mutate_json_file(
        preview_dir / "source_trust_recovery_promote_preview_task146.json",
        lambda payload: payload["promote_preview_rows"][0].update({"promote_preview_status": "preview_noop"}),
    )
    preview = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(preview_dir)])
    assert preview["promote_apply_rows"][0]["promote_apply_status"] == "blocked_promote_preview_not_ready"
    assert preview["blocker_rows"][0]["blocker_code"] == "promote_preview_not_ready"


def test_source_trust_recovery_promote_apply_blocks_task145_draft_state(tmp_path: Path) -> None:
    mismatch_dir = tmp_path / "draft_mismatch"
    _prepare_source_trust_recovery_promote_apply_fixture(mismatch_dir)
    _mutate_source_trust_recovery_draft_row(
        mismatch_dir,
        lambda row: row.update({"candidate_official_source_page_url": "https://company.rzd.ru/ru/9999"}),
    )
    mismatch = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(mismatch_dir)])
    assert mismatch["promote_apply_rows"][0]["promote_apply_status"] == "blocked_task145_draft_mismatch"

    trusted_dir = tmp_path / "trusted_too_early"
    _prepare_source_trust_recovery_promote_apply_fixture(trusted_dir)
    _mutate_source_trust_recovery_draft_row(trusted_dir, lambda row: row.update({"trusted": True}))
    trusted = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(trusted_dir)])
    assert trusted["promote_apply_rows"][0]["promote_apply_status"] == "blocked_task145_draft_marked_trusted_too_early"

    ready_dir = tmp_path / "ready_too_early"
    _prepare_source_trust_recovery_promote_apply_fixture(ready_dir)
    _mutate_source_trust_recovery_draft_row(ready_dir, lambda row: row.update({"ready_for_document_download": True}))
    ready = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(ready_dir)])
    assert (
        ready["promote_apply_rows"][0]["promote_apply_status"]
        == "blocked_task145_draft_marked_ready_for_download_too_early"
    )


def test_source_trust_recovery_promote_apply_blocks_baseline_already_trusted(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_promote_apply_fixture(tmp_path)
    _mutate_source_trust_recovery_source_pack_row(
        tmp_path,
        lambda row: row.update({"trusted_source_hosts": ["company.rzd.ru"]}),
    )

    report = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["promote_apply_rows"][0]["promote_apply_status"] == "blocked_baseline_already_trusted"
    assert report["blocker_rows"][0]["blocker_code"] == "baseline_already_trusted"


def test_source_trust_recovery_promote_apply_output_collisions_fail_safely(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_promote_apply_fixture(tmp_path)
    review_input = tmp_path / "source_trust_recovery_draft_review_task146.json"

    output_collision = _run_source_trust_recovery_promote_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-promote-apply-output",
            str(review_input),
        ]
    )
    assert output_collision["status"] == "failed"
    assert output_collision["errors"] == [
        {"message": "source_trust_recovery_promote_apply_output_must_not_equal_input"}
    ]

    baseline_input = tmp_path / "operator_resolution_pack_task118.json"
    draft_collision = _run_source_trust_recovery_promote_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-promoted-source-pack-draft-output",
            str(baseline_input),
        ]
    )
    assert draft_collision["status"] == "failed"
    assert draft_collision["errors"] == [
        {"message": "source_trust_recovery_promote_apply_draft_must_not_overwrite_input"}
    ]


def test_source_trust_recovery_promote_apply_never_calls_network_or_delete_helpers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task147 must not probe, fetch, download, parse, delete, or trade")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _prepare_source_trust_recovery_promote_apply_fixture(tmp_path)

    report = _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["would_trust_source_urls_in_production"] is False
    assert report["would_update_production_source_pack"] is False
    assert report["would_update_task145_source_pack_draft"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_source_trust_recovery_controlled_apply_no_token_blocks_safely(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_controlled_apply_fixture(tmp_path)

    report = _run_source_trust_recovery_controlled_apply(["--operator-resolution-chain-output-dir", str(tmp_path)])

    assert report["status"] == "blocked"
    assert report["controlled_apply_execution_enabled"] is False
    assert report["controlled_apply_created_count"] == 0
    assert report["blocked_count"] == 1
    assert report["controlled_source_pack_created"] is False
    assert report["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_confirmation_token_required"
    assert report["blocker_rows"][0]["blocker_code"] == "confirmation_token_required"
    assert Path(report["artifacts"]["controlled_apply_json"]).is_file()
    assert Path(report["artifacts"]["controlled_apply_csv"]).is_file()
    assert Path(report["artifacts"]["controlled_apply_markdown"]).is_file()
    assert Path(report["artifacts"]["blockers_json"]).is_file()
    assert Path(report["artifacts"]["blockers_csv"]).is_file()
    assert Path(report["artifacts"]["ledger_json"]).is_file()
    assert Path(report["artifacts"]["rerun_markdown"]).is_file()
    assert not Path(report["artifacts"]["controlled_source_pack_json"]).exists()
    ledger_text = Path(report["artifacts"]["ledger_json"]).read_text(encoding="utf-8")
    ledger = json.loads(ledger_text)
    assert ledger["token_matched"] is False
    assert ledger["confirmation_token_matches"] is False
    assert ledger["controlled_source_pack_path"] is None
    assert ledger["controlled_source_pack_sha256"] is None
    assert "APPLY_RZD_SOURCE_TRUST_TASK148" not in ledger_text
    for artifact_path in report["artifacts"].values():
        path = Path(artifact_path)
        if path.is_file():
            assert "APPLY_RZD_SOURCE_TRUST_TASK148" not in path.read_text(encoding="utf-8")


def test_source_trust_recovery_controlled_apply_hash_gates_block(tmp_path: Path) -> None:
    promoted_dir = tmp_path / "wrong_promoted_sha"
    _prepare_source_trust_recovery_controlled_apply_fixture(promoted_dir)
    wrong_promoted = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(promoted_dir, promoted_sha="bad")
    )
    assert wrong_promoted["status"] == "blocked"
    assert wrong_promoted["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_promoted_draft_sha256_mismatch"
    assert wrong_promoted["blocker_rows"][0]["blocker_code"] == "promoted_draft_sha256_mismatch"
    assert wrong_promoted["controlled_source_pack_created"] is False

    apply_dir = tmp_path / "wrong_promote_apply_sha"
    _prepare_source_trust_recovery_controlled_apply_fixture(apply_dir)
    wrong_apply = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(apply_dir, promote_apply_sha="bad")
    )
    assert wrong_apply["status"] == "blocked"
    assert wrong_apply["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_promote_apply_sha256_mismatch"
    assert wrong_apply["blocker_rows"][0]["blocker_code"] == "promote_apply_sha256_mismatch"
    assert wrong_apply["controlled_source_pack_created"] is False


def test_source_trust_recovery_controlled_apply_creates_controlled_source_pack_only(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_controlled_apply_fixture(tmp_path)
    baseline_path = tmp_path / "operator_resolution_pack_task118.json"
    promoted_path = tmp_path / "source_trust_recovery_promoted_source_pack_draft_task147.json"
    task145_path = tmp_path / "source_trust_recovery_source_pack_draft_task145.json"
    baseline_before = baseline_path.read_bytes()
    promoted_before = promoted_path.read_bytes()
    task145_before = task145_path.read_bytes()

    report = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(tmp_path)
    )

    assert report["status"] == "passed"
    assert report["row_count"] == 1
    assert report["controlled_apply_created_count"] == 1
    assert report["blocked_count"] == 0
    assert report["controlled_source_pack_created"] is True
    assert report["baseline_source_pack_input_preserved"] is True
    assert report["promoted_source_pack_draft_input_preserved"] is True
    assert report["task145_source_pack_draft_input_preserved"] is True
    assert report["production_source_pack_modified"] is False
    assert report["promoted_source_pack_draft_modified"] is False
    assert report["task145_source_pack_draft_modified"] is False
    assert report["document_intake_modified"] is False
    assert baseline_path.read_bytes() == baseline_before
    assert promoted_path.read_bytes() == promoted_before
    assert task145_path.read_bytes() == task145_before
    row = report["controlled_apply_rows"][0]
    assert row["controlled_apply_status"] == "controlled_apply_created"
    assert row["candidate_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert row["candidate_source_host"] == "company.rzd.ru"
    assert row["would_trust_source_url_in_controlled_artifact"] is True
    assert row["would_update_production_source_pack"] is False
    assert row["would_update_document_intake"] is False
    assert row["would_probe_url"] is False
    assert row["would_fetch_url"] is False
    assert row["would_download_document"] is False
    assert row["would_parse_document"] is False

    controlled_payload = json.loads(Path(report["artifacts"]["controlled_source_pack_json"]).read_text(encoding="utf-8"))
    controlled_row = controlled_payload["resolutions"][0]
    assert controlled_row["current_known_source_page_url"] == "https://company.rzd.ru/ru/9471"
    assert "company.rzd.ru" in controlled_row["trusted_source_hosts"]
    assert "company.rzd.ru" in controlled_row["trusted_hosts"]
    assert controlled_row["source_trust_status"] == "controlled_applied_source_trust"
    assert controlled_row["candidate_source_status"] == "controlled_applied_in_task148"
    assert controlled_row["trusted"] is True
    assert controlled_row["trusted_host"] is True
    assert controlled_row["ready_for_document_download"] is False
    assert controlled_row["ready_for_extraction"] is False
    ledger_text = Path(report["artifacts"]["ledger_json"]).read_text(encoding="utf-8")
    ledger = json.loads(ledger_text)
    controlled_sha256 = hashlib.sha256(Path(report["artifacts"]["controlled_source_pack_json"]).read_bytes()).hexdigest()
    assert ledger["token_matched"] is True
    assert ledger["confirmation_token_matches"] is True
    assert ledger["controlled_source_pack_path"] == report["artifacts"]["controlled_source_pack_json"]
    assert ledger["controlled_source_pack_sha256"] == controlled_sha256
    assert "APPLY_RZD_SOURCE_TRUST_TASK148" not in ledger_text
    for artifact_path in report["artifacts"].values():
        path = Path(artifact_path)
        if path.is_file():
            assert "APPLY_RZD_SOURCE_TRUST_TASK148" not in path.read_text(encoding="utf-8")


def test_source_trust_recovery_controlled_apply_blocks_invalid_inputs(tmp_path: Path) -> None:
    apply_dir = tmp_path / "promote_apply_not_successful"
    _prepare_source_trust_recovery_controlled_apply_fixture(apply_dir)
    _mutate_json_file(
        apply_dir / "source_trust_recovery_promote_apply_draft_task147.json",
        lambda payload: payload["promote_apply_rows"][0].update({"promote_apply_status": "blocked_review_not_ready"}),
    )
    blocked_apply = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(apply_dir)
    )
    assert blocked_apply["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_promote_apply_not_successful"

    mismatch_dir = tmp_path / "promoted_draft_mismatch"
    _prepare_source_trust_recovery_controlled_apply_fixture(mismatch_dir)
    _mutate_source_trust_recovery_promoted_draft_row(
        mismatch_dir,
        lambda row: row.update({"current_known_source_page_url": "https://company.rzd.ru/ru/9999"}),
    )
    mismatch = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(mismatch_dir)
    )
    assert mismatch["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_promoted_draft_mismatch"

    ready_dir = tmp_path / "ready_too_early"
    _prepare_source_trust_recovery_controlled_apply_fixture(ready_dir)
    _mutate_source_trust_recovery_promoted_draft_row(
        ready_dir,
        lambda row: row.update({"ready_for_document_download": True}),
    )
    ready = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(ready_dir)
    )
    assert (
        ready["controlled_apply_rows"][0]["controlled_apply_status"]
        == "blocked_promoted_draft_ready_for_download_too_early"
    )

    baseline_dir = tmp_path / "baseline_already_trusted"
    _prepare_source_trust_recovery_controlled_apply_fixture(baseline_dir)
    _mutate_source_trust_recovery_source_pack_row(
        baseline_dir,
        lambda row: row.update({"trusted_source_hosts": ["company.rzd.ru"]}),
    )
    baseline = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(baseline_dir)
    )
    assert baseline["controlled_apply_rows"][0]["controlled_apply_status"] == "blocked_baseline_already_trusted"


def test_source_trust_recovery_controlled_apply_output_collisions_fail_safely(tmp_path: Path) -> None:
    _prepare_source_trust_recovery_controlled_apply_fixture(tmp_path)
    promote_apply_input = tmp_path / "source_trust_recovery_promote_apply_draft_task147.json"

    output_collision = _run_source_trust_recovery_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-controlled-apply-output",
            str(promote_apply_input),
        ]
    )
    assert output_collision["status"] == "failed"
    assert output_collision["errors"] == [
        {"message": "source_trust_recovery_controlled_apply_output_must_not_equal_input"}
    ]

    baseline_input = tmp_path / "operator_resolution_pack_task118.json"
    overwrite_collision = _run_source_trust_recovery_controlled_apply(
        [
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--source-trust-recovery-controlled-source-pack-output",
            str(baseline_input),
        ]
    )
    assert overwrite_collision["status"] == "failed"
    assert overwrite_collision["errors"] == [
        {"message": "source_trust_recovery_controlled_apply_must_not_overwrite_input"}
    ]


def test_source_trust_recovery_controlled_apply_never_calls_network_or_delete_helpers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Task148 must not probe, fetch, download, parse, delete, or trade")

    monkeypatch.setattr(assistant, "_probe_url", unexpected_call)
    monkeypatch.setattr(assistant, "_fetch_candidate_page", unexpected_call)
    monkeypatch.setattr(assistant, "_download_valid_document", unexpected_call)
    monkeypatch.setattr(assistant, "_download_source_document", unexpected_call)
    monkeypatch.setattr(assistant, "_delete_backup_file_after_all_guards", unexpected_call)
    _prepare_source_trust_recovery_controlled_apply_fixture(tmp_path)

    report = _run_source_trust_recovery_controlled_apply(
        _source_trust_recovery_controlled_apply_hash_args(tmp_path)
    )

    assert report["would_update_production_source_pack"] is False
    assert report["would_update_promoted_source_pack_draft"] is False
    assert report["would_update_task145_source_pack_draft"] is False
    assert report["would_update_document_intake"] is False
    assert report["would_probe_urls"] is False
    assert report["would_fetch_urls"] is False
    assert report["would_download_documents"] is False
    assert report["would_parse_documents"] is False
    assert report["would_mutate_database"] is False
    assert report["would_extract_values"] is False
    assert report["would_import_report"] is False
    assert report["would_mutate_scores"] is False
    assert report["would_trigger_paper_trading"] is False
    assert report["would_delete_files"] is False


def test_exact_document_source_coverage_weak_no_reviewed_seed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/")],
    )
    _mock_candidate_fetch(monkeypatch, {})
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18",
            "--exact-document-availability-current-date",
            "2026-05-25",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    coverage = _coverage_for(report, company_id=18)
    assert coverage["coverage_status"] == "weak_no_reviewed_seed"
    assert coverage["coverage_operator_action"] == "review_or_promote_official_seed"
    assert coverage["coverage_grade"] in {"missing", "weak"}
    assert coverage["ready_for_value_extraction"] is False
    assert coverage["ready_for_import"] is False


def test_exact_document_source_coverage_generic_landing_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_custom_seed_pack(
        seed_pack,
        [
            {
                "company_id": 67,
                "company_name": "Mostotrest",
                "canonical_company_id": 67,
                "canonical_company_name": "Mostotrest",
                "official_seeds": [
                    {
                        "seed_type": "issuer_home",
                        "seed_url": "https://mostotrest.ru/",
                        "seed_status": "valid_seed",
                        "confidence": "high",
                        "source": "operator_seed",
                        "operator_review_status": "operator_reviewed",
                    }
                ],
            }
        ],
    )
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/")],
    )
    _mock_candidate_fetch(monkeypatch, {"https://mostotrest.ru/": "<a href=\"/contacts/\">Contacts</a>"})
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-seed-types",
            "issuer_home",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    coverage = _coverage_for(report)
    assert coverage["coverage_status"] == "weak_only_generic_or_landing_pages"
    assert coverage["coverage_operator_action"] == "replace_landing_page_with_reporting_page"
    assert 0 <= coverage["coverage_score"] <= 100


def test_exact_document_source_coverage_missing_official_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    _write_custom_seed_pack(seed_pack, [])
    _mock_candidate_fetch(monkeypatch, {})
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--required-company-names",
            "RZD",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    coverage = _coverage_for(report, company_id=18)
    assert coverage["coverage_status"] == "missing_official_sources"
    assert coverage["coverage_operator_action"] == "add_official_sources"
    assert coverage["coverage_grade"] == "missing"


def test_exact_document_availability_operator_summary_exports_flat_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    summary_json = tmp_path / "availability_summary.json"
    summary_csv = tmp_path / "availability_summary.csv"
    summary_md = tmp_path / "availability_summary.md"
    queue_json = tmp_path / "operator_review_queue.json"
    queue_csv = tmp_path / "operator_review_queue.csv"
    queue_md = tmp_path / "operator_review_queue.md"
    coverage_json = tmp_path / "official_source_coverage.json"
    coverage_csv = tmp_path / "official_source_coverage.csv"
    coverage_md = tmp_path / "official_source_coverage.md"
    fallback_json = tmp_path / "historical_fallback_registry.json"
    fallback_csv = tmp_path / "historical_fallback_registry.csv"
    fallback_md = tmp_path / "historical_fallback_registry.md"
    readiness_json = tmp_path / "reporting_readiness_matrix.json"
    readiness_csv = tmp_path / "reporting_readiness_matrix.csv"
    readiness_md = tmp_path / "reporting_readiness_matrix.md"
    resolution_json = tmp_path / "operator_resolution_pack.json"
    resolution_csv = tmp_path / "operator_resolution_pack.csv"
    resolution_md = tmp_path / "operator_resolution_pack.md"
    _write_reviewed_seed_pack(seed_pack, include_rzd=True)
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/reports/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/"),
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/reports/": "",
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2024.pdf">Annual IFRS financial statements 2024</a>
            """,
            "https://mostotrest.ru/ru/invest/information-disclosure/": "",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18,67",
            "--exact-document-availability-current-date",
            "2026-05-25",
            "--availability-operator-summary-output",
            str(summary_json),
            "--availability-operator-summary-csv-output",
            str(summary_csv),
            "--availability-operator-summary-markdown-output",
            str(summary_md),
            "--operator-review-queue-output",
            str(queue_json),
            "--operator-review-queue-csv-output",
            str(queue_csv),
            "--operator-review-queue-markdown-output",
            str(queue_md),
            "--official-source-coverage-output",
            str(coverage_json),
            "--official-source-coverage-csv-output",
            str(coverage_csv),
            "--official-source-coverage-markdown-output",
            str(coverage_md),
            "--historical-fallback-registry-output",
            str(fallback_json),
            "--historical-fallback-registry-csv-output",
            str(fallback_csv),
            "--historical-fallback-registry-markdown-output",
            str(fallback_md),
            "--reporting-readiness-matrix-output",
            str(readiness_json),
            "--reporting-readiness-matrix-csv-output",
            str(readiness_csv),
            "--reporting-readiness-matrix-markdown-output",
            str(readiness_md),
            "--operator-resolution-pack-output",
            str(resolution_json),
            "--operator-resolution-pack-csv-output",
            str(resolution_csv),
            "--operator-resolution-pack-markdown-output",
            str(resolution_md),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["target_reporting_period_availability_count"] == 2
    assert report["availability_policy_name"] == "annual_ifrs_deadline_aware_grace_window"
    assert report["availability_current_date"] == "2026-05-25"
    assert report["annual_ifrs_primary_deadline_days"] == 120
    assert report["primary_expected_deadline_date"] == "2026-04-30"
    assert report["annual_ifrs_grace_days"] == 180
    assert report["availability_status_counts"]["placeholder_not_found"] == 1
    assert report["availability_status_counts"]["target_period_not_found_after_primary_deadline_within_grace_window"] == 1
    assert report["deadline_status_counts"]["after_primary_deadline_within_grace_window"] == 2
    assert report["availability_primary_deadline_status_counts"]["after_primary_deadline_within_grace_window"] == 2
    assert report["target_evidence_available_count"] == 0
    assert report["historical_fallback_diagnostic_only_count"] == 1
    assert report["extraction_ready_count"] == 0
    assert report["import_ready_count"] == 0
    assert report["operator_action_counts"]["operator_to_find_official_exact_document"] == 1
    assert report["operator_action_counts"]["review_official_sources_or_wait_until_conservative_grace_date"] == 1
    assert report["operator_review_queue_count"] == 2
    assert report["operator_review_queue_blocking_count"] == 2
    assert report["operator_review_queue_manual_action_count"] == 2
    assert report["operator_review_queue_wait_action_count"] == 0
    assert report["operator_review_queue_noop_count"] == 0
    assert report["operator_review_queue_priority_counts"]["high"] == 1
    assert report["operator_review_queue_priority_counts"]["medium"] == 1
    assert report["operator_review_queue_action_type_counts"]["fill_exact_document_url"] == 1
    assert report["operator_review_queue_action_type_counts"]["review_sources_or_wait_grace"] == 1

    rows = {str(row["company_id"]): row for row in report["availability_operator_rows"]}
    assert set(rows) == {"18", "67"}
    assert rows["18"]["availability_status"] == "placeholder_not_found"
    assert rows["18"]["recommended_next_step"] == "fill_exact_official_document_url_or_improve_official_sources"
    assert rows["18"]["can_use_as_target_period_evidence"] is False
    assert rows["18"]["gate_status"] == "quality_gate_not_run"
    placeholder = next(item for item in report["documents"] if item.get("company_id") == 18)
    assert placeholder["filter_status"] == "placeholder_not_found"
    assert rows["67"]["availability_status"] == "target_period_not_found_after_primary_deadline_within_grace_window"
    assert rows["67"]["historical_fallback_scope"] == "diagnostic_only"
    assert rows["67"]["recommended_next_step"] == "review_official_sources_or_wait_until_conservative_grace_date"
    assert rows["67"]["primary_expected_deadline_date"] == "2026-04-30"
    assert rows["67"]["after_primary_deadline"] is True
    assert rows["67"]["within_conservative_grace_window"] is True
    assert rows["67"]["ready_for_value_extraction"] is False
    actions = {str(row["company_id"]): row for row in report["operator_review_queue"]}
    assert actions["18"]["action_id"] == "financial_report:18:2025:annual:IFRS:fill_exact_document_url"
    assert actions["18"]["queue_action_type"] == "fill_exact_document_url"
    assert actions["18"]["queue_priority"] == "high"
    assert actions["18"]["queue_status"] == "open"
    assert actions["18"]["manual_review_required"] is True
    assert actions["18"]["is_blocking_next_stage"] is True
    assert actions["18"]["blocked_stage"] == "value_extraction"
    assert actions["18"]["can_unblock_extraction"] is True
    assert "Do not paste a landing page" in actions["18"]["operator_instruction"]
    assert actions["67"]["action_id"] == "financial_report:67:2025:annual:IFRS:review_sources_or_wait_grace"
    assert actions["67"]["queue_action_type"] == "review_sources_or_wait_grace"
    assert actions["67"]["queue_priority"] == "medium"
    assert actions["67"]["queue_status"] == "open"
    assert actions["67"]["manual_review_required"] is True
    assert actions["67"]["is_blocking_next_stage"] is True
    assert actions["67"]["blocked_stage"] == "value_extraction"
    assert actions["67"]["can_unblock_extraction"] is True
    assert actions["67"]["primary_expected_deadline_date"] == "2026-04-30"
    assert actions["67"]["deadline_status"] == "after_primary_deadline_within_grace_window"
    assert actions["67"]["expected_availability_date"] == "2026-06-29"

    exported = json.loads(summary_json.read_text(encoding="utf-8"))
    assert exported["mode"] == "availability-operator-summary"
    assert exported["summary"]["target_reporting_period_availability_count"] == 2
    assert len(exported["issuers"]) == 2
    csv_rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    assert len(csv_rows) == 2
    assert {
        "availability_status",
        "availability_reason_codes",
        "historical_fallback_scope",
        "can_use_as_target_period_evidence",
        "operator_action",
        "recommended_next_step",
        "ready_for_value_extraction",
        "ready_for_import",
        "primary_expected_deadline_date",
        "after_primary_deadline",
        "within_conservative_grace_window",
        "deadline_status",
    }.issubset(set(csv_rows[0]))
    markdown = summary_md.read_text(encoding="utf-8")
    assert "Target Reporting Period Availability" in markdown
    assert "placeholder_not_found" in markdown
    assert "target_period_not_found_after_primary_deadline_within_grace_window" in markdown
    assert "2026-04-30" in markdown
    assert "diagnostic_only" in markdown
    assert "False" in markdown or "false" in markdown
    queue_exported = json.loads(queue_json.read_text(encoding="utf-8"))
    assert queue_exported["mode"] == "operator-review-queue"
    assert queue_exported["summary"]["operator_review_queue_count"] == 2
    assert len(queue_exported["actions"]) == 2
    queue_csv_rows = list(csv.DictReader(queue_csv.open(encoding="utf-8")))
    assert len(queue_csv_rows) == 2
    assert {
        "action_id",
        "queue_action_type",
        "queue_priority",
        "queue_status",
        "is_blocking_next_stage",
        "blocked_stage",
        "manual_review_required",
        "can_unblock_extraction",
        "operator_instruction",
        "recommended_next_step",
        "primary_expected_deadline_date",
        "within_conservative_grace_window",
        "deadline_status",
    }.issubset(set(queue_csv_rows[0]))
    queue_markdown = queue_md.read_text(encoding="utf-8")
    assert "Operator Review Action Queue" in queue_markdown
    assert "Queue Priority Counts" in queue_markdown
    assert "Queue Action Type Counts" in queue_markdown
    assert "fill_exact_document_url" in queue_markdown
    assert "review_sources_or_wait_grace" in queue_markdown
    assert "after_primary_deadline_within_grace_window" in queue_markdown
    assert report["official_source_coverage_issuer_count"] == 2
    assert report["official_source_coverage_status_counts"]["strong_but_target_report_missing"] == 2
    assert report["official_source_coverage_action_counts"]["verify_target_report_publication"] == 2
    coverage_rows = {str(row["company_id"]): row for row in report["official_source_coverage_rows"]}
    assert coverage_rows["67"]["coverage_status"] == "strong_but_target_report_missing"
    assert coverage_rows["67"]["coverage_operator_action"] == "verify_target_report_publication"
    assert coverage_rows["67"]["historical_annual_ifrs_document_count"] == 1
    assert coverage_rows["67"]["can_use_as_target_period_evidence"] is False
    coverage_exported = json.loads(coverage_json.read_text(encoding="utf-8"))
    assert coverage_exported["mode"] == "official-source-coverage-matrix"
    assert coverage_exported["summary"]["official_source_coverage_issuer_count"] == 2
    coverage_csv_rows = list(csv.DictReader(coverage_csv.open(encoding="utf-8")))
    assert len(coverage_csv_rows) == 2
    assert {
        "coverage_status",
        "coverage_score",
        "coverage_grade",
        "coverage_reason_codes",
        "coverage_operator_action",
        "coverage_operator_instruction",
        "ready_for_value_extraction",
        "ready_for_import",
    }.issubset(set(coverage_csv_rows[0]))
    coverage_markdown = coverage_md.read_text(encoding="utf-8")
    assert "Official Source Coverage Matrix" in coverage_markdown
    assert "Coverage Status Counts" in coverage_markdown
    assert "verify_target_report_publication" in coverage_markdown
    assert report["historical_fallback_registry_issuer_count"] == 2
    assert report["historical_fallback_registry_report_count"] == 1
    assert report["historical_fallback_registry_latest_report_count"] == 1
    assert report["historical_fallback_registry_diagnostic_only_count"] == 1
    assert report["historical_fallback_registry_target_evidence_count"] == 0
    assert report["historical_fallback_registry_extraction_ready_count"] == 0
    assert report["historical_fallback_registry_import_ready_count"] == 0
    assert report["historical_fallback_registry_status_counts"]["no_historical_fallback_available"] == 1
    assert report["historical_fallback_registry_status_counts"]["latest_historical_annual_ifrs_available"] == 1
    fallback_rows = {str(row["company_id"]): row for row in report["historical_fallback_registry_rows"]}
    assert fallback_rows["18"]["historical_fallback_status"] == "no_historical_fallback_available"
    assert fallback_rows["18"]["historical_fallback_scope"] == "none"
    assert fallback_rows["18"]["can_use_for_value_extraction"] is False
    assert fallback_rows["67"]["historical_fallback_status"] == "latest_historical_annual_ifrs_available"
    assert fallback_rows["67"]["latest_available_period"] == "2024"
    assert fallback_rows["67"]["historical_fallback_scope"] == "diagnostic_only"
    assert fallback_rows["67"]["can_use_as_target_period_evidence"] is False
    assert fallback_rows["67"]["can_use_for_value_extraction"] is False
    assert fallback_rows["67"]["can_use_for_import"] is False
    assert fallback_rows["67"]["can_use_for_scoring"] is False
    assert fallback_rows["67"]["can_use_for_paper_trading"] is False
    fallback_exported = json.loads(fallback_json.read_text(encoding="utf-8"))
    assert fallback_exported["mode"] == "historical-fallback-registry"
    assert fallback_exported["summary"]["historical_fallback_registry_issuer_count"] == 2
    fallback_csv_rows = list(csv.DictReader(fallback_csv.open(encoding="utf-8")))
    assert len(fallback_csv_rows) == 2
    assert {
        "historical_fallback_status",
        "historical_fallback_scope",
        "latest_available_period",
        "latest_available_report_type",
        "latest_available_standard",
        "latest_available_document_url",
        "can_use_as_target_period_evidence",
        "can_use_for_value_extraction",
        "can_use_for_import",
        "can_use_for_scoring",
        "can_use_for_paper_trading",
    }.issubset(set(fallback_csv_rows[0]))
    fallback_markdown = fallback_md.read_text(encoding="utf-8")
    assert "Historical Fallback Registry" in fallback_markdown
    assert "Historical Fallback Status Counts" in fallback_markdown
    assert "diagnostic_only" in fallback_markdown
    assert "target evidence False" in fallback_markdown or "target evidence false" in fallback_markdown
    assert report["reporting_readiness_issuer_count"] == 2
    assert report["reporting_readiness_ready_count"] == 0
    assert report["reporting_readiness_blocked_count"] == 2
    assert report["reporting_readiness_needs_operator_count"] == 2
    assert report["reporting_readiness_target_evidence_available_count"] == 0
    assert report["reporting_readiness_gate_passed_count"] == 0
    assert report["reporting_readiness_historical_only_count"] == 1
    assert report["reporting_readiness_source_coverage_blocked_count"] == 0
    assert report["reporting_readiness_status_counts"]["blocked_placeholder_not_found"] == 1
    assert report["reporting_readiness_status_counts"]["blocked_missing_target_evidence"] == 1
    assert report["reporting_readiness_blocker_counts"]["missing_exact_target_period_annual_ifrs"] == 2
    assert report["reporting_readiness_blocker_counts"]["historical_fallback_diagnostic_only"] == 1
    readiness_rows = {str(row["company_id"]): row for row in report["reporting_readiness_rows"]}
    assert readiness_rows["18"]["reporting_readiness_status"] == "blocked_placeholder_not_found"
    assert readiness_rows["18"]["extraction_allowed"] is False
    assert readiness_rows["18"]["import_allowed"] is False
    assert readiness_rows["18"]["scoring_allowed"] is False
    assert readiness_rows["18"]["paper_trading_allowed"] is False
    assert readiness_rows["67"]["reporting_readiness_status"] == "blocked_missing_target_evidence"
    assert "historical_fallback_diagnostic_only" in readiness_rows["67"]["reporting_readiness_reason_codes"]
    assert "historical_fallback" in readiness_rows["67"]["blocking_layers"]
    assert readiness_rows["67"]["next_required_action"] == "find_or_verify_exact_target_period_annual_ifrs_report"
    readiness_exported = json.loads(readiness_json.read_text(encoding="utf-8"))
    assert readiness_exported["mode"] == "reporting-readiness-matrix"
    assert readiness_exported["summary"]["reporting_readiness_issuer_count"] == 2
    readiness_csv_rows = list(csv.DictReader(readiness_csv.open(encoding="utf-8")))
    assert len(readiness_csv_rows) == 2
    assert {
        "reporting_readiness_status",
        "reporting_readiness_grade",
        "primary_blocker",
        "blocking_layers",
        "extraction_allowed",
        "import_allowed",
        "scoring_allowed",
        "paper_trading_allowed",
        "next_required_action",
    }.issubset(set(readiness_csv_rows[0]))
    readiness_markdown = readiness_md.read_text(encoding="utf-8")
    assert "Reporting Readiness Matrix Before Extraction" in readiness_markdown
    assert "Readiness Status Counts" in readiness_markdown
    assert "Readiness Blocker Counts" in readiness_markdown
    assert "blocked_placeholder_not_found" in readiness_markdown
    assert "extraction False" in readiness_markdown or "extraction false" in readiness_markdown
    assert report["operator_resolution_pack_issuer_count"] == 2
    assert report["operator_resolution_pack_action_count"] == 2
    assert report["operator_resolution_pack_manual_action_count"] == 2
    assert report["operator_resolution_pack_wait_action_count"] == 0
    assert report["operator_resolution_pack_can_unblock_extraction_count"] == 2
    assert report["operator_resolution_pack_target_document_fill_count"] == 1
    assert report["operator_resolution_pack_source_review_count"] == 0
    assert report["operator_resolution_pack_escalation_count"] == 0
    assert report["operator_resolution_pack_status_counts"]["open"] == 2
    assert report["operator_resolution_pack_action_type_counts"]["fill_exact_document_url"] == 1
    assert report["operator_resolution_pack_action_type_counts"]["verify_target_report_publication"] == 1
    resolution_rows = {str(row["company_id"]): row for row in report["operator_resolution_pack_rows"]}
    assert resolution_rows["18"]["resolution_action_type"] == "fill_exact_document_url"
    assert resolution_rows["18"]["resolution_priority"] == "high"
    assert resolution_rows["18"]["requires_exact_document_url"] is True
    assert resolution_rows["18"]["operator_fill_exact_document_url"] == ""
    assert resolution_rows["18"]["operator_fill_report_period"] == "2025"
    assert resolution_rows["18"]["operator_fill_report_type"] == "annual"
    assert resolution_rows["18"]["operator_fill_accounting_standard"] == "IFRS"
    assert resolution_rows["67"]["resolution_action_type"] == "verify_target_report_publication"
    assert resolution_rows["67"]["resolution_priority"] == "medium"
    assert resolution_rows["67"]["requires_publication_verification"] is True
    assert resolution_rows["67"]["latest_historical_document_url"].endswith("annual-ifrs-financial-statements-2024.pdf")
    assert resolution_rows["67"]["operator_fill_exact_document_url"] == ""
    resolution_exported = json.loads(resolution_json.read_text(encoding="utf-8"))
    assert resolution_exported["mode"] == "operator-resolution-pack"
    assert resolution_exported["summary"]["operator_resolution_pack_issuer_count"] == 2
    assert len(resolution_exported["resolutions"]) == 2
    resolution_csv_rows = list(csv.DictReader(resolution_csv.open(encoding="utf-8")))
    assert len(resolution_csv_rows) == 2
    assert {
        "resolution_id",
        "resolution_action_type",
        "resolution_priority",
        "operator_fill_exact_document_url",
        "operator_fill_document_title",
        "operator_fill_document_date",
        "operator_fill_source_page_url",
        "operator_fill_source_type",
        "operator_fill_report_period",
        "operator_fill_report_type",
        "operator_fill_accounting_standard",
        "operator_fill_decision",
        "operator_fill_notes",
        "extraction_allowed",
        "import_allowed",
        "scoring_allowed",
        "paper_trading_allowed",
    }.issubset(set(resolution_csv_rows[0]))
    resolution_markdown = resolution_md.read_text(encoding="utf-8")
    assert "Operator Resolution Pack" in resolution_markdown
    assert "Resolution Action Type Counts" in resolution_markdown
    assert "Manual Input Instructions" in resolution_markdown
    assert "Safety Notes" in resolution_markdown
    assert "fill_exact_document_url" in resolution_markdown
    assert "verify_target_report_publication" in resolution_markdown


def test_exact_document_discover_probe_and_download_are_optional(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://mostotrest.ru/ru/invest/financial-results/": """
                <a href="/reports/annual-ifrs-financial-statements-2025.pdf">Annual IFRS financial statements 2025</a>
            """,
        },
    )
    probes: list[str] = []
    downloads: list[str] = []

    def fake_probe(url: str, *, timeout_seconds: float, max_bytes: int) -> dict:
        probes.append(url)
        return {"status": "ok", "http_status": 200, "content_type": "application/pdf", "error": None}

    def fake_download(document: dict, download_dir: Path) -> dict:
        downloads.append(document["document_url"])
        return {
            "url": document["document_url"],
            "local_path": str(download_dir / "annual-ifrs-financial-statements-2025.pdf"),
            "sha256": "abc123",
            "size_bytes": 123,
            "content_type": "application/pdf",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(assistant, "_probe_url", fake_probe)
    monkeypatch.setattr(assistant, "_download_valid_document", fake_download)
    no_network_args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
        ]
    )
    report, exit_code = assistant.run_assistant(no_network_args)
    assert exit_code == 0
    assert report["candidate_count"] == 1
    assert probes == []
    assert downloads == []

    network_args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-probe-urls",
            "true",
            "--exact-document-download-documents",
            "true",
            "--exact-document-download-dir",
            str(tmp_path / "downloads"),
        ]
    )
    network_report, exit_code = assistant.run_assistant(network_args)

    assert exit_code == 0
    assert probes == ["https://mostotrest.ru/reports/annual-ifrs-financial-statements-2025.pdf"]
    assert downloads == ["https://mostotrest.ru/reports/annual-ifrs-financial-statements-2025.pdf"]
    assert network_report["documents"][0]["probe_status"] == "ok"
    assert network_report["documents"][0]["download"]["sha256"] == "abc123"


def test_official_seed_resolve_from_source_intake_builds_seed_pack(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    financial_template, _, _ = _write_task95_outputs(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    seed_output = tmp_path / "official_seed_pack.json"
    seed_csv = tmp_path / "official_seed_pack.csv"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--financial-template-input",
            str(financial_template),
            "--required-company-ids",
            "18,67",
            "--seed-output",
            str(seed_output),
            "--seed-csv-output",
            str(seed_csv),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["mode"] == "official-seed-resolve"
    assert report["issuer_count"] == 2
    assert report["seed_count"] >= 4
    assert report["valid_seed_count"] >= 2
    assert all(issuer["official_seeds"] for issuer in report["issuers"])
    assert {issuer["inn"] for issuer in report["issuers"]} >= {"7708503727", "7701045732"}
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    assert report["read_only"] is True
    assert report["import_executed"] is False
    assert seed_output.is_file()
    rows = list(csv.DictReader(seed_csv.open(encoding="utf-8")))
    assert rows
    assert {"issuer_home", "official_disclosure_home"} & {row["seed_type"] for row in rows}


def test_operator_seed_template_writes_fillable_json_and_csv(
    tmp_path: Path,
) -> None:
    financial_template, _, _ = _write_task95_outputs(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    output = tmp_path / "operator_seed_template.json"
    csv_output = tmp_path / "operator_seed_template.csv"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/ | https://www.e-disclosure.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ | https://www.e-disclosure.ru/"),
        ],
    )
    _write_seed_pack(seed_pack)
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-template",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--financial-template-input",
            str(financial_template),
            "--required-company-ids",
            "18,67",
            "--operator-seed-output",
            str(output),
            "--operator-seed-csv-output",
            str(csv_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "template"
    assert report["issuer_count"] == 2
    assert report["seed_template_count"] == 6
    assert all(seed["seed_url"] == "" for seed in report["seeds"])
    assert {seed["inn"] for seed in report["seeds"]} >= {"7708503727", "7701045732"}
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    assert report["read_only"] is True
    assert report["dry_run_only"] is True
    assert report["import_executed"] is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["seeds"][0]["operator_review_status"] == "operator_to_fill"
    rzd_contexts = [seed["source_context"] for seed in payload["seeds"] if seed["company_id"] == 18]
    assert any("https://www.e-disclosure.ru/" in context for context in rzd_contexts)
    assert all("https://rzd.ru/reports/" not in context for context in rzd_contexts)
    rows = list(csv.DictReader(csv_output.open(encoding="utf-8")))
    assert rows
    assert rows[0].keys() == {
        "company_id",
        "company_name",
        "canonical_company_id",
        "canonical_company_name",
        "inn",
        "ogrn",
        "seed_type",
        "seed_url",
        "operator_review_status",
        "source_context",
        "notes",
    }


def test_operator_seed_validate_accepts_reviewed_official_seeds(
    tmp_path: Path,
) -> None:
    operator_seed = tmp_path / "operator_seed.json"
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "official_disclosure_profile",
                "seed_url": "https://www.e-disclosure.ru/portal/company.aspx?id=1",
                "operator_review_status": "operator_reviewed",
                "notes": "Synthetic official disclosure profile page",
            },
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/reports/annual/",
                "operator_review_status": "reviewed",
                "notes": "Synthetic issuer annual reports page",
            },
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-validate",
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["valid_seed_count"] == 2
    assert report["invalid_seed_count"] == 0
    assert all(seed["seed_status"] == "valid_seed" for seed in report["seeds"])


def test_operator_seed_validate_blocks_missing_unreviewed_unsafe_and_financial_values(
    tmp_path: Path,
) -> None:
    operator_seed = tmp_path / "bad_operator_seed.json"
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/reports/annual/",
                "operator_review_status": "operator_to_fill",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://wikipedia.org/wiki/RZD",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://www.google.com/search?q=rzd+annual+report",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/news/annual-report",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/reports/annual/",
                "operator_review_status": "operator_reviewed",
                "revenue": "100",
            },
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-validate",
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    messages = " ".join(item["message"] for item in report["errors"])
    assert "seed_url is required" in messages
    assert "operator_review_status must be reviewed" in messages
    assert "blocked unofficial source domain" in messages
    assert "financial values are forbidden" in messages


def test_operator_seed_validate_unknown_domain_requires_allow_unknown(
    tmp_path: Path,
) -> None:
    operator_seed = tmp_path / "unknown_operator_seed.json"
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "issuer_reports",
                "seed_url": "https://issuer.example/reports/annual/",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    default_args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-validate",
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
        ]
    )
    default_report, default_exit = assistant.run_assistant(default_args)
    assert default_exit == 1
    assert default_report["seeds"][0]["seed_status"] == "invalid_seed"

    allow_args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-validate",
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )
    allow_report, allow_exit = assistant.run_assistant(allow_args)

    assert allow_exit == 0
    assert allow_report["status"] == "warning"
    assert allow_report["valid_seed_count"] == 0
    assert allow_report["seeds"][0]["seed_status"] == "needs_operator_review"
    assert allow_report["seeds"][0]["confidence"] == "low"


def test_operator_seed_merge_preserves_dedupes_and_rejects_invalid(
    tmp_path: Path,
) -> None:
    seed_pack = tmp_path / "official_seed_pack.json"
    operator_seed = tmp_path / "operator_seed.json"
    merged_output = tmp_path / "merged_seed_pack.json"
    merged_csv = tmp_path / "merged_seed_pack.csv"
    _write_seed_pack(seed_pack)
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "official_disclosure_profile",
                "seed_url": "https://www.e-disclosure.ru/portal/company.aspx?id=1",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "official_disclosure_profile",
                "seed_url": "https://www.e-disclosure.ru/portal/company.aspx?id=1",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "issuer_reports",
                "seed_url": "https://issuer.example/reports/annual/",
                "operator_review_status": "operator_reviewed",
            },
            {
                "company_id": 18,
                "seed_type": "issuer_reports",
                "seed_url": "https://wikipedia.org/wiki/RZD",
                "operator_review_status": "operator_reviewed",
            },
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-merge",
            "--seed-input",
            str(seed_pack),
            "--operator-seed-input",
            str(operator_seed),
            "--seed-output",
            str(merged_output),
            "--seed-csv-output",
            str(merged_csv),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["invalid_rejected_count"] == 1
    assert report["valid_merged_count"] == 1
    assert report["review_needed_count"] == 1
    merged = json.loads(merged_output.read_text(encoding="utf-8"))
    rzd_seeds = merged["issuers"][0]["official_seeds"]
    assert any(seed["seed_url"] == "https://rzd.ru/" for seed in rzd_seeds)
    assert sum(seed["seed_url"] == "https://www.e-disclosure.ru/portal/company.aspx?id=1" for seed in rzd_seeds) == 1
    assert any(
        seed["seed_url"] == "https://issuer.example/reports/annual/"
        and seed["seed_status"] == "needs_operator_review"
        for seed in rzd_seeds
    )
    assert all("wikipedia" not in seed["seed_url"] for seed in rzd_seeds)
    assert merged_csv.is_file()


def test_operator_seed_candidate_discover_without_matches_warns_and_validates_autofill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    candidate_output = tmp_path / "operator_seed_candidates.json"
    candidate_csv = tmp_path / "operator_seed_candidates.csv"
    autofill_output = tmp_path / "operator_seed_autofill.json"
    autofill_csv = tmp_path / "operator_seed_autofill.csv"
    validation_output = tmp_path / "operator_seed_validation.json"
    _write_operator_seeds(template, _operator_seed_template_rows())
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": "<html></html>",
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-candidate-output",
            str(candidate_output),
            "--operator-seed-candidate-csv-output",
            str(candidate_csv),
            "--operator-seed-autofill-output",
            str(autofill_output),
            "--operator-seed-autofill-csv-output",
            str(autofill_csv),
            "--run-operator-seed-validate",
            "true",
            "--operator-seed-validation-json-output",
            str(validation_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0
    assert all(not item["candidate_seed_url"] for item in report["candidates"])
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    autofill = json.loads(autofill_output.read_text(encoding="utf-8"))
    assert all(seed["seed_url"] == "" for seed in autofill["seeds"])
    assert all(seed["operator_review_status"] == "operator_to_fill" for seed in autofill["seeds"])
    validation = json.loads(validation_output.read_text(encoding="utf-8"))
    assert validation["status"] == "failed"
    assert candidate_output.is_file()
    assert candidate_csv.is_file()
    assert autofill_csv.is_file()


def test_operator_seed_candidate_discover_mocked_disclosure_profile_autofills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    autofill_output = tmp_path / "operator_seed_autofill.json"
    validation_output = tmp_path / "operator_seed_validation.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["official_disclosure_profile"]))
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": """
                <a href="/portal/company.aspx?id=synthetic-rzd">RZD issuer profile INN 7708503727</a>
            """,
            "https://rzd.ru/": "<html></html>",
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-autofill-output",
            str(autofill_output),
            "--run-operator-seed-validate",
            "true",
            "--operator-seed-validation-json-output",
            str(validation_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    reviewed = [
        item
        for item in report["candidates"]
        if item["seed_type"] == "official_disclosure_profile"
        and item["operator_review_status"] == "operator_reviewed"
    ]
    assert reviewed
    assert reviewed[0]["candidate_score"] >= 90
    autofill = json.loads(autofill_output.read_text(encoding="utf-8"))
    assert autofill["seeds"][0]["seed_url"] == "https://www.e-disclosure.ru/portal/company.aspx?id=synthetic-rzd"
    assert autofill["seeds"][0]["operator_review_status"] == "operator_reviewed"
    validation = json.loads(validation_output.read_text(encoding="utf-8"))
    assert validation["valid_seed_count"] == 1


def test_operator_seed_candidate_discover_issuer_reports_page_and_pdf_review_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["issuer_reports"]))
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": """
                <a href="/investor/reports/">RZD annual reports INN 7708503727</a>
                <a href="/reports/rzd-annual-ifrs-2025.pdf">RZD annual financial statements INN 7708503727</a>
            """,
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    reports_page = [
        item
        for item in report["candidates"]
        if item["candidate_seed_url"] == "https://rzd.ru/investor/reports/"
    ][0]
    assert reports_page["operator_review_status"] == "operator_reviewed"
    pdf_candidates = [item for item in report["candidates"] if item["candidate_seed_url"].endswith(".pdf")]
    assert pdf_candidates
    assert all(item["operator_review_status"] != "operator_reviewed" for item in pdf_candidates)
    assert "revenue" not in json.dumps(report, ensure_ascii=False)


def test_operator_seed_candidate_discover_blocks_search_and_unofficial_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    autofill_output = tmp_path / "operator_seed_autofill.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["issuer_reports"]))
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": """
                <a href="https://wikipedia.org/wiki/RZD">RZD annual reports INN 7708503727</a>
                <a href="https://www.google.com/search?q=rzd+reports">RZD reports search</a>
                <a href="https://example.com/blog/rzd-reports">RZD reports blog</a>
            """,
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-autofill-output",
            str(autofill_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["blocked_candidate_count"] >= 3
    autofill = json.loads(autofill_output.read_text(encoding="utf-8"))
    assert all(seed["seed_url"] == "" for seed in autofill["seeds"])


def test_operator_seed_candidate_manual_unknown_domain_review_only(
    tmp_path: Path,
) -> None:
    manual = tmp_path / "manual_operator_seed.json"
    _write_operator_seeds(
        manual,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "seed_type": "issuer_reports",
                "seed_url": "https://issuer.example/reports/",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    default_args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(manual),
            "--operator-seed-candidate-source",
            "manual-candidates",
            "--required-company-ids",
            "18",
        ]
    )
    default_report, default_exit = assistant.run_assistant(default_args)
    assert default_exit == 0
    assert default_report["candidate_count"] == 0
    assert default_report["invalid_candidate_count"] == 1
    assert default_report["candidates"] == []

    allow_args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(manual),
            "--operator-seed-candidate-source",
            "manual-candidates",
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )
    allow_report, allow_exit = assistant.run_assistant(allow_args)
    assert allow_exit == 0
    assert allow_report["candidates"][0]["candidate_status"] == "needs_operator_review"
    assert allow_report["candidates"][0]["operator_review_status"] == "needs_operator_review"


def test_operator_seed_candidate_noise_filter_suppresses_passenger_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["issuer_reports"]))
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": """
                <a href="/ru/9269">Купить билет</a>
                <a href="/ru/9316">Поезда и маршруты</a>
                <a href="/ru/11497">Онлайн-табло вокзалов</a>
                <a href="/investors/reports/">Инвесторам — годовая отчетность RZD</a>
            """,
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-candidate-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    kept_urls = {
        item["candidate_seed_url"]
        for item in report["candidates"]
        if item.get("candidate_seed_url") and item.get("filter_status") == "kept"
    }
    assert "https://rzd.ru/investors/reports/" in kept_urls
    assert "https://rzd.ru/ru/9269" not in kept_urls
    assert "https://rzd.ru/ru/9316" not in kept_urls
    assert "https://rzd.ru/ru/11497" not in kept_urls
    assert report["filtered_noise_count"] >= 3


def test_operator_seed_candidate_official_domain_generic_page_is_insufficient(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["issuer_reports"]))
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": '<a href="/about/">RZD</a>',
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-candidate-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    generic = [
        item
        for item in report["candidates"]
        if item.get("candidate_seed_url") == "https://rzd.ru/about/"
    ][0]
    assert generic["final_score"] < 60
    assert generic["filter_status"] in {"filtered_low_score", "filtered_noise"}
    assert report["candidate_count"] == 0


def test_operator_seed_candidate_mostotrest_reporting_pages_rank_high(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    rows = [
        {
            "company_id": 67,
            "company_name": "Mostotrest",
            "canonical_company_id": 67,
            "canonical_company_name": "Mostotrest",
            "inn": "7701045732",
            "ogrn": "1027739167246",
            "seed_type": "issuer_reports",
            "seed_url": "",
            "operator_review_status": "operator_to_fill",
            "source_context": "https://mostotrest.ru/",
            "notes": "Synthetic operator seed template row.",
        }
    ]
    _write_operator_seeds(template, rows)
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://mostotrest.ru/": """
                <a href="/ru/about/">Mostotrest</a>
                <a href="/ru/activity/">Mostotrest activity</a>
                <a href="/ru/invest/information-disclosure/">Mostotrest information disclosure</a>
                <a href="/ru/invest/financial-results/">Mostotrest financial results INN 7701045732</a>
            """,
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "67",
            "--operator-seed-candidate-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    kept = [
        item
        for item in report["candidates"]
        if item.get("candidate_seed_url") and item.get("filter_status") == "kept"
    ]
    financial = [item for item in kept if item["candidate_seed_url"].endswith("/ru/invest/financial-results/")][0]
    disclosure = [item for item in kept if item["candidate_seed_url"].endswith("/ru/invest/information-disclosure/")][0]
    generic_scores = [
        item["final_score"]
        for item in report["candidates"]
        if item.get("candidate_seed_url", "").endswith(("/ru/about/", "/ru/activity/"))
    ]
    assert financial["final_score"] >= 90
    assert financial["candidate_rank"] == 1
    assert disclosure["final_score"] > max(generic_scores)
    assert any("financial" in reason for reason in financial["score_reasons"])
    assert any("disclosure" in reason for reason in disclosure["score_reasons"])


def test_operator_seed_candidate_top_n_and_include_filtered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    _write_operator_seeds(template, _operator_seed_template_rows(seed_types=["issuer_reports"]))
    _write_seed_pack(seed_pack)
    links = "\n".join(
        f'<a href="/investors/reports/{index}/">RZD annual reports {index}</a>'
        for index in range(8)
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": "<html></html>",
            "https://rzd.ru/": links,
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-candidate-top-n-per-type",
            "2",
            "--operator-seed-candidate-include-filtered",
            "true",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    kept = [item for item in report["candidates"] if item.get("filter_status") == "kept" and item.get("candidate_seed_url")]
    filtered = [item for item in report["candidates"] if item.get("filter_status") != "kept" and item.get("candidate_seed_url")]
    assert len(kept) == 2
    assert report["candidate_count"] == 2
    assert filtered
    assert report["filtered_low_score_count"] >= 6


def test_operator_seed_candidate_autofill_caps_review_needed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "operator_seed_template.json"
    seed_pack = tmp_path / "official_seed_pack.json"
    rows = _operator_seed_template_rows(
        seed_types=["issuer_reports", "issuer_investor_relations", "official_disclosure_reports"]
    )
    _write_operator_seeds(template, rows)
    _write_seed_pack(seed_pack)
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://www.e-disclosure.ru/": '<a href="/portal/events.aspx">RZD disclosure reports</a>',
            "https://rzd.ru/": """
                <a href="/investors/reports/">Annual reports</a>
                <a href="/investors/">Investor relations</a>
            """,
            "https://rzd.ru/reports/": "<html></html>",
        },
    )
    autofill_output = tmp_path / "autofill.json"
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-candidate-discover",
            "--operator-seed-input",
            str(template),
            "--seed-input",
            str(seed_pack),
            "--required-company-ids",
            "18",
            "--operator-seed-autofill-output",
            str(autofill_output),
            "--operator-seed-candidate-max-autofill-review-needed",
            "1",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["autofill_review_needed_count"] <= 1
    autofill = json.loads(autofill_output.read_text(encoding="utf-8"))
    assert sum(1 for seed in autofill["seeds"] if seed["operator_review_status"] == "needs_operator_review") <= 1
    assert all("ticket" not in seed.get("notes", "").casefold() for seed in autofill["seeds"])


def test_operator_seed_review_template_from_ranked_candidates_preserves_missing_rows(
    tmp_path: Path,
) -> None:
    candidate_input = tmp_path / "ranked_candidates.json"
    operator_seed = tmp_path / "operator_seed_template.json"
    review_output = tmp_path / "operator_seed_review.json"
    review_csv = tmp_path / "operator_seed_review.csv"
    _write_operator_seed_candidates(candidate_input, _task107_candidate_rows())
    _write_operator_seeds(
        operator_seed,
        [
            *_operator_seed_template_rows(seed_types=["official_disclosure_profile", "official_disclosure_reports", "issuer_reports"]),
            *_operator_seed_template_rows_for(
                67,
                "Mostotrest",
                "7701045732",
                "1027739167246",
                seed_types=["official_disclosure_profile", "official_disclosure_reports", "issuer_reports"],
                source_context="https://mostotrest.ru/",
            ),
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-review-template",
            "--operator-seed-candidate-input",
            str(candidate_input),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18,67",
            "--operator-seed-review-output",
            str(review_output),
            "--operator-seed-review-csv-output",
            str(review_csv),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "template"
    assert report["candidate_review_item_count"] == 2
    assert report["missing_review_item_count"] == 4
    candidate_items = [item for item in report["review_items"] if item["candidate_seed_url"]]
    assert {item["seed_type"] for item in candidate_items} == {"issuer_reports", "official_disclosure_reports"}
    assert all(item["operator_decision"] == "pending" for item in candidate_items)
    assert all(item["operator_review_status"] == "needs_operator_review" for item in candidate_items)
    assert any(item["company_id"] == 18 and item["review_status"] == "missing_candidate" for item in report["review_items"])
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    assert report["read_only"] is True
    assert report["import_executed"] is False
    rows = list(csv.DictReader(review_csv.open(encoding="utf-8")))
    assert rows
    assert "operator_decision" in rows[0]
    payload = json.loads(review_output.read_text(encoding="utf-8"))
    assert payload["review_items"][0]["promotion_status"] == "not_promoted"


def test_operator_seed_promote_reviewed_approved_official_candidate_validates(
    tmp_path: Path,
) -> None:
    review_input = tmp_path / "operator_seed_review.json"
    operator_seed = tmp_path / "operator_seed_template.json"
    promoted_output = tmp_path / "operator_seed_promoted.json"
    promoted_csv = tmp_path / "operator_seed_promoted.csv"
    validation_output = tmp_path / "operator_seed_validation.json"
    _write_operator_seed_review(review_input, [_task107_review_item(decision="approve")])
    _write_operator_seeds(
        operator_seed,
        _operator_seed_template_rows_for(
            67,
            "Mostotrest",
            "7701045732",
            "1027739167246",
            seed_types=["issuer_reports"],
            source_context="https://mostotrest.ru/",
        ),
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-promote-reviewed",
            "--operator-seed-review-input",
            str(review_input),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "67",
            "--operator-seed-output",
            str(promoted_output),
            "--operator-seed-csv-output",
            str(promoted_csv),
            "--run-operator-seed-validate",
            "true",
            "--operator-seed-validation-json-output",
            str(validation_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["promoted_seed_count"] == 1
    assert report["approved_count"] == 1
    promoted = json.loads(promoted_output.read_text(encoding="utf-8"))
    assert promoted["seeds"][0]["seed_url"] == "https://mostotrest.ru/ru/invest/financial-results/"
    assert promoted["seeds"][0]["operator_review_status"] == "operator_reviewed"
    validation = json.loads(validation_output.read_text(encoding="utf-8"))
    assert validation["valid_seed_count"] == 1
    assert "revenue" not in json.dumps(report, ensure_ascii=False)
    assert promoted_csv.is_file()


def test_operator_seed_promote_reviewed_without_approvals_warns(
    tmp_path: Path,
) -> None:
    review_input = tmp_path / "operator_seed_review.json"
    operator_seed = tmp_path / "operator_seed_template.json"
    promoted_output = tmp_path / "operator_seed_promoted.json"
    _write_operator_seed_review(review_input, [_task107_review_item(decision="pending")])
    _write_operator_seeds(
        operator_seed,
        _operator_seed_template_rows_for(
            67,
            "Mostotrest",
            "7701045732",
            "1027739167246",
            seed_types=["issuer_reports"],
            source_context="https://mostotrest.ru/",
        ),
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-promote-reviewed",
            "--operator-seed-review-input",
            str(review_input),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "67",
            "--operator-seed-output",
            str(promoted_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["promoted_seed_count"] == 0
    promoted = json.loads(promoted_output.read_text(encoding="utf-8"))
    assert promoted["seeds"][0]["seed_url"] == ""
    assert promoted["seeds"][0]["operator_review_status"] == "operator_to_fill"


def test_operator_seed_promote_reviewed_invalid_approvals_fail(
    tmp_path: Path,
) -> None:
    review_input = tmp_path / "bad_review.json"
    operator_seed = tmp_path / "operator_seed_template.json"
    rows = [
        _task107_review_item(decision="approve", url=""),
        _task107_review_item(decision="approve", url="https://wikipedia.org/wiki/Mostotrest"),
        _task107_review_item(decision="approve", url="https://issuer.example/reports/"),
        _task107_review_item(decision="approve", extra={"revenue": "100"}),
        _task107_review_item(decision="approve", url="", candidate_status="not_found", review_status="missing_candidate"),
    ]
    _write_operator_seed_review(review_input, rows)
    _write_operator_seeds(
        operator_seed,
        _operator_seed_template_rows_for(
            67,
            "Mostotrest",
            "7701045732",
            "1027739167246",
            seed_types=["issuer_reports"],
            source_context="https://mostotrest.ru/",
        ),
    )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-promote-reviewed",
            "--operator-seed-review-input",
            str(review_input),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "67",
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["invalid_review_item_count"] == 5
    messages = " ".join(item["message"] for item in report["errors"])
    assert "approve requires candidate_seed_url" in messages
    assert "blocked unofficial source domain" in messages
    assert "source URL domain is not in the official allowlist" in messages
    assert "financial values are forbidden" in messages
    assert "cannot approve a not_found review row" in messages


def test_operator_seed_promote_reviewed_dedupes_and_official_seed_resolve_consumes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    review_input = tmp_path / "operator_seed_review.json"
    operator_seed = tmp_path / "operator_seed_template.json"
    promoted_output = tmp_path / "operator_seed_promoted.json"
    intake = tmp_path / "exact_document_intake.json"
    gate_output = tmp_path / "quality_gate.json"
    _write_operator_seed_review(
        review_input,
        [
            _task107_review_item(decision="approve"),
            _task107_review_item(decision="approve"),
        ],
    )
    _write_operator_seeds(
        operator_seed,
        _operator_seed_template_rows_for(
            67,
            "Mostotrest",
            "7701045732",
            "1027739167246",
            seed_types=["issuer_reports"],
            source_context="https://mostotrest.ru/",
        ),
    )
    promote_args = assistant.parse_args(
        [
            "--mode",
            "operator-seed-promote-reviewed",
            "--operator-seed-review-input",
            str(review_input),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "67",
            "--operator-seed-output",
            str(promoted_output),
        ]
    )
    promote_report, promote_exit = assistant.run_assistant(promote_args)
    assert promote_exit == 0
    assert promote_report["promoted_seed_count"] == 1
    promoted = json.loads(promoted_output.read_text(encoding="utf-8"))
    assert sum(seed["seed_url"] == "https://mostotrest.ru/ru/invest/financial-results/" for seed in promoted["seeds"]) == 1

    _write_document_intake(intake, [_empty_document_intake_item(67, "Mostotrest", "")])
    _mock_candidate_fetch(monkeypatch, {"https://mostotrest.ru/ru/invest/financial-results/": "<html></html>"})
    resolve_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--operator-seed-input",
            str(promoted_output),
            "--required-company-ids",
            "67",
            "--run-candidate-discovery",
            "true",
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )
    resolve_report, resolve_exit = assistant.run_assistant(resolve_args)

    assert resolve_exit == 0
    assert any(
        seed["source"] == "operator_seed"
        and seed["seed_status"] == "valid_seed"
        and seed["seed_url"] == "https://mostotrest.ru/ru/invest/financial-results/"
        for issuer in resolve_report["issuers"]
        for seed in issuer["official_seeds"]
    )
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_official_seed_resolve_uses_operator_seed_for_discovery_and_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    operator_seed = tmp_path / "operator_seed.json"
    gate_output = tmp_path / "quality_gate.json"
    discovered = _build_discovered_intake(tmp_path)
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "")])
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/operator-reports/",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float, max_bytes: int, user_agent: str) -> dict:
        calls.append(url)
        return {
            "status": "ok",
            "url": url,
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "body": '<a href="/investors/">Investors</a>',
            "size_bytes": 32,
        }

    monkeypatch.setattr(assistant, "_fetch_candidate_page", fake_fetch)
    args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
            "--run-candidate-discovery",
            "true",
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert "https://rzd.ru/operator-reports/" in calls
    assert any(
        seed["source"] == "operator_seed" and seed["seed_url"] == "https://rzd.ru/operator-reports/"
        for issuer in report["issuers"]
        for seed in issuer["official_seeds"]
    )
    assert report["candidate_discovery_report"]["candidate_count"] == 0
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is False
    assert gate["ready_for_value_extraction"] is False


def test_official_seed_resolve_operator_seed_gate_can_pass_with_mocked_exact_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    operator_seed = tmp_path / "operator_seed.json"
    gate_output = tmp_path / "quality_gate.json"
    discovered = _build_discovered_intake(tmp_path)
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "")])
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "seed_type": "issuer_reports",
                "seed_url": "https://rzd.ru/operator-reports/",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/operator-reports/": '<a href="/reports/rzd-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>',
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
            "--run-candidate-discovery",
            "true",
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["candidate_discovery_report"]["candidate_count"] == 1
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["ready_for_value_extraction"] is True
    assert gate["ready_for_import"] is False
    assert "revenue" not in json.dumps(report, ensure_ascii=False)


def test_official_seed_resolve_operator_seed_validation(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    operator_seed = tmp_path / "operator_seed.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_operator_seeds(
        operator_seed,
        [
            {
                "company_id": 18,
                "canonical_company_id": 18,
                "company_name": "RZD",
                "seed_type": "official_disclosure_profile",
                "seed_url": "https://www.e-disclosure.ru/portal/company.aspx?id=1",
                "operator_review_status": "operator_reviewed",
                "notes": "Official disclosure profile page",
            }
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--operator-seed-input",
            str(operator_seed),
            "--required-company-ids",
            "18",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    operator_seeds = [
        seed
        for issuer in report["issuers"]
        for seed in issuer["official_seeds"]
        if seed["source"] == "operator_seed"
    ]
    assert operator_seeds
    assert operator_seeds[0]["seed_status"] == "valid_seed"
    assert operator_seeds[0]["confidence"] == "high"


def test_official_seed_resolve_blocks_unknown_and_financial_operator_seed(
    tmp_path: Path,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    blocked_seed = tmp_path / "blocked_seed.json"
    unknown_seed = tmp_path / "unknown_seed.json"
    financial_seed = tmp_path / "financial_seed.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    _write_operator_seeds(
        blocked_seed,
        [
            {
                "company_id": 18,
                "seed_url": "https://wikipedia.org/wiki/RZD",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    _write_operator_seeds(
        unknown_seed,
        [
            {
                "company_id": 18,
                "seed_url": "https://issuer.example/profile",
                "operator_review_status": "operator_reviewed",
            }
        ],
    )
    _write_operator_seeds(
        financial_seed,
        [
            {
                "company_id": 18,
                "seed_url": "https://rzd.ru/investors/",
                "operator_review_status": "operator_reviewed",
                "revenue": "100",
            }
        ],
    )

    blocked_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--operator-seed-input",
            str(blocked_seed),
            "--required-company-ids",
            "18",
        ]
    )
    blocked_report, blocked_exit = assistant.run_assistant(blocked_args)
    assert blocked_exit == 1
    assert any("blocked unofficial source domain" in item["message"] for item in blocked_report["errors"])

    unknown_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--operator-seed-input",
            str(unknown_seed),
            "--required-company-ids",
            "18",
            "--allow-unknown-source",
        ]
    )
    unknown_report, unknown_exit = assistant.run_assistant(unknown_args)
    assert unknown_exit == 0
    unknown_operator_seed = [
        seed
        for issuer in unknown_report["issuers"]
        for seed in issuer["official_seeds"]
        if seed["source"] == "operator_seed"
    ][0]
    assert unknown_operator_seed["seed_status"] == "needs_operator_review"

    financial_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--operator-seed-input",
            str(financial_seed),
            "--required-company-ids",
            "18",
        ]
    )
    financial_report, financial_exit = assistant.run_assistant(financial_args)
    assert financial_exit == 1
    assert any("financial values are forbidden" in item["message"] for item in financial_report["errors"])


def test_official_seed_resolve_probe_controls_network_and_upgrades_generated_seed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    intake = tmp_path / "exact_document_intake.json"
    _write_document_intake(intake, [_empty_document_intake_item(18, "RZD", "https://rzd.ru/")])
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float, max_bytes: int, user_agent: str) -> dict:
        calls.append(url)
        return {
            "status": "ok",
            "url": url,
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "body": "<html></html>",
        }

    monkeypatch.setattr(assistant, "_fetch_candidate_page", fake_fetch)
    no_probe_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18",
        ]
    )
    assistant.run_assistant(no_probe_args)
    assert calls == []

    probe_args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "18",
            "--seed-probe-urls",
            "true",
        ]
    )
    report, exit_code = assistant.run_assistant(probe_args)

    assert exit_code == 0
    assert calls
    generated = [
        seed
        for issuer in report["issuers"]
        for seed in issuer["official_seeds"]
        if seed["source"] == "generated_official_path" and seed["seed_url"].endswith("/investors/")
    ][0]
    assert generated["seed_status"] == "valid_seed"
    assert generated["confidence"] == "high"


def test_official_seed_resolve_runs_candidate_discovery_and_quality_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    intake = tmp_path / "exact_document_intake.json"
    candidate_output = tmp_path / "exact_document_candidates.json"
    candidate_csv = tmp_path / "exact_document_candidates.csv"
    gate_output = tmp_path / "quality_gate.json"
    seed_output = tmp_path / "official_seed_pack.json"
    _write_document_intake(
        intake,
        [
            _empty_document_intake_item(18, "RZD", "https://rzd.ru/"),
            _empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/"),
        ],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {
            "https://rzd.ru/reports/": '<a href="/reports/rzd-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>',
            "https://mostotrest.ru/reports/": '<a href="/reports/mostotrest-annual-ifrs-2025.pdf">Annual audited consolidated IFRS financial statements 2025</a>',
        },
    )
    args = assistant.parse_args(
        [
            "--mode",
            "official-seed-resolve",
            "--document-intake-input",
            str(intake),
            "--source-intake-input",
            str(discovered),
            "--required-company-ids",
            "18,67",
            "--seed-output",
            str(seed_output),
            "--run-candidate-discovery",
            "true",
            "--candidate-output",
            str(candidate_output),
            "--candidate-csv-output",
            str(candidate_csv),
            "--run-quality-gate",
            "true",
            "--quality-gate-json-output",
            str(gate_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    discovery = report["candidate_discovery_report"]
    assert discovery["candidate_count"] == 2
    assert discovery["reviewed_candidate_count"] == 2
    gate = json.loads(gate_output.read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["ready_for_value_extraction"] is True
    assert gate["ready_for_import"] is False
    assert seed_output.is_file()
    assert candidate_output.is_file()
    assert candidate_csv.is_file()


def test_document_resolve_accepts_operator_reviewed_status(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    document_intake = tmp_path / "document_intake.json"
    item = _document_item(
        18,
        "https://rzd.ru/investors/annual-ifrs-2025.pdf",
        "Annual audited consolidated IFRS financial statements 2025",
    )
    item["operator_review_status"] = "operator_reviewed"
    _write_document_intake(document_intake, [item])
    args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(document_intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["resolved_document_count"] == 1
    valid_docs = [
        document
        for document in report["issuers"][0]["document_candidates"]
        if document["document_status"] == "valid_official_document"
    ]
    assert valid_docs
    assert all("revenue" not in document and "values" not in document for document in valid_docs)


def test_document_resolve_operator_exact_document_updates_source_intake(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    document_intake = tmp_path / "document_intake.json"
    resolved = tmp_path / "resolved_source_intake.json"
    _write_document_intake(
        document_intake,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/ifrs-2025.pdf",
                "Audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--source-intake-output",
            str(resolved),
            "--document-intake-input",
            str(document_intake),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["resolved_document_count"] == 1
    rzd_docs = report["issuers"][0]["document_candidates"]
    valid_docs = [item for item in rzd_docs if item["document_status"] == "valid_official_document"]
    assert len(valid_docs) == 1
    assert valid_docs[0]["document_url"] == "https://rzd.ru/investors/ifrs-2025.pdf"
    assert "revenue" not in valid_docs[0]
    resolved_payload = json.loads(resolved.read_text(encoding="utf-8"))
    rzd_sources = resolved_payload["issuer_sources"][0]["source_candidates"]
    assert any(
        source.get("status") == "valid_official_source"
        and source.get("url") == "https://rzd.ru/investors/ifrs-2025.pdf"
        for source in rzd_sources
    )


def test_document_resolve_blocks_bad_document_domain(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    document_intake = tmp_path / "bad_document_intake.json"
    _write_document_intake(
        document_intake,
        [
            _document_item(
                18,
                "https://wikipedia.org/wiki/RZD",
                "Wikipedia page",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(document_intake),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("blocked unofficial source domain" in item["message"] for item in report["errors"])


def test_document_resolve_unknown_domain_allow_stays_review_required(
    tmp_path: Path,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    document_intake = tmp_path / "unknown_document_intake.json"
    _write_document_intake(
        document_intake,
        [
            _document_item(
                18,
                "https://example.com/report.pdf",
                "Audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(document_intake),
            "--allow-unknown-source",
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    unknown_doc = report["issuers"][0]["document_candidates"][-1]
    assert unknown_doc["document_status"] == "needs_operator_review"
    assert unknown_doc["document_status"] != "valid_official_document"


def test_document_validate_exact_and_landing_documents(
    tmp_path: Path,
) -> None:
    document_input = tmp_path / "documents.json"
    _write_document_report(
        document_input,
        [
            {
                "company_id": 18,
                "company_name": "RZD",
                "canonical_company_id": 18,
                "canonical_company_name": "RZD",
                "report_period": "2025",
                "document_candidates": [
                    _document_candidate(
                        "https://rzd.ru/investors/ifrs-2025.pdf",
                        "Audited consolidated IFRS financial statements 2025",
                        "valid_official_document",
                    ),
                    {
                        **_document_candidate(
                            "https://rzd.ru/",
                            "",
                            "needs_operator_review",
                        ),
                        "source_file_name": "",
                    },
                ],
            }
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-validate",
            "--document-input",
            str(document_input),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["valid_document_count"] == 1
    assert report["invalid_document_count"] == 0
    assert report["needs_operator_review_count"] == 1
    assert any("landing page" in item["message"] for item in report["warnings"])


def test_document_validate_fails_bad_metadata_and_financial_values(
    tmp_path: Path,
) -> None:
    document_input = tmp_path / "bad_documents.json"
    bad_doc = _document_candidate(
        "",
        "Audited consolidated IFRS financial statements 2025",
        "valid_official_document",
    )
    bad_doc["revenue"] = "1000"
    bad_doc["report_period"] = "2024"
    _write_document_report(
        document_input,
        [
            {
                "company_id": 18,
                "company_name": "RZD",
                "canonical_company_id": 18,
                "canonical_company_name": "RZD",
                "report_period": "2025",
                "document_candidates": [bad_doc],
            }
        ],
    )
    args = assistant.parse_args(
        [
            "--mode",
            "document-validate",
            "--document-input",
            str(document_input),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert any("document_url is required" in item["message"] for item in report["errors"])
    assert any("financial values are forbidden" in item["message"] for item in report["errors"])
    assert any("report_period does not match" in item["message"] for item in report["errors"])


def test_document_download_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    discovered = _build_discovered_intake(tmp_path)
    document_intake = tmp_path / "document_intake.json"
    _write_document_intake(
        document_intake,
        [
            _document_item(
                18,
                "https://rzd.ru/investors/ifrs-2025.pdf",
                "Audited consolidated IFRS financial statements 2025",
            )
        ],
    )
    calls: list[str] = []

    def fake_download(document: dict, download_dir: Path) -> dict:
        calls.append(document["document_url"])
        return {
            "url": document["document_url"],
            "local_path": str(download_dir / "rzd_ifrs_2025.pdf"),
            "sha256": "abc123",
            "size_bytes": 123,
            "content_type": "application/pdf",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(assistant, "_download_valid_document", fake_download)
    no_download_args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(document_intake),
        ]
    )
    no_download_report, _exit_code = assistant.run_assistant(no_download_args)
    assert calls == []
    assert "download" not in no_download_report["issuers"][0]["document_candidates"][-1]

    download_args = assistant.parse_args(
        [
            "--mode",
            "document-resolve",
            "--source-intake-input",
            str(discovered),
            "--document-intake-input",
            str(document_intake),
            "--download-documents",
            "--document-download-dir",
            str(tmp_path / "downloads"),
        ]
    )
    download_report, exit_code = assistant.run_assistant(download_args)

    assert exit_code == 0
    assert calls == ["https://rzd.ru/investors/ifrs-2025.pdf"]
    download = download_report["issuers"][0]["document_candidates"][-1]["download"]
    assert download["sha256"] == "abc123"
    assert download["size_bytes"] == 123


def test_source_validate_blocks_unofficial_and_unknown_domains(
    tmp_path: Path,
) -> None:
    source_intake = tmp_path / "sources.json"
    _write_source_intake(
        source_intake,
        [
            _source_issuer(
                18,
                "RZD",
                "official_issuer_report",
                "https://rzd.ru/investors/ifrs-2025.pdf",
                "IFRS consolidated statements 2025",
            ),
            _source_issuer(
                67,
                "Mostotrest",
                "official_issuer_report",
                "https://wikipedia.org/wiki/Mostotrest",
                "Wikipedia page",
            ),
            _source_issuer(
                77,
                "Unknown Domain",
                "official_issuer_report",
                "https://example.com/report.pdf",
                "Annual report 2025",
            ),
        ],
    )

    args = assistant.parse_args(
        [
            "--mode",
            "source-validate",
            "--source-intake-input",
            str(source_intake),
        ]
    )
    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert report["valid_source_count"] == 1
    assert report["invalid_source_count"] == 2
    assert any("blocked source" in item["message"] for item in report["errors"])
    assert any("not in the official allowlist" in item["message"] for item in report["errors"])

    allow_args = assistant.parse_args(
        [
            "--mode",
            "source-validate",
            "--source-intake-input",
            str(source_intake),
            "--allow-unknown-source",
        ]
    )
    allow_report, allow_exit_code = assistant.run_assistant(allow_args)

    assert allow_exit_code == 1
    assert allow_report["valid_source_count"] == 2
    assert allow_report["invalid_source_count"] == 1
    assert any("operator review required" in item["message"] for item in allow_report["warnings"])


def test_candidate_fill_writes_evidence_backed_values_only(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    source_intake = tmp_path / "source_intake.json"
    manual_values = tmp_path / "manual_values.json"
    candidate = tmp_path / "candidate.csv"
    evidence_output = tmp_path / "evidence.json"
    _build_source_template(financial_template, evidence_template, checklist, source_intake)
    manual_values.write_text(
        json.dumps(
            {
                "status": "operator_reviewed",
                "items": [
                    {
                        "company_id": 18,
                        "canonical_company_id": 18,
                        "period_year": 2025,
                        "source_type": "official_issuer_report",
                        "source_url": "https://rzd.ru/investors/ifrs-2025.pdf",
                        "source_file_name": "rzd_ifrs_2025.pdf",
                        "source_document_title": "IFRS consolidated financial statements 2025",
                        "source_document_date": "2026-03-15",
                        "operator_review_status": "reviewed",
                        "values": {
                            "revenue": {
                                "value": "1000",
                                "page": "10",
                                "table": "Consolidated profit or loss",
                                "evidence_note": "Revenue line",
                            },
                            "ebitda": {
                                "value": "200",
                                "page": "",
                                "table": "",
                                "evidence_note": "",
                            },
                            "cash": {
                                "value": "",
                                "page": "",
                                "table": "",
                                "evidence_note": "",
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = assistant.parse_args(
        [
            "--mode",
            "candidate-fill",
            "--financial-template-input",
            str(financial_template),
            "--source-intake-input",
            str(source_intake),
            "--manual-values-json",
            str(manual_values),
            "--candidate-output",
            str(candidate),
            "--candidate-format",
            "csv",
            "--evidence-output",
            str(evidence_output),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] == "warning"
    rows = list(csv.DictReader(candidate.open(encoding="utf-8")))
    assert rows[0]["revenue"] == "1000"
    assert rows[0]["ebitda"] == ""
    assert rows[0]["cash"] == ""
    assert rows[0]["source_url"] == "https://rzd.ru/investors/ifrs-2025.pdf"
    assert rows[0]["review_status"] == "reviewed"
    assert rows[1]["revenue"] == ""
    evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
    assert evidence["import_executed"] is False
    assert evidence["items"][0]["filled_fields"] == ["revenue"]
    assert "ebitda" in evidence["items"][0]["missing_fields"]
    assert any("no page/table/evidence_note" in item["message"] for item in report["warnings"])


def test_candidate_fill_rejects_bad_financial_evidence(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    source_intake = tmp_path / "source_intake.json"
    manual_values = tmp_path / "manual_values.json"
    _build_source_template(financial_template, evidence_template, checklist, source_intake)
    manual_values.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "company_id": 18,
                        "canonical_company_id": 18,
                        "period_year": 2025,
                        "source_type": "official_issuer_report",
                        "source_url": "https://rzd.ru/investors/ifrs-2025.pdf",
                        "source_document_title": "IFRS consolidated financial statements 2025",
                        "source_document_date": "2026-03-15",
                        "values": {
                            "interest_expense": {
                                "value": "50",
                                "page": "22",
                                "table": "Bond coupon schedule",
                                "evidence_note": "Coupon payments",
                            },
                            "equity": {
                                "value": "100",
                                "page": "1",
                                "table": "Issuer market cap",
                                "evidence_note": "Market capitalization",
                            },
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = assistant.parse_args(
        [
            "--mode",
            "candidate-fill",
            "--financial-template-input",
            str(financial_template),
            "--source-intake-input",
            str(source_intake),
            "--manual-values-json",
            str(manual_values),
            "--candidate-output",
            str(tmp_path / "candidate.csv"),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert any("coupon payments are not accepted" in item["message"] for item in report["errors"])
    assert any("market capitalization is not accepted" in item["message"] for item in report["errors"])


def test_preview_calls_preview_endpoint_only(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.csv"
    _write_candidate_csv(
        candidate,
        [
            _candidate_row(
                source_url="https://rzd.ru/investors/ifrs-2025.pdf",
                source_file_name="rzd_ifrs_2025.pdf",
                source_document_title="IFRS consolidated financial statements 2025",
                source_document_date="2026-03-15",
                source_page="10",
                source_table="Consolidated profit or loss",
                revenue="1000",
                cash="100",
            )
        ],
    )
    calls: list[tuple[str, str]] = []
    args = assistant.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--candidate-input",
            str(candidate),
            "--format",
            "csv",
        ]
    )

    report, exit_code = assistant.run_assistant(args, http_request=_preview_http(calls))

    assert exit_code == 0
    assert report["status"] == "warning"
    assert report["import_executed"] is False
    assert not report["errors"]
    assert any(url.endswith("/api/financial-reports/preview") for _method, url in calls)
    assert not any(url.endswith("/api/financial-reports/ingest") for _method, url in calls)


def test_preview_blocks_wikipedia_before_backend_call(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.csv"
    _write_candidate_csv(
        candidate,
        [
            _candidate_row(
                source_url="https://wikipedia.org/wiki/RZD",
                revenue="1000",
            )
        ],
    )
    calls: list[tuple[str, str]] = []
    args = assistant.parse_args(
        [
            "--mode",
            "preview",
            "--backend-url",
            "http://testserver",
            "--candidate-input",
            str(candidate),
            "--format",
            "csv",
        ]
    )

    report, exit_code = assistant.run_assistant(args, http_request=_preview_http(calls))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert calls == []
    assert any("blocked source" in item["message"] for item in report["errors"])


def test_candidate_fill_blocks_many_zero_fields(
    tmp_path: Path,
) -> None:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    source_intake = tmp_path / "source_intake.json"
    manual_values = tmp_path / "manual_values.json"
    _build_source_template(financial_template, evidence_template, checklist, source_intake)
    manual_values.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "company_id": 18,
                        "canonical_company_id": 18,
                        "period_year": 2025,
                        "source_type": "official_issuer_report",
                        "source_url": "https://rzd.ru/investors/ifrs-2025.pdf",
                        "source_document_title": "IFRS consolidated financial statements 2025",
                        "source_document_date": "2026-03-15",
                        "values": {
                            "revenue": {"value": "0", "page": "1", "table": "A", "evidence_note": "Revenue"},
                            "ebitda": {"value": "0", "page": "1", "table": "A", "evidence_note": "EBITDA"},
                            "cash": {"value": "0", "page": "2", "table": "B", "evidence_note": "Cash"},
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = assistant.parse_args(
        [
            "--mode",
            "candidate-fill",
            "--financial-template-input",
            str(financial_template),
            "--source-intake-input",
            str(source_intake),
            "--manual-values-json",
            str(manual_values),
            "--candidate-output",
            str(tmp_path / "candidate.csv"),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 1
    assert any("many financial fields are zero" in item["message"] for item in report["errors"])


def _write_task95_outputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    financial_template = tmp_path / "collection_ready_financial_template.csv"
    evidence_template = tmp_path / "official_source_evidence_template.json"
    checklist = tmp_path / "official_source_checklist.csv"
    rows = [_template_row(18, "RZD", "7708503727"), _template_row(67, "Mostotrest", "7701045732")]
    with financial_template.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    write_json = {
        "status": "template",
        "read_only": True,
        "issuers": [
            {
                "company_id": 18,
                "company_name": "RZD",
                "canonical_company_id": 18,
                "canonical_company_name": "RZD",
                "identity": {
                    "legal_name": "PJSC RZD",
                    "inn": "7708503727",
                    "ogrn": "1037739877295",
                    "identity_status": "matched",
                    "identity_confidence": 0.9,
                },
            },
            {
                "company_id": 67,
                "company_name": "Mostotrest",
                "canonical_company_id": 67,
                "canonical_company_name": "Mostotrest",
                "identity": {
                    "legal_name": "PJSC Mostotrest",
                    "inn": "7701045732",
                    "ogrn": "1027739167246",
                    "identity_status": "matched",
                    "identity_confidence": 0.85,
                },
            },
        ],
    }
    evidence_template.write_text(json.dumps(write_json, ensure_ascii=False), encoding="utf-8")
    with checklist.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "company_id",
                "company_name",
                "canonical_company_id",
                "canonical_company_name",
                "identity_status",
                "identity_confidence",
                "inn",
                "ogrn",
                "priority_score",
                "priority_level",
                "source_labels",
                "recommended_source_type",
                "official_source_url",
                "source_status",
                "fields_to_collect",
                "notes",
            ],
        )
        writer.writeheader()
        for row in rows:
            for source_type in assistant.SOURCE_INTAKE_SOURCE_TYPES:
                writer.writerow(
                    {
                        "rank": row["company_id"],
                        "company_id": row["company_id"],
                        "company_name": row["company_name"],
                        "canonical_company_id": row["canonical_company_id"],
                        "canonical_company_name": row["canonical_company_name"],
                        "identity_status": row["identity_status"],
                        "identity_confidence": row["identity_confidence"],
                        "inn": row["inn"],
                        "ogrn": row["ogrn"],
                        "priority_score": "100",
                        "priority_level": "high",
                        "source_labels": "top-predictions, bond-universe",
                        "recommended_source_type": source_type,
                        "official_source_url": "",
                        "source_status": "operator_to_find",
                        "fields_to_collect": ", ".join(pack.FIELDS_TO_COLLECT),
                        "notes": assistant.SOURCE_INTAKE_NOTES[source_type],
                    }
                )
    return financial_template, evidence_template, checklist


def _build_source_template(
    financial_template: Path,
    evidence_template: Path,
    checklist: Path,
    source_intake: Path,
) -> None:
    args = assistant.parse_args(
        [
            "--mode",
            "source-template",
            "--financial-template-input",
            str(financial_template),
            "--evidence-template-input",
            str(evidence_template),
            "--source-checklist-input",
            str(checklist),
            "--source-intake-output",
            str(source_intake),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == 0
    assert report["status"] == "passed"


def _build_discovered_intake(tmp_path: Path) -> Path:
    financial_template, evidence_template, checklist = _write_task95_outputs(tmp_path)
    intake = tmp_path / "official_source_intake.json"
    discovered = tmp_path / "official_source_intake_discovered.json"
    _build_source_template(financial_template, evidence_template, checklist, intake)
    args = assistant.parse_args(
        [
            "--mode",
            "source-discover",
            "--source-intake-input",
            str(intake),
            "--source-intake-output",
            str(discovered),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == 0
    assert report["status"] == "warning"
    return discovered


def _write_document_intake(path: Path, documents: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "operator_reviewed",
                "documents": documents,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_operator_seeds(path: Path, seeds: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "operator_reviewed",
                "seeds": seeds,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _operator_seed_template_rows(seed_types: list[str] | None = None) -> list[dict]:
    return [
        {
            "company_id": 18,
            "company_name": "RZD",
            "canonical_company_id": 18,
            "canonical_company_name": "RZD",
            "inn": "7708503727",
            "ogrn": "1037739877295",
            "seed_type": seed_type,
            "seed_url": "",
            "operator_review_status": "operator_to_fill",
            "source_context": "https://www.e-disclosure.ru/ | https://rzd.ru/",
            "notes": "Synthetic operator seed template row.",
        }
        for seed_type in (seed_types or ["official_disclosure_profile", "official_disclosure_reports", "issuer_reports"])
    ]


def _operator_seed_template_rows_for(
    company_id: int,
    company_name: str,
    inn: str,
    ogrn: str,
    *,
    seed_types: list[str] | None = None,
    source_context: str = "",
) -> list[dict]:
    return [
        {
            "company_id": company_id,
            "company_name": company_name,
            "canonical_company_id": company_id,
            "canonical_company_name": company_name,
            "inn": inn,
            "ogrn": ogrn,
            "seed_type": seed_type,
            "seed_url": "",
            "operator_review_status": "operator_to_fill",
            "source_context": source_context,
            "notes": "Synthetic operator seed template row.",
        }
        for seed_type in (seed_types or ["official_disclosure_profile", "official_disclosure_reports", "issuer_reports"])
    ]


def _write_operator_seed_candidates(path: Path, candidates: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-seed-candidate-discover",
                "candidates": candidates,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_operator_seed_review(path: Path, review_items: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "template",
                "mode": "operator-seed-review-template",
                "review_items": review_items,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_reviewed_seed_pack(path: Path, *, include_rzd: bool = False) -> None:
    issuers = []
    if include_rzd:
        issuers.append(
            {
                "company_id": 18,
                "company_name": "RZD",
                "canonical_company_id": 18,
                "canonical_company_name": "RZD",
                "inn": "7708503727",
                "ogrn": "1037739877295",
                "official_seeds": [
                    {
                        "seed_type": "issuer_reports",
                        "seed_url": "https://rzd.ru/reports/",
                        "seed_status": "valid_seed",
                        "confidence": "high",
                        "source": "operator_seed",
                        "operator_review_status": "operator_reviewed",
                        "reason": "Synthetic reviewed official seed.",
                        "warnings": [],
                        "errors": [],
                    }
                ],
                "warnings": [],
                "errors": [],
            }
        )
    issuers.append(
        {
            "company_id": 67,
            "company_name": "Mostotrest",
            "canonical_company_id": 67,
            "canonical_company_name": "Mostotrest",
            "inn": "7701045732",
            "ogrn": "1027739167246",
            "official_seeds": [
                {
                    "seed_type": "issuer_reports",
                    "seed_url": "https://mostotrest.ru/ru/invest/financial-results/",
                    "seed_status": "valid_seed",
                    "confidence": "high",
                    "source": "operator_seed",
                    "operator_review_status": "operator_reviewed",
                    "reason": "Synthetic reviewed official seed.",
                    "warnings": [],
                    "errors": [],
                },
                {
                    "seed_type": "official_disclosure_reports",
                    "seed_url": "https://mostotrest.ru/ru/invest/information-disclosure/",
                    "seed_status": "valid_seed",
                    "confidence": "high",
                    "source": "operator_seed",
                    "operator_review_status": "operator_reviewed",
                    "reason": "Synthetic reviewed official seed.",
                    "warnings": [],
                    "errors": [],
                },
            ],
            "warnings": [],
            "errors": [],
        }
    )
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "official-seed-resolve",
                "issuer_count": len(issuers),
                "issuers": issuers,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
                "paper_trading_called": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_custom_seed_pack(path: Path, issuers: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "official-seed-resolve",
                "issuer_count": len(issuers),
                "issuers": issuers,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
                "paper_trading_called": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _task107_candidate_rows() -> list[dict]:
    return [
        {
            "company_id": 67,
            "company_name": "Mostotrest",
            "canonical_company_id": 67,
            "canonical_company_name": "Mostotrest",
            "inn": "7701045732",
            "ogrn": "1027739167246",
            "seed_type": "issuer_reports",
            "candidate_seed_url": "https://mostotrest.ru/ru/invest/financial-results/",
            "candidate_title": "Financial results",
            "candidate_source_url": "https://mostotrest.ru/",
            "candidate_rank": 1,
            "candidate_score": 145,
            "candidate_confidence": "medium",
            "candidate_status": "needs_operator_review",
            "operator_review_status": "needs_operator_review",
            "filter_status": "kept",
            "score_reasons": ["issuer_reports path signal"],
            "negative_reasons": [],
            "notes": "Synthetic useful candidate.",
        },
        {
            "company_id": 67,
            "company_name": "Mostotrest",
            "canonical_company_id": 67,
            "canonical_company_name": "Mostotrest",
            "inn": "7701045732",
            "ogrn": "1027739167246",
            "seed_type": "official_disclosure_reports",
            "candidate_seed_url": "https://mostotrest.ru/ru/invest/information-disclosure/",
            "candidate_title": "Information disclosure",
            "candidate_source_url": "https://mostotrest.ru/",
            "candidate_rank": 1,
            "candidate_score": 130,
            "candidate_confidence": "medium",
            "candidate_status": "needs_operator_review",
            "operator_review_status": "needs_operator_review",
            "filter_status": "kept",
            "score_reasons": ["disclosure signal"],
            "negative_reasons": [],
            "notes": "Synthetic useful candidate.",
        },
        {
            "company_id": 67,
            "company_name": "Mostotrest",
            "canonical_company_id": 67,
            "canonical_company_name": "Mostotrest",
            "inn": "7701045732",
            "ogrn": "1027739167246",
            "seed_type": "issuer_reports",
            "candidate_seed_url": "https://mostotrest.ru/ru/news/",
            "candidate_title": "News",
            "candidate_source_url": "https://mostotrest.ru/",
            "candidate_rank": None,
            "candidate_score": 0,
            "candidate_confidence": "low",
            "candidate_status": "not_found",
            "operator_review_status": "operator_to_fill",
            "filter_status": "filtered_noise",
            "score_reasons": [],
            "negative_reasons": ["noise navigation page"],
            "notes": "Synthetic noisy candidate.",
        },
    ]


def _task107_review_item(
    *,
    decision: str,
    url: str = "https://mostotrest.ru/ru/invest/financial-results/",
    candidate_status: str = "needs_operator_review",
    review_status: str = "pending_review",
    extra: dict | None = None,
) -> dict:
    item = {
        "company_id": 67,
        "company_name": "Mostotrest",
        "canonical_company_id": 67,
        "canonical_company_name": "Mostotrest",
        "inn": "7701045732",
        "ogrn": "1027739167246",
        "seed_type": "issuer_reports",
        "candidate_seed_url": url,
        "candidate_title": "Financial results",
        "candidate_source_url": "https://mostotrest.ru/",
        "candidate_rank": 1,
        "candidate_score": 145,
        "candidate_confidence": "medium",
        "candidate_status": candidate_status,
        "operator_decision": decision,
        "operator_review_status": "operator_reviewed" if decision == "approve" else "needs_operator_review",
        "review_status": review_status,
        "review_notes": "Synthetic operator review.",
        "suggested_action": "approve_if_official_seed_page",
        "promotion_status": "not_promoted",
        "score_reasons": ["issuer_reports path signal"],
        "negative_reasons": [],
        "notes": "Synthetic review item.",
    }
    if extra:
        item.update(extra)
    return item


def _write_seed_pack(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "official-seed-resolve",
                "issuer_count": 2,
                "issuers": [
                    {
                        "company_id": 18,
                        "company_name": "RZD",
                        "canonical_company_id": 18,
                        "canonical_company_name": "RZD",
                        "inn": "7708503727",
                        "ogrn": "1037739877295",
                        "official_seeds": [
                            {
                                "seed_type": "official_disclosure_home",
                                "seed_url": "https://www.e-disclosure.ru/",
                                "seed_status": "valid_seed",
                                "confidence": "medium",
                                "source": "source_intake",
                                "reason": "official disclosure home",
                                "warnings": [],
                                "errors": [],
                            },
                            {
                                "seed_type": "issuer_home",
                                "seed_url": "https://rzd.ru/",
                                "seed_status": "valid_seed",
                                "confidence": "medium",
                                "source": "source_intake",
                                "reason": "official issuer home",
                                "warnings": [],
                                "errors": [],
                            },
                            {
                                "seed_type": "issuer_reports",
                                "seed_url": "https://rzd.ru/reports/",
                                "seed_status": "needs_operator_review",
                                "confidence": "medium",
                                "source": "generated_official_path",
                                "reason": "probable issuer reporting seed generated from official issuer home",
                                "warnings": [],
                                "errors": [],
                            },
                        ],
                        "warnings": [],
                        "errors": [],
                    },
                    {
                        "company_id": 67,
                        "company_name": "Mostotrest",
                        "canonical_company_id": 67,
                        "canonical_company_name": "Mostotrest",
                        "inn": "7701045732",
                        "ogrn": "1027739167246",
                        "official_seeds": [
                            {
                                "seed_type": "official_disclosure_home",
                                "seed_url": "https://www.e-disclosure.ru/",
                                "seed_status": "valid_seed",
                                "confidence": "medium",
                                "source": "source_intake",
                                "reason": "official disclosure home",
                                "warnings": [],
                                "errors": [],
                            },
                            {
                                "seed_type": "issuer_home",
                                "seed_url": "https://mostotrest.ru/",
                                "seed_status": "valid_seed",
                                "confidence": "medium",
                                "source": "source_intake",
                                "reason": "official issuer home",
                                "warnings": [],
                                "errors": [],
                            },
                        ],
                        "warnings": [],
                        "errors": [],
                    },
                ],
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
                "paper_trading_called": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _document_item(company_id: int, document_url: str, title: str) -> dict:
    return {
        "company_id": company_id,
        "canonical_company_id": company_id,
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "source_type": "official_issuer_report",
        "document_url": document_url,
        "document_title": title,
        "document_date": "2026-03-15",
        "source_file_name": Path(document_url).name or "report.pdf",
        "operator_review_status": "reviewed",
        "notes": "Official issuer PDF",
    }


def _empty_document_intake_item(company_id: int, name: str, source_context: str) -> dict:
    return {
        "company_id": company_id,
        "company_name": name,
        "canonical_company_id": company_id,
        "canonical_company_name": name,
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "source_type": "official_issuer_report",
        "source_url_context": source_context,
        "document_url": "",
        "document_title": "",
        "document_date": "",
        "source_file_name": "",
        "operator_review_status": "operator_to_fill",
        "notes": "Paste exact official annual/audited report page or PDF URL. Do not paste landing page.",
    }


def _mock_candidate_fetch(monkeypatch, pages: dict[str, str]) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float, max_bytes: int, user_agent: str) -> dict:
        calls.append(url)
        return {
            "status": "ok",
            "url": url,
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "body": pages.get(url, "<html></html>"),
            "size_bytes": len(pages.get(url, "")),
        }

    monkeypatch.setattr(assistant, "_fetch_candidate_page", fake_fetch)


def _availability_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("target_reporting_period_availability", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _queue_action_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("operator_review_queue", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _coverage_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("official_source_coverage_rows", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _historical_fallback_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("historical_fallback_registry_rows", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _readiness_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("reporting_readiness_rows", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _resolution_for(report: dict, company_id: int = 67) -> dict:
    return next(
        item
        for item in report.get("operator_resolution_pack_rows", [])
        if str(item.get("company_id")) == str(company_id)
    )


def _operator_resolution_validation_input_row(**updates: str) -> dict[str, str]:
    row = {
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "resolution_action_type": "fill_exact_document_url",
        "operator_fill_exact_document_url": "",
        "operator_fill_document_title": "",
        "operator_fill_document_date": "",
        "operator_fill_source_page_url": "",
        "operator_fill_source_type": "",
        "operator_fill_report_period": "2025",
        "operator_fill_report_type": "annual",
        "operator_fill_accounting_standard": "IFRS",
        "operator_fill_decision": "",
        "operator_fill_notes": "",
        "latest_historical_document_url": "",
        "latest_historical_period": "",
        "requires_exact_document_url": "true",
        "operator_input_required": "true",
    }
    row.update(updates)
    return row


def _write_operator_resolution_validation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = list(dict.fromkeys(field for row in rows for field in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _run_operator_resolution_validation(
    tmp_path: Path,
    rows: list[dict[str, str]],
    *,
    extra_args: list[str] | None = None,
) -> dict:
    input_path = tmp_path / "operator_resolution_input.csv"
    _write_operator_resolution_validation_csv(input_path, rows)
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-validate",
            "--operator-resolution-input",
            str(input_path),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_apply_validation_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "operator_fill_decision": "exact_document_found",
        "operator_fill_exact_document_url": "https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2025.pdf",
        "operator_fill_document_title": "Mostotrest annual IFRS financial statements 2025",
        "operator_fill_document_date": "2026-04-30",
        "operator_fill_source_page_url": "https://mostotrest.ru/ru/invest/financial-results/",
        "operator_fill_source_type": "official_issuer_report",
        "operator_fill_report_period": "2025",
        "operator_fill_report_type": "annual",
        "operator_fill_accounting_standard": "IFRS",
        "validation_status": "valid_for_future_controlled_intake_review",
        "validation_reason_codes": ["strict_target_annual_ifrs_exact_document"],
        "validation_errors": [],
        "can_use_for_future_intake_review": True,
        "document_kind": "exact_report_document",
        "document_period_year": "2025",
        "document_period_status": "target_period",
        "report_type_match_status": "annual_match",
        "accounting_standard_match_status": "standard_match",
        "historical_fallback_url_used_as_exact_document": False,
    }
    row.update(updates)
    return row


def _write_operator_resolution_validation_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "mode": "operator-resolution-validation",
                "validation_rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_apply_preview(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    document_intake_input: Path | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    validation_path = tmp_path / "operator_resolution_validation.json"
    _write_operator_resolution_validation_json(validation_path, rows)
    if document_intake_input is None:
        document_intake_input = tmp_path / "empty_exact_document_intake.json"
        _write_document_intake(document_intake_input, [])
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-apply-preview",
            "--operator-resolution-validation-input",
            str(validation_path),
            "--document-intake-input",
            str(document_intake_input),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_apply_draft_patch_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "patch_id": "operator_resolution_apply_preview:financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "patch_status": "eligible_for_future_controlled_apply",
        "patch_action": "preview_replace_not_found_placeholder",
        "patch_reason_codes": ["strict_target_annual_ifrs_exact_document"],
        "patch_errors": [],
        "patch_warnings": [],
        "source_validation_status": "valid_for_future_controlled_intake_review",
        "can_use_for_future_intake_review": True,
        "operator_fill_decision": "exact_document_found",
        "proposed_document_url": "https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2025.pdf",
        "proposed_document_title": "Mostotrest annual IFRS financial statements 2025",
        "proposed_document_date": "2026-04-30",
        "proposed_source_page_url": "https://mostotrest.ru/ru/invest/financial-results/",
        "proposed_source_type": "official_issuer_report",
        "proposed_report_period": "2025",
        "proposed_report_type": "annual",
        "proposed_accounting_standard": "IFRS",
        "document_kind": "exact_report_document",
        "document_period_year": "2025",
        "document_period_status": "target_period",
        "report_type_match_status": "annual_match",
        "accounting_standard_match_status": "standard_match",
        "would_apply_to_document_intake": False,
        "would_promote_seed": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
        "future_apply_allowed": True,
    }
    row.update(updates)
    return row


def _write_operator_resolution_apply_preview_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "mode": "operator-resolution-apply-preview",
                "patch_rows": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_apply_draft(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    document_intake_input: Path | None = None,
    expected_exit_code: int = 0,
    extra_args: list[str] | None = None,
    return_exit_code: bool = False,
) -> dict | tuple[dict, int]:
    preview_path = tmp_path / "operator_resolution_apply_preview.json"
    _write_operator_resolution_apply_preview_json(preview_path, rows)
    if document_intake_input is None:
        document_intake_input = tmp_path / "exact_document_intake.json"
        _write_document_intake(
            document_intake_input,
            [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
        )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-apply-draft",
            "--operator-resolution-apply-preview-input",
            str(preview_path),
            "--document-intake-input",
            str(document_intake_input),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == expected_exit_code
    return (report, exit_code) if return_exit_code else report


def _run_document_intake_draft_gate_preview(
    tmp_path: Path,
    documents: list[dict] | None = None,
    *,
    draft_input: Path | None = None,
    source_intake_input: Path | None = None,
    expected_exit_code: int = 0,
    extra_args: list[str] | None = None,
) -> dict:
    if draft_input is None:
        draft_input = tmp_path / "exact_document_intake_draft.json"
        _write_document_intake(draft_input, documents or [])
    args = assistant.parse_args(
        [
            "--mode",
            "document-intake-draft-gate-preview",
            "--document-intake-draft-input",
            str(draft_input),
            *(
                [
                    "--source-intake-input",
                    str(source_intake_input),
                ]
                if source_intake_input is not None
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == expected_exit_code
    return report


def _run_operator_resolution_happy_path_synthetic(
    tmp_path: Path,
    *,
    extra_args: list[str] | None = None,
) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-happy-path-synthetic",
            "--operator-resolution-happy-path-output-dir",
            str(tmp_path),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_chain_placeholder(company_id: int, name: str, source_context: str) -> dict:
    return {
        **_empty_document_intake_item(company_id, name, source_context),
        "document_status": "not_found",
        "filter_status": "placeholder_not_found",
        "fallback_status": "not_fallback",
    }


def _operator_resolution_chain_valid_input_row(**updates: str) -> dict[str, str]:
    row = _operator_resolution_validation_input_row(
        operator_fill_decision="exact_document_found",
        operator_fill_exact_document_url="https://mostotrest.ru/reports/mostotrest-annual-ifrs-financial-statements-2025.pdf",
        operator_fill_document_title="Mostotrest annual IFRS financial statements 2025",
        operator_fill_document_date="2026-04-30",
        operator_fill_source_page_url="https://mostotrest.ru/ru/invest/financial-results/",
        operator_fill_source_type="official_issuer_report",
        operator_fill_notes="Task124 real-style preview fixture.",
    )
    row.update(updates)
    return row


def _write_operator_resolution_source_pack(path: Path, rows: list[dict[str, str]]) -> None:
    source_rows = []
    for row in rows:
        source_row = dict(row)
        for field in (
            "operator_fill_exact_document_url",
            "operator_fill_document_title",
            "operator_fill_document_date",
            "operator_fill_source_page_url",
            "operator_fill_source_type",
            "operator_fill_decision",
            "operator_fill_notes",
        ):
            source_row[field] = ""
        source_rows.append(source_row)
    path.write_text(
        json.dumps(
            {
                "status": "template",
                "mode": "operator-resolution-pack",
                "resolutions": source_rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_chain_preview(
    tmp_path: Path,
    rows: list[dict[str, str]],
    documents: list[dict] | None = None,
    *,
    document_intake_input: Path | None = None,
    source_intake_input: Path | None = None,
    include_source_pack: bool = True,
    extra_args: list[str] | None = None,
) -> dict:
    operator_input = tmp_path / "operator_resolution_chain_input.csv"
    source_pack = tmp_path / "operator_resolution_chain_source_pack.json"
    if document_intake_input is None:
        document_intake_input = tmp_path / "exact_document_intake_input.json"
        _write_document_intake(document_intake_input, documents or [])
    _write_operator_resolution_validation_csv(operator_input, rows)
    if include_source_pack:
        _write_operator_resolution_source_pack(source_pack, rows)
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-preview",
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--operator-resolution-input",
            str(operator_input),
            *(
                [
                    "--operator-resolution-source-pack-input",
                    str(source_pack),
                ]
                if include_source_pack
                else []
            ),
            "--document-intake-input",
            str(document_intake_input),
            *(
                [
                    "--source-intake-input",
                    str(source_intake_input),
                ]
                if source_intake_input is not None
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _run_operator_resolution_chain_review_board(
    tmp_path: Path,
    *,
    include_source_pack: bool = True,
    use_output_dir: bool = True,
    extra_args: list[str] | None = None,
) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-chain-review-board",
            *(
                [
                    "--operator-resolution-chain-output-dir",
                    str(tmp_path),
                ]
                if use_output_dir
                else []
            ),
            "--operator-resolution-input",
            str(tmp_path / "operator_resolution_chain_input.csv"),
            *(
                [
                    "--operator-resolution-source-pack-input",
                    str(tmp_path / "operator_resolution_chain_source_pack.json"),
                ]
                if include_source_pack
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_source_trust_board_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "resolution_action_type": "fill_exact_document_url",
        "overall_status": "needs_operator_exact_document_url",
        "primary_blocker": "missing_exact_document_url",
        "next_required_action": "fill_exact_official_target_period_annual_ifrs_url",
        "operator_fill_source_page_url": "",
        "operator_fill_exact_document_url": "",
        "trusted_source_hosts": [],
        "latest_historical_document_url": "",
        "latest_historical_period": "",
    }
    row.update(updates)
    return row


def _operator_resolution_source_trust_source_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "current_known_source_page_url": "",
        "current_known_document_url": "",
        "latest_historical_document_url": "",
        "latest_historical_period": "",
    }
    row.update(updates)
    return row


def _write_operator_resolution_source_trust_board(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-resolution-chain-review-board",
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )


def _write_operator_resolution_source_trust_source_pack(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "template",
                "mode": "operator-resolution-pack",
                "resolutions": rows,
            }
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_source_trust_workspace(
    tmp_path: Path,
    board_rows: list[dict[str, object]],
    source_rows: list[dict[str, object]] | None = None,
    *,
    include_source_pack: bool = True,
    extra_args: list[str] | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    board = tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_REVIEW_BOARD_ARTIFACT_NAMES["board_json"]
    source_pack = tmp_path / "operator_resolution_source_pack.json"
    _write_operator_resolution_source_trust_board(board, board_rows)
    if include_source_pack:
        _write_operator_resolution_source_trust_source_pack(source_pack, source_rows or [])
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-workspace",
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            *(
                [
                    "--operator-resolution-source-pack-input",
                    str(source_pack),
                ]
                if include_source_pack
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_source_trust_refill_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "READONLY_source_trust_status": "trusted_source_missing",
        "READONLY_trusted_source_hosts": "",
        "READONLY_current_known_source_page_url": "",
        "READONLY_current_known_document_url": "",
        "READONLY_latest_historical_document_url": "",
        "READONLY_historical_fallback_allowed_as_trusted_source": "false",
        "READONLY_historical_fallback_allowed_as_target_evidence": "false",
        "READONLY_next_required_action": "fill_official_baseline_source_page_for_future_review",
        "READONLY_operator_instruction": "Fill an official issuer reporting page.",
        "READONLY_safe_source_fill_hint": "Manual URLs require later controlled review.",
        "operator_fill_current_known_source_page_url": "",
        "operator_fill_current_known_document_url": "",
        "operator_fill_source_review_status": "",
        "operator_fill_source_notes": "",
    }
    row.update(updates)
    return row


def _write_operator_resolution_source_trust_refill(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_REFILL_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_operator_resolution_source_trust_refill_validate(
    tmp_path: Path,
    refill_rows: list[dict[str, object]],
    source_rows: list[dict[str, object]] | None = None,
    *,
    source_pack_path: Path | None = None,
    include_source_pack: bool = True,
    include_board: bool = True,
    extra_args: list[str] | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    refill = tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_ARTIFACT_NAMES["refill_csv"]
    source_pack = source_pack_path or tmp_path / "operator_resolution_source_pack.json"
    board = tmp_path / assistant.OPERATOR_RESOLUTION_CHAIN_REVIEW_BOARD_ARTIFACT_NAMES["board_json"]
    _write_operator_resolution_source_trust_refill(refill, refill_rows)
    if include_source_pack and source_pack_path is None:
        _write_operator_resolution_source_trust_source_pack(source_pack, source_rows or [])
    if include_board:
        _write_operator_resolution_source_trust_board(
            board,
            [
                _operator_resolution_source_trust_board_row(
                    resolution_id=str(row.get("resolution_id") or ""),
                    company_id=str(row.get("company_id") or ""),
                    company_name=str(row.get("company_name") or ""),
                    canonical_company_id=str(row.get("canonical_company_id") or ""),
                    canonical_company_name=str(row.get("canonical_company_name") or ""),
                )
                for row in refill_rows
            ],
        )
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-refill-validate",
            "--operator-resolution-chain-output-dir",
            str(tmp_path),
            "--operator-resolution-source-trust-refill-input",
            str(refill),
            *(
                [
                    "--operator-resolution-source-pack-input",
                    str(source_pack),
                ]
                if include_source_pack
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _operator_resolution_source_pack_draft_row(**updates: object) -> dict[str, object]:
    row = _operator_resolution_source_trust_source_row()
    row.update(
        {
            "target_reporting_period": "2025",
            "required_report_type": "annual",
            "required_standard": "IFRS",
            "candidate_current_known_source_page_url": "",
            "candidate_current_known_document_url": "",
            "candidate_operator_fill_source_review_status": "",
            "candidate_source_notes": "",
            "source_context_status": "",
            "operator_source_review_status": "",
            "source_context_origin": "",
            "trusted_host_status": "",
        }
    )
    row.update(updates)
    return row


def _write_operator_resolution_source_pack_draft(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-resolution-pack",
                "resolutions": rows,
            }
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_source_trust_draft_review(
    tmp_path: Path,
    draft_rows: list[dict[str, object]],
    *,
    draft_path: Path | None = None,
    validation_rows: list[dict[str, object]] | None = None,
    patch_rows: list[dict[str, object]] | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    draft = draft_path or tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_REFILL_ARTIFACT_NAMES[
        "source_pack_draft_json"
    ]
    if draft_path is None:
        _write_operator_resolution_source_pack_draft(draft, draft_rows)
    validation = tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_REFILL_ARTIFACT_NAMES["validation_json"]
    patch = tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_REFILL_ARTIFACT_NAMES["patch_preview_json"]
    if validation_rows is not None:
        validation.write_text(json.dumps({"rows": validation_rows}), encoding="utf-8")
    if patch_rows is not None:
        patch.write_text(json.dumps({"patch_rows": patch_rows}), encoding="utf-8")
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-draft-review",
            "--operator-resolution-source-pack-draft-input",
            str(draft),
            *(
                [
                    "--operator-resolution-source-trust-validation-input",
                    str(validation),
                ]
                if validation_rows is not None
                else []
            ),
            *(
                [
                    "--operator-resolution-source-trust-patch-preview-input",
                    str(patch),
                ]
                if patch_rows is not None
                else []
            ),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _load_task128_promote_rows(report: dict) -> list[dict]:
    payload = json.loads(Path(report["artifacts"]["promote_preview_json"]).read_text(encoding="utf-8"))
    return payload["promote_preview_rows"]


def _operator_resolution_source_trust_promote_preview_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "promote_preview_id": "operator_source_trust_promote_preview:financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "review_id": "operator_source_trust_review:financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "resolution_id": "financial_report_resolution:67:2025:annual:IFRS:fill_exact_document_url",
        "company_id": "67",
        "company_name": "Mostotrest",
        "promote_preview_status": "eligible_for_source_pack_promote_draft",
        "promote_preview_action": "preview_promote_candidate_source_context_in_draft",
        "promote_preview_reason_codes": [],
        "proposed_current_known_source_page_url": "https://mostotrest.ru/reports/",
        "proposed_current_known_document_url": "https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf",
        "would_update_original_source_pack": False,
        "would_trust_manual_source": False,
        "would_promote_source_now": False,
        "would_update_database": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
    row.update(updates)
    return row


def _operator_resolution_source_pack_promote_draft_row(**updates: object) -> dict[str, object]:
    row = _operator_resolution_source_pack_draft_row(
        current_known_source_page_url="https://mostotrest.ru/reports/",
        current_known_document_url="https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf",
        candidate_current_known_source_page_url="https://mostotrest.ru/reports/",
        candidate_current_known_document_url="https://docs.mostotrest.ru/reports/annual-ifrs-2025.pdf",
        source_context_status="source_context_candidate_reviewed_for_future_merge",
        operator_source_review_status="reviewed_for_future_controlled_merge",
        source_context_origin="operator_resolution_source_trust_draft_review_task128",
        trusted_host_status="candidate_reviewed_not_yet_baseline_trusted",
    )
    row.update(updates)
    return row


def _write_operator_resolution_source_trust_promote_preview(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-resolution-source-trust-promote-preview",
                "promote_preview_rows": rows,
            }
        ),
        encoding="utf-8",
    )


def _run_operator_resolution_source_trust_promote_apply(
    tmp_path: Path,
    preview_rows: list[dict[str, object]],
    draft_rows: list[dict[str, object]],
    *,
    preview_path: Path | None = None,
    draft_path: Path | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    preview = preview_path or tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_DRAFT_REVIEW_ARTIFACT_NAMES[
        "promote_preview_json"
    ]
    draft = draft_path or tmp_path / assistant.OPERATOR_RESOLUTION_SOURCE_TRUST_DRAFT_REVIEW_ARTIFACT_NAMES[
        "source_pack_promote_draft_json"
    ]
    if preview_path is None:
        _write_operator_resolution_source_trust_promote_preview(preview, preview_rows)
    if draft_path is None:
        _write_operator_resolution_source_pack_draft(draft, draft_rows)
    args = assistant.parse_args(
        [
            "--mode",
            "operator-resolution-source-trust-promote-apply-draft",
            "--operator-resolution-source-trust-promote-preview-input",
            str(preview),
            "--operator-resolution-source-pack-promote-draft-input",
            str(draft),
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    return report


def _run_financial_metric_registry_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "financial-metric-registry-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_financial_extraction_evidence_schema_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "financial-extraction-evidence-schema-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_document_artifact_retention_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "financial-document-artifact-retention-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_backup_retention_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "backup-retention-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_backup_retention_apply_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "backup-retention-apply-draft-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_backup_retention_controlled_apply(extra_args: list[str] | None = None) -> tuple[dict, int]:
    args = assistant.parse_args(
        [
            "--mode",
            "backup-retention-controlled-apply",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)
    return report, exit_code


def _run_backup_retention_execute_readiness(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "backup-retention-execute-readiness-board",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _write_backup_retention_apply_file(path: Path, content: bytes, *, timestamp: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (timestamp, timestamp))
    return path


def _backup_retention_apply_rotation_row(path: Path, **updates: object) -> dict[str, object]:
    stat = path.stat() if path.exists() else None
    row: dict[str, object] = {
        "rotation_plan_id": f"backup_retention_rotation:{path.name}",
        "path": str(path),
        "file_name": path.name,
        "rotation_action": "candidate_delete_old_backup",
        "rotation_reason": "recognized_backup_unprotected_at_or_above_warning_threshold",
        "size_bytes": stat.st_size if stat is not None else 0,
        "size_mb": assistant._bytes_to_mb(stat.st_size if stat is not None else 0),
        "size_gb": assistant._bytes_to_gb(stat.st_size if stat is not None else 0),
        "mtime_utc": assistant._timestamp_to_utc_iso(stat.st_mtime if stat is not None else None),
        "recognized_backup": True,
        "protection_reasons": [],
        "estimated_reclaimable_bytes": stat.st_size if stat is not None else 0,
        "manual_command_hint": "# Preview only: review this file manually before deletion.",
        "would_delete_files": False,
    }
    row.update(updates)
    return row


def _write_backup_retention_apply_inputs(
    output_dir: Path,
    rotation_rows: list[dict[str, object]],
    *,
    preview_status: str = "warning",
    include_inventory: bool = True,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backup_retention_preview_task138.json").write_text(
        json.dumps({"status": preview_status, "mode": "backup-retention-preview"}),
        encoding="utf-8",
    )
    (output_dir / "backup_retention_rotation_plan_task138.json").write_text(
        json.dumps(
            {
                "status": preview_status,
                "mode": "backup-retention-rotation-plan-preview",
                "rotation_plan_rows": rotation_rows,
            }
        ),
        encoding="utf-8",
    )
    if include_inventory:
        (output_dir / "backup_retention_inventory_task138.json").write_text(
            json.dumps(
                {
                    "status": preview_status,
                    "mode": "backup-retention-inventory-preview",
                    "inventory_rows": [
                        {
                            "inventory_id": f"backup_retention_inventory:{row['file_name']}",
                            "path": row["path"],
                        }
                        for row in rotation_rows
                    ],
                }
            ),
            encoding="utf-8",
        )


def _backup_retention_controlled_apply_manifest_row(path: Path, **updates: object) -> dict[str, object]:
    stat = path.stat() if path.exists() else None
    apply_id = f"backup_retention_apply:{path.name}"
    row: dict[str, object] = {
        "manifest_id": f"backup_retention_cleanup_manifest:{apply_id}",
        "apply_id": apply_id,
        "file_name": path.name,
        "file_path": str(path),
        "size_bytes": stat.st_size if stat is not None else 0,
        "size_mb": assistant._bytes_to_mb(stat.st_size if stat is not None else 0),
        "size_gb": assistant._bytes_to_gb(stat.st_size if stat is not None else 0),
        "mtime_utc": assistant._timestamp_to_utc_iso(stat.st_mtime if stat is not None else None),
        "sha256_optional": "not_calculated_preview_only",
        "eligible_for_future_manual_delete": True,
        "manual_review_required": True,
        "cleanup_script_line": f"# rm -- '{path}'",
        "cleanup_script_line_enabled": False,
        "would_delete_file": False,
    }
    row.update(updates)
    return row


def _write_backup_retention_controlled_apply_manifest(
    output_dir: Path,
    manifest_rows: list[dict[str, object]],
    *,
    include_optional: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "backup_retention_cleanup_manifest_task139.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "backup-retention-cleanup-manifest-preview",
                "manifest_row_count": len(manifest_rows),
                "cleanup_manifest_rows": manifest_rows,
            }
        ),
        encoding="utf-8",
    )
    if include_optional:
        (output_dir / "backup_retention_apply_preview_task139.json").write_text(
            json.dumps(
                {
                    "status": "warning",
                    "mode": "backup-retention-apply-draft-preview",
                    "apply_rows": [
                        {
                            "apply_id": row["apply_id"],
                            "file_path": row["file_path"],
                            "current_size_bytes": row["size_bytes"],
                            "current_mtime_utc": row["mtime_utc"],
                            "apply_status": "eligible_manual_delete_preview",
                        }
                        for row in manifest_rows
                    ],
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "backup_retention_apply_blockers_task139.json").write_text(
            json.dumps(
                {
                    "status": "warning",
                    "mode": "backup-retention-apply-blockers-preview",
                    "blocker_rows": [],
                }
            ),
            encoding="utf-8",
        )
    return manifest


def _write_backup_retention_controlled_apply_inputs(output_dir: Path, paths: list[Path]) -> Path:
    return _write_backup_retention_controlled_apply_manifest(
        output_dir,
        [_backup_retention_controlled_apply_manifest_row(path) for path in paths],
        include_optional=True,
    )


def _backup_retention_controlled_apply_confirmation(manifest: Path) -> tuple[str, str]:
    sha256 = assistant.hashlib.sha256(manifest.read_bytes()).hexdigest()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    token = f"CONFIRM_BACKUP_DELETE_TASK139_{sha256[:12]}_{len(payload['cleanup_manifest_rows'])}"
    return sha256, token


def _write_backup_retention_execute_readiness_inputs(
    output_dir: Path,
    *,
    eligible_count: int = 25,
    limit_blocked_count: int = 53,
    unsafe_blocker_code: str | None = None,
    unsafe_status: str = "blocked_unsafe_path",
    task140_updates: dict[str, object] | None = None,
    ledger_updates: dict[str, object] | None = None,
    include_optional: bool = True,
    omit_hash: bool = False,
    omit_token: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = [
        _backup_retention_controlled_apply_manifest_row(
            output_dir.parent / "backups" / f"backup_{index:03d}.dump",
            size_bytes=10,
            size_mb=assistant._bytes_to_mb(10),
            size_gb=assistant._bytes_to_gb(10),
            mtime_utc="2026-01-01T00:00:00Z",
        )
        for index in range(eligible_count + limit_blocked_count + (1 if unsafe_blocker_code else 0))
    ]
    manifest = _write_backup_retention_controlled_apply_manifest(output_dir, manifest_rows, include_optional=True)
    sha256, token = _backup_retention_controlled_apply_confirmation(manifest)
    eligible_rows = [
        {
            "controlled_apply_id": f"controlled:{index}",
            "manifest_id": manifest_rows[index]["manifest_id"],
            "apply_id": manifest_rows[index]["apply_id"],
            "file_name": manifest_rows[index]["file_name"],
            "file_path": manifest_rows[index]["file_path"],
            "controlled_apply_status": "dry_run_eligible_for_delete",
            "controlled_apply_reason_codes": [],
            "did_delete_file": False,
        }
        for index in range(eligible_count)
    ]
    limit_rows = [
        {
            "controlled_apply_id": f"controlled:limit:{index}",
            "manifest_id": manifest_rows[eligible_count + index]["manifest_id"],
            "apply_id": manifest_rows[eligible_count + index]["apply_id"],
            "file_name": manifest_rows[eligible_count + index]["file_name"],
            "file_path": manifest_rows[eligible_count + index]["file_path"],
            "controlled_apply_status": "blocked_delete_count_limit_exceeded",
            "controlled_apply_reason_codes": ["delete_count_limit_exceeded"],
            "did_delete_file": False,
        }
        for index in range(limit_blocked_count)
    ]
    unsafe_rows = []
    if unsafe_blocker_code:
        row_index = eligible_count + limit_blocked_count
        unsafe_rows.append(
            {
                "controlled_apply_id": "controlled:unsafe:0",
                "manifest_id": manifest_rows[row_index]["manifest_id"],
                "apply_id": manifest_rows[row_index]["apply_id"],
                "file_name": manifest_rows[row_index]["file_name"],
                "file_path": manifest_rows[row_index]["file_path"],
                "controlled_apply_status": unsafe_status,
                "controlled_apply_reason_codes": [unsafe_blocker_code],
                "did_delete_file": False,
            }
        )
    controlled_rows = eligible_rows + limit_rows + unsafe_rows
    blocker_rows = [
        {
            "blocker_id": f"blocker:{index}",
            "controlled_apply_id": row["controlled_apply_id"],
            "manifest_id": row["manifest_id"],
            "apply_id": row["apply_id"],
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "controlled_apply_status": row["controlled_apply_status"],
            "blocker_code": (row["controlled_apply_reason_codes"] or [""])[0],
            "severity": "warning",
            "next_manual_action": "Review blocker.",
            "would_delete_file": False,
        }
        for index, row in enumerate(limit_rows + unsafe_rows)
    ]
    ledger_rows = [
        {
            "ledger_id": f"ledger:{index}",
            "controlled_apply_id": row["controlled_apply_id"],
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "size_bytes": 10,
            "mtime_utc": "2026-01-01T00:00:00Z",
            "execute_requested": False,
            "deletion_execution_enabled": False,
            "controlled_apply_status": row["controlled_apply_status"],
            "did_delete_file": False,
            "file_exists_before": True,
            "file_exists_after": True,
            "delete_exception": "",
            "ledger_status": "dry_run_noop" if row["controlled_apply_status"] == "dry_run_eligible_for_delete" else "blocked",
            "ledger_reason_codes": row["controlled_apply_reason_codes"],
            **(ledger_updates or {}),
        }
        for index, row in enumerate(controlled_rows)
    ]
    estimated_bytes = eligible_count * 10
    task140 = {
        "status": "warning",
        "mode": "backup-retention-controlled-apply",
        "execute_requested": False,
        "deletion_execution_enabled": False,
        "cleanup_manifest_sha256": "" if omit_hash else sha256,
        "confirmation_token_expected": "" if omit_token else token,
        "manifest_input_count": len(manifest_rows),
        "dry_run_eligible_count": eligible_count,
        "deleted_count": 0,
        "blocked_count": len(blocker_rows),
        "failed_count": 0,
        "ledger_row_count": len(ledger_rows),
        "post_apply_backup_file_count": 102,
        "post_apply_recognized_backup_file_count": 102,
        "post_apply_recognized_backup_size_gb": 3.036186,
        "manifest_reclaimable_bytes": len(manifest_rows) * 10,
        "estimated_reclaimable_bytes": estimated_bytes,
        "estimated_reclaimable_gb": assistant._bytes_to_gb(estimated_bytes),
        "actual_reclaimed_bytes": 0,
        "actual_reclaimed_gb": 0,
        "max_delete_count": 25,
        "max_delete_gb": 1.0,
        "controlled_apply_status_counts": assistant._count_values(controlled_rows, "controlled_apply_status"),
        "blocker_code_counts": assistant._count_values(blocker_rows, "blocker_code"),
        "controlled_apply_rows": controlled_rows,
        "blocker_rows": blocker_rows,
        "deletion_ledger_rows": ledger_rows,
        "post_apply_snapshot_rows": [
            {
                "snapshot_id": f"snapshot:{index}",
                "file_name": f"backup_{index:03d}.dump",
                "file_path": str(output_dir.parent / "backups" / f"backup_{index:03d}.dump"),
                "entry_type": "recognized_backup",
                "recognized_backup_file": True,
                "size_bytes": 10,
                "size_mb": assistant._bytes_to_mb(10),
                "size_gb": assistant._bytes_to_gb(10),
                "mtime_utc": "2026-01-01T00:00:00Z",
                "is_regular_file": True,
                "is_symlink": False,
            }
            for index in range(102)
        ],
        "read_only": False,
        "dry_run_only": True,
        "cleanup_executed": False,
        "files_deleted": False,
        "files_moved": False,
        "files_compressed": False,
        "files_uploaded": False,
        "database_mutated": False,
        "documents_downloaded": False,
        "documents_parsed": False,
        "import_executed": False,
        "paper_trading_called": False,
        "would_delete_files": True,
        "would_fetch_documents": False,
        "would_download_documents": False,
        "would_parse_documents": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
    task140.update(task140_updates or {})
    (output_dir / "backup_retention_controlled_apply_task140.json").write_text(
        json.dumps(task140),
        encoding="utf-8",
    )
    if include_optional:
        (output_dir / "backup_retention_preview_task138.json").write_text(
            json.dumps(
                {
                    "status": "warning",
                    "mode": "backup-retention-preview",
                    "at_or_over_warning_threshold": True,
                    "over_max_size_limit": True,
                    "rotation_candidate_count": len(manifest_rows),
                    "recognized_backup_size_gb": 3.1,
                    "warning_threshold_size_gb": 2.7,
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "backup_retention_deletion_ledger_task140.json").write_text(
            json.dumps(
                {
                    "status": "warning",
                    "mode": "backup-retention-deletion-ledger",
                    "deletion_ledger_rows": ledger_rows,
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "backup_retention_post_apply_snapshot_task140.json").write_text(
            json.dumps(
                {
                    "status": "warning",
                    "mode": "backup-retention-post-apply-snapshot",
                    "post_apply_snapshot_rows": task140["post_apply_snapshot_rows"],
                }
            ),
            encoding="utf-8",
        )
    return manifest


def _document_artifact_retention_test_paths(tmp_path: Path) -> list[str]:
    return [
        "--document-artifact-root-dir",
        str(tmp_path / "artifacts"),
        "--document-artifact-raw-cache-dir",
        str(tmp_path / "artifacts" / "raw_cache"),
        "--document-artifact-debug-quarantine-dir",
        str(tmp_path / "artifacts" / "debug_quarantine"),
        "--document-artifact-extraction-artifacts-dir",
        str(tmp_path / "financial_reports"),
        "--document-artifact-backups-dir",
        str(tmp_path / "backups"),
        "--document-artifact-logs-dir",
        str(tmp_path / "logs"),
    ]


def _run_financial_document_fetch_plan_preview(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "financial-document-fetch-plan-preview",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _write_financial_document_fetch_retention(
    tmp_path: Path,
    *,
    disk_guard_status: str = "passed",
) -> Path:
    path = tmp_path / "retention.json"
    guard_reasons = [] if disk_guard_status == "passed" else ["raw_cache_ttl_expired_files_present"]
    path.write_text(
        json.dumps(
            {
                "status": "passed" if disk_guard_status == "passed" else "warning",
                "mode": "financial-document-artifact-retention-preview",
                "disk_guard_status": disk_guard_status,
                "download_allowed": disk_guard_status != "blocked",
                "future_extraction_allowed": disk_guard_status != "blocked",
                "guard_reason_codes": guard_reasons,
                "policy_rows": [
                    {
                        "artifact_class": "raw_report_cache",
                        "path_role": "raw_cache",
                        "default_path": str(tmp_path / "artifacts" / "raw_cache"),
                    },
                    {
                        "artifact_class": "debug_quarantine",
                        "path_role": "debug_quarantine",
                        "default_path": str(tmp_path / "artifacts" / "debug_quarantine"),
                    },
                    {
                        "artifact_class": "hash_manifest",
                        "path_role": "hash_manifest",
                        "default_path": str(tmp_path / "artifacts" / "hash_manifest"),
                    },
                    {
                        "artifact_class": "extraction_artifacts",
                        "path_role": "extraction_artifacts",
                        "default_path": str(tmp_path / "financial_reports"),
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _financial_document_fetch_summary_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "draft_row_status": "draft_ready_for_future_extraction_preview",
        "draft_document_url": "https://mostotrest.ru/reports/annual-ifrs-2025.pdf",
        "draft_document_title": "Mostotrest annual IFRS 2025",
        "draft_fallback_status": "not_fallback",
        "validation_status": "passed",
        "document_kind": "exact_report_document",
        "document_period_year": "2025",
        "document_period_status": "target_period",
        "report_type_match_status": "annual_match",
        "accounting_standard_match_status": "standard_match",
        "gate_status": "passed",
        "gate_passed": True,
        "ready_for_value_extraction": True,
        "ready_for_import": False,
        "has_exact_target_document": True,
        "blocked_reason_codes": [],
    }
    row.update(updates)
    return row


def _write_financial_document_fetch_summary(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "mode": "document-intake-draft-gate-preview",
                "draft_gate_summary_rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _financial_document_fetch_board_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "overall_status": "needs_operator_exact_document_url",
        "draft_gate_status": "draft_placeholder_not_ready",
        "ready_for_value_extraction": False,
        "trusted_source_hosts": ["mostotrest.ru"],
        "operator_fill_exact_document_url": "",
        "latest_historical_document_url": "",
    }
    row.update(updates)
    return row


def _write_financial_document_fetch_board(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-resolution-chain-review-board",
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_operator_exact_document_refill_workspace(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "operator-exact-document-refill-workspace-v2",
            *(extra_args or []),
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _operator_exact_document_refill_fetch_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "fetch_plan_id": "financial_document_fetch_plan:67:2025:missing_exact_document_url",
        "resolution_id": "",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "fetch_plan_status": "blocked_missing_exact_document_url",
        "fetch_plan_reason_codes": ["missing_exact_document_url"],
        "fetch_plan_warnings": [],
        "disk_guard_status": "passed",
        "planned_raw_document_path": "logs/financial_reports/document_artifacts/raw_cache/67/2025/missing_exact_document_url.downloaded.pdf",
        "planned_hash_manifest_path": "logs/financial_reports/document_artifacts/hash_manifest/67/2025/missing_exact_document_url.json",
        "historical_fallback_url": "",
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    if "fetch_plan_id" not in updates:
        row["fetch_plan_id"] = (
            f"financial_document_fetch_plan:{row.get('canonical_company_id') or row.get('company_id')}:"
            "2025:missing_exact_document_url"
        )
    return row


def _write_operator_exact_document_refill_fetch_plan(
    path: Path,
    rows: list[dict[str, object]],
    *,
    disk_guard_status: str = "passed",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "financial-document-fetch-plan-preview",
                "disk_guard_status": disk_guard_status,
                "fetch_plan_rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _operator_exact_document_refill_source_trust_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "source_trust_status": "ready_for_document_url_refill",
        "trusted_source_hosts": ["mostotrest.ru"],
        "current_known_source_page_url": "https://mostotrest.ru/reports/",
        "current_known_document_url": "",
        "latest_historical_document_url": "",
        "next_required_action": "fill_exact_official_target_period_annual_ifrs_url",
        "trusted_source_status_reason_codes": ["baseline_trusted_source_available"],
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    return row


def _write_operator_exact_document_refill_rows(path: Path, mode: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": "warning", "mode": mode, "rows": rows}, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_operator_exact_document_refill_validation(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "operator-exact-document-refill-validate-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _operator_exact_document_refill_validation_template_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "workspace_id": "operator_exact_document_refill:67",
        "company_id": "67",
        "company_name": "Mostotrest",
        "workspace_status": "ready_for_exact_document_url_refill",
        "workspace_action": "fill_exact_target_annual_ifrs_document_url",
        "operator_instruction": "Fill the exact document URL.",
        "READONLY_target_reporting_period": "2025",
        "READONLY_required_report_type": "annual",
        "READONLY_required_standard": "IFRS",
        "READONLY_required_consolidated": "true",
        "READONLY_trusted_source_hosts": "mostotrest.ru",
        "READONLY_current_known_source_page_url": "https://mostotrest.ru/reports/",
        "READONLY_latest_historical_document_url": "",
        "READONLY_forbidden_url_hint": "Do not copy historical fallback URLs.",
        "operator_fill_exact_document_url": "https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf",
        "operator_fill_document_title": "Mostotrest annual consolidated IFRS financial statements 2025",
        "operator_fill_document_publication_date": "2026-04-30",
        "operator_fill_document_report_type": "annual",
        "operator_fill_document_accounting_standard": "IFRS",
        "operator_fill_document_consolidated": "true",
        "operator_fill_document_language": "en",
        "operator_fill_notes": "",
    }
    row.update(updates)
    return row


def _operator_exact_document_refill_validation_workspace_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "workspace_id": "operator_exact_document_refill:67",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "workspace_status": "ready_for_exact_document_url_refill",
        "workspace_action": "fill_exact_target_annual_ifrs_document_url",
        "source_trust_status": "ready_for_document_url_refill",
        "trusted_source_hosts": ["mostotrest.ru"],
        "current_known_source_page_url": "https://mostotrest.ru/reports/",
        "latest_historical_document_url": "",
        "historical_fallback_allowed_as_target_evidence": False,
        "historical_fallback_allowed_as_trusted_source": False,
        "fetch_plan_status": "blocked_missing_exact_document_url",
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    return row


def _write_operator_exact_document_refill_template(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=assistant.OPERATOR_EXACT_DOCUMENT_REFILL_TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_operator_exact_document_refill_validation_inputs(
    path: Path,
    *,
    workspace_updates: dict[str, object] | None = None,
    **template_updates: object,
) -> tuple[Path, Path]:
    template = path / "template.csv"
    workspace = path / "workspace.json"
    _write_operator_exact_document_refill_template(
        template,
        [_operator_exact_document_refill_validation_template_row(**template_updates)],
    )
    _write_operator_exact_document_refill_rows(
        workspace,
        "operator-exact-document-refill-workspace-v2",
        [_operator_exact_document_refill_validation_workspace_row(**(workspace_updates or {}))],
    )
    return template, workspace


def _run_operator_exact_document_refill_apply_draft(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "operator-exact-document-refill-apply-draft-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _operator_exact_document_refill_apply_validation_row(**updates: object) -> dict[str, object]:
    url = "https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf"
    row: dict[str, object] = {
        "validation_id": "operator_exact_document_refill_validation:operator_exact_document_refill:67",
        "accepted_candidate_id": "operator_exact_document_refill_candidate:operator_exact_document_refill:67",
        "workspace_id": "operator_exact_document_refill:67",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "normalized_document_url": url,
        "validation_status": "valid_future_exact_document_candidate",
        "accepted_for_future_apply_draft": True,
        "trusted_source_hosts": ["mostotrest.ru"],
        "would_accept_url": False,
        "would_update_exact_document_intake": False,
        "would_fetch_document": False,
        "would_download_document": False,
        "would_parse_document": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_database": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
        "would_delete_files": False,
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    return row


def _operator_exact_document_refill_apply_candidate_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "accepted_candidate_id": "operator_exact_document_refill_candidate:operator_exact_document_refill:67",
        "validation_id": "operator_exact_document_refill_validation:operator_exact_document_refill:67",
        "workspace_id": "operator_exact_document_refill:67",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "exact_document_url": "https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf",
        "document_title": "Mostotrest annual consolidated IFRS financial statements 2025",
        "document_publication_date": "2026-04-30",
        "document_report_type": "annual",
        "document_accounting_standard": "IFRS",
        "document_consolidated": "true",
        "document_language": "en",
        "trusted_source_hosts": ["mostotrest.ru"],
        "document_url_host": "mostotrest.ru",
        "document_url_registrable_domain": "mostotrest.ru",
        "accepted_candidate_status": "future_apply_draft_candidate_only",
        "future_apply_draft_allowed": True,
        "would_accept_url": False,
        "would_update_exact_document_intake": False,
        "would_fetch_document": False,
        "would_download_document": False,
        "would_parse_document": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_database": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    return row


def _write_operator_exact_document_refill_apply_validation(
    path: Path,
    rows: list[dict[str, object]],
    *,
    accepted_rows: list[dict[str, object]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-exact-document-refill-validate-v2",
                "validation_rows": rows,
                "accepted_candidate_rows": accepted_rows or [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_operator_exact_document_refill_apply_candidates(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-exact-document-refill-accepted-candidates-v2",
                "accepted_candidate_rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_exact_document_draft_gate(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-draft-gate-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_rzd_exact_document_refill(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "rzd-exact-document-url-refill-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_rzd_exact_document_refill_validation(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "rzd-exact-document-refill-validate-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_rzd_exact_document_refill_apply(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "rzd-exact-document-refill-apply-draft-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_rzd_exact_document_fetch_plan(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "rzd-exact-document-fetch-plan-preview-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-workspace-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery_validation(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-validate-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery_apply(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-apply-draft-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery_draft_review(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-draft-review-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery_promote_apply(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-promote-apply-draft-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _run_source_trust_recovery_controlled_apply(extra_args: list[str] | None = None) -> dict:
    args = assistant.parse_args(
        [
            "--mode",
            "source-trust-recovery-controlled-apply-v2",
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == (1 if report["status"] == "failed" else 0)
    return report


def _source_trust_recovery_gate_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "gate_id": "exact_document_draft_gate:67:2025",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "gate_status": "blocked_source_trust_required",
        "gate_reason_codes": ["source_trust_required"],
        "apply_status": "skipped_blocked_source_trust_required",
        "apply_blocker_codes": ["source_trust_required"],
        "trusted_source_hosts": [],
        "latest_historical_document_url": "https://example.com/historical-2024.pdf",
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    return row


def _source_trust_recovery_blocker_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "blocker_id": "exact_document_draft_gate_blocker:exact_document_draft_gate:67:2025:source_trust_required",
        "gate_id": "exact_document_draft_gate:67:2025",
        "company_id": "67",
        "company_name": "Mostotrest",
        "gate_status": "blocked_source_trust_required",
        "blocker_code": "source_trust_required",
        "blocker_severity": "warning",
    }
    row.update(updates)
    return row


def _write_source_trust_recovery_gate(
    path: Path,
    rows: list[dict[str, object]],
    blockers: list[dict[str, object]] | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "exact_document_draft_gate_task137.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "exact-document-draft-gate-v2",
                "gate_rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "exact_document_draft_gate_blockers_task137.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "exact-document-draft-gate-blockers-v2",
                "blocker_rows": blockers or [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _source_trust_recovery_validation_template_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "recovery_id": "source_trust_recovery:18:2025",
        "company_id": "18",
        "company_name": "RZD",
        "canonical_company_id": "18",
        "canonical_company_name": "RZD",
        "READONLY_target_reporting_period": "2025",
        "READONLY_required_report_type": "annual",
        "READONLY_required_standard": "IFRS",
        "READONLY_required_consolidated": "true",
        "READONLY_gate_status": "blocked_source_trust_required",
        "READONLY_gate_blocker_codes": "source_trust_required",
        "READONLY_current_known_source_page_url": "",
        "READONLY_current_known_document_url": "",
        "READONLY_latest_historical_document_url": "https://rzd.ru/reports/annual-ifrs-2024.pdf",
        "READONLY_forbidden_url_hint": "Do not copy historical fallback URLs.",
        "READONLY_operator_instruction": "Fill an official source page.",
        "operator_fill_official_source_page_url": "",
        "operator_fill_source_page_title": "",
        "operator_fill_source_page_language": "",
        "operator_fill_source_page_notes": "",
    }
    row.update(updates)
    return row


def _source_trust_recovery_validation_workspace_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "recovery_id": "source_trust_recovery:18:2025",
        "company_id": "18",
        "company_name": "RZD",
        "canonical_company_id": "18",
        "canonical_company_name": "RZD",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "recovery_status": "needs_official_source_page_refill",
        "gate_status": "blocked_source_trust_required",
        "gate_blocker_codes": ["source_trust_required"],
        "current_known_source_page_url": "",
        "current_known_document_url": "",
        "latest_historical_document_url": "https://rzd.ru/reports/annual-ifrs-2024.pdf",
    }
    row.update(updates)
    return row


def _write_source_trust_recovery_validation_inputs(
    path: Path,
    *,
    template_updates: dict[str, object] | None = None,
    workspace_updates: dict[str, object] | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    template_row = _source_trust_recovery_validation_template_row(**(template_updates or {}))
    with (path / "source_trust_recovery_template_task142.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=assistant.SOURCE_TRUST_RECOVERY_TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerow(template_row)
    workspace_row = _source_trust_recovery_validation_workspace_row(**(workspace_updates or {}))
    (path / "source_trust_recovery_workspace_task142.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "source-trust-recovery-workspace-v2",
                "recovery_rows": [workspace_row],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "source_trust_recovery_blockers_task142.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "source-trust-recovery-blockers-v2",
                "blocker_rows": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _source_trust_recovery_apply_source_pack_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "resolution_id": "source_trust_recovery:18:2025",
        "company_id": "18",
        "company_name": "RZD",
        "canonical_company_id": "18",
        "canonical_company_name": "RZD",
        "current_known_source_page_url": "",
        "current_known_document_url": "",
        "trusted_source_hosts": [],
        "trusted_hosts": [],
    }
    row.update(updates)
    return row


def _write_source_trust_recovery_apply_source_pack(path: Path, rows: list[dict[str, object]]) -> Path:
    output = path / "operator_resolution_pack_task118.json"
    output.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-resolution-pack",
                "resolutions": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def _write_source_trust_recovery_apply_accepted_inputs_without_source_pack(path: Path) -> dict:
    _write_source_trust_recovery_validation_inputs(
        path,
        template_updates={
            "operator_fill_official_source_page_url": "https://company.rzd.ru/ru/9471",
            "operator_fill_source_page_title": "Отчетность РЖД",
            "operator_fill_source_page_language": "ru",
            "operator_fill_source_page_notes": (
                "Official RZD reporting hub/source page with links to IFRS/RAS reporting materials; "
                "source trust candidate only."
            ),
        },
    )
    return _run_source_trust_recovery_validation(["--operator-resolution-chain-output-dir", str(path)])


def _write_source_trust_recovery_apply_accepted_fixture(path: Path) -> dict:
    validation_report = _write_source_trust_recovery_apply_accepted_inputs_without_source_pack(path)
    _write_source_trust_recovery_apply_source_pack(path, [_source_trust_recovery_apply_source_pack_row()])
    return validation_report


def _assert_source_trust_recovery_apply_failure_artifacts(report: dict) -> None:
    artifacts = report["artifacts"]
    for key in (
        "apply_json",
        "apply_csv",
        "apply_markdown",
        "blockers_json",
        "blockers_csv",
        "source_pack_draft_summary_csv",
        "rerun_markdown",
    ):
        assert Path(artifacts[key]).is_file()
    assert not Path(artifacts["source_pack_draft_json"]).exists()
    persisted = json.loads(Path(artifacts["apply_json"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["errors"] == report["errors"]


def _prepare_source_trust_recovery_draft_review_fixture(path: Path) -> dict:
    _write_source_trust_recovery_apply_accepted_fixture(path)
    return _run_source_trust_recovery_apply(["--operator-resolution-chain-output-dir", str(path)])


def _prepare_source_trust_recovery_promote_apply_fixture(path: Path) -> dict:
    _prepare_source_trust_recovery_draft_review_fixture(path)
    return _run_source_trust_recovery_draft_review(["--operator-resolution-chain-output-dir", str(path)])


def _prepare_source_trust_recovery_controlled_apply_fixture(path: Path) -> dict:
    _prepare_source_trust_recovery_promote_apply_fixture(path)
    return _run_source_trust_recovery_promote_apply(["--operator-resolution-chain-output-dir", str(path)])


def _source_trust_recovery_controlled_apply_hash_args(
    path: Path,
    *,
    token: str = "APPLY_RZD_SOURCE_TRUST_TASK148",
    promoted_sha: str | None = None,
    promote_apply_sha: str | None = None,
) -> list[str]:
    promoted = path / "source_trust_recovery_promoted_source_pack_draft_task147.json"
    promote_apply = path / "source_trust_recovery_promote_apply_draft_task147.json"
    return [
        "--operator-resolution-chain-output-dir",
        str(path),
        "--source-trust-recovery-controlled-apply-token",
        token,
        "--source-trust-recovery-controlled-apply-expected-promoted-draft-sha256",
        promoted_sha or hashlib.sha256(promoted.read_bytes()).hexdigest(),
        "--source-trust-recovery-controlled-apply-expected-promote-apply-sha256",
        promote_apply_sha or hashlib.sha256(promote_apply.read_bytes()).hexdigest(),
    ]


def _mutate_json_file(path: Path, mutator) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mutate_source_trust_recovery_apply_report(path: Path, mutator) -> None:
    _mutate_json_file(path / "source_trust_recovery_apply_draft_task145.json", mutator)


def _mutate_source_trust_recovery_accepted_candidates(path: Path, mutator) -> None:
    _mutate_json_file(path / "source_trust_recovery_accepted_candidates_task143.json", mutator)


def _mutate_source_trust_recovery_draft_row(path: Path, mutator) -> None:
    def mutate(payload: dict) -> None:
        rows = payload.get("resolutions") or payload.get("rows") or payload
        mutator(rows[0])

    _mutate_json_file(path / "source_trust_recovery_source_pack_draft_task145.json", mutate)


def _mutate_source_trust_recovery_source_pack_row(path: Path, mutator) -> None:
    def mutate(payload: dict) -> None:
        rows = payload.get("resolutions") or payload.get("rows") or payload
        mutator(rows[0])

    _mutate_json_file(path / "operator_resolution_pack_task118.json", mutate)


def _mutate_source_trust_recovery_promoted_draft_row(path: Path, mutator) -> None:
    def mutate(payload: dict) -> None:
        rows = payload.get("resolutions") or payload.get("rows") or payload
        mutator(rows[0])

    _mutate_json_file(path / "source_trust_recovery_promoted_source_pack_draft_task147.json", mutate)


def _exact_document_draft_gate_placeholder_document(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "document_url": "",
        "document_title": "",
        "document_date": "",
        "document_status": "not_found",
        "operator_review_status": "operator_to_fill",
        "filter_status": "placeholder_not_found",
        "fallback_status": "not_fallback",
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    return row


def _exact_document_draft_gate_applied_document(**updates: object) -> dict[str, object]:
    row = {
        **_exact_document_draft_gate_placeholder_document(),
        "document_url": "https://mostotrest.ru/reports/annual-ifrs-consolidated-2025.pdf",
        "document_title": "Mostotrest annual consolidated IFRS financial statements 2025",
        "document_date": "2026-04-30",
        "document_consolidated": "true",
        "document_language": "en",
        "document_context_status": "exact_document_url_apply_draft_for_future_gate",
        "document_context_origin": "operator_exact_document_refill_apply_draft_task136",
        "manual_candidate_status": "future_gate_validation_required",
        "document_status": "draft_candidate_pending_future_gate_validation",
        "operator_review_status": "pending_future_gate_validation",
        "filter_status": "draft_pending_future_gate_validation",
        "fallback_status": "not_fallback",
        "ready_for_document_download": False,
        "ready_for_value_extraction": False,
        "ready_for_import": False,
        "ready_for_scoring": False,
        "ready_for_paper_trading": False,
    }
    row.update(updates)
    return row


def _exact_document_draft_gate_rzd_applied_document(**updates: object) -> dict[str, object]:
    row = _exact_document_draft_gate_applied_document(
        company_id="18",
        company_name="RZD",
        canonical_company_id="18",
        canonical_company_name="RZD",
        document_url="https://company.rzd.ru/reports/annual-ifrs-consolidated-2025.pdf",
        document_title="RZD annual consolidated IFRS financial statements 2025",
    )
    row.update(updates)
    return row


def _exact_document_draft_gate_task152_document(**updates: object) -> dict[str, object]:
    url = "https://company.rzd.ru/ru/9397/page/104069?id=322745"
    row: dict[str, object] = {
        "company_id": "18",
        "company_name": "RZD",
        "canonical_company_id": "18",
        "canonical_company_name": "RZD",
        "target_reporting_period": "2025",
        "document_report_type": "annual",
        "document_accounting_standard": "IFRS",
        "document_consolidated": True,
        "document_language": "ru",
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "official_document_page_url": url,
        "candidate_exact_document_url": url,
        "document_url": url,
        "exact_document_url": url,
        "document_title": "RZD annual consolidated IFRS reporting page 2025",
        "document_context_status": "exact_document_url_apply_draft_for_future_gate",
        "document_context_origin": "rzd_exact_document_refill_apply_draft_task152",
        "manual_candidate_status": "future_gate_validation_required",
        "document_status": "draft_candidate_pending_future_gate_validation",
        "ready_for_document_download": False,
        "ready_for_value_extraction": False,
        "ready_for_extraction": False,
        "ready_for_import": False,
        "ready_for_scoring": False,
        "ready_for_paper_trading": False,
        "download_allowed": False,
        "parse_allowed": False,
        "import_allowed": False,
    }
    row.update(updates)
    return row


def _exact_document_draft_gate_apply_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "apply_id": "operator_exact_document_refill_apply:mostotrest",
        "company_id": "67",
        "company_name": "Mostotrest",
        "canonical_company_id": "67",
        "canonical_company_name": "Mostotrest",
        "target_reporting_period": "2025",
        "required_report_type": "annual",
        "required_standard": "IFRS",
        "required_consolidated": True,
        "apply_status": "applied_to_exact_document_intake_draft",
        "trusted_source_hosts": ["mostotrest.ru"],
        "apply_warnings": [],
    }
    row.update(updates)
    if "company_id" in updates and "canonical_company_id" not in updates:
        row["canonical_company_id"] = updates["company_id"]
    if "company_name" in updates and "canonical_company_name" not in updates:
        row["canonical_company_name"] = updates["company_name"]
    return row


def _exact_document_draft_gate_rzd_source_trust_apply_row(**updates: object) -> dict[str, object]:
    row = _exact_document_draft_gate_apply_row(
        apply_id="operator_exact_document_refill_apply:rzd",
        company_id="18",
        company_name="RZD",
        canonical_company_id="18",
        canonical_company_name="RZD",
        apply_status="skipped_blocked_source_trust_required",
        trusted_source_hosts=[],
    )
    row.update(updates)
    return row


def _exact_document_draft_gate_apply_blocker_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "apply_id": "operator_exact_document_refill_apply:mostotrest",
        "company_id": "67",
        "company_name": "Mostotrest",
        "blocker_code": "missing_exact_document_url",
    }
    row.update(updates)
    return row


def _exact_document_draft_gate_rzd_source_trust_blocker_row(**updates: object) -> dict[str, object]:
    row = _exact_document_draft_gate_apply_blocker_row(
        apply_id="operator_exact_document_refill_apply:rzd",
        company_id="18",
        company_name="RZD",
        blocker_code="source_trust_required",
    )
    row.update(updates)
    return row


def _write_exact_document_draft_gate_source_pack(
    path: Path,
    *,
    filename: str = "source_trust_recovery_controlled_source_pack_task148.json",
    company_id: str = "18",
    company_name: str = "RZD",
    trusted_host: str = "company.rzd.ru",
    source_page_url: str = "https://company.rzd.ru/ru/9471",
    source_trust_status: str = "controlled_applied_source_trust",
) -> Path:
    source_pack = path / filename
    source_pack.write_text(
        json.dumps(
            {
                "mode": "operator-resolution-pack",
                "resolutions": [
                    {
                        "resolution_id": f"resolution:{company_id}",
                        "company_id": company_id,
                        "company_name": company_name,
                        "canonical_company_id": company_id,
                        "canonical_company_name": company_name,
                        "current_known_source_page_url": source_page_url,
                        "trusted_source_hosts": [trusted_host],
                        "trusted_hosts": [trusted_host],
                        "source_trust_status": source_trust_status,
                        "candidate_source_status": "controlled_applied_in_task148",
                        "trusted": True,
                        "trusted_host": True,
                        "ready_for_document_download": False,
                        "ready_for_extraction": False,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return source_pack


def _rzd_exact_document_refill_gate_row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "gate_id": "exact_document_draft_gate:18:2025",
        "workspace_id": "operator_exact_document_refill:rzd",
        "company_id": "18",
        "company_name": "RZD",
        "canonical_company_id": "18",
        "canonical_company_name": "RZD",
        "gate_status": "blocked_missing_exact_document_url",
        "gate_reason_codes": [
            "missing_exact_document_url",
            "controlled_source_pack_trust_context_used",
            "source_trust_recovery_task148",
            "stale_source_trust_apply_blocker_suppressed",
        ],
        "apply_blocker_codes": [],
        "suppressed_apply_blocker_codes": ["source_trust_required"],
        "trusted_source_context_found": True,
        "trusted_source_context_status": "controlled_applied_source_trust",
        "trusted_source_hosts": ["company.rzd.ru"],
        "trusted_hosts": ["company.rzd.ru"],
        "trusted_source_page_url": "https://company.rzd.ru/ru/9471",
        "latest_historical_document_url": "",
    }
    row.update(updates)
    return row


def _write_rzd_exact_document_refill_gate(path: Path, rows: list[dict[str, object]]) -> Path:
    gate = path / "exact_document_draft_gate_task137.json"
    gate.write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "exact-document-draft-gate-v2",
                "gate_rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return gate


def _write_rzd_exact_document_refill_validation_inputs(path: Path) -> dict:
    gate = _write_rzd_exact_document_refill_gate(path, [_rzd_exact_document_refill_gate_row()])
    source_pack = _write_exact_document_draft_gate_source_pack(path)
    report = _run_rzd_exact_document_refill(["--operator-resolution-chain-output-dir", str(path)])
    assert report["status"] == "passed"
    return {"gate": gate, "source_pack": source_pack, "refill": path / "rzd_exact_document_refill_task150.json"}


def _update_rzd_exact_document_refill_report(path: Path, **updates: object) -> dict:
    refill_path = path / "rzd_exact_document_refill_task150.json"
    payload = json.loads(refill_path.read_text(encoding="utf-8"))
    row = payload["refill_rows"][0]
    row.update(updates)
    if "refill_status" in updates:
        payload["accepted_candidate_count"] = 1 if updates["refill_status"] == "accepted_future_exact_document_candidate" else 0
    refill_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_rzd_exact_document_refill_apply_inputs(path: Path) -> dict[str, Path]:
    inputs = _write_rzd_exact_document_refill_validation_inputs(path)
    validation_report = _run_rzd_exact_document_refill_validation(
        ["--operator-resolution-chain-output-dir", str(path)]
    )
    assert validation_report["status"] == "passed"
    return {
        **inputs,
        "validation": path / "rzd_exact_document_refill_validation_task151.json",
        "accepted_candidates": path / "rzd_exact_document_refill_accepted_candidates_task151.json",
    }


def _update_json_first_row(path: Path, key: str, **updates: object) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key][0].update(updates)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_exact_document_draft_gate_inputs(
    path: Path,
    *,
    documents: list[dict[str, object]],
    apply_rows: list[dict[str, object]],
    blocker_rows: list[dict[str, object]],
    retention_status: str = "passed",
    write_retention: bool = True,
) -> None:
    _write_document_intake(path / "operator_exact_document_intake_apply_draft_task136.json", documents)
    (path / "operator_exact_document_refill_apply_task136.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-exact-document-refill-apply-draft-v2",
                "apply_rows": apply_rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "operator_exact_document_refill_apply_blockers_task136.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "operator-exact-document-refill-apply-blockers-v2",
                "blocker_rows": blocker_rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if write_retention:
        (path / "document_artifact_retention_policy_task132.json").write_text(
            json.dumps(
                {
                    "status": "passed" if retention_status == "passed" else "warning",
                    "mode": "financial-document-artifact-retention-preview",
                    "disk_guard_status": retention_status,
                    "download_allowed": retention_status != "blocked",
                    "future_extraction_allowed": retention_status != "blocked",
                    "filesystem_free_gb": 30,
                    "filesystem_free_percent": 60,
                    "vds_disk_limit_gb": 50,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    (path / "financial_document_fetch_plan_task133.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "mode": "financial-document-fetch-plan-preview",
                "fetch_plan_rows": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_exact_document_draft_gate_task152_inputs(
    path: Path,
    *,
    document: dict[str, object] | None = None,
    container_key: str | None = "documents",
) -> Path:
    _write_exact_document_draft_gate_inputs(
        path,
        documents=[_exact_document_draft_gate_applied_document()],
        apply_rows=[],
        blocker_rows=[],
    )
    task152_draft = path / "rzd_exact_document_intake_draft_task152.json"
    rows = [document or _exact_document_draft_gate_task152_document()]
    payload: object = rows if container_key is None else {container_key: rows}
    task152_draft.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_exact_document_draft_gate_source_pack(path)
    return task152_draft


def _write_rzd_exact_document_fetch_plan_inputs(
    path: Path,
    *,
    include_non_rzd_blocker: bool = False,
) -> dict[str, Path]:
    task152_draft = _write_exact_document_draft_gate_task152_inputs(path)
    if include_non_rzd_blocker:
        payload = json.loads(task152_draft.read_text(encoding="utf-8"))
        payload["documents"].append(_exact_document_draft_gate_placeholder_document())
        task152_draft.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_report = _run_exact_document_draft_gate(["--operator-resolution-chain-output-dir", str(path)])
    assert gate_report["ready_for_future_fetch_plan_count"] == 1
    return {
        "gate": path / "exact_document_draft_gate_task137.json",
        "intake_draft": task152_draft,
        "source_pack": path / "source_trust_recovery_controlled_source_pack_task148.json",
    }


def _run_availability_discovery(
    tmp_path: Path,
    monkeypatch,
    *,
    seed_html: str,
    current_date: str = "2026-07-15",
    extra_args: list[str] | None = None,
) -> dict:
    seed_pack = tmp_path / "official_seed_pack.json"
    intake = tmp_path / "exact_document_intake.json"
    _write_reviewed_seed_pack(seed_pack, include_rzd=False)
    _write_document_intake(
        intake,
        [_empty_document_intake_item(67, "Mostotrest", "https://mostotrest.ru/ru/invest/financial-results/")],
    )
    _mock_candidate_fetch(
        monkeypatch,
        {"https://mostotrest.ru/ru/invest/financial-results/": seed_html},
    )
    args = assistant.parse_args(
        [
            "--mode",
            "exact-document-discover-from-seeds",
            "--seed-input",
            str(seed_pack),
            "--document-intake-input",
            str(intake),
            "--required-company-ids",
            "67",
            "--exact-document-include-filtered",
            "true",
            "--exact-document-include-wrong-period",
            "true",
            "--exact-document-include-wrong-report-type",
            "true",
            "--exact-document-availability-current-date",
            current_date,
            *(extra_args or []),
        ]
    )
    report, exit_code = assistant.run_assistant(args)
    assert exit_code == 0
    return report


def _write_document_report(path: Path, issuers: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "operator_reviewed",
                "mode": "document-resolve",
                "issuers": issuers,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _document_report_issuer(company_id: int, name: str, issuer_url: str) -> dict:
    return {
        "company_id": company_id,
        "company_name": name,
        "canonical_company_id": company_id,
        "canonical_company_name": name,
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "document_candidates": [
            {
                "source_type": "issuer_investor_relations",
                "source_url": issuer_url,
                "document_url": "",
                "document_title": "",
                "document_date": "",
                "report_period": "2025",
                "report_type": "annual",
                "accounting_standard": "IFRS",
                "source_file_name": "",
                "document_status": "needs_operator_review",
                "confidence": "medium",
                "resolution_method": "landing_page_requires_operator",
                "operator_action": "select_exact_official_report_document",
                "notes": "Exact annual/audited report document must be selected before candidate-fill.",
            },
            {
                "source_type": "official_disclosure",
                "source_url": "https://www.e-disclosure.ru/",
                "document_url": "",
                "document_title": "",
                "document_date": "",
                "report_period": "2025",
                "report_type": "annual",
                "accounting_standard": "IFRS",
                "source_file_name": "",
                "document_status": "needs_operator_review",
                "confidence": "medium",
                "resolution_method": "landing_page_requires_operator",
                "operator_action": "select_exact_official_report_document",
                "notes": "Exact annual/audited report document must be selected before candidate-fill.",
            },
            {
                "source_type": "issuer_annual_report_pdf",
                "source_url": "",
                "document_url": "",
                "document_title": "",
                "document_date": "",
                "report_period": "2025",
                "report_type": "annual",
                "accounting_standard": "IFRS",
                "source_file_name": "",
                "document_status": "operator_to_find",
                "confidence": "low",
                "resolution_method": "exact_pdf_not_invented",
                "operator_action": "select_exact_official_report_document",
                "notes": "Exact annual/audited report document must be selected before candidate-fill.",
            },
        ],
    }


def _document_candidate(document_url: str, title: str, status: str) -> dict:
    return {
        "source_type": "official_issuer_report",
        "source_url": document_url,
        "document_url": document_url,
        "document_title": title,
        "document_date": "2026-03-15",
        "report_period": "2025",
        "report_type": "annual",
        "accounting_standard": "IFRS",
        "source_file_name": Path(document_url).name or "report.pdf",
        "document_status": status,
        "confidence": "high",
        "resolution_method": "operator_reviewed_exact_document",
        "operator_action": "validate_exact_official_report_document",
        "notes": "Official issuer PDF",
    }


def _template_row(company_id: int, name: str, inn: str) -> dict[str, str]:
    row = {field: "" for field in pack.CSV_FIELDS}
    row.update(
        {
            "canonical_company_id": str(company_id),
            "company_id": str(company_id),
            "company_name": name,
            "canonical_company_name": name,
            "legal_name": f"PJSC {name}",
            "short_name": name,
            "display_name": name,
            "inn": inn,
            "ogrn": f"10277{company_id:07d}",
            "issuer_role": "legal_issuer",
            "identity_status": "matched",
            "identity_confidence": "0.9",
            "period_year": "2025",
            "period_quarter": "0",
            "period_start_date": "2025-01-01",
            "period_end_date": "2025-12-31",
            "report_type": "annual",
            "currency": "RUB",
            "accounting_standard": "IFRS",
            "consolidation_scope": "consolidated",
            "value_scale": "million",
            "source": "operator_collection",
            "review_status": "pending",
            "recommended_collection_type": "full_annual_ifrs_report",
        }
    )
    return row


def _source_issuer(
    company_id: int,
    name: str,
    source_type: str,
    url: str,
    title: str,
) -> dict:
    return {
        "company_id": company_id,
        "company_name": name,
        "canonical_company_id": company_id,
        "canonical_company_name": name,
        "period_year": "2025",
        "source_candidates": [
            {
                "source_type": source_type,
                "url": url,
                "document_title": title,
                "document_date": "2026-03-15",
                "report_period": "2025",
                "status": "operator_reviewed",
                "notes": "Official source candidate",
            }
        ],
    }


def _write_source_intake(path: Path, issuers: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "operator_reviewed",
                "issuer_sources": issuers,
                "read_only": True,
                "dry_run_only": True,
                "import_executed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_candidate_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _candidate_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in pack.CSV_FIELDS}
    row.update(
        {
            "canonical_company_id": "18",
            "company_id": "18",
            "company_name": "RZD",
            "canonical_company_name": "RZD",
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
            "source_file_name": "rzd_ifrs_2025.pdf",
            "review_status": "reviewed",
        }
    )
    row.update(overrides)
    return row


def _preview_http(calls: list[tuple[str, str]]):
    def fake(method: str, url: str, payload=None):
        calls.append((method, url))
        assert "/paper-trading" not in url
        if url.endswith("/api/financial-reports/ingest"):
            raise AssertionError("Task 96 preview must never call ingest")
        if url.endswith("/api/financial-reports/preview"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": []},
            )
        return import_script.HttpResult(ok=False, status_code=404, data={})

    return fake
