from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Sequence


LOG_PATTERNS = {".json", ".log"}
BACKUP_PATTERNS = {".dump", ".sql", ".gz"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or execute BondRadar log and backup retention cleanup.",
    )
    parser.add_argument("--logs-dir", type=Path, default=Path("./logs"))
    parser.add_argument("--backups-dir", type=Path, default=Path("./backups"))
    parser.add_argument("--logs-retention-days", type=int, default=30)
    parser.add_argument("--backups-retention-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true", help="Plan cleanup without deleting files. This is the default.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    return parser.parse_args(argv)


def run_retention(args: argparse.Namespace) -> dict[str, Any]:
    dry_run = not args.execute
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    now = time.time()

    _collect_candidates(
        args.logs_dir,
        kind="log",
        suffixes=LOG_PATTERNS,
        retention_days=args.logs_retention_days,
        now=now,
        candidates=candidates,
    )
    _collect_candidates(
        args.backups_dir,
        kind="backup",
        suffixes=BACKUP_PATTERNS,
        retention_days=args.backups_retention_days,
        now=now,
        candidates=candidates,
    )

    deleted_count = 0
    for item in candidates:
        if dry_run or not item["would_delete"]:
            continue
        path = Path(item["path"])
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
                item["deleted"] = True
                deleted_count += 1
        except OSError as exc:
            item["deleted"] = False
            errors.append({"path": str(path), "message": str(exc)})

    status = "failed" if errors else ("planned" if dry_run else "completed")
    return {
        "status": status,
        "dry_run": dry_run,
        "deleted_count": deleted_count,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "errors": errors,
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_retention(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[retention] wrote JSON report: {args.json_output}", flush=True)
    print(f"[retention] {report['status']}: {report['candidate_count']} candidates", flush=True)
    return 0 if report["status"] in {"planned", "completed"} else 1


def _collect_candidates(
    root: Path,
    *,
    kind: str,
    suffixes: set[str],
    retention_days: int,
    now: float,
    candidates: list[dict[str, Any]],
) -> None:
    if not root.exists() or not root.is_dir():
        return
    cutoff_seconds = max(retention_days, 0) * 24 * 60 * 60
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        age_seconds = max(0.0, now - path.stat().st_mtime)
        age_days = int(age_seconds // (24 * 60 * 60))
        would_delete = age_seconds > cutoff_seconds
        candidates.append(
            {
                "path": str(path),
                "kind": kind,
                "age_days": age_days,
                "would_delete": would_delete,
                "deleted": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
