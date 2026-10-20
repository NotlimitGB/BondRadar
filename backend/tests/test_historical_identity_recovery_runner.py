"""Task308B1 publication/runner contracts; no production or source requests."""
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.schemas.historical_identity_recovery_inventory import InventoryLimits, InventoryAudit, OperationResult
from app.services.historical_identity_recovery_artifacts import verify_inputs, publish, verify_published, NAMES, file_sha
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_audit_canonical_json import digest
from test_historical_identity_recovery_inventory import evidence, pristine_evidence


ROOT=Path(__file__).resolve().parents[2]


def runner():
    spec=importlib.util.spec_from_file_location("task308b1_runner",ROOT/"scripts/historical_identity_recovery_inventory.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def arguments(e):
    args=[]
    for key,value in e.kwargs.items():args.extend(("--"+key.replace("_","-"),str(value)))
    return args+["--output-dir",str(e.output)]


def test_report_artifacts_hashes_idempotency_and_arbitrary_cwd(evidence,monkeypatch,tmp_path):
    e=evidence;monkeypatch.chdir(tmp_path);module=runner();out,err=io.StringIO(),io.StringIO()
    assert module.main(arguments(e)+["--expect-total-secids","72","--expect-current-bindings","11"],stdout=out,stderr=err)==0
    result=OperationResult.model_validate_json(out.getvalue());assert result.status=="COMPLETE" and not result.network_access
    assert set(NAMES)<={p.name for p in e.output.iterdir()}
    audit=InventoryAudit.model_validate_json((e.output/NAMES[-1]).read_bytes())
    assert digest(audit,exclude=("report_sha256",))==audit.report_sha256==result.report_sha256
    assert audit.inventory_sha256==file_sha(e.output/NAMES[0])
    assert verify_published(output_dir=e.output,expected_report_sha256=audit.report_sha256)==audit
    for name,sha in result.artifact_hashes:assert file_sha(e.output/name)==sha
    rows=[json.loads(line) for line in (e.output/NAMES[0]).read_bytes().splitlines()]
    assert len(rows)==len({r["secid"] for r in rows})==72
    saved={name:(e.output/name).read_bytes() for name in NAMES}
    assert module.main(arguments(e)+["--expect-total-secids","72","--expect-current-bindings","11"],stdout=io.StringIO(),stderr=io.StringIO())==0
    assert saved=={name:(e.output/name).read_bytes() for name in NAMES}
    assert "NOT_ESTABLISHED" in saved[NAMES[-2]].decode()
    (e.output/NAMES[0]).write_bytes(b"CONFLICT")
    assert module.main(arguments(e),stdout=io.StringIO(),stderr=io.StringIO())==2
    assert (e.output/NAMES[0]).read_bytes()==b"CONFLICT"


@pytest.mark.parametrize("limit",["output_bytes","max_identity_rows","working_row_bytes","probe_bytes"])
def test_size_memory_budget_refusal_no_authoritative_partial(evidence,limit):
    limits=InventoryLimits(**{limit:1})
    with pytest.raises(IdentityError):
        verified=verify_inputs(**evidence.kwargs,limits=limits)
        publish(verified,output_dir=evidence.output)
    assert not (evidence.output/NAMES[-1]).exists()
    assert not any(p.is_dir() for p in evidence.output.iterdir())


def test_controlled_publication_interruption_cleans_new_set(evidence,monkeypatch):
    verified=verify_inputs(**evidence.kwargs)
    module=__import__("app.services.historical_identity_recovery_artifacts",fromlist=["publish"])
    name="rename" if os.name=="nt" else "link";original=getattr(module.os,name);calls=[]
    def fail_second(src,dest):
        calls.append(str(dest))
        if len(calls)==2:raise OSError("SECRET filesystem details")
        return original(src,dest)
    monkeypatch.setattr(module.os,name,fail_second)
    with pytest.raises(OSError):publish(verified,output_dir=evidence.output)
    assert not any((evidence.output/n).exists() for n in NAMES)
    assert not any(p.is_dir() for p in evidence.output.iterdir())


def test_input_changes_and_output_overlap_block(evidence,tmp_path):
    verified=verify_inputs(**evidence.kwargs)
    with pytest.raises(IdentityError):publish(verified,output_dir=evidence.probe)
    p=evidence.kwargs["task307_report"];p.write_bytes(p.read_bytes()+b" ")
    with pytest.raises(IdentityError,match="CHANGED"):publish(verified,output_dir=evidence.output)
    assert not (evidence.output/NAMES[-1]).exists()


def test_source_path_symlinks_and_reserve(evidence,monkeypatch,tmp_path):
    verified=verify_inputs(**evidence.kwargs)
    monkeypatch.setattr("app.services.historical_identity_recovery_artifacts.shutil.disk_usage",lambda _:SimpleNamespace(free=1))
    with pytest.raises(IdentityError,match="RESERVE"):publish(verified,output_dir=evidence.output)
    # Symlink creation depends on OS privilege; verifier must always call the upstream safe-path gate.
    import app.services.historical_identity_recovery_artifacts as module
    original=module.safe_path
    def reject(path):
        if Path(path)==evidence.kwargs["task307_report"]:raise ValueError("CHECKPOINT_UNSAFE_PATH")
        return original(path)
    monkeypatch.setattr(module,"safe_path",reject)
    with pytest.raises(ValueError,match="UNSAFE"):verify_inputs(**evidence.kwargs)


def test_probe_empty_file_flood_is_bounded(evidence):
    for i in range(257):(evidence.probe/f"unreferenced-{i}").touch()
    with pytest.raises(IdentityError,match="FILE_COUNT_LIMIT"):verify_inputs(**evidence.kwargs)
    assert not (evidence.output/NAMES[-1]).exists()


def test_runner_blocked_sanitized_and_no_other_mode(evidence):
    module=runner();args=arguments(evidence);bad=list(args);bad[bad.index("--task307-report")+1]="SECRET_MISSING_INPUT_PATH"
    out,err=io.StringIO(),io.StringIO()
    assert module.main(bad,stdout=out,stderr=err)==2
    result=OperationResult.model_validate_json(out.getvalue());assert result.status=="BLOCKED" and result.report_sha256 is None
    assert "SECRET_MISSING" not in out.getvalue()+err.getvalue()
    with pytest.raises(SystemExit):module.main(args+["--mode","PROBE"])


def test_identical_partial_set_recovery_and_summary_hash_tamper(evidence):
    verified=verify_inputs(**evidence.kwargs);first=publish(verified,output_dir=evidence.output)
    (evidence.output/NAMES[-1]).unlink()
    assert publish(verified,output_dir=evidence.output)==first
    summary=evidence.output/NAMES[3];summary.write_bytes(summary.read_bytes()+b"CHANGED")
    with pytest.raises(IdentityError,match="BINDING"):verify_published(output_dir=evidence.output)


def test_static_no_network_db_authorize_or_source_mutation():
    paths=[ROOT/"backend/app/services/historical_identity_recovery_inventory_service.py",ROOT/"backend/app/services/historical_identity_recovery_artifacts.py",ROOT/"scripts/historical_identity_recovery_inventory.py"]
    for path in paths:
        tree=ast.parse(path.read_text())
        modules=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any("app.models" in n or "sqlalchemy" in n or "app.db" in n for n in modules)
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls&{"fetch","fetch_page","probe","authorize","getenv","commit","execute","sync","record_assertion"}
        assert "HistoricalIdentityProbeClient(" not in path.read_text()
