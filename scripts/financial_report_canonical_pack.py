from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from financial_report_collection_normalize import (
    MONEY_FIELDS,
    RATIO_FIELDS,
    VALUE_SCALE_FACTORS,
    normalize_rows as normalize_collection_rows,
    write_normalized_rows,
)
from financial_report_import import HttpResult, http_json, run_import_flow, write_json_report
from financial_report_target_issuers import (
    build_report as build_financial_target_report,
    parse_args as parse_financial_target_args,
)


TEMPLATE_FIELDS = [
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
    "period_year",
    "period_quarter",
    "period_start_date",
    "period_end_date",
    "published_at",
    "document_date",
    "currency",
    "accounting_standard",
    "consolidation_scope",
    "value_scale",
    "source",
    "source_url",
    "source_file_name",
    "source_page",
    "source_table",
    "source_note",
    "report_type",
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
    "debt_to_ebitda",
    "interest_coverage",
    "operator_notes",
]
CANONICAL_COLLECTION_FIELDS = TEMPLATE_FIELDS
CANONICAL_CONTEXT_FIELDS = {
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
    "operator_notes",
}
ACCOUNTING_STANDARDS = {"IFRS", "RAS", "management", "unknown"}
CONSOLIDATION_SCOPES = {"consolidated", "standalone", "unknown"}
NULL_MARKERS = {"-", "—", "н/д", "n/a", "na", "null"}
CORE_FINANCIAL_FIELDS = (
    "revenue",
    "ebitda",
    "total_debt",
    "cash",
    "equity",
    "interest_expense",
)
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
ROLLBACK_NOTE = (
    "This script does not perform automatic rollback.\n"
    "Before confirmed import on VDS, create a PostgreSQL backup.\n"
    "To rollback, restore the backup or manually review rows in:\n"
    "- financial_reports\n"
    "- financial_report_source_documents"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare, preview, and optionally import canonical financial report packs.",
    )
    parser.add_argument(
        "--mode",
        choices=("targets", "template", "preview", "apply"),
        default="targets",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--source",
        choices=("mixed", "paper-positions", "top-predictions", "bond-universe"),
        default="mixed",
    )
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--stale-after-days", type=int, default=540)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--company-ids", default="")
    parser.add_argument("--company-names", default="")
    parser.add_argument("--use-duplicate-mapping", action="store_true")
    parser.add_argument("--rollup-duplicates", action="store_true")
    parser.add_argument("--include-duplicate-members", action="store_true")
    parser.add_argument("--collection-template-output", type=Path, default=None)
    parser.add_argument("--reviewed-input", type=Path, default=None)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=Path("logs/financial_reports/canonical_pack_normalized.csv"),
    )
    parser.add_argument("--normalized-format", choices=("csv", "json"), default="csv")
    parser.add_argument("--execute-import", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-import", choices=("yes", "no"), default="no")
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--allow-non-official-source", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_flow(args: argparse.Namespace, http_request: Any = None) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    if args.mode in {"targets", "template"}:
        report = _run_targets_or_template(args, http_request)
    elif args.mode == "preview":
        report = _run_preview(args, http_request)
    else:
        report = _run_apply(args, http_request)
    return report, 1 if report["status"] == "failed" else 0


def run_pack(args: argparse.Namespace, http_request: Any = None) -> tuple[dict[str, Any], int]:
    return run_flow(args, http_request=http_request)


def _run_targets_or_template(
    args: argparse.Namespace,
    http_request: Any,
) -> dict[str, Any]:
    target_report = build_canonical_targets(args, http_request=http_request)
    warnings = list(target_report.get("warnings") or [])
    errors = list(target_report.get("errors") or [])
    template_rows: list[dict[str, Any]] = []

    if args.mode == "template":
        template_rows = build_template_rows(target_report.get("targets") or [])
        if args.collection_template_output is None:
            errors.append({"message": "template mode requires --collection-template-output"})
        elif not errors:
            output_format = _infer_format(args.collection_template_output, args.format)
            write_collection_template(
                template_rows,
                args.collection_template_output,
                output_format,
            )

    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": args.mode,
        "source": args.source,
        "target_report": target_report,
        "safe_sources": target_report.get("safe_sources"),
        "total_targets": target_report.get("total_targets", 0),
        "targets": target_report.get("targets") or [],
        "template_rows": template_rows,
        "collection_template_rows": template_rows,
        "collection_template_output": (
            None if args.collection_template_output is None else str(args.collection_template_output)
        ),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(args.mode, executed=False),
    }


def _run_preview(args: argparse.Namespace, http_request: Any) -> dict[str, Any]:
    return _run_reviewed_pack(args, http_request=http_request, execute=False)


def _run_apply(args: argparse.Namespace, http_request: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if args.execute_import != "yes":
        errors.append({"message": "apply mode requires --execute-import yes"})
    if args.confirm_import != "yes":
        errors.append({"message": "apply mode requires --confirm-import yes"})
    if errors:
        return {
            "status": "failed",
            "mode": args.mode,
            "warnings": [],
            "errors": errors,
            "next_steps": _next_steps(args.mode, executed=False),
        }
    return _run_reviewed_pack(args, http_request=http_request, execute=True)


def _run_reviewed_pack(
    args: argparse.Namespace,
    *,
    http_request: Any,
    execute: bool,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    backend = args.backend_url.rstrip("/")

    if args.reviewed_input is None:
        errors.append({"message": f"{args.mode} mode requires --reviewed-input"})
        return _reviewed_report(
            args,
            warnings=warnings,
            errors=errors,
            coverage_before=None,
            validation=None,
            normalize_report=None,
            dry_run_report=None,
            apply_report=None,
            coverage_after=None,
            import_executed=False,
        )

    coverage_before = _coverage(backend, args, http_request)
    if not coverage_before.ok:
        warnings.append(
            {
                "message": "coverage check before import was unavailable",
                "details": _http_report(coverage_before),
            }
        )

    validation = validate_reviewed_input(
        args.reviewed_input,
        args.format,
        apply_mode=execute,
        allow_non_official_source=args.allow_non_official_source,
    )
    warnings.extend(validation.get("warnings") or [])
    errors.extend(validation.get("errors") or [])

    normalize_report = None
    dry_run_report = None
    apply_report = None
    import_executed = False
    if not errors:
        normalize_report = normalize_canonical_rows(
            validation["rows"],
            args.normalized_output,
            args.normalized_format,
        )
        warnings.extend(normalize_report.get("warnings") or [])
        errors.extend(normalize_report.get("errors") or [])

    if not errors:
        dry_run_report, dry_run_exit_code = run_import_flow(
            input_path=args.normalized_output,
            format_value=args.normalized_format,
            source="operator_collection",
            backend_url=args.backend_url,
            dry_run=True,
            execute="no",
            confirm_import=None,
            rebuild_existing=args.rebuild_existing,
            validate_companies=True,
            limit=None,
            http_request=http_request,
        )
        warnings.extend(dry_run_report.get("warnings") or [])
        errors.extend(dry_run_report.get("errors") or [])
        if dry_run_exit_code != 0:
            errors.append({"message": "dry-run import flow failed"})

    if execute and not errors:
        apply_report, apply_exit_code = run_import_flow(
            input_path=args.normalized_output,
            format_value=args.normalized_format,
            source="operator_collection",
            backend_url=args.backend_url,
            dry_run=False,
            execute="yes",
            confirm_import="yes",
            rebuild_existing=args.rebuild_existing,
            validate_companies=True,
            limit=None,
            http_request=http_request,
        )
        warnings.extend(apply_report.get("warnings") or [])
        errors.extend(apply_report.get("errors") or [])
        if apply_exit_code != 0:
            errors.append({"message": "confirmed import failed"})
        else:
            import_executed = True

    coverage_after = _coverage(backend, args, http_request)
    if not coverage_after.ok:
        warnings.append(
            {
                "message": "coverage check after import was unavailable",
                "details": _http_report(coverage_after),
            }
        )

    return _reviewed_report(
        args,
        warnings=warnings,
        errors=errors,
        coverage_before=coverage_before,
        validation=validation,
        normalize_report=normalize_report,
        dry_run_report=dry_run_report,
        apply_report=apply_report,
        coverage_after=coverage_after,
        import_executed=import_executed,
    )


def build_canonical_targets(
    args: argparse.Namespace,
    http_request: Any = None,
) -> dict[str, Any]:
    http_request = http_request or http_json
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    selected_ids = _parse_int_list(args.company_ids)

    if args.source == "paper-positions":
        return {
            "status": "failed",
            "source": args.source,
            "total_targets": 0,
            "targets": [],
            "warnings": [],
            "errors": [
                {
                    "message": (
                        "paper-positions source is blocked for canonical financial "
                        "report packs; use top-predictions, bond-universe, or mixed"
                    )
                }
            ],
            "next_steps": ["Choose a non-paper target source."],
        }

    try:
        selected_ids.extend(_resolve_company_names(args.company_names, args.backend_url, http_request))
    except ValueError as exc:
        errors.append({"message": str(exc)})

    rows_by_key: dict[int, dict[str, Any]] = {}
    if not errors:
        for source in _safe_sources(args.source):
            target_args = _target_args(args, source, selected_ids)
            source_report = build_financial_target_report(target_args, http_request=http_request)
            warnings.extend(source_report.get("warnings") or [])
            errors.extend(source_report.get("errors") or [])
            for row in source_report.get("targets") or []:
                _merge_target_row(rows_by_key, row)

    rows = list(rows_by_key.values())
    if selected_ids:
        selected = set(selected_ids)
        rows = [
            row
            for row in rows
            if int(row.get("canonical_company_id") or row.get("company_id")) in selected
            or int(row.get("company_id") or 0) in selected
            or bool(selected.intersection(_int_set(row.get("duplicate_company_ids"))))
        ]
        rows = _sort_selected_rows(rows, selected_ids)
    else:
        rows = sorted(
            rows,
            key=lambda item: (
                not item.get("needs_financial_report", True),
                int(item.get("bonds_count") or 0),
                item.get("canonical_company_name") or item.get("company_name") or "",
            ),
            reverse=True,
        )[: max(1, int(args.limit or 1))]

    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "source": args.source,
        "safe_sources": _safe_sources(args.source),
        "selected_company_ids": selected_ids,
        "total_targets": len(rows),
        "targets": rows,
        "warnings": warnings,
        "errors": errors,
        "next_steps": [
            "Generate a canonical collection template.",
            "Fill real values manually from official issuer reports.",
        ],
    }


def build_template_rows(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        rows.append(
            {
                "canonical_company_id": target.get("canonical_company_id") or target.get("company_id"),
                "canonical_company_name": target.get("canonical_company_name") or target.get("company_name"),
                "legal_name": target.get("legal_name"),
                "short_name": target.get("short_name"),
                "inn": target.get("company_inn"),
                "ogrn": target.get("ogrn"),
                "issuer_group_name": target.get("issuer_group_name"),
                "issuer_role": target.get("issuer_role") or "unknown",
                "duplicate_company_ids": _list_value(target.get("duplicate_company_ids")),
                "sample_secids": _merge_lists(
                    target.get("sample_secids"),
                    target.get("duplicate_sample_secids"),
                ),
                "sample_bond_names": _merge_lists(
                    target.get("sample_bond_names"),
                    target.get("duplicate_sample_bond_names"),
                ),
                "period_year": "",
                "period_quarter": "0",
                "period_start_date": "",
                "period_end_date": "",
                "published_at": "",
                "document_date": "",
                "currency": "RUB",
                "accounting_standard": "IFRS",
                "consolidation_scope": "consolidated",
                "value_scale": "million",
                "source": "operator_collection",
                "source_url": "",
                "source_file_name": "",
                "source_page": "",
                "source_table": "",
                "source_note": "",
                "report_type": "annual",
                "revenue": "",
                "ebitda": "",
                "net_debt": "",
                "total_debt": "",
                "cash": "",
                "equity": "",
                "short_term_debt": "",
                "operating_cash_flow": "",
                "net_profit": "",
                "interest_expense": "",
                "debt_to_ebitda": "",
                "interest_coverage": "",
                "operator_notes": "",
            }
        )
    return rows


def write_collection_template(
    rows: list[dict[str, Any]],
    path: Path,
    output_format: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=TEMPLATE_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {field: _csv_value(row.get(field)) for field in TEMPLATE_FIELDS}
                )
        return
    if output_format == "json":
        path.write_text(
            json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    raise ValueError(f"unsupported collection template format: {output_format}")


def validate_reviewed_input(
    path: Path,
    format_value: str,
    *,
    apply_mode: bool,
    allow_non_official_source: bool,
) -> dict[str, Any]:
    try:
        raw_rows = load_reviewed_rows(path, format_value)
    except Exception as exc:
        return {
            "status": "failed",
            "total_rows": 0,
            "rows": [],
            "warnings": [],
            "errors": [{"row_index": None, "message": str(exc)}],
        }

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if not raw_rows:
        errors.append({"row_index": None, "message": "reviewed input has no rows"})

    for row_index, raw in enumerate(raw_rows, start=1):
        row, null_warnings = _clean_reviewed_row(raw, row_index)
        warnings.extend(null_warnings)
        row_errors: list[str] = []
        row_warnings: list[str] = []

        canonical_id = _parse_int(row.get("canonical_company_id"))
        if canonical_id is None or canonical_id <= 0:
            row_errors.append("canonical_company_id is required and must be positive")
        row["canonical_company_id"] = canonical_id

        period_year = _parse_int(row.get("period_year"))
        if period_year is None:
            row_errors.append("period_year is required and must be an integer")
        elif period_year < 1900 or period_year > 2100:
            row_errors.append("period_year must be between 1900 and 2100")
        row["period_year"] = period_year

        if not row.get("currency"):
            row_errors.append("currency is required")
        else:
            row["currency"] = str(row["currency"]).upper()

        scale = str(row.get("value_scale") or "").lower()
        if not scale:
            row_errors.append("value_scale is required")
        elif scale not in VALUE_SCALE_FACTORS:
            row_errors.append("value_scale must be raw, thousand, million, or billion")
        row["value_scale"] = scale

        if not row.get("source"):
            row_errors.append("source is required")

        accounting_standard = row.get("accounting_standard") or "unknown"
        if accounting_standard not in ACCOUNTING_STANDARDS:
            row_errors.append("accounting_standard must be IFRS, RAS, management, or unknown")
        row["accounting_standard"] = accounting_standard

        consolidation_scope = row.get("consolidation_scope") or "unknown"
        if consolidation_scope not in CONSOLIDATION_SCOPES:
            row_errors.append("consolidation_scope must be consolidated, standalone, or unknown")
        row["consolidation_scope"] = consolidation_scope

        row_warnings.extend(_source_warnings(row))
        source_non_official = _source_is_non_official(row.get("source_url"))
        if apply_mode and source_non_official and not allow_non_official_source:
            row_errors.append(
                "non-official source is blocked in apply mode; "
                "rerun with --allow-non-official-source to override"
            )

        if not any(row.get(field) not in (None, "") for field in CORE_FINANCIAL_FIELDS):
            row_warnings.append("all major financial values are empty")

        row_warnings.extend(_suspicious_scaled_value_warnings(row))

        for message in row_errors:
            errors.append(_row_message(row, row_index, message))
        for message in row_warnings:
            warnings.append(_row_message(row, row_index, message))
        rows.append(row)

    invalid_indexes = {item["row_index"] for item in errors if item.get("row_index") is not None}
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "total_rows": len(raw_rows),
        "valid_rows": len(raw_rows) - len(invalid_indexes),
        "invalid_rows": len(invalid_indexes),
        "rows": rows,
        "warnings": warnings,
        "errors": errors,
    }


def validate_collection_rows(
    rows: list[dict[str, Any]],
    *,
    apply_mode: bool,
    allow_non_official_source: bool = False,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cleaned_rows: list[dict[str, Any]] = []
    if not rows:
        errors.append({"row_index": None, "message": "reviewed input has no rows"})

    for row_index, raw in enumerate(rows, start=1):
        row, null_warnings = _clean_reviewed_row(raw, row_index)
        warnings.extend(null_warnings)
        row_errors: list[str] = []
        row_warnings: list[str] = []

        canonical_id = _parse_int(row.get("canonical_company_id"))
        if canonical_id is None or canonical_id <= 0:
            row_errors.append("canonical_company_id is required and must be positive")
        row["canonical_company_id"] = canonical_id

        period_year = _parse_int(row.get("period_year"))
        if period_year is None:
            row_errors.append("period_year is required and must be an integer")
        elif period_year < 1900 or period_year > 2100:
            row_errors.append("period_year must be between 1900 and 2100")
        row["period_year"] = period_year

        if not row.get("currency"):
            row_errors.append("currency is required")
        else:
            row["currency"] = str(row["currency"]).upper()

        scale = str(row.get("value_scale") or "").lower()
        if not scale:
            row_errors.append("value_scale is required")
        elif scale not in VALUE_SCALE_FACTORS:
            row_errors.append("value_scale must be raw, thousand, million, or billion")
        row["value_scale"] = scale

        if not row.get("source"):
            row_errors.append("source is required")

        accounting_standard = row.get("accounting_standard") or "unknown"
        if accounting_standard not in ACCOUNTING_STANDARDS:
            row_errors.append("accounting_standard must be IFRS, RAS, management, or unknown")
        row["accounting_standard"] = accounting_standard

        consolidation_scope = row.get("consolidation_scope") or "unknown"
        if consolidation_scope not in CONSOLIDATION_SCOPES:
            row_errors.append("consolidation_scope must be consolidated, standalone, or unknown")
        row["consolidation_scope"] = consolidation_scope

        row_warnings.extend(_source_warnings(row))
        if apply_mode and _source_is_non_official(row.get("source_url")) and not allow_non_official_source:
            row_errors.append(
                "non-official source is blocked in apply mode; "
                "rerun with --allow-non-official-source to override"
            )

        if not any(row.get(field) not in (None, "") for field in CORE_FINANCIAL_FIELDS):
            row_warnings.append("all major financial values are empty")
        row_warnings.extend(_suspicious_scaled_value_warnings(row))

        for message in row_errors:
            errors.append(_row_message(row, row_index, message))
        for message in row_warnings:
            warnings.append(_row_message(row, row_index, message))
        cleaned_rows.append(row)

    invalid_indexes = {item["row_index"] for item in errors if item.get("row_index") is not None}
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "total_rows": len(rows),
        "valid_rows": len(rows) - len(invalid_indexes),
        "invalid_rows": len(invalid_indexes),
        "rows": cleaned_rows,
        "warnings": warnings,
        "errors": errors,
    }


def load_reviewed_rows(path: Path, format_value: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"reviewed input does not exist: {path}")
    if format_value == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]
    if format_value == "json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON input must be a list of row objects or an object with rows")
        return [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]
    raise ValueError(f"unsupported reviewed input format: {format_value}")


load_collection_rows = load_reviewed_rows


def normalize_canonical_rows(
    rows: list[dict[str, Any]],
    output_path: Path | None = None,
    output_format: str = "csv",
) -> dict[str, Any]:
    collection_rows = [_canonical_to_collection_row(row) for row in rows]
    report = normalize_collection_rows(
        collection_rows,
        default_currency="RUB",
        default_source="operator_collection",
        strict=False,
    )
    if output_path is not None and not report.get("errors"):
        write_normalized_rows(report["normalized_rows"], output_path, output_format)
        report["output"] = str(output_path)
        report["output_format"] = output_format
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Canonical Financial Report Pack",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Flow",
        "",
        f"- Mode: `{report.get('mode')}`",
        f"- Source: `{report.get('source', '')}`",
    ]

    target_report = report.get("target_report") or {}
    if target_report:
        lines.extend(
            [
                "",
                "## Canonical Targets",
                "",
                f"- Total targets: {target_report.get('total_targets', 0)}",
                "",
                "| Canonical ID | Canonical issuer | Duplicates | Coverage | Samples |",
                "| ---: | --- | ---: | --- | --- |",
            ]
        )
        for row in target_report.get("targets") or []:
            lines.append(
                "| {company_id} | {name} | {duplicates} | {coverage} | {samples} |".format(
                    company_id=row.get("canonical_company_id") or row.get("company_id") or "",
                    name=row.get("canonical_company_name") or row.get("company_name") or "",
                    duplicates=row.get("duplicate_count") or 0,
                    coverage=row.get("coverage_effective_status") or row.get("coverage_status") or "",
                    samples=_csv_value(row.get("sample_secids")) or "",
                )
            )

    validation = report.get("validation")
    if validation is not None:
        lines.extend(
            [
                "",
                "## Reviewed Input",
                "",
                f"- Rows: {validation.get('total_rows', 0)}",
                f"- Validation status: `{validation.get('status')}`",
            ]
        )
    normalize_report = report.get("normalize_report")
    if normalize_report is not None:
        lines.extend(
            [
                "",
                "## Normalize",
                "",
                f"- Status: `{normalize_report.get('status')}`",
                f"- Rows: {normalize_report.get('total_rows', 0)}",
                f"- Output: `{report.get('normalized_output')}`",
            ]
        )
    dry_run_report = report.get("dry_run_report")
    if dry_run_report is not None:
        lines.extend(
            [
                "",
                "## Import Dry Run",
                "",
                f"- Status: `{dry_run_report.get('status')}`",
            ]
        )
    apply_report = report.get("apply_report")
    if apply_report is not None:
        lines.extend(
            [
                "",
                "## Confirmed Import",
                "",
                f"- Status: `{apply_report.get('status')}`",
            ]
        )

    if report.get("coverage_before") or report.get("coverage_after"):
        before = report.get("coverage_before") or {}
        after = report.get("coverage_after") or {}
        lines.extend(
            [
                "",
                "## Coverage",
                "",
                f"- Before status code: {before.get('status_code')}",
                f"- After status code: {after.get('status_code')}",
            ]
        )

    lines.extend(["", "## Warnings", ""])
    if report.get("warnings"):
        lines.extend(f"- {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    if report.get("errors"):
        lines.extend(f"- {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")

    if report.get("import_executed"):
        lines.extend(["", "## Rollback Note", "", ROLLBACK_NOTE])

    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report.get("next_steps", []))
    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_flow(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-report-canonical-pack] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[financial-report-canonical-pack] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    print(f"[financial-report-canonical-pack] {report['status']}", flush=True)
    return exit_code


def _reviewed_report(
    args: argparse.Namespace,
    *,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    coverage_before: HttpResult | None,
    validation: dict[str, Any] | None,
    normalize_report: dict[str, Any] | None,
    dry_run_report: dict[str, Any] | None,
    apply_report: dict[str, Any] | None,
    coverage_after: HttpResult | None,
    import_executed: bool,
) -> dict[str, Any]:
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": args.mode,
        "source": "operator_collection",
        "reviewed_input": None if args.reviewed_input is None else str(args.reviewed_input),
        "input": None if args.reviewed_input is None else str(args.reviewed_input),
        "normalized_output": str(args.normalized_output),
        "normalized_format": args.normalized_format,
        "execute_import": args.execute_import,
        "confirm_import": args.confirm_import,
        "coverage_before": None if coverage_before is None else _http_report(coverage_before),
        "validation": validation,
        "normalize_report": normalize_report,
        "dry_run_report": dry_run_report,
        "dry_run_import_report": dry_run_report,
        "apply_report": apply_report,
        "ingest_report": apply_report,
        "coverage_after": None if coverage_after is None else _http_report(coverage_after),
        "import_executed": import_executed,
        "rollback_note": ROLLBACK_NOTE if import_executed else None,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(args.mode, executed=import_executed),
    }


def _target_args(
    args: argparse.Namespace,
    source: str,
    selected_ids: list[int],
) -> argparse.Namespace:
    values = [
        "--backend-url",
        args.backend_url,
        "--source",
        source,
        "--limit",
        str(max(int(args.limit or 1), len(selected_ids) + 50, 50)),
    ]
    if args.model_run_id is not None:
        values.extend(["--model-run-id", str(args.model_run_id)])
    if args.as_of_date:
        values.extend(["--as-of-date", args.as_of_date])
    if selected_ids:
        values.extend(["--include-company-ids", ",".join(str(item) for item in selected_ids)])
    if args.use_duplicate_mapping:
        values.append("--use-duplicate-mapping")
    if args.rollup_duplicates:
        values.append("--rollup-duplicates")
    if args.include_duplicate_members:
        values.append("--include-duplicate-members")
    return parse_financial_target_args(values)


def _safe_sources(source: str) -> list[str]:
    if source == "mixed":
        return ["top-predictions", "bond-universe"]
    if source == "paper-positions":
        return []
    return [source]


def _merge_target_row(rows_by_key: dict[int, dict[str, Any]], row: dict[str, Any]) -> None:
    key = int(row.get("canonical_company_id") or row.get("company_id"))
    if key not in rows_by_key:
        rows_by_key[key] = dict(row)
        return
    existing = rows_by_key[key]
    for field in (
        "sample_secids",
        "sample_bond_names",
        "duplicate_company_ids",
        "duplicate_company_names",
        "duplicate_sample_secids",
        "duplicate_sample_bond_names",
    ):
        existing[field] = _merge_lists(existing.get(field), row.get(field))
    existing["bonds_count"] = max(int(existing.get("bonds_count") or 0), int(row.get("bonds_count") or 0))
    existing["duplicate_count"] = max(
        int(existing.get("duplicate_count") or 0),
        int(row.get("duplicate_count") or 0),
    )
    existing["source_reason"] = "; ".join(
        sorted(set(_split_reason(existing.get("source_reason")) + _split_reason(row.get("source_reason"))))
    )
    for field in (
        "company_name",
        "canonical_company_name",
        "company_inn",
        "legal_name",
        "short_name",
        "ogrn",
        "issuer_group_name",
        "issuer_role",
        "coverage_effective_status",
        "coverage_status",
    ):
        existing[field] = existing.get(field) or row.get(field)
    existing["has_financial_report"] = bool(existing.get("has_financial_report")) or bool(row.get("has_financial_report"))
    existing["canonical_has_financial_report"] = bool(existing.get("canonical_has_financial_report")) or bool(
        row.get("canonical_has_financial_report")
    )
    existing["duplicate_has_financial_report"] = bool(existing.get("duplicate_has_financial_report")) or bool(
        row.get("duplicate_has_financial_report")
    )
    existing["needs_financial_report"] = not bool(existing.get("canonical_has_financial_report"))


def _sort_selected_rows(rows: list[dict[str, Any]], selected_ids: list[int]) -> list[dict[str, Any]]:
    order = {company_id: index for index, company_id in enumerate(selected_ids)}

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        ids = [int(row.get("canonical_company_id") or row.get("company_id") or 0)]
        ids.extend(_int_set(row.get("duplicate_company_ids")))
        match_indexes = [order[item] for item in ids if item in order]
        return (min(match_indexes) if match_indexes else len(order), row.get("canonical_company_name") or "")

    return sorted(rows, key=sort_key)


def _resolve_company_names(
    value: str,
    backend_url: str,
    http_request: Any,
) -> list[int]:
    ids: list[int] = []
    backend = backend_url.rstrip("/")
    for name in _parse_str_list(value):
        query = urllib.parse.urlencode({"query": name, "limit": 20})
        result = http_request("GET", f"{backend}/api/companies?{query}", None)
        data = _data_or_raise(result)
        if not isinstance(data, list):
            raise ValueError(f"company name lookup returned unexpected payload for {name}")
        exact = [
            row
            for row in data
            if str(row.get("name") or "").casefold() == name.casefold()
            or str(row.get("ticker") or "").casefold() == name.casefold()
            or str(row.get("inn") or "").casefold() == name.casefold()
        ]
        candidates = exact or data
        if not candidates:
            raise ValueError(f"company name was not found: {name}")
        if len(candidates) > 1:
            labels = ", ".join(f"{item.get('id')} {item.get('name')}" for item in candidates[:5])
            raise ValueError(f"company name lookup is ambiguous for {name}: {labels}")
        company_id = candidates[0].get("id")
        if company_id is None:
            raise ValueError(f"company lookup result has no id for {name}")
        ids.append(int(company_id))
    return ids


def _coverage(backend: str, args: argparse.Namespace, http_request: Any) -> HttpResult:
    params = {
        "active_only": "true",
        "stale_after_days": str(args.stale_after_days),
    }
    if args.as_of_date:
        params["as_of_date"] = args.as_of_date
    query = urllib.parse.urlencode(params)
    result = http_request(
        "GET",
        f"{backend}/api/data-readiness/financial-reports/coverage?{query}",
        None,
    )
    if isinstance(result, HttpResult):
        return result
    return HttpResult(ok=True, status_code=200, data=result)


def _canonical_to_collection_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {
        "company_id": row.get("canonical_company_id"),
        "company_name": row.get("canonical_company_name"),
        "company_ticker": None,
        "company_inn": row.get("inn"),
    }
    for field in (
        "period_year",
        "period_quarter",
        "period_start_date",
        "period_end_date",
        "published_at",
        "document_date",
        "currency",
        "accounting_standard",
        "consolidation_scope",
        "value_scale",
        "source",
        "source_url",
        "source_file_name",
        "source_page",
        "source_table",
        "source_note",
        "report_type",
        *MONEY_FIELDS,
        *RATIO_FIELDS,
    ):
        output[field] = row.get(field)
    return output


def _clean_reviewed_row(raw: dict[str, Any], row_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    row = {field: _normalize_cell(raw.get(field)) for field in TEMPLATE_FIELDS}
    for field, value in list(row.items()):
        if isinstance(value, str) and value.strip().casefold() in NULL_MARKERS:
            row[field] = None
            warnings.append(
                {
                    "row_index": row_index,
                    "canonical_company_id": raw.get("canonical_company_id"),
                    "period_year": raw.get("period_year"),
                    "message": f"{field} placeholder value was treated as empty/null",
                }
            )
    return row, warnings


def _source_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    source_url = row.get("source_url")
    source_file_name = row.get("source_file_name")
    if not source_url:
        warnings.append("source_url is missing")
    if not source_file_name:
        warnings.append("source_file_name is missing")
    if not source_url and not source_file_name:
        warnings.append("at least one source evidence field is recommended")
    if source_url and "wikipedia.org" in str(source_url).casefold():
        warnings.append("source_url contains wikipedia.org; do not use Wikipedia as a financial source")
    elif _source_is_non_official(source_url):
        warnings.append("source_url appears non-official or unrecognized")
    return warnings


def _source_is_non_official(source_url: Any) -> bool:
    if not source_url:
        return False
    text = str(source_url).casefold()
    if "wikipedia.org" in text:
        return True
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.removeprefix("www.")
    return not any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_SOURCE_DOMAIN_HINTS)


def _suspicious_scaled_value_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    scale = str(row.get("value_scale") or "raw").lower()
    factor = VALUE_SCALE_FACTORS.get(scale, Decimal("1"))
    for field in CORE_FINANCIAL_FIELDS:
        value = _parse_decimal(row.get(field))
        if value is None:
            continue
        scaled = value * factor
        if abs(scaled) > Decimal("10000000000000000"):
            warnings.append(f"{field} looks suspiciously large after value_scale={scale}")
        elif Decimal("0") < abs(scaled) < Decimal("1000"):
            warnings.append(f"{field} looks suspiciously small after value_scale={scale}")
    return warnings


def _row_message(row: dict[str, Any], row_index: int | None, message: str) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "canonical_company_id": row.get("canonical_company_id"),
        "period_year": row.get("period_year"),
        "period_quarter": row.get("period_quarter"),
        "message": message,
    }


def _http_report(result: HttpResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status_code": result.status_code,
        "json": result.data,
        "error": result.error,
    }


def _data_or_raise(result: Any) -> Any:
    if isinstance(result, HttpResult):
        if not result.ok:
            raise ValueError(result.error or result.text or "request failed")
        return result.data
    return result


def _infer_format(path: Path, default: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    return default


def _next_steps(mode: str, *, executed: bool) -> list[str]:
    if mode == "targets":
        return ["Generate a reviewed canonical collection template."]
    if mode == "template":
        return ["Fill the template manually from official issuer reports, then run preview."]
    if mode == "preview":
        return ["Review warnings and backend preview before any confirmed import."]
    if executed:
        return ["Re-run coverage and plan downstream rebuilds separately."]
    return ["Fix validation or dry-run errors before any confirmed import."]


def _parse_str_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _parse_int_list(value: str) -> list[int]:
    result: list[int] = []
    for item in _parse_str_list(value):
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


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


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(";") if item.strip()]
    return [value]


def _int_set(value: Any) -> set[int]:
    result: set[int] = set()
    for item in _list_value(value):
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _merge_lists(left: Any, right: Any) -> list[Any]:
    merged: list[Any] = []
    for value in _list_value(left) + _list_value(right):
        if value not in (None, "") and value not in merged:
            merged.append(value)
    return merged


def _split_reason(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


if __name__ == "__main__":
    sys.exit(main())
