from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report
from financial_report_target_issuers import build_report as build_financial_targets
from financial_report_target_issuers import parse_args as parse_financial_target_args


OUTPUT_FIELDS = [
    "company_id",
    "company_name",
    "ticker",
    "inn",
    "bonds_count",
    "sample_secids",
    "sample_bond_names",
    "reason",
    "identity_status",
    "identity_confidence",
    "suggested_search_query",
    "notes",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export issuer identity cleanup targets without mutating data.",
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
        default="unknown-companies",
    )
    parser.add_argument("--portfolio-id", type=int, default=None)
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--csv-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace, http_request: Any = None) -> dict[str, Any]:
    http_request = http_request or http_json
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    targets: dict[int, dict[str, Any]] = {}
    backend = args.backend_url.rstrip("/")
    sources = (
        ["unknown-companies", "paper-positions", "top-predictions", "bond-universe"]
        if args.source == "mixed"
        else [args.source]
    )
    for source in sources:
        try:
            rows = (
                _unknown_company_targets(backend, args, http_request, warnings)
                if source == "unknown-companies"
                else _financial_target_rows(source, args, http_request)
            )
            for row in rows:
                company_id = row.get("company_id")
                if company_id is None:
                    continue
                target = targets.setdefault(int(company_id), _empty_target(row))
                _merge_target(target, row)
        except Exception as exc:
            warnings.append({"message": f"{source} identity target collection failed: {exc}"})

    rows = sorted(
        targets.values(),
        key=lambda item: (
            item.get("identity_status") not in {"unknown", "weak", "conflict"},
            -int(item.get("bonds_count") or 0),
            item.get("company_name") or "",
        ),
    )[: max(1, args.limit)]
    for row in rows:
        row.pop("_reasons", None)
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "source": args.source,
        "total_targets": len(rows),
        "targets": rows,
        "warnings": warnings,
        "errors": errors,
        "next_steps": [
            "Review issuer identity evidence before collecting financial reports.",
            "Use issuer_identity_import.py for preview and confirmed apply.",
        ],
    }


def write_csv_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in report["targets"]:
            writer.writerow({field: _csv_value(row.get(field)) for field in OUTPUT_FIELDS})


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Issuer Identity Targets",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Targets",
        "",
        "| Company | Identity | Bonds | Reason | Search query |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in report["targets"]:
        lines.append(
            "| {company} | {status} | {bonds} | {reason} | {query} |".format(
                company=row.get("company_name") or "",
                status=row.get("identity_status") or "",
                bonds=row.get("bonds_count") or 0,
                reason=row.get("reason") or "",
                query=row.get("suggested_search_query") or "",
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
        print(f"[issuer-identity-targets] wrote JSON report: {args.json_output}", flush=True)
    if args.csv_output is not None:
        write_csv_report(report, args.csv_output)
        print(f"[issuer-identity-targets] wrote CSV report: {args.csv_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[issuer-identity-targets] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[issuer-identity-targets] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _unknown_company_targets(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = http_request(
        "GET",
        f"{backend}/api/companies/identity/diagnostics?active_only=true&limit={max(1, args.limit)}",
        None,
    )
    data = _data_or_raise(result)
    rows = []
    for item in data.get("top_unknown_issuers", []):
        rows.append(
            {
                "company_id": item.get("company_id"),
                "company_name": item.get("company_name"),
                "ticker": item.get("ticker"),
                "inn": item.get("inn"),
                "bonds_count": item.get("bonds_count") or 0,
                "sample_secids": item.get("sample_secids") or [],
                "sample_bond_names": item.get("sample_bond_names") or [],
                "reason": "unknown issuer identity",
                "identity_status": item.get("identity_status") or "unknown",
                "identity_confidence": item.get("identity_confidence"),
                "suggested_search_query": _suggested_query(item),
                "notes": "",
            }
        )
    warnings.extend(data.get("warnings") or [])
    return rows


def _financial_target_rows(
    source: str,
    args: argparse.Namespace,
    http_request: Any,
) -> list[dict[str, Any]]:
    financial_args = parse_financial_target_args(
        [
            "--backend-url",
            args.backend_url,
            "--source",
            source,
            "--limit",
            str(args.limit),
            *(["--portfolio-id", str(args.portfolio_id)] if args.portfolio_id else []),
            *(["--model-run-id", str(args.model_run_id)] if args.model_run_id else []),
            *(["--as-of-date", args.as_of_date] if args.as_of_date else []),
        ]
    )
    report = build_financial_targets(financial_args, http_request=http_request)
    rows = []
    for item in report.get("targets", []):
        rows.append(
            {
                "company_id": item.get("company_id"),
                "company_name": item.get("company_name"),
                "ticker": item.get("company_ticker"),
                "inn": item.get("company_inn"),
                "bonds_count": item.get("bonds_count") or 0,
                "sample_secids": item.get("sample_secids") or [],
                "sample_bond_names": item.get("sample_bond_names") or [],
                "reason": item.get("source_reason") or source,
                "identity_status": item.get("identity_status") or "unknown",
                "identity_confidence": item.get("identity_confidence"),
                "suggested_search_query": _suggested_query(item),
                "notes": "needs review" if item.get("needs_identity_review") else "",
            }
        )
    return rows


def _empty_target(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": int(row["company_id"]),
        "company_name": row.get("company_name"),
        "ticker": row.get("ticker"),
        "inn": row.get("inn"),
        "bonds_count": 0,
        "sample_secids": [],
        "sample_bond_names": [],
        "reason": "",
        "identity_status": row.get("identity_status") or "unknown",
        "identity_confidence": row.get("identity_confidence"),
        "suggested_search_query": "",
        "notes": "",
        "_reasons": set(),
    }


def _merge_target(target: dict[str, Any], row: dict[str, Any]) -> None:
    target["company_name"] = target.get("company_name") or row.get("company_name")
    target["ticker"] = target.get("ticker") or row.get("ticker")
    target["inn"] = target.get("inn") or row.get("inn")
    target["bonds_count"] += int(row.get("bonds_count") or 0)
    target["identity_status"] = row.get("identity_status") or target["identity_status"]
    target["identity_confidence"] = row.get("identity_confidence") or target.get("identity_confidence")
    target["_reasons"].add(row.get("reason") or "identity review")
    target["reason"] = "; ".join(sorted(target["_reasons"]))
    for field in ("sample_secids", "sample_bond_names"):
        for value in row.get(field, []):
            if value and value not in target[field]:
                target[field].append(value)
    target["suggested_search_query"] = target.get("suggested_search_query") or _suggested_query(target)
    target["notes"] = target.get("notes") or row.get("notes") or ""


def _suggested_query(item: dict[str, Any]) -> str:
    secid = (item.get("sample_secids") or [""])[0]
    bond_name = (item.get("sample_bond_names") or [""])[0]
    if bond_name:
        return f'"{bond_name}" issuer INN'
    if secid:
        return f'"{secid}" issuer'
    return f'"{item.get("company_name") or ""}" issuer INN'


def _data_or_raise(result: Any) -> Any:
    if isinstance(result, HttpResult):
        if not result.ok:
            raise RuntimeError(result.error or result.text or "request failed")
        return result.data
    return result


def _csv_value(value: Any) -> str:
    if isinstance(value, set):
        value = sorted(value)
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
