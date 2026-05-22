from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import http_json, write_json_report
from financial_report_target_issuers import (
    build_report as build_target_issuer_report,
    parse_args as parse_target_issuer_args,
)


TARGET_SOURCE_CHOICES = ("company-id-list", "mixed", "target-issuers")
SAFE_TARGET_EXPORT_SOURCES = ("top-predictions", "bond-universe")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a read-only financial report collection priority queue.",
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
    parser.add_argument("--include-covered", action="store_true", default=False)
    parser.add_argument("--exclude-government-like", action="store_true", default=True)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_queue(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    company_ids: list[int] = []
    source_presence: dict[int, list[str]] = {}
    target_source_report: dict[str, Any] | None = None

    manual_ids = _parse_int_list(args.company_ids)
    for company_id in manual_ids:
        _add_source_label(source_presence, company_id, "manual-id")
    company_ids.extend(manual_ids)

    if args.company_names:
        resolved_ids, name_errors = _resolve_company_names(
            backend,
            args.company_names,
            http_request,
        )
        for company_id in resolved_ids:
            _add_source_label(source_presence, company_id, "company-name")
        company_ids.extend(resolved_ids)
        errors.extend(name_errors)

    if args.source in {"mixed", "target-issuers"}:
        target_ids, target_presence, target_source_report = _collect_target_company_ids(
            args,
            http_request,
        )
        company_ids.extend(target_ids)
        _merge_source_presence(source_presence, target_presence)
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
    priority_report: dict[str, Any] = {
        "status": "failed" if errors else "passed",
        "company_count": 0,
        "queue_count": 0,
        "summary": {},
        "priority_queue": [],
        "already_covered": [],
        "excluded_or_deprioritized": [],
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
            f"{backend}/api/financial-reports/collection-priority/batch",
            {
                "company_ids": company_ids,
                "source_presence": {
                    str(company_id): labels
                    for company_id, labels in sorted(source_presence.items())
                },
                "include_covered": bool(args.include_covered),
                "exclude_government_like": bool(args.exclude_government_like),
            },
        )
        if not result.ok or not isinstance(result.data, dict):
            errors.append(
                {
                    "message": "financial collection priority request failed",
                    "status_code": result.status_code,
                    "details": result.error or result.text,
                }
            )
        else:
            priority_report = result.data

    status = (
        "failed"
        if errors
        else "warning"
        if warnings
        else priority_report.get("status", "passed")
    )
    report = {
        **priority_report,
        "status": status,
        "requested_company_ids": company_ids,
        "requested_source_presence": {
            str(company_id): labels for company_id, labels in sorted(source_presence.items())
        },
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
        "next_steps": _next_steps(status),
    }
    return report, 1 if status == "failed" else 0


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Financial Report Collection Priority Queue",
        "",
        "## Overall Status",
        "",
        f"- status: `{report.get('status')}`",
        f"- company_count: {report.get('company_count')}",
        f"- queue_count: {report.get('queue_count')}",
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
    lines.extend(["## Summary", ""])
    summary = report.get("summary") or {}
    if summary:
        lines.extend(f"- {key}: {value}" for key, value in summary.items())
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Top Priority Queue",
            "",
            "| Rank | Company ID | Company | Type | Level | Score | Report | Readiness | Sources | Reasons |",
            "| ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    queue = report.get("priority_queue") or []
    if queue:
        for row in queue:
            source = row.get("source_presence") or {}
            lines.append(
                "| {rank} | {company_id} | {company_name} | {issuer_type} | {level} | {score} | {report} | {readiness} | {sources} | {reasons} |".format(
                    rank=row.get("rank"),
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    issuer_type=row.get("issuer_type") or "",
                    level=row.get("priority_level") or "",
                    score=row.get("priority_score"),
                    report=row.get("has_financial_report"),
                    readiness=row.get("risk_scoring_readiness") or "",
                    sources=_join(source.get("source_labels")),
                    reasons=_join(row.get("priority_reasons")),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Already Covered / Partial",
            "",
            "| Company ID | Company | Readiness | Next Fields |",
            "| ---: | --- | --- | --- |",
        ]
    )
    covered = report.get("already_covered") or []
    if covered:
        for row in covered:
            lines.append(
                "| {company_id} | {company_name} | {readiness} | {fields} |".format(
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    readiness=row.get("risk_scoring_readiness") or "",
                    fields=_join(row.get("recommended_next_fields")),
                )
            )
    else:
        lines.append("| None |  |  |  |")
    lines.extend(
        [
            "",
            "## Excluded or Deprioritized",
            "",
            "| Company ID | Company | Type | Reasons | Reason |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    excluded = report.get("excluded_or_deprioritized") or []
    if excluded:
        for row in excluded:
            lines.append(
                "| {company_id} | {company_name} | {issuer_type} | {classification} | {reason} |".format(
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    issuer_type=row.get("issuer_type") or "",
                    classification=_join(row.get("classification_reasons")),
                    reason=row.get("reason") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Recommended Collection Fields",
            "",
            "- Full annual IFRS fields: revenue, ebitda, total_debt, cash, equity, net_profit, operating_cash_flow, interest_expense, net_debt",
            "- Optional fields: debt_to_ebitda, interest_coverage",
            "- Partial reports: collect fields listed in Already Covered / Partial.",
            "",
            "## Safety",
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
    report, exit_code = run_queue(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-collection-priority] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[financial-collection-priority] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    print(f"[financial-collection-priority] {report['status']}", flush=True)
    return exit_code


def _collect_target_company_ids(
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[int], dict[int, list[str]], dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    company_ids: list[int] = []
    source_presence: dict[int, list[str]] = {}
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
            if company_id is None:
                continue
            company_ids.append(company_id)
            _add_source_label(source_presence, company_id, source)

    company_ids = _dedupe_keep_order(company_ids)[: max(1, args.limit)]
    source_presence = {
        company_id: source_presence[company_id]
        for company_id in company_ids
        if company_id in source_presence
    }
    return company_ids, source_presence, {
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


def _next_steps(status: str) -> list[str]:
    if status == "failed":
        return ["Fix priority queue input or backend availability, then rerun."]
    return [
        "Review the priority queue; no import, score mutation, or paper action was executed.",
        "Collect the next issuer report from official sources only.",
    ]


def _add_source_label(
    source_presence: dict[int, list[str]],
    company_id: int,
    label: str,
) -> None:
    labels = source_presence.setdefault(int(company_id), [])
    if label not in labels:
        labels.append(label)


def _merge_source_presence(
    target: dict[int, list[str]],
    source: dict[int, list[str]],
) -> None:
    for company_id, labels in source.items():
        for label in labels:
            _add_source_label(target, company_id, label)


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


def _dedupe_keep_order(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _join(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, list):
        return ", ".join(str(item) for item in values)
    return str(values)


if __name__ == "__main__":
    sys.exit(main())
