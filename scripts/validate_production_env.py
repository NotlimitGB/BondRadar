from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse


REQUIRED_VARS = [
    "ENVIRONMENT",
    "PROJECT_NAME",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "API_PREFIX",
    "MOEX_ISS_BASE_URL",
    "MOEX_ISS_TIMEOUT_SECONDS",
    "ML_ARTIFACT_DIR",
    "FRONTEND_PORT",
    "BACKEND_CORS_ORIGINS",
    "POSTGRES_HOST",
    "BACKUP_DIR",
    "PGPASSWORD",
]

SAMPLE_SECRET = "replace-with-a-strong-password"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate BondRadar production environment settings.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.production"))
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as a failed validation.",
    )
    return parser.parse_args(argv)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(raw_value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    previous = ""
    for index, char in enumerate(value):
        if char in {"'", '"'} and previous != "\\":
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None and (index == 0 or previous.isspace()):
            return value[:index]
        previous = char
    return value


def validate_env_file(path: Path, *, strict: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not path.exists():
        _add_check(
            checks,
            errors,
            warnings,
            "env_file_exists",
            "failed",
            "Environment file was not found.",
            {"env_file": str(path)},
        )
        return _report(path, checks, errors, warnings, strict=strict)

    values = parse_env_file(path)
    for name in REQUIRED_VARS:
        _add_check(
            checks,
            errors,
            warnings,
            f"required_{name.lower()}",
            "passed" if values.get(name, "").strip() else "failed",
            f"{name} is present." if values.get(name, "").strip() else f"{name} is required.",
            {},
        )

    if errors:
        return _report(path, checks, errors, warnings, strict=strict)

    _validate_equals(
        checks,
        errors,
        warnings,
        "environment_is_production",
        values["ENVIRONMENT"],
        "production",
        "ENVIRONMENT must be production.",
    )
    _add_check(
        checks,
        errors,
        warnings,
        "api_prefix_format",
        "passed" if values["API_PREFIX"].startswith("/") else "failed",
        "API_PREFIX starts with /." if values["API_PREFIX"].startswith("/") else "API_PREFIX must start with /.",
        {"value": values["API_PREFIX"]},
    )
    _validate_secret_changed(
        checks,
        errors,
        warnings,
        "postgres_password_changed",
        values["POSTGRES_PASSWORD"],
        "POSTGRES_PASSWORD must be changed from the sample value.",
    )
    _validate_secret_changed(
        checks,
        errors,
        warnings,
        "pgpassword_changed",
        values["PGPASSWORD"],
        "PGPASSWORD must be changed from the sample value.",
    )
    _add_check(
        checks,
        errors,
        warnings,
        "postgres_password_matches_pgpassword",
        "passed" if values["POSTGRES_PASSWORD"] == values["PGPASSWORD"] else "failed",
        "POSTGRES_PASSWORD matches PGPASSWORD."
        if values["POSTGRES_PASSWORD"] == values["PGPASSWORD"]
        else "POSTGRES_PASSWORD must match PGPASSWORD.",
        {},
    )
    _validate_database_url(checks, errors, warnings, values["DATABASE_URL"])
    _validate_equals(
        checks,
        errors,
        warnings,
        "postgres_host_localhost",
        values["POSTGRES_HOST"],
        "127.0.0.1",
        "POSTGRES_HOST must be 127.0.0.1 for host-level backup scripts.",
    )
    _validate_port(checks, errors, warnings, "postgres_port_valid", "POSTGRES_PORT", values["POSTGRES_PORT"])
    _validate_port(checks, errors, warnings, "frontend_port_valid", "FRONTEND_PORT", values["FRONTEND_PORT"])
    if "BACKEND_PORT" in values and values["BACKEND_PORT"].strip():
        same_port = values["BACKEND_PORT"] == values["FRONTEND_PORT"]
        _add_check(
            checks,
            errors,
            warnings,
            "frontend_backend_ports_differ",
            "failed" if same_port else "passed",
            "FRONTEND_PORT must differ from BACKEND_PORT." if same_port else "FRONTEND_PORT differs from BACKEND_PORT.",
            {"frontend_port": values["FRONTEND_PORT"], "backend_port": values["BACKEND_PORT"]},
        )
    _validate_positive_float(
        checks,
        errors,
        warnings,
        "moex_timeout_positive",
        "MOEX_ISS_TIMEOUT_SECONDS",
        values["MOEX_ISS_TIMEOUT_SECONDS"],
    )
    _validate_not_blank(checks, errors, warnings, "ml_artifact_dir_present", "ML_ARTIFACT_DIR", values["ML_ARTIFACT_DIR"])
    _validate_not_blank(checks, errors, warnings, "backup_dir_present", "BACKUP_DIR", values["BACKUP_DIR"])
    _validate_cors(checks, errors, warnings, values["BACKEND_CORS_ORIGINS"])

    return _report(path, checks, errors, warnings, strict=strict)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_env_file(args.env_file, strict=args.strict)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[env] wrote JSON report: {args.json_output}", flush=True)
    print(f"[env] {args.env_file}: {report['status']}", flush=True)
    return 0 if report["status"] in {"passed", "warning"} else 1


def _add_check(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    item = {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }
    checks.append(item)
    if status == "failed":
        errors.append(item)
    elif status == "warning":
        warnings.append(item)


def _report(
    path: Path,
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    strict: bool,
) -> dict[str, Any]:
    if errors or (strict and warnings):
        status = "failed"
    elif warnings:
        status = "warning"
    else:
        status = "passed"
    return {
        "status": status,
        "env_file": str(path),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }


def _validate_equals(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    value: str,
    expected: str,
    failed_message: str,
) -> None:
    passed = value == expected
    _add_check(
        checks,
        errors,
        warnings,
        name,
        "passed" if passed else "failed",
        f"{name} is valid." if passed else failed_message,
        {"value": value},
    )


def _validate_secret_changed(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    value: str,
    message: str,
) -> None:
    passed = bool(value.strip()) and value != SAMPLE_SECRET
    _add_check(
        checks,
        errors,
        warnings,
        name,
        "passed" if passed else "failed",
        f"{name} is valid." if passed else message,
        {},
    )


def _validate_database_url(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    value: str,
) -> None:
    no_sample_secret = SAMPLE_SECRET not in value
    _add_check(
        checks,
        errors,
        warnings,
        "database_url_secret_changed",
        "passed" if no_sample_secret else "failed",
        "DATABASE_URL does not contain the sample secret."
        if no_sample_secret
        else "DATABASE_URL must not contain the sample secret.",
        {},
    )
    parsed = urlparse(value)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    docker_host_ok = parsed.hostname == "postgres" and parsed_port == 5432
    _add_check(
        checks,
        errors,
        warnings,
        "database_url_docker_host",
        "passed" if docker_host_ok else "failed",
        "DATABASE_URL uses postgres:5432 inside Docker."
        if docker_host_ok
        else "DATABASE_URL must use postgres:5432 inside Docker.",
        {"hostname": parsed.hostname, "port": parsed_port},
    )


def _validate_port(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    label: str,
    value: str,
) -> None:
    try:
        port = int(value)
    except ValueError:
        port = -1
    passed = 1 <= port <= 65535
    _add_check(
        checks,
        errors,
        warnings,
        name,
        "passed" if passed else "failed",
        f"{label} is a valid TCP port." if passed else f"{label} must be an integer from 1 to 65535.",
        {"value": value},
    )


def _validate_positive_float(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    label: str,
    value: str,
) -> None:
    try:
        number = float(value)
    except ValueError:
        number = -1
    passed = number > 0
    _add_check(
        checks,
        errors,
        warnings,
        name,
        "passed" if passed else "failed",
        f"{label} is positive." if passed else f"{label} must be a positive number.",
        {"value": value},
    )


def _validate_not_blank(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    name: str,
    label: str,
    value: str,
) -> None:
    passed = bool(value.strip())
    _add_check(
        checks,
        errors,
        warnings,
        name,
        "passed" if passed else "failed",
        f"{label} is present." if passed else f"{label} must not be blank.",
        {},
    )


def _validate_cors(
    checks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    value: str,
) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        _add_check(
            checks,
            errors,
            warnings,
            "backend_cors_origins_json",
            "failed",
            "BACKEND_CORS_ORIGINS must be a JSON list.",
            {"error": str(exc)},
        )
        return
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        _add_check(
            checks,
            errors,
            warnings,
            "backend_cors_origins_json",
            "failed",
            "BACKEND_CORS_ORIGINS must be a JSON list of strings.",
            {"value": parsed},
        )
        return
    _add_check(
        checks,
        errors,
        warnings,
        "backend_cors_origins_json",
        "passed",
        "BACKEND_CORS_ORIGINS is a JSON list.",
        {"count": len(parsed)},
    )
    if not parsed:
        _add_check(
            checks,
            errors,
            warnings,
            "backend_cors_origins_non_empty",
            "warning",
            "BACKEND_CORS_ORIGINS is empty in production.",
            {},
        )


if __name__ == "__main__":
    raise SystemExit(main())
