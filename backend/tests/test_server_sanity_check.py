from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "server_sanity_check.py"


def load_sanity_module() -> Any:
    spec = importlib.util.spec_from_file_location("server_sanity_check", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def valid_env() -> str:
    return "\n".join(
        [
            "ENVIRONMENT=production",
            "PROJECT_NAME=BondRadar",
            "POSTGRES_DB=bondradar",
            "POSTGRES_USER=bondradar",
            "POSTGRES_PASSWORD=secret-pass",
            "POSTGRES_PORT=5432",
            "DATABASE_URL=postgresql+psycopg://bondradar:secret-pass@postgres:5432/bondradar",
            "API_PREFIX=/api",
            "MOEX_ISS_BASE_URL=https://iss.moex.com",
            "MOEX_ISS_TIMEOUT_SECONDS=20",
            "ML_ARTIFACT_DIR=artifacts/ml",
            "FRONTEND_PORT=5173",
            'BACKEND_CORS_ORIGINS=["https://bondradar.example.com"]',
            "POSTGRES_HOST=127.0.0.1",
            "BACKUP_DIR=./backups",
            "PGPASSWORD=secret-pass",
        ]
    ) + "\n"


def create_required_files(root: Path) -> None:
    for relative in [
        "docker-compose.prod.yml",
        "scripts/prod_smoke_check.py",
        "scripts/live_data_bootstrap.py",
        "scripts/live_operations_runner.py",
        "scripts/postgres_backup.sh",
        "scripts/postgres_restore.sh",
    ]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (root / ".env.production").write_text(valid_env(), encoding="utf-8")


def check_names(report: dict[str, Any], status: str) -> set[str]:
    return {item["name"] for item in report["checks"] if item["status"] == status}


def test_required_files_check_passes_with_temp_structure(tmp_path: Path) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)

    report = module.run_sanity(
        module.parse_args(["--skip-docker"]),
        root=tmp_path,
    )

    assert report["status"] == "passed"
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "backups").is_dir()


def test_missing_required_file_fails(tmp_path: Path) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)
    (tmp_path / "scripts/prod_smoke_check.py").unlink()

    report = module.run_sanity(
        module.parse_args(["--skip-docker", "--skip-env"]),
        root=tmp_path,
    )

    assert report["status"] == "failed"
    assert "file_exists_scripts_prod_smoke_check_py" in check_names(report, "failed")


def test_env_validation_failure_fails_sanity_check(tmp_path: Path) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)
    (tmp_path / ".env.production").write_text(
        valid_env().replace("ENVIRONMENT=production", "ENVIRONMENT=dev"),
        encoding="utf-8",
    )

    report = module.run_sanity(module.parse_args(["--skip-docker"]), root=tmp_path)

    assert report["status"] == "failed"
    assert "production_env_valid" in check_names(report, "failed")


def test_docker_check_can_be_skipped(tmp_path: Path) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)

    report = module.run_sanity(module.parse_args(["--skip-docker"]), root=tmp_path)

    skipped = check_names(report, "skipped")
    assert "docker_compose_prod_config" in skipped


def test_docker_subprocess_failure_fails_when_not_skipped(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)

    def fake_run_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
        return {"returncode": 1, "output": "bad compose", "duration_seconds": 0.01}

    monkeypatch.setattr(module, "run_command", fake_run_command)

    report = module.run_sanity(module.parse_args([]), root=tmp_path)

    assert report["status"] == "failed"
    assert "docker_compose_prod_config" in check_names(report, "failed")


def test_json_output_is_written(tmp_path: Path, monkeypatch: Any) -> None:
    module = load_sanity_module()
    create_required_files(tmp_path)
    output_path = tmp_path / "sanity.json"
    monkeypatch.setattr(module, "repo_root", lambda: tmp_path)

    exit_code = module.main(["--skip-docker", "--json-output", str(output_path)])

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "passed"' in content
    assert '"production_env_valid"' in content
