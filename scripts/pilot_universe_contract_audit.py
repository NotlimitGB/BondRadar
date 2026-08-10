from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.schemas.pilot_universe import (  # noqa: E402
    PilotUniverseEvaluationRequest,
    PilotUniverseEvaluationResult,
)
from app.services.pilot_universe_service import (  # noqa: E402
    PILOT_UNIVERSE_CONTRACT_VERSION,
    PilotUniverseService,
)


FAILURE_CODE = "pilot_universe_contract_audit_failed"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the read-only BondRadar pilot universe contract.",
    )
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--required-market-date", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args(argv)


def enforce_read_only_transaction(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    db.execute(text("SET TRANSACTION READ ONLY"))
    value = db.execute(text("SHOW transaction_read_only")).scalar_one()
    if str(value).lower() not in {"on", "true", "1"}:
        raise RuntimeError("read_only_transaction_not_enforced")
    return True


def build_cli_projection(result: PilotUniverseEvaluationResult) -> dict[str, Any]:
    summary = result.summary.model_dump(mode="json")
    return {
        "contract_version": PILOT_UNIVERSE_CONTRACT_VERSION,
        "as_of_date": result.request.as_of_date.isoformat(),
        "required_market_trade_date": (
            result.request.required_market_trade_date.isoformat()
        ),
        "summary": summary,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BondRadar Pilot Universe Contract Audit",
        "",
        f"Contract version: `{report['contract_version']}`",
        f"As-of date: `{report['as_of_date']}`",
        (
            "Required market trade date: "
            f"`{report['required_market_trade_date']}`"
        ),
        "",
        "## Gate counts",
        "",
        f"- Bonds: {summary['bonds_total']}",
        (
            "- Identity: "
            f"{summary['identity_pass_count']} pass / "
            f"{summary['identity_fail_count']} fail"
        ),
        (
            "- Legacy terms: "
            f"{summary['legacy_terms_pass_count']} pass / "
            f"{summary['legacy_terms_fail_count']} fail"
        ),
        (
            "- Market: "
            f"{summary['market_pass_count']} pass / "
            f"{summary['market_fail_count']} fail"
        ),
        (
            "- Observed cashflows: "
            f"{summary['observed_cashflow_pass_count']} pass / "
            f"{summary['observed_cashflow_fail_count']} fail / "
            f"{summary['observed_cashflow_not_proven_count']} not proven"
        ),
        (
            "- Pre-pilot data candidates: "
            f"{summary['pre_pilot_data_candidate_count']}"
        ),
        "- Final pilot eligible: 0 (not evaluated)",
        "",
    ]
    sections = (
        ("Identity blocker counts", "identity_blocker_counts"),
        ("Legacy terms blocker counts", "legacy_terms_blocker_counts"),
        ("Market blocker counts", "market_blocker_counts"),
        ("Cashflow blocker counts", "cashflow_blocker_counts"),
        ("System capability blockers", "system_capability_blockers"),
        ("Pre-pilot candidate samples", "pre_pilot_candidate_samples"),
        ("Excluded bond samples", "excluded_bond_samples"),
    )
    for title, key in sections:
        lines.extend((f"## {title}", "", "```json"))
        lines.append(json.dumps(summary[key], ensure_ascii=False, indent=2))
        lines.extend(("```", ""))
    return "\n".join(lines)


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = PilotUniverseEvaluationRequest(
            as_of_date=args.as_of_date,
            required_market_trade_date=args.required_market_date,
            sample_limit=args.sample_limit,
        )
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            try:
                enforce_read_only_transaction(db)
                result = PilotUniverseService(db).evaluate(request)
            finally:
                db.rollback()
        report = build_cli_projection(result)
        rendered = serialize_report(report, args.format)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    except Exception:
        failure = {
            "contract_version": PILOT_UNIVERSE_CONTRACT_VERSION,
            "status": "failed",
            "error": FAILURE_CODE,
        }
        sys.stderr.write(json.dumps(failure, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
