from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


EXPECTED_ARTIFACTS = [
    ("release_preflight", "release_preflight.json"),
    ("env_validation", "env_validation.json"),
    ("server_sanity", "server_sanity.json"),
    ("prod_smoke", "prod_smoke.json"),
    ("live_data_bootstrap_plan", "live_data_bootstrap_plan.json"),
    ("ml_validation_suite", "ml_validation_suite.json"),
    ("quality_gate", "quality_gate.json"),
    ("pilot_bootstrap_dry_run", "pilot_bootstrap_dry_run.json"),
    ("live_ops_monitoring", "live_ops_monitoring.json"),
]

BLOCKING_STATUSES = {"failed", "blocked", "safety_failed"}
WARNING_STATUSES = {"warning", "completed_with_warnings", "dry_run_completed"}
PASSING_STATUSES = {
    "passed",
    "ready",
    "ready_for_deploy",
    "planned",
    "completed",
    "monitoring_completed",
    "prepared",
    "scheduled",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize BondRadar release candidate JSON artifacts.",
    )
    parser.add_argument("--logs-dir", type=Path, default=Path("./logs"))
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failing exit code when warnings are present.",
    )
    return parser.parse_args(argv)


def build_report(logs_dir: Path) -> dict[str, Any]:
    artifacts = [_read_artifact(logs_dir, name, filename) for name, filename in EXPECTED_ARTIFACTS]
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for artifact in artifacts:
        warnings.extend(artifact["warnings"])
        errors.extend(artifact["errors"])

    status = "blocked" if errors else "warning" if warnings else "ready"
    return {
        "status": status,
        "logs_dir": str(logs_dir),
        "artifacts": artifacts,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps(status, warnings, errors),
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Release Candidate Report",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        "## Artifacts",
        "",
        "| Artifact | Present | Status | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for artifact in report["artifacts"]:
        notes = []
        if artifact["warnings"]:
            notes.append(f"{len(artifact['warnings'])} warning(s)")
        if artifact["errors"]:
            notes.append(f"{len(artifact['errors'])} blocker(s)")
        lines.append(
            "| {name} | {present} | {status} | {notes} |".format(
                name=artifact["name"],
                present="yes" if artifact["present"] else "no",
                status=artifact.get("status") or "missing",
                notes=", ".join(notes) if notes else "",
            )
        )

    lines.extend(["", "## Blockers", ""])
    if report["errors"]:
        for item in report["errors"]:
            lines.append(f"- {item['message']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        for item in report["warnings"]:
            lines.append(f"- {item['message']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Next Steps", ""])
    for step in report["next_steps"]:
        lines.append(f"- {step}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.logs_dir)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[release-candidate] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[release-candidate] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[release-candidate] {report['status']}", flush=True)
    if report["status"] == "blocked":
        return 1
    if args.strict and report["status"] == "warning":
        return 1
    return 0


def _read_artifact(logs_dir: Path, name: str, filename: str) -> dict[str, Any]:
    path = logs_dir / filename
    base = {
        "name": name,
        "path": str(path),
        "present": path.is_file(),
        "status": None,
        "summary": {},
        "warnings": [],
        "errors": [],
    }
    if not path.is_file():
        base["warnings"].append(
            _message(name, "missing_artifact", f"{filename} is missing.", {"path": str(path)})
        )
        return base

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base["warnings"].append(
            _message(name, "malformed_artifact", f"{filename} could not be read as JSON.", {"error": str(exc)})
        )
        return base

    if not isinstance(data, dict):
        base["warnings"].append(
            _message(name, "invalid_artifact_shape", f"{filename} does not contain a JSON object.", {})
        )
        return base

    status = data.get("status")
    base["status"] = status if isinstance(status, str) else None
    base["summary"] = _summary(name, data)

    _classify_status(base, name, filename)
    _classify_semantics(base, name, data)
    return base


def _classify_status(
    artifact: dict[str, Any],
    name: str,
    filename: str,
) -> None:
    status = artifact["status"]
    if status in BLOCKING_STATUSES:
        artifact["errors"].append(
            _message(name, "blocking_status", f"{filename} reported blocking status {status}.", {})
        )
    elif status in WARNING_STATUSES:
        artifact["warnings"].append(
            _message(name, "warning_status", f"{filename} reported warning status {status}.", {})
        )
    elif status in PASSING_STATUSES:
        return
    else:
        artifact["warnings"].append(
            _message(name, "unknown_status", f"{filename} reported an unknown or missing status.", {"status": status})
        )


def _classify_semantics(
    artifact: dict[str, Any],
    name: str,
    data: dict[str, Any],
) -> None:
    if name == "quality_gate":
        if data.get("ready_for_50k_paper_pilot") is False:
            artifact["errors"].append(
                _message(
                    name,
                    "paper_pilot_not_ready",
                    "Quality gate says the 50k virtual paper pilot is not ready.",
                    {},
                )
            )
        if data.get("ready_for_vds_deploy") is False:
            artifact["warnings"].append(
                _message(
                    name,
                    "vds_deploy_not_ready",
                    "Quality gate says VDS deploy readiness still needs review.",
                    {},
                )
            )
    if name == "ml_validation_suite" and data.get("recommended_model_run_id") is None:
        artifact["warnings"].append(
            _message(
                name,
                "missing_recommended_model",
                "ML validation suite did not report a recommended model run id.",
                {},
            )
        )


def _summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    keys_by_artifact = {
        "ml_validation_suite": ["recommended_model_run_id", "completed_training_count"],
        "quality_gate": ["ready_for_50k_paper_pilot", "ready_for_vds_deploy"],
        "live_data_bootstrap_plan": ["summary"],
        "live_ops_monitoring": ["summary"],
        "prod_smoke": ["backend_url", "frontend_url"],
    }
    result: dict[str, Any] = {}
    for key in keys_by_artifact.get(name, []):
        if key in data:
            result[key] = data[key]
    return result


def _message(
    artifact: str,
    code: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact": artifact,
        "code": code,
        "message": message,
        "details": details,
    }


def _next_steps(
    status: str,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[str]:
    if status == "blocked":
        return [
            "Resolve blocking artifacts before treating this repository state as a release candidate.",
            "Re-run the affected checks and regenerate the release candidate report.",
        ]
    if warnings:
        return [
            "Review warning artifacts and decide whether more evidence is required.",
            "Save missing reports under ./logs when those checks become available.",
        ]
    return [
        "Archive the release candidate report with the saved JSON artifacts.",
        "Continue with the VDS deployment runbook only after human review.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
