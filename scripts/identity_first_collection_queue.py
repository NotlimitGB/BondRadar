from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Sequence

import financial_collection_priority_queue as priority_script
from financial_report_import import http_json, write_json_report


TARGET_SOURCE_CHOICES = priority_script.TARGET_SOURCE_CHOICES

IDENTITY_REVIEW_FIELDS = [
    "rank",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "issuer_type",
    "classification_confidence",
    "identity_status",
    "identity_confidence",
    "review_status",
    "priority_score",
    "priority_level",
    "review_reasons",
    "recommended_identity_fields",
    "sample_bonds",
    "source_labels",
    "operator_next_action",
]

COLLECTION_READY_FIELDS = [
    "rank",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "issuer_type",
    "identity_status",
    "identity_confidence",
    "priority_score",
    "priority_level",
    "has_financial_report",
    "risk_scoring_readiness",
    "recommended_collection_type",
    "required_financial_fields",
    "optional_financial_fields",
    "sample_bonds",
    "source_labels",
    "operator_next_action",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a read-only identity-first financial collection queue.",
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
    parser.add_argument("--identity-review-csv-output", type=Path, default=None)
    parser.add_argument("--collection-ready-csv-output", type=Path, default=None)
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

    manual_ids = priority_script._parse_int_list(args.company_ids)
    for company_id in manual_ids:
        priority_script._add_source_label(source_presence, company_id, "manual-id")
    company_ids.extend(manual_ids)

    if args.company_names:
        resolved_ids, name_errors = priority_script._resolve_company_names(
            backend,
            args.company_names,
            http_request,
        )
        for company_id in resolved_ids:
            priority_script._add_source_label(source_presence, company_id, "company-name")
        company_ids.extend(resolved_ids)
        errors.extend(name_errors)

    if args.source in {"mixed", "target-issuers"}:
        target_ids, target_presence, target_source_report = (
            priority_script._collect_target_company_ids(args, http_request)
        )
        company_ids.extend(target_ids)
        priority_script._merge_source_presence(source_presence, target_presence)
        warnings.extend(target_source_report.get("warnings") or [])
        errors.extend(target_source_report.get("errors") or [])

    company_ids = priority_script._dedupe_keep_order(company_ids)
    if not company_ids and not errors:
        errors.append(
            {
                "message": (
                    "At least one company id, company name, or target issuer source "
                    "is required"
                )
            }
        )

    stats = priority_script._financial_report_stats(backend, http_request, warnings)
    identity_report: dict[str, Any] = _empty_report(errors=errors)
    if not errors:
        result = http_request(
            "POST",
            f"{backend}/api/financial-reports/identity-first-collection/batch",
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
                    "message": "identity-first collection queue request failed",
                    "status_code": result.status_code,
                    "details": result.error or result.text,
                }
            )
            identity_report = _empty_report(errors=errors)
        else:
            identity_report = result.data

    status = (
        "failed"
        if errors
        else "warning"
        if warnings
        else identity_report.get("status", "passed")
    )
    report = {
        **identity_report,
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
        "identity_apply_executed": False,
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


def write_identity_review_csv(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IDENTITY_REVIEW_FIELDS)
        writer.writeheader()
        for row in report.get("identity_review_required") or []:
            identity = row.get("identity") or {}
            writer.writerow(
                {
                    "rank": row.get("rank"),
                    "company_id": row.get("company_id"),
                    "company_name": row.get("company_name"),
                    "canonical_company_id": row.get("canonical_company_id"),
                    "canonical_company_name": row.get("canonical_company_name"),
                    "issuer_type": row.get("issuer_type"),
                    "classification_confidence": row.get("classification_confidence"),
                    "identity_status": row.get("identity_status"),
                    "identity_confidence": row.get("identity_confidence"),
                    "review_status": identity.get("review_status"),
                    "priority_score": row.get("priority_score"),
                    "priority_level": row.get("priority_level"),
                    "review_reasons": _join(row.get("review_reasons")),
                    "recommended_identity_fields": _join(
                        row.get("recommended_identity_fields")
                    ),
                    "sample_bonds": _sample_bonds(row),
                    "source_labels": _source_labels(row),
                    "operator_next_action": row.get("operator_next_action"),
                }
            )


def write_collection_ready_csv(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLLECTION_READY_FIELDS)
        writer.writeheader()
        for row in report.get("collection_ready") or []:
            collection = row.get("recommended_collection") or {}
            writer.writerow(
                {
                    "rank": row.get("rank"),
                    "company_id": row.get("company_id"),
                    "company_name": row.get("company_name"),
                    "canonical_company_id": row.get("canonical_company_id"),
                    "canonical_company_name": row.get("canonical_company_name"),
                    "issuer_type": row.get("issuer_type"),
                    "identity_status": row.get("identity_status"),
                    "identity_confidence": row.get("identity_confidence"),
                    "priority_score": row.get("priority_score"),
                    "priority_level": row.get("priority_level"),
                    "has_financial_report": row.get("has_financial_report"),
                    "risk_scoring_readiness": row.get("risk_scoring_readiness"),
                    "recommended_collection_type": collection.get("collection_type"),
                    "required_financial_fields": _join(collection.get("required_fields")),
                    "optional_financial_fields": _join(collection.get("optional_fields")),
                    "sample_bonds": _sample_bonds(row),
                    "source_labels": _source_labels(row),
                    "operator_next_action": row.get("operator_next_action"),
                }
            )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Identity-First Financial Collection Queue",
        "",
        "## Overall Status",
        "",
        f"- status: `{report.get('status')}`",
        f"- company_count: {report.get('company_count')}",
        f"- collection_ready_count: {report.get('collection_ready_count')}",
        f"- identity_review_required_count: {report.get('identity_review_required_count')}",
        f"- already_covered_count: {report.get('already_covered_count')}",
        f"- excluded_count: {report.get('excluded_count')}",
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
            "## Collection Ready",
            "",
            "| Rank | Company ID | Company | Type | Identity | Confidence | Score | Reasons | Action |",
            "| ---: | ---: | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    ready = report.get("collection_ready") or []
    if ready:
        for row in ready:
            lines.append(
                "| {rank} | {company_id} | {company_name} | {issuer_type} | {identity_status} | {confidence} | {score} | {reasons} | {action} |".format(
                    rank=row.get("rank"),
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    issuer_type=row.get("issuer_type") or "",
                    identity_status=row.get("identity_status") or "",
                    confidence=row.get("identity_confidence"),
                    score=row.get("priority_score"),
                    reasons=_join(row.get("priority_reasons")),
                    action=row.get("operator_next_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Identity Review Required",
            "",
            "| Rank | Company ID | Company | Type | Identity | Confidence | Reasons | Fields | Action |",
            "| ---: | ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    review_rows = report.get("identity_review_required") or []
    if review_rows:
        for row in review_rows:
            lines.append(
                "| {rank} | {company_id} | {company_name} | {issuer_type} | {identity_status} | {confidence} | {reasons} | {fields} | {action} |".format(
                    rank=row.get("rank"),
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    issuer_type=row.get("issuer_type") or "",
                    identity_status=row.get("identity_status") or "",
                    confidence=row.get("identity_confidence"),
                    reasons=_join(row.get("review_reasons")),
                    fields=_join(row.get("recommended_identity_fields")),
                    action=row.get("operator_next_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Already Covered / Partial",
            "",
            "| Company ID | Company | Readiness | Next Fields | Action |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    covered = report.get("already_covered") or []
    if covered:
        for row in covered:
            lines.append(
                "| {company_id} | {company_name} | {readiness} | {fields} | {action} |".format(
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    readiness=row.get("risk_scoring_readiness") or "",
                    fields=_join(row.get("recommended_next_fields")),
                    action=row.get("operator_next_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Excluded or Deprioritized",
            "",
            "| Company ID | Company | Type | Reason |",
            "| ---: | --- | --- | --- |",
        ]
    )
    excluded = report.get("excluded_or_deprioritized") or []
    if excluded:
        for row in excluded:
            lines.append(
                "| {company_id} | {company_name} | {issuer_type} | {reason} |".format(
                    company_id=row.get("company_id"),
                    company_name=row.get("company_name") or "",
                    issuer_type=row.get("issuer_type") or "",
                    reason=row.get("reason") or _join(row.get("classification_reasons")),
                )
            )
    else:
        lines.append("| None |  |  |  |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- identity_apply_executed: {report.get('identity_apply_executed')}",
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
        print(f"[identity-first-collection] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[identity-first-collection] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    if args.identity_review_csv_output is not None:
        write_identity_review_csv(report, args.identity_review_csv_output)
        print(
            "[identity-first-collection] wrote identity review CSV: "
            f"{args.identity_review_csv_output}",
            flush=True,
        )
    if args.collection_ready_csv_output is not None:
        write_collection_ready_csv(report, args.collection_ready_csv_output)
        print(
            "[identity-first-collection] wrote collection ready CSV: "
            f"{args.collection_ready_csv_output}",
            flush=True,
        )
    print(f"[identity-first-collection] {report['status']}", flush=True)
    return exit_code


def _empty_report(*, errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "failed" if errors else "passed",
        "company_count": 0,
        "collection_ready_count": 0,
        "identity_review_required_count": 0,
        "already_covered_count": 0,
        "excluded_count": 0,
        "summary": {},
        "collection_ready": [],
        "identity_review_required": [],
        "already_covered": [],
        "excluded_or_deprioritized": [],
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "identity_apply_executed": False,
        "paper_trading_called": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
    }


def _next_steps(status: str) -> list[str]:
    if status == "failed":
        return ["Fix identity-first queue input or backend availability, then rerun."]
    return [
        "Collect reports only for collection_ready issuers.",
        "Send identity_review_required rows through issuer identity review before financial collection.",
    ]


def _sample_bonds(row: dict[str, Any]) -> str:
    bonds = (row.get("bond_context") or {}).get("sample_bonds") or []
    values = []
    for bond in bonds:
        secid = bond.get("secid") or bond.get("isin") or bond.get("id")
        name = bond.get("name") or ""
        values.append(f"{secid}: {name}" if name else str(secid))
    return _join(values)


def _source_labels(row: dict[str, Any]) -> str:
    return _join((row.get("source_presence") or {}).get("source_labels"))


def _join(values: Any) -> str:
    return priority_script._join(values)


if __name__ == "__main__":
    sys.exit(main())
