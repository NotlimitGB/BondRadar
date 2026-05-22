from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import http_json, write_json_report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render read-only financial scoring impact previews.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--company-ids", default="")
    parser.add_argument("--company-names", default="")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_preview(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    company_ids = _parse_int_list(args.company_ids)

    if args.company_names:
        resolved_ids, name_errors = _resolve_company_names(
            backend,
            args.company_names,
            http_request,
        )
        company_ids.extend(resolved_ids)
        errors.extend(name_errors)

    company_ids = sorted(set(company_ids))
    if not company_ids and not errors:
        errors.append({"message": "At least one company id or company name is required"})

    stats = _financial_report_stats(backend, http_request, warnings)
    companies: list[dict[str, Any]] = []
    if not errors:
        for company_id in company_ids:
            result = http_request(
                "GET",
                (
                    f"{backend}/api/financial-reports/scoring-preview/company/{company_id}"
                    "?include_diagnostics=true&include_bond_context=true"
                ),
            )
            if not result.ok or not isinstance(result.data, dict):
                errors.append(
                    {
                        "message": (
                            "financial scoring preview request failed for "
                            f"company_id={company_id}"
                        ),
                        "status_code": result.status_code,
                        "details": result.error or result.text,
                    }
                )
                continue
            companies.append(result.data)

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "company_count": len(companies),
        "requested_company_ids": company_ids,
        "companies": companies,
        "financial_reports_count": (
            None if stats is None else stats.get("financial_reports_count")
        ),
        "financial_report_stats": stats,
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "paper_trading_called": False,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, companies),
    }
    return report, 1 if status == "failed" else 0


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Financial Scoring Preview",
        "",
        "## Overall Status",
        "",
        f"`{report.get('status')}`",
        "",
    ]
    for item in report.get("companies") or []:
        latest = item.get("latest_report") or {}
        readiness = item.get("diagnostics_readiness") or {}
        adjustments = item.get("suggested_adjustments") or {}
        lines.extend(
            [
                "## Company",
                "",
                f"- company_id: {item.get('company_id')}",
                f"- company_name: {item.get('company_name')}",
                f"- canonical_company_id: {item.get('canonical_company_id')}",
                f"- canonical_company_name: {item.get('canonical_company_name')}",
                "",
                "## Latest Financial Report",
                "",
                f"- has_financial_report: {item.get('has_financial_report')}",
                f"- report_id: {latest.get('id')}",
                f"- period: {latest.get('period_year')} Q{latest.get('period_quarter')}",
                f"- period_end_date: {latest.get('period_end_date')}",
                f"- source: {latest.get('source')}",
                f"- signal: {latest.get('signal')}",
                "",
                "## Diagnostics Readiness",
                "",
                f"- safe_for_feature_pipeline: {readiness.get('safe_for_feature_pipeline')}",
                f"- safe_for_risk_scoring: {readiness.get('safe_for_risk_scoring')}",
                f"- risk_scoring_readiness: {readiness.get('risk_scoring_readiness')}",
                "",
                "## Financial Risk Factors",
                "",
                "| Factor | Value | Severity | Impact | Reason |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        factors = item.get("financial_risk_factors") or []
        if factors:
            for factor in factors:
                lines.append(
                    "| {factor} | {value} | {severity} | {impact} | {reason} |".format(
                        factor=factor.get("factor"),
                        value="" if factor.get("value") is None else factor.get("value"),
                        severity=factor.get("severity"),
                        impact=factor.get("impact"),
                        reason=factor.get("reason"),
                    )
                )
        else:
            lines.append("| None |  |  |  |  |")
        lines.extend(["", "## Fallback Metrics Used", ""])
        fallback = item.get("fallback_metrics_used") or {}
        if fallback:
            lines.extend(f"- {key}: {value}" for key, value in fallback.items())
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "## Blocking Reasons",
                "",
                f"- {_join(item.get('blocking_reasons'))}",
                "",
                "## Suggested Adjustments",
                "",
                f"- risk_penalty_points: {adjustments.get('risk_penalty_points')}",
                f"- risk_penalty_label: {adjustments.get('risk_penalty_label')}",
                f"- score_adjustment_points: {adjustments.get('score_adjustment_points')}",
                f"- score_adjustment_label: {adjustments.get('score_adjustment_label')}",
                "",
                "## Safety",
                "",
                f"- dry_run_only: {item.get('dry_run_only')}",
                f"- would_mutate_scores: {item.get('would_mutate_scores')}",
                f"- would_trigger_paper_trading: {item.get('would_trigger_paper_trading')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Report Safety",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- financial_reports_count: {report.get('financial_reports_count')}",
            "",
            "## Warnings",
            "",
        ]
    )
    if report.get("warnings"):
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    if report.get("errors"):
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {item}" for item in report.get("next_steps") or [])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_preview(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-scoring-preview] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            f"[financial-scoring-preview] wrote Markdown report: {args.markdown_output}",
            flush=True,
        )
    print(f"[financial-scoring-preview] {report['status']}", flush=True)
    return exit_code


def _resolve_company_names(
    backend: str,
    raw_names: str,
    http_request: Any,
) -> tuple[list[int], list[dict[str, Any]]]:
    ids: list[int] = []
    errors: list[dict[str, Any]] = []
    for name in _parse_str_list(raw_names):
        encoded = urllib.parse.urlencode({"query": name, "limit": 20})
        result = http_request("GET", f"{backend}/api/companies?{encoded}")
        if not result.ok or not isinstance(result.data, list):
            errors.append({"message": f"company name lookup failed for {name}"})
            continue
        exact = [row for row in result.data if str(row.get("name", "")).lower() == name.lower()]
        candidates = exact or result.data
        if len(candidates) != 1:
            errors.append({"message": f"company name is ambiguous or missing: {name}"})
            continue
        company_id = _parse_int(candidates[0].get("id"))
        if company_id is None:
            errors.append({"message": f"company lookup returned invalid id for {name}"})
            continue
        ids.append(company_id)
    return ids, errors


def _financial_report_stats(
    backend: str,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    result = http_request("GET", f"{backend}/api/financial-reports/stats")
    if not result.ok or not isinstance(result.data, dict):
        warnings.append({"message": "financial report stats endpoint was unavailable"})
        return None
    return result.data


def _next_steps(status: str, companies: list[dict[str, Any]]) -> list[str]:
    if status == "failed":
        return ["Fix preview input or backend availability, then rerun."]
    missing = sorted(
        {
            field
            for company in companies
            for field in (company.get("recommended_next_fields") or [])
        }
    )
    if missing:
        return [
            "Review preview-only factors; no score or risk penalty was applied.",
            "Collect missing fields from official issuer reports only: " + ", ".join(missing),
        ]
    return ["Review preview-only factors; no score or risk penalty was applied."]


def _parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for item in _parse_str_list(raw):
        parsed = _parse_int(item)
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_str_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _join(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, list):
        return ", ".join(str(item) for item in values)
    return str(values)


if __name__ == "__main__":
    sys.exit(main())
