from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence


TERMINAL_PIPELINE_STATUSES = {"completed", "completed_with_errors", "failed"}
CONFIRMATION_MODES = {"paper-execute", "full-cycle"}
RUN_DUE_PATH = "/api/paper-trading/live/schedules/run-due"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run safe BondRadar live operations checks and confirmed virtual paper operations.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument(
        "--mode",
        default="monitoring",
        choices=[
            "monitoring",
            "data-refresh",
            "paper-dry-run",
            "paper-execute",
            "full-cycle",
        ],
    )

    parser.add_argument("--recent-days", type=int, default=7)
    parser.add_argument("--minimum-corporate-bonds", type=int, default=20)
    parser.add_argument(
        "--minimum-bonds-with-recent-market-snapshot",
        type=int,
        default=20,
    )
    parser.add_argument("--minimum-bonds-with-recent-features", type=int, default=20)
    parser.add_argument("--minimum-bonds-with-predictions", type=int, default=20)
    parser.add_argument("--include-ofz", action="store_true")

    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument("--return-method", default="risk_adjusted")

    parser.add_argument("--execute-data-pipeline", action="store_true")
    parser.add_argument("--wait-pipeline", action="store_true")
    parser.add_argument("--pipeline-poll-interval-seconds", type=float, default=5)
    parser.add_argument("--pipeline-timeout-seconds", type=float, default=1800)

    parser.add_argument("--execute-due-schedules", action="store_true")
    parser.add_argument("--run-due-now", default=None)

    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--confirm-live-operations", default=None)
    return parser.parse_args(argv)


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    date_to = (
        date.fromisoformat(args.date_to)
        if args.date_to
        else datetime.now(timezone.utc).date()
    )
    date_from = (
        date.fromisoformat(args.date_from)
        if args.date_from
        else date_to - timedelta(days=args.recent_days)
    )
    return date_from.isoformat(), date_to.isoformat()


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 30,
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


def run_operations(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    date_from, date_to = resolve_dates(args)
    backend_url = args.backend_url.rstrip("/")
    steps: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "live_data_status": None,
        "live_data_action_plan_status": None,
        "pipeline_run_id": None,
        "pipeline_final_status": None,
        "monitoring_status": None,
        "dry_run_due_count": None,
        "executed_due_count": None,
        "alerts_count": None,
    }

    if _confirmation_required(args):
        step = _manual_step(
            "confirmation_required",
            "failed",
            "Mutation or execution mode requires --confirm-live-operations yes.",
            details={
                "mode": args.mode,
                "execute_data_pipeline": args.execute_data_pipeline,
                "execute_due_schedules": args.execute_due_schedules,
            },
        )
        steps.append(step)
        _print_step(step)
        summary["exit_reason"] = "confirmation_required"
        return _report(
            status_value="failed",
            started_at=started_at,
            args=args,
            date_from=date_from,
            date_to=date_to,
            steps=steps,
            summary=summary,
        )

    _run_health(args, steps, backend_url)
    if _should_stop(args, steps):
        return _finish(started_at, args, date_from, date_to, steps, summary)

    live_data = _run_live_readiness(args, steps, backend_url)
    summary["live_data_status"] = live_data.get("status")
    if _should_stop(args, steps):
        return _finish(started_at, args, date_from, date_to, steps, summary)

    action_plan: dict[str, Any] = {}
    if args.mode in {"monitoring", "data-refresh", "full-cycle"}:
        action_plan = _run_live_action_plan(
            args,
            steps,
            backend_url,
            date_from,
            date_to,
        )
        summary["live_data_action_plan_status"] = action_plan.get("status")
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)

    if args.mode in {"data-refresh", "full-cycle"}:
        if args.execute_data_pipeline:
            _run_data_pipeline_flow(
                args,
                steps,
                backend_url,
                action_plan,
                summary,
            )
            if _should_stop(args, steps):
                return _finish(started_at, args, date_from, date_to, steps, summary)
            live_data = _run_live_readiness_after(
                args,
                steps,
                backend_url,
                "live_data_readiness_after_pipeline",
            )
            summary["live_data_status"] = live_data.get("status")
            if _should_stop(args, steps):
                return _finish(started_at, args, date_from, date_to, steps, summary)
            action_plan = _run_live_action_plan(
                args,
                steps,
                backend_url,
                date_from,
                date_to,
                name="live_data_action_plan_after_pipeline",
            )
            summary["live_data_action_plan_status"] = action_plan.get("status")
            if _should_stop(args, steps):
                return _finish(started_at, args, date_from, date_to, steps, summary)
        else:
            _append_skipped(
                steps,
                "data_pipeline_run",
                "Data pipeline run was not requested.",
            )

    if args.mode in {"monitoring", "data-refresh", "full-cycle", "paper-dry-run", "paper-execute"}:
        monitoring = _run_monitoring(args, steps, backend_url)
        _update_monitoring_summary(summary, monitoring)
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)

    if args.mode in {"paper-dry-run", "paper-execute", "full-cycle"}:
        dry_run_data = _run_scheduler_due_dry_run(args, steps, backend_url)
        summary["dry_run_due_count"] = _int_or_none(dry_run_data.get("due_schedule_count"))
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)
        if args.execute_due_schedules:
            execution_data = _run_scheduler_due_execution(args, steps, backend_url)
            summary["executed_due_count"] = _int_or_none(execution_data.get("executed_count"))
            if _should_stop(args, steps):
                return _finish(started_at, args, date_from, date_to, steps, summary)
            monitoring_after = _run_monitoring(
                args,
                steps,
                backend_url,
                name="monitoring_overview_after_execution",
            )
            _update_monitoring_summary(summary, monitoring_after)
        else:
            _append_skipped(
                steps,
                "scheduler_due_execution",
                "Due schedule execution was not requested.",
            )
    else:
        _append_skipped(
            steps,
            "scheduler_due_dry_run",
            "Scheduler due dry-run was not requested.",
        )
        _append_skipped(
            steps,
            "scheduler_due_execution",
            "Due schedule execution was not requested.",
        )

    return _finish(started_at, args, date_from, date_to, steps, summary)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_operations(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[operations] wrote JSON report: {args.json_output}", flush=True)
    if report["summary"].get("exit_reason") == "confirmation_required":
        return 2
    if report["status"] == "safety_failed":
        return 3
    if report["status"] == "failed":
        return 1
    return 0


def _run_health(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
) -> dict[str, Any]:
    return _request_step(
        args,
        steps,
        name="backend_health",
        method="GET",
        path="/api/health",
        url=f"{backend_url}/api/health",
        validator=_validate_health,
    )


def _run_live_readiness(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
) -> dict[str, Any]:
    return _run_live_readiness_after(args, steps, backend_url, "live_data_readiness")


def _run_live_readiness_after(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
    name: str,
) -> dict[str, Any]:
    path = "/api/data-readiness/live"
    response = _request_step(
        args,
        steps,
        name=name,
        method="GET",
        path=path,
        url=_url(backend_url, path, _readiness_params(args)),
        validator=lambda item: _validate_shape(
            item,
            {"status", "checks", "next_steps"},
            "Live data readiness shape is valid.",
            "Live data readiness shape is invalid.",
        ),
    )
    return _data(response)


def _run_live_action_plan(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
    date_from: str,
    date_to: str,
    *,
    name: str = "live_data_action_plan",
) -> dict[str, Any]:
    path = "/api/data-readiness/live/action-plan"
    response = _request_step(
        args,
        steps,
        name=name,
        method="GET",
        path=path,
        url=_url(
            backend_url,
            path,
            {
                **_readiness_params(args),
                "date_from": date_from,
                "date_to": date_to,
                "horizon_days": args.horizon_days,
                "return_method": args.return_method,
            },
        ),
        validator=lambda item: _validate_shape(
            item,
            {"status", "pipeline_payload", "commands"},
            "Live data action plan shape is valid.",
            "Live data action plan shape is invalid.",
        ),
    )
    return _data(response)


def _run_monitoring(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
    *,
    name: str = "monitoring_overview",
) -> dict[str, Any]:
    path = "/api/paper-trading/live/monitoring/overview"
    response = _request_step(
        args,
        steps,
        name=name,
        method="GET",
        path=path,
        url=f"{backend_url}{path}",
        validator=_validate_monitoring,
    )
    return _data(response)


def _run_data_pipeline_flow(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
    action_plan: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    pipeline_payload = action_plan.get("pipeline_payload")
    if not isinstance(pipeline_payload, dict):
        step = _manual_step(
            "data_pipeline_run",
            "failed",
            "Live data action plan did not include pipeline_payload.",
        )
        steps.append(step)
        _print_step(step)
        return

    pipeline_response = _request_step(
        args,
        steps,
        name="data_pipeline_run",
        method="POST",
        path="/api/pipeline/run",
        url=f"{backend_url}/api/pipeline/run",
        payload=pipeline_payload,
        validator=_validate_flexible_json_success,
    )
    pipeline_data = _data(pipeline_response)
    pipeline_run_id = _extract_pipeline_run_id(pipeline_data)
    summary["pipeline_run_id"] = pipeline_run_id
    summary["pipeline_final_status"] = pipeline_data.get("status")

    if args.wait_pipeline and pipeline_run_id is not None and not _should_stop(args, steps):
        summary["pipeline_final_status"] = _wait_for_pipeline(
            args,
            steps,
            backend_url,
            pipeline_run_id,
        )


def _wait_for_pipeline(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
    pipeline_run_id: int,
) -> str | None:
    deadline = time.monotonic() + args.pipeline_timeout_seconds
    final_status: str | None = None
    poll_count = 0
    last_response: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        poll_count += 1
        last_response = http_json(
            "GET",
            f"{backend_url}/api/pipeline/runs/{pipeline_run_id}",
            timeout_seconds=args.timeout_seconds,
        )
        data = _data(last_response)
        status_value = data.get("status")
        if isinstance(status_value, str):
            final_status = status_value
        if status_value in TERMINAL_PIPELINE_STATUSES:
            step = _manual_step(
                "pipeline_wait",
                "passed",
                "Pipeline run reached terminal status.",
                method="GET",
                path=f"/api/pipeline/runs/{pipeline_run_id}",
                status_code=None if last_response is None else last_response.get("status_code"),
                details={
                    "pipeline_run_id": pipeline_run_id,
                    "pipeline_status": status_value,
                    "poll_count": poll_count,
                },
            )
            steps.append(step)
            _print_step(step)
            return str(status_value)
        if args.pipeline_poll_interval_seconds > 0:
            time.sleep(args.pipeline_poll_interval_seconds)

    step = _manual_step(
        "pipeline_wait",
        "failed",
        "Pipeline wait timed out before terminal status.",
        method="GET",
        path=f"/api/pipeline/runs/{pipeline_run_id}",
        status_code=None if last_response is None else last_response.get("status_code"),
        details={
            "pipeline_run_id": pipeline_run_id,
            "last_status": final_status,
            "poll_count": poll_count,
        },
    )
    steps.append(step)
    _print_step(step)
    return final_status


def _run_scheduler_due_dry_run(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
) -> dict[str, Any]:
    response = _request_step(
        args,
        steps,
        name="scheduler_due_dry_run",
        method="POST",
        path=RUN_DUE_PATH,
        url=f"{backend_url}{RUN_DUE_PATH}",
        payload=_run_due_payload(args, dry_run=True),
        validator=lambda item: _validate_run_due(item, expected_dry_run=True),
    )
    return _data(response)


def _run_scheduler_due_execution(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    backend_url: str,
) -> dict[str, Any]:
    response = _request_step(
        args,
        steps,
        name="scheduler_due_execution",
        method="POST",
        path=RUN_DUE_PATH,
        url=f"{backend_url}{RUN_DUE_PATH}",
        payload=_run_due_payload(args, dry_run=False),
        validator=lambda item: _validate_run_due(item, expected_dry_run=False),
    )
    return _data(response)


def _run_due_payload(args: argparse.Namespace, *, dry_run: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"dry_run": dry_run}
    if args.run_due_now:
        payload["now"] = args.run_due_now
    return payload


def _request_step(
    args: argparse.Namespace,
    steps: list[dict[str, Any]],
    *,
    name: str,
    method: str,
    path: str,
    url: str,
    validator: Any,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = http_json(
        method,
        url,
        payload=payload,
        timeout_seconds=args.timeout_seconds,
    )
    duration = time.perf_counter() - started
    status_value, message, details = validator(response)
    step = {
        "name": name,
        "method": method,
        "path": path,
        "status": status_value,
        "status_code": response.get("status_code"),
        "duration_seconds": round(duration, 3),
        "message": message,
        "details": details,
    }
    steps.append(step)
    _print_step(step)
    return response


def _finish(
    started_at: str,
    args: argparse.Namespace,
    date_from: str,
    date_to: str,
    steps: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return _report(
        status_value=_status(args, steps),
        started_at=started_at,
        args=args,
        date_from=date_from,
        date_to=date_to,
        steps=steps,
        summary=summary,
    )


def _status(args: argparse.Namespace, steps: list[dict[str, Any]]) -> str:
    if any(step["details"].get("safety_failed") for step in steps):
        return "safety_failed"
    if any(step["status"] == "failed" for step in steps):
        return "failed"
    if args.execute_data_pipeline or args.execute_due_schedules:
        return "completed"
    if args.mode == "paper-dry-run":
        return "dry_run_completed"
    return "monitoring_completed"


def _report(
    *,
    status_value: str,
    started_at: str,
    args: argparse.Namespace,
    date_from: str,
    date_to: str,
    steps: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status_value,
        "mode": args.mode,
        "started_at": started_at,
        "finished_at": utc_now(),
        "backend_url": args.backend_url,
        "date_from": date_from,
        "date_to": date_to,
        "steps": steps,
        "summary": summary,
    }


def _manual_step(
    name: str,
    status_value: str,
    message: str,
    *,
    method: str = "LOCAL",
    path: str = "",
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "method": method,
        "path": path,
        "status": status_value,
        "status_code": status_code,
        "duration_seconds": 0,
        "message": message,
        "details": details or {},
    }


def _append_skipped(
    steps: list[dict[str, Any]],
    name: str,
    message: str,
) -> None:
    step = _manual_step(name, "skipped", message)
    steps.append(step)
    _print_step(step)


def _confirmation_required(args: argparse.Namespace) -> bool:
    needs_confirmation = (
        args.execute_data_pipeline
        or args.execute_due_schedules
        or args.mode in CONFIRMATION_MODES
    )
    return bool(needs_confirmation and args.confirm_live_operations != "yes")


def _should_stop(args: argparse.Namespace, steps: list[dict[str, Any]]) -> bool:
    if any(step["details"].get("safety_failed") for step in steps):
        return True
    return args.fail_fast and any(step["status"] == "failed" for step in steps)


def _readiness_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "recent_days": args.recent_days,
        "minimum_corporate_bonds": args.minimum_corporate_bonds,
        "minimum_bonds_with_recent_market_snapshot": (
            args.minimum_bonds_with_recent_market_snapshot
        ),
        "minimum_bonds_with_recent_features": args.minimum_bonds_with_recent_features,
        "minimum_bonds_with_predictions": args.minimum_bonds_with_predictions,
        "include_ofz": args.include_ofz,
    }


def _validate_health(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    if _is_success(response) and isinstance(data, dict) and data.get("status") == "ok":
        return "passed", "Health endpoint returned ok.", {}
    return "failed", "Health endpoint did not return expected ok response.", _details(response)


def _validate_monitoring(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {"health_status", "alerts"}
    if _is_success(response) and isinstance(data, dict) and required <= data.keys():
        return "passed", "Monitoring overview shape is valid.", {
            "health_status": data.get("health_status"),
            "alerts_count": _list_count(data.get("alerts")),
        }
    return "failed", "Monitoring overview shape is invalid.", _details(response)


def _validate_shape(
    response: dict[str, Any],
    required: set[str],
    pass_message: str,
    fail_message: str,
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    if _is_success(response) and isinstance(data, dict) and required <= data.keys():
        return "passed", pass_message, {"response_status": data.get("status")}
    return "failed", fail_message, _details(response)


def _validate_flexible_json_success(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    if _is_success(response) and isinstance(data, dict):
        return "passed", "Endpoint returned successful JSON response.", {
            "response_status": data.get("status"),
        }
    return "failed", "Endpoint did not return successful JSON response.", _details(response)


def _validate_run_due(
    response: dict[str, Any],
    *,
    expected_dry_run: bool,
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    if not (_is_success(response) and isinstance(data, dict)):
        return "failed", "Run-due endpoint did not return successful JSON response.", _details(response)

    details = {
        "dry_run": data.get("dry_run"),
        "due_schedule_count": data.get("due_schedule_count"),
        "executed_count": data.get("executed_count"),
        "skipped_count": data.get("skipped_count"),
        "warnings_count": _list_count(data.get("warnings")),
        "errors_count": _list_count(data.get("errors")),
    }
    if data.get("dry_run") is not expected_dry_run:
        details["safety_failed"] = True
        expected = "true" if expected_dry_run else "false"
        return (
            "failed",
            f"Run-due response did not confirm dry_run == {expected}.",
            details,
        )
    return "passed", "Run-due response confirmed expected dry-run mode.", details


def _update_monitoring_summary(
    summary: dict[str, Any],
    monitoring: dict[str, Any],
) -> None:
    if not monitoring:
        return
    summary["monitoring_status"] = monitoring.get("health_status")
    summary["alerts_count"] = _list_count(monitoring.get("alerts"))


def _extract_pipeline_run_id(data: dict[str, Any]) -> int | None:
    if isinstance(data.get("id"), int):
        return data["id"]
    run = data.get("run")
    if isinstance(run, dict) and isinstance(run.get("id"), int):
        return run["id"]
    return None


def _data(response: dict[str, Any] | None) -> dict[str, Any]:
    if response is None:
        return {}
    data = response.get("json")
    return data if isinstance(data, dict) else {}


def _is_success(response: dict[str, Any]) -> bool:
    status_code = response.get("status_code")
    return isinstance(status_code, int) and 200 <= status_code < 300


def _details(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "status_code": response.get("status_code"),
        "error": response.get("error"),
        "json": response.get("json"),
        "text": (response.get("text") or "")[:500],
    }


def _url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return f"{base}{path}"
    query = urllib.parse.urlencode(
        {
            key: _param_value(value)
            for key, value in params.items()
            if value is not None
        }
    )
    return f"{base}{path}?{query}"


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _list_count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _print_step(step: dict[str, Any]) -> None:
    print(f"[operations] {step['name']}: {step['status']}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
