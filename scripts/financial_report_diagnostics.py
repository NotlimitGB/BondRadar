from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render read-only diagnostics for canonical financial reports.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--company-ids", default="")
    parser.add_argument("--company-names", default="")
    parser.add_argument(
        "--source",
        choices=("company-id-list", "mixed"),
        default="company-id-list",
    )
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_diagnostics(
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
        resolved_ids, name_warnings, name_errors = _resolve_company_names(
            backend,
            args.company_names,
            http_request,
        )
        company_ids.extend(resolved_ids)
        warnings.extend(name_warnings)
        errors.extend(name_errors)

    if args.source == "mixed":
        source_ids, source_warnings = _mixed_source_company_ids(
            backend,
            args,
            http_request,
        )
        company_ids.extend(source_ids)
        warnings.extend(source_warnings)

    company_ids = _unique_ints(company_ids)
    if not company_ids and not errors:
        errors.append({"message": "At least one company id or company name is required"})

    stats = _financial_report_stats(backend, http_request, warnings)
    companies: list[dict[str, Any]] = []
    if not errors:
        for company_id in company_ids[: max(1, args.limit)]:
            result = http_request(
                "GET",
                (
                    f"{backend}/api/financial-reports/diagnostics/company/{company_id}"
                    "?include_duplicate_context=true&include_derived_metrics=true"
                ),
            )
            if not result.ok or not isinstance(result.data, dict):
                errors.append(
                    {
                        "message": (
                            "financial report diagnostics request failed for "
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
        "paper_schedule_status": "not_checked",
        "read_only": True,
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
        "# Financial Report Diagnostics",
        "",
        "## Overall Status",
        "",
        f"`{report.get('status')}`",
        "",
    ]
    for item in report.get("companies") or []:
        latest = item.get("latest_report") or {}
        raw = item.get("raw_fields") or {}
        derived = item.get("derived_metrics") or {}
        explanation = item.get("signal_explanation") or {}
        lines.extend(
            [
                "## Company",
                "",
                f"- company_id: {item.get('company_id')}",
                f"- company_name: {item.get('company_name')}",
                f"- canonical_company_id: {item.get('canonical_company_id')}",
                f"- canonical_company_name: {item.get('canonical_company_name')}",
                f"- is_duplicate_candidate: {item.get('is_duplicate_candidate')}",
                "",
                "## Latest Report",
                "",
                f"- has_financial_report: {item.get('has_financial_report')}",
                f"- report_id: {latest.get('id')}",
                f"- period: {latest.get('period_year')} Q{latest.get('period_quarter')}",
                f"- period_end_date: {latest.get('period_end_date')}",
                f"- source: {latest.get('source')}",
                f"- currency: {latest.get('currency')}",
                f"- signal: {latest.get('signal')}",
                "",
                "## Raw Field Coverage",
                "",
                f"- present: {_join(raw.get('present'))}",
                f"- missing: {_join(raw.get('missing'))}",
                "",
                "## Derived Metrics",
                "",
                "Computed:",
                "",
            ]
        )
        computed = derived.get("computed") or {}
        if computed:
            for key, value in computed.items():
                if isinstance(value, dict):
                    lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
                else:
                    lines.append(f"- {key}: {value}")
        else:
            lines.append("- None")
        lines.extend(["", "Fallback:", ""])
        fallback = derived.get("fallback") or {}
        if fallback:
            lines.extend(f"- {key}: {value}" for key, value in fallback.items())
        else:
            lines.append("- None")
        lines.extend(["", "Missing:", ""])
        missing_metrics = derived.get("missing") or []
        if missing_metrics:
            lines.extend(
                f"- {entry.get('metric')}: {entry.get('reason')}"
                for entry in missing_metrics
            )
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "## Signal Explanation",
                "",
                f"- signal: {explanation.get('signal')}",
                f"- severity: {explanation.get('severity')}",
                f"- reasons: {_join(explanation.get('reasons'))}",
                f"- critical: {_join(explanation.get('critical'))}",
                f"- warnings: {_join(explanation.get('warnings'))}",
                "",
                "## Recommended Next Collection",
                "",
                f"- fields: {_join(item.get('recommended_next_fields'))}",
                "",
                "## Coverage vs Scoring Readiness",
                "",
                "- covered_by_canonical means collection coverage can be satisfied by a canonical report.",
                "- insufficient_data means the report still lacks fields for stronger scoring readiness.",
                f"- safe_for_feature_pipeline: {item.get('safe_for_feature_pipeline')}",
                f"- safe_for_risk_scoring: {item.get('safe_for_risk_scoring')}",
                f"- risk_scoring_readiness: {item.get('risk_scoring_readiness')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Safety",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- financial_reports_count: {report.get('financial_reports_count')}",
            f"- paper_schedule_status: {report.get('paper_schedule_status')}",
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
    report, exit_code = run_diagnostics(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-report-diagnostics] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            f"[financial-report-diagnostics] wrote Markdown report: {args.markdown_output}",
            flush=True,
        )
    print(f"[financial-report-diagnostics] {report['status']}", flush=True)
    return exit_code


def _resolve_company_names(
    backend: str,
    raw_names: str,
    http_request: Any,
) -> tuple[list[int], list[dict[str, Any]], list[dict[str, Any]]]:
    ids: list[int] = []
    warnings: list[dict[str, Any]] = []
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
    return ids, warnings, errors


def _mixed_source_company_ids(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[int], list[dict[str, Any]]]:
    ids: list[int] = []
    warnings: list[dict[str, Any]] = []
    predictions_url = f"{backend}/api/ml/predictions?limit={max(1, args.limit)}"
    if args.model_run_id is not None:
        predictions_url += f"&model_run_id={args.model_run_id}"
    if args.as_of_date:
        predictions_url += f"&as_of_date={urllib.parse.quote(str(args.as_of_date))}"
    predictions = http_request("GET", predictions_url)
    if predictions.ok:
        data = predictions.data or {}
        rows = data.get("predictions") if isinstance(data, dict) else data
        if isinstance(rows, list):
            ids.extend(_company_ids_from_rows(rows))
    else:
        warnings.append({"message": "top-predictions source was unavailable"})

    bonds = http_request("GET", f"{backend}/api/bonds?skip=0&limit=200")
    if bonds.ok and isinstance(bonds.data, list):
        ids.extend(_company_ids_from_rows(bonds.data))
    else:
        warnings.append({"message": "bond-universe source was unavailable"})
    return _unique_ints(ids), warnings


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


def _company_ids_from_rows(rows: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for row in rows:
        value = (
            row.get("company_id")
            or row.get("issuer_company_id")
            or row.get("predicted_company_id")
        )
        parsed = _parse_int(value)
        if parsed is not None:
            ids.append(parsed)
    return ids


def _next_steps(status: str, companies: list[dict[str, Any]]) -> list[str]:
    if status == "failed":
        return ["Fix diagnostics input or backend availability, then rerun."]
    missing_fields = sorted(
        {
            field
            for company in companies
            for field in (company.get("recommended_next_fields") or [])
        }
    )
    if missing_fields:
        return [
            "Collect missing fields from official issuer reports only: "
            + ", ".join(missing_fields),
            "Do not run paper trading from diagnostics output.",
        ]
    return ["Review diagnostics; no immediate financial-field collection gaps were found."]


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


def _unique_ints(values: list[int]) -> list[int]:
    return sorted(set(values))


def _join(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, list):
        return ", ".join(str(item) for item in values)
    return str(values)


if __name__ == "__main__":
    sys.exit(main())
