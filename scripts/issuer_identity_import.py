from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report


IDENTITY_FIELDS = [
    "company_id",
    "current_company_name",
    "legal_name",
    "short_name",
    "display_name",
    "inn",
    "ogrn",
    "kpp",
    "okpo",
    "country",
    "issuer_group_name",
    "issuer_group_inn",
    "issuer_role",
    "identity_status",
    "identity_confidence",
    "identity_source",
    "source_url",
    "source_file_name",
    "review_status",
    "review_notes",
]
ISSUER_ROLES = {
    "legal_issuer",
    "spv",
    "finance_subsidiary",
    "operating_company",
    "parent_group",
    "unknown",
}
IDENTITY_STATUSES = {"unknown", "weak", "matched", "verified", "conflict"}
IDENTITY_SOURCES = {
    "moex_iss",
    "operator_csv",
    "operator_json",
    "manual_review",
    "existing_company",
    "mixed",
}
REVIEW_STATUSES = {"pending", "reviewed", "accepted", "rejected"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate, preview, and optionally apply issuer identity updates.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--allow-conflicts", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def load_rows(path: Path, format_value: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    if format_value == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [
                {key: _normalize_value(value) for key, value in row.items() if key}
                for row in reader
            ]
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON input must be a list of row objects or an object with rows")
    return [{str(key): _normalize_value(value) for key, value in row.items()} for row in rows]


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []
    if not rows:
        errors.append({"row_index": None, "message": "input file has no rows"})

    for row_index, raw in enumerate(rows, start=1):
        row = {field: _normalize_value(raw.get(field)) for field in IDENTITY_FIELDS}
        row_errors: list[str] = []
        row_warnings: list[str] = []
        company_id = _parse_int(row.get("company_id"))
        if company_id is None or company_id <= 0:
            row_errors.append("company_id is required and must be positive")
        else:
            row["company_id"] = company_id

        if not row.get("legal_name") and not row.get("inn"):
            row_warnings.append("legal_name and inn are both missing")
        if row.get("issuer_role") and row["issuer_role"] not in ISSUER_ROLES:
            row_errors.append("issuer_role is invalid")
        if row.get("identity_status") and row["identity_status"] not in IDENTITY_STATUSES:
            row_errors.append("identity_status is invalid")
        if row.get("identity_source") and row["identity_source"] not in IDENTITY_SOURCES:
            row_errors.append("identity_source is invalid")
        if not row.get("identity_source"):
            row["identity_source"] = "operator_csv"
        if row.get("review_status") and row["review_status"] not in REVIEW_STATUSES:
            row_errors.append("review_status is invalid")
        if not row.get("review_status"):
            row["review_status"] = "pending"
        confidence = row.get("identity_confidence")
        if confidence not in (None, ""):
            try:
                parsed = float(confidence)
            except ValueError:
                row_errors.append("identity_confidence must be numeric")
            else:
                if parsed < 0 or parsed > 1:
                    row_errors.append("identity_confidence must be between 0 and 1")

        for message in row_errors:
            errors.append({"row_index": row_index, "company_id": company_id, "message": message})
        for message in row_warnings:
            warnings.append(
                {"row_index": row_index, "company_id": company_id, "message": message}
            )
        normalized_rows.append(row)

    invalid_indexes = {error["row_index"] for error in errors if error["row_index"] is not None}
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "total_rows": len(rows),
        "valid_rows": len(rows) - len(invalid_indexes),
        "invalid_rows": len(invalid_indexes),
        "rows": normalized_rows,
        "errors": errors,
        "warnings": warnings,
    }


def build_payload(
    rows: list[dict[str, Any]],
    *,
    rebuild_existing: bool,
    confirm_apply: bool = False,
    allow_conflicts: bool = False,
) -> dict[str, Any]:
    payload = {
        "rows": [_payload_row(row) for row in rows],
        "rebuild_existing": rebuild_existing,
    }
    if confirm_apply:
        payload["confirm_apply"] = True
        payload["allow_conflicts"] = allow_conflicts
    return payload


def run_flow(
    *,
    input_path: Path,
    format_value: str,
    backend_url: str,
    dry_run: bool,
    execute: str,
    confirm_apply: str,
    rebuild_existing: bool,
    allow_conflicts: bool,
    http_request: Any = None,
) -> dict[str, Any]:
    http_request = http_request or http_json
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        rows = load_rows(input_path, format_value)
        validation = validate_rows(rows)
    except Exception as exc:
        return {
            "status": "failed",
            "input": str(input_path),
            "validation": None,
            "preview": None,
            "apply": None,
            "errors": [{"message": str(exc)}],
            "warnings": [],
        }

    errors.extend(validation["errors"])
    warnings.extend(validation["warnings"])
    backend = backend_url.rstrip("/")
    preview = None
    apply_result = None

    should_apply = execute == "yes" and not dry_run
    if should_apply and confirm_apply != "yes":
        errors.append({"message": "execute=yes requires --confirm-apply yes"})
    if validation["status"] != "failed":
        preview_payload = build_payload(
            validation["rows"],
            rebuild_existing=rebuild_existing,
        )
        result = http_request(
            "POST",
            f"{backend}/api/companies/identity/preview",
            preview_payload,
        )
        preview = _http_data_or_warning(result, warnings, "identity preview")
        if should_apply and not errors:
            apply_payload = build_payload(
                validation["rows"],
                rebuild_existing=rebuild_existing,
                confirm_apply=True,
                allow_conflicts=allow_conflicts,
            )
            apply_response = http_request(
                "POST",
                f"{backend}/api/companies/identity/apply",
                apply_payload,
            )
            apply_result = _http_data_or_error(apply_response, errors, "identity apply")

    status_value = (
        "failed"
        if errors
        else "warning"
        if warnings or (isinstance(preview, dict) and preview.get("status") == "warning")
        else "passed"
    )
    if dry_run or execute == "no":
        next_steps = ["Review preview output before confirmed identity apply."]
    else:
        next_steps = ["Re-check identity diagnostics and then financial report targets."]
    return {
        "status": status_value,
        "input": str(input_path),
        "dry_run": dry_run or execute == "no",
        "validation": validation,
        "preview": preview,
        "apply": apply_result,
        "errors": errors,
        "warnings": warnings,
        "next_steps": next_steps,
    }


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    validation = report.get("validation") or {}
    lines = [
        "# BondRadar Issuer Identity Import",
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
        "",
        "## Errors",
        "",
    ]
    if report["errors"]:
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report.get("next_steps", []))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_flow(
        input_path=args.input,
        format_value=args.format,
        backend_url=args.backend_url,
        dry_run=args.dry_run,
        execute=args.execute,
        confirm_apply=args.confirm_apply,
        rebuild_existing=args.rebuild_existing,
        allow_conflicts=args.allow_conflicts,
    )
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[issuer-identity-import] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            f"[issuer-identity-import] wrote Markdown report: {args.markdown_output}",
            flush=True,
        )
    print(f"[issuer-identity-import] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _payload_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in IDENTITY_FIELDS if row.get(field) not in (None, "")}


def _http_data_or_warning(
    result: Any,
    warnings: list[dict[str, Any]],
    label: str,
) -> Any:
    if isinstance(result, HttpResult):
        if result.ok:
            return result.data
        warnings.append({"message": f"{label} request failed: {result.error or result.text}"})
        return None
    return result


def _http_data_or_error(
    result: Any,
    errors: list[dict[str, Any]],
    label: str,
) -> Any:
    if isinstance(result, HttpResult):
        if result.ok:
            return result.data
        errors.append({"message": f"{label} request failed: {result.error or result.text}"})
        return None
    return result


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return None if text == "" else text
    return value


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


if __name__ == "__main__":
    sys.exit(main())
