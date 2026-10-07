import importlib.util
import io,json
from pathlib import Path
from sqlalchemy import event,select
from app.models.historical_evidence_foundation import HistoricalEvidenceObservation as Observation
from app.services.historical_evidence_audit_service import HistoricalEvidenceAuditService
from test_historical_evidence_plans import archive,auth


def test_audit_counts_source_proof_and_select_only(archive):
    ids_,bindings=archive.seed();sql=[]
    event.listen(archive.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    service=HistoricalEvidenceAuditService(archive.factory);r=service.audit(run_sha256=archive.run)
    assert r.status=="COMPLETE" and r==service.audit(run_sha256=archive.run)
    assert r.unresolved_security_count>0 and not r.pit_ready
    assert dict(r.observation_counts)["MARKET"]==1 and r.annual_calendar_slot_capacity==0
    assert r.pending_work_count>0 and all(s.lstrip().upper().startswith(("SELECT","BEGIN")) for s in sql)
    with archive.factory() as db:
        row=db.get(Observation,ids_[0]);assert row.available_at is None and row.proof_state=="DATED_SOURCE"
        row.raw_json={"tampered":True};db.commit()
    assert service.audit(run_sha256=archive.run).status=="BLOCKED"


def test_runner_default_report_rollback_and_sanitized_explicit_output(archive,tmp_path,monkeypatch):
    archive.seed()
    path=Path(__file__).resolve().parents[2]/"scripts"/"historical_evidence_foundation.py"
    spec=importlib.util.spec_from_file_location("foundation_runner",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path);output=io.StringIO();commits=[]
    event.listen(archive.engine,"commit",lambda c:commits.append(1))
    assert module.main(["--run-sha256",archive.run],session_factory=archive.factory,stdout=output)==0
    assert not commits and not list(tmp_path.iterdir())
    assert json.loads(output.getvalue())["network_access"] is False
    target=tmp_path/"report.json"
    assert module.main(["--run-sha256",archive.run,"--output",str(target)],session_factory=archive.factory)==0
    assert target.exists()
    output=io.StringIO()
    assert module.main(["--mode","ACQUIRE"],stdout=output)==1 and "SECRET" not in output.getvalue()
