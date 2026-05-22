from __future__ import annotations

import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_report_canonical_pack as pack  # noqa: E402
import financial_report_official_source_fill as fill  # noqa: E402


def test_manual_values_updates_one_canonical_row_and_writes_evidence(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template.csv"
    manual = tmp_path / "manual.json"
    output = tmp_path / "canonical_first3_reports_task86_preview.csv"
    evidence_json = tmp_path / "evidence.json"
    evidence_md = tmp_path / "evidence.md"
    _write_template(template)
    _write_json(
        manual,
        _manual_values(
            values={"revenue": "1000", "ebitda": None},
            evidence={
                "revenue": {
                    "page": "12",
                    "table": "Income statement",
                    "note": "Copied from official report.",
                }
            },
        ),
    )
    args = fill.parse_args(
        [
            "--template-input",
            str(template),
            "--manual-values-json",
            str(manual),
            "--output",
            str(output),
            "--evidence-output",
            str(evidence_json),
            "--markdown-output",
            str(evidence_md),
        ]
    )

    report, exit_code = fill.run_flow(args)

    assert exit_code == 0
    assert report["import_called"] is False
    assert report["apply_called"] is False
    assert report["updated_canonical_company_id"] == 18
    assert output.is_file()
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["canonical_company_id"] == "18"
    assert rows[0]["period_year"] == "2024"
    assert rows[0]["source"] == "official_issuer_report"
    assert rows[0]["revenue"] == "1000"
    assert rows[0]["ebitda"] == ""
    assert rows[0]["duplicate_company_ids"] == "289; 290"
    assert rows[1]["canonical_company_id"] == "67"
    assert rows[1]["revenue"] == ""

    evidence = json.loads(evidence_json.read_text(encoding="utf-8"))
    assert evidence["evidence_rows"][0]["field"] == "revenue"
    assert evidence["evidence_rows"][0]["status"] == "found"
    assert any(row["field"] == "ebitda" and row["status"] == "manual_required" for row in evidence["evidence_rows"])
    assert "## Evidence" in evidence_md.read_text(encoding="utf-8")


def test_manual_values_validation_requires_core_metadata_and_evidence() -> None:
    missing_id = _manual_values(canonical_company_id=None)
    missing_year = _manual_values(period_year=None)
    missing_source = _manual_values(source_url=None, source_file_name=None)
    no_field_evidence = _manual_values(values={"revenue": "1000"}, evidence={})
    null_values = _manual_values(values={"revenue": None}, evidence={})

    assert any(
        item["message"] == "canonical_company_id is required"
        for item in fill.validate_manual_values(missing_id)["errors"]
    )
    assert any(
        item["message"] == "period_year is required"
        for item in fill.validate_manual_values(missing_year)["errors"]
    )
    assert any(
        item["message"] == "source_url or source_file_name is required"
        for item in fill.validate_manual_values(missing_source)["errors"]
    )
    assert any(
        "revenue has a value but no field evidence" in item["message"]
        for item in fill.validate_manual_values(no_field_evidence)["errors"]
    )
    assert fill.validate_manual_values(null_values)["errors"] == []


def test_official_source_checks_block_wikipedia_and_allow_local_file_with_note() -> None:
    blocked = fill.is_official_financial_source(
        "https://wikipedia.org/wiki/Synthetic",
        None,
        "Synthetic",
    )
    official = fill.is_official_financial_source(
        "https://rzd.ru/report.pdf",
        None,
        "Synthetic",
    )
    local = fill.is_official_financial_source(
        None,
        "official-report.pdf",
        "Synthetic",
        source_note="Copied from official issuer annual IFRS report.",
    )
    missing = fill.is_official_financial_source(None, None, "Synthetic")

    assert blocked["errors"]
    assert official["errors"] == []
    assert official["warnings"] == []
    assert local["errors"] == []
    assert local["warnings"] == []
    assert missing["warnings"]


def test_duplicate_ids_remain_context_and_normalization_uses_canonical_id(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template.csv"
    manual = tmp_path / "manual.json"
    output = tmp_path / "logs/financial_reports/canonical_preview.csv"
    _write_template(template)
    _write_json(
        manual,
        _manual_values(
            values={
                "revenue": "1000",
                "ebitda": "200",
                "total_debt": "400",
                "cash": "100",
                "equity": "500",
                "interest_expense": "50",
            },
            evidence={
                field: {"page": "1", "table": "Synthetic table", "note": "Synthetic."}
                for field in (
                    "revenue",
                    "ebitda",
                    "total_debt",
                    "cash",
                    "equity",
                    "interest_expense",
                )
            },
        ),
    )
    args = fill.parse_args(
        [
            "--template-input",
            str(template),
            "--manual-values-json",
            str(manual),
            "--output",
            str(output),
        ]
    )

    report, exit_code = fill.run_flow(args)
    rows = pack.load_collection_rows(output, "csv")
    validation = pack.validate_collection_rows(rows, apply_mode=False)
    normalized = pack.normalize_canonical_rows(validation["rows"])

    assert exit_code == 0
    assert report["errors"] == []
    assert rows[0]["duplicate_company_ids"] == "289; 290"
    assert normalized["normalized_rows"][0]["company_id"] == 18
    assert 289 not in [row["company_id"] for row in normalized["normalized_rows"]]


def test_dry_run_without_output_does_not_write_collection(tmp_path: Path) -> None:
    template = tmp_path / "template.csv"
    manual = tmp_path / "manual.json"
    evidence_json = tmp_path / "evidence.json"
    _write_template(template)
    _write_json(manual, _manual_values(values={"revenue": None}, evidence={}))

    args = fill.parse_args(
        [
            "--template-input",
            str(template),
            "--manual-values-json",
            str(manual),
            "--evidence-output",
            str(evidence_json),
        ]
    )
    report, exit_code = fill.run_flow(args)

    assert exit_code == 0
    assert report["dry_run"] is True
    assert report["rows_written"] == 0
    assert evidence_json.is_file()


def test_manual_values_examples_are_synthetic() -> None:
    template = json.loads(
        (ROOT / "docs/examples/financial_reports/canonical_financial_report_manual_values_template.json").read_text(
            encoding="utf-8"
        )
    )
    example = json.loads(
        (
            ROOT
            / "docs/examples/financial_reports/canonical_financial_report_manual_values_example_synthetic.json"
        ).read_text(encoding="utf-8")
    )

    assert template["values"]["revenue"] is None
    assert "Synthetic" in example["note"]
    assert "not real issuer financials" in example["note"]
    assert fill.validate_manual_values(example)["errors"] == []


def _write_template(path: Path) -> None:
    rows = [
        _template_row(
            canonical_company_id="18",
            canonical_company_name="Synthetic Canonical",
            duplicate_company_ids="289; 290",
        ),
        _template_row(
            canonical_company_id="67",
            canonical_company_name="Synthetic Bridge",
            duplicate_company_ids="68",
        ),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fill.CANONICAL_COLLECTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _template_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in fill.CANONICAL_COLLECTION_FIELDS}
    row.update(
        {
            "currency": "RUB",
            "accounting_standard": "IFRS",
            "consolidation_scope": "consolidated",
            "value_scale": "million",
            "source": "operator_collection",
            "report_type": "annual",
        }
    )
    row.update(overrides)
    return row


def _manual_values(**overrides) -> dict:
    payload = {
        "canonical_company_id": 18,
        "period_year": 2024,
        "period_quarter": 0,
        "period_start_date": "2024-01-01",
        "period_end_date": "2024-12-31",
        "published_at": "2025-03-01",
        "document_date": "2025-03-01",
        "currency": "RUB",
        "accounting_standard": "IFRS",
        "consolidation_scope": "consolidated",
        "value_scale": "million",
        "source": "official_issuer_report",
        "source_url": "https://rzd.ru/report.pdf",
        "source_file_name": "official-report.pdf",
        "source_page": "12",
        "source_table": "Synthetic table",
        "source_note": "Copied from official issuer report.",
        "report_type": "annual",
        "values": {field: None for field in fill.METRIC_FIELDS},
        "evidence": {},
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
