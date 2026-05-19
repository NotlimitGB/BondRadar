from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from datetime import date
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "prod_smoke_check.py"


def load_smoke_module() -> Any:
    spec = importlib.util.spec_from_file_location("prod_smoke_check", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(**overrides: Any) -> Namespace:
    values = {
        "backend_url": "http://backend.test",
        "frontend_url": "http://frontend.test",
        "timeout_seconds": 10,
        "model_run_id": 1,
        "date_from": "2025-01-10",
        "date_to": "2025-03-14",
        "json_output": None,
        "skip_quality_gate": False,
        "fail_fast": False,
    }
    values.update(overrides)
    return Namespace(**values)


def ok_response(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "status_code": 200,
        "text": "{}",
        "json": data,
        "error": None,
    }


def html_response() -> dict[str, Any]:
    return {
        "status_code": 200,
        "text": "<!doctype html><html><body>BondRadar</body></html>",
        "json": None,
        "error": None,
    }


def quality_gate_response(*, scheduler_dry_run: bool = True) -> dict[str, Any]:
    return ok_response(
        {
            "status": "blocked",
            "ready_for_50k_paper_pilot": False,
            "ready_for_vds_deploy": False,
            "gates": [],
            "payloads": {
                "pilot_bootstrap_dry_run_request": {"dry_run_only": True},
                "scheduler_dry_run_request": {"dry_run": scheduler_dry_run},
            },
        }
    )


def fake_http_success(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    if url.endswith("/api/health"):
        return ok_response({"status": "ok"})
    if url == "http://frontend.test/":
        return html_response()
    if url.endswith("/api/data-readiness/corporate-universe/action-plan"):
        return ok_response(
            {
                "status": "needs_sync",
                "can_sync_universe": True,
                "can_continue_to_data_pipeline": False,
            }
        )
    if url.endswith("/api/data-readiness/live"):
        return ok_response({"status": "not_ready", "checks": [], "next_steps": []})
    if url.endswith("/api/data-readiness/live/action-plan"):
        return ok_response({"status": "blocked", "pipeline_payload": {}, "commands": []})
    if url.endswith("/api/pre-deploy/paper-pilot/quality-gate"):
        return quality_gate_response()
    raise AssertionError(f"unexpected URL: {url}")


def test_builds_default_quality_gate_payload_dates() -> None:
    smoke = load_smoke_module()
    payload = smoke.build_quality_gate_payload(
        args(model_run_id=42, date_from=None, date_to=None)
    )

    assert payload["model_run_id"] == 42
    date_from = date.fromisoformat(payload["date_from"])
    date_to = date.fromisoformat(payload["date_to"])
    assert (date_to - date_from).days == 90
    assert payload["include_scheduler_dry_run"] is True
    assert payload["include_detailed_payloads"] is True


def test_skip_quality_gate_skips_check(monkeypatch: Any) -> None:
    smoke = load_smoke_module()
    monkeypatch.setattr(smoke, "http_json", fake_http_success)

    report = smoke.run_checks(args(skip_quality_gate=True))

    quality_gate = report["checks"][-1]
    assert report["status"] == "passed"
    assert quality_gate["name"] == "pre_deploy_quality_gate"
    assert quality_gate["status"] == "skipped"


def test_successful_checks_return_passed_status(monkeypatch: Any) -> None:
    smoke = load_smoke_module()
    monkeypatch.setattr(smoke, "http_json", fake_http_success)

    report = smoke.run_checks(args())

    assert report["status"] == "passed"
    assert all(check["status"] == "passed" for check in report["checks"])


def test_readiness_not_ready_does_not_fail_smoke(monkeypatch: Any) -> None:
    smoke = load_smoke_module()
    monkeypatch.setattr(smoke, "http_json", fake_http_success)

    report = smoke.run_checks(args())
    live_readiness = next(
        check for check in report["checks"] if check["name"] == "live_data_readiness"
    )

    assert live_readiness["status"] == "passed"
    assert report["status"] == "passed"


def test_quality_gate_blocked_does_not_fail_smoke(monkeypatch: Any) -> None:
    smoke = load_smoke_module()
    monkeypatch.setattr(smoke, "http_json", fake_http_success)

    report = smoke.run_checks(args())
    quality_gate = report["checks"][-1]

    assert quality_gate["status"] == "passed"
    assert report["status"] == "passed"


def test_quality_gate_unsafe_payload_fails_smoke(monkeypatch: Any) -> None:
    smoke = load_smoke_module()

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 10,
    ) -> dict[str, Any]:
        if url.endswith("/api/pre-deploy/paper-pilot/quality-gate"):
            return quality_gate_response(scheduler_dry_run=False)
        return fake_http_success(method, url, payload, timeout_seconds)

    monkeypatch.setattr(smoke, "http_json", fake_http)

    report = smoke.run_checks(args())

    assert report["status"] == "failed"
    assert report["checks"][-1]["status"] == "failed"


def test_http_error_fails_relevant_check(monkeypatch: Any) -> None:
    smoke = load_smoke_module()

    def fake_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 10,
    ) -> dict[str, Any]:
        if url.endswith("/api/data-readiness/live/action-plan"):
            return {
                "status_code": 500,
                "text": "error",
                "json": None,
                "error": "HTTP 500",
            }
        return fake_http_success(method, url, payload, timeout_seconds)

    monkeypatch.setattr(smoke, "http_json", fake_http)

    report = smoke.run_checks(args())

    assert report["status"] == "failed"
    failed = [
        check
        for check in report["checks"]
        if check["name"] == "live_data_action_plan"
    ][0]
    assert failed["status"] == "failed"
    assert failed["status_code"] == 500


def test_json_output_writes_file(tmp_path: Path, monkeypatch: Any) -> None:
    smoke = load_smoke_module()
    output_path = tmp_path / "smoke.json"
    monkeypatch.setattr(smoke, "http_json", fake_http_success)

    exit_code = smoke.main(
        [
            "--backend-url",
            "http://backend.test",
            "--frontend-url",
            "http://frontend.test",
            "--model-run-id",
            "1",
            "--json-output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    content = output_path.read_text(encoding="utf-8")
    assert '"status": "passed"' in content
    assert '"pre_deploy_quality_gate"' in content
