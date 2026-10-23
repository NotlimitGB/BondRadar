"""Source-only semantics, full frozen replay, independent publication and runner."""
from datetime import datetime, timedelta, timezone
from decimal import getcontext
from pathlib import Path
import ast
import json
import subprocess
import sys
import httpx
import pytest
from app.schemas.historical_cashflow_qualification import SourceContractReport
from app.schemas.historical_cashflow_semantics import ExpectedHashes, SourceRowIntegrityReport, SemanticsPackage
from app.services.historical_cashflow_semantics_service import (HistoricalCashflowSemanticsService as Service,
    build_semantics, classify_source_event, field_evidence, field_conflict_keys)
from app.services.historical_cashflow_semantics_artifacts import publish, verify_published
from app.services.historical_cashflow_row_integrity import verify_retained_rows
from app.services.historical_cashflow_qualification_service import compare_profiles, HistoricalCashflowQualificationService
from app.services.historical_cashflow_qualification_artifacts import publish as publish_r2
from app.services.historical_evidence_normalization import signed
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_row_integrity import observation, SID
from test_historical_cashflow_qualification import inputs as r2_inputs, publication_args
from test_historical_cashflow_source_forensics import original, pristine_original


def synthetic_package():
    observations = []; findings = []; rows = {}
    for secid, count, missing in (("RU000A0JQAM6", 38, 3), (SID, 12, 0)):
        for table in ("coupons", "amortizations", "offers", "redemptions"):
            profiles = ("B2_ACCEPTED",) if table == "amortizations" else ("BASELINE", "TABLE_ONLY")
            for profile in profiles:
                if table == "coupons":
                    columns = ["coupondate", "value", "faceunit", "facevalue", "valueprc"]
                    data = [[(datetime(2020, 1, 1) + timedelta(days=i * 30)).date().isoformat(), None if i >= count - missing else 13.19, "RUB", 1000, "13.2500000000"] for i in range(count)]
                elif table == "amortizations":
                    columns = ["amortdate", "data_source", "faceunit", "facevalue", "initialfacevalue", "value", "valueprc", "value_rub"]
                    data = [["2028-09-15" if secid != SID else "2022-07-27", "maturity", "RUB", 1000, 1000, 1000, 100, 1000]]
                else:
                    columns = ["offerdate", "price", "value"]
                    data = [["2009-10-13", "101.706", None]] if secid != SID else []
                payload = {"offers" if table == "redemptions" else table: {"columns": columns, "data": data}}
                if table in ("coupons", "amortizations") and profile != "TABLE_ONLY":
                    payload[table + ".cursor"] = {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": [[0, len(data), 100]]}
                body = json.dumps(payload).encode()
                obs = observation(body, secid=secid, table=table, profile=profile,
                                  stamp=datetime(2026, 1, 1 if profile != "TABLE_ONLY" else 2, tzinfo=timezone.utc))
                finding, keyed = verify_retained_rows(body=body, observation=obs)
                observations.append(obs); findings.append(finding); rows[obs.request_id] = keyed
    # Synthetic-only hashes; production expectations are never injected into fixtures.
    from app.schemas.historical_cashflow_qualification import ExpectedHashes as Upstream
    from app.schemas.historical_cashflow_source_forensics import OriginalHashes
    upstream = Upstream(b2=OriginalHashes(plan_sha256="0" * 64, authorization_sha256="1" * 64,
        probe_sha256="2" * 64, evaluation_sha256="3" * 64), forensics_sha256="4" * 64,
        diagnostic_plan_sha256="5" * 64, diagnostic_authorization_sha256="6" * 64, diagnostic_report_sha256="7" * 64)
    expected = ExpectedHashes(upstream=upstream, r2_source_contract_sha256="8" * 64, r2_qualification_sha256="9" * 64)
    source = signed(SourceContractReport, "report_sha256", status="COMPLETE", expected=upstream,
                    observations=tuple(observations), comparisons=compare_profiles(observations))
    integrity = signed(SourceRowIntegrityReport, "report_sha256", status="PASS", expected=expected,
        observations=tuple(findings), retained_row_count=sum(f.retained_rows_checked for f in findings))
    semantics = build_semantics(source=source, integrity=integrity, rows_by_request=rows)
    return SemanticsPackage(status=semantics.status, integrity=integrity, semantics=semantics), source, rows


def test_canonical_schedule_amounts_maturity_matrix_and_no_double_payments():
    before = str(getcontext())
    package, source, rows = synthetic_package()
    assert [c.coupon_row_count for c in package.semantics.coupon_coverage] == [38, 12]
    assert [c.coupon_amount_present_count for c in package.semantics.coupon_coverage] == [35, 12]
    assert [c.coupon_amount_missing_count for c in package.semantics.coupon_coverage] == [3, 0]
    assert all(c.source_schedule_completeness == "COMPLETE" for c in package.semantics.coupon_coverage)
    assert len(package.semantics.coupon_coverage[0].coupon_amount_missing_dates) == 3
    mature = [e for e in package.semantics.events if e.semantic_classification == "SCHEDULED_FINAL_MATURITY"]
    assert len(mature) == 2 and all(e.returned_isin is None and e.payment_status == "NOT_CONFIRMED" for e in mature)
    assert not any(e.table == "redemptions" for e in package.semantics.events)
    assert all(not e.actual_payment_confirmed and not e.pit_ready for e in package.semantics.events)
    assert all(f.proof_status != "DOCUMENTED" for e in package.semantics.events for f in e.fields)
    assert all(c.canonical_request_id == next(o.request_id for o in source.observations if o.requested_secid == c.secid and o.table == c.table and o.profile == "BASELINE")
               for c in package.semantics.canonical_observations if c.table == "coupons")
    assert len([e for e in package.semantics.events if e.table == "coupons"]) == 100  # Occurrences, not 100 economic payments.
    assert str(getcontext()) == before and synthetic_package()[0] == package
    assert SemanticsPackage.model_validate_json(package.model_dump_json()) == package
    with pytest.raises(ValueError): package.status = "BLOCKED"
    with pytest.raises(ValueError): SemanticsPackage.model_validate(dict(package, extra=True))


@pytest.mark.parametrize("value", ["Maturity", " maturity", "amortization", "partial", "", None, 1, True])
def test_unknown_marker_never_infers_partial_or_final(value):
    assert classify_source_event(table="amortizations", row={"data_source": value}) == "SOURCE_AMORTIZATION_UNCLASSIFIED"
    assert field_evidence({"data_source": value}, "data_source").proof_status != "DOCUMENTED"


def test_amount_units_percentage_offer_context_and_profile_disagreement():
    package, source, rows = synthetic_package()
    fields = {f.field_name: f for f in package.semantics.events[0].fields}
    assert fields["faceunit"].proof_status == "PARTIAL" and fields["value"].proof_status == "UNPROVEN"
    assert "per-bond" in fields["value"].proposed_semantic
    offers = [e for e in package.semantics.events if e.table == "offers"]
    assert offers and all(e.semantic_classification == "SCHEDULED_OFFER_OBSERVATION" for e in offers)
    assert all(f.raw_source_type == "NoneType" for e in offers for f in e.fields if f.field_name == "value")
    changed = source.comparisons[0].model_copy(update={"verdict": "CROSS_PROFILE_DIFFERENCE"})
    altered = source.model_copy(update={"comparisons": (changed, *source.comparisons[1:])})
    result = build_semantics(source=altered, integrity=package.integrity, rows_by_request=rows)
    assert any("ECONOMIC_PROMOTION_BLOCKED_BY_PROFILE_DISAGREEMENT" in c.diagnostics for c in result.canonical_observations)
    assert not result.total_return_execution_ready and not result.acquisition_ready


def test_complete_then_profile_priority_and_ambiguous_pages_not_combined():
    package, source, rows = synthetic_package()
    obs = list(source.observations); first = next(i for i, o in enumerate(obs) if o.table == "coupons" and o.profile == "BASELINE")
    second = next(i for i, o in enumerate(obs) if o.table == "coupons" and o.profile == "TABLE_ONLY")
    obs[first] = obs[first].model_copy(update={"view": obs[first].view.model_copy(update={"completeness": "COMPLETENESS_UNPROVEN"})})
    obs[second] = obs[second].model_copy(update={"view": obs[second].view.model_copy(update={"completeness": "COMPLETE"})})
    result = build_semantics(source=source.model_copy(update={"observations": tuple(obs)}), integrity=package.integrity, rows_by_request=rows)
    assert result.canonical_observations[0].canonical_request_id == obs[second].request_id
    amort = next(o for o in obs if o.table == "amortizations")
    duplicate = amort.model_copy(update={"request_id": "f" * 64})
    rows[duplicate.request_id] = rows[amort.request_id]
    result = build_semantics(source=source.model_copy(update={"observations": (*obs, duplicate)}), integrity=package.integrity, rows_by_request=rows)
    assert any(c.table == "amortizations" and c.secid == amort.requested_secid and c.canonical_request_id is None and
               "MULTIPAGE_OBSERVATIONS_NOT_COMBINED" in c.diagnostics for c in result.canonical_observations)


def test_field_level_differences_and_verified_row_guard():
    package, source, rows = synthetic_package()
    obs = next(o for o in source.observations if o.table == "coupons" and o.profile == "TABLE_ONLY")
    original_rows = rows[obs.request_id]
    changed = dict(original_rows[0]); changed["faceunit"] = "USD"
    rows[obs.request_id] = (changed, *original_rows[1:])
    assert (obs.requested_secid, obs.table, "faceunit") in field_conflict_keys(source.observations, rows)
    with pytest.raises(IdentityError): build_semantics(source=source, integrity=package.integrity, rows_by_request=rows)


def test_full_lineage_publication_tampering_runner_and_source_preservation(r2_inputs, tmp_path, monkeypatch):
    import app.services.historical_cashflow_semantics_artifacts as artifacts
    r2 = HistoricalCashflowQualificationService.qualify_existing_evidence(**r2_inputs)
    r2root = tmp_path / "r2-published"; r2root.mkdir()
    publish_r2(package=r2, output_dir=r2root, **publication_args(r2_inputs))
    expected = ExpectedHashes(upstream=r2_inputs["expected"], r2_source_contract_sha256=r2.source_contract.report_sha256,
                              r2_qualification_sha256=r2.qualification.report_sha256)
    kwargs = dict(r2_inputs, expected=expected, r2_output_dir=r2root)
    roots = dict(publication_args(r2_inputs)["roots"], r2=r2root)
    files = publication_args(r2_inputs)["files"]
    protected = {p: p.read_bytes() for root in roots.values() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: pytest.fail("offline HTTP construction"))
    package = Service.qualify_cashflow_semantics(**kwargs)
    assert package.status == "COMPLETE", package.integrity.blockers
    assert Service.qualify_cashflow_semantics(**kwargs) == package
    assert package.integrity.retained_row_count == len(package.semantics.events)
    output = tmp_path / "r3-output"; output.mkdir()
    publish(package=package, output_dir=output, files=files, roots=roots)
    assert verify_published(output_dir=output) == package
    publish(package=package, output_dir=output, files=files, roots=roots)
    with pytest.raises(IdentityError): publish(package=package, output_dir=r2root, files=files, roots=roots)
    # Controlled interruption removes only files created by this attempt.
    other = tmp_path / "interrupted"; other.mkdir(); real_atomic = artifacts.atomic; calls = []
    def interrupted(path, *a, **kw):
        calls.append(path)
        if len(calls) == 2: raise OSError("synthetic interruption")
        return real_atomic(path, *a, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(artifacts, "atomic", interrupted)
        with pytest.raises(OSError): publish(package=package, output_dir=other, files=files, roots=roots)
    assert not list(other.glob("task308b2r3-*")) and not list(other.glob(".task308b2r3-*"))
    assert protected == {p: p.read_bytes() for p in protected}
    for target in (r2root / "task308b2r2-source-contract-report.json", next((kwargs["r1_evidence_root"] / "quarantine").glob("*.body"))):
        saved = target.read_bytes()
        try:
            target.write_bytes(b"{}")
            assert Service.qualify_cashflow_semantics(**kwargs).status == "BLOCKED"
            with pytest.raises(IdentityError): publish(package=package, output_dir=output, files=files, roots=roots)
        finally: target.write_bytes(saved)
    bad = dict(kwargs, expected=expected.model_copy(update={"r2_qualification_sha256": "0" * 64}))
    refused = Service.qualify_cashflow_semantics(**bad)
    assert refused.status == "BLOCKED" and not refused.semantics.events
    with pytest.raises(ValueError): type(package.integrity).model_validate(dict(package.integrity, pit_ready=True))
    script = Path(__file__).resolve().parents[2] / "scripts/historical_cashflow_semantics.py"
    runner_out = tmp_path / "runner-out"; runner_out.mkdir()
    args = [sys.executable, str(script), "--mode", "QUALIFY_CASHFLOW_SEMANTICS", "--output", str(runner_out)]
    for k, v in kwargs.items():
        if k != "expected": args += ["--" + k.replace("_", "-"), str(v)]
    for name in ("plan", "authorization", "probe", "evaluation"):
        args += ["--expected-b2-" + name + "-sha256", getattr(expected.upstream.b2, name + "_sha256")]
    for name in ("forensics", "diagnostic_plan", "diagnostic_authorization", "diagnostic_report"):
        args += ["--expected-" + name.replace("_", "-") + "-sha256", getattr(expected.upstream, name + "_sha256")]
    args += ["--expected-r2-source-contract-sha256", expected.r2_source_contract_sha256,
             "--expected-r2-qualification-sha256", expected.r2_qualification_sha256]
    process = subprocess.run(args, cwd=tmp_path, text=True, capture_output=True)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["status"] == "COMPLETE" and verify_published(output_dir=runner_out) == package
    summary = output / "task308b2r3-cashflow-semantics-summary.md"; summary.write_bytes(b"conflict")
    with pytest.raises(IdentityError): publish(package=package, output_dir=output, files=files, roots=roots)


def test_static_isolation_and_pinned_a4_fingerprint():
    from app.services.historical_discovery_checkpoint import fingerprint
    assert fingerprint() == "2087e8465cc9274f3f9408795c21ac5bee3e5033e6b23a3806adbfa596c84027"
    folder = Path(__file__).resolve().parents[1] / "app/services"
    for name in ("historical_cashflow_row_integrity.py", "historical_cashflow_semantics_service.py", "historical_cashflow_semantics_artifacts.py"):
        tree = ast.parse((folder / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): assert not any(n.name.split(".")[0] in ("httpx", "requests", "sqlalchemy", "socket") for n in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("fetch", "probe", "commit", "execute", "sync", "apply_batch", "acquire_batch")
