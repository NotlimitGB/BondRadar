"""Offline frozen evidence PLAN, narrow source gates and reversible source constraint."""
import ast
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, getcontext
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select, text, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.company import Company
from app.models.bond import Bond
from app.models.bond_security_master_profile import BondSecurityMasterProfile as Profile
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence as Evidence
from app.services.bond_security_master_service import BondSecurityMasterService as Master
from app.services.execution_terms_evidence_bridge_service import ExecutionTermsEvidenceBridgeService as Service, _row, _read
from app.schemas.execution_terms_evidence_bridge import ExecutionTermsEvidenceApplyAuthorizationV1 as Authorization
from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService as Frozen
from app.services.tinvest_bond_identity_bridge_service import TInvestBondIdentityBridgeService as Identity
from test_tinvest_frozen_admission_evidence import inputs, NOW
from test_tinvest_bond_import_preflight import description, _run_preflight


def sources(lots=(1,), *, classes=None, buy=None):
    values=inputs(isins=("RU000A100AA1",))
    first=values["source_bonds"][0]
    rows=tuple(first.model_copy(update={"uid":f"UID-{i}","lot":lot,
        "class_code":(classes or ["TQCB"]*len(lots))[i],
        "source_fields":{**first.source_fields,"classCode":(classes or ["TQCB"]*len(lots))[i]},
        **({"buy_available":buy[i],"availability_classification":type(first.availability_classification)(
            "API_BUY_AVAILABLE" if buy[i] else "API_VISIBLE_NOT_BUYABLE")} if buy is not None else {})}) for i,lot in enumerate(lots))
    values.update(source_bonds=rows,identity_bridge=Identity.build(rows,[],[]))
    frozen=Frozen.build(**values)
    manifest=frozen.admission_manifest
    pf=_run_preflight(admission_manifest=manifest,
        moex_descriptions=[description(c) for c in manifest.import_candidate_manifest],internal_bonds=[],company_projections=[])
    return frozen,pf


def setup(db,lots=(1,),*,classes=None,buy=None,count=1):
    frozen,pf=sources(lots,classes=classes,buy=buy)
    c=Company(name="Isolated issuer",ticker="TASK302B"); db.add(c); db.flush()
    candidate=pf.candidate_rows[0]
    b=Bond(company_id=c.id,name="Synthetic",isin=candidate.isin,secid=candidate.secid)
    db.add(b); db.flush()
    master=Master(db)
    for field,value in (("currency_code","RUB"),("nominal_value",Decimal(1000))):
        master.record_assertion(bond=b,field_name=field,source="moex_description",assertion_type="scalar_value",
            normalized_value=value,observed_at=NOW,source_key=b.secid)
    master.resolve_profile(b); db.commit()
    factory=sessionmaker(bind=db.get_bind(),expire_on_commit=False)
    return frozen,pf,b.id,factory,Service(factory)


def plan(service,frozen,pf,ids,at=NOW):
    return service.plan(frozen_admission=frozen,preflight=pf,target_bond_ids=ids,board_observed_at=at)


def auth(p):
    return Authorization(explicit_apply=True,**{k:getattr(p,k) for k in (
        "plan_sha256","target_bond_id_set_sha256","frozen_admission_sha256","import_preflight_sha256","current_db_state_sha256")})


def apply(service,f,pf,p,a=None):
    return service.apply(frozen_admission=f,preflight=pf,reviewed_plan=p,authorization=a or auth(p))


def test_plan_deterministic_read_only_source_copy_serialization_and_context(db_session):
    f,pf,bid,factory,service=setup(db_session,lots=(5,5))
    before=deepcopy((f.model_dump(),pf.model_dump())); ctx=getcontext().copy(); sql=[]
    def capture(conn,cursor,s,params,context,many): sql.append(s)
    event.listen(db_session.bind,"before_cursor_execute",capture)
    try:
        p=plan(service,f,pf,[bid]); again=plan(service,f,pf,[bid])
    finally: event.remove(db_session.bind,"before_cursor_execute",capture)
    assert p.status=="EXECUTABLE" and p.ready_count==1 and p.rows[0].lot==5
    assert len(p.rows[0].assertions)==3 and {a.source for a in p.rows[0].assertions}=={"tinvest_universe","moex_universe"}
    assert p.model_dump_json()==again.model_dump_json() and p.plan_sha256==again.plan_sha256
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)
    assert before==(f.model_dump(),pf.model_dump()) and getcontext().prec==ctx.prec
    assert type(p).model_validate_json(p.model_dump_json())==p
    for obj in (p,p.rows[0],p.rows[0].lot_evidence[0],p.rows[0].board_evidence,p.capabilities,p.provenance):
        with pytest.raises(ValueError): setattr(obj,"pit_ready",True)
        with pytest.raises(ValueError): type(obj)(**obj.model_dump(),extra=True)


@pytest.mark.parametrize("lots,expected",[((1,),()),((10,),()),((None,2),()),((2,3),("LOT_SIZE_CONFLICT",)),
    ((None,),("LOT_SIZE_NOT_AVAILABLE",)),((0,1),("LOT_SIZE_INVALID",)),((-1,1),("LOT_SIZE_INVALID",)),
    ((2147483648,1),("LOT_SIZE_INVALID",))])
def test_lot_matrix(db_session,lots,expected):
    f,pf,bid,_,service=setup(db_session,lots)
    p=plan(service,f,pf,[bid]); assert p.blockers==expected
    if None in lots: assert "SOURCE_LOT_NOT_SUPPLIED" in p.rows[0].diagnostics


def test_conflict_not_hidden_by_buy_availability_and_off_board_exclusion(db_session):
    f,pf,bid,_,service=setup(db_session,(1,2),buy=[True,False])
    assert "LOT_SIZE_CONFLICT" in plan(service,f,pf,[bid]).blockers
    f2,pf2=sources((1,99),classes=["TQCB","OTHER"])
    p=plan(service,f2,pf2,[bid]); assert p.status=="EXECUTABLE" and p.rows[0].lot==1
    assert len([a for a in p.rows[0].assertions if a.source=="tinvest_universe"])==1


@pytest.mark.parametrize("change",["hash","source","version","incomplete","pit","preflight"])
def test_frozen_failures_before_session(db_session,change):
    f,pf=sources()
    if change=="hash": f=f.model_copy(update={"artifact_sha256":"0"*64})
    elif change=="source": f=f.model_copy(update={"source_bonds":(f.source_bonds[0].model_copy(update={"lot":2}),)})
    elif change=="version": f=f.model_copy(update={"schema_version":"bad"})
    elif change=="incomplete": f=f.model_copy(update={"acquisition_complete":False})
    elif change=="pit": f=f.model_copy(update={"pit_ready":True})
    else: pf=pf.model_copy(update={"contract_version":"bad"})
    def forbidden(): pytest.fail("Invalid frozen evidence reached DB")
    p=plan(Service(forbidden),f,pf,[1]); assert p.status=="BLOCKED"
    assert p.blockers==("PREFLIGHT_INVALID" if change=="preflight" else "FROZEN_ADMISSION_INVALID",)


@pytest.mark.parametrize("ids",[[],"1",{1},iter([1]),[True],[0],[1,1],[1.0]])
def test_invalid_target_inputs_before_db(ids):
    f,pf=sources()
    p=plan(Service(lambda:pytest.fail("Invalid IDs reached DB")),f,pf,ids)
    assert p.blockers==("TARGET_IDS_INVALID",)


@pytest.mark.parametrize("field",["isin","secid"])
def test_exact_identity_no_normalization(db_session,field):
    f,pf,bid,factory,service=setup(db_session)
    with factory() as db:
        b=db.get(Bond,bid); setattr(b,field," "+getattr(b,field).lower()); db.commit()
    p=plan(service,f,pf,[bid]); assert "TARGET_IDENTITY_MISMATCH" in p.blockers


def test_missing_profile_target_and_observation_timestamp(db_session):
    f,pf,bid,factory,service=setup(db_session)
    assert "BOARD_OBSERVED_AT_MISSING" in plan(service,f,pf,[bid],None).blockers
    assert plan(service,f,pf,[bid],NOW.replace(tzinfo=None)).blockers==("BOARD_OBSERVED_AT_INVALID",)
    assert "TARGET_BOND_NOT_FOUND" in plan(service,f,pf,[99999]).blockers
    with factory() as db: db.delete(db.scalar(select(Profile).where(Profile.bond_id==bid))); db.commit()
    assert "SECURITY_MASTER_PROFILE_MISSING" in plan(service,f,pf,[bid]).blockers


@pytest.mark.parametrize("kind",["none","incomplete","error","secid","isin","primary","observed"])
def test_board_narrow_gates(db_session,kind):
    f,pf,bid,factory,_=setup(db_session)
    with factory() as db: state=_read(db,[bid])
    c=pf.candidate_rows[0]; board=c.board_evidence
    if kind=="none": board=board.model_copy(update={"observations":()})
    elif kind in ("incomplete","error"): board=board.model_copy(update={"scan_status":"INCOMPLETE" if kind=="incomplete" else "SOURCE_ERROR"})
    elif kind in ("secid","isin"): board=board.model_copy(update={"observations":(board.observations[0].model_copy(update={kind:"WRONG"}),)})
    c=c.model_copy(update={"board_evidence":board,**({"primary_board":"OTHER"} if kind=="primary" else {"board_observed":False} if kind=="observed" else {})})
    damaged=pf.model_copy(update={"candidate_rows":(c,)})
    row=_row(bid,state["bonds"][0],state["profiles"][0],state["evidence"],f,damaged,NOW)
    assert row.status=="BLOCKED" and row.blockers
    if kind in ("secid","isin"): assert "BOARD_IDENTITY_CONFLICT" in row.blockers


def migration():
    path=Path(__file__).parents[1]/"alembic/versions/202610030001_add_tinvest_security_master_evidence_source.py"
    spec=importlib.util.spec_from_file_location("task302b_migration",path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def test_migration_upgrade_downgrade_safety_and_sources():
    engine=create_engine("sqlite:///:memory:"); mod=migration()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE bond_security_master_evidence (id INTEGER PRIMARY KEY, source VARCHAR(32), CONSTRAINT source_allowed CHECK (source in ('moex_universe','moex_description','moex_cashflows')))"))
        ops=Operations(MigrationContext.configure(conn))
        with ops.context(MigrationContext.configure(conn)):
            mod.upgrade()
            for i,s in enumerate(("moex_universe","moex_description","moex_cashflows","tinvest_universe")):
                conn.execute(text("INSERT INTO bond_security_master_evidence VALUES (:id,:source)"),dict(id=i,source=s))
            with pytest.raises(Exception): conn.execute(text("INSERT INTO bond_security_master_evidence VALUES (99,'UNKNOWN')"))
            with pytest.raises(RuntimeError): mod.downgrade()
            assert conn.scalar(text("SELECT COUNT(*) FROM bond_security_master_evidence"))==4
            conn.execute(text("DELETE FROM bond_security_master_evidence WHERE source='tinvest_universe'"))
            mod.downgrade()
            with pytest.raises(Exception): conn.execute(text("INSERT INTO bond_security_master_evidence VALUES (98,'tinvest_universe')"))
    assert mod.down_revision=="202609160001"


def test_static_offline_no_legacy_or_direct_profile_assignment():
    tree=ast.parse((Path(__file__).parents[1]/"app/services/execution_terms_evidence_bridge_service.py").read_text())
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(any(x in i for x in ("httpx","requests","urllib","moex_iss","universe_client","paper","pilot")) for i in imports)
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {"sync","getenv","now","open"} for n in ast.walk(tree))
    assert not any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr in {"lot_size","lot_size_state","trading_board","trading_board_state"} for t in n.targets) for n in ast.walk(tree))
