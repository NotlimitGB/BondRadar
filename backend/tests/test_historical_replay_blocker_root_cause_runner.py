"""Read-only runner including arbitrary repository mount locations."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import pytest
from sqlalchemy import create_engine,event
from app.db.base import Base
import app.models
from app.schemas.historical_replay_blocker_root_cause import HistoricalReplayBlockerRootCauseAuditV1 as Audit
from test_modern_historical_replay_readiness_audit import seeded


def load(path=None):
    path=path or Path(__file__).resolve().parents[2]/"scripts"/"historical_replay_blocker_root_cause_audit.py"
    spec=importlib.util.spec_from_file_location("task306a1_runner",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_stdout_file_readonly_sql_and_rollback(tmp_path,monkeypatch):
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    sql=[];commits=[];rollbacks=[]
    event.listen(engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    event.listen(engine,"commit",lambda c:commits.append(1))
    event.listen(engine,"rollback",lambda c:rollbacks.append(1))
    monkeypatch.chdir(tmp_path);r=load();stream=io.StringIO()
    assert r.main([],engine=engine,stdout=stream)==0
    result=json.loads(stream.getvalue());assert result["status"]=="COMPLETE"
    assert not result["DB_MUTATION"] and not result["NETWORK_ACCESS"]
    assert not commits and rollbacks and not list(tmp_path.iterdir())
    assert all(s.lstrip().upper().startswith(("PRAGMA QUERY_ONLY","BEGIN","SELECT","WITH")) for s in sql)
    output=tmp_path/"explicit.json"
    assert r.main(["--mode","REPORT","--output",str(output)],engine=engine)==0
    assert output.read_text(encoding="utf-8")==stream.getvalue()
    engine.dispose()


def test_existing_query_only_not_weakened():
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with engine.connect() as c:c.exec_driver_sql("PRAGMA query_only=ON")
    assert load().report(engine).status=="COMPLETE"
    with engine.connect() as c:assert c.exec_driver_sql("PRAGMA query_only").scalar_one()==1
    engine.dispose()


def test_arbitrary_repository_root(tmp_path,monkeypatch):
    root=tmp_path/"work";scripts=root/"scripts";scripts.mkdir(parents=True)
    (root/"backend").mkdir()
    source=Path(__file__).resolve().parents[2]/"scripts"/"historical_replay_blocker_root_cause_audit.py"
    destination=scripts/source.name;shutil.copyfile(source,destination)
    previous=list(sys.path)
    try:
        monkeypatch.chdir(root)
        r=load(destination)
        assert r.ROOT==root and sys.path[0]==str(root/"backend")
        engine=create_engine("sqlite://");Base.metadata.create_all(engine)
        assert r.report(engine).status=="COMPLETE"
        engine.dispose()
    finally:sys.path[:]=previous


@pytest.mark.parametrize("mode",["APPLY","FIX","BACKFILL","SYNC"])
def test_report_only(mode):
    with pytest.raises(SystemExit) as e:load().main(["--mode",mode])
    assert e.value.code!=0


def test_source_schema_connection_and_output_failures_sanitized(tmp_path):
    r=load();engine=create_engine("sqlite://");stream=io.StringIO()
    assert r.main([],engine=engine,stdout=stream)==1
    report=json.loads(stream.getvalue());assert report["blockers"]==["SOURCE_TASK306A_BLOCKED"]
    assert "SELECT" not in stream.getvalue()
    Base.metadata.create_all(engine);stream=io.StringIO()
    assert r.main(["--output",str(tmp_path/"absent"/"private.json")],engine=engine,stdout=stream)==1
    assert "private.json" not in stream.getvalue() and "EXPLICIT_OUTPUT_WRITE_FAILED" in stream.getvalue()
    class Broken:
        dialect=type("Dialect",(),{"name":"postgresql"})()
        def connect(self):raise RuntimeError("password=secret; private-host")
    failure=r.report(Broken())
    assert failure.status=="BLOCKED" and "secret" not in failure.model_dump_json()
    engine.dispose()


def test_postgresql_repeatable_read_readonly_no_commit(monkeypatch):
    r=load();events=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execution_options(self,**kw):events.append(kw);return self
        def begin(self):events.append("begin")
        def rollback(self):events.append("rollback")
    class Engine:
        dialect=type("Dialect",(),{"name":"postgresql"})()
        def connect(self):return Connection()
    class Db:
        def __init__(self,**kw):events.append(kw)
        def __enter__(self):return self
        def __exit__(self,*a):pass
    class Service:
        def __init__(self,db):pass
        def build(self):return Audit(status="COMPLETE")
    monkeypatch.setattr(r,"Session",Db);monkeypatch.setattr(r,"HistoricalReplayBlockerRootCauseAuditService",Service)
    assert r.report(Engine()).status=="COMPLETE"
    assert events[0]=={"isolation_level":"REPEATABLE READ","postgresql_readonly":True}
    assert events[1]=="begin" and events[-1]=="rollback"
    assert events[2]["join_transaction_mode"]=="rollback_only"
