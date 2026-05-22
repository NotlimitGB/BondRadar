from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_canonical_pack import CANONICAL_COLLECTION_FIELDS
from financial_report_collection_normalize import MONEY_FIELDS, RATIO_FIELDS
from financial_report_import import write_json_report


METRIC_FIELDS = (*MONEY_FIELDS, *RATIO_FIELDS)
OFFICIAL_SOURCE_DOMAIN_HINTS = (
    "e-disclosure.ru",
    "disclosure.1prime.ru",
    "fedresurs.ru",
    "moex.com",
    "moex.ru",
    "rzd.ru",
    "eng.rzd.ru",
    "mostotrest.ru",
    "tmk-group.ru",
    "tmk-group.com",
)
BLOCKED_SOURCE_HINTS = (
    "wikipedia.org",
    "wikimedia.org",
    "wikiwand.com",
    "encyclopedia.com",
    "britannica.com",
)
PRIVATE_OUTPUT_HINTS = (
    "data/financial_reports/private/",
    "logs/financial_reports/",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill a canonical financial report collection template from "
            "operator-provided official source evidence. Preview-only helper."
        ),
    )
    parser.add_argument("--template-input", type=Path, required=True)
    parser.add_argument("--company-id", type=int, default=None)
    parser.add_argument("--company-name", default=None)
    parser.add_argument("--source-file", type=Path, default=None)
    parser.add_argument("--source-url", default=None)
    parser.add_argument(
        "--source-type",
        choices=("pdf", "xlsx", "csv", "html", "manual-json"),
        default=None,
    )
    parser.add_argument("--period-year", type=int, default=None)
    parser.add_argument("--period-quarter", type=int, default=None)
    parser.add_argument("--period-start-date", default=None)
    parser.add_argument("--period-end-date", default=None)
    parser.add_argument("--report-type", choices=("annual", "quarterly", "interim"), default=None)
    parser.add_argument(
        "--accounting-standard",
        choices=("IFRS", "RAS", "management", "unknown"),
        default=None,
    )
    parser.add_argument(
        "--consolidation-scope",
        choices=("consolidated", "standalone", "unknown"),
        default=None,
    )
    parser.add_argument("--currency", default="RUB")
    parser.add_argument(
        "--value-scale",
        choices=("raw", "thousand", "million", "billion"),
        default=None,
    )
    parser.add_argument("--manual-values-json", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evidence-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    parser.add_argument("--allow-missing-values", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def run_flow(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    effective_dry_run = bool(args.dry_run or args.output is None)
    template_rows: list[dict[str, Any]] = []
    output_rows: list[dict[str, Any]] = []
    updated_row: dict[str, Any] | None = None
    manual_values: dict[str, Any] | None = None

    try:
        template_rows = load_template_rows(args.template_input)
        output_rows = [dict(row) for row in template_rows]
    except Exception as exc:
        errors.append({"message": str(exc)})

    if not errors:
        try:
            manual_values = _manual_values_from_args(args)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if manual_values is not None and not errors:
        validation = validate_manual_values(
            manual_values,
            company_id=args.company_id,
            allow_missing_values=args.allow_missing_values,
        )
        warnings.extend(validation["warnings"])
        errors.extend(validation["errors"])
        if not errors:
            matches = _matching_template_indexes(
                output_rows,
                canonical_company_id=int(manual_values["canonical_company_id"]),
                company_name=args.company_name,
            )
            if len(matches) != 1:
                errors.append(
                    {
                        "message": (
                            "canonical_company_id must match exactly one row in template"
                        ),
                        "canonical_company_id": manual_values.get("canonical_company_id"),
                        "match_count": len(matches),
                    }
                )
            else:
                row_index = matches[0]
                updated_row = apply_manual_values(output_rows[row_index], manual_values, args)
                output_rows[row_index] = updated_row

    source_check = is_official_financial_source(
        _coalesce(
            None if manual_values is None else manual_values.get("source_url"),
            args.source_url,
        ),
        _coalesce(
            None if manual_values is None else manual_values.get("source_file_name"),
            None if args.source_file is None else args.source_file.name,
        ),
        _coalesce(
            None if updated_row is None else updated_row.get("canonical_company_name"),
            args.company_name,
        ),
        source_note=None if manual_values is None else manual_values.get("source_note"),
    )
    warnings.extend(source_check["warnings"])
    errors.extend(source_check["errors"])

    if args.output is not None:
        warnings.extend(_output_path_warnings(args.output))
    if args.source_file is not None and not args.source_file.is_file():
        warnings.append({"message": f"source file does not exist locally: {args.source_file}"})

    evidence_rows = build_evidence_rows(
        manual_values,
        updated_row,
        allow_missing_values=args.allow_missing_values,
    )
    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "template_input": str(args.template_input),
        "output": None if args.output is None else str(args.output),
        "dry_run": effective_dry_run,
        "source_type": args.source_type or ("manual-json" if args.manual_values_json else None),
        "manual_values_json": None
        if args.manual_values_json is None
        else str(args.manual_values_json),
        "rows_read": len(template_rows),
        "rows_written": 0,
        "updated_canonical_company_id": None
        if updated_row is None
        else _parse_int(updated_row.get("canonical_company_id")),
        "updated_company_name": None
        if updated_row is None
        else updated_row.get("canonical_company_name"),
        "import_called": False,
        "apply_called": False,
        "evidence_rows": evidence_rows,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status),
    }

    if status != "failed" and not effective_dry_run and args.output is not None:
        write_collection_rows(output_rows, args.output)
        report["rows_written"] = len(output_rows)
    if args.evidence_output is not None:
        write_json_report(report, args.evidence_output)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
    return report, 1 if status == "failed" else 0


def load_template_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"template input does not exist: {path}")
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("template JSON must be a list of rows or object with rows")
        return [_template_row(row) for row in rows]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [_template_row(row) for row in reader]


def write_collection_rows(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COLLECTION_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_value(row.get(field)) for field in CANONICAL_COLLECTION_FIELDS}
            )


def load_manual_values(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"manual values JSON does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("manual values JSON must be an object")
    return payload


def validate_manual_values(
    payload: dict[str, Any],
    *,
    company_id: int | None = None,
    allow_missing_values: bool = False,
    strict: bool = True,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    canonical_company_id = _parse_int(payload.get("canonical_company_id"))
    if canonical_company_id is None:
        errors.append({"message": "canonical_company_id is required"})
    elif company_id is not None and canonical_company_id != company_id:
        errors.append(
            {
                "message": "canonical_company_id does not match --company-id",
                "canonical_company_id": canonical_company_id,
                "company_id": company_id,
            }
        )

    if _parse_int(payload.get("period_year")) is None:
        errors.append({"message": "period_year is required"})

    source_url = payload.get("source_url")
    source_file_name = payload.get("source_file_name")
    if not source_url and not source_file_name:
        errors.append({"message": "source_url or source_file_name is required"})

    values = payload.get("values") or {}
    evidence = payload.get("evidence") or {}
    if not isinstance(values, dict):
        errors.append({"message": "values must be an object"})
        values = {}
    if not isinstance(evidence, dict):
        errors.append({"message": "evidence must be an object"})
        evidence = {}

    missing_fields: list[str] = []
    for field in METRIC_FIELDS:
        value = values.get(field)
        if value in (None, ""):
            missing_fields.append(field)
            continue
        if strict and not _field_has_evidence(evidence.get(field)):
            errors.append(
                {
                    "message": f"{field} has a value but no field evidence",
                    "field": field,
                }
            )

    if missing_fields and not allow_missing_values:
        warnings.append(
            {
                "message": "some financial values are missing and will remain empty",
                "fields": missing_fields,
            }
        )

    source_check = is_official_financial_source(
        source_url,
        source_file_name,
        payload.get("canonical_company_name"),
        source_note=payload.get("source_note"),
    )
    warnings.extend(source_check["warnings"])
    errors.extend(source_check["errors"])
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "warnings": warnings,
        "errors": errors,
    }


def apply_manual_values(
    template_row: dict[str, Any],
    manual_values: dict[str, Any],
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    row = dict(template_row)
    values = manual_values.get("values") or {}
    evidence = manual_values.get("evidence") or {}
    metadata_fields = (
        "period_year",
        "period_quarter",
        "period_start_date",
        "period_end_date",
        "published_at",
        "document_date",
        "currency",
        "accounting_standard",
        "consolidation_scope",
        "value_scale",
        "source",
        "source_url",
        "source_file_name",
        "source_page",
        "source_table",
        "source_note",
        "report_type",
    )
    cli_defaults = _cli_defaults(args)
    for field in metadata_fields:
        value = _coalesce(manual_values.get(field), cli_defaults.get(field))
        if value not in (None, ""):
            row[field] = value
    if not row.get("source"):
        row["source"] = "official_issuer_report"
    if not row.get("source_file_name") and args is not None and args.source_file is not None:
        row["source_file_name"] = args.source_file.name
    row["source_page"] = row.get("source_page") or _combined_evidence_value(evidence, "page")
    row["source_table"] = row.get("source_table") or _combined_evidence_value(evidence, "table")
    row["source_note"] = row.get("source_note") or _combined_evidence_value(evidence, "note")
    for field in METRIC_FIELDS:
        row[field] = "" if values.get(field) in (None, "") else str(values.get(field))
    row["operator_notes"] = _operator_notes(row.get("operator_notes"), values)
    return _template_row(row)


def build_evidence_rows(
    manual_values: dict[str, Any] | None,
    updated_row: dict[str, Any] | None,
    *,
    allow_missing_values: bool,
) -> list[dict[str, Any]]:
    if manual_values is None:
        return []
    values = manual_values.get("values") or {}
    evidence = manual_values.get("evidence") or {}
    company_id = manual_values.get("canonical_company_id")
    company_name = (
        manual_values.get("canonical_company_name")
        or (updated_row or {}).get("canonical_company_name")
    )
    period = _period_label(manual_values)
    rows: list[dict[str, Any]] = []
    for field in METRIC_FIELDS:
        item = evidence.get(field) if isinstance(evidence, dict) else None
        item = item if isinstance(item, dict) else {}
        value = values.get(field)
        found = value not in (None, "")
        rows.append(
            {
                "company_id": company_id,
                "company_name": company_name,
                "period": period,
                "source_url": manual_values.get("source_url"),
                "source_file_name": manual_values.get("source_file_name"),
                "field": field,
                "value": value,
                "page": item.get("page") or manual_values.get("source_page"),
                "table": item.get("table") or manual_values.get("source_table"),
                "note": item.get("note") or manual_values.get("source_note"),
                "confidence": item.get("confidence") or ("manual" if found else "missing"),
                "status": "found"
                if found
                else "missing"
                if allow_missing_values
                else "manual_required",
            }
        )
    return rows


def is_official_financial_source(
    source_url: Any,
    source_file_name: Any,
    company_name: Any,
    source_note: Any = None,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source_url_text = str(source_url or "").strip()
    file_name = str(source_file_name or "").strip()
    note_text = str(source_note or "").strip()
    host = ""
    if source_url_text:
        parsed = urllib.parse.urlparse(source_url_text.casefold())
        host = (parsed.netloc or parsed.path.split("/")[0]).removeprefix("www.")
        if any(hint in host for hint in BLOCKED_SOURCE_HINTS) or "wiki" in host:
            errors.append(
                {
                    "message": "source_url is blocked: use official issuer or disclosure documents, not wiki/encyclopedia pages",
                    "source_url": source_url_text,
                }
            )
        elif host and any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_SOURCE_DOMAIN_HINTS):
            return {"status": "passed", "warnings": warnings, "errors": errors}
        elif source_url_text and not _looks_like_report_file(source_url_text):
            warnings.append(
                {
                    "message": "source_url is not official-looking; use issuer or disclosure report links",
                    "source_url": source_url_text,
                    "company_name": company_name,
                }
            )
        else:
            warnings.append(
                {
                    "message": "source_url domain is not in the official-source allowlist; verify manually",
                    "source_url": source_url_text,
                    "company_name": company_name,
                }
            )
    if not source_url_text and file_name:
        if note_text:
            return {"status": "passed", "warnings": warnings, "errors": errors}
        warnings.append(
            {
                "message": "local source file is provided; add source_note describing the official origin",
                "source_file_name": file_name,
            }
        )
    if not source_url_text and not file_name:
        warnings.append({"message": "source_url or source_file_name is recommended"})
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "warnings": warnings,
        "errors": errors,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Official Source Financial Fill",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Summary",
        "",
        f"- Template input: `{report.get('template_input')}`",
        f"- Output: `{report.get('output')}`",
        f"- Dry run: `{report.get('dry_run')}`",
        f"- Rows read: {report.get('rows_read', 0)}",
        f"- Rows written: {report.get('rows_written', 0)}",
        f"- Updated canonical company ID: {report.get('updated_canonical_company_id')}",
        "",
        "## Evidence",
        "",
        "| Field | Value | Status | Page | Table | Confidence | Note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.get("evidence_rows") or []:
        lines.append(
            "| {field} | {value} | {status} | {page} | {table} | {confidence} | {note} |".format(
                field=row.get("field") or "",
                value="" if row.get("value") is None else row.get("value"),
                status=row.get("status") or "",
                page=row.get("page") or "",
                table=row.get("table") or "",
                confidence=row.get("confidence") or "",
                note=row.get("note") or "",
            )
        )
    lines.extend(["", "## Warnings", ""])
    if report.get("warnings"):
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    if report.get("errors"):
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Safety", ""])
    lines.extend(
        [
            "- This helper never calls backend import/apply endpoints.",
            "- Review the output CSV before running canonical pack preview.",
            "- Do not commit private filled financial data.",
        ]
    )
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report.get("next_steps", []))
    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_flow(args)
    print(f"[financial-report-official-source-fill] {report['status']}", flush=True)
    if args.output is not None and report.get("rows_written"):
        print(f"[financial-report-official-source-fill] wrote output: {args.output}", flush=True)
    if args.evidence_output is not None:
        print(
            f"[financial-report-official-source-fill] wrote evidence JSON: {args.evidence_output}",
            flush=True,
        )
    if args.markdown_output is not None:
        print(
            f"[financial-report-official-source-fill] wrote evidence Markdown: {args.markdown_output}",
            flush=True,
        )
    return exit_code


def _manual_values_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.manual_values_json is not None:
        payload = load_manual_values(args.manual_values_json)
    else:
        payload = {
            "canonical_company_id": args.company_id,
            "canonical_company_name": args.company_name,
            "values": {},
            "evidence": {},
        }
    if args.company_id is not None:
        _set_missing(payload, "canonical_company_id", args.company_id)
    if args.company_name:
        _set_missing(payload, "canonical_company_name", args.company_name)
    for field, value in _cli_defaults(args).items():
        if value not in (None, ""):
            _set_missing(payload, field, value)
    return payload


def _cli_defaults(args: argparse.Namespace | None) -> dict[str, Any]:
    if args is None:
        return {}
    return {
        "period_year": args.period_year,
        "period_quarter": args.period_quarter,
        "period_start_date": args.period_start_date,
        "period_end_date": args.period_end_date,
        "currency": args.currency,
        "accounting_standard": args.accounting_standard,
        "consolidation_scope": args.consolidation_scope,
        "value_scale": args.value_scale,
        "source_url": args.source_url,
        "source_file_name": None if args.source_file is None else args.source_file.name,
        "source": "official_issuer_report",
        "report_type": args.report_type,
    }


def _template_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        field: "" if raw.get(field) is None else raw.get(field)
        for field in CANONICAL_COLLECTION_FIELDS
    }


def _matching_template_indexes(
    rows: list[dict[str, Any]],
    *,
    canonical_company_id: int,
    company_name: str | None,
) -> list[int]:
    matches = [
        index
        for index, row in enumerate(rows)
        if _parse_int(row.get("canonical_company_id")) == canonical_company_id
    ]
    if matches or not company_name:
        return matches
    name = company_name.casefold()
    return [
        index
        for index, row in enumerate(rows)
        if str(row.get("canonical_company_name") or "").casefold() == name
    ]


def _field_has_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(value.get(field) not in (None, "") for field in ("page", "table", "note"))


def _combined_evidence_value(evidence: dict[str, Any], key: str) -> str:
    values: list[str] = []
    if not isinstance(evidence, dict):
        return ""
    for field in METRIC_FIELDS:
        item = evidence.get(field)
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if value not in (None, "") and str(value) not in values:
            values.append(str(value))
    return "; ".join(values)


def _operator_notes(existing: Any, values: dict[str, Any]) -> str:
    notes = [str(existing).strip()] if existing not in (None, "") else []
    missing = [field for field in METRIC_FIELDS if values.get(field) in (None, "")]
    if missing:
        notes.append("Missing values left empty: " + ", ".join(missing))
    notes.append("Official source fill helper output; review before preview.")
    result: list[str] = []
    for note in notes:
        if note and note not in result:
            result.append(note)
    return " ".join(result)


def _period_label(payload: dict[str, Any]) -> str:
    year = payload.get("period_year") or ""
    quarter = payload.get("period_quarter")
    if quarter in (None, "", 0, "0"):
        return f"{year} FY".strip()
    return f"{year} Q{quarter}".strip()


def _looks_like_report_file(value: str) -> bool:
    path = urllib.parse.urlparse(value).path.casefold()
    return path.endswith((".pdf", ".xls", ".xlsx", ".csv", ".html", ".htm"))


def _output_path_warnings(path: Path) -> list[dict[str, Any]]:
    normalized = path.as_posix().casefold()
    if any(hint in normalized for hint in PRIVATE_OUTPUT_HINTS):
        return []
    return [
        {
            "message": (
                "output path is not under data/financial_reports/private or "
                "logs/financial_reports; keep real filled financial data out of git"
            ),
            "output": str(path),
        }
    ]


def _next_steps(status: str) -> list[str]:
    if status == "failed":
        return [
            "Fix manual-values JSON metadata/evidence errors.",
            "Rerun this helper before canonical pack preview.",
        ]
    return [
        "Review the evidence report and filled private CSV.",
        "Run financial_report_canonical_pack.py in mode=preview only.",
        "Do not run apply/import until preview is reviewed and a PostgreSQL backup exists.",
    ]


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _set_missing(payload: dict[str, Any], key: str, value: Any) -> None:
    if payload.get(key) in (None, ""):
        payload[key] = value


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
