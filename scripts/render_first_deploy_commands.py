from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


DEFAULT_SERVER_IP = "<SERVER_IP>"
DEFAULT_REPO_URL = "<REPO_URL>"
DEFAULT_DEPLOY_DIR = "/opt/BondRadar"
DEFAULT_DEPLOY_USER = "bondradar"
DEFAULT_PYTHON_BIN = "python3"
DEFAULT_FRONTEND_PORT = "5173"
DEFAULT_BACKEND_PORT = "8000"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render BondRadar first-deploy commands without executing them.",
    )
    parser.add_argument("--server-ip", default=DEFAULT_SERVER_IP)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--deploy-dir", default=DEFAULT_DEPLOY_DIR)
    parser.add_argument("--deploy-user", default=DEFAULT_DEPLOY_USER)
    parser.add_argument("--python-bin", default=DEFAULT_PYTHON_BIN)
    parser.add_argument("--frontend-port", default=DEFAULT_FRONTEND_PORT)
    parser.add_argument("--backend-port", default=DEFAULT_BACKEND_PORT)
    parser.add_argument(
        "--access-mode",
        choices=("private", "public-dev"),
        default="private",
        help="private uses SSH tunnel access; public-dev renders app port firewall examples with warnings.",
    )
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_command_report(args: argparse.Namespace) -> dict[str, Any]:
    backend_url = f"http://127.0.0.1:{args.backend_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    private_tunnel = (
        f"ssh -L {args.frontend_port}:127.0.0.1:{args.frontend_port} "
        f"-L {args.backend_port}:127.0.0.1:{args.backend_port} "
        f"{args.deploy_user}@{args.server_ip}"
    )
    firewall_commands = [
        "sudo ufw allow OpenSSH",
        "sudo ufw enable",
        "sudo ufw status",
    ]
    firewall_warning = None
    if args.access_mode == "public-dev":
        firewall_commands = [
            "sudo ufw allow OpenSSH",
            f"sudo ufw allow {args.frontend_port}/tcp",
            f"sudo ufw allow {args.backend_port}/tcp",
            "sudo ufw enable",
            "sudo ufw status",
        ]
        firewall_warning = (
            "Not recommended for the first private VDS deployment. Do not use "
            "for public or team operation without auth, HTTPS, and reverse-proxy hardening."
        )

    return {
        "server_ip": args.server_ip,
        "repo_url": args.repo_url,
        "deploy_dir": args.deploy_dir,
        "deploy_user": args.deploy_user,
        "python_bin": args.python_bin,
        "frontend_port": str(args.frontend_port),
        "backend_port": str(args.backend_port),
        "access_mode": args.access_mode,
        "sections": [
            {
                "title": "SSH login",
                "commands": [
                    f"ssh root@{args.server_ip}",
                    f"ssh -i ~/.ssh/<key_name> root@{args.server_ip}",
                ],
            },
            {
                "title": "create deploy user",
                "commands": [
                    f"adduser {args.deploy_user}",
                    f"usermod -aG sudo {args.deploy_user}",
                    f"rsync --archive --chown={args.deploy_user}:{args.deploy_user} ~/.ssh /home/{args.deploy_user}",
                    f"su - {args.deploy_user}",
                ],
            },
            {
                "title": "firewall",
                "commands": firewall_commands,
                **({"warning": firewall_warning} if firewall_warning else {}),
            },
            {
                "title": "SSH tunnel",
                "commands": [
                    private_tunnel,
                ],
                "note": (
                    "Keep this session open, then use the local browser at "
                    f"{frontend_url} and local API checks at {backend_url}."
                ),
            },
            {
                "title": "clone repository",
                "commands": [
                    f"sudo mkdir -p {args.deploy_dir}",
                    f"sudo chown -R {args.deploy_user}:{args.deploy_user} {args.deploy_dir}",
                    f"git clone {args.repo_url} {args.deploy_dir}",
                    f"cd {args.deploy_dir}",
                ],
            },
            {
                "title": "prepare env",
                "commands": [
                    "cp .env.production.example .env.production",
                    "nano .env.production",
                    "mkdir -p logs backups",
                ],
            },
            {
                "title": "validate env",
                "commands": [
                    (
                        f"{args.python_bin} scripts/validate_production_env.py "
                        "--env-file .env.production "
                        "--json-output ./logs/env_validation.json"
                    ),
                    (
                        f"{args.python_bin} scripts/server_sanity_check.py "
                        "--env-file .env.production "
                        "--json-output ./logs/server_sanity.json"
                    ),
                ],
            },
            {
                "title": "build/start compose",
                "commands": [
                    "docker compose -f docker-compose.prod.yml --env-file .env.production build",
                    "docker compose -f docker-compose.prod.yml --env-file .env.production up -d",
                    "docker compose -f docker-compose.prod.yml --env-file .env.production ps",
                ],
            },
            {
                "title": "health checks",
                "commands": [
                    f"curl -s {backend_url}/api/health",
                    f"curl -s {frontend_url}/",
                    f"curl -s {frontend_url}/api/health",
                ],
            },
            {
                "title": "smoke checks",
                "commands": [
                    (
                        f"{args.python_bin} scripts/prod_smoke_check.py "
                        f"--backend-url {backend_url} "
                        f"--frontend-url {frontend_url} "
                        "--json-output ./logs/prod_smoke.json"
                    ),
                ],
            },
            {
                "title": "bootstrap plan",
                "commands": [
                    (
                        f"{args.python_bin} scripts/live_data_bootstrap.py "
                        f"--backend-url {backend_url} "
                        "--json-output ./logs/live_data_bootstrap_plan.json"
                    ),
                ],
            },
            {
                "title": "monitoring check",
                "commands": [
                    (
                        f"{args.python_bin} scripts/live_operations_runner.py "
                        f"--backend-url {backend_url} "
                        "--mode monitoring "
                        "--json-output ./logs/live_ops_monitoring.json"
                    ),
                ],
            },
            {
                "title": "backup check",
                "commands": [
                    "set -a && . ./.env.production && set +a && bash scripts/postgres_backup.sh",
                    "ls -lah backups",
                ],
            },
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar First Deploy Commands",
        "",
        f"Server IP: `{report['server_ip']}`",
        f"Repository URL: `{report['repo_url']}`",
        f"Deploy directory: `{report['deploy_dir']}`",
        f"Deploy user: `{report['deploy_user']}`",
        f"Access mode: `{report.get('access_mode', 'private')}`",
        "",
        "These commands are rendered for operator review. Run them manually and adjust provider-specific steps when needed.",
        "Default private mode keeps app ports local to the VDS and uses an SSH tunnel for operator access.",
        "",
    ]
    for section in report["sections"]:
        lines.extend([f"## {section['title']}", ""])
        if section.get("warning"):
            lines.extend([f"> Warning: {section['warning']}", ""])
        if section.get("note"):
            lines.extend([section["note"], ""])
        lines.extend(
            [
                "```bash",
                *section["commands"],
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_command_report(args)
    markdown = render_markdown(report)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[first-deploy] wrote JSON commands: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[first-deploy] wrote Markdown commands: {args.markdown_output}", flush=True)
    print(markdown, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
