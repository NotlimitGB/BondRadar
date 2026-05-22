from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_target_issuers import (
    build_report as build_target_issuer_report,
    parse_args as parse_target_issuer_args,
)
from financial_report_import import http_json, write_json_report


TARGET_SOURCE_CHOICES = ("company-id-list", "mixed", "target-issuers")
SAFE_TARGET_EXPORT_SOURCES = ("top-predictions", "bond-universe")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render read-only financial scoring impact previews.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--company-ids", default="")
    parser.add_argument("--company-names", default="")
    parser.add_argument(
        "--source",
        choices=TARGET_SOURCE_CHOICES,
        default="company-id-list",
    )
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--rollup-duplicates", action="store_true")
    parser.add_argument("--include-duplicate-members", action="store_true")
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
    target_source_report: dict[str, Any] | None = None

    if args.company_names:
        resolved_ids, name_errors = _resolve_company_names(
            backend,
            args.company_names,
            http_request,
        )
        company_ids.extend(resolved_ids)
        errors.extend(name_errors)

    if args.source in {"mixed", "target-issuers"}:
        target_ids, target_source_report = _collect_target_company_ids(
            args,
            http_request,
        )
        company_ids.extend(target_ids)
        warnings.extend(target_source_report.get("warnings") or [])
        errors.extend(target_source_report.get("errors") or [])

    company_ids = _dedupe_keep_order(company_ids)
    if not company_ids and not errors:
        errors.append(
            {
                "message": (
                    "At least one company id, company name, or target issuer source "
                    "is required"
                )
            }
        )

    stats = _financial_report_stats(backend, http_request, warnings)
    batch: dict[str, Any] = {
        "status": "failed" if errors else "passed",
        "company_count": 0,
        "summary": {},
        "top_negative_preview_companies": [],
        "missing_fields_summary": {},
        "risk_factor_summary": {},
        "companies": [],
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }
    if not errors:
        result = http_request(
            "POST",
            f"{backend}/api/financial-reports/scoring-preview/batch",
            {
                "company_ids": company_ids,
                "include_diagnostics": True,
                "include_bond_context": True,
            },
        )
        if not result.ok or not isinstance(result.data, dict):
            errors.append(
                {
                    "message": "financial scoring batch preview request failed",
                    "status_code": result.status_code,
                    "details": result.error or result.text,
                }
            )
        else:
            batch = result.data

    status = "failed" if errors else "warning" if warnings else batch.get("status", "passed")
    report = {
        **batch,
        "status": status,
        "company_count": batch.get("company_count", len(batch.get("companies") or [])),
        "requested_company_ids": company_ids,
        "target_source_report": target_source_report,
        "financial_reports_count": (
            None if stats is None else stats.get("financial_reports_count")
        ),
        "financial_report_stats": stats,
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, batch.get("companies") or []),
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
        f"- status: `{report.get('status')}`",
        f"- company_count: {report.get('company_count')}",
        f"- read_only: {report.get('read_only')}",
        f"- dry_run_only: {report.get('dry_run_only')}",
        "",
    ]
    target_source = report.get("target_source_report")
    if isinstance(target_source, dict):
        lines.extend(
            [
                "## Target Source",
                "",
                f"- source: {target_source.get('source')}",
                f"- safe_sources: {_join(target_source.get('safe_sources'))}",
                f"- collected_target_count: {target_source.get('target_count')}",
                "",
            ]
        )
    lines.extend(["## Batch Summary", ""])
    summary = report.get("summary") or {}
    if summary:
        lines.extend(f"- {key}: {value}" for key, value in summary.items())
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Top Negative Preview Companies",
            "",
            "| Company ID | Company | Readiness | Negative Factors | High/Critical Factors | Blocking Reasons |",
            "| ---: | --- | --- | ---: | ---: | --- |",
        ]
    )
    top_negative = report.get("top_negative_preview_companies") or []
    if top_negative:
        for row in top_negative:
            lines.append(
                "| {company_id} | {company_name} | {readiness} | {negative} | {high} | {blocking} |".format(
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    readiness=row.get("risk_scoring_readiness") or "",
                    negative=row.get("negative_factor_count") or 0,
                    high=row.get("high_factor_count") or 0,
                    blocking=_join(row.get("blocking_reasons")),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |")
    lines.extend(["", "## Missing Fields Summary", ""])
    missing_fields = report.get("missing_fields_summary") or {}
    if missing_fields:
        lines.extend(f"- {key}: {value}" for key, value in missing_fields.items())
    else:
        lines.append("- None")
    lines.extend(["", "## Risk Factor Summary", ""])
    factor_summary = report.get("risk_factor_summary") or {}
    if factor_summary:
        for factor, severities in factor_summary.items():
            details = ", ".join(
                f"{severity}: {count}" for severity, count in severities.items()
            )
            lines.append(f"- {factor}: {details}")
    else:
        lines.append("- None")
    lines.extend(["", "## Company Details", ""])
    for item in report.get("companies") or []:
        latest = item.get("latest_report") or {}
        readiness = item.get("diagnostics_readiness") or {}
        adjustments = item.get("suggested_adjustments") or {}
        lines.extend(
            [
                f"### Company {item.get('company_id')}",
                "",
                f"- company_id: {item.get('company_id')}",
                f"- company_name: {item.get('company_name')}",
                f"- canonical_company_id: {item.get('canonical_company_id')}",
                f"- canonical_company_name: {item.get('canonical_company_name')}",
                "",
                "### Latest Financial Report",
                "",
                f"- has_financial_report: {item.get('has_financial_report')}",
                f"- report_id: {latest.get('id')}",
                f"- period: {latest.get('period_year')} Q{latest.get('period_quarter')}",
                f"- period_end_date: {latest.get('period_end_date')}",
                f"- source: {latest.get('source')}",
                f"- signal: {latest.get('signal')}",
                "",
                "### Diagnostics Readiness",
                "",
                f"- safe_for_feature_pipeline: {readiness.get('safe_for_feature_pipeline')}",
                f"- safe_for_risk_scoring: {readiness.get('safe_for_risk_scoring')}",
                f"- risk_scoring_readiness: {readiness.get('risk_scoring_readiness')}",
                "",
                "### Financial Risk Factors",
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
        lines.extend(["", "### Fallback Metrics Used", ""])
        fallback = item.get("fallback_metrics_used") or {}
        if fallback:
            lines.extend(f"- {key}: {value}" for key, value in fallback.items())
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "### Blocking Reasons",
                "",
                f"- {_join(item.get('blocking_reasons'))}",
                "",
                "### Suggested Adjustments",
                "",
                f"- risk_penalty_points: {adjustments.get('risk_penalty_points')}",
                f"- risk_penalty_label: {adjustments.get('risk_penalty_label')}",
                f"- score_adjustment_points: {adjustments.get('score_adjustment_points')}",
                f"- score_adjustment_label: {adjustments.get('score_adjustment_label')}",
                "",
                "### Safety",
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
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
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


def _collect_target_company_ids(
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[int], dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    company_ids: list[int] = []
    source_reports: list[dict[str, Any]] = []

    for source in SAFE_TARGET_EXPORT_SOURCES:
        target_args = _target_export_args(args, source)
        try:
            report = build_target_issuer_report(target_args, http_request=http_request)
        except Exception as exc:
            warnings.append({"message": f"{source} target source failed: {exc}"})
            continue
        source_reports.append(
            {
                "source": source,
                "status": report.get("status"),
                "target_count": report.get("total_targets"),
                "rollup_summary": report.get("rollup_summary"),
            }
        )
        warnings.extend(report.get("warnings") or [])
        errors.extend(report.get("errors") or [])
        for row in report.get("targets") or []:
            company_id = _parse_int(
                row.get("canonical_company_id") or row.get("company_id")
            )
            if company_id is not None:
                company_ids.append(company_id)

    company_ids = _dedupe_keep_order(company_ids)[: max(1, args.limit)]
    return company_ids, {
        "source": args.source,
        "safe_sources": list(SAFE_TARGET_EXPORT_SOURCES),
        "target_count": len(company_ids),
        "source_reports": source_reports,
        "warnings": warnings,
        "errors": errors,
    }


def _target_export_args(args: argparse.Namespace, source: str) -> argparse.Namespace:
    argv = [
        "--backend-url",
        args.backend_url,
        "--source",
        source,
        "--limit",
        str(max(1, args.limit)),
    ]
    if args.model_run_id is not None:
        argv.extend(["--model-run-id", str(args.model_run_id)])
    if args.as_of_date:
        argv.extend(["--as-of-date", str(args.as_of_date)])
    if args.use_duplicate_mapping:
        argv.append("--use-duplicate-mapping")
    if args.rollup_duplicates:
        argv.append("--rollup-duplicates")
    if args.include_duplicate_members:
        argv.append("--include-duplicate-members")
    return parse_target_issuer_args(argv)


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


def _dedupe_keep_order(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
