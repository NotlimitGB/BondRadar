from datetime import date, datetime, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine,select,event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
import app.models
from app.models.bond import Bond
from app.models.company import Company
from app.models.historical_evidence_foundation import HistoricalEvidenceObservation as Observation,HistoricalEvidenceSecurity as Security
from app.schemas.historical_evidence_foundation import *
from app.services.historical_evidence_normalization import sha,signed
from app.services.historical_evidence_execution_service import HistoricalEvidenceExecutionService,scope
from app.services.historical_evidence_plan_service import HistoricalEvidencePlanService,run_key

DAY=date(2020,9,1);END=date(2020,9,2);STAMP=datetime(2026,10,7,tzinfo=timezone.utc)
SECID="OLD_BOND";ISIN="RU000A000001"


def auth(plan,operation):
    return HistoricalAuthorization(operation=operation,explicit_authorization=True,plan_sha256=plan.plan_sha256,
        source_manifest_sha256=plan.source_manifest_sha256,current_db_sha256=plan.current_db_sha256,scope_sha256=scope(plan))


def page(query,rows,*,complete=True,next_offset=None,total=None):
    return signed(HistoricalSourcePage,"page_sha256",query=query,rows=tuple(rows),observed_at=STAMP,complete=complete,
        next_offset=next_offset,cursor_total=total,cursor_page_size=100 if total is not None else None,attempts=1,transient_failures=0)


@pytest.fixture
def archive():
    engine=create_engine("sqlite://",poolclass=StaticPool,connect_args={"check_same_thread":False})
    with engine.connect() as c:c.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine);factory=sessionmaker(engine,expire_on_commit=False)
    policy=HistoricalEvidencePolicy(cutoff=END)
    discovery=signed(HistoricalDiscovery,"source_manifest_sha256",policy=policy,status="COMPLETE",
        securities=(HistoricalSecurity(secid=SECID,isin=ISIN,board="TQCB",authority="SOURCE_LISTING",interval_start=DAY,interval_end=END),))
    planner=HistoricalEvidencePlanService(factory);executor=HistoricalEvidenceExecutionService(factory)
    def acquire(family,table,rows,*,day=None,offset=0,complete=True,next_offset=None,total=None):
        plan=planner.plan_acquisition(discovery=discovery)
        query=next(q for q in plan.queries if q.family==family and q.table==table and (day is None or q.trade_date==day))
        assert query.offset==offset
        result=executor.acquire_batch(reviewed_plan=plan,authorization=auth(plan,"ACQUIRE"),source_pages=(page(query,rows,complete=complete,next_offset=next_offset,total=total),))
        return result
    def seed(existing=False):
        assert acquire("LISTING","securities",[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"TQCB","HISTORY_FROM":str(DAY),"HISTORY_TILL":str(END)}]).status=="COMMITTED"
        description={"ISIN":ISIN,"NAME":"Historical bond","FACEUNIT":"RUB","FACEVALUE":"1000","MATDATE":"2021-09-01"}
        assert acquire("DESCRIPTION","description",[{"name":k,"value":v} for k,v in description.items()]).status=="COMMITTED"
        assert acquire("REFERENCE","securities",[{"secid":SECID,"isin":ISIN,"issuer_title":"Issuer","issuer_inn":"1234567890"}]).status=="COMMITTED"
        if existing:
            with factory() as db:
                c=Company(name="Issuer",inn="1234567890",ticker="MOEX_1234567890");db.add(c);db.flush()
                db.add(Bond(company_id=c.id,secid=SECID,isin=ISIN,name="Historical bond",currency="RUB",nominal_value=1000));db.commit()
        assert acquire("MARKET","history",[{"SECID":SECID,"ISIN":ISIN,"BOARDID":"TQCB","TRADEDATE":str(DAY),"CLOSE":"100","ACCINT":"2","YIELD":"10","DURATION":"365","VALUE":"10000","NUMTRADES":3}],day=DAY).status=="COMMITTED"
        with factory() as db:
            listing=db.execute(select(Observation.id).where(Observation.family=="LISTING")).scalar_one()
            observations=tuple(db.execute(select(Observation.id).where(Observation.family=="MARKET")).scalars())
        return observations,(BoardBinding(secid=SECID,isin=ISIN,board="TQCB",start=DAY,end=END,listing_observation_id=listing),)
    result=SimpleNamespace(engine=engine,factory=factory,discovery=discovery,policy=policy,planner=planner,executor=executor,acquire=acquire,seed=seed,
        run=run_key(policy,discovery.source_manifest_sha256))
    yield result
    engine.dispose()


def test_plan_read_only_deterministic_source_range_and_frozen(archive):
    sql=[];event.listen(archive.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    p=archive.planner.plan_acquisition(discovery=archive.discovery);p2=archive.planner.plan_acquisition(discovery=archive.discovery)
    assert p==p2 and p.policy.history_start==DAY and p.policy.horizons==(90,180,365)
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert {q.trade_date for q in p.queries if q.family=="MARKET"}=={DAY,END}
    with pytest.raises(ValueError):p.status="BLOCKED"
    with pytest.raises(ValueError):HistoricalEvidencePolicy(cutoff=END,horizons=(90,180,366))


def test_projection_collision_board_period_and_storage(archive):
    ids_,bindings=archive.seed(existing=True)
    p=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    assert p.status=="EXECUTABLE" and p.actions[0].action=="CREATE"
    invalid=bindings[0].model_copy(update={"board":"OTHER"})
    assert archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=(invalid,)).status=="BLOCKED"
    with archive.factory() as db:
        row=db.get(Observation,ids_[0]);row.normalized_json={**row.normalized_json,"values":{**row.normalized_json["values"],"price":"100.0000001"}}
        row.row_sha256=sha({"normalized":row.normalized_json,"raw":row.raw_json});db.commit()
    with pytest.raises(ValueError,match="ARCHIVE_MAPPING_DRIFT"):
        archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=bindings)
    from app.services.historical_evidence_plan_service import storage_errors
    assert storage_errors({"price":"100.0000001"},"MARKET")==("STORAGE_UNREPRESENTABLE:price",)


@pytest.mark.parametrize("ids_",[set([1]),(x for x in [1]),[True],[0],[1,1],[]])
def test_input_validation_before_database(archive,ids_):
    with pytest.raises(ValueError):archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=())


def test_frozen_frontier_cutoff_and_storage_context_independence(archive):
    from decimal import localcontext
    from app.services.historical_evidence_normalization import fits
    with pytest.raises(ValueError,match="FRONTIER_UNAVAILABLE"):archive.planner.research_policy()
    archive.seed(existing=True)
    with archive.factory() as db:
        bond=db.execute(select(Bond)).scalar_one()
        from app.models.bond_market_snapshot import BondMarketSnapshot
        db.add(BondMarketSnapshot(bond_id=bond.id,source="moex",trade_date=END));db.commit()
    assert archive.planner.research_policy().cutoff==END
    assert archive.planner.research_policy(cutoff=DAY).cutoff==DAY
    with localcontext() as ctx:
        ctx.prec=2
        assert fits("100.00000000",18,6) and not fits("100.00000001",18,6)
        assert not fits("1000000000000",18,6)


def test_history_without_isin_requires_independent_exact_reference_binding(archive):
    _,bindings=archive.seed(existing=True)
    assert archive.acquire("MARKET","history",[{"SECID":SECID,"BOARDID":"TQCB","TRADEDATE":str(END),"CLOSE":"100","ACCINT":"0"}],day=END).status=="COMMITTED"
    with archive.factory() as db:
        oid=db.execute(select(Observation.id).where(Observation.family=="MARKET",Observation.event_date==END)).scalar_one()
    plan=archive.planner.plan_projection(run_sha256=archive.run,observation_ids=(oid,),bindings=bindings)
    assert plan.status=="EXECUTABLE" and plan.actions[0].isin==ISIN
    wrong=bindings[0].model_copy(update={"isin":"RU000A999999"})
    assert archive.planner.plan_projection(run_sha256=archive.run,observation_ids=(oid,),bindings=(wrong,)).status=="BLOCKED"


def test_multiple_board_bindings_block_without_current_board_fallback(archive):
    ids_,bindings=archive.seed(existing=True)
    second=bindings[0].model_copy(update={"board":"OTHER","start":DAY})
    with pytest.raises(ValueError,match="DUPLICATE_BOARD_BINDING"):
        archive.planner.plan_projection(run_sha256=archive.run,observation_ids=ids_,bindings=(*bindings,second))
