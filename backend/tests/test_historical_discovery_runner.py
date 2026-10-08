import ast
import importlib.util
import io
import json
from pathlib import Path
import pytest
from test_historical_discovery_checkpoint import POLICY, DAY, client, response
from app.services import historical_discovery_checkpoint as checkpoint

ROOT=Path(__file__).resolve().parents[2]


def runner():
    spec=importlib.util.spec_from_file_location("a4_runner",ROOT/"scripts/historical_evidence_foundation.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def scope(monkeypatch):
    queries=tuple(checkpoint.discovery_queries(POLICY))[:4]
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))


def test_cli_policy_authorize_bounded_resume_validate_finalize_without_db(tmp_path,scope,monkeypatch):
    module=runner();monkeypatch.chdir(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError("DB must not be accessed")
    monkeypatch.setattr(module,"create_engine",forbidden)
    source,_=client(response);common=["--evidence-root",str(tmp_path)]
    output=io.StringIO()
    assert module.main(["--mode","DISCOVERY_POLICY"],stdout=output)==0
    assert json.loads(output.getvalue())["cutoff"]=="2026-09-30"
    auth=tmp_path/"authorization.json"
    assert module.main(["--mode","DISCOVERY_AUTHORIZE",*common,"--confirm-network","--max-requests","2","--output",str(auth)],stdout=io.StringIO())==0
    args=[*common,"--authorization",str(auth),"--max-requests","2","--confirm-network"]
    output=io.StringIO()
    assert module.main(["--mode","DISCOVER",*args],source_client=source,stdout=output)==0
    assert json.loads(output.getvalue())["status"]=="BUDGET_STOP"
    output=io.StringIO()
    assert module.main(["--mode","DISCOVERY_RESUME",*args],source_client=source,stdout=output)==0
    assert json.loads(output.getvalue())["status"]=="COMPLETE"
    for mode in ("DISCOVERY_PROGRESS","DISCOVERY_VALIDATE","DISCOVERY_FINALIZE","DISCOVERY_SUMMARY"):
        output=io.StringIO();assert module.main(["--mode",mode,*common],stdout=output)==0
        result=json.loads(output.getvalue());assert result["db_mutation"] is False and result["pit_ready"] is False


def test_cli_sanitizes_paths_and_never_defaults_discovery_to_db(tmp_path,monkeypatch):
    module=runner()
    monkeypatch.setattr(module,"create_engine",lambda *a,**k:(_ for _ in ()).throw(AssertionError("SECRET_DATABASE_URL")))
    calls=[];source,_=client(lambda r:calls.append(r) or response(r))
    output=io.StringIO()
    assert module.main(["--mode","DISCOVER","--confirm-network"],source_client=source,stdout=output)==1
    assert not calls and "SECRET" not in output.getvalue()
    assert json.loads(output.getvalue())["db_mutation"] is False
    output=io.StringIO()
    assert module.main(["--mode","DISCOVERY_AUTHORIZE","--confirm-network","--evidence-root",str(tmp_path),
                        "--output",str(tmp_path.parent/"outside.json")],stdout=output)==1
    assert not (tmp_path.parent/"outside.json").exists()
    assert "outside.json" not in output.getvalue()


def test_checkpoint_module_static_source_only_and_streaming_guards():
    source=(ROOT/"backend/app/services/historical_discovery_checkpoint.py").read_text()
    tree=ast.parse(source)
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):imports.extend(alias.name for alias in node.names)
        if isinstance(node,ast.ImportFrom):imports.append(node.module or "")
    assert not any(name.startswith(("sqlalchemy","sqlite3","app.models","app.core.config")) for name in imports)
    assert ".model_dump(" not in source
    assert "requests.get" not in source and "httpx.Client" not in source
    assert "os.environ" not in source and "getenv" not in source
