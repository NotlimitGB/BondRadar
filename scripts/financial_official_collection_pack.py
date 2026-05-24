from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

import identity_first_collection_queue as identity_queue
from financial_report_canonical_pack import (
    validate_collection_rows,
    normalize_canonical_rows,
)
from financial_report_collection_normalize import MONEY_FIELDS, RATIO_FIELDS
from financial_report_import import http_json, run_import_flow, write_json_report


TARGET_SOURCE_CHOICES = identity_queue.TARGET_SOURCE_CHOICES
MODE_CHOICES = ("queue", "template", "preview")
REPORT_TYPES = ("annual", "quarterly")
ACCOUNTING_STANDARDS = ("IFRS", "RAS", "unknown")
CONSOLIDATION_SCOPES = ("consolidated", "standalone", "unknown")
VALUE_SCALES = ("million", "unit", "thousand", "billion")

FINANCIAL_TEMPLATE_FIELDS = [
    "canonical_company_id",
    "company_id",
    "company_name",
    "canonical_company_name",
    "legal_name",
    "short_name",
    "display_name",
    "inn",
    "ogrn",
    "issuer_role",
    "identity_status",
    "identity_confidence",
    "identity_review_status",
    "period_year",
    "period_quarter",
    "period_start_date",
    "period_end_date",
    "published_at",
    "document_date",
    "report_type",
    "currency",
    "accounting_standard",
    "consolidation_scope",
    "value_scale",
    "source",
    "source_url",
    "source_file_name",
    "source_document_title",
    "source_document_date",
    "source_page",
    "source_table",
    "source_notes",
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "interest_expense",
    "debt_to_ebitda",
    "interest_coverage",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "review_status",
    "review_notes",
    "operator_notes",
    "recommended_collection_type",
]
CANONICAL_COMPAT_FIELDS = [
    "duplicate_company_ids",
    "sample_secids",
    "sample_bond_names",
    "issuer_group_name",
    "source_note",
]
CSV_FIELDS = FINANCIAL_TEMPLATE_FIELDS + CANONICAL_COMPAT_FIELDS
FINANCIAL_FIELDS = list(MONEY_FIELDS) + list(RATIO_FIELDS)
FIELDS_TO_COLLECT = [
    "revenue",
    "ebitda",
    "total_debt",
    "cash",
    "equity",
    "net_profit",
    "operating_cash_flow",
    "interest_expense",
    "net_debt",
]
RECOMMENDED_SOURCE_TYPES = [
    {
        "source_type": "issuer_investor_relations",
        "priority": 1,
        "notes": "Prefer official issuer site annual IFRS consolidated report.",
    },
    {
        "source_type": "official_disclosure",
        "priority": 2,
        "notes": "Use official disclosure system if issuer site is unavailable.",
    },
    {
        "source_type": "issuer_annual_report_pdf",
        "priority": 3,
        "notes": "Use the issuer annual report PDF or audited IFRS report PDF.",
    },
]
ALLOWED_OFFICIAL_SOURCE_TYPES = {
    "issuer_investor_relations",
    "official_issuer_report",
    "official_disclosure",
    "exchange_disclosure",
    "auditor_report",
    "issuer_annual_report_pdf",
    "operator_collection",
}
OFFICIAL_SOURCE_DOMAIN_HINTS = (
    "e-disclosure.ru",
    "disclosure.1prime.ru",
    "fedresurs.ru",
    "moex.com",
    "moex.ru",
    "rzd.ru",
    "eng.rzd.ru",
    "mostotrest.ru",
    "tmk-group.ru",
    "tmk-group.com",
)
BLOCKED_SOURCE_HINTS = (
    "wikipedia",
    "wikimedia",
    "wikiwand",
    "social_media",
    "forum",
    "blog",
)
SAFETY_FLAGS = {
    "read_only": True,
    "dry_run_only": True,
    "import_executed": False,
    "paper_trading_called": False,
    "identity_apply_executed": False,
    "would_mutate_scores": False,
    "would_trigger_paper_trading": False,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build preview-only official-source financial collection packs.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=MODE_CHOICES, required=True)
    parser.add_argument("--company-ids", default="")
    parser.add_argument("--company-names", default="")
    parser.add_argument("--source", choices=TARGET_SOURCE_CHOICES, default="company-id-list")
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--rollup-duplicates", action="store_true")
    parser.add_argument("--include-duplicate-members", action="store_true")
    parser.add_argument("--include-covered", action="store_true", default=False)
    parser.add_argument("--exclude-government-like", action="store_true", default=True)
    parser.add_argument("--include-partial", action="store_true")
    parser.add_argument("--only-collection-ready", action="store_true", default=True)
    parser.add_argument("--max-issuers", type=int, default=None)
    parser.add_argument("--period-year", type=int, default=None)
    parser.add_argument("--period-quarter", type=int, default=0)
    parser.add_argument("--report-type", choices=REPORT_TYPES, default="annual")
    parser.add_argument("--currency", default="RUB")
    parser.add_argument("--accounting-standard", choices=ACCOUNTING_STANDARDS, default="IFRS")
    parser.add_argument(
        "--consolidation-scope",
        choices=CONSOLIDATION_SCOPES,
        default="consolidated",
    )
    parser.add_argument("--value-scale", choices=VALUE_SCALES, default="million")
    parser.add_argument("--reviewed-input", type=Path, default=None)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--collection-ready-input", type=Path, default=None)
    parser.add_argument("--collection-pack-output", type=Path, default=None)
    parser.add_argument("--financial-template-output", type=Path, default=None)
    parser.add_argument("--evidence-template-output", type=Path, default=None)
    parser.add_argument("--source-checklist-output", type=Path, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_pack(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    if args.mode == "preview":
        report = _run_preview(args, http_request)
    else:
        report = _run_queue_or_template(args, http_request)
    return report, 1 if report["status"] == "failed" else 0


def _run_queue_or_template(
    args: argparse.Namespace,
    http_request: Any,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    identity_report = _load_or_run_identity_queue(args, http_request, warnings, errors)
    selected = list(identity_report.get("collection_ready") or [])
    selected = selected[: max(0, args.max_issuers)] if args.max_issuers is not None else selected
    partial_followup = _partial_followup_rows(identity_report) if args.include_partial else []
    template_source_rows = selected + partial_followup
    template_rows: list[dict[str, Any]] = []
    evidence_template: dict[str, Any] | None = None
    checklist_rows: list[dict[str, Any]] = []

    if args.mode == "template":
        metadata_errors = _template_metadata_errors(args)
        errors.extend(metadata_errors)
        if not errors:
            template_rows = build_financial_template_rows(template_source_rows, args)
            evidence_template = build_evidence_template(template_source_rows)
            checklist_rows = build_source_checklist_rows(template_source_rows)
            if args.financial_template_output is not None:
                write_financial_template_csv(template_rows, args.financial_template_output)
            if args.evidence_template_output is not None:
                write_json_report(evidence_template, args.evidence_template_output)
            if args.source_checklist_output is not None:
                write_source_checklist_csv(checklist_rows, args.source_checklist_output)
            if args.collection_pack_output is not None:
                write_json_report(
                    {
                        "status": "template",
                        "selected_issuers": selected,
                        "partial_followup_issuers": partial_followup,
                        "financial_template_rows": template_rows,
                        "evidence_template": evidence_template,
                        "source_checklist_rows": checklist_rows,
                        **SAFETY_FLAGS,
                    },
                    args.collection_pack_output,
                )
            warnings.extend(_template_warnings(template_rows))

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "mode": args.mode,
        "selected_issuer_count": len(selected),
        "partial_followup_count": len(partial_followup),
        "financial_template_rows": len(template_rows),
        "evidence_template_issuer_count": 0
        if evidence_template is None
        else len(evidence_template.get("issuers") or []),
        "source_checklist_rows": len(checklist_rows),
        "selected_issuers": [_issuer_summary(row) for row in selected],
        "partial_followup_issuers": [_issuer_summary(row) for row in partial_followup],
        "identity_first_report": identity_report,
        "financial_template_output": _path_value(args.financial_template_output),
        "evidence_template_output": _path_value(args.evidence_template_output),
        "source_checklist_output": _path_value(args.source_checklist_output),
        "collection_pack_output": _path_value(args.collection_pack_output),
        "template_fields": CSV_FIELDS,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(args.mode, status),
        **SAFETY_FLAGS,
    }
    return report


def _run_preview(
    args: argparse.Namespace,
    http_request: Any,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if args.reviewed_input is None:
        errors.append({"message": "preview mode requires --reviewed-input"})
        return _preview_report(args, warnings, errors, None, None, None)

    validation = validate_reviewed_collection(args.reviewed_input, args.format)
    warnings.extend(validation.get("warnings") or [])
    errors.extend(validation.get("errors") or [])
    normalize_report = None
    dry_run_report = None

    if not errors:
        normalize_report = normalize_canonical_rows(validation["rows"])
        warnings.extend(normalize_report.get("warnings") or [])
        errors.extend(normalize_report.get("errors") or [])

    if not errors:
        with tempfile.TemporaryDirectory() as temp_dir:
            normalized_path = Path(temp_dir) / "official_collection_preview_normalized.json"
            normalized_path.write_text(
                json.dumps(
                    {"rows": normalize_report["normalized_rows"]},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            dry_run_report, dry_run_exit_code = run_import_flow(
                input_path=normalized_path,
                format_value="json",
                source="operator_collection",
                backend_url=args.backend_url,
                dry_run=True,
                execute="no",
                confirm_import=None,
                rebuild_existing=False,
                validate_companies=True,
                limit=None,
                http_request=http_request,
            )
            warnings.extend(dry_run_report.get("warnings") or [])
            errors.extend(dry_run_report.get("errors") or [])
            if dry_run_exit_code != 0:
                errors.append({"message": "backend preview dry-run failed"})

    return _preview_report(args, warnings, errors, validation, normalize_report, dry_run_report)


def build_financial_template_rows(
    issuers: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issuer in issuers:
        identity = issuer.get("identity") or {}
        collection = issuer.get("recommended_collection") or {}
        period_year = int(args.period_year)
        period_quarter = _period_quarter(args)
        rows.append(
            {
                "canonical_company_id": issuer.get("canonical_company_id"),
                "company_id": issuer.get("company_id"),
                "company_name": issuer.get("company_name"),
                "canonical_company_name": issuer.get("canonical_company_name"),
                "legal_name": identity.get("legal_name"),
                "short_name": identity.get("short_name"),
                "display_name": identity.get("display_name"),
                "inn": identity.get("inn"),
                "ogrn": identity.get("ogrn"),
                "issuer_role": identity.get("issuer_role"),
                "identity_status": issuer.get("identity_status"),
                "identity_confidence": issuer.get("identity_confidence"),
                "identity_review_status": identity.get("review_status"),
                "period_year": period_year,
                "period_quarter": period_quarter,
                "period_start_date": _period_start_date(period_year, period_quarter),
                "period_end_date": _period_end_date(period_year, period_quarter),
                "published_at": "",
                "document_date": "",
                "report_type": args.report_type,
                "currency": args.currency.upper(),
                "accounting_standard": args.accounting_standard,
                "consolidation_scope": args.consolidation_scope,
                "value_scale": args.value_scale,
                "source": "operator_collection",
                "source_url": "",
                "source_file_name": "",
                "source_document_title": "",
                "source_document_date": "",
                "source_page": "",
                "source_table": "",
                "source_notes": "",
                "revenue": "",
                "ebitda": "",
                "net_debt": "",
                "total_debt": "",
                "cash": "",
                "interest_expense": "",
                "debt_to_ebitda": "",
                "interest_coverage": "",
                "equity": "",
                "short_term_debt": "",
                "operating_cash_flow": "",
                "net_profit": "",
                "review_status": "pending",
                "review_notes": "",
                "operator_notes": (
                    "Official-source collection template; fill values only "
                    "from official issuer/disclosure/auditor reports."
                ),
                "duplicate_company_ids": "",
                "sample_secids": _sample_secids(issuer),
                "sample_bond_names": _sample_bond_names(issuer),
                "issuer_group_name": identity.get("issuer_group_name"),
                "source_note": "",
                "recommended_collection_type": collection.get("collection_type"),
            }
        )
    return rows


def build_evidence_template(issuers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "template",
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "issuers": [
            {
                "company_id": issuer.get("company_id"),
                "company_name": issuer.get("company_name"),
                "canonical_company_id": issuer.get("canonical_company_id"),
                "canonical_company_name": issuer.get("canonical_company_name"),
                "identity": _identity_payload(issuer),
                "recommended_sources": [
                    {
                        **source,
                        "status": "operator_to_find",
                        "url": "",
                    }
                    for source in RECOMMENDED_SOURCE_TYPES
                ],
                "fields_to_collect": list(FIELDS_TO_COLLECT),
                "field_evidence": {
                    field: {
                        "value": None,
                        "page": None,
                        "table": None,
                        "evidence_note": None,
                    }
                    for field in FIELDS_TO_COLLECT
                },
            }
            for issuer in issuers
        ],
    }


def build_source_checklist_rows(issuers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issuer in issuers:
        identity = issuer.get("identity") or {}
        for source in RECOMMENDED_SOURCE_TYPES:
            rows.append(
                {
                    "rank": issuer.get("rank"),
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name"),
                    "canonical_company_id": issuer.get("canonical_company_id"),
                    "canonical_company_name": issuer.get("canonical_company_name"),
                    "identity_status": issuer.get("identity_status"),
                    "identity_confidence": issuer.get("identity_confidence"),
                    "inn": identity.get("inn"),
                    "ogrn": identity.get("ogrn"),
                    "priority_score": issuer.get("priority_score"),
                    "priority_level": issuer.get("priority_level"),
                    "source_labels": _join((issuer.get("source_presence") or {}).get("source_labels")),
                    "recommended_source_type": source["source_type"],
                    "official_source_url": "",
                    "source_status": "operator_to_find",
                    "fields_to_collect": _join(FIELDS_TO_COLLECT),
                    "notes": source["notes"],
                }
            )
    return rows


def validate_reviewed_collection(path: Path, format_value: str) -> dict[str, Any]:
    try:
        raw_rows = [_canonical_row(row) for row in _load_reviewed_rows(path, format_value)]
    except Exception as exc:
        return {
            "status": "failed",
            "total_rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "rows": [],
            "warnings": [],
            "errors": [{"row_index": None, "message": str(exc)}],
        }

    canonical_validation = validate_collection_rows(
        raw_rows,
        apply_mode=False,
        allow_non_official_source=False,
    )
    warnings = list(canonical_validation.get("warnings") or [])
    errors = list(canonical_validation.get("errors") or [])
    rows = list(canonical_validation.get("rows") or [])

    invalid_indexes = {
        item.get("row_index")
        for item in errors
        if item.get("row_index") is not None
    }
    for row_index, row in enumerate(rows, start=1):
        strict = _strict_source_validation(row)
        for message in strict["warnings"]:
            warnings.append(_row_message(row, row_index, message))
        for message in strict["errors"]:
            errors.append(_row_message(row, row_index, message))
            invalid_indexes.add(row_index)

    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "total_rows": len(raw_rows),
        "row_count": len(raw_rows),
        "valid_rows": len(raw_rows) - len(invalid_indexes),
        "invalid_rows": len(invalid_indexes),
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
    }


def write_financial_template_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in CSV_FIELDS})


def write_source_checklist_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "company_id",
        "company_name",
        "canonical_company_id",
        "canonical_company_name",
        "identity_status",
        "identity_confidence",
        "inn",
        "ogrn",
        "priority_score",
        "priority_level",
        "source_labels",
        "recommended_source_type",
        "official_source_url",
        "source_status",
        "fields_to_collect",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Official-Source Financial Collection Pack",
        "",
        "## Overall Status",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- selected_issuer_count: {report.get('selected_issuer_count')}",
        f"- read_only: {report.get('read_only')}",
        f"- dry_run_only: {report.get('dry_run_only')}",
        f"- import_executed: {report.get('import_executed')}",
        "",
        "## Selected Issuers",
        "",
        "| Company ID | Company | Identity | Confidence | Score | Action |",
        "| ---: | --- | --- | ---: | ---: | --- |",
    ]
    selected = report.get("selected_issuers") or []
    if selected:
        for issuer in selected:
            lines.append(
                "| {company_id} | {company_name} | {identity_status} | {identity_confidence} | {priority_score} | {operator_next_action} |".format(
                    company_id=issuer.get("company_id"),
                    company_name=issuer.get("company_name") or "",
                    identity_status=issuer.get("identity_status") or "",
                    identity_confidence=issuer.get("identity_confidence"),
                    priority_score=issuer.get("priority_score"),
                    operator_next_action=issuer.get("operator_next_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Financial Template",
            "",
            f"- path: `{report.get('financial_template_output')}`",
            f"- row_count: {report.get('financial_template_rows')}",
            f"- fields: {_join(report.get('template_fields'))}",
            "",
            "## Evidence Template",
            "",
            f"- path: `{report.get('evidence_template_output')}`",
            f"- issuer_count: {report.get('evidence_template_issuer_count')}",
            f"- fields_to_collect: {_join(FIELDS_TO_COLLECT)}",
            "",
            "## Source Checklist",
            "",
            f"- path: `{report.get('source_checklist_output')}`",
            f"- recommended official source types: {_join([item['source_type'] for item in RECOMMENDED_SOURCE_TYPES])}",
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
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- identity_apply_executed: {report.get('identity_apply_executed')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
            "## Next Steps",
            "",
        ]
    )
    lines.extend(f"- {step}" for step in report.get("next_steps") or [])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_pack(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-official-collection-pack] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[financial-official-collection-pack] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    print(f"[financial-official-collection-pack] {report['status']}", flush=True)
    return exit_code


def _load_or_run_identity_queue(
    args: argparse.Namespace,
    http_request: Any,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    if args.collection_ready_input is not None:
        try:
            with args.collection_ready_input.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise ValueError("collection-ready input JSON must be an object")
            return payload
        except Exception as exc:
            errors.append({"message": str(exc)})
            return _empty_identity_report()
    report, _exit_code = identity_queue.run_queue(args, http_request=http_request)
    warnings.extend(report.get("warnings") or [])
    errors.extend(report.get("errors") or [])
    return report


def _partial_followup_rows(identity_report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in identity_report.get("already_covered") or []
        if row.get("risk_scoring_readiness") == "partial"
    ]


def _template_metadata_errors(args: argparse.Namespace) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if args.period_year is None:
        errors.append({"message": "template mode requires --period-year"})
    if args.period_year is not None and (args.period_year < 1900 or args.period_year > 2100):
        errors.append({"message": "period_year must be between 1900 and 2100"})
    if args.period_quarter < 0 or args.period_quarter > 4:
        errors.append({"message": "period_quarter must be between 0 and 4"})
    return errors


def _template_warnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        for field in ("source_url", "source_file_name", "source_page", "source_table"):
            if not row.get(field):
                warnings.append(
                    {
                        "row_index": index,
                        "company_id": row.get("company_id"),
                        "message": f"{field} is empty; official source evidence required before preview/import",
                    }
                )
        warnings.append(
            {
                "row_index": index,
                "company_id": row.get("company_id"),
                "message": "financial values are intentionally empty in template mode",
            }
        )
    return warnings


def _preview_report(
    args: argparse.Namespace,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    validation: dict[str, Any] | None,
    normalize_report: dict[str, Any] | None,
    dry_run_report: dict[str, Any] | None,
) -> dict[str, Any]:
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": "preview",
        "row_count": 0 if validation is None else validation.get("row_count", 0),
        "valid_rows": 0 if validation is None else validation.get("valid_rows", 0),
        "invalid_rows": 0 if validation is None else validation.get("invalid_rows", 0),
        "validation": validation,
        "normalize_report": normalize_report,
        "dry_run_report": dry_run_report,
        "financial_template_output": None,
        "evidence_template_output": None,
        "source_checklist_output": None,
        "collection_pack_output": None,
        "selected_issuer_count": 0,
        "selected_issuers": [],
        "financial_template_rows": 0,
        "evidence_template_issuer_count": 0,
        "template_fields": CSV_FIELDS,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("preview", status),
        **SAFETY_FLAGS,
    }


def _strict_source_validation(row: dict[str, Any]) -> dict[str, list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    source = str(row.get("source") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    source_file_name = str(row.get("source_file_name") or "").strip()
    source_type = str(row.get("recommended_source_type") or source or "").strip()
    financial_values_present = [
        field for field in FINANCIAL_FIELDS if row.get(field) not in (None, "")
    ]

    source_text = " ".join([source, source_url, source_file_name, source_type]).casefold()
    if any(hint in source_text for hint in BLOCKED_SOURCE_HINTS) or "wiki" in source_text:
        errors.append("blocked source detected; use official issuer/disclosure/auditor sources only")
    if source_type and source_type not in ALLOWED_OFFICIAL_SOURCE_TYPES:
        errors.append("unknown or unsupported source type is blocked in preview mode")
    if financial_values_present and not source_url and not source_file_name:
        errors.append("financial values require source_url or source_file_name")
    if source_url and _unknown_official_domain(source_url):
        errors.append("source_url domain is not recognized as official for Task 95 preview")
    if not source_url and not source_file_name:
        warnings.append("source_url or source_file_name is recommended")
    for field in ("source_page", "source_table"):
        if financial_values_present and not row.get(field):
            warnings.append(f"{field} is recommended for financial values")
    zero_fields = [
        field
        for field in FINANCIAL_FIELDS
        if str(row.get(field) or "").strip() in {"0", "0.0", "0.00"}
    ]
    if len(zero_fields) >= 3:
        errors.append("many financial fields are zero; verify that placeholders were not entered as values")
    return {"warnings": warnings, "errors": errors}


def _load_reviewed_rows(path: Path, format_value: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"reviewed input does not exist: {path}")
    if format_value == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON input must be a list of row objects or an object with rows")
    return [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_company_id": row.get("canonical_company_id") or row.get("company_id"),
        "canonical_company_name": row.get("canonical_company_name") or row.get("company_name"),
        "legal_name": row.get("legal_name"),
        "short_name": row.get("short_name"),
        "inn": row.get("inn"),
        "ogrn": row.get("ogrn"),
        "issuer_role": row.get("issuer_role"),
        "duplicate_company_ids": row.get("duplicate_company_ids"),
        "sample_secids": row.get("sample_secids"),
        "sample_bond_names": row.get("sample_bond_names"),
        "period_year": row.get("period_year"),
        "period_quarter": row.get("period_quarter"),
        "period_start_date": row.get("period_start_date"),
        "period_end_date": row.get("period_end_date"),
        "published_at": row.get("published_at"),
        "document_date": row.get("document_date") or row.get("source_document_date"),
        "currency": row.get("currency"),
        "accounting_standard": row.get("accounting_standard"),
        "consolidation_scope": row.get("consolidation_scope"),
        "value_scale": "raw" if row.get("value_scale") == "unit" else row.get("value_scale"),
        "source": row.get("source"),
        "source_url": row.get("source_url"),
        "source_file_name": row.get("source_file_name"),
        "source_page": row.get("source_page"),
        "source_table": row.get("source_table"),
        "source_note": row.get("source_notes") or row.get("source_note"),
        "report_type": row.get("report_type"),
        **{field: row.get(field) for field in FINANCIAL_FIELDS},
        "operator_notes": row.get("operator_notes"),
    }


def _row_message(row: dict[str, Any], row_index: int | None, message: str) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "canonical_company_id": row.get("canonical_company_id"),
        "period_year": row.get("period_year"),
        "period_quarter": row.get("period_quarter"),
        "message": message,
    }


def _identity_payload(issuer: dict[str, Any]) -> dict[str, Any]:
    identity = issuer.get("identity") or {}
    return {
        "legal_name": identity.get("legal_name"),
        "short_name": identity.get("short_name"),
        "display_name": identity.get("display_name"),
        "inn": identity.get("inn"),
        "ogrn": identity.get("ogrn"),
        "issuer_role": identity.get("issuer_role"),
        "identity_status": issuer.get("identity_status") or identity.get("identity_status"),
        "identity_confidence": issuer.get("identity_confidence")
        if issuer.get("identity_confidence") is not None
        else identity.get("identity_confidence"),
        "review_status": identity.get("review_status"),
        "identity_source": identity.get("identity_source"),
        "source_url": identity.get("source_url"),
    }


def _issuer_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": row.get("rank"),
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "canonical_company_id": row.get("canonical_company_id"),
        "canonical_company_name": row.get("canonical_company_name"),
        "identity_status": row.get("identity_status"),
        "identity_confidence": row.get("identity_confidence"),
        "priority_score": row.get("priority_score"),
        "priority_level": row.get("priority_level"),
        "operator_next_action": row.get("operator_next_action"),
    }


def _empty_identity_report() -> dict[str, Any]:
    return {
        "collection_ready": [],
        "already_covered": [],
        "identity_review_required": [],
        "excluded_or_deprioritized": [],
    }


def _period_quarter(args: argparse.Namespace) -> int:
    if args.report_type == "annual":
        return 0
    return int(args.period_quarter)


def _period_start_date(year: int, quarter: int) -> str:
    if quarter == 0:
        return f"{year}-01-01"
    month = 1 + (quarter - 1) * 3
    return f"{year}-{month:02d}-01"


def _period_end_date(year: int, quarter: int) -> str:
    if quarter == 0:
        return f"{year}-12-31"
    month_day = {
        1: "03-31",
        2: "06-30",
        3: "09-30",
        4: "12-31",
    }
    return f"{year}-{month_day.get(quarter, '12-31')}"


def _sample_secids(issuer: dict[str, Any]) -> str:
    values = [
        item.get("secid")
        for item in (issuer.get("bond_context") or {}).get("sample_bonds") or []
        if item.get("secid")
    ]
    return _join(values)


def _sample_bond_names(issuer: dict[str, Any]) -> str:
    values = [
        item.get("name")
        for item in (issuer.get("bond_context") or {}).get("sample_bonds") or []
        if item.get("name")
    ]
    return _join(values)


def _unknown_official_domain(source_url: str) -> bool:
    parsed = urllib.parse.urlparse(source_url.casefold())
    host = (parsed.netloc or parsed.path.split("/")[0]).removeprefix("www.")
    if not host:
        return False
    return not any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_SOURCE_DOMAIN_HINTS)


def _path_value(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _next_steps(mode: str, status: str) -> list[str]:
    if status == "failed":
        return ["Fix collection pack errors and rerun in preview-only mode."]
    if mode == "preview":
        return [
            "Review preview warnings and errors before any future import task.",
            "Do not import until official-source evidence has been reviewed.",
        ]
    return [
        "Fill financial values only from official issuer/disclosure/auditor reports.",
        "Run mode=preview before any future import task.",
    ]


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _join(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, list):
        return ", ".join(str(item) for item in values)
    return str(values)


if __name__ == "__main__":
    sys.exit(main())
