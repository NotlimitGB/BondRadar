from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


def _patterns() -> list[str]:
    return [
        "/api/paper-trading/live/schedules/" + "{id}" + "/run",
        "/api/paper-trading/live/cycles" + "/run",
        "/" + "re" + "balance",
        "/" + "mark-" + "period",
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan BondRadar operational scripts for disallowed direct endpoint calls.",
    )
    parser.add_argument("--root", type=Path, default=_repo_root())
    parser.add_argument("--json-output", type=Path, default=None)
    return parser.parse_args(argv)


def scan_scripts(root: Path) -> dict[str, Any]:
    scripts_dir = root / "scripts"
    files = sorted(scripts_dir.glob("*.py")) if scripts_dir.is_dir() else []
    violations: list[dict[str, Any]] = []

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(
                {
                    "path": str(path),
                    "pattern": None,
                    "message": f"Could not read script: {exc}",
                }
            )
            continue

        for pattern in _patterns():
            if pattern in text:
                violations.append(
                    {
                        "path": str(path),
                        "pattern": pattern,
                        "message": "Disallowed direct operational endpoint/path string found.",
                    }
                )

    return {
        "status": "failed" if violations else "passed",
        "root": str(root),
        "scanned_files": [str(path) for path in files],
        "violations": violations,
    }


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = scan_scripts(args.root)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[ci-guardrails] wrote JSON report: {args.json_output}", flush=True)
    print(
        f"[ci-guardrails] {report['status']}: "
        f"{len(report['scanned_files'])} script(s), {len(report['violations'])} violation(s)",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    raise SystemExit(main())
