from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence


TERMINAL_PIPELINE_STATUSES = {"completed", "completed_with_errors", "failed"}
MUTATION_FLAGS = {
    "execute_universe_sync",
    "execute_data_pipeline",
    "run_ml_validation",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or run a controlled BondRadar live data bootstrap.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=30)

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

    parser.add_argument("--execute-universe-sync", action="store_true")
    parser.add_argument("--execute-data-pipeline", action="store_true")
    parser.add_argument("--wait-pipeline", action="store_true")
    parser.add_argument("--pipeline-poll-interval-seconds", type=float, default=5)
    parser.add_argument("--pipeline-timeout-seconds", type=float, default=1800)

    parser.add_argument("--run-ml-validation", action="store_true")
    parser.add_argument("--model-run-id", type=int, default=None)
    parser.add_argument("--run-quality-gate", action="store_true")

    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--confirm-live-data-bootstrap", default=None)
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
        else date_to - timedelta(days=90)
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


def run_bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    date_from, date_to = resolve_dates(args)
    backend_url = args.backend_url.rstrip("/")
    steps: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "corporate_universe_status": None,
        "live_data_status": None,
        "live_data_action_plan_status": None,
        "pipeline_run_id": None,
        "pipeline_final_status": None,
        "recommended_model_run_id": None,
        "quality_gate_status": None,
        "ready_for_50k_paper_pilot": False,
        "ready_for_vds_deploy": False,
    }

    if _confirmation_required(args):
        steps.append(
            _manual_step(
                "confirmation_required",
                "failed",
                "Mutation flags require --confirm-live-data-bootstrap yes.",
                details={"mutation_flags": _enabled_mutation_flags(args)},
            )
        )
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

    corporate_path = "/api/data-readiness/corporate-universe/action-plan"
    corporate_response = _request_step(
        args,
        steps,
        name="corporate_universe_plan",
        method="GET",
        path=corporate_path,
        url=_url(
            backend_url,
            corporate_path,
            {
                "minimum_corporate_bonds": args.minimum_corporate_bonds,
                "include_ofz": args.include_ofz,
            },
        ),
        validator=lambda response: _validate_shape(
            response,
            {"status", "sync_payload", "can_sync_universe", "can_continue_to_data_pipeline"},
            "Corporate universe action plan shape is valid.",
            "Corporate universe action plan shape is invalid.",
        ),
    )
    corporate_data = _data(corporate_response)
    summary["corporate_universe_status"] = corporate_data.get("status")
    if _should_stop(args, steps):
        return _finish(started_at, args, date_from, date_to, steps, summary)

    live_readiness_path = "/api/data-readiness/live"
    live_readiness_response = _request_step(
        args,
        steps,
        name="live_data_readiness",
        method="GET",
        path=live_readiness_path,
        url=_url(backend_url, live_readiness_path, _readiness_params(args)),
        validator=lambda response: _validate_shape(
            response,
            {"status", "checks", "next_steps"},
            "Live data readiness shape is valid.",
            "Live data readiness shape is invalid.",
        ),
    )
    live_readiness_data = _data(live_readiness_response)
    summary["live_data_status"] = live_readiness_data.get("status")
    if _should_stop(args, steps):
        return _finish(started_at, args, date_from, date_to, steps, summary)

    action_plan_path = "/api/data-readiness/live/action-plan"
    action_plan_response = _request_step(
        args,
        steps,
        name="live_data_action_plan",
        method="GET",
        path=action_plan_path,
        url=_url(
            backend_url,
            action_plan_path,
            {
                **_readiness_params(args),
                "date_from": date_from,
                "date_to": date_to,
                "horizon_days": args.horizon_days,
                "return_method": args.return_method,
            },
        ),
        validator=lambda response: _validate_shape(
            response,
            {"status", "pipeline_payload", "commands"},
            "Live data action plan shape is valid.",
            "Live data action plan shape is invalid.",
        ),
    )
    action_plan_data = _data(action_plan_response)
    summary["live_data_action_plan_status"] = action_plan_data.get("status")
    if _should_stop(args, steps):
        return _finish(started_at, args, date_from, date_to, steps, summary)

    if args.execute_universe_sync:
        sync_payload = corporate_data.get("sync_payload")
        if isinstance(sync_payload, dict):
            _request_step(
                args,
                steps,
                name="universe_sync",
                method="POST",
                path="/api/market-data/moex/bonds/sync",
                url=f"{backend_url}/api/market-data/moex/bonds/sync",
                payload=sync_payload,
                validator=_validate_flexible_json_success,
            )
            if not _should_stop(args, steps):
                corporate_after = _request_step(
                    args,
                    steps,
                    name="corporate_universe_plan_after_sync",
                    method="GET",
                    path=corporate_path,
                    url=_url(
                        backend_url,
                        corporate_path,
                        {
                            "minimum_corporate_bonds": args.minimum_corporate_bonds,
                            "include_ofz": args.include_ofz,
                        },
                    ),
                    validator=lambda response: _validate_shape(
                        response,
                        {"status", "sync_payload", "can_sync_universe", "can_continue_to_data_pipeline"},
                        "Corporate universe action plan after sync shape is valid.",
                        "Corporate universe action plan after sync shape is invalid.",
                    ),
                )
                summary["corporate_universe_status"] = _data(corporate_after).get("status")
        else:
            steps.append(
                _manual_step(
                    "universe_sync",
                    "failed",
                    "Corporate universe action plan did not include sync_payload.",
                )
            )
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)
    else:
        steps.append(_manual_step("universe_sync", "skipped", "Universe sync was not requested."))

    if args.execute_data_pipeline:
        pipeline_payload = action_plan_data.get("pipeline_payload")
        if isinstance(pipeline_payload, dict):
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
                final_status = _wait_for_pipeline(args, steps, backend_url, pipeline_run_id)
                summary["pipeline_final_status"] = final_status
            if not _should_stop(args, steps):
                live_after = _request_step(
                    args,
                    steps,
                    name="live_data_readiness_after_pipeline",
                    method="GET",
                    path=live_readiness_path,
                    url=_url(backend_url, live_readiness_path, _readiness_params(args)),
                    validator=lambda response: _validate_shape(
                        response,
                        {"status", "checks", "next_steps"},
                        "Live data readiness after pipeline shape is valid.",
                        "Live data readiness after pipeline shape is invalid.",
                    ),
                )
                summary["live_data_status"] = _data(live_after).get("status")
            if not _should_stop(args, steps):
                action_after = _request_step(
                    args,
                    steps,
                    name="live_data_action_plan_after_pipeline",
                    method="GET",
                    path=action_plan_path,
                    url=_url(
                        backend_url,
                        action_plan_path,
                        {
                            **_readiness_params(args),
                            "date_from": date_from,
                            "date_to": date_to,
                            "horizon_days": args.horizon_days,
                            "return_method": args.return_method,
                        },
                    ),
                    validator=lambda response: _validate_shape(
                        response,
                        {"status", "pipeline_payload", "commands"},
                        "Live data action plan after pipeline shape is valid.",
                        "Live data action plan after pipeline shape is invalid.",
                    ),
                )
                action_plan_data = _data(action_after)
                summary["live_data_action_plan_status"] = action_plan_data.get("status")
        else:
            steps.append(
                _manual_step(
                    "data_pipeline_run",
                    "failed",
                    "Live data action plan did not include pipeline_payload.",
                )
            )
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)
    else:
        steps.append(_manual_step("data_pipeline_run", "skipped", "Data pipeline run was not requested."))

    if args.run_ml_validation:
        ml_response = _request_step(
            args,
            steps,
            name="ml_validation_suite",
            method="POST",
            path="/api/ml/validation-suite/run",
            url=f"{backend_url}/api/ml/validation-suite/run",
            payload=_ml_validation_payload(args, date_from, date_to),
            validator=lambda response: _validate_shape(
                response,
                {"status", "recommended_model_run_id"},
                "ML validation suite shape is valid.",
                "ML validation suite shape is invalid.",
            ),
        )
        summary["recommended_model_run_id"] = _data(ml_response).get("recommended_model_run_id")
        if _should_stop(args, steps):
            return _finish(started_at, args, date_from, date_to, steps, summary)
    else:
        steps.append(_manual_step("ml_validation_suite", "skipped", "ML validation suite was not requested."))

    if args.run_quality_gate:
        model_id = args.model_run_id or summary.get("recommended_model_run_id")
        if model_id is None:
            steps.append(
                _manual_step(
                    "pre_deploy_quality_gate",
                    "skipped",
                    "Quality gate skipped because no model id was available.",
                )
            )
        else:
            quality_response = _request_step(
                args,
                steps,
                name="pre_deploy_quality_gate",
                method="POST",
                path="/api/pre-deploy/paper-pilot/quality-gate",
                url=f"{backend_url}/api/pre-deploy/paper-pilot/quality-gate",
                payload=_quality_gate_payload(args, int(model_id), date_from, date_to),
                validator=_validate_quality_gate,
            )
            quality_data = _data(quality_response)
            summary["quality_gate_status"] = quality_data.get("status")
            summary["ready_for_50k_paper_pilot"] = bool(
                quality_data.get("ready_for_50k_paper_pilot")
            )
            summary["ready_for_vds_deploy"] = bool(quality_data.get("ready_for_vds_deploy"))
    else:
        steps.append(_manual_step("pre_deploy_quality_gate", "skipped", "Quality gate was not requested."))

    return _finish(started_at, args, date_from, date_to, steps, summary)


def write_json_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_bootstrap(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[bootstrap] wrote JSON report: {args.json_output}", flush=True)
    if report["summary"].get("exit_reason") == "confirmation_required":
        return 2
    if report["status"] == "safety_failed":
        return 3
    if report["status"] == "failed":
        return 1
    return 0


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
            steps.append(
                _manual_step(
                    "pipeline_wait",
                    "passed",
                    "Pipeline run reached terminal status.",
                    method="GET",
                    path=f"/api/pipeline/runs/{pipeline_run_id}",
                    status_code=last_response.get("status_code"),
                    details={
                        "pipeline_run_id": pipeline_run_id,
                        "pipeline_status": status_value,
                        "poll_count": poll_count,
                    },
                )
            )
            _print_step(steps[-1])
            return str(status_value)
        if args.pipeline_poll_interval_seconds > 0:
            time.sleep(args.pipeline_poll_interval_seconds)

    steps.append(
        _manual_step(
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
    )
    _print_step(steps[-1])
    return final_status


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
    if _any_optional_action_requested(args):
        return "completed"
    return "planned"


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


def _confirmation_required(args: argparse.Namespace) -> bool:
    return bool(_enabled_mutation_flags(args)) and args.confirm_live_data_bootstrap != "yes"


def _enabled_mutation_flags(args: argparse.Namespace) -> list[str]:
    return [flag for flag in sorted(MUTATION_FLAGS) if getattr(args, flag)]


def _any_optional_action_requested(args: argparse.Namespace) -> bool:
    return any(
        [
            args.execute_universe_sync,
            args.execute_data_pipeline,
            args.run_ml_validation,
            args.run_quality_gate,
        ]
    )


def _should_stop(args: argparse.Namespace, steps: list[dict[str, Any]]) -> bool:
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


def _ml_validation_payload(
    args: argparse.Namespace,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    return {
        "suite_name": "live_data_bootstrap_ml_validation",
        "require_live_data_ready": True,
        "allow_readiness_warning": False,
        "recent_days": args.recent_days,
        "minimum_corporate_bonds": args.minimum_corporate_bonds,
        "minimum_bonds_with_recent_market_snapshot": (
            args.minimum_bonds_with_recent_market_snapshot
        ),
        "minimum_bonds_with_recent_features": args.minimum_bonds_with_recent_features,
        "minimum_bonds_with_predictions": 0,
        "include_ofz": args.include_ofz,
        "training_configs": [],
        "generate_predictions": True,
        "save_predictions": True,
        "run_candidate_comparison": True,
        "comparison_date_from": date_from,
        "comparison_date_to": date_to,
        "comparison_return_method": args.return_method,
        "comparison_horizon_days": args.horizon_days,
    }


def _quality_gate_payload(
    args: argparse.Namespace,
    model_run_id: int,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    return {
        "model_run_id": model_run_id,
        "return_method": args.return_method,
        "horizon_days": args.horizon_days,
        "date_from": date_from,
        "date_to": date_to,
        "include_scheduler_dry_run": True,
        "include_detailed_payloads": True,
    }


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


def _validate_quality_gate(
    response: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    data = response.get("json")
    required = {
        "status",
        "ready_for_50k_paper_pilot",
        "ready_for_vds_deploy",
        "payloads",
    }
    if not (_is_success(response) and isinstance(data, dict) and required <= data.keys()):
        return "failed", "Quality gate response shape is invalid.", _details(response)
    payloads = data.get("payloads") or {}
    pilot_payload = payloads.get("pilot_bootstrap_dry_run_request") or {}
    scheduler_payload = payloads.get("scheduler_dry_run_request") or {}
    if pilot_payload.get("dry_run_only") is not True:
        return (
            "failed",
            "Quality gate returned unsafe pilot bootstrap payload.",
            {
                "safety_failed": True,
                "pilot_bootstrap_dry_run_request": pilot_payload,
            },
        )
    if scheduler_payload.get("dry_run") is not True:
        return (
            "failed",
            "Quality gate returned unsafe scheduler payload.",
            {
                "safety_failed": True,
                "scheduler_dry_run_request": scheduler_payload,
            },
        )
    return "passed", "Quality gate response shape and dry-run payloads are safe.", {
        "response_status": data.get("status"),
    }


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


def _print_step(step: dict[str, Any]) -> None:
    print(f"[bootstrap] {step['name']}: {step['status']}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
