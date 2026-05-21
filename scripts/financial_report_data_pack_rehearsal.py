from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_collection_normalize import (
    load_collection_rows,
    normalize_rows,
    render_markdown as render_normalize_markdown,
    write_normalized_rows,
)
from financial_report_import import HttpResult, http_json, run_import_flow, write_json_report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe first financial report data pack rehearsal.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--stale-after-days", type=int, default=540)
    parser.add_argument("--normalized-output", type=Path, default=Path("logs/financial_reports/normalized_data_pack.csv"))
    parser.add_argument("--execute-import", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-import", default=None)
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_rehearsal(args: argparse.Namespace, http_request: Any = None) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    before = _coverage(backend, args, http_request)
    if not before.ok:
        warnings.append({"message": "coverage check before import was unavailable", "details": _http_report(before)})

    normalized_output = args.normalized_output
    output_format = "json" if normalized_output.suffix.lower() == ".json" else "csv"
    try:
        collection_rows = load_collection_rows(args.input, args.format)
        normalize_report = normalize_rows(
            collection_rows,
            default_currency="RUB",
            default_source="operator_collection",
            strict=False,
        )
        warnings.extend(normalize_report.get("warnings") or [])
        errors.extend(normalize_report.get("errors") or [])
        if not normalize_report["errors"]:
            write_normalized_rows(
                normalize_report["normalized_rows"],
                normalized_output,
                output_format,
            )
    except Exception as exc:
        normalize_report = {
            "status": "failed",
            "warnings": [],
            "errors": [{"message": str(exc)}],
            "normalized_rows": [],
        }
        errors.extend(normalize_report["errors"])

    import_report = None
    if args.execute_import == "yes" and args.confirm_import != "yes":
        errors.append({"message": "confirmed import requires --confirm-import yes"})
    elif not errors:
        import_report, import_exit_code = run_import_flow(
            input_path=normalized_output,
            format_value=output_format,
            source="operator_collection",
            backend_url=args.backend_url,
            dry_run=args.execute_import != "yes",
            execute=args.execute_import,
            confirm_import=args.confirm_import,
            rebuild_existing=args.rebuild_existing,
            validate_companies=True,
            limit=None,
            http_request=http_request,
        )
        warnings.extend(import_report.get("warnings") or [])
        errors.extend(import_report.get("errors") or [])
        if import_exit_code != 0:
            errors.append({"message": "import flow failed"})

    after = _coverage(backend, args, http_request)
    if not after.ok:
        warnings.append({"message": "coverage check after import was unavailable", "details": _http_report(after)})

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "execute_import": args.execute_import,
        "input": str(args.input),
        "normalized_output": str(normalized_output),
        "coverage_before": _http_report(before),
        "normalize_report": normalize_report,
        "import_report": import_report,
        "coverage_after": _http_report(after),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, args.execute_import == "yes"),
    }
    return report, 1 if status == "failed" else 0


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Financial Report Data Pack Rehearsal",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Flow",
        "",
        f"- Input: `{report['input']}`",
        f"- Normalized output: `{report['normalized_output']}`",
        f"- Import execution: `{report['execute_import']}`",
        "",
        "## Coverage",
        "",
        f"- Before status code: {report['coverage_before']['status_code']}",
        f"- After status code: {report['coverage_after']['status_code']}",
        "",
        "## Normalize",
        "",
        f"- Status: {report['normalize_report'].get('status')}",
        f"- Rows: {report['normalize_report'].get('total_rows', 0)}",
        "",
        "## Import",
        "",
    ]
    if report.get("import_report") is None:
        lines.append("- Import flow was not run.")
    else:
        lines.append(f"- Status: {report['import_report'].get('status')}")
    lines.extend(["", "## Warnings", ""])
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
    report, exit_code = run_rehearsal(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-report-data-pack] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[financial-report-data-pack] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[financial-report-data-pack] {report['status']}", flush=True)
    return exit_code


def _coverage(backend: str, args: argparse.Namespace, http_request: Any) -> HttpResult:
    params = {"active_only": "true", "stale_after_days": str(args.stale_after_days)}
    if args.as_of_date:
        params["as_of_date"] = args.as_of_date
    query = urllib.parse.urlencode(params)
    return http_request(
        "GET",
        f"{backend}/api/data-readiness/financial-reports/coverage?{query}",
        None,
    )


def _http_report(result: HttpResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status_code": result.status_code,
        "json": result.data,
        "error": result.error,
    }


def _next_steps(status: str, executed: bool) -> list[str]:
    if status == "failed":
        return ["Fix data pack rehearsal errors and rerun before import."]
    if not executed:
        return ["Review normalized output and backend preview before confirmed import."]
    return ["Run post-ingest rebuild plan before paper pilot review."]


if __name__ == "__main__":
    sys.exit(main())
