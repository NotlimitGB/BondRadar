from __future__ import annotations

import argparse
import csv
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report


OUTPUT_FIELDS = [
    "group_key",
    "canonical_company_id",
    "canonical_company_name",
    "canonical_identity_status",
    "candidate_company_id",
    "candidate_company_name",
    "match_type",
    "match_score",
    "match_reasons",
    "sample_secids",
    "sample_bond_names",
    "recommended_action",
    "persisted_status",
    "review_status",
    "review_notes",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export non-destructive issuer duplicate review candidates.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--active-only", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--min-score", default="0.50")
    parser.add_argument("--include-accepted", action="store_true")
    parser.add_argument("--exclude-accepted", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--csv-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace, http_request: Any = None) -> dict[str, Any]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    params = urllib.parse.urlencode(
        {
            "active_only": str(bool(args.active_only)).lower(),
            "limit": max(0, int(args.limit)),
            "min_score": args.min_score,
            "include_bonds": "true",
        }
    )
    result = http_request(
        "GET",
        f"{backend}/api/companies/identity/duplicates/diagnostics?{params}",
        None,
    )
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    if isinstance(result, HttpResult):
        if result.ok and isinstance(result.data, dict):
            data = result.data
        else:
            errors.append(
                {
                    "message": "duplicate diagnostics request failed",
                    "details": result.error or result.text,
                }
            )
    elif isinstance(result, dict):
        data = result
    else:
        errors.append({"message": "duplicate diagnostics returned unexpected payload"})

    warnings.extend(data.get("warnings") or [])
    rows = _flatten_groups(data.get("groups") or [])
    if getattr(args, "exclude_accepted", False):
        rows = [
            row
            for row in rows
            if not (
                row.get("persisted_status") == "accepted"
                and row.get("review_status") in {"reviewed", "accepted"}
            )
        ]
    status = "failed" if errors else "warning" if warnings or rows else "passed"
    return {
        "status": status,
        "active_only": bool(args.active_only),
        "min_score": args.min_score,
        "candidate_group_count": data.get("candidate_group_count", 0),
        "candidate_pair_count": data.get("candidate_pair_count", len(rows)),
        "high_confidence_count": data.get("high_confidence_count", 0),
        "medium_confidence_count": data.get("medium_confidence_count", 0),
        "low_confidence_count": data.get("low_confidence_count", 0),
        "groups": data.get("groups") or [],
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
        "next_steps": [
            "Review duplicate candidates manually.",
            "Use issuer_identity_duplicate_review.py for preview before any confirmed apply.",
        ],
    }


def write_csv_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({field: _csv_value(row.get(field)) for field in OUTPUT_FIELDS})


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Issuer Duplicate Candidates",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Summary",
        "",
        f"- Candidate groups: {report.get('candidate_group_count', 0)}",
        f"- Candidate pairs: {report.get('candidate_pair_count', 0)}",
        f"- High confidence: {report.get('high_confidence_count', 0)}",
        f"- Medium confidence: {report.get('medium_confidence_count', 0)}",
        f"- Low confidence: {report.get('low_confidence_count', 0)}",
        "",
        "## Candidates",
        "",
        "| Canonical | Candidate | Score | Type | Reasons |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            "| {canonical} | {candidate} | {score} | {match_type} | {reasons} |".format(
                canonical=f"{row.get('canonical_company_id')} {row.get('canonical_company_name') or ''}",
                candidate=f"{row.get('candidate_company_id')} {row.get('candidate_company_name') or ''}",
                score=row.get("match_score") or "",
                match_type=row.get("match_type") or "",
                reasons=_csv_value(row.get("match_reasons")),
            )
        )
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report["next_steps"])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[issuer-identity-duplicates] wrote JSON report: {args.json_output}", flush=True)
    if args.csv_output is not None:
        write_csv_report(report, args.csv_output)
        print(f"[issuer-identity-duplicates] wrote CSV report: {args.csv_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            f"[issuer-identity-duplicates] wrote Markdown report: {args.markdown_output}",
            flush=True,
        )
    print(f"[issuer-identity-duplicates] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _flatten_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        for candidate in group.get("candidates") or []:
            rows.append(
                {
                    "group_key": group.get("group_key"),
                    "canonical_company_id": group.get("canonical_company_id"),
                    "canonical_company_name": group.get("canonical_company_name"),
                    "canonical_identity_status": group.get("canonical_identity_status"),
                    "candidate_company_id": candidate.get("company_id"),
                    "candidate_company_name": candidate.get("company_name"),
                    "match_type": candidate.get("match_type"),
                    "match_score": candidate.get("match_score"),
                    "match_reasons": candidate.get("match_reasons") or [],
                    "sample_secids": candidate.get("sample_secids") or [],
                    "sample_bond_names": candidate.get("sample_bond_names") or [],
                    "recommended_action": candidate.get("recommended_action") or "review",
                    "persisted_status": candidate.get("persisted_status"),
                    "review_status": candidate.get("review_status"),
                    "review_notes": "",
                }
            )
    return rows


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
