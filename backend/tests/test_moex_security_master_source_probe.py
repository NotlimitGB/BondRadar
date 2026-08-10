from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.moex_iss_client import MoexCashflowScheduleResult


def load_probe_module() -> Any:
    path = Path(__file__).parents[2] / "scripts" / "moex_security_master_source_probe.py"
    spec = importlib.util.spec_from_file_location(
        "moex_security_master_source_probe",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProbeClient:
    def __init__(
        self,
        *,
        descriptions: dict[str, dict[str, Any]] | None = None,
        schedules: dict[str, MoexCashflowScheduleResult] | None = None,
        description_errors: dict[str, Exception] | None = None,
        cashflow_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.descriptions = descriptions or {}
        self.schedules = schedules or {}
        self.description_errors = description_errors or {}
        self.cashflow_errors = cashflow_errors or {}
        self.description_calls: list[str] = []
        self.cashflow_calls: list[str] = []

    def fetch_bond_description(self, secid: str):
        self.description_calls.append(secid)
        if secid in self.description_errors:
            raise self.description_errors[secid]
        return dict(self.descriptions.get(secid, {"secid": secid, "raw": {}})), []

    def fetch_bond_cashflows(self, secid: str):
        self.cashflow_calls.append(secid)
        if secid in self.cashflow_errors:
            raise self.cashflow_errors[secid]
        return self.schedules.get(
            secid,
            MoexCashflowScheduleResult(
                warnings=[
                    f"MOEX cashflow table coupons is missing for {secid}",
                    f"MOEX cashflow table amortizations is missing for {secid}",
                    f"MOEX cashflow table offers is missing for {secid}",
                    f"MOEX cashflow table redemptions is missing for {secid}",
                ]
            ),
        )


def description_payload(
    *,
    currency: Any = "SUR",
    amortization: Any = None,
    floating: Any = None,
) -> dict[str, Any]:
    raw = {
        "EMITENT_TITLE": "Source Issuer",
        "EMITENT_INN": "7700000999",
        "FACEUNIT": currency,
        "MATDATE": "2030-01-01",
        "OFFERDATE": "2028-06-01",
        "IS_SUBORDINATED": 0,
        "IS_PERPETUAL": 0,
        "UNRELATED_SECRET": "raw-secret-must-not-leak",
        "UNRELATED_OBJECT": {"large": "payload"},
    }
    if amortization is not None:
        raw["HAS_AMORTIZATION"] = amortization
    if floating is not None:
        raw["FLOATING_COUPON"] = floating
    return {
        "secid": "RU000A200001",
        "issuer_name": "Source Issuer",
        "issuer_inn": "7700000999",
        "currency": currency,
        "maturity_date": "2030-01-01",
        "offer_date": "2028-06-01",
        "has_amortization": amortization,
        "is_subordinated": 0,
        "is_perpetual": 0,
        "raw": raw,
    }


def result_by_secid(report: dict[str, Any], secid: str) -> dict[str, Any]:
    return next(row for row in report["results"] if row["secid"] == secid)


def test_probe_reports_narrow_description_and_cashflow_source_evidence() -> None:
    probe = load_probe_module()
    secid = "RU000A200001"
    client = FakeProbeClient(
        descriptions={
            secid: description_payload(amortization=True, floating="VARIABLE")
        },
        schedules={
            secid: MoexCashflowScheduleResult(
                coupons=[
                    {"coupondate": "2027-01-01", "__moex_source_table": "coupons"}
                ],
                amortizations=[
                    {
                        "amortdate": "2028-01-01",
                        "__moex_source_table": "amortization_schedule",
                    }
                ],
                offers=[
                    {"offerdate": "2028-06-01", "__moex_source_table": "offers"}
                ],
                redemptions=[
                    {"date": "2030-01-01", "__moex_source_table": "maturity"}
                ],
            )
        },
    )

    report = probe.build_probe_report(
        client,
        [secid],
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    row = report["results"][0]

    assert report["schema"] == "bondradar.moex_security_master_source_probe.v1"
    assert report["generated_at"] == "2026-08-10T00:00:00+00:00"
    assert report["currency_sur_count"] == 1
    assert report["issuer_name_present_count"] == 1
    assert report["issuer_inn_present_count"] == 1
    assert row["issuer_name_value"] == "Source Issuer"
    assert row["issuer_inn_value"] == "7700000999"
    assert row["raw_currency_value"] == "SUR"
    assert row["canonical_currency"] == "RUB"
    assert row["maturity_date_value"] == "2030-01-01"
    assert row["offer_date_value"] == "2028-06-01"
    assert row["explicit_amortization_field_present"] is True
    assert row["explicit_floating_coupon_field_present"] is True
    assert row["current_floating_classification_trusted"] is False
    assert row["amortization_evidence_state"] == "AMORTIZATION_POSITIVE_EVIDENCE"
    assert row["coupon_source_tables"] == ["coupons"]
    assert row["amortization_source_tables"] == ["amortization_schedule"]
    assert row["offer_source_tables"] == ["offers"]
    assert row["redemption_source_tables"] == ["maturity"]
    assert row["redemption_source_state"] == "REDEMPTION_SOURCE_ROWS_OBSERVED"
    assert report["redemption_synthesized"] is False
    encoded = json.dumps(report, ensure_ascii=False)
    assert "raw-secret-must-not-leak" not in encoded
    assert "UNRELATED_SECRET" not in encoded
    assert "UNRELATED_OBJECT" not in encoded
    assert "app.db" not in (
        Path(probe.__file__).read_text(encoding="utf-8")
    )


def test_probe_preserves_not_proven_states_and_isolates_secret_failures() -> None:
    probe = load_probe_module()
    first = "RU000A200011"
    failed = "RU000A200012"
    third = "RU000A200013"
    missing_redemption_warnings = [
        f"MOEX cashflow table redemptions is missing for {first}"
    ]
    client = FakeProbeClient(
        descriptions={
            first: description_payload(amortization=None, floating=None),
            third: description_payload(
                currency="USD",
                amortization=False,
                floating=None,
            ),
        },
        schedules={
            first: MoexCashflowScheduleResult(
                coupons=[{"__moex_source_table": "coupons"}],
                warnings=missing_redemption_warnings,
            ),
            third: MoexCashflowScheduleResult(),
        },
        description_errors={
            failed: RuntimeError("DATABASE_URL=https://secret@production")
        },
        cashflow_errors={
            failed: RuntimeError("token=super-secret https://private.example")
        },
    )

    report = probe.build_probe_report(
        client,
        [third, failed, first],
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert [row["secid"] for row in report["results"]] == [first, failed, third]
    assert client.description_calls == [third, failed, first]
    assert client.cashflow_calls == [third, failed, first]
    assert report["secids_requested"] == 3
    assert report["description_success_count"] == 2
    assert report["description_failure_count"] == 1
    assert report["cashflow_success_count"] == 2
    assert report["cashflow_failure_count"] == 1
    first_row = result_by_secid(report, first)
    assert first_row["amortization_rows"] == 0
    assert first_row["amortization_evidence_state"] == "AMORTIZATION_NOT_PROVEN"
    assert first_row["explicit_floating_coupon_field_present"] is False
    assert first_row["redemption_rows"] == 0
    assert first_row["redemption_table_present"] is False
    assert first_row["redemption_source_state"] == "REDEMPTION_SOURCE_NOT_OBSERVED"
    third_row = result_by_secid(report, third)
    assert third_row["amortization_evidence_state"] == (
        "AMORTIZATION_EXPLICIT_NEGATIVE_EVIDENCE"
    )
    failed_row = result_by_secid(report, failed)
    assert failed_row["description_fetch_status"] == "failed"
    assert failed_row["cashflow_fetch_status"] == "failed"
    assert failed_row["warnings"] == [
        "DESCRIPTION_FETCH_FAILED",
        "CASHFLOW_FETCH_FAILED",
    ]
    encoded = json.dumps(report, ensure_ascii=False)
    assert "super-secret" not in encoded
    assert "production" not in encoded
    assert "private.example" not in encoded


def test_probe_input_cli_rendering_and_sanitized_invocation_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    probe = load_probe_module()
    line_file = tmp_path / "secids.txt"
    line_file.write_text(" ru000a200021 \nRU000A200022\n", encoding="utf-8")
    json_file = tmp_path / "secids.json"
    json_file.write_text(
        json.dumps(["RU000A200022", "ru000a200023"]),
        encoding="utf-8",
    )
    assert probe.load_secids(["RU000A200020", "ru000a200020"], line_file) == [
        "RU000A200020",
        "RU000A200021",
        "RU000A200022",
    ]
    assert probe.load_secids([], json_file) == ["RU000A200022", "RU000A200023"]
    with pytest.raises(ValueError):
        probe.load_secids(["bad secid"])
    with pytest.raises(ValueError):
        probe.load_secids([f"S{index:03d}" for index in range(101)])

    client = FakeProbeClient(
        descriptions={"RU000A200020": description_payload()},
    )
    monkeypatch.setattr(probe, "MoexIssClient", lambda: client)
    output = tmp_path / "probe.json"
    assert probe.main(
        [
            "--secid",
            "RU000A200020",
            "--format",
            "json",
            "--output",
            str(output),
        ]
    ) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["secids_processed"] == 1
    assert probe.serialize_report(payload, "markdown").startswith(
        "# BondRadar MOEX Security-Master Source Probe"
    )

    def secret_failure(*_args, **_kwargs):
        raise RuntimeError("DATABASE_URL=postgresql://secret@production")

    monkeypatch.setattr(probe, "load_secids", secret_failure)
    assert probe.main(["--secid", "RU000A200020"]) == 1
    captured = capsys.readouterr()
    assert "secret" not in captured.err
    assert "production" not in captured.err
    assert "moex_security_master_source_probe_failed" in captured.err
