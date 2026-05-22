from __future__ import annotations

import csv
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_canonical_pack as pack  # noqa: E402
import financial_report_import as import_script  # noqa: E402


def test_template_generation_uses_canonical_ids_and_no_paper_endpoints(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    args = pack.parse_args(
        [
            "--mode",
            "template",
            "--backend-url",
            "http://testserver",
            "--source",
            "mixed",
            "--company-ids",
            "18",
            "--use-duplicate-mapping",
            "--rollup-duplicates",
            "--include-duplicate-members",
            "--collection-template-output",
            str(tmp_path / "canonical_template.csv"),
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        assert "/api/paper-trading" not in url
        path = url.replace("http://testserver", "")
        if path == "/api/financial-reports/stats":
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "financial_reports_count": 0,
                    "financial_report_source_documents_count": 0,
                    "financial_report_import_runs_count": 0,
                },
            )
        if path.startswith("/api/ml/predictions?"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"predictions": []},
            )
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
                "legal_name": "Synthetic Canonical LLC",
                "short_name": "Synthetic Canonical",
                "ogrn": "1027700000001",
                "issuer_group_name": "Synthetic Group",
                "issuer_role": "legal_issuer",
            },
            "/api/companies/18/reports?limit=1": [],
            "/api/companies/289/reports?limit=1": [],
        }
        return import_script.HttpResult(ok=True, status_code=200, data=responses.get(path, []))

    report, exit_code = pack.run_pack(args, http_request=fake_http)
    markdown = pack.render_markdown(report)

    assert exit_code == 0
    assert report["import_executed"] is False
    assert report["template_generated"] is True
    assert report["financial_values_expected_empty"] is True
    assert report["financial_reports_count_before"] == 0
    assert report["financial_reports_count_after"] == 0
    assert report["created_reports_count"] == 0
    assert report["updated_reports_count"] == 0
    assert report["next_steps"] == [
        "Fill the collection template manually from official issuer reports.",
        "Run mode=preview before any confirmed import.",
        "Do not use Wikipedia or unofficial sources for financial values.",
    ]
    assert "## Template Mode Notice" in markdown
    assert "No financial report import was executed." in markdown
    assert "## Official Source Checklist" in markdown
    assert report["total_targets"] == 1
    assert report["safe_sources"] == ["top-predictions", "bond-universe"]
    assert not any("/api/paper-trading" in url for url in calls)

    rows = list(csv.DictReader((tmp_path / "canonical_template.csv").open(encoding="utf-8")))
    assert rows[0]["canonical_company_id"] == "18"
    assert rows[0]["canonical_company_name"] == "Synthetic Canonical"
    assert rows[0]["duplicate_company_ids"] == "289"
    assert "RU000CAN001" in rows[0]["sample_secids"]
    assert "RU000DUP001" in rows[0]["sample_secids"]
    assert rows[0]["currency"] == "RUB"
    assert rows[0]["accounting_standard"] == "IFRS"
    assert rows[0]["consolidation_scope"] == "consolidated"
    assert rows[0]["value_scale"] == "million"
    assert rows[0]["source"] == "operator_collection"
    assert rows[0]["report_type"] == "annual"
    assert rows[0]["revenue"] == ""
    assert rows[0]["ebitda"] == ""


def test_stats_unavailable_sets_null_counts_and_warning() -> None:
    args = pack.parse_args(
        [
            "--mode",
            "targets",
            "--backend-url",
            "http://testserver",
            "--source",
            "bond-universe",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        path = url.replace("http://testserver", "")
        if path == "/api/financial-reports/stats":
            return import_script.HttpResult(
                ok=False,
                status_code=404,
                data={"detail": "not found"},
                text="not found",
            )
        if path == "/api/bonds?skip=0&limit=200":
            return import_script.HttpResult(ok=True, status_code=200, data=[])
        if "/api/companies/identity/duplicates/diagnostics" in path:
            return import_script.HttpResult(ok=True, status_code=200, data={"groups": []})
        return import_script.HttpResult(ok=True, status_code=200, data={})

    report, exit_code = pack.run_pack(args, http_request=fake_http)

    assert exit_code == 0
    assert report["financial_reports_count_before"] is None
    assert report["financial_reports_count_after"] is None
    assert any(
        "financial report stats endpoint was unavailable" in item["message"]
        for item in report["warnings"]
    )


def test_duplicate_candidate_company_id_resolves_to_canonical_template_row(
    tmp_path: Path,
) -> None:
    output = tmp_path / "canonical_template.csv"
    args = pack.parse_args(
        [
            "--mode",
            "template",
            "--backend-url",
            "http://testserver",
            "--source",
            "bond-universe",
            "--company-ids",
            "289",
            "--use-duplicate-mapping",
            "--rollup-duplicates",
            "--include-duplicate-members",
            "--collection-template-output",
            str(output),
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        path = url.replace("http://testserver", "")
        if path == "/api/financial-reports/stats":
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "financial_reports_count": 0,
                    "financial_report_source_documents_count": 0,
                    "financial_report_import_runs_count": 0,
                },
            )
        if path == "/api/companies/identity/canonical-groups?active_only=true":
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
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
                "legal_name": "Synthetic Canonical LLC",
                "short_name": "Synthetic Canonical",
                "ogrn": "1027700000001",
                "issuer_group_name": "Synthetic Group",
                "issuer_role": "legal_issuer",
            },
            "/api/companies/18/reports?limit=1": [],
            "/api/companies/289/reports?limit=1": [],
        }
        return import_script.HttpResult(ok=True, status_code=200, data=responses.get(path, []))

    report, exit_code = pack.run_pack(args, http_request=fake_http)
    rows = list(csv.DictReader(output.open(encoding="utf-8")))

    assert exit_code == 0
    assert report["requested_company_ids"] == [289]
    assert report["selected_company_ids"] == [18]
    assert report["resolved_requested_company_ids"] == [
        {
            "requested_company_id": 289,
            "resolved_canonical_company_id": 18,
            "warning": (
                "Requested company is an accepted duplicate candidate; "
                "using canonical company instead."
            ),
        }
    ]
    assert any(
        "accepted duplicate candidate" in item["message"] for item in report["warnings"]
    )
    assert len(rows) == 1
    assert rows[0]["canonical_company_id"] == "18"
    assert rows[0]["duplicate_company_ids"] == "289"


def test_paper_positions_source_fails_safely_without_calling_paper_api() -> None:
    calls: list[str] = []
    args = pack.parse_args(
        [
            "--mode",
            "targets",
            "--backend-url",
            "http://testserver",
            "--source",
            "paper-positions",
        ]
    )

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        return import_script.HttpResult(ok=True, status_code=200, data={})

    report, exit_code = pack.run_pack(args, http_request=fake_http)

    assert exit_code == 1
    assert report["status"] == "failed"
    assert "paper-positions source is blocked" in report["errors"][0]["message"]
    assert calls == []


def test_validation_handles_required_fields_placeholders_and_sources() -> None:
    rows = [
        _canonical_row(canonical_company_id="", period_year="2025"),
        _canonical_row(period_year="", canonical_company_id="18"),
        _canonical_row(value_scale="mega"),
        _canonical_row(
            revenue="—",
            ebitda="N/A",
            net_debt="",
            total_debt="",
            cash="",
            equity="",
            interest_expense="",
            source_url="https://wikipedia.org/wiki/Synthetic",
        ),
    ]

    report = pack.validate_collection_rows(rows, apply_mode=False)
    errors = [item["message"] for item in report["errors"]]
    warnings = [item["message"] for item in report["warnings"]]

    assert "canonical_company_id is required and must be positive" in errors
    assert "period_year is required and must be an integer" in errors
    assert "value_scale must be raw, thousand, million, or billion" in errors
    assert any("placeholder value was treated as empty/null" in item for item in warnings)
    assert "all major financial values are empty" in warnings
    assert any("wikipedia.org" in item for item in warnings)


def test_apply_blocks_non_official_source_unless_allowed() -> None:
    rows = [_canonical_row(source_url="https://example.invalid/report.pdf")]

    blocked = pack.validate_collection_rows(
        rows,
        apply_mode=True,
        allow_non_official_source=False,
    )
    allowed = pack.validate_collection_rows(
        rows,
        apply_mode=True,
        allow_non_official_source=True,
    )

    assert any("non-official source is blocked" in item["message"] for item in blocked["errors"])
    assert allowed["errors"] == []
    assert any("non-official" in item["message"] for item in allowed["warnings"])


def test_normalization_maps_canonical_id_scales_values_and_keeps_one_row() -> None:
    validation = pack.validate_collection_rows(
        [
            _canonical_row(
                canonical_company_id="18",
                duplicate_company_ids="289; 290",
                revenue="1000",
                ebitda="200",
                total_debt="400",
                cash="",
                interest_expense="50",
                source_url="https://rzd.ru/report.pdf",
            )
        ],
        apply_mode=False,
    )

    report = pack.normalize_canonical_rows(validation["rows"])
    row = report["normalized_rows"][0]

    assert len(report["normalized_rows"]) == 1
    assert row["company_id"] == 18
    assert row["revenue"] == "1000000000"
    assert row["cash"] is None
    assert row["debt_to_ebitda"] == "2"
    assert row["interest_coverage"] == "4"


def test_preview_never_calls_ingest(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.csv"
    normalized = tmp_path / "normalized.csv"
    _write_canonical_csv(reviewed, [_canonical_row(source_url="https://rzd.ru/report.pdf")])
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        if url.endswith("/ingest"):
            raise AssertionError("preview mode must not call ingest")
        if url.endswith("/stats"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "financial_reports_count": 0,
                    "financial_report_source_documents_count": 0,
                    "financial_report_import_runs_count": 0,
                },
            )
        if url.endswith("/preview"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": []},
            )
        return import_script.HttpResult(
            ok=True,
            status_code=200,
            data={"status": "ready", "warnings": []},
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
            "--normalized-output",
            str(normalized),
            "--normalized-format",
            "csv",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=fake_http)

    assert exit_code == 0
    assert report["dry_run_import_report"]["status"] == "passed"
    assert report["financial_reports_count_before"] == 0
    assert report["financial_reports_count_after"] == 0
    assert report["created_reports_count"] == 0
    assert report["updated_reports_count"] == 0
    assert not any(url.endswith("/ingest") for url in calls)
    assert normalized.is_file()


def test_apply_requires_confirmation_and_stops_when_dry_run_fails(tmp_path: Path) -> None:
    reviewed = tmp_path / "reviewed.csv"
    _write_canonical_csv(reviewed, [_canonical_row(source_url="https://rzd.ru/report.pdf")])
    calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        calls.append(url)
        if url.endswith("/preview"):
            return import_script.HttpResult(
                ok=False,
                status_code=400,
                data={"detail": "preview failed"},
                text="preview failed",
            )
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "ready"})

    missing_confirm = pack.parse_args(
        [
            "--mode",
            "apply",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
            "--execute-import",
            "yes",
        ]
    )
    report, exit_code = pack.run_pack(missing_confirm, http_request=fake_http)
    assert exit_code == 1
    assert any("requires --confirm-import yes" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/ingest") for url in calls)

    calls.clear()
    confirmed = pack.parse_args(
        [
            "--mode",
            "apply",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
            "--execute-import",
            "yes",
            "--confirm-import",
            "yes",
        ]
    )
    report, exit_code = pack.run_pack(confirmed, http_request=fake_http)
    assert exit_code == 1
    assert any("dry-run import flow failed" in item["message"] for item in report["errors"])
    assert not any(url.endswith("/ingest") for url in calls)


def test_confirmed_apply_runs_dry_run_before_ingest_and_markdown_has_rollback(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed.csv"
    markdown = tmp_path / "apply.md"
    _write_canonical_csv(reviewed, [_canonical_row(source_url="https://rzd.ru/report.pdf")])
    import_calls: list[str] = []

    def fake_http(method: str, url: str, payload=None):
        if url.endswith("/preview") or url.endswith("/ingest"):
            import_calls.append(url)
        if url.endswith("/preview"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={"status": "passed", "errors": [], "warnings": []},
            )
        if url.endswith("/ingest"):
            return import_script.HttpResult(
                ok=True,
                status_code=200,
                data={
                    "run_id": 1,
                    "status": "completed",
                    "total_rows": 1,
                    "created": 1,
                    "updated": 0,
                    "skipped": 0,
                    "failed": 0,
                    "errors": [],
                    "warnings": [],
                },
            )
        return import_script.HttpResult(ok=True, status_code=200, data={"status": "ready"})

    args = pack.parse_args(
        [
            "--mode",
            "apply",
            "--backend-url",
            "http://testserver",
            "--reviewed-input",
            str(reviewed),
            "--format",
            "csv",
            "--execute-import",
            "yes",
            "--confirm-import",
            "yes",
        ]
    )

    report, exit_code = pack.run_pack(args, http_request=fake_http)
    pack.write_markdown_report(report, markdown)

    assert exit_code == 0
    assert import_calls == [
        "http://testserver/api/financial-reports/preview",
        "http://testserver/api/financial-reports/preview",
        "http://testserver/api/financial-reports/ingest",
    ]
    assert "## Rollback Note" in markdown.read_text(encoding="utf-8")


def test_canonical_examples_parse_and_synthetic_rows_validate_without_errors() -> None:
    template_csv = pack.load_collection_rows(
        ROOT / "docs/examples/financial_reports/canonical_financial_report_collection_template.csv",
        "csv",
    )
    template_json = pack.load_collection_rows(
        ROOT / "docs/examples/financial_reports/canonical_financial_report_collection_template.json",
        "json",
    )
    synthetic_csv = pack.load_collection_rows(
        ROOT / "docs/examples/financial_reports/canonical_financial_report_collection_example_synthetic.csv",
        "csv",
    )
    synthetic_json = pack.load_collection_rows(
        ROOT / "docs/examples/financial_reports/canonical_financial_report_collection_example_synthetic.json",
        "json",
    )

    assert len(template_csv) == 1
    assert len(template_json) == 1
    assert len(synthetic_csv) == 2
    assert len(synthetic_json) == 2
    assert pack.validate_collection_rows(synthetic_csv, apply_mode=False)["errors"] == []
    assert pack.validate_collection_rows(synthetic_json, apply_mode=False)["errors"] == []


def _canonical_row(**overrides: str) -> dict[str, str]:
    row = {
        "canonical_company_id": "18",
        "canonical_company_name": "Synthetic Canonical",
        "legal_name": "Synthetic Canonical LLC",
        "short_name": "Synthetic",
        "inn": "7700000001",
        "ogrn": "1027700000001",
        "issuer_group_name": "Synthetic Group",
        "issuer_role": "legal_issuer",
        "duplicate_company_ids": "",
        "sample_secids": "RU000SYN001",
        "sample_bond_names": "Synthetic BO",
        "period_year": "2025",
        "period_quarter": "0",
        "period_start_date": "2025-01-01",
        "period_end_date": "2025-12-31",
        "published_at": "2026-03-20T12:00:00Z",
        "document_date": "2026-03-20",
        "currency": "RUB",
        "accounting_standard": "IFRS",
        "consolidation_scope": "consolidated",
        "value_scale": "million",
        "source": "operator_collection",
        "source_url": "https://rzd.ru/report.pdf",
        "source_file_name": "synthetic-report.pdf",
        "source_page": "1",
        "source_table": "Synthetic table",
        "source_note": "Synthetic only",
        "report_type": "annual",
        "revenue": "1000",
        "ebitda": "200",
        "net_debt": "300",
        "total_debt": "400",
        "cash": "100",
        "equity": "500",
        "short_term_debt": "80",
        "operating_cash_flow": "150",
        "net_profit": "70",
        "interest_expense": "50",
        "debt_to_ebitda": "",
        "interest_coverage": "",
        "operator_notes": "Synthetic test row",
    }
    row.update(overrides)
    return row


def _write_canonical_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANONICAL_COLLECTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
