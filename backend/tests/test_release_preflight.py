from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_preflight.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_preflight_module() -> Any:
    spec = importlib.util.spec_from_file_location("release_preflight", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(**overrides: Any) -> Namespace:
    values = {
        "skip_backend_tests": False,
        "skip_frontend_build": False,
        "skip_docker_config": False,
        "run_smoke_check": False,
        "fail_fast": False,
        "json_output": None,
    }
    values.update(overrides)
    return Namespace(**values)


def test_builds_default_check_list() -> None:
    preflight = load_preflight_module()

    checks = preflight.build_checks(args(), Path("/repo"))

    assert [check.name for check in checks] == [
        "backend_compile",
        "backend_tests",
        "frontend_build",
        "docker_compose_config",
    ]
    assert checks[0].display_command == "python -m compileall backend/app"
    assert checks[2].cwd == Path("/repo") / "frontend"


def test_skip_flags_remove_checks() -> None:
    preflight = load_preflight_module()

    checks = preflight.build_checks(
        args(
            skip_backend_tests=True,
            skip_frontend_build=True,
            skip_docker_config=True,
        ),
        Path("/repo"),
    )

    assert [check.name for check in checks] == ["backend_compile"]


def test_smoke_check_is_optional_and_non_default() -> None:
    preflight = load_preflight_module()

    default_checks = preflight.build_checks(args(), Path("/repo"))
    smoke_checks = preflight.build_checks(args(run_smoke_check=True), Path("/repo"))

    assert "prod_smoke_check" not in [check.name for check in default_checks]
    assert smoke_checks[-1].name == "prod_smoke_check"
    assert smoke_checks[-1].display_command == "python scripts/prod_smoke_check.py"


def test_non_zero_check_produces_failed_report_and_exit(monkeypatch: Any) -> None:
    preflight = load_preflight_module()

    def fake_run(*run_args: Any, **run_kwargs: Any) -> SimpleNamespace:
        command = run_args[0]
        return SimpleNamespace(
            returncode=1 if "pytest" in command else 0,
            stdout="failed\n" if "pytest" in command else "ok\n",
        )

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    exit_code = preflight.main(["--skip-frontend-build", "--skip-docker-config"])

    assert exit_code == 1


def test_json_output_file_is_written(tmp_path: Path, monkeypatch: Any) -> None:
    preflight = load_preflight_module()
    output_path = tmp_path / "preflight.json"

    def fake_run(*run_args: Any, **run_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    exit_code = preflight.main(
        [
            "--skip-backend-tests",
            "--skip-frontend-build",
            "--skip-docker-config",
            "--json-output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "passed"' in content
    assert '"name": "backend_compile"' in content


def test_fail_fast_stops_after_first_failure(monkeypatch: Any) -> None:
    preflight = load_preflight_module()
    calls: list[list[str]] = []

    def fake_run(*run_args: Any, **run_kwargs: Any) -> SimpleNamespace:
        command = list(run_args[0])
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="failed\n")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    checks = preflight.build_checks(args(), Path("/repo"))

    report = preflight.run_checks(checks, fail_fast=True)

    assert report["status"] == "failed"
    assert len(report["checks"]) == 1
    assert len(calls) == 1


def test_production_compose_uses_nginx_frontend_and_local_postgres() -> None:
    compose = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "dockerfile: Dockerfile.prod" in compose
    assert '"127.0.0.1:${POSTGRES_PORT:-5432}:5432"' in compose
    assert "VITE_API_PROXY_TARGET" not in compose


def test_frontend_nginx_proxies_api() -> None:
    nginx = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "location /api/" in nginx
    assert "proxy_pass http://backend:8000/api/;" in nginx


def test_production_env_uses_localhost_backup_binding() -> None:
    env_example = (REPO_ROOT / ".env.production.example").read_text(encoding="utf-8")

    assert "POSTGRES_HOST=127.0.0.1" in env_example
    assert "DATABASE_URL=postgresql+psycopg://" in env_example
    assert "@postgres:5432/" in env_example
