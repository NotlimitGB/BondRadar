"""Task308A offline qualification and sampling; all evidence is synthetic."""
from datetime import date, datetime, timezone
from functools import lru_cache
import importlib.util
import io
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.schemas.historical_discovery_coverage_audit import *
from app.schemas.historical_identity_feasibility import SamplePlan, ProbeLimits
from app.services.historical_discovery_checkpoint import fingerprint
from app.services.historical_discovery_coverage_audit import signed_report
from app.services.historical_audit_canonical_json import digest
from app.services.historical_identity_feasibility_service import HistoricalIdentityFeasibilityService, IdentityError, validate_plan, isin_valid, resolve_rows
from app.services.historical_identity_probe_service import encode

ROOT=Path(__file__).resolve().parents[2]
DATES=(date(2020,10,1),date(2021,1,1),date(2025,1,1),date(2026,9,30))


@lru_cache
def make_report(n=60):
    items=[]
    for i in range(n):
        observed=i%5!=0;early=i%2==0
        items.append(IdentityDiagnostic(secid=f"SU{i:04}" if i%11==0 else f"H{i:04}",isin=None,listing=True,observed=observed,
            listing_boards=("TQCB",),observed_boards=("OLD","TQCB") if i%3==0 and observed else ("TQCB",) if observed else (),
            first_observed_date=DATES[0 if early else 2] if observed else None,last_observed_date=DATES[1 if early else 3] if observed else None,
            first_listing_interval=date(2020,9,1) if i%4 else None,last_listing_interval=None,observed_date_count=2 if observed else 0,
            actual_trade_date_count=int(observed and early),usable_price_nkd_date_count=int(observed and i%3==0),conflicting_isin_candidates=()))
    items=tuple(sorted(items,key=lambda x:x.secid));obs=sum(i.observed for i in items)
    history=sum(len(i.observed_boards) for i in items)
    u=UniverseCoverage(identity_records=n+history,distinct_secids=n,distinct_isins=0,exact_pairs=n,
        secid_board_pairs=sum(len(set(i.listing_boards+i.observed_boards)) for i in items),listing_pairs=n,observed_pairs=obs,
        listing_only_pairs=n-obs,observed_only_pairs=0,shared_pairs=obs,missing_isin_pairs=n,affected_missing_isin_secids=n,
        conflicting_secids=0,multi_board_pairs=sum(len(set(i.listing_boards+i.observed_boards))>1 for i in items),
        not_observed_on_last_market_date_pairs=sum(i.observed and i.last_observed_date!=DATES[-1] for i in items))
    return signed_report(status="COMPLETE",source_manifest_sha256="1"*64,source_content_sha256="2"*64,scope_sha256="3"*64,implementation_sha256=fingerprint(),
        evidence_integrity=Integrity(status="VERIFIED",manifest_verified=True,indexes_verified=True,receipt_pages_verified=True,index_replay_verified=True,
            reconciliation_verified=True,verification_level="FULL_ACCEPTED_GENERATION_REPLAY"),
        discovery_coverage=DiscoveryCoverage(history_start=date(2020,9,1),cutoff=DATES[-1],requested_partitions=4,completed_partitions=4,accepted_pages=4,
            http_attempts=4,retries=0,empty_market_dates=(),source_families=("COLUMNS","DATES","LISTING","MARKET"),
            source_observation_first=datetime(2026,10,1,tzinfo=timezone.utc),source_observation_last=datetime(2026,10,1,tzinfo=timezone.utc)),
        universe_coverage=u,identity_coverage=IdentityCoverage(missing_isin_listing_records=n,missing_isin_history_records=history,
            affected_market_dates=DATES,listing_market_discrepancy_secids=(),identities=items),
        date_coverage=DateCoverage(observed_dates=DATES,actual_trade_dates=DATES,usable_price_nkd_dates=DATES,actual_and_usable_dates=DATES,
            unknown_trade_dates=(),annual=(),monthly=(),research_entries=()),
        board_coverage=tuple(BoardCoverage(board=b,market_rows=0,distinct_identities=0,observed_dates=0,first_date=None,last_date=None,
            actual_trade_rows=0,zero_trade_rows=0,unknown_trade_rows=0,usable_price_nkd_rows=0) for b in ("OLD","TQCB")),capabilities=Capabilities(deep_accepted_source_integrity=True))


def plan(size=36):return HistoricalIdentityFeasibilityService().build(report=make_report(),sample_size=size)


def reseal(r,**changes):
    r=r.model_copy(update=changes)
    return r.model_copy(update={"audit_sha256":digest(r)})


def test_plan_determinism_strata_frozen_roundtrip():
    r=make_report();before=encode(r);p=plan()
    assert len(p.samples)==36 and tuple(s.secid for s in p.samples)==tuple(sorted({s.secid for s in p.samples}))
    assert validate_plan(p)==p and plan()==p and encode(r)==before
    assert SamplePlan.model_validate_json(encode(p))==p
    for name in ("LISTING_ONLY","SHARED","MULTI_OBSERVED_BOARD","EARLY_FIRST","RECENT_LAST","CANONICAL_OFZ","POSSIBLE_INACTIVE_PROXY"):
        assert next(s for s in p.strata if s.name==name).selected_count>0
    assert plan(12).plan_sha256!=p.plan_sha256
    with pytest.raises(ValidationError):p.population_secids=1
    with pytest.raises(ValidationError):ProbeLimits(max_requests=True)
    with pytest.raises(IdentityError):HistoricalIdentityFeasibilityService().build(report=make_report(2))


@pytest.mark.parametrize("damage",["sha","expected","source_manifest","duplicate","counts","conflicts","dates","fingerprint"])
def test_upstream_rejection(damage):
    r=make_report();kwargs={}
    if damage=="sha":r=r.model_copy(update={"audit_sha256":"0"*64})
    if damage=="expected":kwargs["expected_audit_sha256"]="0"*64
    if damage=="source_manifest":kwargs["expected_manifest_sha256"]="0"*64
    if damage=="duplicate":r=reseal(r,identity_coverage=r.identity_coverage.model_copy(update={"identities":r.identity_coverage.identities+(r.identity_coverage.identities[0],)}))
    if damage=="counts":r=reseal(r,universe_coverage=r.universe_coverage.model_copy(update={"distinct_secids":1}))
    if damage=="conflicts":
        rows=list(r.identity_coverage.identities);rows[0]=rows[0].model_copy(update={"conflicting_isin_candidates":("FAKE",)})
        r=reseal(r,identity_coverage=r.identity_coverage.model_copy(update={"identities":tuple(rows)}))
    if damage=="dates":r=reseal(r,date_coverage=r.date_coverage.model_copy(update={"observed_dates":tuple(reversed(DATES))}))
    if damage=="fingerprint":r=reseal(r,implementation_sha256="0"*64)
    with pytest.raises((IdentityError,ValueError)):HistoricalIdentityFeasibilityService().build(report=r,**kwargs)


def test_multiple_pairs_preserved_once_per_secid():
    r=make_report();items=list(r.identity_coverage.identities)
    index=next(i for i,x in enumerate(items) if not x.observed);old=items[index]
    values=("RU000A0JS3W6","US0378331005")
    items[index:index+1]=[old.model_copy(update={"isin":v,"conflicting_isin_candidates":values}) for v in values]
    u=r.universe_coverage.model_copy(update={"identity_records":r.universe_coverage.identity_records+1,"distinct_isins":2,"exact_pairs":61,
        "listing_pairs":61,"listing_only_pairs":r.universe_coverage.listing_only_pairs+1,
        "missing_isin_pairs":59,"affected_missing_isin_secids":59,"conflicting_secids":1})
    changed=reseal(r,universe_coverage=u,identity_coverage=r.identity_coverage.model_copy(update={"identities":tuple(items)}))
    p=HistoricalIdentityFeasibilityService().build(report=changed,sample_size=48)
    assert len(next(s for s in p.samples if s.secid==old.secid).original_identities)==2
    assert len({s.secid for s in p.samples})==48


@pytest.mark.parametrize("value,valid",[("US0378331005",True),("RU000A0JS3W6",True),("US0378331006",False),("us0378331005",False),
    (" US0378331005",False),("US0378331005 ",False),(None,False),(True,False),(123,False),("RU000A0JS3W",False)])
def test_isin_validation(value,valid):assert isin_valid(value) is valid


@pytest.mark.parametrize("rows,status",[([],"SOURCE_RECORD_UNAVAILABLE"),([{"secid":"OTHER","isin":"US0378331005"}],"SOURCE_RESULT_NOT_EXACT"),
    ([{"secid":"X"},{"secid":"Y"}],"SOURCE_MATCH_AMBIGUOUS"),([{"secid":"H0001"}],"ISIN_NOT_PRESENT"),
    ([{"secid":"h0001","isin":"US0378331005"}],"SOURCE_RESULT_NOT_EXACT"),
    ([{"secid":"H0001","isin":"US0378331005"},{"secid":"OTHER"}],"EXACT_CURRENT_SOURCE_BINDING_FOUND"),
    ([{"secid":"H0001","isin":"US0378331005"},{"secid":"H0001","isin":"RU000A0JS3W6"}],"EXACT_CURRENT_SOURCE_BINDING_CONFLICT"),
    ([{"secid":"H0001","isin":"US0378331006"}],"SOURCE_INVALID")])
def test_exact_resolver(rows,status):
    r=resolve_rows(secid="H0001",source="REFERENCE",rows=rows,receipts=(),complete=True)
    assert r.status==status and not r.pit_ready and not r.historical_publication_proven


def runner():
    spec=importlib.util.spec_from_file_location("task308a_runner",ROOT/"scripts/historical_identity_feasibility.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_offline_cli_key_order_no_overwrite(tmp_path,monkeypatch):
    r=make_report();source=tmp_path/"audit.json";source.write_bytes(encode(r));out=tmp_path/"out";out.mkdir()
    before=source.read_bytes();monkeypatch.chdir(tmp_path)
    module=runner();args=["--task307-report",str(source),"--output-dir",str(out),"--expected-audit-sha256",r.audit_sha256]
    assert module.main(args,stdout=io.StringIO(),stderr=io.StringIO())==0
    saved=(out/"task308a-sample-plan.json").read_bytes();source.write_text(json.dumps(json.loads(before),indent=2))
    assert module.main(args,stdout=io.StringIO(),stderr=io.StringIO())==0
    assert (out/"task308a-sample-plan.json").read_bytes()==saved
    assert module.main(args+["--sample-size","12"],stdout=io.StringIO(),stderr=io.StringIO())==2
    assert (out/"task308a-sample-plan.json").read_bytes()==saved
