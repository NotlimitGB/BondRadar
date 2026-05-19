from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


REQUIRED_FILES = [
    "docker-compose.prod.yml",
    "scripts/prod_smoke_check.py",
    "scripts/live_data_bootstrap.py",
    "scripts/live_operations_runner.py",
    "scripts/postgres_backup.sh",
    "scripts/postgres_restore.sh",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local VDS server-side sanity checks for BondRadar.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.production"))
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-env", action="store_true")
    parser.add_argument("--skip-files", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_sanity(args: argparse.Namespace, root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    checks: list[dict[str, Any]] = []

    if args.skip_files:
        checks.append(_check("required_files", "skipped", "Required file checks skipped.", {}))
    else:
        _check_required_files(base, args.env_file, checks)
        _ensure_directory(base / "logs", checks, "logs_dir_ready")
        _ensure_directory(base / "backups", checks, "backups_dir_ready")

    if args.skip_env:
        checks.append(_check("production_env_valid", "skipped", "Environment validation skipped.", {}))
    else:
        env_report = _load_env_validator().validate_env_file(_resolve_env_path(base, args.env_file))
        checks.append(
            _check(
                "production_env_valid",
                "passed" if env_report["status"] in {"passed", "warning"} else "failed",
                "Production environment validation completed.",
                {
                    "env_status": env_report["status"],
                    "error_count": len(env_report.get("errors", [])),
                    "warning_count": len(env_report.get("warnings", [])),
                },
            )
        )

    if args.skip_docker:
        checks.append(_check("docker_compose_prod_config", "skipped", "Docker compose config check skipped.", {}))
    else:
        checks.append(_run_docker_config(base, args.env_file))

    status = "failed" if any(item["status"] == "failed" for item in checks) else "passed"
    return {"status": status, "checks": checks}


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_sanity(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[sanity] wrote JSON report: {args.json_output}", flush=True)
    print(f"[sanity] {report['status']}", flush=True)
    return 0 if report["status"] == "passed" else 1


def run_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
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
    return {
        "returncode": returncode,
        "output": output,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def _check_required_files(base: Path, env_file: Path, checks: list[dict[str, Any]]) -> None:
    for relative in REQUIRED_FILES:
        path = base / relative
        checks.append(
            _check(
                f"file_exists_{relative.replace('/', '_').replace('.', '_')}",
                "passed" if path.is_file() else "failed",
                f"{relative} exists." if path.is_file() else f"{relative} is required.",
                {"path": str(path)},
            )
        )
    env_path = _resolve_env_path(base, env_file)
    checks.append(
        _check(
            "file_exists_env",
            "passed" if env_path.is_file() else "failed",
            f"{env_path} exists." if env_path.is_file() else f"{env_path} is required.",
            {"path": str(env_path)},
        )
    )


def _ensure_directory(path: Path, checks: list[dict[str, Any]], name: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        status = "passed" if path.is_dir() else "failed"
        message = f"{path} is ready." if status == "passed" else f"{path} could not be created."
    except OSError as exc:
        status = "failed"
        message = str(exc)
    checks.append(_check(name, status, message, {"path": str(path)}))


def _run_docker_config(base: Path, env_file: Path) -> dict[str, Any]:
    env_path = _resolve_env_path(base, env_file)
    command = [
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "--env-file",
        str(env_path),
        "config",
        "--quiet",
    ]
    result = run_command(command, cwd=base)
    return _check(
        "docker_compose_prod_config",
        "passed" if result["returncode"] == 0 else "failed",
        "Production compose config is valid."
        if result["returncode"] == 0
        else "Production compose config check failed.",
        {
            "command": " ".join(command),
            "returncode": result["returncode"],
            "output": result["output"][:1000],
            "duration_seconds": result["duration_seconds"],
        },
    )


def _load_env_validator() -> Any:
    path = Path(__file__).with_name("validate_production_env.py")
    spec = importlib.util.spec_from_file_location("validate_production_env", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_env_path(base: Path, env_file: Path) -> Path:
    return env_file if env_file.is_absolute() else base / env_file


def _check(name: str, status: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details,
    }


if __name__ == "__main__":
    raise SystemExit(main())
