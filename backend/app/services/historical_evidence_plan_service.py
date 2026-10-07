"""Read-only archive/projection planning with exact, lossless storage gates."""
import hashlib
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func
from app.models.historical_evidence_foundation import (
    HistoricalEvidenceRun as Run, HistoricalEvidenceWorkItem as Work, HistoricalEvidenceSourcePage as Page,
    HistoricalEvidenceSecurity as Security, HistoricalEvidenceObservation as Observation,
    HistoricalEvidenceApplicationReceipt as Receipt,
)
from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_market_snapshot import BondMarketSnapshot as Market
from app.models.bond_cashflow_event import BondCashflowEvent as Cashflow
from app.schemas.historical_evidence_foundation import (
    HistoricalAcquisitionPlan, HistoricalProjectionPlan, HistoricalProjectionAction, BoardBinding, HistoricalEvidencePolicy,
    RepresentativeBinding,
)
from app.services.historical_evidence_discovery import acquisition_queries
from app.services.historical_evidence_normalization import checked, signed, safe_json, sha, ids, fits, hash_syntax, raw_date, normalize, json_bytes, LIMIT
from app.schemas.historical_evidence_foundation import HistoricalSourcePage
from app.services.historical_audit_canonical_json import chunks

ARCHIVE_TABLES=(Run,Work,Page,Security,Observation)


def run_key(policy,source_manifest_sha256,representatives=()):
    content={"policy":policy,"source_manifest_sha256":source_manifest_sha256}
    if representatives:content["representatives"]=tuple(sorted(representatives,key=lambda r:(r.secid,r.isin,r.bond_id,r.snapshot_id)))
    return sha(content)


def state_hash(db,run_sha256):
    h=hashlib.sha256()
    run=db.execute(select(Run.id,Run.policy_json,Run.source_manifest_sha256,Run.scope_json).where(Run.run_sha256==run_sha256)).first()
    if run is None:return sha({})
    for part in chunks(tuple(run)):h.update(part.encode("ascii"))
    for model in (Work,Page,Observation,Receipt):
        statement=select(model)
        if model is Work:statement=statement.where(Work.run_id==run.id)
        elif model is Page:statement=statement.join(Work,Page.work_id==Work.id).where(Work.run_id==run.id)
        elif model is Observation:statement=statement.join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id).where(Work.run_id==run.id)
        else:statement=statement.where(Receipt.run_id==run.id)
        # Only compact hash/state columns; no full raw history object graph.
        columns=[model.id]
        if model is Work:columns.extend((Work.query_sha256,Work.next_offset,Work.status,Work.failure_code))
        if model is Page:columns.extend((Page.page_sha256,Page.offset))
        if model is Observation:columns.extend((Observation.row_sha256,Observation.security_id))
        if model is Receipt:columns.extend((Receipt.batch_sha256,Receipt.plan_sha256,Receipt.before_sha256,Receipt.after_sha256,Receipt.receipt_json))
        for row in db.execute(statement.with_only_columns(*columns).order_by(model.id).execution_options(yield_per=1 if model is Receipt else 1000)):
            for part in chunks(tuple(row)):h.update(part.encode("ascii"))
    return h.hexdigest()


def fresh(factory):
    db=factory()
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise ValueError("SESSION_NOT_FRESH")
    return db


def policy_from_json(value):return HistoricalEvidencePolicy.model_validate_json(__import__("json").dumps(value))


def model_values(row):return safe_json({c.name:getattr(row,c.name) for c in row.__table__.columns if c.name not in ("created_at","updated_at")})


def verify_observation(db,observation,*,frozen=None):
    page=db.get(Page,observation.page_id)
    if frozen is None:frozen=checked(HistoricalSourcePage.model_validate_json(__import__("json").dumps(page.page_json)),"page_sha256")
    if page.page_sha256!=frozen.page_sha256 or not 0<=observation.ordinal<len(frozen.rows):raise ValueError("ARCHIVE_SOURCE_BINDING_DRIFT")
    raw=frozen.rows[observation.ordinal];mapped=normalize(frozen.query,raw)
    expected=safe_json({k:v for k,v in mapped.items() if k!="raw"})
    if observation.family!=frozen.query.family or observation.event_date!=mapped["event_date"] or observation.raw_json!=safe_json(raw) or observation.normalized_json!=expected:
        raise ValueError("ARCHIVE_MAPPING_DRIFT")
    if sha({"normalized":expected,"raw":safe_json(raw)})!=observation.row_sha256:raise ValueError("ARCHIVED_OBSERVATION_TAMPERED")
    security=db.get(Security,observation.security_id) if observation.security_id is not None else None
    if mapped["secid"] is not None:
        if security is None or (security.secid,security.isin)!=(mapped["secid"],mapped["isin"]):raise ValueError("ARCHIVE_SECURITY_BINDING_DRIFT")
    elif security is not None:raise ValueError("ARCHIVE_SECURITY_BINDING_DRIFT")


def reconcile(current,values):
    if current is None:return "CREATE",()
    conflicts=[];enrichment=False
    for field,new in values.items():
        if field=="raw_payload":continue
        old=getattr(current,field)
        if new is None:continue
        if old is None:enrichment=True
        elif isinstance(old,Decimal):
            if old!=Decimal(str(new)):conflicts.append(field)
        elif isinstance(old,date):
            if old!=raw_date(new):conflicts.append(field)
        elif old!=new:conflicts.append(field)
    return ("CONFLICT",tuple("CANONICAL_VALUE_CONFLICT:"+f for f in sorted(conflicts))) if conflicts else ("ENRICH_NULL" if enrichment else "RETAIN",())


def storage_errors(values,family):
    errors=[]
    for field,value in values.items():
        if value is None or field=="raw_payload":continue
        if field in ("price","clean_price","dirty_price","nkd","amount") and not fits(value,18,6):errors.append("STORAGE_UNREPRESENTABLE:"+field)
        if field=="volume" and not fits(value,18,2):errors.append("STORAGE_UNREPRESENTABLE:"+field)
        if field in ("yield_to_maturity","duration_years") and not fits(value,7,3):errors.append("STORAGE_UNREPRESENTABLE:"+field)
        if field=="amount_percent" and not fits(value,12,6):errors.append("STORAGE_UNREPRESENTABLE:"+field)
    for field in ("price","clean_price","dirty_price","nkd","duration_years","volume","amount","amount_percent"):
        if values.get(field) is not None:
            try:
                number=Decimal(str(values[field]))
                if not number.is_finite() or number<0:errors.append("ECONOMIC_VALUE_INVALID:"+field)
            except Exception:errors.append("ECONOMIC_VALUE_INVALID:"+field)
    return tuple(sorted(set(errors)))


def _identity(db,secid,isin):
    rows=list(db.execute(select(Bond).where((Bond.secid==secid)|(Bond.isin==isin))).scalars())
    if not rows:return None,()
    if len(rows)!=1 or rows[0].secid!=secid or rows[0].isin!=isin:return None,("BOND_IDENTITY_COLLISION",)
    return rows[0],()


def _issuer(db,run_id,secid,isin):
    # Exact reference rows and description observations are current, never PIT binding.
    rows=db.execute(select(Observation).join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id)
        .join(Security,Observation.security_id==Security.id).where(Work.run_id==run_id,Security.secid==secid,
        (Security.isin==isin)|Security.isin.is_(None),Observation.family.in_(("REFERENCE","DESCRIPTION")))
        .order_by(Observation.id).execution_options(yield_per=100)).scalars()
    refs=[];metadata={};conflict=False
    for observation in rows:
        verify_observation(db,observation)
        normalized=observation.normalized_json
        raw=normalized["values"]
        if normalized["source_table"]=="description":
            name=raw.get("name");v=raw.get("value")
            if type(name) is str:
                name=name.upper()
                if name in metadata and metadata[name]!=v:conflict=True
                metadata[name]=v
        else:
            if raw.get("secid",raw.get("SECID"))==secid and raw.get("isin",raw.get("ISIN"))==isin:
                title=raw.get("issuer_title",raw.get("ISSUER_TITLE"));inn=raw.get("issuer_inn",raw.get("ISSUER_INN"))
                if type(title) is str and title.strip() and type(inn) is str and inn.strip():refs.append((title,inn.strip()))
    if conflict or len(set(refs))!=1:return None,None,{},("ISSUER_OR_METADATA_UNAVAILABLE_OR_CONFLICT",)
    title,inn=refs[0]
    if len(title)>255 or len(inn)>16:return None,None,{},("ISSUER_STORAGE_INVALID",)
    companies=list(db.execute(select(Company).where((Company.inn==inn)|(Company.ticker=="MOEX_"+inn)|(Company.name==title))).scalars())
    if any(c.inn!=inn for c in companies) or len({c.id for c in companies})>1:return title,inn,{},("COMPANY_COLLISION",)
    name=metadata.get("NAME") or metadata.get("SECNAME") or metadata.get("SHORTNAME")
    nominal=metadata.get("FACEVALUE");currency=metadata.get("FACEUNIT")
    maturity=raw_date(metadata.get("MATDATE"))
    if metadata.get("ISIN")!=isin or metadata.get("SECID") not in (None,secid):return title,inn,{},("DESCRIPTION_IDENTITY_UNPROVEN",)
    if type(name) is not str or not name.strip() or len(name)>255 or currency!="RUB" or not fits(nominal,14,2):
        return title,inn,{},("BOND_ADMISSION_TERMS_UNAVAILABLE",)
    if nominal is None or Decimal(str(nominal))<=0:return title,inn,{},("BOND_ADMISSION_TERMS_UNAVAILABLE",)
    return title,inn,{"name":name,"currency":"RUB","nominal_value":str(nominal),"maturity_date":maturity.isoformat() if maturity else None,
                      "signal":"insufficient_data","is_perpetual":False,"is_floating_coupon":False,"is_subordinated":False},()


class HistoricalEvidencePlanService:
    def __init__(self,session_factory):self.factory=session_factory

    def research_policy(self,*,cutoff=None):
        """Freeze an explicit cutoff or the persisted MOEX frontier, SELECT only."""
        if cutoff is not None and type(cutoff) is not date:raise ValueError("EXACT_CUTOFF_DATE_REQUIRED")
        if cutoff is None:
            with fresh(self.factory) as db,db.no_autoflush:
                cutoff=db.execute(select(func.max(Market.trade_date)).where(Market.source=="moex")).scalar_one()
        if cutoff is None:raise ValueError("PERSISTED_MOEX_FRONTIER_UNAVAILABLE")
        return HistoricalEvidencePolicy(cutoff=cutoff)

    def plan_acquisition(self,*,discovery,representatives=()):
        checked(discovery,"source_manifest_sha256")
        if type(representatives) not in (tuple,list):raise ValueError("FROZEN_REPRESENTATIVES_REQUIRED")
        for representative in representatives:
            if type(representative) is not RepresentativeBinding:raise ValueError("FROZEN_REPRESENTATIVES_REQUIRED")
            RepresentativeBinding.model_validate({k:getattr(representative,k) for k in RepresentativeBinding.model_fields})
            hash_syntax(representative.source_audit_sha256)
            if not any(s.secid==representative.secid and s.isin==representative.isin for s in discovery.securities):
                raise ValueError("REPRESENTATIVE_NOT_IN_DISCOVERY")
        if len({(r.secid,r.isin) for r in representatives})!=len(representatives):raise ValueError("DUPLICATE_REPRESENTATIVE")
        queries=tuple(acquisition_queries(discovery,representatives));key=run_key(discovery.policy,discovery.source_manifest_sha256,representatives)
        with fresh(self.factory) as db,db.no_autoflush:
            for r in representatives:
                match=db.execute(select(Market.bond_id,Market.trade_date,Bond.secid,Bond.isin).join(Bond,Market.bond_id==Bond.id)
                    .where(Market.id==r.snapshot_id,Market.source=="moex")).first()
                if match is None or tuple((match.bond_id,match.secid,match.isin))!=(r.bond_id,r.secid,r.isin) or not 0<=(r.selection_date-match.trade_date).days<=7:
                    raise ValueError("REPRESENTATIVE_EXACT_BINDING_INVALID")
            run=db.execute(select(Run.id).where(Run.run_sha256==key)).scalar_one_or_none()
            work={} if run is None else {r.query_sha256:r for r in db.execute(select(Work).where(Work.run_id==run)).scalars()}
            queries=tuple(q.model_copy(update={"offset":work[sha(q)].next_offset}) if sha(q) in work else q
                          for q in queries if sha(q) not in work or work[sha(q)].status!="COMPLETE")
            return signed(HistoricalAcquisitionPlan,"plan_sha256",policy=discovery.policy,source_manifest_sha256=discovery.source_manifest_sha256,
                queries=queries,representatives=tuple(sorted(representatives,key=lambda r:(r.secid,r.isin,r.bond_id,r.snapshot_id))),current_db_sha256=state_hash(db,key),status="EXECUTABLE")

    def resume_plan(self,*,discovery,representatives=()):
        from app.services.historical_evidence_audit_service import HistoricalEvidenceAuditService
        key=run_key(discovery.policy,discovery.source_manifest_sha256,representatives)
        with fresh(self.factory) as db,db.no_autoflush:
            exists=db.execute(select(Run.id).where(Run.run_sha256==key)).scalar_one_or_none()
        if exists is not None and HistoricalEvidenceAuditService(self.factory).audit(run_sha256=key).status!="COMPLETE":
            raise ValueError("RESUME_RECEIPT_RECONCILIATION_FAILED")
        return self.plan_acquisition(discovery=discovery,representatives=representatives)

    def plan_projection(self,*,run_sha256,observation_ids,bindings):
        hash_syntax(run_sha256);selected=ids(observation_ids)
        if len(selected)>1000:raise ValueError("BATCH_ROW_LIMIT_EXCEEDED")
        if type(bindings) not in (tuple,list) or not bindings or any(type(b) is not BoardBinding for b in bindings):raise ValueError("BOARD_BINDINGS_REQUIRED")
        for b in bindings:BoardBinding.model_validate({k:getattr(b,k) for k in BoardBinding.model_fields})
        if len({(b.secid,b.isin,b.start,b.end) for b in bindings})!=len(bindings):raise ValueError("DUPLICATE_BOARD_BINDING")
        bindings=tuple(sorted(bindings,key=lambda b:(b.secid,b.isin,b.start,b.end,b.board)))
        with fresh(self.factory) as db,db.no_autoflush:return self._projection(db,run_sha256,selected,bindings)

    def _projection(self,db,run_sha256,selected,bindings):
        run=db.execute(select(Run).where(Run.run_sha256==run_sha256)).scalar_one()
        policy=policy_from_json(run.policy_json);actions=[];errors=set();seen=set();state=[];payload_size=0
        for oid in selected:
            row=db.execute(select(Observation,Security).join(Security,Observation.security_id==Security.id)
                .join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id)
                .where(Observation.id==oid,Work.run_id==run.id)).first()
            if row is None:raise ValueError("OBSERVATION_NOT_IN_RUN")
            observation,security=row;data=observation.normalized_json
            verify_observation(db,observation)
            if sha({"normalized":data,"raw":observation.raw_json})!=observation.row_sha256:raise ValueError("ARCHIVED_OBSERVATION_TAMPERED")
            if observation.family not in ("MARKET","CASHFLOWS"):raise ValueError("NON_PROJECTABLE_OBSERVATION")
            secid,isin=security.secid,security.isin;day=observation.event_date;blockers=[]
            if isin is None:
                candidates=[b for b in bindings if b.secid==secid and day and b.start<=day<=b.end]
                if len(candidates)==1:
                    proposed=candidates[0].isin
                    refs=db.execute(select(Observation.normalized_json).join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id)
                        .where(Work.run_id==run.id,Observation.family=="REFERENCE")).scalars()
                    if any(r["values"].get("secid",r["values"].get("SECID"))==secid and
                           r["values"].get("isin",r["values"].get("ISIN"))==proposed for r in refs):isin=proposed
                    else:blockers.append("REFERENCE_EXACT_IDENTITY_UNPROVEN")
            if not isin or len(isin)!=12 or len(secid)>32:blockers.append("EXACT_IDENTITY_UNAVAILABLE")
            if day is None or not policy.history_start<=day<=policy.cutoff:blockers.append("EVENT_OUTSIDE_FROZEN_RANGE")
            applicable=[b for b in bindings if b.secid==secid and b.isin==isin and day and b.start<=day<=b.end]
            if len(applicable)!=1:blockers.append("BOARD_BINDING_AMBIGUOUS_OR_MISSING")
            else:
                b=applicable[0]
                listing=db.execute(select(Observation,Security).join(Security,Observation.security_id==Security.id)
                    .join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id)
                    .where(Observation.id==b.listing_observation_id,Work.run_id==run.id)).first()
                if listing is None or listing[0].family!="LISTING" or listing[1].secid!=secid or listing[1].isin not in (None,isin):blockers.append("LISTING_BINDING_INVALID")
                else:
                    verify_observation(db,listing[0])
                    if sha({"normalized":listing[0].normalized_json,"raw":listing[0].raw_json})!=listing[0].row_sha256:
                        blockers.append("LISTING_EVIDENCE_TAMPERED")
                    terms=listing[0].normalized_json["values"];start=raw_date(terms.get("start"));end=raw_date(terms.get("end"))
                    if terms.get("board")!=b.board or start is None or b.start<start or end is not None and b.end>end:blockers.append("LISTING_PERIOD_UNPROVEN")
                if observation.family=="MARKET" and data["board"]!=b.board:blockers.append("SOURCE_BOARD_CONFLICT")
            bond,identity_errors=_identity(db,secid,isin);blockers.extend(identity_errors)
            title=inn=None;new_bond={}
            if bond is None and not identity_errors:
                title,inn,new_bond,issuer_errors=_issuer(db,run.id,secid,isin);blockers.extend(issuer_errors)
            values=data["values"].copy();values.pop("diagnostics",None)
            if observation.family=="MARKET":
                values={k:v for k,v in values.items() if k in ("price","clean_price","dirty_price","nkd","yield_to_maturity","duration_years","volume","raw_payload")}
                target=None if bond is None else db.execute(select(Market).where(Market.bond_id==bond.id,Market.trade_date==day,Market.source=="moex")).scalar_one_or_none()
                natural=(secid,isin,day,"MARKET")
            else:
                values={k:v for k,v in values.items() if k in ("event_type","amount","amount_percent","currency")}
                if values.get("event_type")=="offer_redemption":blockers.append("OFFER_NOT_AUTOMATIC")
                if values.get("amount") is None or values.get("currency")!="RUB":blockers.append("CASHFLOW_AMOUNT_OR_CURRENCY_INVALID")
                target=None if bond is None else db.execute(select(Cashflow).where(Cashflow.bond_id==bond.id,Cashflow.event_date==day,Cashflow.event_type==values["event_type"],Cashflow.source=="moex")).scalar_one_or_none()
                natural=(secid,isin,day,values["event_type"])
            if natural in seen:blockers.append("DUPLICATE_SOURCE_NATURAL_KEY")
            seen.add(natural);blockers.extend(storage_errors(values,observation.family))
            action,reasons=reconcile(target,values);blockers.extend(reasons)
            if blockers:action="STORAGE_UNREPRESENTABLE" if any(s.startswith("STORAGE_UNREPRESENTABLE") for s in blockers) else "CONFLICT" if reasons or identity_errors else "BLOCKED"
            before=model_values(target) if target else {}
            payload_size+=len(json_bytes(data))+len(json_bytes(before))
            if payload_size>LIMIT:raise ValueError("PROJECTION_BATCH_PAYLOAD_LIMIT_EXCEEDED")
            state.append({"observation":observation.row_sha256,"target":before,"bond":model_values(bond) if bond else {},
                          "companies":tuple(model_values(c) for c in db.execute(select(Company).where(Company.inn==inn)).scalars()) if inn else ()})
            if new_bond:values["_new_bond"]=new_bond
            actions.append(HistoricalProjectionAction(observation_id=oid,family=observation.family,secid=secid,isin=isin or "",bond_id=bond.id if bond else None,
                target_id=target.id if target else None,action=action,values=values,before_sha256=sha(before),issuer_title=title,issuer_inn=inn,blockers=tuple(sorted(set(blockers))),
                diagnostics=("CURRENT_ISSUER_LINKAGE_NOT_HISTORICAL_PROOF","MODEL_DEFAULT_NOT_EVIDENCE") if new_bond else ()))
            errors.update(blockers)
        # A shared issuer with incompatible authoritative names blocks the whole batch.
        issuer_names={}
        for action in actions:
            if action.issuer_inn:
                issuer_names.setdefault(action.issuer_inn,set()).add(action.issuer_title)
        if any(len(names)>1 for names in issuer_names.values()):errors.add("ISSUER_GROUP_NAME_CONFLICT")
        return signed(HistoricalProjectionPlan,"plan_sha256",policy=policy,source_manifest_sha256=run.source_manifest_sha256,run_sha256=run_sha256,
            bindings=bindings,observation_ids=selected,actions=tuple(actions),current_db_sha256=sha({"archive":state_hash(db,run_sha256),"targets":state}),
            status="BLOCKED" if errors else "EXECUTABLE",blockers=tuple(sorted(errors)))
