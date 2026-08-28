from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.services.moex_iss_client import MoexIssClient  # noqa: E402
from app.services.moex_issuer_identity_source_service import (  # noqa: E402
    MoexIssuerIdentitySourceResolution,
    MoexIssuerIdentitySourceService,
)


SCHEMA = "bondradar.moex_issuer_identity_source_probe.v1"
FAILURE_CODE = "moex_issuer_identity_source_probe_failed"
SECID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
MAX_SECIDS = 100
SUCCESS_STATUSES = {
    "EXACT_SECID",
    "EXACT_SECID_ISIN_CORROBORATED",
    "EXACT_ISIN_RECOVERED",
}


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid_probe_arguments")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _SanitizedArgumentParser(
        description="Probe official MOEX issuer reference coverage read-only.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--secid", action="append", default=[])
    mode.add_argument("--db-coverage", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.sample_limit <= 100:
        raise ValueError("invalid_sample_limit")
    args.secid = normalize_secids(args.secid)
    return args


def normalize_secids(values: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        secid = str(value).strip().upper()
        if not SECID_PATTERN.fullmatch(secid):
            raise ValueError("invalid_secid")
        if secid not in seen:
            seen.add(secid)
            normalized.append(secid)
    if len(normalized) > MAX_SECIDS:
        raise ValueError("secid_limit_exceeded")
    return normalized


def build_explicit_probe_report(
    client: MoexIssClient,
    secids: Sequence[str],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = normalize_secids(secids)
    if not normalized:
        raise ValueError("secid_input_required")
    service = MoexIssuerIdentitySourceService(client)
    resolutions = [
        service.lookup(requested_secid=secid)
        for secid in normalized
    ]
    resolutions.sort(key=lambda row: row.requested_secid or "")
    status_counts = Counter(row.security_match_status for row in resolutions)
    return {
        "schema": SCHEMA,
        "generated_at": _generated_at(generated_at),
        "mode": "explicit_security",
        "status": "completed",
        "summary": {
            "securities_requested": len(normalized),
            "securities_processed": len(resolutions),
            "exact_security_matches": sum(
                row.security_match_status in SUCCESS_STATUSES for row in resolutions
            ),
            "source_errors": status_counts["SOURCE_ERROR"],
            "security_status_counts": _sorted_counter(status_counts),
            "issuer_complete": sum(
                row.issuer_metadata_status == "ISSUER_COMPLETE"
                for row in resolutions
            ),
            "issuer_partial": sum(
                row.issuer_metadata_status == "ISSUER_PARTIAL"
                for row in resolutions
            ),
            "issuer_missing": sum(
                row.issuer_metadata_status == "ISSUER_MISSING"
                for row in resolutions
            ),
        },
        "results": [asdict(row) for row in resolutions],
        "read_only": True,
        "database_accessed": False,
        "database_mutation_executed": False,
        "identity_verified": False,
        "identity_applied": False,
        "task243_started": False,
    }


def build_db_coverage_report(
    db: Session,
    client: MoexIssClient,
    *,
    sample_limit: int = 20,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if not 1 <= sample_limit <= 100:
        raise ValueError("invalid_sample_limit")
    rows = _load_bond_identity_rows(db)
    service = MoexIssuerIdentitySourceService(client)
    status_counts: Counter[str] = Counter()
    issuer_status_counts: Counter[str] = Counter()
    currency_counts: Counter[str] = Counter()
    isin_prefix_counts: Counter[str] = Counter()
    issuer_ids: set[str] = set()
    issuer_inns: set[str] = set()
    counters: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = {
        "not_found": [],
        "ambiguous": [],
        "identifier_conflicts": [],
        "missing_issuer_metadata": [],
        "company_inn_mismatches": [],
        "placeholder_recovery_candidates": [],
    }

    for row in rows:
        secid = _clean(row["secid"], upper=True)
        isin = _clean(row["isin"], upper=True)
        currency = _clean(row["currency"], upper=True) or "MISSING"
        currency_counts[currency] += 1
        isin_prefix_counts[_isin_prefix(isin)] += 1
        counters["bonds_with_secid"] += secid is not None
        counters["bonds_with_isin"] += isin is not None
        counters["company_identity_profiles"] += row["identity_profile_id"] is not None
        counters["company_inn_populated"] += _clean(row["company_inn"]) is not None
        is_placeholder = str(row["company_name"] or "").startswith(
            "Unknown issuer for "
        )
        counters["placeholder_companies"] += is_placeholder

        resolution = service.lookup(
            requested_secid=secid,
            expected_isin=isin,
        )
        status_counts[resolution.security_match_status] += 1
        issuer_status_counts[resolution.issuer_metadata_status] += 1
        counters["source_queries"] += resolution.source_query_count
        counters["exact_security_matches"] += (
            resolution.security_match_status in SUCCESS_STATUSES
        )
        counters["exact_secid_matches"] += resolution.security_match_status in {
            "EXACT_SECID",
            "EXACT_SECID_ISIN_CORROBORATED",
        }
        counters["exact_secid_isin_corroborated"] += (
            resolution.security_match_status == "EXACT_SECID_ISIN_CORROBORATED"
        )
        counters["exact_isin_recovered"] += (
            resolution.security_match_status == "EXACT_ISIN_RECOVERED"
        )
        for field_name, counter_name in (
            ("issuer_id", "issuer_id_populated"),
            ("issuer_title", "issuer_title_populated"),
            ("issuer_inn", "issuer_inn_populated"),
            ("issuer_okpo", "issuer_okpo_populated"),
        ):
            counters[counter_name] += getattr(resolution, field_name) is not None
        counters["issuer_id_title_inn_populated"] += all(
            value is not None
            for value in (
                resolution.issuer_id,
                resolution.issuer_title,
                resolution.issuer_inn,
            )
        )
        if resolution.issuer_id is not None:
            issuer_ids.add(resolution.issuer_id)
        if resolution.issuer_inn is not None:
            issuer_inns.add(resolution.issuer_inn)

        company_inn = _clean(row["company_inn"])
        profile_inn = _clean(row["identity_profile_inn"])
        if resolution.issuer_inn is not None and company_inn is not None:
            agreement = resolution.issuer_inn == company_inn
            counters["moex_company_inn_agreements"] += agreement
            counters["moex_company_inn_mismatches"] += not agreement
        if resolution.issuer_inn is not None and profile_inn is not None:
            agreement = resolution.issuer_inn == profile_inn
            counters["moex_profile_inn_agreements"] += agreement
            counters["moex_profile_inn_mismatches"] += not agreement

        sample = _sample_row(row, resolution)
        if resolution.security_match_status == "SECURITY_NOT_FOUND":
            samples["not_found"].append(sample)
        elif resolution.security_match_status == "SECURITY_AMBIGUOUS":
            samples["ambiguous"].append(sample)
        elif resolution.security_match_status == "SECURITY_IDENTIFIER_CONFLICT":
            samples["identifier_conflicts"].append(sample)
        if (
            resolution.security_match_status in SUCCESS_STATUSES
            and resolution.issuer_metadata_status != "ISSUER_COMPLETE"
        ):
            samples["missing_issuer_metadata"].append(sample)
        if (
            resolution.issuer_inn is not None
            and company_inn is not None
            and resolution.issuer_inn != company_inn
        ):
            samples["company_inn_mismatches"].append(sample)
        if (
            is_placeholder
            and resolution.security_match_status in SUCCESS_STATUSES
            and resolution.issuer_metadata_status != "ISSUER_MISSING"
        ):
            samples["placeholder_recovery_candidates"].append(sample)

    return {
        "schema": SCHEMA,
        "generated_at": _generated_at(generated_at),
        "mode": "db_coverage",
        "status": "completed",
        "summary": {
            "total_bonds": len(rows),
            "bonds_with_secid": counters["bonds_with_secid"],
            "bonds_with_isin": counters["bonds_with_isin"],
            "exact_security_matches": counters["exact_security_matches"],
            "exact_secid_matches": counters["exact_secid_matches"],
            "exact_secid_isin_corroborated": counters[
                "exact_secid_isin_corroborated"
            ],
            "exact_isin_recovered": counters["exact_isin_recovered"],
            "identifier_conflicts": status_counts[
                "SECURITY_IDENTIFIER_CONFLICT"
            ],
            "ambiguous_matches": status_counts["SECURITY_AMBIGUOUS"],
            "not_found": status_counts["SECURITY_NOT_FOUND"],
            "source_errors": status_counts["SOURCE_ERROR"],
            "issuer_id_populated": counters["issuer_id_populated"],
            "issuer_title_populated": counters["issuer_title_populated"],
            "issuer_inn_populated": counters["issuer_inn_populated"],
            "issuer_okpo_populated": counters["issuer_okpo_populated"],
            "issuer_id_title_inn_populated": counters[
                "issuer_id_title_inn_populated"
            ],
            "unique_issuer_ids": len(issuer_ids),
            "unique_issuer_inns": len(issuer_inns),
            "placeholder_companies": counters["placeholder_companies"],
            "company_inn_populated": counters["company_inn_populated"],
            "company_identity_profiles": counters["company_identity_profiles"],
            "moex_company_inn_agreements": counters[
                "moex_company_inn_agreements"
            ],
            "moex_company_inn_mismatches": counters[
                "moex_company_inn_mismatches"
            ],
            "moex_profile_inn_agreements": counters[
                "moex_profile_inn_agreements"
            ],
            "moex_profile_inn_mismatches": counters[
                "moex_profile_inn_mismatches"
            ],
            "source_queries": counters["source_queries"],
            "security_status_counts": _sorted_counter(status_counts),
            "issuer_metadata_status_counts": _sorted_counter(
                issuer_status_counts
            ),
            "nominal_currency_counts": _sorted_counter(currency_counts),
            "isin_country_prefix_counts": _sorted_counter(isin_prefix_counts),
        },
        "samples": {
            key: sorted(value, key=_sample_sort_key)[:sample_limit]
            for key, value in samples.items()
        },
        "read_only": True,
        "database_accessed": True,
        "database_mutation_executed": False,
        "identity_verified": False,
        "identity_applied": False,
        "task243_started": False,
    }


def _load_bond_identity_rows(db: Session) -> list[Any]:
    from app.models.bond import Bond
    from app.models.company import Company
    from app.models.company_identity_profile import CompanyIdentityProfile

    statement = (
        select(
            Bond.id.label("bond_id"),
            Bond.secid,
            Bond.isin,
            Bond.currency,
            Bond.company_id,
            Company.name.label("company_name"),
            Company.inn.label("company_inn"),
            CompanyIdentityProfile.id.label("identity_profile_id"),
            CompanyIdentityProfile.inn.label("identity_profile_inn"),
            CompanyIdentityProfile.identity_status,
            CompanyIdentityProfile.review_status,
        )
        .select_from(Bond)
        .outerjoin(Company, Company.id == Bond.company_id)
        .outerjoin(
            CompanyIdentityProfile,
            CompanyIdentityProfile.company_id == Company.id,
        )
        .order_by(Bond.id.asc())
    )
    return list(db.execute(statement).mappings())


def enforce_read_only_transaction(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    db.execute(text("SET TRANSACTION READ ONLY"))
    value = db.execute(text("SHOW transaction_read_only")).scalar_one()
    if str(value).lower() not in {"on", "true", "1"}:
        raise RuntimeError("read_only_transaction_not_enforced")
    return True


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MOEX Legal Issuer Identity Source Probe",
        "",
        f"Schema: `{report['schema']}`",
        f"Mode: `{report['mode']}`",
        f"Generated at: `{report['generated_at']}`",
        "",
        (
            "Issuer fields are observed official-source facts, "
            "not verified legal identity."
        ),
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    detail_key = "results" if report["mode"] == "explicit_security" else "samples"
    lines.extend(
        (
            f"## {detail_key.title()}",
            "",
            "```json",
            json.dumps(
                report[detail_key],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        )
    )
    return "\n".join(lines)


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        client = MoexIssClient()
        if args.db_coverage:
            from app.db.session import SessionLocal

            with SessionLocal() as db:
                try:
                    enforce_read_only_transaction(db)
                    report = build_db_coverage_report(
                        db,
                        client,
                        sample_limit=args.sample_limit,
                    )
                finally:
                    db.rollback()
        else:
            report = build_explicit_probe_report(client, args.secid)
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


def _sample_row(
    row: Any,
    resolution: MoexIssuerIdentitySourceResolution,
) -> dict[str, Any]:
    isin = _clean(row["isin"], upper=True)
    return {
        "bond_id": int(row["bond_id"]),
        "secid": _clean(row["secid"], upper=True),
        "isin": isin,
        "isin_country_prefix": _isin_prefix(isin),
        "nominal_currency": _clean(row["currency"], upper=True),
        "company_id": int(row["company_id"]),
        "identity_status": row["identity_status"],
        "review_status": row["review_status"],
        "security_match_status": resolution.security_match_status,
        "issuer_metadata_status": resolution.issuer_metadata_status,
        "matched_secid": resolution.matched_secid,
        "matched_isin": resolution.matched_isin,
        "issuer_id": resolution.issuer_id,
        "issuer_title": resolution.issuer_title,
        "issuer_inn": resolution.issuer_inn,
        "issuer_okpo": resolution.issuer_okpo,
    }


def _generated_at(value: datetime | None) -> str:
    generated = value or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated.astimezone(timezone.utc).isoformat()


def _clean(value: Any, *, upper: bool = False) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    return text_value.upper() if upper else text_value


def _isin_prefix(isin: str | None) -> str:
    if isin is None or len(isin) < 2 or not isin[:2].isalpha():
        return "MISSING_OR_INVALID"
    return isin[:2].upper()


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _sample_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(row["bond_id"]),
        str(row.get("secid") or ""),
        str(row.get("isin") or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
