from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


TAIL_LIMIT = 4000


@dataclass(frozen=True)
class RehearsalStep:
    name: str
    command: list[str]
    cwd: Path
    skip: bool = False
    skip_reason: str | None = None
    report_path: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str], Path], CommandResult]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run safe local BondRadar release-candidate rehearsal checks.",
    )
    parser.add_argument("--reports-dir", type=Path, default=Path("./logs/rehearsal"))
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--node-package-manager", choices=("auto", "npm"), default="auto")
    parser.add_argument("--frontend-dir", type=Path, default=Path("frontend"))
    parser.add_argument("--backend-tests-path", default="backend/tests")
    parser.add_argument("--skip-backend-tests", action="store_true")
    parser.add_argument("--skip-frontend-build", action="store_true")
    parser.add_argument("--skip-compose-config", action="store_true")
    parser.add_argument("--skip-private-exposure", action="store_true")
    parser.add_argument("--skip-release-preflight", action="store_true")
    parser.add_argument("--skip-render-commands", action="store_true")
    parser.add_argument("--skip-release-candidate-report", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_steps(args: argparse.Namespace, root: Path | None = None) -> list[RehearsalStep]:
    base = root or repo_root()
    reports_dir = args.reports_dir
    python_bin = args.python_bin
    frontend_dir = args.frontend_dir
    package_manager = resolve_node_package_manager(args.node_package_manager, base / frontend_dir)

    return [
        RehearsalStep(
            name="backend_compile",
            command=[python_bin, "-m", "compileall", "backend/app"],
            cwd=base,
        ),
        RehearsalStep(
            name="backend_tests",
            command=[python_bin, "-m", "pytest", args.backend_tests_path, "-q"],
            cwd=base,
            skip=args.skip_backend_tests,
            skip_reason="Skipped by --skip-backend-tests.",
        ),
        RehearsalStep(
            name="frontend_build",
            command=[package_manager, "run", "build"],
            cwd=base / frontend_dir,
            skip=args.skip_frontend_build,
            skip_reason="Skipped by --skip-frontend-build.",
        ),
        RehearsalStep(
            name="compose_config",
            command=[
                "docker",
                "compose",
                "-f",
                "docker-compose.prod.yml",
                "--env-file",
                ".env.production.example",
                "config",
                "--quiet",
            ],
            cwd=base,
            skip=args.skip_compose_config,
            skip_reason="Skipped by --skip-compose-config.",
        ),
        RehearsalStep(
            name="private_exposure_check",
            command=[
                python_bin,
                "scripts/private_vds_exposure_check.py",
                "--render-commands",
                "--json-output",
                str(reports_dir / "private_vds_exposure.json"),
            ],
            cwd=base,
            skip=args.skip_private_exposure,
            skip_reason="Skipped by --skip-private-exposure.",
            report_path=reports_dir / "private_vds_exposure.json",
        ),
        RehearsalStep(
            name="release_preflight",
            command=[
                python_bin,
                "scripts/release_preflight.py",
                "--json-output",
                str(reports_dir / "release_preflight.json"),
            ],
            cwd=base,
            skip=args.skip_release_preflight,
            skip_reason="Skipped by --skip-release-preflight.",
            report_path=reports_dir / "release_preflight.json",
        ),
        RehearsalStep(
            name="render_first_deploy_commands",
            command=[
                python_bin,
                "scripts/render_first_deploy_commands.py",
                "--access-mode",
                "private",
                "--json-output",
                str(reports_dir / "first_deploy_commands.json"),
                "--markdown-output",
                str(reports_dir / "first_deploy_commands.md"),
            ],
            cwd=base,
            skip=args.skip_render_commands,
            skip_reason="Skipped by --skip-render-commands.",
            report_path=reports_dir / "first_deploy_commands.json",
        ),
        RehearsalStep(
            name="release_candidate_report",
            command=[
                python_bin,
                "scripts/release_candidate_report.py",
                "--logs-dir",
                str(reports_dir),
                "--json-output",
                str(reports_dir / "release_candidate_report.json"),
                "--markdown-output",
                str(reports_dir / "release_candidate_report.md"),
            ],
            cwd=base,
            skip=args.skip_release_candidate_report,
            skip_reason="Skipped by --skip-release-candidate-report.",
            report_path=reports_dir / "release_candidate_report.json",
        ),
    ]


def resolve_node_package_manager(value: str, frontend_dir: Path) -> str:
    if value == "npm":
        return npm_executable()
    if (frontend_dir / "package-lock.json").exists():
        return npm_executable()
    return npm_executable()


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def run_subprocess(command: list[str], cwd: Path) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError as exc:
        return CommandResult(exit_code=127, stderr=str(exc))


def run_rehearsal(
    args: argparse.Namespace,
    root: Path | None = None,
    runner: CommandRunner = run_subprocess,
) -> dict[str, Any]:
    base = root or repo_root()
    reports_dir = resolve_path(args.reports_dir, base)
    reports_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    steps = build_steps(args, base)
    step_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    stop_remaining = False

    for step in steps:
        if stop_remaining:
            step_results.append(skipped_step(step, "Skipped because --fail-fast stopped after a failure."))
            continue
        if step.skip:
            step_results.append(skipped_step(step, step.skip_reason or "Skipped."))
            continue

        result = run_step(step, runner)
        step_results.append(result)
        if result["status"] == "failed":
            errors.append(f"{step.name} failed with exit code {result['exit_code']}.")
            if args.fail_fast:
                stop_remaining = True
        else:
            warnings.extend(extract_step_warnings(step, base))

    finished_at = utc_now()
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "reports_dir": str(reports_dir),
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": step_results,
        "warnings": warnings,
        "errors": errors,
        "next_steps": next_steps(status, warnings, errors),
    }


def run_step(step: RehearsalStep, runner: CommandRunner) -> dict[str, Any]:
    print(f"[rehearsal] running {step.name}: {display_command(step.command)}", flush=True)
    started = time.perf_counter()
    result = runner(step.command, step.cwd)
    duration = time.perf_counter() - started
    status = "passed" if result.exit_code == 0 else "failed"
    print(
        f"[rehearsal] {step.name}: {status} ({duration:.2f}s, exit {result.exit_code})",
        flush=True,
    )
    return {
        "name": step.name,
        "status": status,
        "command": step.command,
        "cwd": str(step.cwd),
        "duration_seconds": round(duration, 3),
        "exit_code": result.exit_code,
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
    }


def resolve_path(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def skipped_step(step: RehearsalStep, reason: str) -> dict[str, Any]:
    return {
        "name": step.name,
        "status": "skipped",
        "command": step.command,
        "cwd": str(step.cwd),
        "duration_seconds": 0.0,
        "exit_code": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "message": reason,
    }


def extract_step_warnings(step: RehearsalStep, root: Path) -> list[str]:
    if step.report_path is None:
        return []
    path = step.report_path if step.report_path.is_absolute() else root / step.report_path
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    status = payload.get("status")
    if status in {"warning", "blocked"}:
        return [f"{step.name} generated a {status} report."]
    if isinstance(payload.get("warnings"), list) and payload["warnings"]:
        return [f"{step.name} generated report warnings."]
    return []


def next_steps(status: str, warnings: list[str], errors: list[str]) -> list[str]:
    if status == "failed":
        return [
            "Resolve failed rehearsal steps before VDS purchase or configuration.",
            "Re-run the local release rehearsal after fixes.",
        ]
    if warnings:
        return [
            "Review warning reports under the rehearsal reports directory.",
            "Decide whether missing optional artifacts should be generated before VDS work.",
        ]
    return [
        "Review the saved rehearsal reports with the release candidate go/no-go document.",
        "Proceed to private VDS provisioning only after human review.",
    ]


def tail(value: str, limit: int = TAIL_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def display_command(command: list[str]) -> str:
    return " ".join(command)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Local Release Rehearsal",
        "",
        "## Overall Status",
        "",
        f"`{report['status']}`",
        "",
        f"Reports directory: `{report['reports_dir']}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Exit |",
        "| --- | --- | --- |",
    ]
    for step in report["steps"]:
        lines.append(f"| {step['name']} | {step['status']} | {step['exit_code']} |")

    lines.extend(["", "## Errors", ""])
    if report["errors"]:
        lines.extend(f"- {error}" for error in report["errors"])
    else:
        lines.append("- None")

    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {warning}" for warning in report["warnings"])
    else:
        lines.append("- None")

    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report["next_steps"])
    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_rehearsal(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[rehearsal] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[rehearsal] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[rehearsal] status: {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
