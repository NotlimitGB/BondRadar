from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report


DUPLICATE_REVIEW_FIELDS = [
    "canonical_company_id",
    "canonical_company_name",
    "candidate_company_id",
    "candidate_company_name",
    "match_type",
    "match_score",
    "match_reasons",
    "sample_secids",
    "sample_bond_names",
    "status",
    "review_status",
    "review_notes",
]
MATCH_TYPES = {
    "exact_inn",
    "exact_ogrn",
    "exact_legal_name",
    "normalized_name",
    "bond_name_phrase",
    "same_group_name",
    "manual_review",
    "mixed",
}
STATUS_VALUES = {"candidate", "accepted", "rejected", "needs_review", "conflict"}
REVIEW_STATUS_VALUES = {"pending", "reviewed", "accepted", "rejected"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview and optionally persist issuer duplicate review decisions.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--allow-conflicts", action="store_true")
    parser.add_argument("--allow-weak-canonical", action="store_true")
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
        row = {field: raw.get(field) for field in DUPLICATE_REVIEW_FIELDS}
        row_errors: list[str] = []
        canonical_id = _parse_int(row.get("canonical_company_id"))
        candidate_id = _parse_int(row.get("candidate_company_id"))
        if canonical_id is None or canonical_id <= 0:
            row_errors.append("canonical_company_id is required and must be positive")
        if candidate_id is None or candidate_id <= 0:
            row_errors.append("candidate_company_id is required and must be positive")
        if canonical_id is not None and candidate_id is not None and canonical_id == candidate_id:
            row_errors.append("canonical_company_id and candidate_company_id must differ")
        row["canonical_company_id"] = canonical_id
        row["candidate_company_id"] = candidate_id

        row["match_type"] = row.get("match_type") or "manual_review"
        if row["match_type"] not in MATCH_TYPES:
            row_errors.append("match_type is invalid")
        row["status"] = row.get("status") or "needs_review"
        if row["status"] not in STATUS_VALUES:
            row_errors.append("status is invalid")
        row["review_status"] = row.get("review_status") or "pending"
        if row["review_status"] not in REVIEW_STATUS_VALUES:
            row_errors.append("review_status is invalid")

        score = _parse_decimal(row.get("match_score"), default=Decimal("0.5000"))
        if score is None or score < 0 or score > 1:
            row_errors.append("match_score must be between 0 and 1")
        row["match_score"] = str(score) if score is not None else None
        row["match_reasons"] = _parse_list(row.get("match_reasons"))
        row["sample_secids"] = _parse_list(row.get("sample_secids"))
        row["sample_bond_names"] = _parse_list(row.get("sample_bond_names"))
        row["source"] = "manual_review"

        for message in row_errors:
            errors.append(
                {
                    "row_index": row_index,
                    "canonical_company_id": canonical_id,
                    "candidate_company_id": candidate_id,
                    "message": message,
                }
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


def run_flow(
    *,
    input_path: Path,
    format_value: str,
    backend_url: str,
    dry_run: bool,
    execute_apply: str,
    confirm_apply: str,
    allow_conflicts: bool,
    allow_weak_canonical: bool,
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
            "next_steps": ["Fix the duplicate review input file, then rerun preview."],
        }

    errors.extend(validation["errors"])
    warnings.extend(validation["warnings"])
    backend = backend_url.rstrip("/")
    preview = None
    apply_result = None
    should_apply = execute_apply == "yes" and not dry_run
    if should_apply and confirm_apply != "yes":
        errors.append({"message": "execute-apply=yes requires --confirm-apply yes"})

    if validation["status"] != "failed":
        preview_payload = _payload(
            validation["rows"],
            allow_conflicts=allow_conflicts,
            allow_weak_canonical=allow_weak_canonical,
        )
        preview_response = http_request(
            "POST",
            f"{backend}/api/companies/identity/duplicates/preview",
            preview_payload,
        )
        preview = _http_data_or_warning(preview_response, warnings, "duplicate preview")
        if should_apply and not errors:
            apply_payload = _payload(
                validation["rows"],
                allow_conflicts=allow_conflicts,
                allow_weak_canonical=allow_weak_canonical,
                confirm_apply=True,
            )
            apply_response = http_request(
                "POST",
                f"{backend}/api/companies/identity/duplicates/apply",
                apply_payload,
            )
            apply_result = _http_data_or_error(apply_response, errors, "duplicate apply")

    status_value = (
        "failed"
        if errors
        else "warning"
        if warnings
        or (isinstance(preview, dict) and preview.get("status") == "warning")
        or (isinstance(apply_result, dict) and apply_result.get("status") == "warning")
        else "passed"
    )
    return {
        "status": status_value,
        "input": str(input_path),
        "dry_run": dry_run or execute_apply == "no",
        "validation": validation,
        "preview": preview,
        "apply": apply_result,
        "errors": errors,
        "warnings": warnings,
        "next_steps": _next_steps(should_apply and apply_result is not None),
    }


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    validation = report.get("validation") or {}
    apply_result = report.get("apply")
    lines = [
        "# BondRadar Issuer Duplicate Review",
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
    ]
    if isinstance(apply_result, dict):
        summary = apply_result.get("affected_rows_summary") or {}
        lines.extend(
            [
                "",
                "## Affected Duplicate Decisions",
                "",
                f"- Created decisions: {summary.get('created_candidate_count', 0)}",
                f"- Updated decisions: {summary.get('updated_candidate_count', 0)}",
                f"- Skipped rows: {summary.get('skipped_count', 0)}",
                f"- Conflicts: {summary.get('conflict_count', 0)}",
                "",
                "| Canonical Company ID | Candidate Company ID | Action | Warnings |",
                "| ---: | ---: | --- | --- |",
            ]
        )
        for row in apply_result.get("rows") or []:
            lines.append(
                "| {canonical} | {candidate} | {action} | {warnings} |".format(
                    canonical=row.get("canonical_company_id"),
                    candidate=row.get("candidate_company_id"),
                    action=row.get("action") or "",
                    warnings="; ".join(item.get("message", "") for item in row.get("warnings") or []),
                )
            )
        lines.extend(
            [
                "",
                "## Rollback Note",
                "",
                "This script does not perform automatic rollback.",
                "",
                "Before applying duplicate review decisions on VDS, create a PostgreSQL backup.",
                "",
                "To rollback, restore the backup or manually review affected rows in:",
                "",
                "- company_identity_duplicate_candidates",
            ]
        )
    lines.extend(["", "## Errors", ""])
    if report["errors"]:
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Recommended Next Steps", ""])
    lines.extend(f"- {step}" for step in report.get("next_steps", []))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_flow(
        input_path=args.input,
        format_value=args.format,
        backend_url=args.backend_url,
        dry_run=args.dry_run,
        execute_apply=args.execute_apply,
        confirm_apply=args.confirm_apply,
        allow_conflicts=args.allow_conflicts,
        allow_weak_canonical=args.allow_weak_canonical,
    )
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[issuer-identity-duplicate-review] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[issuer-identity-duplicate-review] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    print(f"[issuer-identity-duplicate-review] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _payload(
    rows: list[dict[str, Any]],
    *,
    allow_conflicts: bool,
    allow_weak_canonical: bool,
    confirm_apply: bool = False,
) -> dict[str, Any]:
    payload = {
        "rows": [_payload_row(row) for row in rows],
        "allow_conflicts": allow_conflicts,
        "allow_weak_canonical": allow_weak_canonical,
    }
    if confirm_apply:
        payload["confirm_apply"] = True
    return payload


def _payload_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}


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


def _parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(";") if item.strip()]


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return None if text == "" else text
    return value


def _next_steps(applied: bool) -> list[str]:
    if applied:
        return [
            "Re-run duplicate diagnostics.",
            "Continue identity cleanup without merging companies automatically.",
        ]
    return [
        "Review preview output.",
        "Apply only reviewed duplicate decisions after a PostgreSQL backup.",
    ]


if __name__ == "__main__":
    sys.exit(main())
