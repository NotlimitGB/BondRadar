from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a safe post-ingest rebuild plan for BondRadar financial reports.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--as-of-date-from", required=True)
    parser.add_argument("--as-of-date-to", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    backend = args.backend_url.rstrip("/")
    coverage_url = (
        f"{backend}/api/data-readiness/financial-reports/coverage"
        f"?as_of_date={args.as_of_date_to}&active_only=true&stale_after_days=540"
    )
    readiness_url = f"{backend}/api/data-readiness/live"
    quality_url = (
        f"{backend}/api/datasets/quality-report"
        f"?as_of_date_from={args.as_of_date_from}"
        f"&as_of_date_to={args.as_of_date_to}"
        "&return_method=risk_adjusted"
    )
    return {
        "status": "planned",
        "dry_run": True,
        "as_of_date_from": args.as_of_date_from,
        "as_of_date_to": args.as_of_date_to,
        "steps": [
            {
                "name": "coverage_after_import",
                "description": "Confirm financial report coverage changed after import.",
                "command": f"curl -s \"{coverage_url}\"",
            },
            {
                "name": "rebuild_company_credit_health",
                "description": "Run the data pipeline credit health step for the reviewed date range.",
                "payload": _pipeline_payload(args, ["credit_health"]),
            },
            {
                "name": "rebuild_bond_risk_assessment",
                "description": "Run the data pipeline bond risk assessment step after credit health.",
                "payload": _pipeline_payload(args, ["bond_risk_assessment"]),
            },
            {
                "name": "rebuild_feature_snapshots",
                "description": "Run dataset build steps after reports and risk data are refreshed.",
                "payload": _pipeline_payload(
                    args,
                    [
                        "dataset_build_price",
                        "labels_total_return",
                        "labels_risk_adjusted",
                    ],
                ),
            },
            {
                "name": "optional_model_work",
                "description": "Only after feature quality is reviewed, optionally retrain or regenerate predictions.",
                "payload": _pipeline_payload(
                    args,
                    ["ml_train", "ml_predict", "ml_evaluate"],
                    run_ml=True,
                    run_predictions=True,
                    run_evaluation=True,
                ),
            },
            {
                "name": "feature_quality_review",
                "description": "Review ratio coverage in feature snapshots.",
                "command": f"curl -s \"{quality_url}\"",
            },
            {
                "name": "live_readiness_review",
                "description": "Review live data readiness before any paper pilot dry-run.",
                "command": f"curl -s \"{readiness_url}\"",
            },
        ],
        "warnings": [
            "This script renders a plan only and does not run rebuild requests.",
            "Keep paper schedules paused until coverage, rebuild, readiness, and manual review are complete.",
        ],
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Post-Ingest Rebuild Plan",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Steps",
        "",
    ]
    for step in report["steps"]:
        lines.append(f"### {step['name']}")
        lines.append("")
        lines.append(step["description"])
        lines.append("")
        if "command" in step:
            lines.extend(["```bash", step["command"], "```", ""])
        if "payload" in step:
            lines.extend(["```json", json.dumps(step["payload"], indent=2), "```", ""])
    lines.extend(["## Warnings", ""])
    lines.extend(f"- {item}" for item in report["warnings"])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[financial-report-rebuild] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[financial-report-rebuild] wrote Markdown report: {args.markdown_output}", flush=True)
    print("[financial-report-rebuild] planned", flush=True)
    return 0


def _pipeline_payload(
    args: argparse.Namespace,
    steps: list[str],
    *,
    run_ml: bool = False,
    run_predictions: bool = False,
    run_evaluation: bool = False,
) -> dict[str, Any]:
    return {
        "mode": "manual",
        "date_from": args.as_of_date_from,
        "date_to": args.as_of_date_to,
        "steps": steps,
        "return_methods": ["price", "total_return", "risk_adjusted"],
        "rebuild_existing": True,
        "run_ml": run_ml,
        "run_predictions": run_predictions,
        "run_evaluation": run_evaluation,
        "readiness_require_financial_reports": True,
    }


if __name__ == "__main__":
    sys.exit(main())
