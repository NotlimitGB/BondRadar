from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report
from issuer_identity_import import (
    IDENTITY_FIELDS,
    build_payload,
    load_rows,
    validate_rows,
)
from issuer_identity_target_export import build_report as build_target_report


DIAGNOSTIC_METRICS = (
    "company_count",
    "unknown_company_count",
    "weak_identity_count",
    "verified_identity_count",
)
RECOMMENDED_NEXT_STEPS = [
    "Review generated target list.",
    "Fill identity review CSV manually from reliable sources.",
    "Run preview mode.",
    "Create PostgreSQL backup.",
    "Apply only reviewed rows.",
    "Re-run identity diagnostics.",
    "Then collect financial reports for verified issuers.",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe issuer identity batch rehearsal.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--source",
        choices=(
            "unknown-companies",
            "paper-positions",
            "top-predictions",
            "bond-universe",
            "mixed",
        ),
        default="mixed",
    )
    parser.add_argument("--portfolio-id", type=int, default=None)
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--review-template-output", type=Path, default=None)
    parser.add_argument("--reviewed-input", type=Path, default=None)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--execute-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--allow-conflicts", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_rehearsal(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    diagnostics_before = _fetch_diagnostics(backend, args.limit, http_request, warnings)
    targets = _build_targets(args, http_request, warnings)
    review_template = _write_review_template_if_requested(
        targets,
        args.review_template_output,
        warnings,
    )
    validation = None
    preview = None
    apply_result = None
    apply_executed = False

    if args.execute_apply == "yes" and args.reviewed_input is None:
        errors.append({"message": "--execute-apply yes requires --reviewed-input"})
    if args.execute_apply == "yes" and args.confirm_apply != "yes":
        errors.append({"message": "--execute-apply yes requires --confirm-apply yes"})

    if args.reviewed_input is None:
        warnings.append(
            {
                "message": (
                    "No reviewed input provided; generated template only. "
                    "Fill the review CSV manually before preview/apply."
                )
            }
        )
    else:
        try:
            reviewed_rows = load_rows(args.reviewed_input, args.format)
            validation = validate_rows(reviewed_rows)
        except Exception as exc:
            validation = None
            errors.append({"message": str(exc)})
        else:
            warnings.extend(validation.get("warnings") or [])
            errors.extend(validation.get("errors") or [])
            if validation.get("status") != "failed":
                preview_payload = build_payload(
                    validation["rows"],
                    rebuild_existing=args.rebuild_existing,
                )
                preview_response = http_request(
                    "POST",
                    f"{backend}/api/companies/identity/preview",
                    preview_payload,
                )
                preview = _http_data_or_error(preview_response, errors, "identity preview")
                if isinstance(preview, dict) and preview.get("status") == "failed":
                    errors.append({"message": "identity preview returned failed status"})
                if (
                    args.execute_apply == "yes"
                    and args.confirm_apply == "yes"
                    and not errors
                ):
                    apply_payload = build_payload(
                        validation["rows"],
                        rebuild_existing=args.rebuild_existing,
                        confirm_apply=True,
                        allow_conflicts=args.allow_conflicts,
                    )
                    apply_response = http_request(
                        "POST",
                        f"{backend}/api/companies/identity/apply",
                        apply_payload,
                    )
                    apply_result = _http_data_or_error(
                        apply_response,
                        errors,
                        "identity apply",
                    )
                    apply_executed = apply_result is not None
                    if isinstance(apply_result, dict) and apply_result.get("status") == "failed":
                        errors.append({"message": "identity apply returned failed status"})

    diagnostics_after = _fetch_diagnostics(backend, args.limit, http_request, warnings)
    diagnostics_diff = _diagnostics_diff(diagnostics_before, diagnostics_after)
    affected_summary = _affected_summary(apply_result)
    status_value = _status(errors, warnings, targets, preview, apply_result)
    report = {
        "status": status_value,
        "backend_url": args.backend_url,
        "source": args.source,
        "review_template_output": None
        if review_template is None
        else str(review_template.get("path")),
        "reviewed_input": None if args.reviewed_input is None else str(args.reviewed_input),
        "execute_apply": args.execute_apply,
        "apply_executed": apply_executed,
        "diagnostics_before": diagnostics_before,
        "diagnostics_after": diagnostics_after,
        "diagnostics_diff": diagnostics_diff,
        "targets": targets,
        "review_template": review_template,
        "validation": validation,
        "preview": preview,
        "apply": apply_result,
        "affected_rows_summary": affected_summary,
        "errors": errors,
        "warnings": warnings,
        "next_steps": RECOMMENDED_NEXT_STEPS,
    }
    return report, 1 if status_value == "failed" else 0


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    diff = report.get("diagnostics_diff") or {}
    targets = report.get("targets") or {}
    affected = report.get("affected_rows_summary") or {}
    lines = [
        "# BondRadar Issuer Identity Batch Rehearsal",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Identity Coverage Diff",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for metric in DIAGNOSTIC_METRICS:
        lines.append(
            "| {metric} | {before} | {after} | {delta} |".format(
                metric=metric,
                before=diff.get(f"before_{metric}", 0),
                after=diff.get(f"after_{metric}", 0),
                delta=diff.get(f"delta_{metric}", 0),
            )
        )
    lines.extend(
        [
            "",
            "## Targets",
            "",
            f"- Total targets: {targets.get('total_targets', 0)}",
            f"- Review template: {report.get('review_template_output') or 'not written'}",
            "",
            "## Affected Rows Summary",
            "",
            f"- Affected company IDs: {affected.get('affected_company_ids', [])}",
            f"- Created profiles: {affected.get('created_profile_count', 0)}",
            f"- Updated profiles: {affected.get('updated_profile_count', 0)}",
            f"- Updated companies: {affected.get('updated_company_count', 0)}",
            f"- Skipped rows: {affected.get('skipped_count', 0)}",
            f"- Conflicts: {affected.get('conflict_count', 0)}",
            f"- Warnings: {affected.get('warning_count', 0)}",
        ]
    )
    if report.get("apply_executed"):
        lines.extend(_affected_companies_table(report))
        lines.extend(
            [
                "",
                "## Rollback Note",
                "",
                "This script does not perform automatic rollback.",
                "",
                "Before applying identity changes on VDS, create a PostgreSQL backup.",
                "",
                "To rollback, restore the backup or manually review affected rows in:",
                "- company_identity_profiles",
                "- companies",
            ]
        )
    lines.extend(["", "## Errors", ""])
    if report.get("errors"):
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    if report.get("warnings"):
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Recommended Next Steps", ""])
    for index, step in enumerate(report.get("next_steps") or RECOMMENDED_NEXT_STEPS, start=1):
        lines.append(f"{index}. {step}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_rehearsal(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(
            f"[issuer-identity-batch] wrote JSON report: {args.json_output}",
            flush=True,
        )
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            f"[issuer-identity-batch] wrote Markdown report: {args.markdown_output}",
            flush=True,
        )
    print(f"[issuer-identity-batch] {report['status']}", flush=True)
    return exit_code


def _fetch_diagnostics(
    backend: str,
    limit: int,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    result = http_request(
        "GET",
        f"{backend}/api/companies/identity/diagnostics?active_only=true&limit={max(1, limit)}",
        None,
    )
    if isinstance(result, HttpResult):
        if result.ok and isinstance(result.data, dict):
            return result.data
        warnings.append(
            {"message": f"identity diagnostics request failed: {result.error or result.text}"}
        )
        return None
    return result if isinstance(result, dict) else None


def _build_targets(
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    target_args = argparse.Namespace(
        backend_url=args.backend_url,
        source=args.source,
        portfolio_id=args.portfolio_id,
        model_run_id=args.model_run_id,
        as_of_date=args.as_of_date,
        limit=args.limit,
        json_output=None,
        csv_output=None,
        markdown_output=None,
    )
    report = build_target_report(target_args, http_request=http_request)
    warnings.extend(report.get("warnings") or [])
    return report


def _write_review_template_if_requested(
    targets: dict[str, Any],
    path: Path | None,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if path is None:
        return None
    rows = [_template_row(target) for target in targets.get("targets", [])]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IDENTITY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if not rows:
        warnings.append({"message": "review template has no target rows"})
    return {"path": str(path), "rows_written": len(rows)}


def _template_row(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": target.get("company_id"),
        "current_company_name": target.get("company_name"),
        "legal_name": "",
        "short_name": "",
        "display_name": "",
        "inn": target.get("inn") or "",
        "ogrn": "",
        "kpp": "",
        "okpo": "",
        "country": "RU",
        "issuer_group_name": "",
        "issuer_group_inn": "",
        "issuer_role": "unknown",
        "identity_status": "weak",
        "identity_confidence": "",
        "identity_source": "operator_csv",
        "source_url": "",
        "source_file_name": "",
        "review_status": "pending",
        "review_notes": (
            f"Target reason: {target.get('reason') or ''}. "
            f"Search hint: {target.get('suggested_search_query') or ''}."
        ).strip(),
    }


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


def _diagnostics_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for metric in DIAGNOSTIC_METRICS:
        before_value = int((before or {}).get(metric) or 0)
        after_value = int((after or {}).get(metric) or 0)
        diff[f"before_{metric}"] = before_value
        diff[f"after_{metric}"] = after_value
        diff[f"delta_{metric}"] = after_value - before_value
    return diff


def _affected_summary(apply_result: Any) -> dict[str, Any]:
    if not isinstance(apply_result, dict):
        return {
            "affected_company_ids": [],
            "created_profile_count": 0,
            "updated_profile_count": 0,
            "updated_company_count": 0,
            "skipped_count": 0,
            "conflict_count": 0,
            "warning_count": 0,
        }
    summary = apply_result.get("affected_rows_summary")
    if isinstance(summary, dict):
        return summary
    rows = apply_result.get("rows") or []
    return {
        "affected_company_ids": sorted(
            {
                row.get("company_id")
                for row in rows
                if row.get("action") in {"created", "updated"}
            }
        ),
        "created_profile_count": apply_result.get("created", 0),
        "updated_profile_count": apply_result.get("updated", 0),
        "updated_company_count": apply_result.get("company_updates", 0),
        "skipped_count": apply_result.get("skipped", 0),
        "conflict_count": sum(len(row.get("conflicts") or []) for row in rows),
        "warning_count": len(apply_result.get("warnings") or []),
    }


def _affected_companies_table(report: dict[str, Any]) -> list[str]:
    input_rows = {
        row.get("company_id"): row
        for row in ((report.get("validation") or {}).get("rows") or [])
    }
    apply_rows = (report.get("apply") or {}).get("rows") or []
    lines = [
        "",
        "## Affected Companies",
        "",
        "| Company ID | Current Name | Legal Name | Action | Warnings |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for row in apply_rows:
        company_id = row.get("company_id")
        input_row = input_rows.get(company_id, {})
        warnings = "; ".join(
            item.get("message", "") for item in (row.get("warnings") or [])
        )
        lines.append(
            "| {company_id} | {current} | {legal} | {action} | {warnings} |".format(
                company_id=company_id,
                current=input_row.get("current_company_name") or "",
                legal=input_row.get("legal_name") or "",
                action=row.get("action") or "",
                warnings=warnings,
            )
        )
    return lines


def _status(
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    targets: dict[str, Any],
    preview: Any,
    apply_result: Any,
) -> str:
    if errors:
        return "failed"
    child_statuses = [
        targets.get("status") if isinstance(targets, dict) else None,
        preview.get("status") if isinstance(preview, dict) else None,
        apply_result.get("status") if isinstance(apply_result, dict) else None,
    ]
    if "failed" in child_statuses:
        return "failed"
    if warnings or any(status in {"warning", "completed_with_errors"} for status in child_statuses):
        return "warning"
    return "passed"


if __name__ == "__main__":
    sys.exit(main())
