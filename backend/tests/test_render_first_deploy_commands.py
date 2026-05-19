from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "render_first_deploy_commands.py"


def load_render_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_first_deploy_commands", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_report(argv: list[str] | None = None) -> dict[str, Any]:
    module = load_render_module()
    return module.build_command_report(module.parse_args(argv or []))


def render_text(report: dict[str, Any]) -> str:
    module = load_render_module()
    return json.dumps(report) + "\n" + module.render_markdown(report)


def test_default_placeholders_are_used() -> None:
    report = build_report()
    text = render_text(report)

    assert report["server_ip"] == "<SERVER_IP>"
    assert report["repo_url"] == "<REPO_URL>"
    assert report["deploy_dir"] == "/opt/BondRadar"
    assert report["deploy_user"] == "bondradar"
    assert "<SERVER_IP>" in text
    assert "<REPO_URL>" in text


def test_custom_server_and_repo_are_rendered() -> None:
    report = build_report(["--server-ip", "1.2.3.4", "--repo-url", "https://example.com/repo.git"])
    text = render_text(report)

    assert report["server_ip"] == "1.2.3.4"
    assert report["repo_url"] == "https://example.com/repo.git"
    assert "ssh root@1.2.3.4" in text
    assert "git clone https://example.com/repo.git /opt/BondRadar" in text


def test_output_has_required_sections() -> None:
    report = build_report()
    titles = [section["title"] for section in report["sections"]]

    assert titles == [
        "SSH login",
        "create deploy user",
        "firewall",
        "clone repository",
        "prepare env",
        "validate env",
        "build/start compose",
        "health checks",
        "smoke checks",
        "bootstrap plan",
        "monitoring check",
        "backup check",
    ]


def test_no_paper_execution_enablement_commands() -> None:
    report = build_report()
    text = render_text(report)

    assert "--execute-due-schedules" not in text
    assert "bondradar-live-operations-paper-execute.timer" not in text
    assert "systemctl enable --now bondradar-live-operations-paper-execute.timer" not in text


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_render_module()
    output_path = tmp_path / "first_deploy.json"

    exit_code = module.main(["--json-output", str(output_path)])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["server_ip"] == "<SERVER_IP>"
    assert payload["sections"]


def test_markdown_output_is_written(tmp_path: Path) -> None:
    module = load_render_module()
    output_path = tmp_path / "first_deploy.md"

    exit_code = module.main(["--markdown-output", str(output_path)])

    assert exit_code == 0
    text = output_path.read_text(encoding="utf-8")
    assert text.startswith("# BondRadar First Deploy Commands")
    assert "```bash" in text
