from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence


REQUIRED_FILES = [
    "docker-compose.prod.yml",
    ".env.production.example",
    "scripts/render_first_deploy_commands.py",
    "docs/deployment/PRIVATE_VDS_SECURITY_BASELINE.md",
    "docs/deployment/SECURITY_DEBT_REGISTER.md",
]

FORBIDDEN_PRIVATE_RENDER_STRINGS = [
    "sudo ufw allow 5173/tcp",
    "sudo ufw allow 8000/tcp",
    "--execute-due-schedules",
    "bondradar-live-operations-paper-execute.timer",
    "systemctl enable --now bondradar-live-operations-paper-execute.timer",
]

REQUIRED_PRIVATE_RENDER_STRINGS = [
    "ssh -L",
    "sudo ufw allow OpenSSH",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check BondRadar private VDS exposure posture without remote calls.",
    )
    parser.add_argument("--compose-file", default="docker-compose.prod.yml")
    parser.add_argument("--env-file", default=".env.production.example")
    parser.add_argument("--render-commands", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def make_check(
    name: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }


def read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, str(exc)


def check_required_files(root: Path, compose_file: str, env_file: str) -> list[dict[str, Any]]:
    required = list(REQUIRED_FILES)
    required[0] = compose_file
    required[1] = env_file
    checks: list[dict[str, Any]] = []
    missing = [path for path in required if not (root / path).exists()]
    if missing:
        checks.append(
            make_check(
                "required_files",
                "failed",
                "Required private deployment files are missing.",
                {"missing": missing},
            )
        )
    else:
        checks.append(
            make_check(
                "required_files",
                "passed",
                "Required private deployment files are present.",
                {"files": required},
            )
        )
    return checks


def public_binding_lines(compose_text: str, container_port: str) -> list[str]:
    lines: list[str] = []
    port_pattern = re.compile(rf"(?<!\d){re.escape(container_port)}\s*$")
    for raw_line in compose_text.splitlines():
        line = raw_line.strip()
        normalized = line.replace('"', "").replace("'", "")
        if not line or line.startswith("#") or ":" not in line:
            continue
        if f":{container_port}" not in normalized:
            continue
        if "127.0.0.1:" in normalized or "localhost:" in normalized:
            continue
        if "0.0.0.0:" in normalized or port_pattern.search(normalized):
            lines.append(raw_line.strip())
            continue
        if re.search(rf"(^|[-\s])(?:\$\{{[^}}]+}}|{container_port})\s*:\s*{container_port}", normalized):
            lines.append(raw_line.strip())
    return lines


def localhost_binding_lines(compose_text: str, container_port: str) -> list[str]:
    return [
        raw_line.strip()
        for raw_line in compose_text.splitlines()
        if f":{container_port}" in raw_line and ("127.0.0.1:" in raw_line or "localhost:" in raw_line)
    ]


def check_compose_exposure(root: Path, compose_file: str) -> list[dict[str, Any]]:
    compose_path = root / compose_file
    compose_text, error = read_text(compose_path)
    if error or compose_text is None:
        return [
            make_check(
                "compose_exposure",
                "failed",
                "Could not read production compose file.",
                {"path": str(compose_path), "error": error},
            )
        ]

    checks: list[dict[str, Any]] = []
    postgres_public = public_binding_lines(compose_text, "5432")
    if postgres_public:
        checks.append(
            make_check(
                "postgres_localhost_binding",
                "failed",
                "PostgreSQL appears to have a public host port binding.",
                {"public_lines": postgres_public},
            )
        )
    elif localhost_binding_lines(compose_text, "5432"):
        checks.append(
            make_check(
                "postgres_localhost_binding",
                "passed",
                "PostgreSQL host binding appears localhost-only.",
            )
        )
    else:
        checks.append(
            make_check(
                "postgres_localhost_binding",
                "warning",
                "Could not confidently find a PostgreSQL host port binding.",
            )
        )

    app_public = public_binding_lines(compose_text, "8000") + public_binding_lines(compose_text, "5173")
    if app_public:
        checks.append(
            make_check(
                "app_localhost_bindings",
                "failed",
                "Backend or frontend appears to have a public host port binding.",
                {"public_lines": app_public},
            )
        )
    else:
        missing = [
            port
            for port in ("8000", "5173")
            if not localhost_binding_lines(compose_text, port)
        ]
        if missing:
            checks.append(
                make_check(
                    "app_localhost_bindings",
                    "warning",
                    "Could not confidently find localhost-only app port bindings.",
                    {"missing_container_ports": missing},
                )
            )
        else:
            checks.append(
                make_check(
                    "app_localhost_bindings",
                    "passed",
                    "Backend and frontend host bindings appear localhost-only.",
                )
            )
    return checks


def render_private_commands(root: Path) -> tuple[str | None, str | None]:
    script_path = root / "scripts" / "render_first_deploy_commands.py"
    try:
        spec = importlib.util.spec_from_file_location("render_first_deploy_commands_checked", script_path)
        if spec is None or spec.loader is None:
            return None, f"Could not load spec for {script_path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        args = module.parse_args(["--access-mode", "private"])
        report = module.build_command_report(args)
        text = json.dumps(report, ensure_ascii=False) + "\n" + module.render_markdown(report)
        return text, None
    except Exception as exc:  # pragma: no cover - defensive for operator CLI use.
        return None, str(exc)


def check_rendered_private_commands(root: Path) -> list[dict[str, Any]]:
    rendered, error = render_private_commands(root)
    if error or rendered is None:
        return [
            make_check(
                "private_firewall_commands",
                "failed",
                "Could not render private first-deploy commands.",
                {"error": error},
            )
        ]

    missing = [value for value in REQUIRED_PRIVATE_RENDER_STRINGS if value not in rendered]
    forbidden = [value for value in FORBIDDEN_PRIVATE_RENDER_STRINGS if value in rendered]
    if missing or forbidden:
        return [
            make_check(
                "private_firewall_commands",
                "failed",
                "Rendered private commands do not match private-by-default expectations.",
                {"missing": missing, "forbidden": forbidden},
            )
        ]
    return [
        make_check(
            "private_firewall_commands",
            "passed",
            "Rendered private commands use SSH tunnel access and do not expose app ports.",
        )
    ]


def finalize_report(checks: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    errors = [check["message"] for check in checks if check["status"] == "failed"]
    warnings = [check["message"] for check in checks if check["status"] == "warning"]
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "root": str(root),
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
    }


def run_checks(args: argparse.Namespace, root: Path | None = None) -> dict[str, Any]:
    repo_root = root or project_root()
    checks: list[dict[str, Any]] = []
    checks.extend(check_required_files(repo_root, args.compose_file, args.env_file))
    checks.extend(check_compose_exposure(repo_root, args.compose_file))
    if args.render_commands:
        checks.extend(check_rendered_private_commands(repo_root))
    return finalize_report(checks, repo_root)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def exit_code_for_report(report: dict[str, Any], strict: bool) -> int:
    if report["status"] == "failed":
        return 1
    if strict and report["status"] == "warning":
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_checks(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[private-vds] wrote JSON report: {args.json_output}", flush=True)
    for check in report["checks"]:
        print(f"[private-vds] {check['name']}: {check['status']} - {check['message']}", flush=True)
    print(f"[private-vds] status: {report['status']}", flush=True)
    return exit_code_for_report(report, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
