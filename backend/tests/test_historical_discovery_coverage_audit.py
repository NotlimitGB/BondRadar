"""Task307 source-only fixtures: no real source requests or application DB."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, getcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import ast
import tracemalloc
import pytest
from pydantic import ValidationError
from app.schemas.historical_discovery_checkpoint import DiscoverySourcePage
from app.schemas.historical_evidence_foundation import HistoricalEvidencePolicy, HistoricalSourceQuery
from app.schemas.historical_discovery_coverage_audit import AuditLimits, HistoricalDiscoveryCoverageAudit
from app.services import historical_discovery_checkpoint as checkpoint
from app.services import historical_discovery_coverage_reader as reader
from app.services.historical_discovery_coverage_audit import HistoricalDiscoveryCoverageAuditService, trade_class
from app.services.historical_evidence_normalization import signed, sha
from app.services.historical_audit_canonical_json import digest, chunks


ROOT = Path(__file__).resolve().parents[2]
POLICY = HistoricalEvidencePolicy(cutoff=date(2026,9,30))
DAY = date(2020,10,1)
LIMITS = AuditLimits(reserve_bytes=0)


def row(secid="CORP_A", isin="RU_A", board="TQCB", day=DAY, **extra):
    return {"SECID":secid,"ISIN":isin,"BOARDID":board,"TRADEDATE":str(day),
            "CLOSE":"90","ACCINT":"1","NUMTRADES":1,**extra}


def page(query, rows):
    metadata = query.family in ("DATES","COLUMNS")
    return signed(DiscoverySourcePage,"page_sha256",query=query,rows=tuple(rows),
        observed_at=datetime(2026,10,8,tzinfo=timezone.utc),complete=True,next_offset=None,
        completion_basis="METADATA" if metadata else "CURSOR",cursor_total=None if metadata else len(rows),
        cursor_page_size=None if metadata else 100,attempts=1,transient_failures=0)


def create(root, queries, data):
    store = checkpoint.CheckpointStore(root,POLICY,create=True)
    for query in queries:
        store.accept(query,store.state(query),page(query,data(query)),1)
    return store,checkpoint.finalize(store)


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    days=(DAY,DAY+timedelta(days=9),DAY+timedelta(days=19),date(2021,1,5),POLICY.cutoff)
    queries=tuple(checkpoint.discovery_queries(POLICY))[:3]+tuple(HistoricalSourceQuery(family="MARKET",table="history",trade_date=d) for d in days)
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    monkeypatch.setattr(reader,"discovery_queries",lambda policy:iter(queries))
    def data(q):
        if q.family=="LISTING":
            return [{"SECID":"CORP_A","ISIN":"RU_A","BOARDID":"TQCB","HISTORY_FROM":"2020-01-01","HISTORY_TILL":"2026-09-30"},
                    {"SECID":"LIST_ONLY","ISIN":"RU_L","BOARDID":"OLD"},
                    {"SECID":"UNKNOWN","BOARDID":"TQCB"}]
        if q.family!="MARKET":return []
        if q.trade_date==DAY:
            return [row(LEGALCLOSEPRICE="91"),row("UNKNOWN",None,NUMTRADES=None),
                row("SU26207RMFS9","RU000A0JS3W6","TQOB",NUMTRADES=0,ACCINT=0),
                row("BAD_NKD","RU_N",ACCINT="bad"),row("BAD_PRICE","RU_P",CLOSE="bad")]
        if q.trade_date==days[1]:
            return [row(day=q.trade_date,board="OTHER",NUMTRADES=0),row(day=q.trade_date,isin="RU_OTHER",NUMTRADES="bad"),
                row("BOOL","RU_B",day=q.trade_date,NUMTRADES=False)]
        if q.trade_date==days[2]:return []
        return [row(day=q.trade_date)]
    source=tmp_path/"source";scratch=tmp_path/"scratch";output=tmp_path/"output"
    scratch.mkdir();output.mkdir()
    store,manifest=create(source,queries,data)
    return source,scratch,output,store,manifest,queries


def build(fixture, **kwargs):
    source,scratch,*_=fixture
    return HistoricalDiscoveryCoverageAuditService().build(evidence_root=source,scratch_dir=scratch,limits=kwargs.pop("limits",LIMITS),**kwargs)


def snapshots(root):
    return {str(p.relative_to(root)):checkpoint.stream_hash(p) for p in root.rglob("*") if p.is_file()}


def test_complete_exact_counts_integrity_temporal_and_identity(fixture):
    source,scratch,_,_,manifest,_=fixture
    before=snapshots(source)
    report=build(fixture,expected_manifest_sha256=manifest.source_manifest_sha256)
    assert report.status=="COMPLETE",report.readiness_blockers
    assert snapshots(source)==before and not list(scratch.iterdir())
    assert report.evidence_integrity.verification_level=="FULL_ACCEPTED_GENERATION_REPLAY"
    assert report.source_manifest_sha256==manifest.source_manifest_sha256
    assert digest(report)==report.audit_sha256
    assert report.universe_coverage.identity_records==manifest.identity_count
    assert report.universe_coverage.missing_isin_pairs==1
    assert report.universe_coverage.conflicting_secids==1
    assert report.universe_coverage.shared_pairs==2
    assert report.universe_coverage.listing_only_pairs==1
    assert report.universe_coverage.multi_board_pairs==1
    metrics={m.name:m.count for m in report.trading_activity}
    assert metrics=={"actual":5,"zero":2,"missing":1,"malformed":2,"negative":0,"fractional":0,"nonfinite":0}
    # Row counts are 5+3+0+1+1 = 10, not unique Bond counts.
    assert manifest.market_row_count==10
    assert sum(metrics.values())==10
    price={m.name:m.count for m in report.price_nkd_coverage}
    assert price["clean"]==1 and price["usable"]==manifest.usable_price_row_count
    assert len(report.date_coverage.research_entries)==60
    october=report.date_coverage.research_entries[0]
    assert october.month=="2020-10" and october.entry_date==DAY
    assert october.identities==5 and october.jointly_covered_identities==1
    assert october.horizons[0].entry_identities_with_subsequent_observation==1
    assert report.date_coverage.research_entries[1].entry_date is None
    assert report.discovery_coverage.empty_market_dates==(DAY+timedelta(days=19),)
    assert {b.board for b in report.board_coverage}=={"TQCB","TQOB","OTHER","OLD"}
    assert all(not f.publication_proven for f in report.pit_evidence)
    assert not report.capabilities.pit_ready and not report.capabilities.acquisition_ready


@pytest.mark.parametrize("damage",["expected","manifest","index_missing","index_bytes","fingerprint","scope","page_missing","page_bytes","receipt","pointer","reconciliation","index_replay"])
def test_invalid_evidence_blocks_without_mutation(fixture,damage):
    source,_,_,store,manifest,queries=fixture
    expected=None
    if damage=="expected":expected="0"*64
    elif damage in ("manifest","fingerprint","reconciliation","index_replay"):
        raw=checkpoint.read_json(source/"manifest.json")
        if damage=="manifest":raw["source_manifest_sha256"]="0"*64
        else:
            if damage=="fingerprint":raw["implementation_sha256"]="0"*64
            if damage=="reconciliation":raw["market_row_count"]+=1
            if damage=="index_replay":
                raw["index_sha256s"]["bindings"]=__import__("hashlib").sha256(b"").hexdigest()
                (source/(raw["index_sha256s"]["bindings"]+".ndjson")).write_bytes(b"")
            raw["source_manifest_sha256"]=sha({k:v for k,v in raw.items() if k!="source_manifest_sha256"})
        (source/"manifest.json").write_text(json.dumps(raw),encoding="utf-8")
    elif damage.startswith("index"):
        path=source/(manifest.index_sha256s["identities"]+".ndjson")
        if damage=="index_missing":path.unlink()
        else:path.write_bytes(path.read_bytes()+b" ")
    elif damage=="scope":(source/"scope.json").write_text("{}")
    else:
        query=queries[3];state=store.state(query)
        path,receipt=next(store.receipts(query,state))
        if damage=="page_missing":path.unlink()
        elif damage=="page_bytes":path.write_bytes(path.read_bytes()+b" ")
        elif damage=="receipt":(store.directory(query)/"0"/"0.json").write_text("{}")
        else:(store.directory(query)/"state.json").write_text("{}")
    before=snapshots(source)
    report=build(fixture,expected_manifest_sha256=expected)
    assert report.status=="BLOCKED" and report.universe_coverage is None
    assert not report.evidence_integrity.receipt_pages_verified
    assert snapshots(source)==before
    assert not report.capabilities.network_access and not report.capabilities.db_mutation


@pytest.mark.parametrize("raw,expected",[(None,"missing"),("","missing"),(" ","missing"),(False,"malformed"),("bad","malformed"),
    ("-1","negative"),("1.5","fractional"),("NaN","nonfinite"),("Infinity","nonfinite"),(0,"zero"),("2.0","actual")])
def test_trade_categories(raw,expected):
    assert trade_class(raw)==expected


def test_frozen_deterministic_roundtrip_context_and_limits(fixture):
    getcontext().clear_flags()
    context=getcontext().copy()
    report=build(fixture)
    assert build(fixture)==report and str(getcontext())==str(context)
    serialized="".join(chunks(report))
    assert HistoricalDiscoveryCoverageAudit.model_validate_json(serialized)==report
    with pytest.raises(ValidationError):report.status="BLOCKED"
    with pytest.raises(ValidationError):AuditLimits(scratch_bytes=True)
    with pytest.raises(ValidationError):
        from app.schemas.historical_discovery_coverage_audit import Capabilities
        Capabilities(pit_ready=0)
    with pytest.raises(ValidationError):HistoricalDiscoveryCoverageAudit.model_validate_json(serialized[:-1]+',"unknown":1}')
    assert build(fixture,limits=AuditLimits(reserve_bytes=0,scratch_bytes=1)).status=="BLOCKED"
    assert build(fixture,limits=AuditLimits(reserve_bytes=0,output_bytes=100)).readiness_blockers==("OUTPUT_BUDGET_EXCEEDED",)
    assert build(fixture,limits=AuditLimits(reserve_bytes=0,max_identity_rows=1)).status=="BLOCKED"


def runner():
    spec=importlib.util.spec_from_file_location("task307_runner",ROOT/"scripts/historical_discovery_coverage_audit.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_runner_arbitrary_cwd_two_artifacts_and_no_overwrite(fixture,monkeypatch,tmp_path):
    source,scratch,output,_,manifest,_=fixture
    monkeypatch.chdir(tmp_path)
    module=runner();out,err=io.StringIO(),io.StringIO()
    args=["--evidence-root",str(source),"--scratch-dir",str(scratch),"--output-dir",str(output),"--reserve-bytes","0"]
    assert module.main(args,stdout=out,stderr=err)==0
    report=json.loads(out.getvalue())
    assert (output/module.JSON_NAME).exists() and (output/module.MD_NAME).exists()
    before=snapshots(output)
    assert module.main(args,stdout=io.StringIO(),stderr=io.StringIO())==0
    assert snapshots(output)==before
    other=json.loads((output/module.JSON_NAME).read_text());other["source_manifest_sha256"]="0"*64
    (output/module.JSON_NAME).write_text(json.dumps(other))
    before=snapshots(output);out=io.StringIO()
    assert module.main(args,stdout=out,stderr=io.StringIO())==2
    assert json.loads(out.getvalue())["readiness_blockers"]==["EXISTING_REPORT_CONFLICT"]
    assert snapshots(output)==before
    assert manifest.source_manifest_sha256==report["source_manifest_sha256"]


def test_paths_reserve_sanitized_errors_and_read_only(fixture,monkeypatch):
    source,scratch,*_=fixture
    report=HistoricalDiscoveryCoverageAuditService().build(evidence_root=source,scratch_dir=source,limits=LIMITS)
    assert report.readiness_blockers==("AUDIT_PATHS_OVERLAP",)
    original_usage=shutil.disk_usage
    monkeypatch.setattr(shutil,"disk_usage",lambda p:shutil._ntuple_diskusage(100,99,1))
    assert build(fixture,limits=AuditLimits(reserve_bytes=2)).readiness_blockers==("FREE_SPACE_RESERVE_NOT_MET",)
    monkeypatch.setattr(shutil,"disk_usage",original_usage)
    # Explicit guard catches every attempted source write even when running as root.
    original=Path.open
    def guarded(path,mode="r",*args,**kwargs):
        if source==path or source in path.parents:
            assert not any(flag in mode for flag in ("w","a","x","+"))
        return original(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,"open",guarded)
    assert build(fixture).status=="COMPLETE"
    bad=source.parent/"password=SECRET"
    result=HistoricalDiscoveryCoverageAuditService().build(evidence_root=bad,scratch_dir=scratch,limits=LIMITS)
    assert result.status=="BLOCKED" and "SECRET" not in str(result)


def test_horizon_beyond_cutoff_and_current_reference_not_authority(fixture):
    report=build(fixture)
    # Direct descriptive helper permits policy horizons beyond observed frozen range.
    from app.services.historical_discovery_coverage_audit import Aggregation
    with reader.workspace(fixture[0],fixture[1],LIMITS) as (root,db,budget):
        r=reader.CoverageReader(root,db,budget);a=Aggregation(db);r.scan(a.market)
        policy=POLICY.model_copy(update={"entry_span":(date(2026,9,1),date(2026,9,1))})
        entry=a.entries(policy,a.dates())[0]
        assert all(h.status=="BEYOND_CUTOFF" and h.entry_identities_with_subsequent_observation is None for h in entry.horizons)
    assert next(p for p in report.pit_evidence if p.family=="issuer_linkage").available_evidence=="NOT_ACQUIRED"


def test_price_nkd_alias_matrix_and_invalid_nonnull_clean(tmp_path,monkeypatch):
    queries=tuple(checkpoint.discovery_queries(POLICY))[:3]+(HistoricalSourceQuery(family="MARKET",table="history",trade_date=DAY),)
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    monkeypatch.setattr(reader,"discovery_queries",lambda policy:iter(queries))
    cases=[{"CLOSE":0,"ACCINT":0},{"ACCINT":"-1"},{"ACCINT":True},{"ACCINT":1,"ACCRUEDINT":"1.0"},
        {"ACCINT":1,"ACCRUEDINT":2},{"LEGALCLOSEPRICE":"-1"},{"CLOSE":"bad","LEGALCLOSEPRICE":"101","ACCINT":0},
        {"ACCINT":"NaN"},{"ACCINT":"Infinity"},{"ACCINT":None,"ACCRUEDINT":0},{"ACCINT":0,"ACCRUEDINT":" "}]
    root=tmp_path/"evidence";scratch=tmp_path/"scratch";scratch.mkdir()
    create(root,queries,lambda q:[row(f"S{i}",f"RU{i}",**values) for i,values in enumerate(cases)] if q.family=="MARKET" else [])
    result=HistoricalDiscoveryCoverageAuditService().build(evidence_root=root,scratch_dir=scratch,limits=LIMITS)
    assert result.status=="COMPLETE",result.readiness_blockers
    counts={m.name:m.count for m in result.market_quality}
    assert counts["usable"]==4 and counts["clean"]==1 and counts["cleaninvalid"]==1
    assert counts["nkdinvalid"]==5 and counts["nkd"]==6


def test_resealed_duplicate_rows_still_block(tmp_path,monkeypatch):
    queries=tuple(checkpoint.discovery_queries(POLICY))[:3]+(HistoricalSourceQuery(family="MARKET",table="history",trade_date=DAY),)
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    monkeypatch.setattr(reader,"discovery_queries",lambda policy:iter(queries))
    monkeypatch.setattr(checkpoint.CheckpointStore,"check_page",lambda *args:None)
    root=tmp_path/"evidence";scratch=tmp_path/"scratch";scratch.mkdir()
    create(root,queries,lambda q:[row(),row()] if q.family=="MARKET" else [])
    result=HistoricalDiscoveryCoverageAuditService().build(evidence_root=root,scratch_dir=scratch,limits=LIMITS)
    assert result.readiness_blockers==("SOURCE_DUPLICATE_PARTITION_ROW",)


def test_publication_failure_rolls_back_new_artifacts(fixture,monkeypatch):
    module=runner();source,scratch,output,*_=fixture
    real_link=os.link;calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise OSError("password=SECRET")
        return real_link(*args,**kwargs)
    monkeypatch.setattr(os,"link",fail_second)
    out=io.StringIO()
    code=module.main(["--evidence-root",str(source),"--scratch-dir",str(scratch),"--output-dir",str(output),"--reserve-bytes","0"],stdout=out,stderr=io.StringIO())
    assert code==2 and json.loads(out.getvalue())["status"]=="BLOCKED"
    assert "SECRET" not in out.getvalue() and not list(output.iterdir())


def test_scale_generated_pages_bounded_processing(tmp_path,monkeypatch,record_property):
    dates=tuple(DAY+timedelta(days=i) for i in range(30))
    queries=tuple(checkpoint.discovery_queries(POLICY))[:3]+tuple(HistoricalSourceQuery(family="MARKET",table="history",trade_date=d) for d in dates)
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    monkeypatch.setattr(reader,"discovery_queries",lambda policy:iter(queries))
    source=tmp_path/"source";scratch=tmp_path/"scratch";scratch.mkdir()
    store=checkpoint.CheckpointStore(source,POLICY,create=True)
    for q in queries:
        if q.family!="MARKET":store.accept(q,store.state(q),page(q,[]),1);continue
        for offset in range(0,1000,100):
            p=signed(DiscoverySourcePage,"page_sha256",query=q.model_copy(update={"offset":offset}),
                rows=tuple(row(f"S{i}",f"RU{i}",day=q.trade_date,NUMTRADES=i%3) for i in range(offset,offset+100)),
                observed_at=datetime(2026,10,8,tzinfo=timezone.utc),complete=offset==900,next_offset=None if offset==900 else offset+100,
                cursor_total=1000,cursor_page_size=100,completion_basis="CURSOR",attempts=1,transient_failures=0)
            store.accept(q,store.state(q),p,1)
    checkpoint.finalize(store)
    tracemalloc.start()
    report=HistoricalDiscoveryCoverageAuditService().build(evidence_root=source,scratch_dir=scratch,limits=LIMITS)
    peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    assert report.status=="COMPLETE",report.readiness_blockers
    assert report.universe_coverage.exact_pairs==1000
    assert next(m for m in report.market_quality if m.name=="total").count==30000
    assert report.discovery_coverage.accepted_pages==303
    assert peak<64*1024**2 and not list(scratch.iterdir())
    record_property("processing_peak_bytes",peak)
    print(f"task307_processing_peak_bytes={peak}")


def test_static_no_execution_db_or_network_paths():
    paths=[ROOT/"backend/app/services/historical_discovery_coverage_reader.py",ROOT/"backend/app/services/historical_discovery_coverage_audit.py",ROOT/"scripts/historical_discovery_coverage_audit.py"]
    forbidden={"VerifiedDiscoveryIndex","finalize","lock","acquire_batch","apply_batch","run_discovery","create_engine","Session","getenv","environ","Client","sync","requests","httpx"}
    for path in paths:
        tree=ast.parse(path.read_text())
        names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}|{n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
        assert not names&forbidden
