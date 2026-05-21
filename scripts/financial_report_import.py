from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence


SUPPORTED_FIELDS = [
    "company_id",
    "company_ticker",
    "company_inn",
    "period_year",
    "period_quarter",
    "period_start_date",
    "period_end_date",
    "published_at",
    "document_date",
    "currency",
    "source",
    "source_url",
    "source_file_name",
    "report_type",
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
    "debt_to_ebitda",
    "interest_coverage",
]
IDENTIFIER_FIELDS = ("company_id", "company_ticker", "company_inn")
NUMERIC_FIELDS = (
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
    "debt_to_ebitda",
    "interest_coverage",
)
NON_NEGATIVE_FIELDS = {
    "revenue",
    "total_debt",
    "cash",
    "short_term_debt",
    "interest_expense",
}
WARNING_NEGATIVE_FIELDS = {"equity", "net_profit"}


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status_code: int | None
    data: Any = None
    text: str = ""
    error: str | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and optionally import structured BondRadar financial reports.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--source", default="operator_csv")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-import", default=None)
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--validate-companies", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def load_rows(path: Path, format_value: str, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    if format_value == "csv":
        rows = _load_csv(path)
    elif format_value == "json":
        rows = _load_json(path)
    else:
        raise ValueError(f"unsupported format: {format_value}")
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        return rows[:limit]
    return rows


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [
            {key: _normalize_value(value) for key, value in row.items() if key}
            for row in reader
        ]


def _load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("JSON input must be a list of rows or an object with rows")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON rows must be objects")
    return [{str(key): _normalize_value(value) for key, value in row.items()} for row in rows]


def validate_rows(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []
    field_counts = {field: 0 for field in SUPPORTED_FIELDS}
    seen: dict[tuple[str, str, int, int], int] = {}

    if not rows:
        errors.append({"row_index": None, "message": "input file has no rows"})

    for row_index, raw in enumerate(rows, start=1):
        row = {field: _normalize_value(raw.get(field)) for field in SUPPORTED_FIELDS}
        if not row.get("source"):
            row["source"] = source
        row_errors: list[str] = []
        row_warnings: list[str] = []
        identifier = _identifier_key(row)
        if identifier is None:
            row_errors.append("company identifier is required")

        period_year = _parse_int(row.get("period_year"))
        if period_year is None:
            row_errors.append("period_year is required and must be an integer")
        elif period_year < 1900 or period_year > 2100:
            row_errors.append("period_year must be between 1900 and 2100")

        period_quarter = _parse_int(row.get("period_quarter"), default=0)
        if period_quarter is None or period_quarter < 0 or period_quarter > 4:
            row_errors.append("period_quarter must be between 0 and 4")

        if identifier is not None and period_year is not None and period_quarter is not None:
            duplicate_key = (*identifier, period_year, period_quarter)
            first_row = seen.get(duplicate_key)
            if first_row is not None:
                row_errors.append(
                    f"duplicate company-period row; first seen at row {first_row}"
                )
            else:
                seen[duplicate_key] = row_index

        for field in NUMERIC_FIELDS:
            value = row.get(field)
            if value in (None, ""):
                continue
            parsed = _parse_decimal(value)
            if parsed is None:
                row_errors.append(f"invalid decimal value for {field}")
                continue
            if field in NON_NEGATIVE_FIELDS and parsed < 0:
                row_errors.append(f"{field} cannot be negative")
            elif field in WARNING_NEGATIVE_FIELDS and parsed < 0:
                row_warnings.append(f"{field} is negative")

        for field in SUPPORTED_FIELDS:
            if row.get(field) not in (None, ""):
                field_counts[field] += 1

        for message in row_errors:
            errors.append(_message(row, row_index, message))
        for message in row_warnings:
            warnings.append(_message(row, row_index, message))
        normalized_rows.append(row)

    valid_row_indexes = {
        index
        for index in range(1, len(normalized_rows) + 1)
        if not any(error.get("row_index") == index for error in errors)
    }
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "total_rows": len(rows),
        "valid_rows": len(valid_row_indexes),
        "invalid_rows": len(rows) - len(valid_row_indexes),
        "rows": normalized_rows,
        "errors": errors,
        "warnings": warnings,
        "field_coverage": field_counts,
        "duplicate_count": sum(
            1 for error in errors if "duplicate company-period row" in error["message"]
        ),
        "missing_company_identifier_count": sum(
            1 for error in errors if error["message"] == "company identifier is required"
        ),
    }


def build_payload(rows: list[dict[str, Any]], source: str, rebuild_existing: bool) -> dict[str, Any]:
    return {
        "source": source,
        "rows": [_payload_row(row, source) for row in rows],
        "rebuild_existing": rebuild_existing,
    }


def run_import_flow(
    *,
    input_path: Path,
    format_value: str,
    source: str,
    backend_url: str,
    dry_run: bool,
    execute: str,
    confirm_import: str | None,
    rebuild_existing: bool,
    validate_companies: bool,
    limit: int | None,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        rows = load_rows(input_path, format_value, limit)
        validation = validate_rows(rows, source)
    except Exception as exc:
        report = {
            "status": "failed",
            "input": str(input_path),
            "format": format_value,
            "source": source,
            "validation": None,
            "preview": None,
            "ingest": None,
            "warnings": [],
            "errors": [{"message": str(exc)}],
        }
        return report, 1

    warnings.extend(validation["warnings"])
    errors.extend(validation["errors"])
    payload = build_payload(validation["rows"], source, rebuild_existing)
    preview = None
    ingest = None
    backend = backend_url.rstrip("/")

    if validate_companies and not errors:
        preview_result = http_request("POST", f"{backend}/api/financial-reports/preview", payload)
        preview = _http_report(preview_result)
        if not preview_result.ok:
            errors.append({"message": "company preview request failed", "details": preview})
        elif isinstance(preview_result.data, dict):
            errors.extend(preview_result.data.get("errors") or [])
            warnings.extend(preview_result.data.get("warnings") or [])

    should_execute = execute == "yes" and not dry_run
    if should_execute and confirm_import != "yes":
        errors.append({"message": "confirmed import requires --confirm-import yes"})
    if should_execute and not errors:
        ingest_result = http_request("POST", f"{backend}/api/financial-reports/ingest", payload)
        ingest = _http_report(ingest_result)
        if not ingest_result.ok:
            errors.append({"message": "ingest request failed", "details": ingest})
        elif isinstance(ingest_result.data, dict):
            errors.extend(ingest_result.data.get("errors") or [])
            warnings.extend(ingest_result.data.get("warnings") or [])

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "input": str(input_path),
        "format": format_value,
        "source": source,
        "dry_run": not should_execute,
        "rebuild_existing": rebuild_existing,
        "validation": validation,
        "preview": preview,
        "ingest": ingest,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, should_execute),
    }
    return report, 1 if status == "failed" else 0


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> HttpResult:
    body: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
            return HttpResult(
                ok=200 <= response.status < 300,
                status_code=response.status,
                data=_json_or_none(text),
                text=text,
            )
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return HttpResult(
            ok=False,
            status_code=exc.code,
            data=_json_or_none(text),
            text=text,
            error=str(exc),
        )
    except urllib.error.URLError as exc:
        return HttpResult(ok=False, status_code=None, error=str(exc.reason))
    except TimeoutError as exc:
        return HttpResult(ok=False, status_code=None, error=str(exc))


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    validation = report.get("validation") or {}
    lines = [
        "# BondRadar Financial Report Import",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Validation",
        "",
        f"- Total rows: {validation.get('total_rows', 0)}",
        f"- Valid rows: {validation.get('valid_rows', 0)}",
        f"- Invalid rows: {validation.get('invalid_rows', 0)}",
        f"- Duplicate rows: {validation.get('duplicate_count', 0)}",
        "",
        "## Warnings",
        "",
    ]
    if report["warnings"]:
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    if report["errors"]:
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report["next_steps"])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_import_flow(
        input_path=args.input,
        format_value=args.format,
        source=args.source,
        backend_url=args.backend_url,
        dry_run=args.dry_run,
        execute=args.execute,
        confirm_import=args.confirm_import,
        rebuild_existing=args.rebuild_existing,
        validate_companies=args.validate_companies,
        limit=args.limit,
    )
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-report-import] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[financial-report-import] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[financial-report-import] {report['status']}", flush=True)
    return exit_code


def _payload_row(row: dict[str, Any], source: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in SUPPORTED_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            payload[field] = value
    payload.setdefault("source", source)
    return payload


def _identifier_key(row: dict[str, Any]) -> tuple[str, str] | None:
    for field in IDENTIFIER_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            return (field, str(value).strip())
    return None


def _parse_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _message(row: dict[str, Any], row_index: int | None, message: str) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "company_id": row.get("company_id"),
        "company_ticker": row.get("company_ticker"),
        "company_inn": row.get("company_inn"),
        "period_year": row.get("period_year"),
        "period_quarter": row.get("period_quarter"),
        "message": message,
    }


def _http_report(result: HttpResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status_code": result.status_code,
        "json": result.data,
        "error": result.error,
    }


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _next_steps(status: str, executed: bool) -> list[str]:
    if status == "failed":
        return ["Fix invalid rows or backend validation errors, then rerun dry-run."]
    if not executed:
        return ["Review the dry-run report before running a confirmed import."]
    return [
        "Re-check financial report coverage.",
        "Plan post-ingest rebuild for credit health, risk assessments, and features.",
    ]


if __name__ == "__main__":
    sys.exit(main())
