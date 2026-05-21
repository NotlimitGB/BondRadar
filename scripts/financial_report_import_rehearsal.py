from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import (
    HttpResult,
    http_json,
    render_markdown as render_import_markdown,
    run_import_flow,
    write_json_report,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe before/after rehearsal for financial report import.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--source", default="operator_csv")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--stale-after-days", type=int, default=540)
    parser.add_argument("--execute", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-import", default=None)
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_rehearsal(
    args: argparse.Namespace,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    backend = args.backend_url.rstrip("/")

    before = _coverage(backend, args, http_request)
    if not before.ok:
        errors.append({"message": "coverage check before import failed", "details": _http_report(before)})

    if args.execute == "yes" and args.confirm_import != "yes":
        errors.append({"message": "confirmed import requires --confirm-import yes"})
        import_report = None
        import_exit_code = 1
    else:
        import_report, import_exit_code = run_import_flow(
            input_path=args.input,
            format_value=args.format,
            source=args.source,
            backend_url=args.backend_url,
            dry_run=args.execute != "yes",
            execute=args.execute,
            confirm_import=args.confirm_import,
            rebuild_existing=args.rebuild_existing,
            validate_companies=True,
            limit=args.limit,
            http_request=http_request,
        )
        warnings.extend(import_report.get("warnings") or [])
        errors.extend(import_report.get("errors") or [])
        if import_exit_code != 0:
            errors.append({"message": "import flow failed"})

    after = _coverage(backend, args, http_request)
    if not after.ok:
        errors.append({"message": "coverage check after import failed", "details": _http_report(after)})

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "execute": args.execute,
        "input": str(args.input),
        "format": args.format,
        "source": args.source,
        "coverage_before": _http_report(before),
        "import_report": import_report,
        "coverage_after": _http_report(after),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, args.execute == "yes"),
    }
    return report, 1 if status == "failed" else 0


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Financial Report Import Rehearsal",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Coverage",
        "",
        f"- Before status code: {report['coverage_before']['status_code']}",
        f"- After status code: {report['coverage_after']['status_code']}",
        "",
        "## Import",
        "",
    ]
    if report.get("import_report") is not None:
        lines.append(f"- Import flow status: {report['import_report']['status']}")
    else:
        lines.append("- Import flow was not run.")
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
        print(f"[financial-report-import-rehearsal] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
        print(f"[financial-report-import-rehearsal] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[financial-report-import-rehearsal] {report['status']}", flush=True)
    return exit_code


def _coverage(backend: str, args: argparse.Namespace, http_request: Any) -> HttpResult:
    params = {
        "active_only": "true",
        "stale_after_days": str(args.stale_after_days),
    }
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
        return ["Fix rehearsal errors and rerun before changing financial report data."]
    if not executed:
        return ["Review rehearsal output before a confirmed import."]
    return ["Run the post-ingest rebuild plan before any paper pilot review."]


if __name__ == "__main__":
    sys.exit(main())
