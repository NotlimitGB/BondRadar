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
    assert report["target_reporting_period_availability_count"] == 1
    assert report["availability_status_counts"]["exact_target_period_document_found"] == 1
    operator_row = report["availability_operator_rows"][0]
    assert operator_row["recommended_next_step"] == "proceed_to_quality_gate_or_extraction_preview"
    assert operator_row["gate_status"] == "quality_gate_not_run"
    assert operator_row["ready_for_value_extraction"] is False
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


def test_exact_document_availability_policy_historical_inside_grace_window(
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
    assert availability["availability_status"] == "target_period_likely_not_yet_published_by_policy_window"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["historical_fallback_allowed"] is True
    assert availability["historical_fallback_scope"] == "diagnostic_only"
    assert availability["latest_available_period"] == "2024"
    assert availability["reporting_window_policy"]["expected_availability_date"] == "2026-06-29"
    assert availability["reporting_window_policy"]["within_grace_window"] is True
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "wait_or_recheck_publication"
    assert action["queue_priority"] == "medium"
    assert action["queue_status"] == "waiting"
    assert action["manual_review_required"] is False
    assert action["is_blocking_next_stage"] is True
    assert action["blocked_stage"] == "value_extraction"
    assert action["can_unblock_extraction"] is False
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
    )

    availability = _availability_for(report)
    placeholder = next(item for item in report["documents"] if item.get("document_status") == "not_found")
    assert placeholder["filter_status"] == "placeholder_not_found"
    assert placeholder["availability_status"] == "placeholder_not_found"
    assert placeholder["can_use_as_target_period_evidence"] is False
    assert availability["availability_status"] == "placeholder_not_found"
    assert availability["can_use_as_target_period_evidence"] is False
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
    assert availability["availability_status"] == "no_usable_official_report_candidates"
    assert availability["can_use_as_target_period_evidence"] is False
    assert availability["historical_fallback_allowed"] is False
    assert availability["historical_fallback_scope"] == "none"
    action = _queue_action_for(report)
    assert action["queue_action_type"] == "improve_official_source_coverage"


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
        ]
    )

    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["target_reporting_period_availability_count"] == 2
    assert report["availability_policy_name"] == "annual_ifrs_grace_window"
    assert report["availability_current_date"] == "2026-05-25"
    assert report["annual_ifrs_grace_days"] == 180
    assert report["availability_status_counts"]["placeholder_not_found"] == 1
    assert report["availability_status_counts"]["target_period_likely_not_yet_published_by_policy_window"] == 1
    assert report["target_evidence_available_count"] == 0
    assert report["historical_fallback_diagnostic_only_count"] == 1
    assert report["extraction_ready_count"] == 0
    assert report["import_ready_count"] == 0
    assert report["operator_action_counts"]["operator_to_find_official_exact_document"] == 1
    assert report["operator_action_counts"]["wait_for_target_period_publication_or_review_official_sources"] == 1
    assert report["operator_review_queue_count"] == 2
    assert report["operator_review_queue_blocking_count"] == 2
    assert report["operator_review_queue_manual_action_count"] == 1
    assert report["operator_review_queue_wait_action_count"] == 1
    assert report["operator_review_queue_noop_count"] == 0
    assert report["operator_review_queue_priority_counts"]["high"] == 1
    assert report["operator_review_queue_priority_counts"]["medium"] == 1
    assert report["operator_review_queue_action_type_counts"]["fill_exact_document_url"] == 1
    assert report["operator_review_queue_action_type_counts"]["wait_or_recheck_publication"] == 1

    rows = {str(row["company_id"]): row for row in report["availability_operator_rows"]}
    assert set(rows) == {"18", "67"}
    assert rows["18"]["availability_status"] == "placeholder_not_found"
    assert rows["18"]["recommended_next_step"] == "fill_exact_official_document_url_or_improve_official_sources"
    assert rows["18"]["can_use_as_target_period_evidence"] is False
    assert rows["18"]["gate_status"] == "quality_gate_not_run"
    placeholder = next(item for item in report["documents"] if item.get("company_id") == 18)
    assert placeholder["filter_status"] == "placeholder_not_found"
    assert rows["67"]["availability_status"] == "target_period_likely_not_yet_published_by_policy_window"
    assert rows["67"]["historical_fallback_scope"] == "diagnostic_only"
    assert rows["67"]["recommended_next_step"] == "wait_for_target_period_publication_or_review_official_sources"
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
    assert actions["67"]["action_id"] == "financial_report:67:2025:annual:IFRS:wait_or_recheck_publication"
    assert actions["67"]["queue_action_type"] == "wait_or_recheck_publication"
    assert actions["67"]["queue_priority"] == "medium"
    assert actions["67"]["queue_status"] == "waiting"
    assert actions["67"]["manual_review_required"] is False
    assert actions["67"]["is_blocking_next_stage"] is True
    assert actions["67"]["blocked_stage"] == "value_extraction"
    assert actions["67"]["can_unblock_extraction"] is False
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
    }.issubset(set(csv_rows[0]))
    markdown = summary_md.read_text(encoding="utf-8")
    assert "Target Reporting Period Availability" in markdown
    assert "placeholder_not_found" in markdown
    assert "target_period_likely_not_yet_published_by_policy_window" in markdown
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
    }.issubset(set(queue_csv_rows[0]))
    queue_markdown = queue_md.read_text(encoding="utf-8")
    assert "Operator Review Action Queue" in queue_markdown
    assert "Queue Priority Counts" in queue_markdown
    assert "Queue Action Type Counts" in queue_markdown
    assert "fill_exact_document_url" in queue_markdown
    assert "wait_or_recheck_publication" in queue_markdown


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
