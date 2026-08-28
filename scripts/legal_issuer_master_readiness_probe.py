from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models.bond_legal_issuer_evidence import (  # noqa: E402
    BondLegalIssuerEvidence,
)
from app.models.bond_legal_issuer_profile import (  # noqa: E402
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    LEGAL_ISSUER_MAPPING_SOURCE,
    BondLegalIssuerProfile,
)
from app.services.bond_legal_issuer_service import (  # noqa: E402
    BondLegalIssuerService,
)
from app.services.legal_issuer_master_service import (  # noqa: E402
    LegalIssuerMasterService,
)


SCHEMA = "bondradar.legal_issuer_master_readiness_probe.v1"
FAILURE_CODE = "legal_issuer_master_readiness_probe_failed"


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid_probe_arguments")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _SanitizedArgumentParser(
        description="Analyze Task243 readiness for Legal Issuer Master population.",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.sample_limit <= 100:
        raise ValueError("invalid_sample_limit")
    return args


def build_legal_issuer_master_readiness_report(
    db: Session,
    *,
    sample_limit: int = 20,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if not 1 <= sample_limit <= 100:
        raise ValueError("invalid_sample_limit")
    profiles = list(
        db.execute(
            select(BondLegalIssuerProfile).order_by(BondLegalIssuerProfile.bond_id)
        ).scalars()
    )
    upstream_rows = list(
        db.execute(
            select(BondLegalIssuerEvidence).order_by(
                BondLegalIssuerEvidence.source,
                BondLegalIssuerEvidence.source_issuer_id,
                BondLegalIssuerEvidence.matched_secid,
                BondLegalIssuerEvidence.observed_at,
                BondLegalIssuerEvidence.evidence_fingerprint,
            )
        ).scalars()
    )

    usable: list[dict[str, Any]] = []
    missing_source_id = 0
    for row in upstream_rows:
        canonical = BondLegalIssuerService._validated_persisted_evidence(row)
        if canonical["source_issuer_id"] is None:
            missing_source_id += 1
            continue
        usable.append(canonical)

    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        by_identity[(row["source"], row["source_issuer_id"])].append(row)

    planned_states: dict[tuple[str, str], str] = {}
    resolved_inns: dict[tuple[str, str], str] = {}
    security_distribution: Counter[int] = Counter()
    samples: dict[str, list[dict[str, Any]]] = {
        "missing_inn": [],
        "inn_conflicts": [],
        "title_ambiguities": [],
        "okpo_ambiguities": [],
        "shared_inn_source_identities": [],
        "unresolvable_profiles": [],
    }
    metrics: Counter[str] = Counter()
    for identity in sorted(by_identity):
        rows = by_identity[identity]
        current = LegalIssuerMasterService.latest_per_security(
            [
                {
                    "source_security_secid": row["matched_secid"],
                    "issuer_title": row["issuer_title"],
                    "issuer_inn": row["issuer_inn"],
                    "issuer_okpo": row["issuer_okpo"],
                    "observed_at": row["observed_at"],
                    "fingerprint": row["fingerprint"],
                }
                for row in rows
            ]
        )
        titles = _non_null_values(current, "issuer_title")
        inns = _non_null_values(current, "issuer_inn")
        okpos = _non_null_values(current, "issuer_okpo")
        securities = {row["source_security_secid"] for row in current}
        security_distribution[len(securities)] += 1
        state = (
            "conflict"
            if len(inns) > 1
            else "verified"
            if len(titles) == 1
            else "observed"
        )
        planned_states[identity] = state
        sample_base = {
            "identity_source": identity[0],
            "source_issuer_id": identity[1],
            "security_count": len(securities),
        }
        if not inns:
            metrics["issuer_ids_with_missing_inn"] += 1
            samples["missing_inn"].append(
                {**sample_base, "diagnostic_code": "ISSUER_INN_MISSING"}
            )
        elif len(inns) == 1:
            resolved_inns[identity] = next(iter(inns))
        else:
            metrics["issuer_ids_with_multiple_current_non_null_inns"] += 1
            samples["inn_conflicts"].append(
                {
                    **sample_base,
                    "diagnostic_code": "CURRENT_ISSUER_INN_CONFLICT",
                    "distinct_value_count": len(inns),
                }
            )
        if len(titles) > 1:
            metrics["issuer_ids_with_multiple_current_titles"] += 1
            samples["title_ambiguities"].append(
                {
                    **sample_base,
                    "diagnostic_code": "CURRENT_ISSUER_TITLE_AMBIGUITY",
                    "distinct_value_count": len(titles),
                }
            )
        if len(okpos) > 1:
            metrics["issuer_ids_with_multiple_current_okpos"] += 1
            samples["okpo_ambiguities"].append(
                {
                    **sample_base,
                    "diagnostic_code": "CURRENT_ISSUER_OKPO_AMBIGUITY",
                    "distinct_value_count": len(okpos),
                }
            )

    identities_by_inn: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for identity, inn in resolved_inns.items():
        identities_by_inn[inn].append(identity)
    for identities in identities_by_inn.values():
        if len(identities) < 2:
            continue
        metrics["inns_shared_by_multiple_source_issuer_ids"] += 1
        for identity in sorted(identities):
            samples["shared_inn_source_identities"].append(
                {
                    "identity_source": identity[0],
                    "source_issuer_id": identity[1],
                    "diagnostic_code": "INN_SHARED_ACROSS_SOURCE_ISSUER_IDS",
                    "source_identity_count": len(identities),
                }
            )

    resolvable_profiles = 0
    profiles_missing_source_id = 0
    for profile in profiles:
        identity = (
            profile.mapping_source,
            profile.source_issuer_id,
        )
        resolvable = (
            profile.contract_version == LEGAL_ISSUER_MAPPING_CONTRACT_VERSION
            and profile.mapping_state == "verified"
            and profile.mapping_source == LEGAL_ISSUER_MAPPING_SOURCE
            and profile.source_issuer_id is not None
            and planned_states.get(identity) == "verified"
        )
        if profile.source_issuer_id is None:
            profiles_missing_source_id += 1
        if resolvable:
            resolvable_profiles += 1
        else:
            samples["unresolvable_profiles"].append(
                {
                    "bond_id": profile.bond_id,
                    "mapping_source": profile.mapping_source,
                    "source_issuer_id": profile.source_issuer_id,
                    "mapping_state": profile.mapping_state,
                    "diagnostic_code": "PROFILE_NOT_RESOLVABLE_TO_PLANNED_MASTER",
                }
            )

    verified_profiles = sum(profile.mapping_state == "verified" for profile in profiles)
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    return {
        "schema": SCHEMA,
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "status": "completed",
        "summary": {
            "total_bond_legal_issuer_profiles": len(profiles),
            "verified_profiles": verified_profiles,
            "non_verified_profiles": len(profiles) - verified_profiles,
            "task243_evidence_count": len(upstream_rows),
            "unique_mapping_source_issuer_ids": len(by_identity),
            "profiles_missing_source_issuer_id": profiles_missing_source_id,
            "evidence_missing_source_issuer_id": missing_source_id,
            "issuer_ids_with_missing_inn": metrics["issuer_ids_with_missing_inn"],
            "issuer_ids_with_multiple_current_non_null_inns": metrics[
                "issuer_ids_with_multiple_current_non_null_inns"
            ],
            "issuer_ids_with_multiple_current_titles": metrics[
                "issuer_ids_with_multiple_current_titles"
            ],
            "issuer_ids_with_multiple_current_okpos": metrics[
                "issuer_ids_with_multiple_current_okpos"
            ],
            "inns_shared_by_multiple_source_issuer_ids": metrics[
                "inns_shared_by_multiple_source_issuer_ids"
            ],
            "planned_legal_issuer_row_count": len(by_identity),
            "planned_issuer_evidence_row_count": len(usable),
            "profiles_resolvable_to_planned_master": resolvable_profiles,
            "unresolved_profile_count": len(profiles) - resolvable_profiles,
        },
        "securities_per_source_issuer_id_distribution": {
            str(count): security_distribution[count]
            for count in sorted(security_distribution)
        },
        "samples": {
            key: sorted(value, key=_sample_sort_key)[:sample_limit]
            for key, value in samples.items()
        },
        "read_only": True,
        "database_mutation_executed": False,
        "external_source_called": False,
        "company_mutation_executed": False,
        "financial_report_mutation_executed": False,
    }


def enforce_read_only_transaction(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    db.execute(text("SET TRANSACTION READ ONLY"))
    value = db.execute(text("SHOW transaction_read_only")).scalar_one()
    if str(value).lower() not in {"on", "true", "1"}:
        raise RuntimeError("read_only_transaction_not_enforced")
    return True


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Legal Issuer Master Readiness Probe",
            "",
            f"Schema: `{report['schema']}`",
            f"Generated at: `{report['generated_at']}`",
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Securities per source issuer",
            "",
            "```json",
            json.dumps(
                report["securities_per_source_issuer_id_distribution"],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## Samples",
            "",
            "```json",
            json.dumps(report["samples"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            try:
                enforce_read_only_transaction(db)
                report = build_legal_issuer_master_readiness_report(
                    db,
                    sample_limit=args.sample_limit,
                )
            finally:
                db.rollback()
        rendered = serialize_report(report, args.format)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    except Exception:
        failure = {"schema": SCHEMA, "status": "failed", "error": FAILURE_CODE}
        sys.stderr.write(json.dumps(failure, sort_keys=True) + "\n")
        return 1


def _non_null_values(rows: list[dict[str, Any]], field_name: str) -> set[str]:
    return {row[field_name] for row in rows if row[field_name] is not None}


def _sample_sort_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "") for key in sorted(row))


if __name__ == "__main__":
    raise SystemExit(main())
