from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.moex_iss_client import (  # noqa: E402
    MoexCashflowScheduleResult,
    MoexIssClient,
)
from app.services.moex_normalization import canonicalize_moex_currency  # noqa: E402


SCHEMA = "bondradar.moex_security_master_source_probe.v1"
MAX_SECIDS = 100
SECID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
FAILURE_CODE = "moex_security_master_source_probe_failed"

RAW_KEY_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "issuer_name": (
        "ISSUER_NAME",
        "EMITENT_TITLE",
        "EMITENTNAME",
        "EMITENT_FULL_NAME",
        "ISSUER",
    ),
    "issuer_inn": ("ISSUER_INN", "EMITENT_INN", "INN"),
    "currency": ("CURRENCY", "CURRENCYID", "FACEUNIT"),
    "maturity": ("MATURITY_DATE", "MATDATE", "MATURITYDATE"),
    "offer": ("OFFER_DATE", "OFFERDATE"),
    "amortization": (
        "HAS_AMORTIZATION",
        "AMORTIZATION",
        "AMORTIZED",
    ),
    "floating_coupon": (
        "IS_FLOATING_COUPON",
        "FLOATING_COUPON",
        "FLOATING",
        "COUPON_TYPE",
        "COUPONTYPE",
        "COUPONKIND",
    ),
    "subordinated": ("IS_SUBORDINATED", "SUBORDINATED"),
    "perpetual": ("IS_PERPETUAL", "PERPETUAL"),
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe narrow MOEX security-master source evidence without a DB.",
    )
    parser.add_argument("--secid", action="append", default=[])
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def load_secids(values: Sequence[str], input_path: Path | None = None) -> list[str]:
    combined: list[Any] = list(values)
    if input_path is not None:
        text = input_path.read_text(encoding="utf-8")
        stripped = text.strip()
        if stripped.startswith("["):
            loaded = json.loads(stripped)
            if not isinstance(loaded, list) or not all(
                isinstance(item, str) for item in loaded
            ):
                raise ValueError("invalid_secid_input")
            combined.extend(loaded)
        else:
            combined.extend(line for line in text.splitlines() if line.strip())

    normalized: list[str] = []
    seen: set[str] = set()
    for value in combined:
        secid = str(value).strip().upper()
        if not SECID_PATTERN.fullmatch(secid):
            raise ValueError("invalid_secid_input")
        if secid not in seen:
            seen.add(secid)
            normalized.append(secid)
    if not normalized:
        raise ValueError("secid_input_required")
    if len(normalized) > MAX_SECIDS:
        raise ValueError("secid_limit_exceeded")
    return normalized


def build_probe_report(
    client: MoexIssClient,
    secids: Sequence[str],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    results = [_probe_secid(client, secid) for secid in secids]
    results.sort(key=lambda row: row["secid"])
    return {
        "schema": SCHEMA,
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "secids_requested": len(secids),
        "secids_processed": len(results),
        "description_success_count": _count_status(
            results, "description_fetch_status", "success"
        ),
        "description_failure_count": _count_status(
            results, "description_fetch_status", "failed"
        ),
        "cashflow_success_count": _count_status(
            results, "cashflow_fetch_status", "success"
        ),
        "cashflow_failure_count": _count_status(
            results, "cashflow_fetch_status", "failed"
        ),
        "currency_sur_count": _count_status(
            results, "currency_source_class", "SUR"
        ),
        "currency_rub_count": _count_status(
            results, "currency_source_class", "RUB"
        ),
        "currency_foreign_count": _count_status(
            results, "currency_source_class", "FOREIGN"
        ),
        "currency_missing_count": _count_status(
            results, "currency_source_class", "MISSING"
        ),
        "currency_invalid_count": _count_status(
            results, "currency_source_class", "INVALID"
        ),
        "issuer_name_present_count": sum(
            row["issuer_name_present"] for row in results
        ),
        "issuer_inn_present_count": sum(
            row["issuer_inn_present"] for row in results
        ),
        "explicit_amortization_field_present_count": sum(
            row["explicit_amortization_field_present"] for row in results
        ),
        "amortization_rows_present_count": sum(
            row["amortization_rows"] > 0 for row in results
        ),
        "explicit_floating_field_present_count": sum(
            row["explicit_floating_coupon_field_present"] for row in results
        ),
        "explicit_subordinated_field_present_count": sum(
            row["explicit_subordinated_field_present"] for row in results
        ),
        "explicit_perpetual_field_present_count": sum(
            row["explicit_perpetual_field_present"] for row in results
        ),
        "redemption_table_present_count": sum(
            row["redemption_table_present"] for row in results
        ),
        "redemption_rows_present_count": sum(
            row["redemption_rows"] > 0 for row in results
        ),
        "warnings_count": sum(len(row["warnings"]) for row in results),
        "current_floating_classification_trusted": False,
        "database_accessed": False,
        "redemption_synthesized": False,
        "results": results,
    }


def _probe_secid(client: MoexIssClient, secid: str) -> dict[str, Any]:
    result = _empty_result(secid)
    description: dict[str, Any] = {}
    try:
        description, description_warnings = client.fetch_bond_description(secid)
        result["description_fetch_status"] = "success"
        if description_warnings:
            result["warnings"].append("DESCRIPTION_SOURCE_WARNING")
        _apply_description(result, description)
    except Exception:
        result["description_fetch_status"] = "failed"
        result["warnings"].append("DESCRIPTION_FETCH_FAILED")

    schedule: MoexCashflowScheduleResult | None = None
    try:
        schedule = client.fetch_bond_cashflows(secid)
        result["cashflow_fetch_status"] = "success"
        _apply_cashflows(result, schedule)
    except Exception:
        result["cashflow_fetch_status"] = "failed"
        result["warnings"].append("CASHFLOW_FETCH_FAILED")

    explicit_amortization = _safe_bool(result["explicit_amortization_value"])
    if explicit_amortization is True or result["amortization_rows"] > 0:
        result["amortization_evidence_state"] = "AMORTIZATION_POSITIVE_EVIDENCE"
    elif explicit_amortization is False:
        result["amortization_evidence_state"] = (
            "AMORTIZATION_EXPLICIT_NEGATIVE_EVIDENCE"
        )
    else:
        result["amortization_evidence_state"] = "AMORTIZATION_NOT_PROVEN"
    return result


def _empty_result(secid: str) -> dict[str, Any]:
    return {
        "secid": secid,
        "description_fetch_status": "failed",
        "cashflow_fetch_status": "failed",
        "issuer_name_present": False,
        "issuer_name_value": None,
        "issuer_inn_present": False,
        "issuer_inn_value": None,
        "raw_currency_present": False,
        "raw_currency_value": None,
        "canonical_currency": None,
        "currency_source_class": "NOT_OBSERVED",
        "maturity_date_present": False,
        "maturity_date_value": None,
        "offer_date_present": False,
        "offer_date_value": None,
        "explicit_amortization_field_present": False,
        "explicit_amortization_value": None,
        "explicit_floating_coupon_field_present": False,
        "explicit_floating_coupon_value": None,
        "explicit_subordinated_field_present": False,
        "explicit_subordinated_value": None,
        "explicit_perpetual_field_present": False,
        "explicit_perpetual_value": None,
        "coupon_rows": 0,
        "amortization_rows": 0,
        "offer_rows": 0,
        "redemption_rows": 0,
        "coupon_table_present": False,
        "amortization_table_present": False,
        "offer_table_present": False,
        "redemption_table_present": False,
        "coupon_source_tables": [],
        "amortization_source_tables": [],
        "offer_source_tables": [],
        "redemption_source_tables": [],
        "amortization_evidence_state": "AMORTIZATION_NOT_PROVEN",
        "redemption_source_state": "REDEMPTION_SOURCE_NOT_OBSERVED",
        "current_floating_classification_trusted": False,
        "candidate_raw_keys": {
            concept: [] for concept in RAW_KEY_ALLOWLISTS
        },
        "warnings": [],
    }


def _apply_description(result: dict[str, Any], description: dict[str, Any]) -> None:
    raw = description.get("raw")
    raw_dict = raw if isinstance(raw, dict) else {}
    evidence = {
        concept: _candidate_evidence(raw_dict, aliases)
        for concept, aliases in RAW_KEY_ALLOWLISTS.items()
    }
    result["candidate_raw_keys"] = evidence

    _assign_value(result, "issuer_name", description.get("issuer_name"))
    _assign_value(result, "issuer_inn", description.get("issuer_inn"))
    _assign_value(result, "maturity_date", description.get("maturity_date"))
    _assign_value(result, "offer_date", description.get("offer_date"))

    raw_currency = _safe_scalar(description.get("currency"))
    result["raw_currency_present"] = _has_value(raw_currency)
    result["raw_currency_value"] = raw_currency
    result["canonical_currency"] = canonicalize_moex_currency(raw_currency)
    result["currency_source_class"] = _currency_source_class(raw_currency)

    _assign_explicit(
        result,
        output_prefix="amortization",
        normalized_value=description.get("has_amortization"),
        evidence=evidence["amortization"],
    )
    _assign_explicit(
        result,
        output_prefix="floating_coupon",
        normalized_value=description.get("is_floating_coupon"),
        evidence=evidence["floating_coupon"],
    )
    _assign_explicit(
        result,
        output_prefix="subordinated",
        normalized_value=description.get("is_subordinated"),
        evidence=evidence["subordinated"],
    )
    _assign_explicit(
        result,
        output_prefix="perpetual",
        normalized_value=description.get("is_perpetual"),
        evidence=evidence["perpetual"],
    )


def _apply_cashflows(
    result: dict[str, Any],
    schedule: MoexCashflowScheduleResult,
) -> None:
    groups = (
        ("coupon", "coupons"),
        ("amortization", "amortizations"),
        ("offer", "offers"),
        ("redemption", "redemptions"),
    )
    warning_text = [str(value).lower() for value in schedule.warnings]
    for output_name, schedule_name in groups:
        rows = getattr(schedule, schedule_name)
        result[f"{output_name}_rows"] = len(rows)
        result[f"{output_name}_source_tables"] = sorted(
            {
                str(row.get("__moex_source_table") or schedule_name)
                for row in rows
            }
        )
        missing_marker = f"cashflow table {schedule_name} is missing"
        result[f"{output_name}_table_present"] = bool(rows) or not any(
            missing_marker in warning for warning in warning_text
        )

    if not result["redemption_table_present"]:
        result["redemption_source_state"] = "REDEMPTION_SOURCE_NOT_OBSERVED"
        result["warnings"].append("REDEMPTION_SOURCE_NOT_OBSERVED")
    elif result["redemption_rows"]:
        result["redemption_source_state"] = "REDEMPTION_SOURCE_ROWS_OBSERVED"
    else:
        result["redemption_source_state"] = "REDEMPTION_SOURCE_TABLE_EMPTY"
    if schedule.warnings and not result["warnings"]:
        result["warnings"].append("CASHFLOW_SOURCE_WARNING")


def _candidate_evidence(
    raw: dict[str, Any],
    aliases: tuple[str, ...],
) -> list[dict[str, Any]]:
    allowed = set(aliases)
    rows: list[dict[str, Any]] = []
    for key, value in raw.items():
        normalized_key = str(key).strip().upper()
        if normalized_key not in allowed:
            continue
        rows.append({"key": normalized_key, "value": _safe_scalar(value)})
    return sorted(rows, key=lambda row: row["key"])


def _assign_value(result: dict[str, Any], prefix: str, value: Any) -> None:
    safe_value = _safe_scalar(value)
    result[f"{prefix}_present"] = _has_value(safe_value)
    result[f"{prefix}_value"] = safe_value


def _assign_explicit(
    result: dict[str, Any],
    *,
    output_prefix: str,
    normalized_value: Any,
    evidence: list[dict[str, Any]],
) -> None:
    if _has_value(normalized_value):
        value = _safe_scalar(normalized_value)
        present = True
    elif evidence:
        value = evidence[0]["value"]
        present = True
    else:
        value = None
        present = False
    result[f"explicit_{output_prefix}_field_present"] = present
    result[f"explicit_{output_prefix}_value"] = value


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    return str(value)[:200]


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower() if value is not None else ""
    if text in {"1", "true", "yes", "y", "да"}:
        return True
    if text in {"0", "false", "no", "n", "нет"}:
        return False
    return None


def _currency_source_class(value: Any) -> str:
    if not _has_value(value):
        return "MISSING"
    raw = str(value).strip().upper()
    canonical = canonicalize_moex_currency(raw)
    if canonical is None:
        return "INVALID"
    if raw == "SUR":
        return "SUR"
    if raw == "RUB":
        return "RUB"
    return "FOREIGN"


def _count_status(
    results: list[dict[str, Any]],
    field: str,
    expected: str,
) -> int:
    return sum(row[field] == expected for row in results)


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def render_markdown(report: dict[str, Any]) -> str:
    summary_fields = [
        key for key in report if key not in {"results"}
    ]
    lines = [
        "# BondRadar MOEX Security-Master Source Probe",
        "",
        "```json",
        json.dumps(
            {key: report[key] for key in summary_fields},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Results",
        "",
        "```json",
        json.dumps(report["results"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        secids = load_secids(args.secid, args.input)
        report = build_probe_report(MoexIssClient(), secids)
        rendered = serialize_report(report, args.format)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    except Exception:
        failure = {
            "schema": SCHEMA,
            "status": "failed",
            "error": FAILURE_CODE,
        }
        sys.stderr.write(json.dumps(failure, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
