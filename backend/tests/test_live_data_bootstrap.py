from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "live_data_bootstrap.py"


def load_bootstrap_module() -> Any:
    spec = importlib.util.spec_from_file_location("live_data_bootstrap", SCRIPT_PATH)
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


def corporate_payload() -> dict[str, Any]:
    return {
        "status": "needs_sync",
        "sync_payload": {
            "board": "TQCB",
            "active_only": True,
            "create_missing_companies": True,
            "rebuild_existing": False,
            "max_pages": 100,
            "page_size": 100,
        },
        "can_sync_universe": True,
        "can_continue_to_data_pipeline": False,
    }


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


def live_readiness_payload() -> dict[str, Any]:
    return {"status": "not_ready", "checks": [], "next_steps": []}


def ml_validation_payload(model_run_id: int = 77) -> dict[str, Any]:
    return {"status": "completed", "recommended_model_run_id": model_run_id}


def quality_gate_payload(*, dry_run: bool = True) -> dict[str, Any]:
    return {
        "status": "blocked",
        "ready_for_50k_paper_pilot": False,
        "ready_for_vds_deploy": False,
        "payloads": {
            "pilot_bootstrap_dry_run_request": {"dry_run_only": True},
            "scheduler_dry_run_request": {"dry_run": dry_run},
        },
    }


def fake_http_factory(calls: list[dict[str, Any]], *, unsafe_quality_gate: bool = False) -> Any:
    pipeline_poll_count = {"value": 0}

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "payload": payload})
        if url.endswith("/api/data-readiness/corporate-universe/action-plan") or (
            "/api/data-readiness/corporate-universe/action-plan?" in url
        ):
            return response(corporate_payload())
        if url.endswith("/api/data-readiness/live") or "/api/data-readiness/live?" in url:
            return response(live_readiness_payload())
        if url.endswith("/api/data-readiness/live/action-plan") or (
            "/api/data-readiness/live/action-plan?" in url
        ):
            return response(action_plan_payload())
        if url.endswith("/api/market-data/moex/bonds/sync"):
            return response({"processed_securities": 10, "warnings": [], "errors": []})
        if url.endswith("/api/pipeline/run"):
            return response({"run": {"id": 7}, "status": "running"})
        if url.endswith("/api/pipeline/runs/7"):
            pipeline_poll_count["value"] += 1
            status = "running" if pipeline_poll_count["value"] == 1 else "completed"
            return response({"id": 7, "status": status})
        if url.endswith("/api/ml/validation-suite/run"):
            return response(ml_validation_payload(88))
        if url.endswith("/api/pre-deploy/paper-pilot/quality-gate"):
            return response(quality_gate_payload(dry_run=not unsafe_quality_gate))
        raise AssertionError(f"unexpected URL: {url}")

    return fake_http


def test_default_run_is_plan_only_and_no_mutation_calls(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(bootstrap.parse_args(["--backend-url", "http://api.test"]))

    assert report["status"] == "planned"
    assert [call["method"] for call in calls] == ["GET", "GET", "GET"]
    assert [step["name"] for step in report["steps"][:3]] == [
        "corporate_universe_plan",
        "live_data_readiness",
        "live_data_action_plan",
    ]


def test_mutation_flags_require_confirmation() -> None:
    bootstrap = load_bootstrap_module()

    exit_code = bootstrap.main(["--execute-universe-sync"])

    assert exit_code == 2


def test_universe_sync_uses_action_plan_sync_payload(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(
            ["--execute-universe-sync", "--confirm-live-data-bootstrap", "yes"]
        )
    )

    sync_call = next(call for call in calls if call["url"].endswith("/api/market-data/moex/bonds/sync"))
    assert report["status"] == "completed"
    assert sync_call["method"] == "POST"
    assert sync_call["payload"] == corporate_payload()["sync_payload"]


def test_data_pipeline_uses_action_plan_pipeline_payload(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(
            ["--execute-data-pipeline", "--confirm-live-data-bootstrap", "yes"]
        )
    )

    pipeline_call = next(call for call in calls if call["url"].endswith("/api/pipeline/run"))
    assert report["status"] == "completed"
    assert pipeline_call["method"] == "POST"
    assert pipeline_call["payload"] == action_plan_payload()["pipeline_payload"]


def test_wait_pipeline_polls_until_terminal_status(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(
            [
                "--execute-data-pipeline",
                "--wait-pipeline",
                "--pipeline-poll-interval-seconds",
                "0",
                "--confirm-live-data-bootstrap",
                "yes",
            ]
        )
    )

    poll_calls = [call for call in calls if call["url"].endswith("/api/pipeline/runs/7")]
    assert len(poll_calls) == 2
    assert report["summary"]["pipeline_final_status"] == "completed"


def test_ml_validation_captures_recommended_model_id(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(
            ["--run-ml-validation", "--confirm-live-data-bootstrap", "yes"]
        )
    )

    assert report["summary"]["recommended_model_run_id"] == 88


def test_quality_gate_uses_explicit_model_id(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(["--run-quality-gate", "--model-run-id", "123"])
    )

    gate_call = next(
        call
        for call in calls
        if call["url"].endswith("/api/pre-deploy/paper-pilot/quality-gate")
    )
    assert report["status"] == "completed"
    assert gate_call["payload"]["model_run_id"] == 123


def test_quality_gate_uses_ml_recommended_model_id(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    report = bootstrap.run_bootstrap(
        bootstrap.parse_args(
            [
                "--run-ml-validation",
                "--run-quality-gate",
                "--confirm-live-data-bootstrap",
                "yes",
            ]
        )
    )

    gate_call = next(
        call
        for call in calls
        if call["url"].endswith("/api/pre-deploy/paper-pilot/quality-gate")
    )
    assert report["summary"]["recommended_model_run_id"] == 88
    assert gate_call["payload"]["model_run_id"] == 88


def test_quality_gate_unsafe_payload_returns_exit_code_3(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        bootstrap,
        "http_json",
        fake_http_factory(calls, unsafe_quality_gate=True),
    )

    exit_code = bootstrap.main(["--run-quality-gate", "--model-run-id", "123"])

    assert exit_code == 3


def test_json_output_is_written(tmp_path: Path, monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    output_path = tmp_path / "bootstrap.json"
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bootstrap, "http_json", fake_http_factory(calls))

    exit_code = bootstrap.main(["--json-output", str(output_path)])

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "planned"' in content
    assert '"corporate_universe_plan"' in content


def test_fail_fast_stops_after_first_failed_http_check(monkeypatch: Any) -> None:
    bootstrap = load_bootstrap_module()
    calls: list[dict[str, Any]] = []

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "payload": payload})
        return response({"error": "failed"}, status_code=500)

    monkeypatch.setattr(bootstrap, "http_json", fake_http)

    report = bootstrap.run_bootstrap(bootstrap.parse_args(["--fail-fast"]))

    assert report["status"] == "failed"
    assert len(calls) == 1
    assert len(report["steps"]) == 1
    assert report["steps"][0]["name"] == "corporate_universe_plan"


def test_source_does_not_include_forbidden_paper_execution_endpoints() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "/api/paper-trading/live/schedules/run-due",
        "/api/paper-trading/live/cycles/run",
        "/api/paper-trading/portfolios/",
        "/rebalance",
        "/mark-period",
    ]

    for item in forbidden:
        assert item not in source
