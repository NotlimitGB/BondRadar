from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops_retention.py"


def load_retention_module() -> Any:
    spec = importlib.util.spec_from_file_location("ops_retention", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def touch_old(path: Path, *, days: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data", encoding="utf-8")
    timestamp = time.time() - days * 24 * 60 * 60
    os.utime(path, (timestamp, timestamp))


def test_dry_run_does_not_delete_files(tmp_path: Path) -> None:
    module = load_retention_module()
    logs = tmp_path / "logs"
    backups = tmp_path / "backups"
    old_log = logs / "old.json"
    old_backup = backups / "old.dump"
    touch_old(old_log, days=40)
    touch_old(old_backup, days=20)

    report = module.run_retention(
        module.parse_args(["--logs-dir", str(logs), "--backups-dir", str(backups)])
    )

    assert report["status"] == "planned"
    assert report["dry_run"] is True
    assert old_log.exists()
    assert old_backup.exists()
    assert report["deleted_count"] == 0


def test_execute_deletes_only_old_matching_files(tmp_path: Path) -> None:
    module = load_retention_module()
    logs = tmp_path / "logs"
    backups = tmp_path / "backups"
    old_log = logs / "old.log"
    new_log = logs / "new.log"
    ignored = logs / "old.txt"
    old_backup = backups / "old.sql"
    touch_old(old_log, days=40)
    touch_old(new_log, days=1)
    touch_old(ignored, days=40)
    touch_old(old_backup, days=20)

    report = module.run_retention(
        module.parse_args(
            [
                "--logs-dir",
                str(logs),
                "--backups-dir",
                str(backups),
                "--execute",
            ]
        )
    )

    assert report["status"] == "completed"
    assert not old_log.exists()
    assert not old_backup.exists()
    assert new_log.exists()
    assert ignored.exists()
    assert report["deleted_count"] == 2


def test_directories_are_not_deleted(tmp_path: Path) -> None:
    module = load_retention_module()
    logs = tmp_path / "logs"
    directory = logs / "old.log"
    directory.mkdir(parents=True)
    timestamp = time.time() - 40 * 24 * 60 * 60
    os.utime(directory, (timestamp, timestamp))

    report = module.run_retention(
        module.parse_args(["--logs-dir", str(logs), "--backups-dir", str(tmp_path / "backups"), "--execute"])
    )

    assert directory.exists()
    assert report["candidate_count"] == 0


def test_symlinks_are_not_followed_or_deleted(tmp_path: Path) -> None:
    module = load_retention_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    target = logs / "target.txt"
    link = logs / "link.log"
    touch_old(target, days=1)
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    timestamp = time.time() - 40 * 24 * 60 * 60
    try:
        os.utime(link, (timestamp, timestamp), follow_symlinks=False)
    except (NotImplementedError, OSError):
        pass

    report = module.run_retention(
        module.parse_args(["--logs-dir", str(logs), "--backups-dir", str(tmp_path / "backups"), "--execute"])
    )

    assert link.exists()
    assert target.exists()
    assert report["candidate_count"] == 0


def test_json_output_is_written(tmp_path: Path) -> None:
    module = load_retention_module()
    logs = tmp_path / "logs"
    old_log = logs / "old.json"
    touch_old(old_log, days=40)
    output_path = tmp_path / "retention.json"

    exit_code = module.main(
        [
            "--logs-dir",
            str(logs),
            "--backups-dir",
            str(tmp_path / "backups"),
            "--json-output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "planned"' in content
    assert "old.json" in content
