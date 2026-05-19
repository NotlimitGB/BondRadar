from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "live_operations_runner.py"


def load_operations_module() -> Any:
    spec = importlib.util.spec_from_file_location("live_operations_runner", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def response(data: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
    return {
        "status_code": status_code,
        "text": "{}",
        "json": data,
        "error": None if status_code < 400 else f"HTTP {status_code}",
    }


def live_readiness_payload() -> dict[str, Any]:
    return {"status": "not_ready", "checks": [], "next_steps": []}


def action_plan_payload() -> dict[str, Any]:
    return {
        "status": "needs_attention",
        "pipeline_payload": {
            "mode": "manual",
            "date_from": "2025-01-10",
            "date_to": "2025-03-14",
            "steps": ["moex_market_sync"],
        },
        "commands": [],
    }


def monitoring_payload(external_risk_mode: str = "normal") -> dict[str, Any]:
    return {
        "health_status": "healthy",
        "now": "2025-03-14T08:00:00+00:00",
        "due_schedule_count": 1,
        "external_risk_regime": external_risk_payload(external_risk_mode),
        "alerts": [],
    }


def external_risk_payload(mode: str = "normal") -> dict[str, Any]:
    return {
        "mode": mode,
        "reason": "Manual operator caution before paper execution window."
        if mode != "normal"
        else "Default external risk regime.",
        "source": "manual" if mode != "normal" else "default",
        "is_active": True,
        "expires_at": None,
    }


def run_due_payload(*, dry_run: bool, include_marker: bool = True) -> dict[str, Any]:
    data = {
        "now": "2025-03-14T08:00:00+00:00",
        "due_schedule_count": 1,
        "executed_count": 0 if dry_run else 1,
        "skipped_count": 0,
        "results": [],
        "warnings": [],
        "errors": [],
    }
    if include_marker:
        data["dry_run"] = dry_run
    return data


def fake_http_factory(
    calls: list[dict[str, Any]],
    *,
    unsafe_dry_run: bool = False,
    unsafe_execution: bool = False,
    external_risk_mode: str = "normal",
) -> Any:
    pipeline_poll_count = {"value": 0}

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "payload": payload})
        if url.endswith("/api/health"):
            return response({"status": "ok"})
        if url.endswith("/api/risk/external-regime"):
            return response(external_risk_payload(external_risk_mode))
        if "/api/data-readiness/live/action-plan" in url:
            return response(action_plan_payload())
        if "/api/data-readiness/live" in url:
            return response(live_readiness_payload())
        if url.endswith("/api/paper-trading/live/monitoring/overview"):
            return response(monitoring_payload(external_risk_mode))
        if url.endswith("/api/pipeline/run"):
            return response({"run": {"id": 7}, "status": "running"})
        if url.endswith("/api/pipeline/runs/7"):
            pipeline_poll_count["value"] += 1
            status = "running" if pipeline_poll_count["value"] == 1 else "completed"
            return response({"id": 7, "status": status})
        if url.endswith("/api/paper-trading/live/schedules/run-due"):
            assert payload is not None
            if payload.get("dry_run") is True:
                return response(run_due_payload(dry_run=not unsafe_dry_run))
            if unsafe_execution:
                return response(run_due_payload(dry_run=True))
            return response(run_due_payload(dry_run=False))
        raise AssertionError(f"unexpected URL: {url}")

    return fake_http


def test_default_run_is_monitoring_only_and_read_only(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(
        operations.parse_args(["--backend-url", "http://api.test"])
    )

    assert report["status"] == "monitoring_completed"
    assert [call["method"] for call in calls] == ["GET", "GET", "GET", "GET", "GET"]
    assert [call["url"].split("http://api.test", 1)[1].split("?", 1)[0] for call in calls] == [
        "/api/health",
        "/api/risk/external-regime",
        "/api/data-readiness/live",
        "/api/data-readiness/live/action-plan",
        "/api/paper-trading/live/monitoring/overview",
    ]
    assert report["summary"]["external_risk_mode"] == "normal"


def test_paper_dry_run_mode_uses_dry_run_schedule_due_only(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(operations.parse_args(["--mode", "paper-dry-run"]))

    run_due_calls = [
        call
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "dry_run_completed"
    assert len(run_due_calls) == 1
    assert run_due_calls[0]["payload"] == {"dry_run": True}


def test_paper_dry_run_with_severe_external_risk_stays_dry_run(
    monkeypatch: Any,
) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, external_risk_mode="severe"),
    )

    report = operations.run_operations(operations.parse_args(["--mode", "paper-dry-run"]))

    run_due_calls = [
        call
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "dry_run_completed"
    assert report["summary"]["external_risk_mode"] == "severe"
    assert run_due_calls == [{"method": "POST", "url": "http://127.0.0.1:8000/api/paper-trading/live/schedules/run-due", "payload": {"dry_run": True}}]


def test_execution_flags_require_confirmation() -> None:
    operations = load_operations_module()

    exit_code = operations.main(["--mode", "paper-execute", "--execute-due-schedules"])

    assert exit_code == 2


def test_data_refresh_pipeline_uses_action_plan_payload(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "data-refresh",
                "--execute-data-pipeline",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    pipeline_call = next(call for call in calls if call["url"].endswith("/api/pipeline/run"))
    assert report["status"] == "completed"
    assert pipeline_call["method"] == "POST"
    assert pipeline_call["payload"] == action_plan_payload()["pipeline_payload"]


def test_wait_pipeline_polls_until_terminal_status(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "data-refresh",
                "--execute-data-pipeline",
                "--wait-pipeline",
                "--pipeline-poll-interval-seconds",
                "0",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    poll_calls = [call for call in calls if call["url"].endswith("/api/pipeline/runs/7")]
    assert len(poll_calls) == 2
    assert report["summary"]["pipeline_final_status"] == "completed"


def test_dry_run_due_unsafe_response_fails_safety(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, unsafe_dry_run=True),
    )

    report = operations.run_operations(operations.parse_args(["--mode", "paper-dry-run"]))
    exit_code = operations.main(["--mode", "paper-dry-run"])

    assert report["status"] == "safety_failed"
    assert exit_code == 3


def test_paper_execution_sends_false_only_after_dry_run_and_confirmation(
    monkeypatch: Any,
) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "paper-execute",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    run_due_payloads = [
        call["payload"]
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "completed"
    assert run_due_payloads == [{"dry_run": True}, {"dry_run": False}]


def test_paper_execution_with_elevated_external_risk_requires_ack(
    monkeypatch: Any,
) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, external_risk_mode="elevated"),
    )

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "paper-execute",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    run_due_payloads = [
        call["payload"]
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "safety_failed"
    assert run_due_payloads == [{"dry_run": True}]


def test_paper_execution_with_severe_external_risk_blocks_before_execution(
    monkeypatch: Any,
) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, external_risk_mode="severe"),
    )

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "paper-execute",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    run_due_payloads = [
        call["payload"]
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "safety_failed"
    assert run_due_payloads == [{"dry_run": True}]


def test_paper_execution_with_severe_override_records_override(
    monkeypatch: Any,
) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, external_risk_mode="severe"),
    )

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "paper-execute",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
                "--override-external-risk-severe",
            ]
        )
    )

    run_due_payloads = [
        call["payload"]
        for call in calls
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]
    assert report["status"] == "completed"
    assert report["summary"]["external_risk_override_used"] is True
    assert run_due_payloads == [{"dry_run": True}, {"dry_run": False}]


def test_confirmation_is_still_required_with_external_risk_ack() -> None:
    operations = load_operations_module()

    exit_code = operations.main(
        [
            "--mode",
            "paper-execute",
            "--execute-due-schedules",
            "--ack-external-risk-elevated",
        ]
    )

    assert exit_code == 2


def test_execution_response_unsafe_marker_fails(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        operations,
        "http_json",
        fake_http_factory(calls, unsafe_execution=True),
    )

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "paper-execute",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    assert report["status"] == "safety_failed"


def test_full_cycle_runs_pipeline_then_dry_run_then_execution(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    report = operations.run_operations(
        operations.parse_args(
            [
                "--mode",
                "full-cycle",
                "--execute-data-pipeline",
                "--wait-pipeline",
                "--pipeline-poll-interval-seconds",
                "0",
                "--execute-due-schedules",
                "--confirm-live-operations",
                "yes",
            ]
        )
    )

    pipeline_index = next(
        index for index, call in enumerate(calls) if call["url"].endswith("/api/pipeline/run")
    )
    poll_index = next(
        index for index, call in enumerate(calls) if call["url"].endswith("/api/pipeline/runs/7")
    )
    monitoring_index = next(
        index
        for index, call in enumerate(calls)
        if call["url"].endswith("/api/paper-trading/live/monitoring/overview")
    )
    run_due_indices = [
        index
        for index, call in enumerate(calls)
        if call["url"].endswith("/api/paper-trading/live/schedules/run-due")
    ]

    assert report["status"] == "completed"
    assert pipeline_index < poll_index < monitoring_index < run_due_indices[0] < run_due_indices[1]
    assert calls[run_due_indices[0]]["payload"] == {"dry_run": True}
    assert calls[run_due_indices[1]]["payload"] == {"dry_run": False}


def test_json_output_is_written(tmp_path: Path, monkeypatch: Any) -> None:
    operations = load_operations_module()
    output_path = tmp_path / "operations.json"
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(operations, "http_json", fake_http_factory(calls))

    exit_code = operations.main(["--json-output", str(output_path)])

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "monitoring_completed"' in content
    assert '"backend_health"' in content


def test_fail_fast_stops_after_first_failed_check(monkeypatch: Any) -> None:
    operations = load_operations_module()
    calls: list[dict[str, Any]] = []

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "payload": payload})
        return response({"status": "error"}, status_code=500)

    monkeypatch.setattr(operations, "http_json", fake_http)

    report = operations.run_operations(operations.parse_args(["--fail-fast"]))

    assert report["status"] == "failed"
    assert len(calls) == 1
    assert report["steps"][0]["name"] == "backend_health"


def test_source_does_not_include_forbidden_endpoint_strings() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "/api/paper-trading/live/schedules/{id}/run",
        "/api/paper-trading/live/cycles/run",
        "/rebalance",
        "/mark-period",
        "broker",
    ]

    for item in forbidden:
        assert item not in source
