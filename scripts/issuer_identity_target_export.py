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
    "ticker",
    "inn",
    "bonds_count",
    "sample_secids",
    "sample_bond_names",
    "reason",
    "identity_status",
    "identity_confidence",
    "legal_name",
    "short_name",
    "ogrn",
    "issuer_group_name",
    "issuer_role",
    "possible_canonical_company_id",
    "possible_canonical_company_name",
    "possible_duplicate_match_score",
    "possible_duplicate_reasons",
    "needs_duplicate_review",
    "suggested_search_query",
    "notes",
]
REASON_PRIORITY = {
    "active paper position": 1,
    "top ML prediction": 2,
    "corporate bond universe": 3,
    "recent bond universe": 4,
    "unknown issuer identity": 5,
    "missing inn": 6,
}


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
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--rollup-duplicates", action="store_true")
    parser.add_argument("--include-duplicate-members", action="store_true")
    parser.add_argument("--compare-rollup", action="store_true")
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
        ["paper-positions", "top-predictions", "bond-universe", "unknown-companies"]
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

    raw_target_count = len(targets)
    canonical_context = _canonical_context(backend, args, http_request, warnings)
    duplicate_hints = _duplicate_hints(backend, args, http_request, warnings)
    for target in targets.values():
        _attach_duplicate_hint(target, duplicate_hints.get(target["company_id"]))

    if _flag(args, "use_duplicate_mapping") or _flag(args, "rollup_duplicates"):
        _attach_canonical_resolution(targets, canonical_context)
    if _flag(args, "rollup_duplicates"):
        targets = _rollup_targets(
            targets,
            canonical_context,
            include_duplicate_members=_flag(args, "include_duplicate_members"),
        )
        _enrich_canonical_identity_rows(targets, backend, http_request)

    rows = sorted(
        targets.values(),
        key=lambda item: (
            int(item.get("_priority") or 99),
            item.get("identity_status") not in {"unknown", "weak", "conflict"},
            -int(item.get("bonds_count") or 0),
            item.get("company_name") or "",
        ),
    )[: max(1, args.limit)]
    for row in rows:
        row.pop("_reasons", None)
        row.pop("_priority", None)
    status = "failed" if errors else "warning" if warnings else "passed"
    rollup_comparison = _rollup_comparison(raw_target_count, rows)
    rollup_summary = (
        _rollup_summary(raw_target_count, rows, canonical_context)
        if _flag(args, "use_duplicate_mapping") or _flag(args, "rollup_duplicates")
        else None
    )
    return {
        "status": status,
        "source": args.source,
        "total_targets": len(rows),
        "rollup_summary": rollup_summary,
        "rollup_comparison": rollup_comparison if _flag(args, "compare_rollup") else None,
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
        "| Company | Canonical | Identity | Bonds | Duplicates | Reason | Search query |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in report["targets"]:
        lines.append(
            "| {company} | {canonical} | {status} | {bonds} | {duplicates} | {reason} | {query} |".format(
                company=row.get("company_name") or "",
                canonical=row.get("canonical_company_name") or "",
                status=row.get("identity_status") or "",
                bonds=row.get("bonds_count") or 0,
                duplicates=row.get("duplicate_count") or 0,
                reason=row.get("reason") or "",
                query=row.get("suggested_search_query") or "",
            )
        )
    rollup_summary = report.get("rollup_summary")
    if isinstance(rollup_summary, dict):
        lines.extend(
            [
                "",
                "## Duplicate Rollup Summary",
                "",
                f"- Raw target count: {rollup_summary.get('raw_target_count', 0)}",
                f"- Canonical target count: {rollup_summary.get('canonical_target_count', 0)}",
                f"- Deduplicated count: {rollup_summary.get('deduplicated_count', 0)}",
                f"- Duplicate member count: {rollup_summary.get('duplicate_member_count', 0)}",
                f"- Canonical groups count: {rollup_summary.get('canonical_groups_count', 0)}",
                (
                    "- Targets with duplicates: "
                    f"{rollup_summary.get('targets_with_duplicates_count', 0)}"
                ),
            ]
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
                "legal_name": item.get("legal_name"),
                "short_name": item.get("short_name"),
                "ogrn": item.get("ogrn"),
                "issuer_group_name": item.get("issuer_group_name"),
                "issuer_role": item.get("issuer_role"),
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
                "legal_name": item.get("legal_name"),
                "short_name": item.get("short_name"),
                "ogrn": item.get("ogrn"),
                "issuer_group_name": item.get("issuer_group_name"),
                "issuer_role": item.get("issuer_role"),
                "suggested_search_query": _suggested_query(item),
                "notes": "needs review" if item.get("needs_identity_review") else "",
            }
        )
    return rows


def _empty_target(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": int(row["company_id"]),
        "company_name": row.get("company_name"),
        "canonical_company_id": int(row["company_id"]),
        "canonical_company_name": row.get("company_name"),
        "is_canonical_target": True,
        "is_duplicate_candidate": False,
        "duplicate_mapping_status": None,
        "duplicate_review_status": None,
        "duplicate_match_score": None,
        "duplicate_match_type": None,
        "duplicate_company_ids": [],
        "duplicate_company_names": [],
        "duplicate_sample_secids": [],
        "duplicate_sample_bond_names": [],
        "duplicate_count": 0,
        "ticker": row.get("ticker"),
        "inn": row.get("inn"),
        "bonds_count": 0,
        "sample_secids": [],
        "sample_bond_names": [],
        "reason": "",
        "identity_status": row.get("identity_status") or "unknown",
        "identity_confidence": row.get("identity_confidence"),
        "legal_name": row.get("legal_name"),
        "short_name": row.get("short_name"),
        "ogrn": row.get("ogrn"),
        "issuer_group_name": row.get("issuer_group_name"),
        "issuer_role": row.get("issuer_role"),
        "possible_canonical_company_id": None,
        "possible_canonical_company_name": None,
        "possible_duplicate_match_score": None,
        "possible_duplicate_reasons": [],
        "needs_duplicate_review": False,
        "suggested_search_query": "",
        "notes": "",
        "_reasons": set(),
        "_priority": 99,
    }


def _merge_target(target: dict[str, Any], row: dict[str, Any]) -> None:
    target["company_name"] = target.get("company_name") or row.get("company_name")
    target["ticker"] = target.get("ticker") or row.get("ticker")
    target["inn"] = target.get("inn") or row.get("inn")
    target["bonds_count"] += int(row.get("bonds_count") or 0)
    row_status = row.get("identity_status")
    current_status = target.get("identity_status")
    if row_status and _identity_status_priority(row_status) >= _identity_status_priority(current_status):
        target["identity_status"] = row_status
        target["identity_confidence"] = row.get("identity_confidence") or target.get("identity_confidence")
    elif not target.get("identity_confidence"):
        target["identity_confidence"] = row.get("identity_confidence")
    for field in ("legal_name", "short_name", "ogrn", "issuer_group_name", "issuer_role"):
        target[field] = target.get(field) or row.get(field)
    for reason in _split_reasons(row.get("reason") or "identity review"):
        target["_reasons"].add(reason)
    if not row.get("inn"):
        target["_reasons"].add("missing inn")
    if str(row.get("company_name") or "").startswith("Unknown issuer for "):
        target["_reasons"].add("unknown issuer identity")
    target["reason"] = "; ".join(sorted(target["_reasons"]))
    target["_priority"] = min(REASON_PRIORITY.get(reason, 99) for reason in target["_reasons"])
    for field in ("sample_secids", "sample_bond_names"):
        for value in row.get(field, []):
            if value and value not in target[field]:
                target[field].append(value)
    target["suggested_search_query"] = target.get("suggested_search_query") or _suggested_query(target)
    target["notes"] = target.get("notes") or row.get("notes") or ""


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
    result = http_request(
        "GET",
        f"{backend}/api/companies/identity/canonical-groups?active_only=true",
        None,
    )
    try:
        data = _data_or_raise(result)
    except Exception as exc:
        warnings.append({"message": f"canonical group resolution failed: {exc}"})
        return {"by_candidate": {}, "groups_by_canonical": {}}
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
        mapping = by_candidate.get(company_id)
        if not mapping:
            target["canonical_company_id"] = target["company_id"]
            target["canonical_company_name"] = target.get("company_name")
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
            base = _canonical_empty_target(target, group) if mapping else dict(target)
            rolled[canonical_id] = base
        if mapping:
            _merge_target(rolled[canonical_id], target)
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


def _canonical_empty_target(
    source_target: dict[str, Any],
    group: dict[str, Any] | None,
) -> dict[str, Any]:
    canonical_id = int(group.get("canonical_company_id") if group else source_target["company_id"])
    name = group.get("canonical_company_name") if group else source_target.get("company_name")
    return {
        **_empty_target(
            {
                "company_id": canonical_id,
                "company_name": name,
                "ticker": None if group is None else group.get("canonical_ticker"),
                "inn": None if group is None else group.get("canonical_inn"),
                "identity_status": None if group is None else group.get("canonical_identity_status"),
                "identity_confidence": None,
                "legal_name": None if group is None else group.get("canonical_legal_name"),
                "short_name": None if group is None else group.get("canonical_short_name"),
                "ogrn": None if group is None else group.get("canonical_ogrn"),
                "issuer_group_name": None if group is None else group.get("canonical_issuer_group_name"),
                "issuer_role": None if group is None else group.get("canonical_issuer_role"),
            }
        ),
        "source_reasons": set(),
    }


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
    if include_duplicate_members:
        target["duplicate_company_ids"] = [member.get("company_id") for member in members]
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


def _rollup_summary(
    raw_count: int,
    rows: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, int]:
    canonical_count = len(rows)
    duplicate_member_count = sum(int(row.get("duplicate_count") or 0) for row in rows)
    return {
        "raw_target_count": raw_count,
        "canonical_target_count": canonical_count,
        "deduplicated_count": max(0, raw_count - canonical_count),
        "duplicate_member_count": duplicate_member_count,
        "canonical_groups_count": len(context.get("groups_by_canonical") or {}),
        "targets_with_duplicates_count": sum(
            1 for row in rows if int(row.get("duplicate_count") or 0) > 0
        ),
    }


def _enrich_canonical_identity_rows(
    targets: dict[int, dict[str, Any]],
    backend: str,
    http_request: Any,
) -> None:
    for row in targets.values():
        canonical_id = row.get("canonical_company_id") or row.get("company_id")
        if canonical_id is None or not row.get("is_canonical_target", True):
            continue
        company = _safe_get_json(http_request, f"{backend}/api/companies/{int(canonical_id)}", default={})
        profile = _safe_get_json(
            http_request,
            f"{backend}/api/companies/identity/profiles/{int(canonical_id)}",
            default={},
        )
        if not isinstance(company, dict):
            company = {}
        if not isinstance(profile, dict):
            profile = {}
        row["company_id"] = int(canonical_id)
        row["canonical_company_id"] = int(canonical_id)
        row["company_name"] = company.get("name") or row.get("company_name")
        row["canonical_company_name"] = company.get("name") or row.get("canonical_company_name")
        row["ticker"] = company.get("ticker") or row.get("ticker")
        row["inn"] = profile.get("inn") or company.get("inn") or row.get("inn")
        if profile.get("identity_status"):
            row["identity_status"] = profile.get("identity_status")
        row["identity_confidence"] = profile.get("identity_confidence") or row.get("identity_confidence")
        for field in ("legal_name", "short_name", "ogrn", "issuer_group_name", "issuer_role"):
            row[field] = profile.get(field) or row.get(field)


def _duplicate_hints(
    backend: str,
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    result = http_request(
        "GET",
        (
            f"{backend}/api/companies/identity/duplicates/diagnostics"
            f"?active_only=true&limit={max(50, args.limit * 4)}&min_score=0.50&include_bonds=true"
        ),
        None,
    )
    try:
        data = _data_or_raise(result)
    except Exception as exc:
        warnings.append({"message": f"duplicate hint diagnostics failed: {exc}"})
        return {}
    if not isinstance(data, dict):
        return {}
    hints: dict[int, dict[str, Any]] = {}
    for group in data.get("groups") or []:
        for candidate in group.get("candidates") or []:
            company_id = candidate.get("company_id")
            if company_id is None:
                continue
            current = hints.get(int(company_id))
            score = _float(candidate.get("match_score"))
            if current is not None and score <= _float(current.get("possible_duplicate_match_score")):
                continue
            hints[int(company_id)] = {
                "possible_canonical_company_id": group.get("canonical_company_id"),
                "possible_canonical_company_name": group.get("canonical_company_name"),
                "possible_duplicate_match_score": candidate.get("match_score"),
                "possible_duplicate_reasons": candidate.get("match_reasons") or [],
            }
    warnings.extend(data.get("warnings") or [])
    return hints


def _attach_duplicate_hint(target: dict[str, Any], hint: dict[str, Any] | None) -> None:
    if not hint:
        return
    target.update(hint)
    target["needs_duplicate_review"] = True
    target["notes"] = (
        "possible same-issuer duplicate requires review"
        if not target.get("notes")
        else f"{target['notes']}; possible same-issuer duplicate requires review"
    )


def _suggested_query(item: dict[str, Any]) -> str:
    secid = (item.get("sample_secids") or [""])[0]
    bond_name = (item.get("sample_bond_names") or [""])[0]
    if bond_name:
        return f'"{bond_name}" issuer INN'
    if secid:
        return f'"{secid}" issuer'
    return f'"{item.get("company_name") or ""}" issuer INN'


def _split_reasons(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def _data_or_raise(result: Any) -> Any:
    if isinstance(result, HttpResult):
        if not result.ok:
            raise RuntimeError(result.error or result.text or "request failed")
        return result.data
    return result


def _safe_get_json(http_request: Any, url: str, default: Any = None) -> Any:
    try:
        return _data_or_raise(http_request("GET", url, None))
    except Exception:
        return default


def _csv_value(value: Any) -> str:
    if isinstance(value, set):
        value = sorted(value)
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _identity_status_priority(value: Any) -> int:
    return {
        "unknown": 0,
        "weak": 1,
        "conflict": 2,
        "matched": 3,
        "verified": 4,
    }.get(str(value or "unknown").casefold(), 0)


def _flag(args: argparse.Namespace, name: str) -> bool:
    return bool(getattr(args, name, False))


if __name__ == "__main__":
    sys.exit(main())
