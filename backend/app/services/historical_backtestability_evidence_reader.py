"""A2-only batched scalar projections; raw payloads never survive their row."""
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from sqlalchemy import select
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.services.modern_historical_replay_evidence_reader import SPECS
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from app.services.historical_endpoint_evidence import recover_field


@dataclass(frozen=True, slots=True)
class MarketRow:
    id: int
    bond_id: int
    trade_date: object
    proofs: tuple
    decision_inputs: bool
    decision_ready: bool
    volume: bool
    turnover: bool
    trades: bool
    positive: bool
    source: str = "moex"

    def __getitem__(self,key):
        if key=="_endpoint_fields":return self.proofs
        if key=="_decision_inputs":return self.decision_inputs
        if key=="_decision_ready":return self.decision_ready
        return getattr(self,key)


TABLES={"bonds","bond_cashflow_events","bond_security_master_profiles","bond_security_master_evidence",
    "bond_legal_issuer_profiles","legal_issuers","credit_risk_source_artifacts","credit_rating_events"}


def compact_market_row(row,bond,profile):
    proof=tuple(recover_field(row,bond,field) for field in ("clean_price","price","nkd"))
    tokens=tuple((p.field,p.status,p.value if p.status=="RAW_RECOVERABLE" else None,p.source_fields,p.blockers) for p in proof)
    volume,turnover,trades=rca.original._liquidity(row["raw_payload"],set())
    return MarketRow(row["id"],row["bond_id"],row["trade_date"],tokens,
        rca.original._market_inputs(row),rca.original._market_ready(row,profile),
        volume is not None,turnover is not None,trades is not None,turnover is not None and turnover>0)


def read_evidence(db):
    with db.no_autoflush:
        tables={}
        for model,names in SPECS:
            name=model.__tablename__
            if name not in TABLES:continue
            names=tuple(n for n in names if n not in ("created_at","updated_at","ingestion_at","last_resolved_at","retrieved_at"))
            statement=select(*(getattr(model,n) for n in names)).order_by(*model.__mapper__.primary_key)
            if name=="bond_security_master_evidence":statement=statement.where(model.field_name=="coupon_frequency_per_year")
            if name=="bond_cashflow_events":statement=statement.where(model.source=="moex")
            tables[name]=tuple(dict(r) for r in db.execute(statement.execution_options(yield_per=1024)).mappings())
        bonds={r["id"]:r for r in tables["bonds"]};profiles={r["bond_id"]:r for r in tables["bond_security_master_profiles"]}
        names=("id","bond_id","trade_date","source","clean_price","price","nkd","yield_to_maturity","duration_years","raw_payload")
        market=BondMarketSnapshot
        statement=select(*(getattr(market,n) for n in names)).where(market.source=="moex").order_by(market.bond_id,market.trade_date,market.id)
        rows=[];dates=set()
        for result in db.execute(statement.execution_options(yield_per=1024)).mappings():
            row=dict(result);bid=row["bond_id"];rca.require(bid in bonds)
            rows.append(compact_market_row(row,bonds[bid],profiles.get(bid)))
            dates.add(row["trade_date"])
        tables["bond_market_snapshots"]=tuple(rows)
        return SimpleNamespace(tables=tables,decision_windows=(),market_date_counts=tuple({"trade_date":day} for day in sorted(dates)))


class WindowView:
    def __init__(self,index):self.index=index
    def __getitem__(self,day):
        from datetime import timedelta
        result={}
        for bid,days in self.index.distinct_days.items():
            a,b=bisect_left(days,day-timedelta(days=30)),bisect_left(days,day)
            if b>a:result[bid]={"observation_days":b-a}
        return result


class DecisionIndex(rca.EvidenceIndex):
    def __init__(self,data):
        # Reuse exact credit/issuer mapping without constructing outcome intervals
        # or generic decision-window materialization.
        empty=SimpleNamespace(tables={**data.tables,"bond_market_snapshots":()},decision_windows=())
        super().__init__(empty)
        self.data=data;self.history=defaultdict(list)
        self.snapshot_by_id={row.id:row for row in data.tables["bond_market_snapshots"]}
        for row in data.tables["bond_market_snapshots"]:self.history[row.bond_id].append(row)
        self.days={bid:tuple(r.trade_date for r in rows) for bid,rows in self.history.items()}
        self.distinct_days={bid:tuple(dict.fromkeys(days)) for bid,days in self.days.items()}
        self.liquidity={};self.volume_prefix={}
        for bid,rows in self.history.items():
            prefix=[(0,0,0)];vol=[0]
            for row in rows:
                a,b,c=prefix[-1];prefix.append((a+row.turnover,b+row.trades,c+row.positive));vol.append(vol[-1]+row.volume)
            self.liquidity[bid]=tuple(prefix);self.volume_prefix[bid]=tuple(vol)
        self.windows=WindowView(self)
