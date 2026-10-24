"""Task310: hermetic source binding, real pure planning and read-only ownership."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_UP
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.current_forward_readiness import (
    AccountEvidence, CurrentForwardReadinessRequest, CurrentForwardReadinessReport, ReviewedGenesis, SourceContext,
)
from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection
from app.schemas.shadow_scale_experiment import CAPITAL_GRID_RUB
from app.services.tinvest_bond_identity_bridge_service import TInvestBondIdentityBridgeService
from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService
from app.services import current_forward_readiness_report as pure
from app.services import current_forward_readiness_service as module
from test_tinvest_bond_admission_manifest import source as source_row
from test_shadow_scale_experiment_genesis import prepared
from test_risk_candidate_reducer import missing_issuer
from test_investment_model_reducer import evaluate, batch as candidate_batch
from test_unified_candidate_reducer import composite, snapshot, incomplete
from app.services.unified_candidate_reducer import UnifiedCandidateReducer

D = Decimal
ROOT = Path(__file__).resolve().parents[2]


def request_for(prepared, *, changes=None, observed_delta=0, plans=False):
    investment = prepared.inputs[0].source_shadow_execution.source_strategy.source_investment_batch
    ids = investment.source_batch.requested_bond_ids
    now = datetime.combine(investment.as_of_date, datetime.min.time(), tzinfo=timezone.utc)+timedelta(hours=12)
    internal = tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in investment.evaluations)
    rows = tuple(source_row(f"UID-{p.bond_id}",p.isin).model_copy(update=(changes or {}).get(p.bond_id,{})) for p in internal)
    bridge = TInvestBondIdentityBridgeService.build(rows,internal,())
    artifact = TInvestFrozenAdmissionEvidenceService.build(source_bonds=rows,internal_bonds=internal,
        core_m3_complete_bond_ids=(),identity_bridge=bridge,finalized_resolutions=(),captured_at=now)
    context = SourceContext(collection_id="synthetic-task310",observed_at=now-timedelta(seconds=observed_delta),
        frozen_admission=artifact, expected_artifact_sha256=artifact.artifact_sha256,
        expected_source_bonds_sha256=artifact.source_bonds_sha256)
    return CurrentForwardReadinessRequest(as_of_date=investment.as_of_date,evaluation_at=now,implementation_sha="a"*40,
        bond_ids=ids,source_context=context,reviewed_genesis=tuple(ReviewedGenesis(capital_rub=c.capital_rub,
            plan=c.reviewed_strategy_genesis) for c in prepared.inputs) if plans else ())


@contextmanager
def readonly(engine):
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA query_only=ON")
        connection.exec_driver_sql("BEGIN")
        with Session(bind=connection,join_transaction_mode="rollback_only",autoflush=True) as db:
            try: yield db
            finally: connection.rollback()
        connection.exec_driver_sql("PRAGMA query_only=OFF")
        connection.rollback()


@pytest.fixture
def env(prepared, monkeypatch):
    investment = prepared.inputs[0].source_shadow_execution.source_strategy.source_investment_batch
    calls = []
    def load(self, ids, day, **kwargs):
        calls.append((self.db,ids,day,kwargs))
        return investment
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",load)
    return SimpleNamespace(prepared=prepared,investment=investment,calls=calls,request=request_for(prepared))


def run(env, request=None):
    with readonly(env.prepared.env.engine) as db:
        return module.CurrentForwardReadinessService(db).build(request=request or env.request)


def decisions(report): return {d.name:d.value for d in report.readiness_decisions}


def test_shared_investment_seven_real_plans_and_missing_genesis(env,monkeypatch):
    counts={"risk":0,"strategy":[],"terms":0,"shadow":0}
    for cls,method,key in ((module.RiskCandidateReducer,"build","risk"),(module.PortfolioStrategyBuilder,"build","strategy"),
        (module.ShadowExecutionTermsLoader,"build","terms"),(module.ShadowExecutionPlanner,"build","shadow")):
        original=getattr(cls,method)
        def wrapper(*args,_original=original,_key=key,**kwargs):
            if _key=="strategy": counts[_key].append(args[2].capital_rub)
            else: counts[_key]+=1
            return _original(*args,**kwargs)
        monkeypatch.setattr(cls,method,wrapper)
    result=run(env)
    assert result.status=="BLOCKED", result.blockers
    assert len(env.calls)==1 and counts=={"risk":1,"strategy":list(CAPITAL_GRID_RUB),"terms":7,"shadow":7}
    assert tuple(c.capital_rub for c in result.capital_cases)==CAPITAL_GRID_RUB
    assert all(c.execution_status is not None for c in result.capital_cases),result.blockers
    assert all(c.benchmark.status=="NOT_EVALUATED" for c in result.capital_cases)
    assert decisions(result)["OFZ_BENCHMARK_EVALUABLE"]=="UNKNOWN"
    assert decisions(result)["SEVEN_CAPITAL_CASES_EVALUATED"]=="YES"
    assert all(f.eligible_witness_uids for f in result.candidate_findings)
    assert result.joint_source_core_bond_ids==(1,2,3)
    assert all(c.execution_summary.shadow_cash_rub==c.capital_rub-c.execution_summary.planned_shadow_invested_rub for c in result.capital_cases)


def test_supplied_genesis_delegates_actual_scale_and_ofz(env,monkeypatch):
    calls=[]; original=module.ShadowScaleExperimentGenesisService.build
    def scale(self,**kwargs):calls.append(kwargs);return original(self,**kwargs)
    monkeypatch.setattr(module.ShadowScaleExperimentGenesisService,"build",scale)
    result=run(env,request_for(env.prepared,plans=True))
    assert len(calls)==1
    assert all(c.benchmark.status=="READY" for c in result.capital_cases),result.blockers
    assert result.status=="READY",result.blockers
    assert decisions(result)["FORWARD_GENESIS_INPUTS_READY"]=="YES"
    assert decisions(result)["OPERATIONAL_EVIDENCE_SUFFICIENT"]=="UNKNOWN"
    assert result.production_status=="NOT_VERIFIED" and not result.official_day0_ready


@pytest.mark.parametrize("seconds,reason",[(86400,None),(86401,"SOURCE_STALE"),(-1,"SOURCE_OBSERVATION_IN_FUTURE")])
def test_observation_boundary_and_no_clock(env,seconds,reason):
    request=pure.normalize_request(request_for(env.prepared,observed_delta=seconds))
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in env.investment.evaluations)
    rows=pure.source_findings(request,canonical)
    assert all((reason in f.reason_codes) if reason else f.source_eligible for f in rows)


@pytest.mark.parametrize("changes,reason",[
    ({"buy_available":None,"availability_classification":None},"BUY_AVAILABILITY_UNKNOWN"),
    ({"for_qual_investor":True},"QUALIFIED_ONLY_RESTRICTED"),
    ({"for_qual_investor":None},"QUALIFICATION_UNKNOWN"),
    ({"required_tests":None,"required_tests_state":"NOT_SUPPLIED"},"REQUIRED_TESTS_NOT_PROVEN"),
    ({"required_tests":("BOND_TEST",),"required_tests_state":"SOURCE_VALUES"},"REQUIRED_TESTS_NOT_PROVEN"),
    ({"class_code":"OTHER"},"SOURCE_BOARD_NOT_TQCB"),
    ({"source_fields":{"is_traded":False}},"NOT_ACTIVE_OR_NOT_TRADED"),
])
def test_source_restrictions_remain_separate_from_diagnostics(env,changes,reason):
    request=pure.normalize_request(request_for(env.prepared,changes={1:changes}))
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in env.investment.evaluations)
    finding=next(f for f in pure.source_findings(request,canonical) if f.bond_id==1)
    assert reason in finding.reason_codes and not finding.source_eligible


def test_account_evidence_required_tests_freshness_and_tampering(env):
    request=request_for(env.prepared,changes={1:{"required_tests":("BOND_TEST",),"required_tests_state":"SOURCE_VALUES"}})
    proof=AccountEvidence(uid="UID-1",observed_at=request.evaluation_at,permitted=True,passed_tests=("BOND_TEST",),evidence_sha256="0"*64)
    proof=proof.model_copy(update={"evidence_sha256":pure.account_fingerprint(proof)})
    request=request.model_copy(update={"source_context":request.source_context.model_copy(update={"accounts":(proof,)})})
    result=run(env,request)
    assert next(f for f in result.source_findings if f.uid=="UID-1").source_eligible
    bad=request.model_copy(update={"source_context":request.source_context.model_copy(update={"accounts":(proof.model_copy(update={"permitted":False}),)})})
    assert run(env,bad).blockers==("ACCOUNT_HASH_MISMATCH",)


def test_missing_evidence_identity_conflict_and_stable_blocked_contract(env):
    missing=run(env,env.request.model_copy(update={"source_context":None}))
    assert missing.status=="INPUTS_MISSING" and decisions(missing)["CURRENT_UNIVERSE_BOUND"]=="UNKNOWN"
    assert next(c for c in missing.input_counts if c.name=="full_source_uids").count is None
    assert missing.funnel_stages[0].evaluation_status=="NOT_EVALUATED"
    assert len(missing.capital_cases)==7
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin="WRONG" if e.bond_id==1 else e.isin,secid=e.secid) for e in env.investment.evaluations)
    request=pure.normalize_request(env.request)
    rows=pure.source_findings(request,canonical)
    assert rows[0].canonical_identity_status=="SOURCE_FACT_CONFLICT"
    tampered=request.model_copy(update={"source_context":request.source_context.model_copy(update={"expected_artifact_sha256":"0"*64})})
    before=len(env.calls); blocked=run(env,tampered)
    assert blocked.blockers==("SOURCE_HASH_MISMATCH",) and len(env.calls)==before
    assert CurrentForwardReadinessReport.model_validate_json(blocked.model_dump_json())==blocked


def test_empty_core_and_independent_family_no_intersection(env):
    composites=[incomplete(composite(1),"market_fresh"),incomplete(composite(2),"liquidity_score_ready"),incomplete(composite(3),"modified_duration_ready")]
    batch=UnifiedCandidateReducer.build(snapshot(composites))
    investment=evaluate(batch)
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in batch.exclusions)
    result=pure.build_report(pure.normalize_request(env.request),canonical,investment,None,
        tuple(pure.CaseWork(c) for c in CAPITAL_GRID_RUB))
    assert result.joint_source_core_bond_ids==()
    assert "INDIVIDUAL_FAMILIES_AVAILABLE_BUT_NO_JOINT_READY_CANDIDATES" in result.blockers
    assert all(c.count>0 for c in result.independent_family_counts)


def test_missing_verified_issuer_and_risk_policy_rejection(env,monkeypatch):
    investment=missing_issuer(env.investment)
    monkeypatch.setattr(module.InvestmentModelSnapshotService,"build",lambda *a,**k:investment)
    result=run(env)
    f=result.candidate_findings[0]
    assert f.investment_status=="READY" and f.risk_status=="BLOCKED"
    assert "ISSUER_IDENTITY_UNAVAILABLE" in f.reason_codes


def test_zero_lots_partial_cases_and_post_rounding_risk(env,monkeypatch):
    original=module.ShadowExecutionTermsLoader.build
    from app.services.shadow_execution_planner import make_terms,make_terms_batch
    from test_shadow_execution_terms_loader import row
    def giant(self,strategy):
        return make_terms_batch(strategy,tuple(make_terms(p,row(p,lot_size=100)) for p in strategy.positions))
    monkeypatch.setattr(module.ShadowExecutionTermsLoader,"build",giant)
    result=run(env)
    assert result.capital_cases[0].status=="UNEXECUTABLE"
    assert result.capital_cases[0].execution_summary.planned_position_count==0
    assert result.capital_cases[0].execution_summary.shadow_cash_rub==D(50000)
    assert any(c.execution_summary.planned_position_count>0 for c in result.capital_cases[1:])
    assert all(p.seed_below_one_lot for p in result.capital_cases[0].positions)
    assert decisions(result)["SHADOW_50K_POST_RISK_PASS"]=="NO"
    monkeypatch.setattr(module.ShadowExecutionTermsLoader,"build",original)


def test_post_rounding_risk_is_preserved_without_repair(env):
    from test_shadow_execution_terms_loader import strategy,terms
    from test_shadow_ledger_genesis import unique_identities
    from app.services.shadow_execution_planner import ShadowExecutionPlanner
    strategy=unique_identities(strategy(overrides={1:{"modified_duration_years":D(5)}},capital="50000"))
    execution=ShadowExecutionPlanner.build(strategy,terms(strategy,{2:{"lot_size":100},3:{"lot_size":100}}))
    investment=strategy.source_investment_batch
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in investment.evaluations)
    result=pure.build_report(pure.normalize_request(env.request),canonical,investment,strategy.source_risk_batch,
        (pure.CaseWork(CAPITAL_GRID_RUB[0],strategy,execution),)+tuple(pure.CaseWork(c) for c in CAPITAL_GRID_RUB[1:]))
    real=result.capital_cases[0]
    assert real.post_rounding_risk_status=="BLOCKED"
    assert "PORTFOLIO_DURATION_LIMIT_EXCEEDED" in real.blockers
    assert next(p for p in real.positions if p.bond_id==1).planned_lots>0


def test_uid_facts_cannot_be_combined_and_lot_conflict_remains(env):
    request=request_for(env.prepared,changes={1:{"required_tests":("TEST",),"required_tests_state":"SOURCE_VALUES"}})
    artifact=request.source_context.frozen_admission
    extra=artifact.source_bonds[0].model_copy(update={"uid":"UID-ALT","for_qual_investor":True,"required_tests":(),"required_tests_state":"SOURCE_EMPTY"})
    rows=artifact.source_bonds+(extra,)
    bridge=TInvestBondIdentityBridgeService.build(rows,artifact.internal_bonds,())
    frozen=TInvestFrozenAdmissionEvidenceService.build(source_bonds=rows,internal_bonds=artifact.internal_bonds,
        core_m3_complete_bond_ids=(),identity_bridge=bridge,finalized_resolutions=(),captured_at=artifact.captured_at)
    context=request.source_context.model_copy(update={"frozen_admission":frozen,"expected_artifact_sha256":frozen.artifact_sha256,
        "expected_source_bonds_sha256":frozen.source_bonds_sha256})
    request=pure.normalize_request(request.model_copy(update={"source_context":context}))
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in env.investment.evaluations)
    found=[r for r in pure.source_findings(request,canonical) if r.bond_id==1]
    assert len(found)==2 and not any(r.source_eligible for r in found)


def test_genesis_drift_and_benchmark_failures_never_become_ready(env,monkeypatch):
    request=request_for(env.prepared,plans=True)
    first=request.reviewed_genesis[0]
    wrong=first.model_copy(update={"plan":first.plan.model_copy(update={"source_code_sha":"b"*40})})
    drift=run(env,request.model_copy(update={"reviewed_genesis":(wrong,)+request.reviewed_genesis[1:]}))
    assert "REVIEWED_GENESIS_EVIDENCE_CONFLICT" in drift.blockers
    from app.services.ofz_total_return_benchmark_service import OfzTotalReturnBenchmarkService
    original=OfzTotalReturnBenchmarkService.build_genesis
    def unavailable(self,**kwargs):
        value=original(self,**kwargs)
        return value.model_copy(update={"status":"BLOCKED","blockers":("BENCHMARK_MODIFIED_DURATION_UNAVAILABLE",)})
    monkeypatch.setattr(OfzTotalReturnBenchmarkService,"build_genesis",unavailable)
    blocked=run(env,request)
    assert blocked.status!="READY" and any(c.benchmark.status=="BLOCKED" for c in blocked.capital_cases)


def test_single_case_failure_retains_selection_and_all_capitals(env,monkeypatch):
    original=module.ShadowExecutionTermsLoader.build
    def fail_one(self,strategy):
        if strategy.request.capital_rub==D(50000): raise ValueError("private path must not escape")
        return original(self,strategy)
    monkeypatch.setattr(module.ShadowExecutionTermsLoader,"build",fail_one)
    result=run(env)
    first=result.capital_cases[0]
    assert first.selected_bond_ids and first.strategy_targets and first.execution_status is None
    assert all(c.execution_status is not None for c in result.capital_cases[1:])
    stage=next(s for s in result.funnel_stages if s.name=="SHADOW_TERMS_VERIFIED" and s.capital_rub==D(50000))
    assert stage.unknown_count==len(first.selected_bond_ids)
    assert "private path" not in result.model_dump_json()


def test_select_only_pending_state_and_separate_identity_map_same_connection(env):
    statements=[]
    engine=env.prepared.env.engine
    def capture(c,u,sql,p,x,m): statements.append(sql)
    with readonly(engine) as db:
        loaded=db.get(Bond,1); deleted=db.get(Bond,2)
        original=loaded.isin; loaded.isin="caller-unflushed"
        pending=Company(name="Pending Task310",ticker="PENDING310"); db.add(pending)
        db.delete(deleted)
        before=(set(db.new),set(db.dirty),set(db.deleted))
        event.listen(engine,"before_cursor_execute",capture)
        try: result=module.CurrentForwardReadinessService(db).build(request=env.request)
        finally:event.remove(engine,"before_cursor_execute",capture)
        assert result.candidate_findings and result.candidate_findings[0].isin==original,result.blockers
        assert (set(db.new),set(db.dirty),set(db.deleted))==before and loaded.isin=="caller-unflushed"
        assert db.in_transaction() and db.connection().connection.driver_connection.in_transaction
        assert env.calls[-1][0] is not db and env.calls[-1][0].bind is db.connection()
        assert statements and all(q.lstrip().upper().startswith(("SELECT","PRAGMA QUERY_ONLY")) for q in statements)
        with pytest.raises(Exception): db.connection().exec_driver_sql("DELETE FROM companies")


def test_refuses_unprotected_transaction_and_bad_original_types(env):
    with env.prepared.env.factory() as db:
        result=module.CurrentForwardReadinessService(db).build(request=env.request)
        assert result.blockers==("READ_ONLY_SNAPSHOT_REQUIRED",)
    for updates in ({"bond_ids":(True,)},{"source_max_age_seconds":True},{"pit_ready":True}):
        result=run(env,env.request.model_copy(update=updates))
        assert result.status=="BLOCKED" and len(result.capital_cases)==7


def test_determinism_immutability_json_context_and_all_funnel_partitions(env):
    before=deepcopy(env.request.model_dump())
    one=run(env)
    with localcontext() as ctx:
        ctx.prec=8;ctx.rounding=ROUND_UP
        two=run(env,env.request.model_copy(update={"bond_ids":tuple(reversed(env.request.bond_ids))}))
        assert ctx.prec==8 and ctx.rounding==ROUND_UP
    assert two==one
    assert env.request.model_dump()==before
    assert CurrentForwardReadinessReport.model_validate_json(one.model_dump_json())==one
    for stage in one.funnel_stages:
        assert stage.input_count==stage.passing_count+stage.excluded_count+stage.unknown_count
    with pytest.raises(ValueError):one.status="READY"
    with pytest.raises(ValueError):CurrentForwardReadinessReport(**one.model_dump(),extra=True)
    changed=run(env,env.request.model_copy(update={"evaluation_at":env.request.evaluation_at+timedelta(seconds=1)}))
    assert changed.report_sha256!=one.report_sha256


def runner_module():
    spec=importlib.util.spec_from_file_location("task310_runner",ROOT/"scripts/current_forward_readiness.py")
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_runner_owned_transaction_rollback_and_explicit_connection(env,monkeypatch):
    runner=runner_module(); trace=[]
    from sqlalchemy import create_engine
    engine=create_engine("sqlite://")
    event.listen(engine,"commit",lambda c:trace.append("commit"))
    event.listen(engine,"rollback",lambda c:trace.append("rollback"))
    monkeypatch.setattr(runner,"create_engine",lambda *a,**k:engine)
    def build(self,**kwargs):
        assert module.readonly_connection(self.db) is not None
        return pure.blocked("SYNTHETIC_NO_DATA")
    monkeypatch.setattr(runner.CurrentForwardReadinessService,"build",build)
    result=runner.evaluate(env.request,"sqlite://")
    assert result.blockers==("SYNTHETIC_NO_DATA",) and trace==["rollback"]


def test_runner_duplicate_json_drift_and_output_conflict(env,tmp_path,monkeypatch,capsys):
    runner=runner_module(); path=tmp_path/"request.json"
    path.write_text('{"key":1,"key":2}')
    with pytest.raises(ValueError):runner.read_input(path)
    output=tmp_path/"report.json";output.write_text("other artifact")
    with pytest.raises(ValueError):runner.publish(pure.blocked("TEST"),output)
    assert output.read_text()=="other artifact" and not list(tmp_path.glob(".task310-*"))
    path.write_text(env.request.model_dump_json())
    args=["--mode","REPORT_CURRENT_FORWARD_READINESS","--input",str(path),"--database-url","sqlite://",
        "--expected-source-sha256",pure.fingerprint(env.request.source_context),"--expected-code-sha","a"*40]
    def drift(*args):
        path.write_text("changed")
        return pure.blocked("TEST")
    monkeypatch.setattr(runner,"evaluate",drift)
    assert runner.main(args)==2
    assert json.loads(capsys.readouterr().out)["blockers"]==["RUNNER_INPUT_CONNECTION_OR_OUTPUT_FAILURE"]
    assert runner.main(["--secret-option","DO_NOT_ECHO_CREDENTIAL"] )==2
    assert "DO_NOT_ECHO_CREDENTIAL" not in capsys.readouterr().out


def test_runner_offline_hashes_output_limits_drift_and_sanitized_errors(env,tmp_path,monkeypatch,capsys):
    runner=runner_module(); request=tmp_path/"request.json";request.write_text(env.request.model_dump_json(),encoding="utf-8")
    args=["--mode","REPORT_CURRENT_FORWARD_READINESS","--input",str(request),"--database-url","sqlite://",
        "--expected-source-sha256",pure.fingerprint(env.request.source_context),"--expected-code-sha","a"*40]
    result=run(env);monkeypatch.setattr(runner,"evaluate",lambda *a:result)
    monkeypatch.chdir(tmp_path)
    assert runner.main(args)==2
    assert json.loads(capsys.readouterr().out)["report_sha256"]==result.report_sha256
    output=tmp_path/"report.json"
    assert runner.main(args+["--output",str(output)])==2 and output.exists()
    first=output.read_bytes();runner.main(args+["--output",str(output)]);assert output.read_bytes()==first
    assert not list(tmp_path.glob(".task310-*"))
    monkeypatch.setattr(runner,"evaluate",lambda *a:(_ for _ in ()).throw(RuntimeError("SECRET postgresql://credential@host/path")))
    runner.main(args);text=capsys.readouterr().out
    assert "SECRET" not in text and "credential" not in text
    monkeypatch.setattr(runner,"LIMIT",10)
    assert runner.main(args)==2 and output.read_bytes()==first


def test_static_isolation_and_no_genesis_plan_call():
    paths=[ROOT/"backend/app/services/current_forward_readiness_service.py",ROOT/"backend/app/services/current_forward_readiness_report.py",ROOT/"scripts/current_forward_readiness.py"]
    for path in paths:
        tree=ast.parse(path.read_text(encoding="utf-8"))
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls & {"apply","sync","commit","add_all","merge","delete","refresh","plan"}
        if path.name!="current_forward_readiness.py": assert "flush" not in calls
        imports=" ".join(n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom))
        assert not any(name in imports for name in ("iss_client","instrument_universe_client","controlled_bond_import_service","execution_terms_evidence_bridge_service","shadow_scale_activation_service"))


def test_output_envelope_and_false_flag_types_without_dependencies():
    result=pure.blocked("SYNTHETIC")
    for updates in ({"capital_cases":result.capital_cases[:-1]}, {"readiness_decisions":result.readiness_decisions[:-1]},
                    {"db_mutation":0}, {"official_day0_ready":True}, {"pit_ready":0}):
        with pytest.raises(ValueError):
            CurrentForwardReadinessReport.model_validate({**result.model_dump(),**updates})


@pytest.mark.parametrize("changes,unknown,excluded",[
    ({"buy_available":None,"availability_classification":None},1,0),
    ({"for_qual_investor":True},0,1),
])
def test_missing_source_fact_is_unknown_not_policy_rejection(env,changes,unknown,excluded):
    request=pure.normalize_request(request_for(env.prepared,changes={1:changes}))
    canonical=tuple(BondIdentityProjection(bond_id=e.bond_id,isin=e.isin,secid=e.secid) for e in env.investment.evaluations)
    result=pure.build_report(request,canonical,env.investment,None,tuple(pure.CaseWork(c) for c in CAPITAL_GRID_RUB))
    stage=next(s for s in result.funnel_stages if s.name=="SOURCE_ELIGIBLE_CORE_INTERSECTION")
    assert (stage.unknown_count,stage.excluded_count)==(unknown,excluded)
