from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_production_env.py"


def load_env_module() -> Any:
    spec = importlib.util.spec_from_file_location("validate_production_env", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def valid_env(**overrides: str) -> str:
    values = {
        "ENVIRONMENT": "production",
        "PROJECT_NAME": "BondRadar",
        "POSTGRES_DB": "bondradar",
        "POSTGRES_USER": "bondradar",
        "POSTGRES_PASSWORD": "secret-pass",
        "POSTGRES_PORT": "5432",
        "DATABASE_URL": "postgresql+psycopg://bondradar:secret-pass@postgres:5432/bondradar",
        "API_PREFIX": "/api",
        "MOEX_ISS_BASE_URL": "https://iss.moex.com",
        "MOEX_ISS_TIMEOUT_SECONDS": "20",
        "ML_ARTIFACT_DIR": "artifacts/ml",
        "FRONTEND_PORT": "5173",
        "BACKEND_CORS_ORIGINS": '["https://bondradar.example.com"]',
        "POSTGRES_HOST": "127.0.0.1",
        "BACKUP_DIR": "./backups",
        "PGPASSWORD": "secret-pass",
    }
    values.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def write_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env.production"
    path.write_text(content, encoding="utf-8")
    return path


def check_names(report: dict[str, Any], status: str) -> set[str]:
    return {item["name"] for item in report["checks"] if item["status"] == status}


def test_valid_env_passes(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(tmp_path, valid_env())

    report = module.validate_env_file(path)

    assert report["status"] == "passed"
    assert not report["errors"]


def test_sample_secret_fails(tmp_path: Path) -> None:
    module = load_env_module()
    sample = "replace-with-a-strong-password"
    path = write_env(
        tmp_path,
        valid_env(
            POSTGRES_PASSWORD=sample,
            PGPASSWORD=sample,
            DATABASE_URL=f"postgresql+psycopg://bondradar:{sample}@postgres:5432/bondradar",
        ),
    )

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "postgres_password_changed" in check_names(report, "failed")
    assert "pgpassword_changed" in check_names(report, "failed")


def test_database_url_sample_secret_fails(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(
        tmp_path,
        valid_env(
            DATABASE_URL=(
                "postgresql+psycopg://bondradar:"
                "replace-with-a-strong-password@postgres:5432/bondradar"
            )
        ),
    )

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "database_url_secret_changed" in check_names(report, "failed")


def test_password_mismatch_fails(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(tmp_path, valid_env(PGPASSWORD="other-secret"))

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "postgres_password_matches_pgpassword" in check_names(report, "failed")


def test_invalid_cors_json_fails(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(tmp_path, valid_env(BACKEND_CORS_ORIGINS="not-json"))

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "backend_cors_origins_json" in check_names(report, "failed")


def test_empty_cors_warns_and_strict_fails(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(tmp_path, valid_env(BACKEND_CORS_ORIGINS="[]"))

    report = module.validate_env_file(path)
    strict_report = module.validate_env_file(path, strict=True)

    assert report["status"] == "warning"
    assert "backend_cors_origins_non_empty" in check_names(report, "warning")
    assert strict_report["status"] == "failed"


def test_invalid_port_fails(tmp_path: Path) -> None:
    module = load_env_module()
    path = write_env(tmp_path, valid_env(POSTGRES_PORT="99999"))

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "postgres_port_valid" in check_names(report, "failed")


def test_missing_required_var_fails(tmp_path: Path) -> None:
    module = load_env_module()
    content = valid_env().replace("PROJECT_NAME=BondRadar\n", "")
    path = write_env(tmp_path, content)

    report = module.validate_env_file(path)

    assert report["status"] == "failed"
    assert "required_project_name" in check_names(report, "failed")


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_env_module()
    env_path = write_env(tmp_path, valid_env())
    output_path = tmp_path / "env-report.json"

    exit_code = module.main(["--env-file", str(env_path), "--json-output", str(output_path)])

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "passed"' in content
    assert '"environment_is_production"' in content
