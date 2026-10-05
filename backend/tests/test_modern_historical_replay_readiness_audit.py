"""Task306A persisted fixtures: no source requests and no production database."""
import ast
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from app.db.base import Base
import app.models
from app.models.company import Company
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.legal_issuer import LegalIssuer
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact
from app.models.bond_return_label import BondReturnLabel
from app.models.cbr_bank_financial_evidence import CbrBankSourceArtifact, CbrBankReportSnapshot, CbrBankArtifactAvailabilityEvidence
from app.schemas.modern_historical_replay_readiness import HistoricalReplayReadinessAuditV1 as Audit
from app.services.modern_historical_replay_readiness_audit_service import (
    ModernHistoricalReplayReadinessAuditService as Service, publication_gate, classify_date, outcome_coverage, pct,
)
from app.services.modern_historical_replay_evidence_reader import read_evidence

D = Decimal
T = date(2025,1,10)
UTC = timezone.utc


@pytest.fixture
def seeded():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = Company(name="Audit fixture",ticker="AUDIT306A"); db.add(company); db.flush()
        issuer = LegalIssuer(source_issuer_id="306A",issuer_title="Fixture",issuer_inn="7701234567",resolution_state="verified")
        db.add(issuer); db.flush()
        bonds=[]
        for n in range(5):
            bond=Bond(company_id=company.id,name="OFZ-PD" if n>=3 else "Corporate",
                isin=f"RU000306A{n:03d}",secid=f"SU306A{n}" if n>=3 else f"RU306A{n}",maturity_date=T+timedelta(days=365))
            db.add(bond); db.flush(); bonds.append(bond)
            db.add(BondSecurityMasterProfile(bond_id=bond.id,currency_state="verified",currency_code="RUB",
                nominal_state="verified",nominal_value=D("1000"),coupon_structure="fixed",amortization_structure="bullet",
                perpetual_structure="dated",coupon_frequency_state="verified",coupon_frequency_per_year=2,
                lot_size_state="verified",lot_size=1,trading_board_state="verified",trading_board="TQCB",
                maturity_state="verified",maturity_date=T+timedelta(days=365),listing_status="active"))
            if n<3:
                db.add(BondLegalIssuerProfile(bond_id=bond.id,mapping_state="verified",mapping_source="moex_security_reference",
                    source_issuer_id="306A",issuer_title="Fixture",issuer_inn="7701234567",security_match_status="EXACT_SECID"))
                artifact=CreditRiskSourceArtifact(source_provider="ACRA",source_kind="RATING_RELEASE",source_url=f"https://example.invalid/{n}",
                    content_type="application/pdf",content_bytes=b"fixture",content_sha256=f"{n+1:064x}",
                    retrieved_at=datetime(2026,1,1,tzinfo=UTC))
                db.add(artifact);db.flush()
                db.add(CreditRatingEvent(artifact_id=artifact.id,rating_agency="ACRA",target_kind="BOND",resolution_state="RESOLVED",
                    bond_id=bond.id,source_bond_isin=bond.isin,source_object_id=str(n),event_date=T-timedelta(days=10),
                    publication_precision="UNKNOWN",rating_scale_raw=None,rating_value_raw="AAA",event_fingerprint=f"{n+10:064x}"))
            for offset in range(-6,91):
                duration = D("3") if n==4 else D("1")
                db.add(BondMarketSnapshot(bond_id=bond.id,trade_date=T+timedelta(days=offset),source="moex",
                    price=D("100"),clean_price=D("100"),dirty_price=None,nkd=D("0"),yield_to_maturity=D("10"),
                    duration_years=duration,volume=D("10"),raw_payload={"moex":{"DURATION":str(duration*365),
                    "VOLUME":"10","VALUE":"10000","NUMTRADES":"5"}}))
        db.commit()
        ids=tuple(b.id for b in bonds)
    yield SimpleNamespace(engine=engine,ids=ids)
    engine.dispose()


def build(env):
    with Session(env.engine) as db:return Service(db).build()


def at(report,day=T):
    return next(r for r in report.per_date_readiness if r.as_of_date==day)


def test_exact_inventory_classification_and_current_universe(seeded):
    r=build(seeded); assert r.status=="COMPLETE",r.known_p0_blockers
    inventory=next(i for i in r.source_inventories if i.source_table=="bond_market_snapshots")
    assert (inventory.row_count,inventory.bond_count,inventory.date_count)==(485,5,97)
    assert (inventory.min_date,inventory.max_date)==(T-timedelta(days=6),T+timedelta(days=90))
    fields={f.field:f for f in inventory.fields}
    assert fields["nkd"].nonnull_count==485 and fields["nkd"].nonnull_pct==D("100")
    assert fields["dirty_price"].nonnull_pct==0
    row=at(r)
    assert row.classification=="DIAGNOSTIC_ONLY" and row.joint_qualifying_bond_ids==seeded.ids[:3]
    assert row.ofz_duration_node_count==2 and row.outcome_full_count==5
    assert row.security_master_pit_proven_bond_count==0 and not row.cashflow_completeness_proven
    assert not r.historical_universe_membership_proven and r.pit_safe_date_count==0
    assert "SURVIVORSHIP_BIAS_RISK" in r.known_p0_blockers
    assert row.rating_publication_proven_bond_count==0
    assert "RATING_PUBLICATION_UNKNOWN" in row.blockers
    assert r.monthly_candidate_date_count and r.window_summary.monthly_candidate_dates[0]==T-timedelta(days=1)
    assert r.credit_target_inventory[0].rating_value_raw=="AAA"
    assert r.credit_target_inventory[0].rating_scale_raw is None
    assert r.credit_target_inventory[0].publication_breakdown[0].key=="UNKNOWN"
    assert row.liquidity_benchmark_eligible_bond_count==5 and not row.liquidity_benchmark_size_ready
    assert "TASK270_BENCHMARK_UNIVERSE_INSUFFICIENT" in row.diagnostics


@pytest.mark.parametrize("precision,day_delta,hour,expected",[
    ("DATE",-1,0,"PROVEN"),("DATE",0,0,"FUTURE"),("DATE",1,0,"FUTURE"),
    ("TIMESTAMP",0,0,"PROVEN"),("TIMESTAMP",0,1,"FUTURE"),("TIMESTAMP",-1,23,"PROVEN"),
    ("UNKNOWN",0,0,"UNPROVEN")])
def test_publication_start_day_boundary(precision,day_delta,hour,expected):
    day=T+timedelta(days=day_delta)
    row={"publication_precision":precision,"publication_date":day if precision=="DATE" else None,
        "publication_at":datetime.combine(day,datetime.min.time(),UTC)+timedelta(hours=hour) if precision=="TIMESTAMP" else None}
    assert publication_gate(row,datetime.combine(T,datetime.min.time(),UTC))==expected


def test_unobserved_future_and_current_inactive_universe(seeded):
    with Session(seeded.engine) as db:
        company=db.execute(select(Company)).scalar_one()
        absent=Bond(company_id=company.id,name="No history",isin="RU000NOHIST1",secid="NONE306A")
        future=Bond(company_id=company.id,name="Later observed",isin="RU000FUTURE1",secid="LATER306A")
        db.add_all([absent,future]);db.flush()
        ids=absent.id,future.id
        db.add(BondMarketSnapshot(bond_id=future.id,trade_date=T+timedelta(days=1),source="moex",price=D("100")))
        profile=db.execute(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==seeded.ids[0])).scalar_one()
        profile.listing_status="delisted"
        db.commit()
    result=build(seeded)
    inventory=next(i for i in result.source_inventories if i.source_table=="bonds")
    counts={e.key:e.count for e in inventory.breakdowns}
    assert counts["bonds_without_MOEX_history"]==1
    assert counts["historical_bonds_current_nonactive"]==1
    assert at(result).first_observation_after_entry_bond_count==1
    history={r.bond_id:r for r in result.bond_histories}
    assert history[ids[0]].first_trade_date is None
    assert history[ids[1]].first_trade_date==T+timedelta(days=1)
    assert result.pit_safe_date_count==0


def test_publication_authority_does_not_upgrade_current_identity_or_terms(seeded):
    with Session(seeded.engine) as db:
        for row in db.scalars(select(CreditRatingEvent)):
            row.publication_precision="DATE";row.publication_date=T-timedelta(days=1)
        db.commit()
    result=build(seeded)
    assert at(result).rating_publication_proven_bond_count==3
    assert at(result).classification=="DIAGNOSTIC_ONLY"
    families={r.family:r for r in result.evidence_matrix}
    assert families["rating_publication"].pit_status=="PROVEN"
    assert families["security_master"].available_now and not families["security_master"].historically_dated
    assert families["issuer_identity"].available_now and not families["issuer_identity"].historically_dated
    counts={r.key:r.count for i in result.source_inventories if i.source_table=="credit_rating_events" for r in i.breakdowns}
    assert counts["RATING_PUBLICATION_RESOLVED_TARGET_COUNT"]==3
    assert counts["RATING_PIT_PROVEN_COUNT"]==0


def test_curve_inputs_independent_of_dirty_price_and_execution_terms(seeded):
    with Session(seeded.engine) as db:
        for bid in seeded.ids[3:]:
            profile=db.execute(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==bid)).scalar_one()
            profile.nominal_state="unknown";profile.nominal_value=None
            for snap in db.execute(select(BondMarketSnapshot).where(BondMarketSnapshot.bond_id==bid)).scalars():snap.nkd=None
        db.commit()
    result=build(seeded)
    assert at(result).ofz_duration_node_count==2
    assert at(result).outcome_full_count==3
    assert result.ofz_market_date_coverage[0].positive_duration_node_count==2


def test_future_outcome_never_improves_decision_counts_and_five_day_boundary(seeded):
    first=build(seeded); assert at(first,T-timedelta(days=1)).liquidity_history_ready_bond_count==5
    assert at(first,T-timedelta(days=2)).liquidity_history_ready_bond_count==0
    with Session(seeded.engine) as db:
        future=db.scalars(select(BondMarketSnapshot).where(BondMarketSnapshot.trade_date>=T)).all()
        for s in future:s.nkd=None
        db.commit()
    second=build(seeded)
    a,b=at(first),at(second)
    for name in ("entry_market_ready_bond_count","liquidity_history_ready_bond_count","credit_evidence_ready_bond_count",
        "security_master_current_ready_bond_count","ofz_duration_node_count"):
        assert getattr(a,name)==getattr(b,name)
    assert b.outcome_full_count==0 and b.classification=="UNUSABLE"


@pytest.mark.parametrize("kind",["future_publication","cohort_scale","ofz_structure","missing_issuer","missing_lot"])
def test_joint_gates_and_current_terms_cannot_be_pit(seeded,kind):
    with Session(seeded.engine) as db:
        if kind=="future_publication":
            r=db.scalars(select(CreditRatingEvent)).first();r.publication_precision="DATE";r.publication_date=T
        elif kind=="cohort_scale":db.scalars(select(CreditRatingEvent)).first().rating_scale_raw=" "
        elif kind=="ofz_structure":db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==seeded.ids[4])).one().coupon_structure="floating"
        elif kind=="missing_issuer":db.delete(db.scalars(select(BondLegalIssuerProfile)).first())
        else:
            p=db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==seeded.ids[0])).one();p.lot_size_state="unknown";p.lot_size=None
        db.commit()
    r=at(build(seeded));assert r.classification=="UNUSABLE" and not r.universe_membership_proven


@pytest.mark.parametrize("kind",["gap","redemption","missing_amount","maturity","after_redemption","offer"])
def test_outcome_daily_boundary_and_contractual_events(kind):
    interval=((T,T+timedelta(days=90)),)
    events=[];maturity=None
    def flow(offset,event_type="redemption",amount=D("1000")):
        return {"event_date":T+timedelta(days=offset),"event_type":event_type,"amount":amount,"currency":"RUB"}
    if kind=="gap":interval=((T,T+timedelta(days=89)),)
    if kind=="redemption":interval=((T,T+timedelta(days=9)),);events=[flow(10)]
    if kind=="missing_amount":events=[flow(20,"coupon",None)]
    if kind=="maturity":maturity=T+timedelta(days=20)
    if kind=="after_redemption":events=[flow(10),flow(11,"coupon",D("1"))]
    if kind=="offer":events=[flow(10,"offer_redemption",None)]
    state,coupon,blockers,diagnostics=outcome_coverage(T,interval,events,maturity)
    assert state==("FULL" if kind in ("redemption","offer") else "PARTIAL")
    if kind=="offer":assert "OFFER_NOT_AUTOMATIC_CASH" in diagnostics


def test_daily_gaps_duplicate_and_nonrub_cashflow():
    intervals=((T,T+timedelta(days=10)),(T+timedelta(days=12),T+timedelta(days=90)))
    assert outcome_coverage(T,intervals,[],None)[0]=="PARTIAL"
    f={"event_date":T+timedelta(days=10),"event_type":"coupon","amount":D("1"),"currency":"USD"}
    result=outcome_coverage(T,((T,T+timedelta(days=90)),),[f,f],None)
    assert result[0]=="PARTIAL" and "DUPLICATE_CASHFLOW_EVENT" in result[2]
    assert "CASHFLOW_AMOUNT_OR_CURRENCY_INVALID" in result[2]


def test_classifier_all_proof_and_missing_proof():
    kwargs=dict(joint_bond_ids=(1,2,3),peer_bond_ids=(1,2,3),ofz_nodes=2)
    proofs=dict.fromkeys(("universe","market","terms","issuer","rating","liquidity","duration","ofz","outcome","cashflow"),True)
    assert classify_date(**kwargs,proofs=proofs)=="PIT_SAFE"
    assert classify_date(**kwargs,proofs={**proofs,"universe":False})=="DIAGNOSTIC_ONLY"
    assert classify_date(**{**kwargs,"joint_bond_ids":(1,2)},proofs=proofs)=="UNUSABLE"


def test_determinism_json_frozen_decimal_and_select_only_pending(seeded):
    with Session(seeded.engine) as db:
        pending=Company(name="Pending",ticker="PENDING306A");db.add(pending)
        dirty=db.get(Bond,seeded.ids[0]);dirty.name="Caller-owned pending name"
        removed=db.get(Company,dirty.company_id);db.delete(removed)
        states=(set(db.new),set(db.dirty),set(db.deleted));sql=[]
        event.listen(seeded.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
        with localcontext() as context:
            context.prec=6;context.rounding=ROUND_DOWN
            a=Service(db).build();b=Service(db).build()
            assert context.prec==6 and context.rounding==ROUND_DOWN
        assert a.status=="COMPLETE" and a==b
        assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in sql)
        import re
        assert not any(re.search(r"\b(INSERT|UPDATE|DELETE)\b",s,re.I) for s in sql)
        assert states==(set(db.new),set(db.dirty),set(db.deleted))
        assert Audit.model_validate_json(a.model_dump_json())==a
        with pytest.raises(ValueError):a.status="BLOCKED"
        with pytest.raises(ValueError):Audit(status="COMPLETE",audit_classification="INSUFFICIENT_FOR_REPLAY",unexpected=True)
    assert pct(1,3)==D("33.33333333333333333333333333")


@pytest.mark.parametrize("kwargs",[{"horizon_days":True},{"horizon_days":89},{"market_source":"MOEX"},
    {"liquidity_lookback_calendar_days":31},{"liquidity_min_observation_days":False}])
def test_bad_configuration_before_select(kwargs):
    with pytest.raises(ValueError,match="INVALID_AUDIT_CONFIGURATION"):Service(None).build(**kwargs)


def test_empty_missing_schema_and_stable_blocked_contract():
    engine=create_engine("sqlite://")
    with Session(engine) as db:
        a=Service(db).build();assert a.status=="BLOCKED"
        assert Audit.model_validate_json(a.model_dump_json())==a and a.audit_sha256
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        b=Service(db).build();assert b.status=="COMPLETE" and b.all_candidate_date_count==0
        assert b.audit_classification=="INSUFFICIENT_FOR_REPLAY"
    engine.dispose()


def test_legacy_labels_do_not_upgrade_readiness(seeded):
    before=build(seeded)
    with Session(seeded.engine) as db:
        db.add(BondReturnLabel(bond_id=seeded.ids[0],as_of_date=T,horizon_days=90,return_method="total_return",
            label="positive_return",future_return=D("0.99"),gross_total_return=D("0.99")))
        db.commit()
    after=build(seeded)
    assert before.per_date_readiness==after.per_date_readiness
    row=next(i for i in after.source_inventories if i.source_table=="bond_return_labels")
    assert row.row_count==1 and "NOT_MODERN_OUTCOME_AUTHORITY" in row.diagnostics
    assert "0.990000" not in after.model_dump_json()


def test_financial_report_date_is_not_publication_and_future_terms_unproven(seeded):
    now=datetime(2026,1,1,tzinfo=UTC)
    with Session(seeded.engine) as db:
        artifact=CbrBankSourceArtifact(source_url="https://example.invalid/source",artifact_filename="fixture.rar",form="0409101",
            report_date=T-timedelta(days=30),content_bytes=b"x",content_sha256="a"*64,compressed_size=1,
            content_type="application/octet-stream",first_discovered_at=now,first_retrieved_at=now,ingested_at=now,
            parser_contract_version="fixture",archive_runtime_contract="fixture",artifact_fingerprint="b"*64)
        db.add(artifact);db.flush()
        for bound in (True,False):
            db.add(CbrBankArtifactAvailabilityEvidence(artifact_id=artifact.id,evidence_source="WAYBACK",observed_at=now,
                exact_payload_bound=bound,source_reference="fixture-reference"))
        db.add(CbrBankReportSnapshot(artifact_id=artifact.id,form="0409101",report_date=T-timedelta(days=30),
            value_member_name="VALUE",member_schema_inventory=[],form_schema_fingerprint="c"*64,parser_contract_version="fixture",
            observed_at=now,retrieved_at=now,ingested_at=now,publication_status="UNKNOWN",publication_at=None,
            record_count=0,subject_count=0,subject_set_sha256="d"*64,observation_set_sha256="e"*64,snapshot_fingerprint="f"*64))
        db.add(BondSecurityMasterEvidence(bond_id=seeded.ids[0],field_name="lot_size",source="moex_universe",
            assertion_type="scalar_value",normalized_value_json={"value":1},effective_at=datetime(2024,1,1,tzinfo=UTC),
            observed_at=now,ingestion_at=now,evidence_fingerprint="1"*64))
        db.commit()
    report=build(seeded)
    assert "FINANCIAL_PUBLICATION_UNKNOWN" in report.known_p0_blockers
    assert at(report).classification=="DIAGNOSTIC_ONLY" and at(report).security_master_pit_proven_bond_count==0
    family=next(f for f in report.evidence_matrix if f.family=="financial_publication")
    assert family.pit_status=="NOT_APPLICABLE" and not family.current_model_input
    evidence=next(i for i in report.source_inventories if i.source_table=="bond_security_master_evidence:lot_size")
    assert evidence.time_ranges[1].minimum==now
    availability=next(i for i in report.source_inventories if i.source_table=="cbr_bank_artifact_availability_evidence")
    counts={c.key:c.count for c in availability.breakdowns}
    assert counts["exact_payload_bound=True"]==counts["exact_payload_bound=False"]==1


@pytest.mark.parametrize("component",["turnover","trades","zero_turnover","malformed_turnover"])
def test_liquidity_history_not_component_readiness(seeded,component):
    with Session(seeded.engine) as db:
        for row in db.scalars(select(BondMarketSnapshot).where(BondMarketSnapshot.bond_id==seeded.ids[0],BondMarketSnapshot.trade_date<T)):
            payload=deepcopy(row.raw_payload)
            if component=="turnover":payload["moex"].pop("VALUE")
            elif component=="trades":payload["moex"].pop("NUMTRADES")
            elif component=="zero_turnover":payload["moex"]["VALUE"]="0"
            else:payload["moex"]["VALUE"]=True
            row.raw_payload=payload
        db.commit()
    row=at(build(seeded))
    assert row.liquidity_history_ready_bond_count==5 and row.classification=="UNUSABLE"
    assert "TASK270_BENCHMARK_UNIVERSE_INSUFFICIENT" in row.diagnostics


def test_market_duration_separate_from_present_terms_and_ofz_lot(seeded):
    with Session(seeded.engine) as db:
        p=db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==seeded.ids[0])).one()
        p.nominal_state="unknown";p.nominal_value=None
        p=db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id==seeded.ids[4])).one()
        p.lot_size_state="unknown";p.lot_size=None
        db.commit()
    row=at(build(seeded))
    assert row.entry_market_ready_bond_count==row.duration_market_history_bond_count==5
    assert row.ofz_duration_node_count==2
    assert row.classification=="UNUSABLE"


def test_input_row_order_and_source_models_remain_unchanged(seeded):
    from dataclasses import replace
    from app.services.modern_historical_replay_readiness_audit_service import _build
    with Session(seeded.engine) as db:
        data=read_evidence(db)
        copied={k:tuple(deepcopy(dict(r)) for r in rows) for k,rows in data.tables.items()}
        before=deepcopy(copied)
        a=_build(replace(data,tables=copied))
        reverse={k:tuple(reversed(rows)) for k,rows in copied.items()}
        b=_build(replace(data,tables=reverse,decision_windows=tuple(reversed(data.decision_windows))))
    assert a==b and copied==before


def test_nonfinite_invalid_evidence_and_duplicate_economic_dates(seeded):
    from dataclasses import replace
    from app.services.modern_historical_replay_readiness_audit_service import _build
    with Session(seeded.engine) as db:data=read_evidence(db)
    tables={k:tuple(dict(r) for r in rows) for k,rows in data.tables.items()}
    first=dict(tables["bond_market_snapshots"][0]);first["yield_to_maturity"]=D("Infinity")
    tables["bond_market_snapshots"]=(first,*tables["bond_market_snapshots"][1:])
    assert _build(replace(data,tables=tables)).status=="COMPLETE"
    tables["bond_market_snapshots"]+= (first,)
    with pytest.raises(ValueError,match="INVALID_DB_EVIDENCE"):_build(replace(data,tables=tables))


@pytest.mark.parametrize("bad",[True,0,1,"false"])
def test_pit_declaration_exact_false(bad):
    with pytest.raises(ValueError):Audit(status="COMPLETE",audit_classification="INSUFFICIENT_FOR_REPLAY",pit_ready=bad)


def test_no_financial_results_mutation_network_or_loader_calls():
    assert not {"strategy_return","profit","alpha","sharpe","win_rate"} & set(Audit.model_fields)
    root=Path(__file__).resolve().parents[1]/"app"/"services"
    for name in ("modern_historical_replay_evidence_reader.py","modern_historical_replay_readiness_audit_service.py"):
        tree=ast.parse((root/name).read_text(encoding="utf-8"))
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls & {"commit","flush","add_all","delete","sync","build_curve","build_for_bond","apply"}
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="add"
            and ((isinstance(n.func.value,ast.Name) and n.func.value.id in ("db","session")) or
                 isinstance(n.func.value,ast.Attribute)) for n in ast.walk(tree))
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(x.startswith(("httpx","requests","app.services.investment_model","app.services.paper_")) for x in imports)
