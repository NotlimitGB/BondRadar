"""Offline full-lineage qualification and immutable publication."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import ast
import httpx
import pytest
from app.schemas.historical_cashflow_qualification import ExpectedHashes, QualificationPackage
from app.schemas.historical_cashflow_source_forensics import DiagnosticLimits
from app.services.historical_cashflow_source_forensics_service import HistoricalCashflowSourceForensicsService as Forensics
from app.services.historical_cashflow_diagnostic_probe_service import HistoricalCashflowDiagnosticProbeService as Probe
from app.services.historical_cashflow_diagnostic_client import HistoricalCashflowDiagnosticClient
from app.services.historical_cashflow_qualification_service import HistoricalCashflowQualificationService as Service
from app.services.historical_cashflow_qualification_artifacts import publish, verify_published
from app.services.historical_identity_probe_service import encode
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_source_forensics import original, pristine_original, response_body, row


@pytest.fixture
def inputs(original, tmp_path):
    package = Forensics.forensic_plan(**original.kwargs)
    root = tmp_path / "diagnostic"; root.mkdir(mode=0o700)
    now = [datetime(2026, 2, 1, tzinfo=timezone.utc)]
    def sleep(n): now[0] += timedelta(seconds=n)
    def handler(request):
        table = request.url.params["iss.only"].split(",")[0]
        sid = request.url.path.rsplit("/", 1)[-1][:-5]
        if table == "offers": return httpx.Response(200, content=response_body(table, rows=[], cursor=False))
        body = json.loads(response_body(table, rows=[row(sid)], cursor=False))
        cursor = {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": [[0, 1, 100]]}
        if table == "redemptions" or "," in request.url.params["iss.only"]:
            text = json.dumps(body)[:-1] + ',"coupons.cursor":' + json.dumps(cursor) + ',"coupons.cursor":' + json.dumps(cursor) + '}'
        else: text = json.dumps(body)
        return httpx.Response(200, content=text.encode())
    probe = Probe(client_factory=lambda: HistoricalCashflowDiagnosticClient(transport=httpx.MockTransport(handler)),
                  sleeper=sleep, clock=lambda: now[0])
    auth = probe.authorize(plan=package.plan, reviewed_plan_sha256=package.plan.plan_sha256,
        secids=package.plan.secids, tables=package.plan.tables, profiles=("BASELINE", "TABLE_ONLY"),
        limits=DiagnosticLimits(), evidence_root=root, confirm=True)
    result = probe.probe(plan=package.plan, authorization=auth, evidence_root=root, confirm_network=True)
    assert result.status == "COMPLETE" and result.quarantine_pages == 6
    reports = tmp_path / "r1-reports"; reports.mkdir()
    files = dict(b2_plan=original.kwargs["b2_plan"], b2_authorization=original.kwargs["b2_authorization"], b2_evaluation=original.kwargs["b2_evaluation"])
    for key, value in (("r1_forensics", package.report), ("r1_plan", package.plan), ("r1_authorization", auth), ("r1_report", result)):
        files[key] = reports / (key + ".json"); files[key].write_bytes(encode(value))
    expected = ExpectedHashes(b2=original.expected, forensics_sha256=package.report.report_sha256,
        diagnostic_plan_sha256=package.plan.plan_sha256, diagnostic_authorization_sha256=auth.authorization_sha256,
        diagnostic_report_sha256=result.report_sha256)
    return dict(**files, b2_evidence_root=original.root, r1_evidence_root=root, expected=expected)


def publication_args(inputs):
    return dict(files={k: v for k, v in inputs.items() if k != "expected" and not k.endswith("evidence_root")},
                roots={"b2": inputs["b2_evidence_root"], "r1": inputs["r1_evidence_root"]})


def test_full_offline_lineage_occurrences_hashes_and_publication(inputs, tmp_path, monkeypatch):
    monkeypatch.setattr(HistoricalCashflowDiagnosticClient, "__init__", lambda *a, **kw: pytest.fail("HTTP instantiated offline"))
    before = {p: p.read_bytes() for root in publication_args(inputs)["roots"].values() for p in root.rglob("*") if p.is_file()}
    result = Service.qualify_existing_evidence(**inputs)
    assert result.status == "COMPLETE", result.source_contract.blockers
    assert Service.qualify_existing_evidence(**inputs) == result
    assert len(result.source_contract.observations) == 14 and len(result.qualification.coverage) == 8
    assert sum(o.view.duplicate_exception is not None for o in result.source_contract.observations) == 6
    assert all(c.verdict == "CROSS_PROFILE_MATCH" for c in result.source_contract.comparisons if c.table != "offers")
    assert any(c.verdict == "CROSS_PROFILE_DIFFERENCE" for c in result.source_contract.comparisons if c.table == "offers")
    coupons = [o for o in result.source_contract.observations if o.table == "coupons"]
    assert len(coupons) == 4 and all(len(o.view.events) == 1 for o in coupons)
    assert len({o.observed_at for o in result.source_contract.observations}) > 1
    assert not any(o.view.unit_status == "DOCUMENTED" for o in result.source_contract.observations)
    assert before == {p: p.read_bytes() for p in before}
    assert QualificationPackage.model_validate_json(result.model_dump_json()) == result
    output = tmp_path / "output"; output.mkdir()
    publish(package=result, output_dir=output, **publication_args(inputs))
    assert verify_published(output_dir=output) == result
    publish(package=result, output_dir=output, **publication_args(inputs))
    assert len(list(output.glob("task308b2r2-*"))) == 4
    (output / "task308b2r2-qualification-summary.md").write_bytes(b"conflict")
    with pytest.raises(IdentityError): publish(package=result, output_dir=output, **publication_args(inputs))
    assert not list(output.glob(".task308b2r2-*"))
    with pytest.raises(IdentityError): publish(package=result, output_dir=inputs["b2_evidence_root"], **publication_args(inputs))


@pytest.mark.parametrize("damage", ["hash", "r1_report", "r1_forensics", "quarantine", "missing", "binding"])
def test_tampering_missing_and_destination_fail_closed(inputs, damage, tmp_path):
    kwargs = dict(inputs)
    if damage == "hash": kwargs["expected"] = inputs["expected"].model_copy(update={"diagnostic_report_sha256": "0" * 64})
    elif damage in ("r1_report", "r1_forensics"): inputs[damage].write_bytes(b"{}")
    elif damage == "quarantine": next((inputs["r1_evidence_root"] / "quarantine").glob("*.body")).write_bytes(b"{}")
    elif damage == "missing": inputs["r1_plan"].unlink()
    else:
        import shutil
        destination = tmp_path / "relocated"; shutil.copytree(inputs["r1_evidence_root"], destination)
        kwargs["r1_evidence_root"] = destination
    result = Service.qualify_existing_evidence(**kwargs)
    assert result.status == "BLOCKED" and result.source_contract.blockers
    assert result.source_contract.observations == () and result.qualification.coverage == ()


def test_changed_inputs_before_publication_and_controlled_failure(inputs, tmp_path, monkeypatch):
    result = Service.qualify_existing_evidence(**inputs)
    output = tmp_path / "output"; output.mkdir()
    import app.services.historical_cashflow_qualification_artifacts as artifacts
    real = artifacts.atomic
    calls = []
    def fail(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 3: raise OSError("synthetic private path")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(artifacts, "atomic", fail)
    with pytest.raises(OSError): publish(package=result, output_dir=output, **publication_args(inputs))
    assert not list(output.glob("task308b2r2-*"))
    inputs["r1_report"].write_bytes(b"{}")
    with pytest.raises(IdentityError): publish(package=result, output_dir=output, **publication_args(inputs))


def test_runner_arbitrary_cwd_and_single_mode(inputs, tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/historical_cashflow_qualification.py"
    output = tmp_path / "runner-output"; output.mkdir()
    args = [sys.executable, str(script), "--mode", "QUALIFY_EXISTING_EVIDENCE", "--output", str(output)]
    for key, value in inputs.items():
        if key != "expected": args += ["--" + key.replace("_", "-"), str(value)]
    expected = inputs["expected"]
    for key in ("plan", "authorization", "probe", "evaluation"):
        args += ["--expected-b2-" + key + "-sha256", getattr(expected.b2, key + "_sha256")]
    for key in ("forensics", "diagnostic_plan", "diagnostic_authorization", "diagnostic_report"):
        args += ["--expected-" + key.replace("_", "-") + "-sha256", getattr(expected, key + "_sha256")]
    process = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr
    envelope = json.loads(process.stdout)
    assert envelope["status"] == "COMPLETE" and envelope["result"] is None
    assert not envelope["network_access"] and not envelope["db_mutation"]
    assert verify_published(output_dir=output).status == "COMPLETE"


def test_no_existing_fingerprint_delta_or_network_database_paths():
    root = Path(__file__).resolve().parents[2]
    from app.services.historical_discovery_checkpoint import fingerprint
    services = root / "backend/app/services"
    # Pinned at the reviewed starting SHA; works with CI's shallow checkout.
    assert fingerprint() == "2087e8465cc9274f3f9408795c21ac5bee3e5033e6b23a3806adbfa596c84027"
    for name in ("historical_cashflow_candidate_decoder.py", "historical_cashflow_qualification_service.py", "historical_cashflow_qualification_artifacts.py"):
        tree = ast.parse((services / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): assert not any(a.name.split(".")[0] in {"httpx", "requests", "sqlalchemy", "socket"} for a in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"fetch", "probe", "commit", "execute", "sync", "acquire_batch", "apply_batch"}
