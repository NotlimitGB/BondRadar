from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_guardrails.py"


def load_guardrails_module() -> Any:
    spec = importlib.util.spec_from_file_location("ci_guardrails", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_script(root: Path, name: str, text: str) -> Path:
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_allowed_run_due_endpoint_passes(tmp_path: Path) -> None:
    module = load_guardrails_module()
    write_script(
        tmp_path,
        "safe.py",
        'PATH = "/api/paper-trading/live/schedules/run-due"\n',
    )

    report = module.scan_scripts(tmp_path)

    assert report["status"] == "passed"
    assert report["violations"] == []


def test_forbidden_endpoint_string_fails(tmp_path: Path) -> None:
    module = load_guardrails_module()
    disallowed = "/api/paper-trading/live/schedules/" + "{id}" + "/run"
    write_script(tmp_path, "unsafe.py", f'PATH = "{disallowed}"\n')

    report = module.scan_scripts(tmp_path)

    assert report["status"] == "failed"
    assert report["violations"][0]["pattern"] == disallowed


def test_scans_scripts_py_only(tmp_path: Path) -> None:
    module = load_guardrails_module()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    docs_dir.joinpath("note.md").write_text(
        "/api/paper-trading/live/cycles/run\n",
        encoding="utf-8",
    )
    write_script(tmp_path, "safe.py", "VALUE = 1\n")

    report = module.scan_scripts(tmp_path)

    assert report["status"] == "passed"
    assert len(report["scanned_files"]) == 1


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_guardrails_module()
    write_script(tmp_path, "safe.py", "VALUE = 1\n")
    output_path = tmp_path / "guardrails.json"

    exit_code = module.main(["--root", str(tmp_path), "--json-output", str(output_path)])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
