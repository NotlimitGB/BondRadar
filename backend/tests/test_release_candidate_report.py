from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_candidate_report.py"


def load_report_module() -> Any:
    spec = importlib.util.spec_from_file_location("release_candidate_report", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_artifact(logs_dir: Path, name: str, payload: dict[str, Any]) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def write_ready_artifacts(logs_dir: Path) -> None:
    write_artifact(logs_dir, "release_preflight.json", {"status": "passed"})
    write_artifact(logs_dir, "env_validation.json", {"status": "passed"})
    write_artifact(logs_dir, "server_sanity.json", {"status": "passed"})
    write_artifact(logs_dir, "prod_smoke.json", {"status": "passed"})
    write_artifact(logs_dir, "live_data_bootstrap_plan.json", {"status": "planned"})
    write_artifact(
        logs_dir,
        "ml_validation_suite.json",
        {"status": "completed", "recommended_model_run_id": 7},
    )
    write_artifact(
        logs_dir,
        "quality_gate.json",
        {
            "status": "ready_for_deploy",
            "ready_for_50k_paper_pilot": True,
            "ready_for_vds_deploy": True,
        },
    )
    write_artifact(logs_dir, "pilot_bootstrap_dry_run.json", {"status": "prepared"})
    write_artifact(logs_dir, "live_ops_monitoring.json", {"status": "monitoring_completed"})


def test_missing_artifacts_produce_warning_status(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"

    report = module.build_report(logs_dir)
    exit_code = module.main(["--logs-dir", str(logs_dir)])

    assert report["status"] == "warning"
    assert report["warnings"]
    assert not report["errors"]
    assert exit_code == 0


def test_failed_artifact_blocks(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_artifact(logs_dir, "release_preflight.json", {"status": "failed"})

    report = module.build_report(logs_dir)
    exit_code = module.main(["--logs-dir", str(logs_dir)])

    assert report["status"] == "blocked"
    assert exit_code == 1


def test_blocked_quality_gate_blocks(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_artifact(
        logs_dir,
        "quality_gate.json",
        {"status": "blocked", "ready_for_50k_paper_pilot": False},
    )

    report = module.build_report(logs_dir)

    assert report["status"] == "blocked"
    assert any(item["artifact"] == "quality_gate" for item in report["errors"])


def test_warning_artifact_gives_warning(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_artifact(logs_dir, "env_validation.json", {"status": "warning"})

    report = module.build_report(logs_dir)

    assert report["status"] == "warning"
    assert any(item["artifact"] == "env_validation" for item in report["warnings"])


def test_strict_mode_treats_warnings_as_exit_1(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"

    exit_code = module.main(["--logs-dir", str(logs_dir), "--strict"])

    assert exit_code == 1


def test_ready_artifacts_produce_ready(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_ready_artifacts(logs_dir)

    report = module.build_report(logs_dir)

    assert report["status"] == "ready"
    assert not report["warnings"]
    assert not report["errors"]


def test_partial_representative_artifacts_remain_warning(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_artifact(logs_dir, "release_preflight.json", {"status": "passed"})
    write_artifact(logs_dir, "env_validation.json", {"status": "passed"})
    write_artifact(logs_dir, "server_sanity.json", {"status": "passed"})
    write_artifact(logs_dir, "prod_smoke.json", {"status": "passed"})
    write_artifact(logs_dir, "live_data_bootstrap_plan.json", {"status": "planned"})
    write_artifact(logs_dir, "live_ops_monitoring.json", {"status": "monitoring_completed"})

    report = module.build_report(logs_dir)

    assert report["status"] == "warning"
    missing = [item for item in report["warnings"] if item["code"] == "missing_artifact"]
    assert missing


def test_json_and_markdown_outputs_are_written(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    write_ready_artifacts(logs_dir)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    exit_code = module.main(
        [
            "--logs-dir",
            str(logs_dir),
            "--json-output",
            str(json_path),
            "--markdown-output",
            str(markdown_path),
        ]
    )

    assert exit_code == 0
    assert '"status": "ready"' in json_path.read_text(encoding="utf-8")
    assert "# BondRadar Release Candidate Report" in markdown_path.read_text(encoding="utf-8")


def test_malformed_json_is_reported_without_crashing(tmp_path: Path) -> None:
    module = load_report_module()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "release_preflight.json").write_text("{bad json", encoding="utf-8")

    report = module.build_report(logs_dir)

    assert report["status"] == "warning"
    assert any(item["code"] == "malformed_artifact" for item in report["warnings"])
