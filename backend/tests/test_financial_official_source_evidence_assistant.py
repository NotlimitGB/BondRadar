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
