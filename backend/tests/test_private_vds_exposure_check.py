from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "private_vds_exposure_check.py"
RENDER_SCRIPT_PATH = REPO_ROOT / "scripts" / "render_first_deploy_commands.py"


VALID_COMPOSE = """
services:
  postgres:
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"
  backend:
    ports:
      - "127.0.0.1:${BACKEND_PORT:-8000}:8000"
  frontend:
    ports:
      - "127.0.0.1:${FRONTEND_PORT:-5173}:5173"
"""


def load_exposure_module() -> Any:
    spec = importlib.util.spec_from_file_location("private_vds_exposure_check", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_baseline(root: Path, compose_text: str = VALID_COMPOSE, render_script: bool = False) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "docs" / "deployment").mkdir(parents=True)
    (root / "docker-compose.prod.yml").write_text(compose_text, encoding="utf-8")
    (root / ".env.production.example").write_text("POSTGRES_HOST=127.0.0.1\n", encoding="utf-8")
    if render_script:
        (root / "scripts" / "render_first_deploy_commands.py").write_text(
            RENDER_SCRIPT_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        (root / "scripts" / "render_first_deploy_commands.py").write_text("# placeholder\n", encoding="utf-8")
    (root / "docs" / "deployment" / "PRIVATE_VDS_SECURITY_BASELINE.md").write_text(
        "# Private baseline\n",
        encoding="utf-8",
    )
    (root / "docs" / "deployment" / "SECURITY_DEBT_REGISTER.md").write_text(
        "# Security debt\n",
        encoding="utf-8",
    )


def run_report(root: Path, argv: list[str] | None = None) -> dict[str, Any]:
    module = load_exposure_module()
    args = module.parse_args(argv or [])
    return module.run_checks(args, root=root)


def test_valid_private_baseline_passes(tmp_path: Path) -> None:
    write_baseline(tmp_path)

    report = run_report(tmp_path)

    assert report["status"] == "passed"
    assert not report["errors"]


def test_missing_required_file_fails(tmp_path: Path) -> None:
    write_baseline(tmp_path)
    (tmp_path / "docs" / "deployment" / "SECURITY_DEBT_REGISTER.md").unlink()

    report = run_report(tmp_path)

    assert report["status"] == "failed"
    assert any(check["name"] == "required_files" and check["status"] == "failed" for check in report["checks"])


def test_obvious_public_postgres_binding_fails(tmp_path: Path) -> None:
    compose = """
services:
  postgres:
    ports:
      - "5432:5432"
  backend:
    ports:
      - "127.0.0.1:${BACKEND_PORT:-8000}:8000"
  frontend:
    ports:
      - "127.0.0.1:${FRONTEND_PORT:-5173}:5173"
"""
    write_baseline(tmp_path, compose)

    report = run_report(tmp_path)

    assert report["status"] == "failed"
    assert any(check["name"] == "postgres_localhost_binding" for check in report["checks"])


def test_localhost_postgres_binding_passes(tmp_path: Path) -> None:
    write_baseline(tmp_path)

    report = run_report(tmp_path)
    postgres_check = next(check for check in report["checks"] if check["name"] == "postgres_localhost_binding")

    assert postgres_check["status"] == "passed"


def test_rendered_private_commands_do_not_expose_app_ports(tmp_path: Path) -> None:
    write_baseline(tmp_path, render_script=True)

    report = run_report(tmp_path, ["--render-commands"])

    assert report["status"] == "passed"
    render_check = next(check for check in report["checks"] if check["name"] == "private_firewall_commands")
    assert render_check["status"] == "passed"


def test_strict_mode_returns_one_on_warning(tmp_path: Path) -> None:
    module = load_exposure_module()
    compose = """
services:
  postgres:
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"
"""
    write_baseline(tmp_path, compose)

    report = run_report(tmp_path)

    assert report["status"] == "warning"
    assert module.exit_code_for_report(report, strict=True) == 1


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_exposure_module()
    write_baseline(tmp_path)
    output_path = tmp_path / "private_exposure.json"
    report = run_report(tmp_path)

    module.write_json_report(report, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["checks"]
