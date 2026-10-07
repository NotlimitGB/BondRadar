import pytest
from sqlalchemy import select,event,func
from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_market_snapshot import BondMarketSnapshot as Market
from app.models.historical_evidence_foundation import HistoricalEvidenceApplicationReceipt as Receipt,HistoricalEvidenceObservation as Observation
from app.services.historical_evidence_plan_service import HistoricalEvidencePlanService
from app.services.historical_evidence_execution_service import HistoricalEvidenceExecutionService
from test_historical_evidence_plans import archive,auth,page,DAY,SECID,ISIN


def test_archive_batch_idempotency_authorization_and_resume(archive):
    plan=archive.planner.plan_acquisition(discovery=archive.discovery)
    q=next(q for q in plan.queries if q.family=="LISTING");p=page(q,[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"TQCB","HISTORY_FROM":str(DAY)}])
    commits=[];event.listen(archive.engine,"commit",lambda c:commits.append(1))
    bad=archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE").model_copy(update={"scope_sha256":"x"}),source_pages=(p,))
    assert bad.status=="BLOCKED" and not commits
    first=archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(p,))
    assert first.status=="COMMITTED" and len(commits)==1
    repeat=archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(p,))
    assert repeat.status=="IDEMPOTENT_NOOP" and len(commits)==1
    resumed=archive.planner.resume_plan(discovery=archive.discovery)
    assert q not in resumed.queries


def test_controlled_new_bond_shared_company_and_noop(archive):
    ids_,bindings=archive.seed();plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert plan.status=="EXECUTABLE",plan.blockers
    commits=[];event.listen(archive.engine,"commit",lambda c:commits.append(1))
    result=archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY"))
    assert result.status=="COMMITTED",result
    assert len(commits)==1
    with archive.factory() as db:
        assert db.execute(select(func.count()).select_from(Company)).scalar_one()==1
        bond=db.execute(select(Bond)).scalar_one();market=db.execute(select(Market)).scalar_one()
        assert bond.secid==SECID and str(market.nkd)=="2.000000" and market.raw_payload["moex"]["ACCINT"]=="2"
        assert not bond.is_floating_coupon and bond.signal=="insufficient_data"
    assert archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY")).status=="BLOCKED"
    new=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert new.actions[0].action=="RETAIN"
    assert archive.executor.apply_batch(reviewed_plan=new,authorization=auth(new,"APPLY")).status=="IDEMPOTENT_NOOP"
    assert len(commits)==1


def test_safe_null_enrichment_keeps_raw_id_and_nonnull_conflict(archive):
    ids_,bindings=archive.seed(existing=True)
    with archive.factory() as db:
        bond=db.execute(select(Bond)).scalar_one();row=Market(bond_id=bond.id,trade_date=DAY,source="moex",price=100,raw_payload={"old":"evidence"})
        db.add(row);db.commit();rid=row.id
    plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert plan.actions[0].action=="ENRICH_NULL"
    assert archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY")).status=="COMMITTED"
    with archive.factory() as db:
        row=db.get(Market,rid);assert row.raw_payload=={"old":"evidence"} and row.nkd==2
        row.price=99;db.commit()
    plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert plan.status=="BLOCKED"
    assert archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY")).committed_mutations==0


def test_failure_after_insert_atomic_rollback_and_lock_refusal(archive,monkeypatch):
    ids_,bindings=archive.seed();plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    def fail(c,u,s,p,x,m):
        if s.startswith("INSERT INTO bond_market_snapshots"):raise RuntimeError("SECRET")
    event.listen(archive.engine,"before_cursor_execute",fail)
    result=archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY"))
    event.remove(archive.engine,"before_cursor_execute",fail)
    assert result.status=="ROLLED_BACK" and result.rollback_confirmed and result.committed_mutations==0
    with archive.factory() as db:
        assert db.execute(select(func.count()).select_from(Bond)).scalar_one()==0
        assert db.execute(select(func.count()).select_from(Company)).scalar_one()==0
    from app.services import historical_evidence_execution_service as execution
    monkeypatch.setattr(execution,"lock",lambda *a:(_ for _ in ()).throw(RuntimeError("lock")))
    assert archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY")).status=="BLOCKED"


def test_pending_session_and_ambiguous_commit_no_blind_retry(archive,monkeypatch):
    plan=archive.planner.plan_acquisition(discovery=archive.discovery);q=next(q for q in plan.queries if q.family=="DATES")
    pending=archive.factory();pending.add(Company(name="pending",ticker="PENDING"))
    executor=HistoricalEvidenceExecutionService(lambda:pending)
    assert executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(q,[]),)).status=="BLOCKED"
    assert len(pending.new)==1
    pending.close()
    from sqlalchemy.orm import Session
    original=Session.commit
    def uncertain(db):original(db);raise RuntimeError("transport after commit")
    with monkeypatch.context() as m:
        m.setattr(Session,"commit",uncertain)
        result=archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(q,[]),))
    assert result.status=="COMMIT_OUTCOME_UNKNOWN" and result.committed_mutations is None
    repeat=archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(q,[]),))
    assert repeat.status=="IDEMPOTENT_NOOP"


def test_partial_partition_failure_then_exact_resume(archive):
    from app.services.historical_evidence_source_client import HistoricalSourceError
    plan=archive.planner.plan_acquisition(discovery=archive.discovery)
    q=next(q for q in plan.queries if q.family=="LISTING")
    rows=[{"SECID":f"OLD_{i}"} for i in range(100)]
    assert archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),
        source_pages=(page(q,rows,complete=False,next_offset=100,total=101),)).status=="COMMITTED"
    resumed=archive.planner.resume_plan(discovery=archive.discovery)
    q=next(q for q in resumed.queries if q.family=="LISTING");assert q.offset==100
    class Broken:
        def fetch_page(self,q):raise HistoricalSourceError("TRANSIENT_HTTP",attempts=3)
    broken=HistoricalEvidenceExecutionService(archive.factory,source_client=Broken())
    failure=broken.acquire_batch(reviewed_plan=resumed,authorization=auth(resumed,"ACQUIRE"),queries=(q,))
    assert failure.status=="BLOCKED" and failure.failed_partition_sha256s and failure.source_attempt_count==3
    assert archive.planner.resume_plan(discovery=archive.discovery)==resumed
    assert archive.executor.acquire_batch(reviewed_plan=resumed,authorization=auth(resumed,"ACQUIRE"),
        source_pages=(page(q,[{"SECID":"FINAL_EXTINCT"}],total=101),)).status=="COMMITTED"
    assert not any(q.family=="LISTING" for q in archive.planner.resume_plan(discovery=archive.discovery).queries)


@pytest.mark.parametrize("table,amount,expected",[("coupons","1","EXECUTABLE"),("coupons",None,"BLOCKED"),("redemptions","1000","EXECUTABLE"),("offers","1","BLOCKED")])
def test_cashflow_projection_no_amount_or_offer_inference(archive,table,amount,expected):
    from app.models.bond_cashflow_event import BondCashflowEvent
    ids_,bindings=archive.seed(existing=True)
    dates={"coupons":"COUPONDATE","redemptions":"REDEMPTIONDATE","offers":"OFFERDATE"}
    assert archive.acquire("CASHFLOWS",table,[{dates[table]:str(DAY),"VALUE":amount,"FACEUNIT":"RUB"}]).status=="COMMITTED"
    with archive.factory() as db:
        oid=db.execute(select(Observation.id).where(Observation.family=="CASHFLOWS")).scalar_one()
    plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=(oid,),bindings=bindings)
    assert plan.status==expected
    result=archive.executor.apply_batch(reviewed_plan=plan,authorization=auth(plan,"APPLY"))
    assert result.status==("COMMITTED" if expected=="EXECUTABLE" else "BLOCKED")
    with archive.factory() as db:
        assert db.execute(select(func.count()).select_from(BondCashflowEvent)).scalar_one()==(expected=="EXECUTABLE")


def test_new_archive_revision_preserved_but_canonical_replacement_forbidden(archive):
    from app.schemas.historical_evidence_foundation import HistoricalDiscovery,BoardBinding
    from app.services.historical_evidence_normalization import signed
    from app.services.historical_evidence_plan_service import run_key
    ids_,bindings=archive.seed(existing=True)
    original=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert archive.executor.apply_batch(reviewed_plan=original,authorization=auth(original,"APPLY")).status=="COMMITTED"
    discovery=signed(HistoricalDiscovery,"source_manifest_sha256",policy=archive.policy,status="COMPLETE",
        securities=archive.discovery.securities,source_page_hashes=("f"*64,))
    p=archive.planner.plan_acquisition(discovery=discovery)
    listing=next(q for q in p.queries if q.family=="LISTING")
    market=next(q for q in p.queries if q.family=="MARKET" and q.trade_date==DAY)
    source_pages=(page(listing,[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"TQCB","HISTORY_FROM":str(DAY)}]),
        page(market,[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"TQCB","TRADEDATE":str(DAY),"CLOSE":"99","ACCINT":"2"}]))
    assert archive.executor.acquire_batch(reviewed_plan=p,authorization=auth(p,"ACQUIRE"),source_pages=source_pages).status=="COMMITTED"
    new_run=run_key(archive.policy,discovery.source_manifest_sha256)
    with archive.factory() as db:
        rows=tuple(db.execute(select(Observation).order_by(Observation.id)).scalars())
        lid=next(r.id for r in reversed(rows) if r.family=="LISTING")
        oid=next(r.id for r in reversed(rows) if r.family=="MARKET")
        assert db.get(Observation,ids_[0]).raw_json["CLOSE"]=="100"
    b=bindings[0].model_copy(update={"listing_observation_id":lid})
    new=archive.planner.plan_projection(run_sha256=new_run,observation_ids=(oid,),bindings=(b,))
    assert new.status=="BLOCKED" and new.actions[0].action=="CONFLICT"
    with archive.factory() as db:assert db.execute(select(Market.price)).scalar_one()==100


def test_two_extinct_bonds_share_one_authoritative_company_atomic_batch(archive):
    from app.schemas.historical_evidence_foundation import HistoricalDiscovery,HistoricalSecurity,BoardBinding
    from app.services.historical_evidence_normalization import signed
    from app.services.historical_evidence_plan_service import run_key
    from test_historical_evidence_plans import END
    identities=((SECID,ISIN),("EXTINCT_SECOND","RU000A000002"))
    discovery=signed(HistoricalDiscovery,"source_manifest_sha256",policy=archive.policy,status="COMPLETE",
        securities=tuple(HistoricalSecurity(secid=s,isin=i,board="TQCB",authority="SOURCE_LISTING",interval_start=DAY,interval_end=END) for s,i in identities))
    plan=archive.planner.plan_acquisition(discovery=discovery);source=[]
    for q in plan.queries:
        if q.family=="LISTING":rows=[{"SECID":s,"ISIN":i,"BOARDID":"TQCB","HISTORY_FROM":str(DAY),"HISTORY_TILL":str(END)} for s,i in identities]
        elif q.family=="MARKET" and q.trade_date==DAY:
            rows=[{"SECID":s,"ISIN":i,"BOARDID":"TQCB","TRADEDATE":str(DAY),"CLOSE":"100","ACCINT":"0"} for s,i in identities]
        elif q.family=="REFERENCE":rows=[{"secid":q.secid,"isin":q.expected_isin,"issuer_title":"Shared issuer","issuer_inn":"1234567890"}]
        elif q.family=="DESCRIPTION":
            rows=[{"name":k,"value":v} for k,v in {"ISIN":q.expected_isin,"NAME":q.secid,"FACEUNIT":"RUB","FACEVALUE":"1000"}.items()]
        else:continue
        source.append(page(q,rows))
    assert archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=tuple(source)).status=="COMMITTED"
    key=run_key(archive.policy,discovery.source_manifest_sha256)
    with archive.factory() as db:
        observations=tuple(db.execute(select(Observation).order_by(Observation.id)).scalars())
        ids_=tuple(r.id for r in observations if r.family=="MARKET")
        from app.models.historical_evidence_foundation import HistoricalEvidenceSecurity
        bindings=tuple(BoardBinding(secid=s,isin=i,board="TQCB",start=DAY,end=END,
            listing_observation_id=next(r.id for r in observations if r.family=="LISTING" and db.get(HistoricalEvidenceSecurity,r.security_id).secid==s)) for s,i in identities)
    reviewed=archive.planner.plan_projection(run_sha256=key,observation_ids=ids_,bindings=bindings)
    assert reviewed.status=="EXECUTABLE" and all("MODEL_DEFAULT_NOT_EVIDENCE" in a.diagnostics for a in reviewed.actions)
    assert archive.executor.apply_batch(reviewed_plan=reviewed,authorization=auth(reviewed,"APPLY")).status=="COMMITTED"
    with archive.factory() as db:
        bonds=tuple(db.execute(select(Bond).order_by(Bond.id)).scalars())
        assert len(bonds)==2 and len({b.company_id for b in bonds})==1
        assert db.execute(select(func.count()).select_from(Company)).scalar_one()==1


def test_post_commit_audit_failure_is_not_rollback(archive):
    plan=archive.planner.plan_acquisition(discovery=archive.discovery)
    q=next(q for q in plan.queries if q.family=="DATES")
    calls=[]
    def factory():
        calls.append(1)
        if len(calls)==2:raise RuntimeError("post-commit read unavailable")
        return archive.factory()
    executor=HistoricalEvidenceExecutionService(factory)
    result=executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(q,[]),))
    assert result.status=="POST_COMMIT_AUDIT_FAILED" and result.db_mutated and result.commit_count==1 and not result.rollback_confirmed
    assert archive.executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(q,[]),)).status=="IDEMPOTENT_NOOP"
