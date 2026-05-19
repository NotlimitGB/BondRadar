from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run production-like BondRadar smoke checks against a running stack.",
    )
    parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL.",
    )
    parser.add_argument(
        "--frontend-url",
        default="http://127.0.0.1:5173",
        help="Frontend base URL.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10,
        help="HTTP timeout per request.",
    )
    parser.add_argument(
        "--model-run-id",
        type=int,
        default=None,
        help="Optional completed model run id for pre-deploy quality gate smoke.",
    )
    parser.add_argument(
        "--date-from",
        default=None,
        help="Optional quality-gate date_from in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--date-to",
        default=None,
        help="Optional quality-gate date_to in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Write a machine-readable smoke report to this path.",
    )
    parser.add_argument(
        "--skip-quality-gate",
        action="store_true",
        help="Skip the optional pre-deploy quality gate smoke.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed smoke check.",
    )
    return parser.parse_args(argv)


def build_quality_gate_payload(args: argparse.Namespace) -> dict[str, Any]:
    date_to = (
        date.fromisoformat(args.date_to)
        if args.date_to
        else datetime.now(timezone.utc).date()
    )
    date_from = (
        date.fromisoformat(args.date_from)
        if args.date_from
        else date_to - timedelta(days=90)
    )
    return {
        "model_run_id": args.model_run_id,
        "return_method": "risk_adjusted",
        "horizon_days": 30,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "include_scheduler_dry_run": True,
        "include_detailed_payloads": True,
    }


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    body: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "status_code": response.status,
                "text": raw,
                "json": _json_or_none(raw),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "status_code": exc.code,
            "text": raw,
            "json": _json_or_none(raw),
            "error": str(exc),
        }
    except urllib.error.URLError as exc:
        return {
            "status_code": None,
            "text": "",
            "json": None,
            "error": str(exc.reason),
        }
    except TimeoutError as exc:
        return {
            "status_code": None,
            "text": "",
            "json": None,
            "error": str(exc),
        }


def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    backend_url = args.backend_url.rstrip("/")
    frontend_url = args.frontend_url.rstrip("/")
    checks: list[dict[str, Any]] = []

    check_specs = [
        (
            "backend_health",
            "GET",
            f"{backend_url}/api/health",
            None,
            _validate_health,
        ),
        (
            "frontend_root",
            "GET",
            f"{frontend_url}/",
            None,
            _validate_frontend_root,
        ),
        (
            "frontend_api_proxy",
            "GET",
            f"{frontend_url}/api/health",
            None,
            _validate_health,
        ),
        (
            "corporate_universe_action_plan",
            "GET",
            f"{backend_url}/api/data-readiness/corporate-universe/action-plan",
            None,
            _validate_corporate_universe,
        ),
        (
            "live_data_readiness",
            "GET",
            f"{backend_url}/api/data-readiness/live",
            None,
            _validate_live_data_readiness,
        ),
        (
            "live_data_action_plan",
            "GET",
            f"{backend_url}/api/data-readiness/live/action-plan",
            None,
            _validate_live_data_action_plan,
        ),
    ]

    for name, method, url, payload, validator in check_specs:
        result = _run_http_check(
            name=name,
            method=method,
            url=url,
            payload=payload,
            timeout_seconds=args.timeout_seconds,
            validator=validator,
        )
        checks.append(result)
        _print_check(result)
        if args.fail_fast and result["status"] == "failed":
            return _report(args, started_at, checks)

    quality_gate = _run_quality_gate_check(args, backend_url)
    checks.append(quality_gate)
    _print_check(quality_gate)

    return _report(args, started_at, checks)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_checks(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[smoke] wrote JSON report: {args.json_output}", flush=True)
    return 0 if report["status"] == "passed" else 1


def _run_quality_gate_check(
    args: argparse.Namespace,
    backend_url: str,
) -> dict[str, Any]:
    url = f"{backend_url}/api/pre-deploy/paper-pilot/quality-gate"
    if args.skip_quality_gate:
        return _skipped_check(
            "pre_deploy_quality_gate",
            "POST",
            url,
            "Skipped by --skip-quality-gate.",
        )
    if args.model_run_id is None:
        return _skipped_check(
            "pre_deploy_quality_gate",
            "POST",
            url,
            "Skipped because --model-run-id was not provided.",
        )
    payload = build_quality_gate_payload(args)
    return _run_http_check(
        name="pre_deploy_quality_gate",
        method="POST",
        url=url,
        payload=payload,
        timeout_seconds=args.timeout_seconds,
        validator=_validate_quality_gate,
    )


def _run_http_check(
    *,
    name: str,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
    validator: Any,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = http_json(
        method,
        url,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    duration = time.perf_counter() - started
    status_value, message, details = validator(response)
    return {
        "name": name,
        "method": method,
        "url": url,
        "status": status_value,
        "status_code": response["status_code"],
        "duration_seconds": round(duration, 3),
        "message": message,
        "details": details,
    }


def _skipped_check(
    name: str,
    method: str,
    url: str,
    message: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "method": method,
        "url": url,
        "status": "skipped",
        "status_code": None,
        "duration_seconds": 0,
        "message": message,
        "details": {},
    }


def _report(
    args: argparse.Namespace,
    started_at: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    status_value = (
        "failed" if any(check["status"] == "failed" for check in checks) else "passed"
    )
    return {
        "status": status_value,
        "started_at": started_at,
        "finished_at": utc_now(),
        "backend_url": args.backend_url,
        "frontend_url": args.frontend_url,
        "checks": checks,
    }


def _print_check(check: dict[str, Any]) -> None:
    print(f"[smoke] {check['name']}: {check['status']}", flush=True)


def _validate_health(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    if response["status_code"] == 200 and isinstance(data, dict) and data.get("status") == "ok":
        return "passed", "Health endpoint returned ok.", {}
    return "failed", "Health endpoint did not return expected ok response.", _details(response)


def _validate_frontend_root(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    text = response.get("text") or ""
    looks_like_html = "<html" in text.lower() or "<!doctype html" in text.lower()
    if response["status_code"] == 200 and looks_like_html:
        return "passed", "Frontend root returned HTML.", {}
    return "failed", "Frontend root did not return expected HTML.", _details(response)


def _validate_corporate_universe(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {"status", "can_sync_universe", "can_continue_to_data_pipeline"}
    if response["status_code"] == 200 and isinstance(data, dict) and required <= data.keys():
        return "passed", "Corporate universe action plan endpoint shape is valid.", {}
    return "failed", "Corporate universe action plan endpoint shape is invalid.", _details(response)


def _validate_live_data_readiness(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {"status", "checks", "next_steps"}
    if response["status_code"] == 200 and isinstance(data, dict) and required <= data.keys():
        return "passed", "Live data readiness endpoint shape is valid.", {}
    return "failed", "Live data readiness endpoint shape is invalid.", _details(response)


def _validate_live_data_action_plan(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {"status", "pipeline_payload", "commands"}
    if response["status_code"] == 200 and isinstance(data, dict) and required <= data.keys():
        return "passed", "Live data action plan endpoint shape is valid.", {}
    return "failed", "Live data action plan endpoint shape is invalid.", _details(response)


def _validate_quality_gate(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {
        "status",
        "ready_for_50k_paper_pilot",
        "ready_for_vds_deploy",
        "gates",
        "payloads",
    }
    if response["status_code"] != 200 or not isinstance(data, dict) or not required <= data.keys():
        return "failed", "Pre-deploy quality gate endpoint shape is invalid.", _details(response)
    payloads = data.get("payloads") or {}
    pilot_payload = payloads.get("pilot_bootstrap_dry_run_request") or {}
    scheduler_payload = payloads.get("scheduler_dry_run_request") or {}
    if pilot_payload.get("dry_run_only") is not True:
        return (
            "failed",
            "Pre-deploy quality gate did not return dry-run-only pilot payload.",
            {"pilot_bootstrap_dry_run_request": pilot_payload},
        )
    if scheduler_payload.get("dry_run") is not True:
        return (
            "failed",
            "Pre-deploy quality gate did not return scheduler dry-run payload.",
            {"scheduler_dry_run_request": scheduler_payload},
        )
    return "passed", "Pre-deploy quality gate endpoint shape and dry-run payloads are valid.", {}


def _details(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "status_code": response.get("status_code"),
        "error": response.get("error"),
        "json": response.get("json"),
        "text": (response.get("text") or "")[:500],
    }


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
