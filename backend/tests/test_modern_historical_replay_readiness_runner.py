"""REPORT CLI has explicit output only and never opens a source connection."""
import importlib.util
import io
import json
from pathlib import Path
import ast
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db.base import Base
import app.models
from app.models.company import Company
from test_modern_historical_replay_readiness_audit import seeded


def runner():
    path=Path(__file__).resolve().parents[2]/"scripts"/"modern_historical_replay_readiness_audit.py"
    spec=importlib.util.spec_from_file_location("audit306a_runner",path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def test_report_stdout_only_query_only_and_no_commit(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    commits=[];sql=[]
    event.listen(engine,"commit",lambda c:commits.append(1))
    event.listen(engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    output=io.StringIO();code=runner().main([],engine=engine,stdout=output)
    report=json.loads(output.getvalue())
    assert code==0 and report["status"]=="COMPLETE" and report["audit_classification"]=="INSUFFICIENT_FOR_REPLAY"
    assert report["DB_MUTATION"] is False and report["NETWORK_ACCESS"] is False
    assert not commits and not list(tmp_path.iterdir())
    assert all(s.upper().startswith(("SELECT","WITH","PRAGMA QUERY_ONLY","BEGIN")) for s in sql)
    # Restore the pooled connection's previous write capability after REPORT.
    with Session(engine) as db:
        db.add(Company(name="Isolated post-audit fixture",ticker="AFTER306A"));db.commit()
    engine.dispose()


def test_explicit_file_matches_stdout_and_deterministic_hash(seeded,tmp_path):
    r=runner();output=io.StringIO()
    assert r.main([],engine=seeded.engine,stdout=output)==0
    path=tmp_path/"report.json"
    assert r.main(["--mode","REPORT","--output",str(path)],engine=seeded.engine)==0
    assert path.read_text()==output.getvalue()


def test_missing_schema_failure_sanitized_and_no_apply_mode():
    engine=create_engine("sqlite://");output=io.StringIO();r=runner()
    assert r.main([],engine=engine,stdout=output)==1
    report=json.loads(output.getvalue());assert report["status"]=="BLOCKED"
    assert "sqlite" not in output.getvalue().lower() and "SELECT" not in output.getvalue()
    with pytest.raises(SystemExit) as e:r.main(["--mode","APPLY"],engine=engine)
    assert e.value.code!=0
    engine.dispose()


def test_postgresql_readonly_transaction_options(monkeypatch):
    r=runner();observations=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execution_options(self,**kwargs):observations.append(kwargs);return self
        def rollback(self):observations.append("rollback")
    class Engine:
        dialect=type("Dialect",(),{"name":"postgresql"})()
        def connect(self):return Connection()
    class FakeSession:
        def __init__(self,**kwargs):observations.append(kwargs)
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(r,"Session",FakeSession)
    from app.schemas.modern_historical_replay_readiness import HistoricalReplayReadinessAuditV1
    class Service:
        def __init__(self,db):pass
        def build(self):return HistoricalReplayReadinessAuditV1(status="COMPLETE",audit_classification="INSUFFICIENT_FOR_REPLAY")
    monkeypatch.setattr(r,"ModernHistoricalReplayReadinessAuditService",Service)
    assert r.report(Engine()).status=="COMPLETE"
    assert observations[0]=={"isolation_level":"REPEATABLE READ","postgresql_readonly":True}
    assert observations[-1]=="rollback"


def test_runner_has_no_mutation_or_live_source_methods():
    tree=ast.parse(Path(runner().__file__).read_text(encoding="utf-8"))
    calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls & {"commit","flush","add","delete","sync","apply","fetch","get","post"}


def test_connection_failure_redacted(monkeypatch):
    r=runner()
    class Engine:
        dialect=type("Dialect",(),{"name":"sqlite"})()
        def connect(self):raise RuntimeError("password=secret-token host=private")
    result=r.report(Engine())
    assert result.status=="BLOCKED" and "secret-token" not in result.model_dump_json()


def test_existing_query_only_state_is_not_weakened():
    r=runner();engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with engine.connect() as c:
        c.exec_driver_sql("PRAGMA query_only=ON")
    assert r.report(engine).status=="COMPLETE"
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA query_only").scalar_one()==1
    engine.dispose()


def test_explicit_output_error_is_sanitized(tmp_path):
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    output=io.StringIO()
    assert runner().main(["--output",str(tmp_path/"absent"/"private.json")],engine=engine,stdout=output)==1
    value=json.loads(output.getvalue())
    assert value["known_p0_blockers"]==["EXPLICIT_OUTPUT_WRITE_FAILED"]
    assert "private.json" not in output.getvalue()
    engine.dispose()
