from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "local_release_rehearsal.py"


def load_rehearsal_module() -> Any:
    spec = importlib.util.spec_from_file_location("local_release_rehearsal", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(argv: list[str] | None = None) -> Any:
    module = load_rehearsal_module()
    return module.parse_args(argv or [])


def test_default_plan_includes_safe_steps(tmp_path: Path) -> None:
    module = load_rehearsal_module()

    steps = module.build_steps(args(["--reports-dir", str(tmp_path / "reports")]), root=tmp_path)

    assert [step.name for step in steps] == [
        "backend_compile",
        "backend_tests",
        "frontend_build",
        "compose_config",
        "private_exposure_check",
        "release_preflight",
        "render_first_deploy_commands",
        "release_candidate_report",
    ]


def test_skip_flags_mark_expected_steps_skipped(tmp_path: Path) -> None:
    module = load_rehearsal_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path) -> Any:
        calls.append(command)
        return module.CommandResult(exit_code=0, stdout="ok\n")

    report = module.run_rehearsal(
        args(
            [
                "--reports-dir",
                str(tmp_path / "reports"),
                "--skip-backend-tests",
                "--skip-frontend-build",
            ]
        ),
        root=tmp_path,
        runner=fake_runner,
    )

    skipped = {step["name"] for step in report["steps"] if step["status"] == "skipped"}
    assert {"backend_tests", "frontend_build"} <= skipped
    assert all("pytest" not in command and command[:3] != ["npm", "run", "build"] for command in calls)


def test_successful_fake_subprocess_results_produce_passed_report(tmp_path: Path) -> None:
    module = load_rehearsal_module()

    def fake_runner(command: list[str], cwd: Path) -> Any:
        return module.CommandResult(exit_code=0, stdout="ok\n", stderr="")

    report = module.run_rehearsal(
        args(["--reports-dir", str(tmp_path / "reports")]),
        root=tmp_path,
        runner=fake_runner,
    )

    assert report["status"] == "passed"
    assert all(step["status"] in {"passed", "skipped"} for step in report["steps"])


def test_failed_command_produces_failed_report(tmp_path: Path) -> None:
    module = load_rehearsal_module()

    def fake_runner(command: list[str], cwd: Path) -> Any:
        if "pytest" in command:
            return module.CommandResult(exit_code=1, stdout="failed\n")
        return module.CommandResult(exit_code=0, stdout="ok\n")

    report = module.run_rehearsal(
        args(["--reports-dir", str(tmp_path / "reports")]),
        root=tmp_path,
        runner=fake_runner,
    )

    assert report["status"] == "failed"
    assert any(step["name"] == "backend_tests" and step["status"] == "failed" for step in report["steps"])


def test_fail_fast_stops_after_first_failure(tmp_path: Path) -> None:
    module = load_rehearsal_module()
    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path) -> Any:
        calls.append(command)
        return module.CommandResult(exit_code=1, stdout="failed\n")

    report = module.run_rehearsal(
        args(["--reports-dir", str(tmp_path / "reports"), "--fail-fast"]),
        root=tmp_path,
        runner=fake_runner,
    )

    assert report["status"] == "failed"
    assert len(calls) == 1
    assert report["steps"][0]["status"] == "failed"
    assert all(step["status"] == "skipped" for step in report["steps"][1:])


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_rehearsal_module()
    output_path = tmp_path / "local_release_rehearsal.json"
    report = {"status": "passed", "steps": [], "warnings": [], "errors": [], "next_steps": []}

    module.write_json_report(report, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"


def test_markdown_output_is_written(tmp_path: Path) -> None:
    module = load_rehearsal_module()
    output_path = tmp_path / "local_release_rehearsal.md"
    report = {
        "status": "passed",
        "reports_dir": str(tmp_path / "reports"),
        "steps": [{"name": "backend_compile", "status": "passed", "exit_code": 0}],
        "warnings": [],
        "errors": [],
        "next_steps": ["Review reports."],
    }

    module.write_markdown_report(report, output_path)

    text = output_path.read_text(encoding="utf-8")
    assert text.startswith("# BondRadar Local Release Rehearsal")
    assert "| backend_compile | passed | 0 |" in text


def test_source_does_not_contain_forbidden_execution_or_deploy_strings() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "docker compose up",
        "docker compose build",
        "/api/paper-trading/live/schedules/run-due",
        "/api/paper-trading/live/schedules/{id}/run",
        "/api/paper-trading/live/cycles/run",
        "--execute-due-schedules",
        "--execute-data-pipeline",
        "--run-ml-validation",
        "--confirm-live-operations yes",
        "--confirm-live-data-bootstrap yes",
    ]

    for value in forbidden:
        assert value not in source
