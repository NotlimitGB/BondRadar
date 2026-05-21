from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import HttpResult, http_json, write_json_report


OUTPUT_FIELDS = [
    "company_id",
    "company_name",
    "company_ticker",
    "company_inn",
    "identity_status",
    "identity_confidence",
    "legal_name",
    "short_name",
    "ogrn",
    "issuer_group_name",
    "issuer_role",
    "needs_identity_review",
    "bonds_count",
    "sample_secids",
    "sample_bond_names",
    "source_reason",
    "has_financial_report",
    "latest_report_period_year",
    "latest_report_period_quarter",
    "latest_report_period_end_date",
    "coverage_status",
    "notes",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export priority issuer targets for financial report collection.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--source",
        choices=("paper-positions", "top-predictions", "bond-universe", "mixed"),
        default="mixed",
    )
    parser.add_argument("--portfolio-id", type=int, default=None)
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-secids", default="")
    parser.add_argument("--include-company-ids", default="")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--csv-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace, http_request: Any = None) -> dict[str, Any]:
    http_request = http_request or http_json
    backend = args.backend_url.rstrip("/")
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    targets: dict[int, dict[str, Any]] = {}

    sources = (
        ["paper-positions", "top-predictions", "bond-universe"]
        if args.source == "mixed"
        else [args.source]
    )
    for source in sources:
        try:
            source_targets, source_warnings = _collect_source(
                source,
                backend,
                args,
                http_request,
            )
            warnings.extend(source_warnings)
            _merge_targets(targets, source_targets)
        except Exception as exc:
            warnings.append({"message": f"{source} target collection failed: {exc}"})

    manual_company_ids = _parse_int_list(args.include_company_ids)
    if manual_company_ids:
        manual_targets, source_warnings = _manual_company_targets(
            manual_company_ids,
            backend,
            http_request,
        )
        warnings.extend(source_warnings)
        _merge_targets(targets, manual_targets)

    secids = _parse_str_list(args.include_secids)
    if secids:
        bond_targets, source_warnings = _manual_secid_targets(secids, backend, http_request)
        warnings.extend(source_warnings)
        _merge_targets(targets, bond_targets)

    rows = [_enrich_target(target, backend, http_request, warnings) for target in targets.values()]
    rows = sorted(
        rows,
        key=lambda item: (
            not item["has_financial_report"],
            item["bonds_count"],
            item["company_name"] or "",
        ),
        reverse=True,
    )[: max(1, args.limit)]
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "source": args.source,
        "total_targets": len(rows),
        "targets": rows,
        "warnings": warnings,
        "errors": errors,
        "next_steps": [
            "Fill the collection template from official issuer reports.",
            "Normalize the collection file before import dry-run.",
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
        "# BondRadar Financial Report Target Issuers",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Targets",
        "",
        "| Company | Ticker | Identity | Bonds | Coverage | Reason |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report["targets"]:
        lines.append(
            "| {name} | {ticker} | {identity} | {bonds} | {coverage} | {reason} |".format(
                name=row.get("company_name") or "",
                ticker=row.get("company_ticker") or "",
                identity=row.get("identity_status") or "",
                bonds=row.get("bonds_count") or 0,
                coverage=row.get("coverage_status") or "",
                reason=row.get("source_reason") or "",
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
        print(f"[financial-report-targets] wrote JSON report: {args.json_output}", flush=True)
    if args.csv_output is not None:
        write_csv_report(report, args.csv_output)
        print(f"[financial-report-targets] wrote CSV report: {args.csv_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[financial-report-targets] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[financial-report-targets] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _collect_source(
    source: str,
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if source == "paper-positions":
        return _paper_position_targets(backend, args, http_request)
    if source == "top-predictions":
        return _prediction_targets(backend, args, http_request)
    if source == "bond-universe":
        return _bond_universe_targets(backend, args, http_request)
    return [], [{"message": f"unsupported source {source}"}]


def _paper_position_targets(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    portfolio_id = args.portfolio_id
    if portfolio_id is None:
        portfolios = _get_json(http_request, f"{backend}/api/paper-trading/portfolios?limit=1")
        if not portfolios:
            return [], [{"message": "no paper portfolio found for paper-positions source"}]
        portfolio_id = int(portfolios[0]["id"])
    positions = _get_json(
        http_request,
        f"{backend}/api/paper-trading/portfolios/{portfolio_id}/positions",
    )
    targets: list[dict[str, Any]] = []
    for position in positions:
        if not position.get("is_active", True):
            continue
        company_id = position.get("company_id")
        if company_id is None:
            warnings.append({"message": f"position {position.get('id')} has no company_id"})
            continue
        bond = _safe_get_json(http_request, f"{backend}/api/bonds/{position.get('bond_id')}")
        targets.append(
            _base_target(
                company_id=company_id,
                reason="active paper position",
                bond=bond,
            )
        )
    return targets, warnings


def _prediction_targets(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    params: dict[str, Any] = {"limit": max(args.limit * 4, args.limit), "offset": 0}
    if args.model_run_id is not None:
        params["model_run_id"] = args.model_run_id
    if args.as_of_date:
        params["as_of_date_from"] = args.as_of_date
        params["as_of_date_to"] = args.as_of_date
    response = _get_json(http_request, f"{backend}/api/ml/predictions?{urllib.parse.urlencode(params)}")
    predictions = sorted(
        response.get("predictions", []),
        key=lambda item: _decimal_float(item.get("probability_positive")),
        reverse=True,
    )
    targets: list[dict[str, Any]] = []
    for prediction in predictions[: max(1, args.limit)]:
        bond = _safe_get_json(http_request, f"{backend}/api/bonds/{prediction.get('bond_id')}")
        targets.append(
            _base_target(
                company_id=prediction.get("company_id"),
                reason="top ML prediction",
                bond=bond,
            )
        )
    return targets, []


def _bond_universe_targets(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bonds = _fetch_bonds(backend, http_request, max_bonds=2000)
    grouped: dict[int, dict[str, Any]] = {}
    for bond in bonds:
        if not _is_corporate_bond(bond):
            continue
        company_id = bond.get("company_id")
        if company_id is None:
            continue
        target = grouped.setdefault(
            int(company_id),
            _base_target(company_id=company_id, reason="corporate bond universe", bond=None),
        )
        _add_bond_sample(target, bond)
    return sorted(grouped.values(), key=lambda item: item["bonds_count"], reverse=True), []


def _manual_company_targets(
    company_ids: list[int],
    backend: str,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets = [_base_target(company_id=company_id, reason="manual company include", bond=None) for company_id in company_ids]
    return targets, []


def _manual_secid_targets(
    secids: list[str],
    backend: str,
    http_request: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bonds = _fetch_bonds(backend, http_request, max_bonds=2000)
    wanted = {secid.upper() for secid in secids}
    targets = [
        _base_target(company_id=bond.get("company_id"), reason="manual secid include", bond=bond)
        for bond in bonds
        if str(bond.get("secid") or "").upper() in wanted and bond.get("company_id") is not None
    ]
    found = {str(target["sample_secids"][0]).upper() for target in targets if target["sample_secids"]}
    warnings = [
        {"message": f"secid was not found through paginated /api/bonds: {secid}"}
        for secid in wanted - found
    ]
    return targets, warnings


def _enrich_target(
    target: dict[str, Any],
    backend: str,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    company_id = target["company_id"]
    company = _safe_get_json(http_request, f"{backend}/api/companies/{company_id}")
    reports = _safe_get_json(
        http_request,
        f"{backend}/api/companies/{company_id}/reports?limit=1",
        default=[],
    )
    identity = _safe_get_json(
        http_request,
        f"{backend}/api/companies/identity/profiles/{company_id}",
        default=None,
    )
    if not isinstance(company, dict):
        warnings.append({"message": f"company {company_id} could not be resolved"})
        company = {}
    if not isinstance(identity, dict):
        identity = {}
    latest = reports[0] if isinstance(reports, list) and reports else {}
    has_report = bool(latest)
    identity_status = _identity_status(company, identity)
    needs_identity_review = identity_status in {"unknown", "weak", "conflict"}
    coverage_status = "has_report" if has_report else "missing_report"
    if needs_identity_review:
        coverage_status = "missing_identity"
    return {
        "company_id": company_id,
        "company_name": company.get("name"),
        "company_ticker": company.get("ticker"),
        "company_inn": company.get("inn"),
        "identity_status": identity_status,
        "identity_confidence": identity.get("identity_confidence"),
        "legal_name": identity.get("legal_name"),
        "short_name": identity.get("short_name"),
        "ogrn": identity.get("ogrn"),
        "issuer_group_name": identity.get("issuer_group_name"),
        "issuer_role": identity.get("issuer_role") or "unknown",
        "needs_identity_review": needs_identity_review,
        "bonds_count": target["bonds_count"],
        "sample_secids": target["sample_secids"][:5],
        "sample_bond_names": target["sample_bond_names"][:5],
        "source_reason": "; ".join(sorted(target["source_reasons"])),
        "has_financial_report": has_report,
        "latest_report_period_year": latest.get("period_year"),
        "latest_report_period_quarter": latest.get("period_quarter"),
        "latest_report_period_end_date": latest.get("period_end_date"),
        "coverage_status": coverage_status,
        "notes": "",
    }


def _merge_targets(targets: dict[int, dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        company_id = row.get("company_id")
        if company_id is None:
            continue
        existing = targets.setdefault(int(company_id), _base_target(company_id=company_id, reason="", bond=None))
        existing["bonds_count"] += row.get("bonds_count", 0)
        existing["source_reasons"].update(row.get("source_reasons", set()))
        for secid in row.get("sample_secids", []):
            if secid and secid not in existing["sample_secids"]:
                existing["sample_secids"].append(secid)
        for name in row.get("sample_bond_names", []):
            if name and name not in existing["sample_bond_names"]:
                existing["sample_bond_names"].append(name)


def _base_target(company_id: Any, reason: str, bond: dict[str, Any] | None) -> dict[str, Any]:
    target = {
        "company_id": int(company_id),
        "bonds_count": 0,
        "sample_secids": [],
        "sample_bond_names": [],
        "source_reasons": {reason} if reason else set(),
    }
    if bond:
        _add_bond_sample(target, bond)
    return target


def _add_bond_sample(target: dict[str, Any], bond: dict[str, Any]) -> None:
    target["bonds_count"] += 1
    secid = bond.get("secid")
    name = bond.get("name")
    if secid and secid not in target["sample_secids"]:
        target["sample_secids"].append(secid)
    if name and name not in target["sample_bond_names"]:
        target["sample_bond_names"].append(name)


def _fetch_bonds(backend: str, http_request: Any, *, max_bonds: int) -> list[dict[str, Any]]:
    bonds: list[dict[str, Any]] = []
    offset = 0
    page_size = 200
    while len(bonds) < max_bonds:
        page = _get_json(http_request, f"{backend}/api/bonds?skip={offset}&limit={page_size}")
        if not page:
            break
        bonds.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return bonds[:max_bonds]


def _get_json(http_request: Any, url: str) -> Any:
    result = http_request("GET", url, None)
    if not isinstance(result, HttpResult):
        return result
    if not result.ok:
        raise RuntimeError(result.error or f"request failed: {url}")
    return result.data


def _safe_get_json(http_request: Any, url: str, default: Any = None) -> Any:
    try:
        return _get_json(http_request, url)
    except Exception:
        return default


def _is_corporate_bond(bond: dict[str, Any]) -> bool:
    text = " ".join(
        str(bond.get(field) or "").upper()
        for field in ("name", "secid", "isin")
    )
    if "ОФЗ" in text or "OFZ" in text:
        return False
    if str(bond.get("secid") or "").upper().startswith("SU"):
        return False
    return True


def _parse_int_list(value: str) -> list[int]:
    result: list[int] = []
    for item in _parse_str_list(value):
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


def _parse_str_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _decimal_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _identity_status(company: dict[str, Any], identity: dict[str, Any]) -> str:
    status = identity.get("identity_status")
    if status:
        return str(status)
    name = str(company.get("name") or "")
    if name.startswith("Unknown issuer for "):
        return "unknown"
    if not company.get("inn"):
        return "weak"
    return "matched"


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())
