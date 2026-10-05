"""Isolated SQLite activation, no production sessions or source requests."""
from copy import deepcopy
from decimal import localcontext, ROUND_DOWN
from types import SimpleNamespace
import ast
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event, select, func, text
from sqlalchemy.orm import sessionmaker
from app.schemas.shadow_scale_activation import (
    ShadowScaleActivationRequestV1 as Request, ShadowScaleActivationAuthorizationV1 as Authorization,
    ShadowScaleActivationPlanV1 as Plan, ShadowScaleActivationReceiptV1 as Receipt,
)
from app.services import shadow_scale_activation_service as activation
from app.services import shadow_scale_persistence_repository as store
from app.services import shadow_ledger_repository as ledger
from app.models.shadow_test_run import ShadowTestRun
from app.models.company import Company
from test_shadow_scale_experiment_genesis import prepared


@pytest.fixture
def activation_env(prepared,tmp_path):
    engine=create_engine("sqlite:///"+(tmp_path/"activation.sqlite").as_posix())
    origin=prepared.env.engine.raw_connection();destination=engine.raw_connection()
    origin.driver_connection.backup(destination.driver_connection)
    origin.close();destination.close()
    factory=sessionmaker(bind=engine)
    request=Request(scale_genesis=prepared.matrix)
    yield SimpleNamespace(engine=engine,factory=factory,request=request,service=activation.ShadowScaleActivationService(factory))
    engine.dispose()


def authorize(plan):
    return Authorization(explicit_apply=True,**activation.authorization_fields(plan))


def counts(env):
    with env.factory() as db:
        return tuple(db.scalar(select(func.count()).select_from(m)) for m in (*store.TABLES,*ledger.TABLES))


def test_plan_deterministic_select_only_types_and_owned_sessions(activation_env):
    e=activation_env;before=deepcopy(e.request.model_dump());sql=[]
    event.listen(e.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    with localcontext() as ctx:
        ctx.prec=7;ctx.rounding=ROUND_DOWN
        a=e.service.plan(request=e.request);b=e.service.plan(request=e.request)
        assert ctx.prec==7 and ctx.rounding==ROUND_DOWN
    assert a.status=="EXECUTABLE",a.blockers
    assert a==b and all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert len(a.cases)==7 and len({c.child_run_key_sha256 for c in a.cases})==7
    assert e.request.model_dump()==before
    assert Plan.model_validate_json(a.model_dump_json())==a
    assert counts(e)==(0,)*8
    with pytest.raises(ValueError):a.status="BLOCKED"
    with pytest.raises(ValueError):Request(scale_genesis=e.request.scale_genesis,extra=True)
    with e.factory() as db:
        pending=Company(name="Pending",ticker="TASK305_PENDING");db.add(pending)
        rejected=activation.ShadowScaleActivationService(lambda:db).plan(request=e.request)
        assert rejected.blockers==("SESSION_NOT_FRESH",) and pending in db.new


def test_apply_one_commit_seven_cases_and_fresh_noop(activation_env):
    e=activation_env;plan=e.service.plan(request=e.request);commits=[]
    from app.models.bond import Bond
    from app.models.bond_market_snapshot import BondMarketSnapshot
    from app.models.bond_security_master_profile import BondSecurityMasterProfile
    models=(Company,Bond,BondMarketSnapshot,BondSecurityMasterProfile)
    with e.factory() as db:
        source_before=tuple(tuple(db.execute(select(m.__table__).order_by(m.id))) for m in models)
    event.listen(e.engine,"commit",lambda c:commits.append(1))
    result=e.service.apply(request=e.request,reviewed_plan=plan,authorization=authorize(plan))
    assert result.status=="APPLIED",result.blockers
    assert commits==[1] and result.commit_count==1 and result.committed_child_run_count==7
    with e.factory() as db:
        assert tuple(tuple(db.execute(select(m.__table__).order_by(m.id))) for m in models)==source_before
    assert result.pre_commit_audit.status==result.post_commit_audit.status=="VERIFIED"
    assert counts(e)[:4]==(1,7,7,result.committed_component_count)
    stale=e.service.apply(request=e.request,reviewed_plan=plan,authorization=authorize(plan))
    assert stale.status=="BLOCKED" and stale.blockers==("CURRENT_SCALE_STATE_DRIFT",)
    fresh=e.service.plan(request=e.request)
    assert fresh.status=="IDEMPOTENT_NOOP",fresh.blockers
    assert fresh.plan_sha256!=plan.plan_sha256
    before=counts(e)
    noop=e.service.apply(request=e.request,reviewed_plan=fresh,authorization=authorize(fresh))
    assert noop.status=="IDEMPOTENT_NOOP" and noop.commit_count==0
    assert noop.committed_mutation_count==0 and counts(e)==before and commits==[1]
    assert noop.child_run_ids==result.child_run_ids
    with e.factory() as db:
        for case,rid in zip(e.request.scale_genesis.cases,result.child_run_ids):
            run=db.get(ShadowTestRun,rid)
            assert run.genesis_plan_sha256==case.reviewed_strategy_genesis.plan_sha256


@pytest.mark.parametrize("kind",["sha","auth","matrix","nonready"])
def test_tampering_before_mutation(activation_env,kind):
    e=activation_env;p=e.service.plan(request=e.request);a=authorize(p);r=e.request
    if kind=="sha":p=p.model_copy(update={"plan_sha256":"f"*64})
    elif kind=="auth":a=a.model_copy(update={"current_db_state_sha256":"f"*64})
    elif kind=="matrix":r=r.model_copy(update={"scale_genesis":r.scale_genesis.model_copy(update={"scale_genesis_sha256":"f"*64})})
    else:r=r.model_copy(update={"scale_genesis":r.scale_genesis.model_copy(update={"status":"BLOCKED"})})
    assert e.service.apply(request=r,reviewed_plan=p,authorization=a).status=="BLOCKED"
    assert counts(e)==(0,)*8


@pytest.mark.parametrize("failure",["second_child","pre_audit","lock","commit","post_audit"])
def test_atomic_failure_and_ambiguous_outcomes(activation_env,monkeypatch,failure):
    e=activation_env;p=e.service.plan(request=e.request)
    if failure=="second_child":
        original=ledger.persist;calls=[]
        def persist(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError("synthetic")
            return original(*args,**kwargs)
        monkeypatch.setattr(ledger,"persist",persist)
    elif failure=="lock":
        monkeypatch.setattr(activation,"acquire_locks",lambda *a: (_ for _ in ()).throw(ValueError("LOCK_OR_TRANSACTION_ERROR")))
    elif failure in ("pre_audit","post_audit"):
        original=store.audit_group;calls=[]
        def audit(*args):
            calls.append(1)
            if len(calls)==(1 if failure=="pre_audit" else 2):return store.Audit(status="FAILED")
            return original(*args)
        monkeypatch.setattr(store,"audit_group",audit)
    else:
        from sqlalchemy.orm import Session
        monkeypatch.setattr(Session,"commit",lambda self: (_ for _ in ()).throw(RuntimeError("uncertain")))
    result=e.service.apply(request=e.request,reviewed_plan=p,authorization=authorize(p))
    expected={"second_child":"ROLLED_BACK","pre_audit":"ROLLED_BACK","lock":"BLOCKED",
        "commit":"COMMIT_OUTCOME_UNKNOWN","post_audit":"POST_COMMIT_AUDIT_FAILED"}[failure]
    assert result.status==expected,result
    assert Receipt.model_validate_json(result.model_dump_json())==result
    if failure=="post_audit":assert counts(e)[:3]==(1,7,7) and result.committed_group_count==1
    elif failure=="commit":assert result.committed_mutation_count is None and result.commit_count is None
    else:assert counts(e)==(0,)*8 and result.committed_mutation_count==0 and result.rollback_confirmed


def test_orphan_partial_and_historical_drift(activation_env):
    e=activation_env;p=e.service.plan(request=e.request)
    with e.factory() as db:
        child=activation.build_genesis_plan_in_session(db,request=activation.child_request(e.request.scale_genesis,e.request.scale_genesis.cases[0]))
        ledger.persist(db,child,activation.genesis_fields(child,activation.child_request(e.request.scale_genesis,e.request.scale_genesis.cases[0])))
        db.commit()
    assert "ORPHAN_CHILD_RUN_CONFLICT" in e.service.plan(request=e.request).blockers
    # Never adopt existing standalone children.
    assert counts(e)[:4]==(0,0,0,0)


def test_no_network_sync_or_standalone_apply():
    root=Path(__file__).resolve().parents[1]/"app"/"services"
    for name in ("shadow_scale_activation_service.py","shadow_scale_persistence_repository.py"):
        tree=ast.parse((root/name).read_text())
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(any(x in i for x in ("httpx","requests","moex","tinvest","paper_")) for i in imports)
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in
            ("sync","execute_apply","build_curve","build_daily") for n in ast.walk(tree))


def test_postgres_lock_order_and_unsupported_dialect():
    from types import SimpleNamespace
    class Fake:
        def __init__(self,dialect,ok=True):self.dialect=dialect;self.sql=[];self.ok=ok
        def get_bind(self):return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))
        def begin(self):self.sql.append('BEGIN')
        def execute(self,statement,parameters=None):
            self.sql.append(str(statement));return SimpleNamespace(scalar_one=lambda:self.ok)
    db=Fake('postgresql');activation.acquire_locks(db,'f'*64)
    assert db.sql[1]=='SELECT pg_try_advisory_xact_lock(:key)'
    tables=tuple(m.__tablename__ for m in (*store.TABLES,*ledger.TABLES))
    assert db.sql[2:10]==[f'LOCK TABLE {t} IN SHARE ROW EXCLUSIVE MODE NOWAIT' for t in tables]
    assert db.sql[10:]==[f'LOCK TABLE {t} IN SHARE MODE NOWAIT' for t in
        ('bonds','bond_market_snapshots','bond_security_master_profiles')]
    with pytest.raises(ValueError,match='LOCK_OR_TRANSACTION_ERROR'):activation.acquire_locks(Fake('postgresql',False),'a'*64)
    with pytest.raises(ValueError,match='APPLY_DIALECT_UNSUPPORTED'):activation.acquire_locks(Fake('unknown'),'a'*64)


@pytest.mark.parametrize('kind',['bond','market','profile'])
def test_source_identity_drift_cannot_create_activation(activation_env,kind):
    from app.models.bond import Bond
    from app.models.bond_market_snapshot import BondMarketSnapshot
    from app.models.bond_security_master_profile import BondSecurityMasterProfile
    e=activation_env;source=e.request.scale_genesis.cases[0].source_shadow_execution.positions[0]
    with e.factory() as db:
        if kind=='bond':db.get(Bond,source.bond_id).secid='CONTRADICTORY'
        elif kind=='market':db.get(BondMarketSnapshot,source.market_snapshot_id).source='synthetic'
        else:db.delete(db.get(BondSecurityMasterProfile,source.security_master_profile_id))
        db.commit()
    result=e.service.plan(request=e.request)
    assert result.status=='BLOCKED' and result.blockers==('SOURCE_REFERENCE_MISSING',)
    assert counts(e)==(0,)*8


def test_unconfirmed_rollback_is_not_reported_as_rolled_back(activation_env,monkeypatch):
    from sqlalchemy.orm import Session
    e=activation_env;p=e.service.plan(request=e.request)
    def fail(db,*args):
        db.add(store.Group(**store.group_fields(e.request.scale_genesis,p.group_key_sha256)));db.flush()
        raise RuntimeError('synthetic')
    monkeypatch.setattr(activation,'persist_group',fail)
    monkeypatch.setattr(Session,'rollback',lambda self: (_ for _ in ()).throw(RuntimeError('synthetic')))
    result=e.service.apply(request=e.request,reviewed_plan=p,authorization=authorize(p))
    assert result.status=='BLOCKED' and result.blockers==('ROLLBACK_FAILED',)
    assert result.rollback_confirmed is False and result.committed_mutation_count is None
    assert Receipt.model_validate_json(result.model_dump_json())==result


@pytest.mark.parametrize('bad',[None,{},True,'request'])
def test_wrong_request_types_before_factory(bad):
    calls=[]
    service=activation.ShadowScaleActivationService(lambda:calls.append(1))
    result=service.plan(request=bad)
    assert result.status=='BLOCKED' and result.blockers==('INPUT_INVALID',) and not calls


def test_validated_snapshot_has_no_caller_owned_model_aliases(activation_env):
    e=activation_env;source=e.request.scale_genesis
    snapshot=activation.validate_request(e.request)
    assert snapshot==source and snapshot is not source
    assert snapshot.cases[0].source_shadow_execution is not source.cases[0].source_shadow_execution
    leaf=source.cases[0].experiment_genesis.benchmark.curve.nodes[0].component_bond_ids
    old=list(leaf);before=snapshot.model_dump()
    try:
        leaf.append(999999)
        assert snapshot.model_dump()==before
    finally:leaf[:]=old
