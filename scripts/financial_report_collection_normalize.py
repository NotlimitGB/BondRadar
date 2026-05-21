from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from financial_report_import import SUPPORTED_FIELDS, write_json_report


COLLECTION_FIELDS = [
    "company_id",
    "company_name",
    "company_ticker",
    "company_inn",
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
]
MONEY_FIELDS = (
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
)
RATIO_FIELDS = ("debt_to_ebitda", "interest_coverage")
VALUE_SCALE_FACTORS = {
    "raw": Decimal("1"),
    "thousand": Decimal("1000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
}
FAIL_NEGATIVE_FIELDS = {"revenue", "cash", "total_debt", "short_term_debt"}
WARN_NEGATIVE_FIELDS = {"equity", "net_profit"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize operator-collected financial reports into ingest-ready rows.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("csv", "json"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-format", choices=("csv", "json"), required=True)
    parser.add_argument("--default-currency", default="RUB")
    parser.add_argument("--default-source", default="operator_collection")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json-report", type=Path, default=None)
    parser.add_argument("--markdown-report", type=Path, default=None)
    return parser.parse_args(argv)


def load_collection_rows(path: Path, format_value: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    if format_value == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [
                {key: _normalize_value(value) for key, value in row.items() if key}
                for row in reader
            ]
    if format_value == "json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("JSON input must be a list of rows or an object with rows")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("JSON rows must be objects")
        return [{str(key): _normalize_value(value) for key, value in row.items()} for row in rows]
    raise ValueError(f"unsupported format: {format_value}")


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    default_currency: str,
    default_source: str,
    strict: bool = False,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []

    if not rows:
        errors.append({"row_index": None, "message": "collection file has no rows"})

    for row_index, raw in enumerate(rows, start=1):
        row = {field: _normalize_value(raw.get(field)) for field in COLLECTION_FIELDS}
        row_errors: list[str] = []
        row_warnings: list[str] = []

        if not any(row.get(field) for field in ("company_id", "company_ticker", "company_inn")):
            row_errors.append("company identifier is required")

        period_year = _parse_int(row.get("period_year"))
        if period_year is None:
            row_errors.append("period_year is required and must be an integer")
        elif period_year < 1900 or period_year > 2100:
            row_errors.append("period_year must be between 1900 and 2100")

        period_quarter = _parse_int(row.get("period_quarter"), default=0)
        if period_quarter is None or period_quarter < 0 or period_quarter > 4:
            row_errors.append("period_quarter must be between 0 and 4")

        currency = str(row.get("currency") or default_currency).upper()
        if len(currency) != 3 or not currency.isalpha():
            row_errors.append("currency must be a 3-letter code")

        scale = str(row.get("value_scale") or "raw").lower()
        factor = VALUE_SCALE_FACTORS.get(scale)
        if factor is None:
            row_errors.append("value_scale must be raw, thousand, million, or billion")
            factor = Decimal("1")

        output = {field: None for field in SUPPORTED_FIELDS}
        for field in ("company_id", "company_ticker", "company_inn"):
            output[field] = row.get(field)
        for field in (
            "period_year",
            "period_quarter",
            "period_start_date",
            "period_end_date",
            "published_at",
            "document_date",
            "source_url",
            "source_file_name",
            "report_type",
        ):
            output[field] = row.get(field)
        output["currency"] = currency
        output["source"] = row.get("source") or default_source

        money_values: dict[str, Decimal | None] = {}
        for field in MONEY_FIELDS:
            parsed = _parse_decimal(row.get(field))
            if row.get(field) not in (None, "") and parsed is None:
                row_errors.append(f"invalid decimal value for {field}")
                money_values[field] = None
                continue
            value = None if parsed is None else parsed * factor
            money_values[field] = value
            output[field] = _decimal_to_string(value)
            if value is None:
                continue
            if field in FAIL_NEGATIVE_FIELDS and value < 0:
                row_errors.append(f"{field} cannot be negative")
            elif field in WARN_NEGATIVE_FIELDS and value < 0:
                row_warnings.append(f"{field} is negative")

        for field in ("ebitda", "interest_expense", "total_debt"):
            if money_values.get(field) is None:
                row_warnings.append(f"{field} is missing")
        if money_values.get("ebitda") is not None and money_values["ebitda"] <= 0:
            row_warnings.append("ebitda is non-positive")
        if (
            money_values.get("interest_expense") is not None
            and money_values["interest_expense"] <= 0
        ):
            row_warnings.append("interest_expense is non-positive")

        computed_ratios = _computed_ratios(money_values)
        for field in RATIO_FIELDS:
            provided = _parse_decimal(row.get(field))
            if row.get(field) not in (None, "") and provided is None:
                row_errors.append(f"invalid decimal value for {field}")
                output[field] = None
                continue
            computed = computed_ratios.get(field)
            if provided is None and computed is not None:
                output[field] = _decimal_to_string(computed)
            else:
                output[field] = _decimal_to_string(provided)
                if provided is not None and computed is not None and _ratio_conflicts(provided, computed):
                    row_warnings.append(
                        f"{field} differs from computed value { _decimal_to_string(computed) }"
                    )

        metadata_rows.append(
            {
                "row_index": row_index,
                "company_name": row.get("company_name"),
                "accounting_standard": row.get("accounting_standard"),
                "consolidation_scope": row.get("consolidation_scope"),
                "value_scale": scale,
                "source_page": row.get("source_page"),
                "source_table": row.get("source_table"),
                "source_note": row.get("source_note"),
            }
        )
        for message in row_errors:
            errors.append(_message(row, row_index, message))
        for message in row_warnings:
            warnings.append(_message(row, row_index, message))
        normalized.append(output)

    status = "failed" if errors or (strict and warnings) else "warning" if warnings else "passed"
    return {
        "status": status,
        "total_rows": len(rows),
        "normalized_rows": normalized,
        "collection_metadata": metadata_rows,
        "warnings": warnings,
        "errors": errors,
        "strict": strict,
    }


def write_normalized_rows(rows: list[dict[str, Any]], path: Path, output_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUPPORTED_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: "" if row.get(field) is None else row.get(field) for field in SUPPORTED_FIELDS})
        return
    if output_format == "json":
        path.write_text(
            json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    raise ValueError(f"unsupported output format: {output_format}")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Financial Report Collection Normalize",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Summary",
        "",
        f"- Total rows: {report['total_rows']}",
        f"- Warnings: {len(report['warnings'])}",
        f"- Errors: {len(report['errors'])}",
        "",
        "## Warnings",
        "",
    ]
    if report["warnings"]:
        lines.extend(f"- row {item.get('row_index')}: {item.get('message')}" for item in report["warnings"])
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    if report["errors"]:
        lines.extend(f"- row {item.get('row_index')}: {item.get('message')}" for item in report["errors"])
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows = load_collection_rows(args.input, args.format)
        report = normalize_rows(
            rows,
            default_currency=args.default_currency,
            default_source=args.default_source,
            strict=args.strict,
        )
        if not report["errors"]:
            write_normalized_rows(report["normalized_rows"], args.output, args.output_format)
            report["output"] = str(args.output)
    except Exception as exc:
        report = {
            "status": "failed",
            "total_rows": 0,
            "normalized_rows": [],
            "collection_metadata": [],
            "warnings": [],
            "errors": [{"row_index": None, "message": str(exc)}],
            "strict": args.strict,
        }
    if args.json_report is not None:
        write_json_report(report, args.json_report)
        print(f"[financial-report-normalize] wrote JSON report: {args.json_report}", flush=True)
    if args.markdown_report is not None:
        write_markdown_report(report, args.markdown_report)
        print(f"[financial-report-normalize] wrote Markdown report: {args.markdown_report}", flush=True)
    print(f"[financial-report-normalize] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _computed_ratios(values: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
    return {
        "debt_to_ebitda": _safe_divide(values.get("total_debt"), values.get("ebitda")),
        "interest_coverage": _safe_divide(values.get("ebitda"), values.get("interest_expense")),
    }


def _safe_divide(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return (numerator / denominator).quantize(Decimal("0.000001"))


def _ratio_conflicts(provided: Decimal, computed: Decimal) -> bool:
    diff = abs(provided - computed)
    if diff <= Decimal("0.05"):
        return False
    denominator = abs(computed)
    if denominator == 0:
        return diff > Decimal("0.05")
    return diff / denominator > Decimal("0.05")


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _decimal_to_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _message(row: dict[str, Any], row_index: int, message: str) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "company_id": row.get("company_id"),
        "company_ticker": row.get("company_ticker"),
        "company_inn": row.get("company_inn"),
        "period_year": row.get("period_year"),
        "period_quarter": row.get("period_quarter"),
        "message": message,
    }


if __name__ == "__main__":
    sys.exit(main())
