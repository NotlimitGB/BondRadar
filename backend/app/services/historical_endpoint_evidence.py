"""Pure persisted endpoint inspection; no pricing, acquisition or mutation."""
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from app.schemas.multi_horizon_historical_backtestability import (
    RawFieldRecoverabilityV1 as Field, SnapshotEndpointReadinessV1 as Endpoint,
    HistoricalBondHorizonReadinessV1 as BondHorizon,
)
from app.services.moex_nkd_repair_service import MoexNkdRepairService as Nkd
from app.services.moex_market_data_service import MoexMarketDataService as Mapper

HORIZONS=(90,180,365)

def finite(value):
    return type(value) is Decimal and value.is_finite()

def price_valid(value):
    return finite(value) and value>0

def terms_ready(profile):
    return bool(profile and profile["currency_state"]=="verified" and profile["currency_code"]=="RUB" and
        profile["nominal_state"]=="verified" and finite(profile["nominal_value"]) and profile["nominal_value"]>0)

def supplied(value):
    return value is not None and not (isinstance(value,str) and not value.strip())

def mapping_values(row,names):
    return [(str(k),v) for k,v in row.items() if isinstance(k,str) and k.casefold() in {n.casefold() for n in names} and supplied(v)]

def raw_binding(snapshot,bond):
    payload=snapshot["raw_payload"]
    if not isinstance(payload,Mapping) or not isinstance(payload.get("moex"),Mapping):return None,None,("RAW_PAYLOAD_MISSING",)
    raw=payload["moex"];canonical=payload.get("canonical")
    errors=[]
    for name,expected in (("SECID",bond["secid"]),("TRADEDATE",snapshot["trade_date"])):
        values=mapping_values(raw,(name,))
        if not values:errors.append("RAW_"+name+"_MISSING");continue
        for _,v in values:
            ok=(isinstance(v,str) and isinstance(expected,str) and v.strip().upper()==expected.strip().upper()) if name=="SECID" else Nkd._parse_trade_date(v)==expected
            if not ok:errors.append("RAW_"+name+"_MISMATCH")
    if isinstance(canonical,Mapping):
        for name,expected in (("secid",bond["secid"]),("trade_date",snapshot["trade_date"])):
            for _,v in mapping_values(canonical,(name,)):
                ok=(isinstance(v,str) and isinstance(expected,str) and v.strip().upper()==expected.strip().upper()) if name=="secid" else Nkd._parse_trade_date(v)==expected
                if not ok:errors.append("RAW_HISTORY_IDENTITY_CONFLICT")
    if snapshot["source"]!="moex":errors.append("SOURCE_NOT_MOEX")
    return raw,canonical if isinstance(canonical,Mapping) else None,tuple(sorted(set(errors)))

def first_price(row,names,history=False):
    for name in names:
        values=mapping_values(row,(name,))
        if not values:continue
        parsed=[]
        for field,value in values:
            if isinstance(value,bool):return None,(field,),"RAW_INVALID"
            v=Mapper._history_decimal(value,field,warnings=[],bond_id=0,secid="",trade_date=None) if history else Mapper._decimal(value,field,[],None)
            if not price_valid(v):return None,(field,),"RAW_INVALID"
            parsed.append(v)
        if len(set(parsed))>1:return None,tuple(sorted(k for k,_ in values)),"RAW_CONFLICT"
        return parsed[0],tuple(sorted(k for k,_ in values)),"RAW_RECOVERABLE"
    return None,(),"RAW_MISSING"

def recover_field(snapshot,bond,name):
    existing=snapshot[name];valid=price_valid(existing) if name!="nkd" else finite(existing) and existing>=0
    if existing is not None:
        return Field(field=name,status="ALREADY_CANONICAL" if valid else "RAW_INVALID",value=existing if valid else None,
            blockers=() if valid else ("CANONICAL_VALUE_INVALID",))
    raw,canonical,errors=raw_binding(snapshot,bond)
    if errors:return Field(field=name,status="RAW_INVALID" if raw else "RAW_MISSING",blockers=errors)
    if name=="nkd":
        values=mapping_values(raw,("ACCINT","ACCRUEDINT"))
        parsed=[Nkd._parse_nkd(v) for _,v in values]
        fields=tuple(sorted(k for k,_ in values))
        status="RAW_MISSING" if not values else "RAW_INVALID" if any(v is None for v in parsed) else "RAW_CONFLICT" if len(set(parsed))>1 else "RAW_RECOVERABLE"
        value=parsed[0] if status=="RAW_RECOVERABLE" else None
        if canonical and supplied(canonical.get("accrued_interest")):
            other=Nkd._parse_nkd(canonical["accrued_interest"])
            if other is None:status,value="RAW_INVALID",None
            elif value is not None and other!=value:status,value="RAW_CONFLICT",None
            elif status=="RAW_MISSING":status,value="RAW_RECOVERABLE",other
        return Field(field=name,status=status,value=value,source_fields=fields)
    names=("LEGALCLOSEPRICE",) if name=="clean_price" else ("CLOSE","WAPRICE","PRICE")
    history_names=("legal_close_price",) if name=="clean_price" else ("close_price","market_price","weighted_average_price","last_price")
    value,fields,status=first_price(raw,names)
    if canonical:
        other,other_fields,other_status=first_price(canonical,history_names,True)
        if other_status in ("RAW_INVALID","RAW_CONFLICT"):status,value=other_status,None
        elif status=="RAW_RECOVERABLE" and other_status=="RAW_RECOVERABLE" and value!=other:status,value="RAW_CONFLICT",None
        elif status=="RAW_MISSING":value,fields,status=other,other_fields,other_status
    return Field(field=name,status=status,value=value,source_fields=fields)

class EndpointIndex:
    def __init__(self,rows,profiles,bonds):
        self.profiles=profiles;self.bonds=bonds;self.rows={};self.days={};self.cache={}
        daily={}
        for row in rows:
            key=(row["bond_id"],row["trade_date"])
            if row["source"]=="moex" and (key not in daily or row["id"]>daily[key]["id"]):daily[key]=row
        for (bid,day),row in sorted(daily.items()):self.rows.setdefault(bid,[]).append(row)
        for bid,values in self.rows.items():self.days[bid]=tuple(r["trade_date"] for r in values)

    def endpoint(self,bid,target,kind):
        dates=self.days.get(bid,())
        pos=(bisect_left(dates,target) if kind=="ENTRY" else bisect_right(dates,target))-1
        terms=terms_ready(self.profiles.get(bid));errors=[]
        if not terms:errors.append("CURRENT_DIAGNOSTIC_TERMS_UNAVAILABLE")
        if pos<0:return Endpoint(bond_id=bid,target_date=target,kind=kind,current_terms_ready=terms,blockers=tuple(sorted(errors+["SNAPSHOT_MISSING"])))
        row=self.rows[bid][pos];age=(target-row["trade_date"]).days;fresh=0<=age<=7
        if not fresh:errors.append("SNAPSHOT_STALE")
        compact=hasattr(row,"proofs")
        if compact:
            fields=tuple(Field(field=f,status=s,value=v,source_fields=sf,blockers=b) for f,s,v,sf,b in row["_endpoint_fields"])
        else:
            if row["id"] not in self.cache:self.cache[row["id"]]=tuple(recover_field(row,self.bonds[bid],name) for name in ("clean_price","price","nkd"))
            fields=self.cache[row["id"]]
        canonical_basis=next((f.field for f in fields[:2] if f.status=="ALREADY_CANONICAL"),None) if compact else next((name for name in ("clean_price","price") if price_valid(row[name])),None)
        recovered_basis=next((f.field for f in fields[:2] if f.status in ("ALREADY_CANONICAL","RAW_RECOVERABLE")),None)
        nkd=fields[2].status=="ALREADY_CANONICAL" if compact else finite(row["nkd"]) and row["nkd"]>=0
        recovered_nkd=fields[2].status in ("ALREADY_CANONICAL","RAW_RECOVERABLE")
        if canonical_basis is None:errors.append("PRICE_UNAVAILABLE")
        if not nkd:errors.append("NKD_UNAVAILABLE")
        return Endpoint(bond_id=bid,target_date=target,kind=kind,snapshot_id=row["id"],trade_date=row["trade_date"],age_days=age,
            price_basis=canonical_basis,recovered_price_basis=recovered_basis,current_terms_ready=terms,fresh=fresh,
            canonical_price_ready=canonical_basis is not None,canonical_nkd_ready=nkd,recoverable_price_ready=recovered_basis is not None,
            recoverable_nkd_ready=recovered_nkd,canonical_ready=bool(terms and fresh and canonical_basis and nkd),
            after_raw_recovery_ready=bool(terms and fresh and recovered_basis and recovered_nkd),fields=fields,blockers=tuple(sorted(errors)))

class CashflowIndex:
    """Prefix diagnostics and bisection; no event rescan per horizon."""
    def __init__(self,events):
        self.rows=sorted(events,key=lambda r:(r["event_date"],{"coupon":0,"amortization":1,"redemption":2}.get(r["event_type"],3),r["id"]))
        self.dates=tuple(r["event_date"] for r in self.rows);self.redemptions=[];self.redeemed_dates=[]
        self.prefix=[Counter()];seen=set();redeemed=False
        for row in self.rows:
            counts=self.prefix[-1].copy();kind=row["event_type"];identity=(row["event_date"],kind)
            valid=row["currency"]=="RUB" and finite(row["amount"]) and row["amount"]>=0
            if kind=="offer_redemption":counts["OFFER_NOT_AUTOMATIC_CASH"]+=1
            else:
                if identity in seen:counts["DUPLICATE_AUTOMATIC_EVENT"]+=1
                if redeemed:counts["AUTOMATIC_EVENT_AFTER_REDEMPTION"]+=1
                if kind not in ("coupon","amortization","redemption"):counts["UNSUPPORTED_AUTOMATIC_EVENT"]+=1
                if not valid:counts["CASHFLOW_AMOUNT_OR_CURRENCY_INVALID"]+=1
                if kind=="coupon":counts["COUPON"]+=1;counts["INVALID_COUPON"]+=(not valid or identity in seen or redeemed)
                if kind=="redemption":
                    self.redeemed_dates.append(row["event_date"])
                    if valid:self.redemptions.append(row["event_date"]);redeemed=True
            seen.add(identity);self.prefix.append(counts)

    def inspect(self,start,end,maturity):
        a,b=bisect_right(self.dates,start),bisect_right(self.dates,end)
        counts={k:self.prefix[b][k]-self.prefix[a][k] for k in self.prefix[b]}
        errors={k for k,n in counts.items() if n and k not in ("COUPON","INVALID_COUPON","OFFER_NOT_AUTOMATIC_CASH")}
        if self.redeemed_dates and self.redeemed_dates[0]<=start:errors.add("REDEMPTION_ON_OR_BEFORE_ENTRY")
        pos=bisect_right(self.redemptions,start)
        redemption=self.redemptions[pos] if pos<len(self.redemptions) and self.redemptions[pos]<=end else None
        if maturity is not None and start<maturity<=end and redemption is None:errors.add("MATURITY_REDEMPTION_EVIDENCE_MISSING")
        diagnostic={"CONTRACTUAL_CASHFLOW_COMPLETENESS_UNPROVEN"}
        if not b-a:diagnostic.add("ZERO_PERSISTED_EVENTS")
        if counts.get("OFFER_NOT_AUTOMATIC_CASH",0):diagnostic.add("OFFER_NOT_AUTOMATIC_CASH")
        return redemption,tuple(sorted(errors)),tuple(sorted(diagnostic)),b-a,counts.get("COUPON",0),not counts.get("INVALID_COUPON",0)

def bond_horizon(index,flows,bid,day,horizon):
    entry=index.endpoint(bid,day,"ENTRY");end=day+timedelta(days=horizon)
    redemption,errors,diagnostic,event_count,coupons,coupon_valid=flows.inspect(day,end,index.bonds[bid]["maturity_date"])
    terminal=None if redemption else index.endpoint(bid,end,"TERMINAL")
    terminal_ready=redemption is not None or terminal.canonical_ready
    terminal_recovered=redemption is not None or terminal.after_raw_recovery_ready
    blockers=set(errors)|set(entry.blockers)|(set(terminal.blockers) if terminal else set())
    return BondHorizon(bond_id=bid,entry=entry,terminal=terminal,terminal_path="REDEEMED" if redemption else "MARKET",redemption_date=redemption,
        cashflow_events_valid=not errors,persisted_event_count=event_count,persisted_coupon_event_count=coupons,coupon_events_valid=coupon_valid,
        coupon_baseline_observable=coupons>0 and coupon_valid,zero_persisted_events=event_count==0,
        canonical_ready=entry.canonical_ready and terminal_ready and not errors,
        after_raw_recovery_ready=entry.after_raw_recovery_ready and terminal_recovered and not errors,
        blockers=tuple(sorted(blockers)),diagnostics=diagnostic)
