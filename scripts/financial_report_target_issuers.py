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
    "canonical_company_id",
    "canonical_company_name",
    "is_canonical_target",
    "is_duplicate_candidate",
    "duplicate_mapping_status",
    "duplicate_review_status",
    "duplicate_match_score",
    "duplicate_match_type",
    "duplicate_company_ids",
    "duplicate_company_names",
    "duplicate_sample_secids",
    "duplicate_sample_bond_names",
    "duplicate_count",
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
    "canonical_has_financial_report",
    "duplicate_has_financial_report",
    "coverage_effective_status",
    "needs_financial_report",
    "latest_report_period_year",
    "latest_report_period_quarter",
    "latest_report_period_end_date",
    "coverage_status",
    "possible_canonical_company_id",
    "possible_canonical_company_name",
    "possible_duplicate_match_score",
    "possible_duplicate_reasons",
    "needs_duplicate_review",
    "notes",
]
COLLECTION_TEMPLATE_FIELDS = [
    "canonical_company_id",
    "canonical_company_name",
    "legal_name",
    "short_name",
    "inn",
    "ogrn",
    "issuer_group_name",
    "issuer_role",
    "duplicate_company_ids",
    "sample_secids",
    "sample_bond_names",
    "coverage_effective_status",
    "latest_report_period_year",
    "latest_report_period_quarter",
    "latest_report_period_end_date",
    "needs_financial_report",
    "priority_reason",
    "operator_notes",
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
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--rollup-duplicates", action="store_true")
    parser.add_argument("--include-duplicate-members", action="store_true")
    parser.add_argument("--compare-rollup", action="store_true")
    parser.add_argument("--collection-template-output", type=Path, default=None)
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

    raw_target_count = len(targets)
    canonical_context = _canonical_context(backend, args, http_request, warnings)
    if _flag(args, "use_duplicate_mapping") or _flag(args, "rollup_duplicates"):
        _attach_canonical_resolution(targets, canonical_context)
    if _flag(args, "rollup_duplicates"):
        targets = _rollup_targets(
            targets,
            canonical_context,
            include_duplicate_members=_flag(args, "include_duplicate_members"),
        )

    rows = [_enrich_target(target, backend, http_request, warnings) for target in targets.values()]
    duplicate_hints = _duplicate_hints(backend, args, http_request, warnings)
    for row in rows:
        _attach_duplicate_hint(row, duplicate_hints.get(row["company_id"]))
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
        "rollup_comparison": _rollup_comparison(raw_target_count, rows)
        if _flag(args, "compare_rollup")
        else None,
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


def write_collection_template(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLLECTION_TEMPLATE_FIELDS)
        writer.writeheader()
        for row in report["targets"]:
            writer.writerow(
                {
                    "canonical_company_id": row.get("canonical_company_id") or row.get("company_id"),
                    "canonical_company_name": row.get("canonical_company_name") or row.get("company_name"),
                    "legal_name": row.get("legal_name"),
                    "short_name": row.get("short_name"),
                    "inn": row.get("company_inn"),
                    "ogrn": row.get("ogrn"),
                    "issuer_group_name": row.get("issuer_group_name"),
                    "issuer_role": row.get("issuer_role"),
                    "duplicate_company_ids": _csv_value(row.get("duplicate_company_ids")),
                    "sample_secids": _csv_value(row.get("sample_secids")),
                    "sample_bond_names": _csv_value(row.get("sample_bond_names")),
                    "coverage_effective_status": row.get("coverage_effective_status"),
                    "latest_report_period_year": row.get("latest_report_period_year"),
                    "latest_report_period_quarter": row.get("latest_report_period_quarter"),
                    "latest_report_period_end_date": row.get("latest_report_period_end_date"),
                    "needs_financial_report": _csv_value(row.get("needs_financial_report")),
                    "priority_reason": row.get("source_reason"),
                    "operator_notes": "",
                }
            )


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
        "| Company | Canonical | Identity | Bonds | Duplicates | Coverage | Reason |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in report["targets"]:
        lines.append(
            "| {name} | {canonical} | {identity} | {bonds} | {duplicates} | {coverage} | {reason} |".format(
                name=row.get("company_name") or "",
                canonical=row.get("canonical_company_name") or "",
                identity=row.get("identity_status") or "",
                bonds=row.get("bonds_count") or 0,
                duplicates=row.get("duplicate_count") or 0,
                coverage=row.get("coverage_effective_status") or row.get("coverage_status") or "",
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
    if args.collection_template_output is not None:
        write_collection_template(report, args.collection_template_output)
        print(
            "[financial-report-targets] wrote collection template: "
            f"{args.collection_template_output}",
            flush=True,
        )
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
    duplicate_reports = _duplicate_reports(target, backend, http_request)
    duplicate_has_report = bool(duplicate_reports)
    identity_status = _identity_status(company, identity)
    needs_identity_review = identity_status in {"unknown", "weak", "conflict"}
    coverage_status = "has_report" if has_report else "missing_report"
    if needs_identity_review:
        coverage_status = "missing_identity"
    coverage_effective_status = (
        "covered_by_canonical"
        if has_report
        else "covered_by_duplicate_warning"
        if duplicate_has_report
        else "missing_report"
    )
    if duplicate_has_report and not has_report:
        warnings.append(
            {
                "code": "financial_report_attached_to_duplicate_candidate",
                "message": (
                    f"Canonical company {company_id} has no financial report, "
                    "but a duplicate candidate does."
                ),
                "company_id": company_id,
            }
        )
    return {
        "company_id": company_id,
        "company_name": company.get("name"),
        "canonical_company_id": target.get("canonical_company_id") or company_id,
        "canonical_company_name": target.get("canonical_company_name") or company.get("name"),
        "is_canonical_target": target.get("is_canonical_target", True),
        "is_duplicate_candidate": target.get("is_duplicate_candidate", False),
        "duplicate_mapping_status": target.get("duplicate_mapping_status"),
        "duplicate_review_status": target.get("duplicate_review_status"),
        "duplicate_match_score": target.get("duplicate_match_score"),
        "duplicate_match_type": target.get("duplicate_match_type"),
        "duplicate_company_ids": target.get("duplicate_company_ids") or [],
        "duplicate_company_names": target.get("duplicate_company_names") or [],
        "duplicate_sample_secids": target.get("duplicate_sample_secids") or [],
        "duplicate_sample_bond_names": target.get("duplicate_sample_bond_names") or [],
        "duplicate_count": target.get("duplicate_count") or 0,
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
        "canonical_has_financial_report": has_report,
        "duplicate_has_financial_report": duplicate_has_report,
        "coverage_effective_status": coverage_effective_status,
        "needs_financial_report": not has_report,
        "latest_report_period_year": latest.get("period_year"),
        "latest_report_period_quarter": latest.get("period_quarter"),
        "latest_report_period_end_date": latest.get("period_end_date"),
        "coverage_status": coverage_status,
        "possible_canonical_company_id": None,
        "possible_canonical_company_name": None,
        "possible_duplicate_match_score": None,
        "possible_duplicate_reasons": [],
        "needs_duplicate_review": False,
        "notes": "",
    }


def _duplicate_reports(
    target: dict[str, Any],
    backend: str,
    http_request: Any,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for company_id in target.get("duplicate_company_ids") or []:
        rows = _safe_get_json(
            http_request,
            f"{backend}/api/companies/{company_id}/reports?limit=1",
            default=[],
        )
        if isinstance(rows, list) and rows:
            reports.append({"company_id": company_id, "report": rows[0]})
    return reports


def _duplicate_hints(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    data = _safe_get_json(
        http_request,
        (
            f"{backend}/api/companies/identity/duplicates/diagnostics"
            f"?active_only=true&limit={max(50, args.limit * 4)}&min_score=0.50&include_bonds=true"
        ),
        default=None,
    )
    if not isinstance(data, dict):
        return {}
    hints: dict[int, dict[str, Any]] = {}
    for group in data.get("groups") or []:
        for candidate in group.get("candidates") or []:
            company_id = candidate.get("company_id")
            if company_id is None:
                continue
            current = hints.get(int(company_id))
            score = _decimal_float(candidate.get("match_score"))
            if current is not None and score <= _decimal_float(current.get("possible_duplicate_match_score")):
                continue
            hints[int(company_id)] = {
                "possible_canonical_company_id": group.get("canonical_company_id"),
                "possible_canonical_company_name": group.get("canonical_company_name"),
                "possible_duplicate_match_score": candidate.get("match_score"),
                "possible_duplicate_reasons": candidate.get("match_reasons") or [],
            }
    warnings.extend(data.get("warnings") or [])
    return hints


def _attach_duplicate_hint(row: dict[str, Any], hint: dict[str, Any] | None) -> None:
    if not hint:
        return
    row.update(hint)
    row["needs_duplicate_review"] = True
    row["coverage_status"] = "duplicate_review"
    note = "possible same-issuer duplicate requires review"
    row["notes"] = note if not row.get("notes") else f"{row['notes']}; {note}"


def _canonical_context(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    if not (
        _flag(args, "use_duplicate_mapping")
        or _flag(args, "rollup_duplicates")
        or _flag(args, "compare_rollup")
    ):
        return {"by_candidate": {}, "groups_by_canonical": {}}
    data = _safe_get_json(
        http_request,
        f"{backend}/api/companies/identity/canonical-groups?active_only=true",
        default=None,
    )
    if not isinstance(data, dict):
        return {"by_candidate": {}, "groups_by_canonical": {}}
    by_candidate: dict[int, dict[str, Any]] = {}
    groups_by_canonical: dict[int, dict[str, Any]] = {}
    for group in data.get("groups") or []:
        canonical_id = group.get("canonical_company_id")
        if canonical_id is None:
            continue
        groups_by_canonical[int(canonical_id)] = group
        for member in group.get("duplicate_members") or []:
            member_id = member.get("company_id")
            if member_id is None:
                continue
            by_candidate[int(member_id)] = {"group": group, "member": member}
    warnings.extend(data.get("warnings") or [])
    return {"by_candidate": by_candidate, "groups_by_canonical": groups_by_canonical}


def _attach_canonical_resolution(
    targets: dict[int, dict[str, Any]],
    context: dict[str, Any],
) -> None:
    by_candidate = context.get("by_candidate") or {}
    for company_id, target in targets.items():
        _ensure_duplicate_fields(target)
        mapping = by_candidate.get(company_id)
        if not mapping:
            target["canonical_company_id"] = target["company_id"]
            target["canonical_company_name"] = None
            target["is_canonical_target"] = True
            target["is_duplicate_candidate"] = False
            continue
        group = mapping["group"]
        member = mapping["member"]
        target["canonical_company_id"] = group.get("canonical_company_id")
        target["canonical_company_name"] = group.get("canonical_company_name")
        target["is_canonical_target"] = False
        target["is_duplicate_candidate"] = True
        target["duplicate_mapping_status"] = member.get("duplicate_mapping_status")
        target["duplicate_review_status"] = member.get("duplicate_review_status")
        target["duplicate_match_score"] = member.get("duplicate_match_score")
        target["duplicate_match_type"] = member.get("duplicate_match_type")


def _ensure_duplicate_fields(target: dict[str, Any]) -> None:
    target.setdefault("canonical_company_id", target.get("company_id"))
    target.setdefault("canonical_company_name", None)
    target.setdefault("is_canonical_target", True)
    target.setdefault("is_duplicate_candidate", False)
    target.setdefault("duplicate_mapping_status", None)
    target.setdefault("duplicate_review_status", None)
    target.setdefault("duplicate_match_score", None)
    target.setdefault("duplicate_match_type", None)
    target.setdefault("duplicate_company_ids", [])
    target.setdefault("duplicate_company_names", [])
    target.setdefault("duplicate_sample_secids", [])
    target.setdefault("duplicate_sample_bond_names", [])
    target.setdefault("duplicate_count", 0)


def _rollup_targets(
    targets: dict[int, dict[str, Any]],
    context: dict[str, Any],
    *,
    include_duplicate_members: bool,
) -> dict[int, dict[str, Any]]:
    by_candidate = context.get("by_candidate") or {}
    groups_by_canonical = context.get("groups_by_canonical") or {}
    rolled: dict[int, dict[str, Any]] = {}
    for company_id, target in targets.items():
        mapping = by_candidate.get(company_id)
        canonical_id = (
            int(mapping["group"]["canonical_company_id"])
            if mapping
            else int(target["company_id"])
        )
        group = groups_by_canonical.get(canonical_id)
        if canonical_id not in rolled:
            rolled[canonical_id] = _canonical_base_target(target, group) if mapping else dict(target)
        if mapping:
            _merge_single_target(rolled[canonical_id], target)
            _add_duplicate_member_samples(rolled[canonical_id], target)
    for canonical_id, row in rolled.items():
        group = groups_by_canonical.get(canonical_id)
        if group:
            _attach_group_members(
                row,
                group,
                include_duplicate_members=include_duplicate_members,
            )
        row["is_canonical_target"] = True
        row["is_duplicate_candidate"] = False
    return rolled


def _canonical_base_target(
    source_target: dict[str, Any],
    group: dict[str, Any] | None,
) -> dict[str, Any]:
    canonical_id = int(group.get("canonical_company_id") if group else source_target["company_id"])
    target = _base_target(
        company_id=canonical_id,
        reason="",
        bond=None,
    )
    target.update(
        {
            "canonical_company_id": canonical_id,
            "canonical_company_name": None if group is None else group.get("canonical_company_name"),
            "is_canonical_target": True,
            "is_duplicate_candidate": False,
            "duplicate_company_ids": [],
            "duplicate_company_names": [],
            "duplicate_sample_secids": [],
            "duplicate_sample_bond_names": [],
            "duplicate_count": 0,
        }
    )
    return target


def _merge_single_target(target: dict[str, Any], row: dict[str, Any]) -> None:
    target["bonds_count"] += row.get("bonds_count", 0)
    target["source_reasons"].update(row.get("source_reasons", set()))
    for secid in row.get("sample_secids", []):
        if secid and secid not in target["sample_secids"]:
            target["sample_secids"].append(secid)
    for name in row.get("sample_bond_names", []):
        if name and name not in target["sample_bond_names"]:
            target["sample_bond_names"].append(name)


def _add_duplicate_member_samples(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field, duplicate_field in (
        ("sample_secids", "duplicate_sample_secids"),
        ("sample_bond_names", "duplicate_sample_bond_names"),
    ):
        for value in source.get(field) or []:
            if value and value not in target[duplicate_field]:
                target[duplicate_field].append(value)


def _attach_group_members(
    target: dict[str, Any],
    group: dict[str, Any],
    *,
    include_duplicate_members: bool,
) -> None:
    members = group.get("duplicate_members") or []
    target["duplicate_count"] = len(members)
    target["duplicate_company_ids"] = [member.get("company_id") for member in members]
    if include_duplicate_members:
        target["duplicate_company_names"] = [member.get("company_name") for member in members]


def _rollup_comparison(raw_count: int, rows: list[dict[str, Any]]) -> dict[str, int]:
    canonical_count = len(rows)
    duplicate_member_count = sum(int(row.get("duplicate_count") or 0) for row in rows)
    return {
        "raw_target_count": raw_count,
        "canonical_target_count": canonical_count,
        "deduplicated_count": max(0, raw_count - canonical_count),
        "duplicate_member_count": duplicate_member_count,
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


def _flag(args: argparse.Namespace, name: str) -> bool:
    return bool(getattr(args, name, False))


if __name__ == "__main__":
    sys.exit(main())
