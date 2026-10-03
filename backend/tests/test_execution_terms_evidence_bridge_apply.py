"""Isolated authorized transaction, rollback, drift, lineage and unchanged Shadow gates."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.bond import Bond
from app.models.bond_security_master_profile import BondSecurityMasterProfile as Profile
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence as Evidence
from app.services.bond_security_master_service import BondSecurityMasterService as Master
from app.services.shadow_execution_terms_loader import ShadowExecutionTermsLoader
from app.services.execution_terms_evidence_bridge_service import ExecutionTermsEvidenceBridgeService as Service
from test_execution_terms_evidence_bridge import setup, plan, auth, apply, NOW, sources


def test_atomic_apply_lineage_commit_and_new_plan_noop(db_session):
    f,pf,bid,factory,service=setup(db_session,(10,10))
    p=plan(service,f,pf,[bid]); commits=[]
    class Counted(Session):
        def commit(self): commits.append(True); return super().commit()
    service=Service(sessionmaker(bind=db_session.bind,class_=Counted))
    result=apply(service,f,pf,p)
    assert result.status=="APPLIED" and result.evidence_created==result.committed_evidence_created==3 and commits==[True]
    assert result.profiles_verified==result.execution_terms_ready_count==1
    assert result.pre_commit_audit.status==result.post_commit_audit.status=="VERIFIED"
    with factory() as db:
        profile=db.scalar(select(Profile).where(Profile.bond_id==bid))
        assert profile.lot_size_state==profile.trading_board_state=="verified" and profile.lot_size==10 and profile.trading_board=="TQCB"
        rows=db.scalars(select(Evidence).where(Evidence.source=="tinvest_universe")).all()
        assert {r.source_key for r in rows}=={"UID-0","UID-1"} and all(r.observed_at==NOW.replace(tzinfo=None) for r in rows)
        assert all(len(r.raw_value_json)==4 and r.source_table==f.source_bonds[0].contract_version for r in rows)
    stale=apply(service,f,pf,p); assert stale.status=="BLOCKED" and stale.blockers==("CURRENT_DB_DRIFT",)
    again=plan(service,f,pf,[bid]); assert again.already_satisfied_count==1
    noop=apply(service,f,pf,again)
    assert noop.status=="IDEMPOTENT_NOOP" and noop.committed_evidence_created==0 and noop.commit_count==0 and commits==[True]
    assert noop.pre_state_sha256==noop.post_state_sha256==result.post_state_sha256


@pytest.mark.parametrize("field",["plan_sha256","target_bond_id_set_sha256","frozen_admission_sha256","import_preflight_sha256","current_db_state_sha256","explicit_apply"])
def test_authorization_tampering_no_mutation(db_session,field):
    f,pf,bid,_,service=setup(db_session); p=plan(service,f,pf,[bid]); a=auth(p)
    changed=a.model_copy(update={field:False if field=="explicit_apply" else "0"*64})
    result=apply(service,f,pf,p,changed)
    assert result.status=="BLOCKED" and result.committed_evidence_created==0


@pytest.mark.parametrize("change",["lot","board","identity","evidence","profile"])
def test_current_db_drift(db_session,change):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid])
    with factory() as db:
        profile=db.scalar(select(Profile).where(Profile.bond_id==bid)); bond=db.get(Bond,bid)
        if change=="identity": bond.secid="WRONG"
        elif change=="profile": profile.contract_version="OTHER"
        elif change=="evidence": Master(db).record_assertion(bond=bond,field_name="lot_size",source="moex_description",assertion_type="scalar_value",normalized_value=1,observed_at=NOW,source_key=bond.secid)
        elif change=="lot": profile.lot_size_state="verified"; profile.lot_size=1
        else: profile.trading_board_state="verified"; profile.trading_board="OTHER"
        db.commit()
    result=apply(service,f,pf,p); assert result.status=="BLOCKED" and result.blockers==("CURRENT_DB_DRIFT",)


@pytest.mark.parametrize("field,value",[("lot_size",99),("trading_board","OTHER")])
def test_current_evidence_conflicts_fail_closed(db_session,field,value):
    f,pf,bid,factory,service=setup(db_session)
    with factory() as db:
        master=Master(db); bond=db.get(Bond,bid)
        master.record_assertion(bond=bond,field_name=field,source="moex_description",assertion_type="scalar_value",normalized_value=value,observed_at=NOW,source_key=bond.secid)
        master.resolve_profile(bond); db.commit()
    p=plan(service,f,pf,[bid]); assert p.status=="BLOCKED"
    assert ("CURRENT_LOT_CONFLICT" if field=="lot_size" else "CURRENT_BOARD_CONFLICT") in p.blockers


def test_failure_after_insert_rolls_back_all(db_session,monkeypatch):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid]); calls=[]
    original=Master.resolve_profile
    def fail(self,bond): calls.append(bond.id); original(self,bond); raise RuntimeError("SECRET database details")
    monkeypatch.setattr(Master,"resolve_profile",fail)
    result=apply(service,f,pf,p)
    assert result.status=="ROLLED_BACK" and result.rollback_confirmed and result.evidence_created==2
    assert result.committed_evidence_created==0 and result.db_mutated is False and "SECRET" not in result.model_dump_json()
    with factory() as db:
        assert not db.scalars(select(Evidence).where(Evidence.source=="tinvest_universe")).all()
        assert db.scalar(select(Profile).where(Profile.bond_id==bid)).lot_size_state=="unknown"


def test_nonfresh_session_not_owned_and_lock_refusal(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid])
    db_session.execute(text("SELECT 1"))
    bad=Service(lambda:db_session)
    assert apply(bad,f,pf,p).blockers==("SESSION_NOT_FRESH",) and db_session.in_transaction()
    db_session.rollback()
    class Locked(Session):
        def execute(self,statement,*a,**kw):
            if str(statement)=="BEGIN IMMEDIATE": raise RuntimeError("Lock unavailable secret")
            return super().execute(statement,*a,**kw)
    blocked=apply(Service(sessionmaker(bind=db_session.bind,class_=Locked)),f,pf,p)
    assert blocked.status=="BLOCKED" and blocked.blockers==("LOCK_OR_TRANSACTION_ERROR",)


def test_commit_and_post_commit_audit_failures_not_rollback_claim(db_session,monkeypatch):
    f,pf,bid,_,service=setup(db_session); p=plan(service,f,pf,[bid])
    class Uncertain(Session):
        def commit(self): super().commit(); raise RuntimeError("Commit acknowledgement lost")
    result=apply(Service(sessionmaker(bind=db_session.bind,class_=Uncertain)),f,pf,p)
    assert result.status=="COMMIT_OUTCOME_UNKNOWN" and result.committed_evidence_created is None and result.db_mutated is None
    # A new freeze/plan can inspect the committed state without claiming historic mutation counts.
    new=plan(service,f,pf,[bid]); assert new.already_satisfied_count==1


def test_matching_profile_without_lineage_requires_apply(db_session):
    f,pf,bid,factory,service=setup(db_session)
    with factory() as db:
        master=Master(db); b=db.get(Bond,bid)
        for field,value in (("lot_size",1),("trading_board","TQCB")):
            master.record_assertion(bond=b,field_name=field,source="moex_description",assertion_type="scalar_value",normalized_value=value,observed_at=NOW,source_key=b.secid)
        master.resolve_profile(b); db.commit()
    p=plan(service,f,pf,[bid]); assert p.ready_count==1 and p.already_satisfied_count==0
    assert apply(service,f,pf,p).status=="APPLIED"


def test_task302_loader_ready_after_bridge_without_task302_changes(db_session):
    from test_shadow_execution_terms_loader import strategy
    f,pf,bid,factory,service=setup(db_session)
    p=plan(service,f,pf,[bid]); assert apply(service,f,pf,p).status=="APPLIED"
    db_session.expire_all()
    profile=db_session.scalar(select(Profile).where(Profile.bond_id==bid))
    s=strategy(3,{1:{"security_master_profile_id":profile.id}})
    result=ShadowExecutionTermsLoader(db_session).build(s)
    assert result.ready_count==1 and result.terms[0].lot_size==1 and result.terms[0].trading_board=="TQCB"


def batch_setup(db):
    from test_tinvest_frozen_admission_evidence import inputs
    from test_tinvest_bond_import_preflight import description, _run_preflight
    from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService as Frozen
    from app.services.tinvest_bond_identity_bridge_service import TInvestBondIdentityBridgeService as Identity
    from app.models.company import Company
    values=inputs()
    rows=[s.model_copy(update={"lot":2}) for s in values["source_bonds"]]
    values.update(source_bonds=rows,identity_bridge=Identity.build(rows,[],[]))
    f=Frozen.build(**values); m=f.admission_manifest
    pf=_run_preflight(admission_manifest=m,moex_descriptions=[description(c) for c in m.import_candidate_manifest],internal_bonds=[],company_projections=[])
    c=Company(name="Batch issuer",ticker="TASK302B_BATCH"); db.add(c); db.flush(); ids=[]
    for r in pf.candidate_rows:
        b=Bond(name="Synthetic",company_id=c.id,isin=r.isin,secid=r.secid); db.add(b); db.flush(); ids.append(b.id)
        master=Master(db)
        for field,value in (("currency_code","RUB"),("nominal_value",Decimal(1000))):
            master.record_assertion(bond=b,field_name=field,source="moex_description",assertion_type="scalar_value",normalized_value=value,observed_at=NOW,source_key=b.secid)
        master.resolve_profile(b)
    db.commit(); factory=sessionmaker(bind=db.bind)
    return f,pf,tuple(ids),factory,Service(factory)


def test_whole_batch_failure_rolls_back_first_row_and_unrelated_state(db_session,monkeypatch):
    f,pf,ids,factory,service=batch_setup(db_session); p=plan(service,f,pf,list(reversed(ids)))
    assert p.target_bond_ids==tuple(sorted(ids))
    original=Master.resolve_profile; calls=[]
    def fail_second(self,bond):
        calls.append(bond.id); result=original(self,bond)
        if len(calls)==2: raise RuntimeError("Synthetic second-row failure")
        return result
    monkeypatch.setattr(Master,"resolve_profile",fail_second)
    r=apply(service,f,pf,p)
    assert r.status=="ROLLED_BACK" and r.attempted_rows==2 and r.committed_evidence_created==0
    with factory() as db:
        assert not db.scalars(select(Evidence).where(Evidence.source=="tinvest_universe")).all()
        assert all(x.lot_size_state=="unknown" for x in db.scalars(select(Profile)))


def test_successful_batch_assertions_only_and_input_immutability(db_session):
    f,pf,ids,factory,service=batch_setup(db_session)
    before=f.model_dump_json(),pf.model_dump_json(); sql=[]
    def capture(conn,cursor,s,params,context,many): sql.append(s)
    event.listen(db_session.bind,"before_cursor_execute",capture)
    try:
        p=plan(service,f,pf,list(reversed(ids)))
        r=apply(service,f,pf,p)
    finally: event.remove(db_session.bind,"before_cursor_execute",capture)
    assert r.status=="APPLIED" and r.evidence_created==4 and r.profiles_verified==2 and r.commit_count==1
    assert before==(f.model_dump_json(),pf.model_dump_json())
    writes=[s.lower() for s in sql if s.lstrip().upper().startswith(("INSERT","UPDATE","DELETE"))]
    assert writes and all("bond_security_master_evidence" in s or "bond_security_master_profiles" in s for s in writes)
    assert not any(s.lstrip().upper().startswith("DELETE") for s in sql)


def test_post_commit_audit_failure_remains_committed(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid]); calls=[]
    def factory_then_fail():
        calls.append(1)
        if len(calls)>1: raise RuntimeError("Postcommit read unavailable")
        return factory()
    r=apply(Service(factory_then_fail),f,pf,p)
    assert r.status=="POST_COMMIT_AUDIT_FAILED" and r.commit_count==1 and r.committed_evidence_created==2 and r.rollback_confirmed is False
    assert plan(service,f,pf,[bid]).already_satisfied_count==1


def test_source_hash_mismatch_and_plan_hash_tampering(db_session):
    f,pf,bid,_,service=setup(db_session); p=plan(service,f,pf,[bid]); f2,pf2=sources((2,))
    assert apply(service,f2,pf2,p).blockers==("SOURCE_HASH_MISMATCH",)
    changed=p.model_copy(update={"ready_count":99})
    assert apply(service,f,pf,changed,auth(p)).blockers==("PLAN_SHA_MISMATCH",)


def test_verified_lineage_older_than_conflicting_source_blocks(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid]); assert apply(service,f,pf,p).status=="APPLIED"
    with factory() as db:
        b=db.get(Bond,bid)
        Master(db).record_assertion(bond=b,field_name="lot_size",source="tinvest_universe",assertion_type="scalar_value",
            normalized_value=9,observed_at=NOW+timedelta(days=1),source_key="OTHER_UID")
        db.commit()
    p=plan(service,f,pf,[bid]); assert "CURRENT_LOT_CONFLICT" in p.blockers


def test_profile_resolution_mutation_with_reused_evidence_reported_truthfully(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid]); assert apply(service,f,pf,p).status=="APPLIED"
    with factory() as db:
        profile=db.scalar(select(Profile).where(Profile.bond_id==bid))
        profile.lot_size_state="unknown"; profile.lot_size=None; db.commit()
    p=plan(service,f,pf,[bid]); r=apply(service,f,pf,p)
    assert r.status=="APPLIED" and r.evidence_created==0 and r.evidence_reused==2 and r.db_mutated is True


def test_corrupt_persisted_numeric_evidence_not_coerced(db_session):
    f,pf,bid,factory,service=setup(db_session)
    with factory() as db:
        master=Master(db); b=db.get(Bond,bid)
        e,_=master.record_assertion(bond=b,field_name="lot_size",source="moex_description",assertion_type="scalar_value",normalized_value=1,observed_at=NOW,source_key=b.secid)
        db.execute(update(Evidence).where(Evidence.id==e.id).values(normalized_value_json={"value":True})); db.commit()
    assert "SECURITY_MASTER_PROFILE_INVALID" in plan(service,f,pf,[bid]).blockers


def test_nonfresh_pending_state_preserved_without_query(db_session):
    from app.models.company import Company
    f,pf=sources(); pending=Company(name="Caller pending",ticker="TASK302B_PENDING")
    db_session.add(pending); before=set(db_session.new),set(db_session.dirty),set(db_session.deleted)
    p=plan(Service(lambda:db_session),f,pf,[1])
    assert p.blockers==("SESSION_NOT_FRESH",) and pending.id is None
    assert before==(set(db_session.new),set(db_session.dirty),set(db_session.deleted))


@pytest.mark.parametrize("dialect",["unsupported","postgresql"])
def test_apply_dialect_gate_and_postgres_lock_order(db_session,dialect):
    from types import SimpleNamespace
    f,pf,bid,_,service=setup(db_session); p=plan(service,f,pf,[bid]); locks=[]
    class DialectSession(Session):
        gate=True
        def get_bind(self,*args,**kwargs):
            if self.gate and not args and not kwargs:
                self.gate=False; return SimpleNamespace(dialect=SimpleNamespace(name=dialect))
            return super().get_bind(*args,**kwargs)
        def execute(self,statement,*a,**kw):
            if str(statement).startswith("LOCK TABLE"):
                locks.append(str(statement))
                if len(locks)==3: raise RuntimeError("Synthetic lock refusal")
                return None
            return super().execute(statement,*a,**kw)
    r=apply(Service(sessionmaker(bind=db_session.bind,class_=DialectSession)),f,pf,p)
    assert r.status=="BLOCKED" and r.committed_evidence_created==0
    if dialect=="unsupported": assert r.blockers==("APPLY_DIALECT_UNSUPPORTED",) and not locks
    else: assert locks==[f"LOCK TABLE {t} IN SHARE ROW EXCLUSIVE MODE NOWAIT" for t in ("bonds","bond_security_master_evidence","bond_security_master_profiles")]


def test_sources_revalidated_after_lock_before_insert(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid])
    class MutatedDuringLock(Session):
        def execute(self,statement,*a,**kw):
            result=super().execute(statement,*a,**kw)
            if str(statement)=="BEGIN IMMEDIATE": f.source_bonds[0].source_fields["sector"]="government"
            return result
    r=apply(Service(sessionmaker(bind=db_session.bind,class_=MutatedDuringLock)),f,pf,p)
    assert r.status=="BLOCKED" and r.blockers==("FROZEN_ADMISSION_INVALID",) and r.attempted_rows==0
    with factory() as db: assert not db.scalars(select(Evidence).where(Evidence.source=="tinvest_universe")).all()


def test_lineage_checks_actual_source_keys_not_only_cached_fingerprint(db_session):
    f,pf,bid,factory,service=setup(db_session); p=plan(service,f,pf,[bid]); assert apply(service,f,pf,p).status=="APPLIED"
    with factory() as db:
        e=db.scalar(select(Evidence).where(Evidence.source=="tinvest_universe")); e.source_key="WRONG_UID"; db.commit()
    p=plan(service,f,pf,[bid]); assert p.already_satisfied_count==0 and p.blockers==("CURRENT_LINEAGE_CONFLICT",)
    r=apply(service,f,pf,p)
    assert r.status=="BLOCKED" and r.committed_evidence_created==0


def test_duplicate_target_security_identity_blocks_both_rows(db_session,monkeypatch):
    import app.services.execution_terms_evidence_bridge_service as module
    f,pf,ids,factory,service=batch_setup(db_session)
    with factory() as db: state=module._read(db,ids)
    state["bonds"][1].update(isin=state["bonds"][0]["isin"],secid=state["bonds"][0]["secid"])
    monkeypatch.setattr(module,"_read",lambda db,ids:state)
    p=plan(service,f,pf,ids)
    assert p.blocked_count==2 and all("TARGET_IDENTITY_MISMATCH" in r.blockers for r in p.rows)
