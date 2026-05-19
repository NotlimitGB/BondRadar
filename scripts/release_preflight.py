from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]
    cwd: Path
    display_command: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run BondRadar release preflight checks.",
    )
    parser.add_argument(
        "--skip-backend-tests",
        action="store_true",
        help="Skip python -m pytest backend/tests -q.",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Skip frontend npm run build.",
    )
    parser.add_argument(
        "--skip-docker-config",
        action="store_true",
        help="Skip docker compose config --quiet.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed required check.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Write a machine-readable report to this path.",
    )
    return parser.parse_args(argv)


def build_checks(args: argparse.Namespace, root: Path | None = None) -> list[Check]:
    base = root or repo_root()
    checks = [
        Check(
            name="backend_compile",
            command=[sys.executable, "-m", "compileall", "backend/app"],
            cwd=base,
            display_command="python -m compileall backend/app",
        ),
    ]
    if not args.skip_backend_tests:
        checks.append(
            Check(
                name="backend_tests",
                command=[sys.executable, "-m", "pytest", "backend/tests", "-q"],
                cwd=base,
                display_command="python -m pytest backend/tests -q",
            )
        )
    if not args.skip_frontend_build:
        checks.append(
            Check(
                name="frontend_build",
                command=["npm", "run", "build"],
                cwd=base / "frontend",
                display_command="cd frontend && npm run build",
            )
        )
    if not args.skip_docker_config:
        checks.append(
            Check(
                name="docker_compose_config",
                command=["docker", "compose", "config", "--quiet"],
                cwd=base,
                display_command="docker compose config --quiet",
            )
        )
    return checks


def run_single_check(check: Check) -> dict[str, Any]:
    print(f"[preflight] running {check.name}: {check.display_command}", flush=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            check.command,
            cwd=check.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        returncode = completed.returncode
        output = completed.stdout or ""
    except FileNotFoundError as exc:
        returncode = 127
        output = str(exc)
    duration = time.perf_counter() - started
    status_value = "passed" if returncode == 0 else "failed"
    print(
        f"[preflight] {check.name}: {status_value} "
        f"({duration:.2f}s, exit {returncode})",
        flush=True,
    )
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return {
        "name": check.name,
        "command": check.display_command,
        "returncode": returncode,
        "duration_seconds": round(duration, 3),
        "status": status_value,
    }


def run_checks(checks: list[Check], fail_fast: bool = False) -> dict[str, Any]:
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    for check in checks:
        result = run_single_check(check)
        results.append(result)
        if fail_fast and result["status"] == "failed":
            break
    finished_at = utc_now()
    status_value = (
        "failed"
        if any(result["status"] == "failed" for result in results)
        else "passed"
    )
    return {
        "status": status_value,
        "started_at": started_at,
        "finished_at": finished_at,
        "checks": results,
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    checks = build_checks(args)
    report = run_checks(checks, fail_fast=args.fail_fast)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[preflight] wrote JSON report: {args.json_output}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
