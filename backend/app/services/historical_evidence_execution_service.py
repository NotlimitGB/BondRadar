"""Fresh-session bounded archive writes and separately authorized projection."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select, text
from app.models.historical_evidence_foundation import (
    HistoricalEvidenceRun as Run, HistoricalEvidenceWorkItem as Work, HistoricalEvidenceSourcePage as Page,
    HistoricalEvidenceSecurity as Security, HistoricalEvidenceObservation as Observation,
    HistoricalEvidenceApplicationReceipt as Receipt,
)
from app.models.bond import Bond
from app.models.company import Company
from app.models.bond_market_snapshot import BondMarketSnapshot as Market
from app.models.bond_cashflow_event import BondCashflowEvent as Cashflow
from app.schemas.historical_evidence_foundation import HistoricalBatchReceipt, HistoricalAuthorization
from app.services.historical_evidence_plan_service import HistoricalEvidencePlanService, fresh, run_key, state_hash, model_values, verify_observation
from app.services.historical_evidence_normalization import checked, safe_json, sha, normalize, json_bytes, LIMIT, raw_date
from app.services.historical_evidence_source_client import HistoricalSourceError


def scope(plan):
    return sha(plan.queries) if hasattr(plan,"queries") else sha({"ids":plan.observation_ids,"bindings":plan.bindings})


def authorize(plan,authorization,operation):
    checked(plan,"plan_sha256")
    if type(authorization) is not HistoricalAuthorization:raise ValueError("AUTHORIZATION_REQUIRED")
    HistoricalAuthorization.model_validate({k:getattr(authorization,k) for k in type(authorization).model_fields})
    expected=(operation,True,plan.plan_sha256,plan.source_manifest_sha256,plan.current_db_sha256,scope(plan))
    actual=(authorization.operation,authorization.explicit_authorization,authorization.plan_sha256,authorization.source_manifest_sha256,authorization.current_db_sha256,authorization.scope_sha256)
    if actual!=expected or plan.status!="EXECUTABLE":raise ValueError("AUTHORIZATION_OR_PLAN_MISMATCH")


def lock(db,operation):
    dialect=db.bind.dialect.name
    if dialect=="sqlite":db.execute(text("BEGIN IMMEDIATE"))
    elif dialect=="postgresql":
        tables=("historical_evidence_runs","historical_evidence_work_items","historical_evidence_source_pages",
            "historical_evidence_securities","historical_evidence_observations","historical_evidence_application_receipts")
        if operation=="APPLY":tables+=("companies","bonds","bond_market_snapshots","bond_cashflow_events")
        for table in tables:db.execute(text("LOCK TABLE "+table+" IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
    else:raise ValueError("APPLY_DIALECT_NOT_SUPPORTED")


def failed(operation,plan,code,*,attempted=0,unknown=False,rollback=False,partitions=(),source_attempts=None):
    return HistoricalBatchReceipt(operation=operation,status="COMMIT_OUTCOME_UNKNOWN" if unknown else "ROLLED_BACK" if attempted else "BLOCKED",
        plan_sha256=getattr(plan,"plan_sha256",None),attempted_mutations=attempted,committed_mutations=None if unknown else 0,
        commit_count=None if unknown else 0,db_mutated=None if unknown else False,rollback_confirmed=rollback and not unknown,blockers=(code,),
        failed_partition_sha256s=partitions,source_attempt_count=source_attempts)


def decode_values(values,model):
    result={}
    for key,value in values.items():
        if key.startswith("_"):continue
        column=model.__table__.columns.get(key)
        if column is None:raise ValueError("UNSUPPORTED_PERSISTED_FIELD")
        if value is not None:
            from sqlalchemy import Numeric, Date
            if isinstance(column.type,Numeric):value=Decimal(str(value))
            elif isinstance(column.type,Date):value=raw_date(value)
        result[key]=value
    return result


class HistoricalEvidenceExecutionService:
    def __init__(self,session_factory,*,source_client=None):self.factory=session_factory;self.source_client=source_client

    def acquire_batch(self,*,reviewed_plan,authorization,source_pages=None,queries=None):
        plan=reviewed_plan;requested=()
        try:
            authorize(plan,authorization,"ACQUIRE")
            if source_pages is None:
                if self.source_client is None:raise ValueError("SOURCE_CLIENT_REQUIRED")
                requested=tuple(queries) if queries is not None else plan.queries[:1]
                if not requested or len(requested)>10 or any(q not in plan.queries for q in requested):raise ValueError("QUERY_OUTSIDE_PLAN")
                # Fetch entirely before starting any database mutation.
                fetched=[];row_count=0;payload_size=0
                for q in requested:
                    try:page=self.source_client.fetch_page(q)
                    except HistoricalSourceError as exc:
                        return failed("ACQUIRE",plan,exc.code,partitions=(sha(q),),source_attempts=exc.attempts)
                    row_count+=len(page.rows);payload_size+=len(json_bytes(page))
                    if row_count>1000 or payload_size>LIMIT:raise ValueError("ACQUISITION_BATCH_LIMIT_EXCEEDED")
                    fetched.append(page)
                source_pages=tuple(fetched)
            if type(source_pages) not in (tuple,list) or not source_pages:raise ValueError("SOURCE_PAGES_REQUIRED")
            if sum(len(p.rows) for p in source_pages)>1000:raise ValueError("BATCH_ROW_LIMIT_EXCEEDED")
            if sum(len(json_bytes(safe_json(p))) for p in source_pages)>LIMIT:raise ValueError("BATCH_PAYLOAD_LIMIT_EXCEEDED")
            normalized=[];seen=set();payload_size=0
            for page in source_pages:
                checked(page,"page_sha256")
                if page.query not in plan.queries or page.query in seen:raise ValueError("QUERY_OUTSIDE_PLAN_OR_DUPLICATED")
                seen.add(page.query)
                if (page.complete and page.next_offset is not None) or (not page.complete and (type(page.next_offset) is not int or page.next_offset<=page.query.offset)):
                    raise ValueError("SOURCE_CONTINUATION_INVALID")
                rows=[]
                for raw in page.rows:
                    mapped=normalize(page.query,raw);payload_size+=len(json_bytes(mapped))
                    if payload_size>LIMIT:raise ValueError("NORMALIZED_BATCH_PAYLOAD_LIMIT_EXCEEDED")
                    rows.append(mapped)
                normalized.append((page,tuple(rows)))
            key=run_key(plan.policy,plan.source_manifest_sha256,plan.representatives)
            batch=sha({"run":key,"pages":tuple({"query":p.query,"rows":p.rows,"complete":p.complete,"next":p.next_offset,"total":p.cursor_total} for p in source_pages)})
            db=fresh(self.factory)
        except HistoricalSourceError as exc:return failed("ACQUIRE",plan,exc.code,partitions=tuple(sorted(sha(q) for q in requested)),source_attempts=exc.attempts)
        except (ValueError,TypeError,AttributeError):return failed("ACQUIRE",plan,"ACQUISITION_INPUT_OR_AUTHORIZATION_INVALID")
        attempted=0;committing=False
        try:
            with db.no_autoflush:
                lock(db,"ACQUIRE")
                previous=db.execute(select(Receipt).where(Receipt.batch_sha256==batch)).scalar_one_or_none()
                if previous:
                    self._audit_archive_receipt(db,previous)
                    return HistoricalBatchReceipt(operation="ACQUIRE",status="IDEMPOTENT_NOOP",batch_sha256=batch,plan_sha256=plan.plan_sha256)
                if state_hash(db,key)!=plan.current_db_sha256:raise ValueError("DB_STATE_DRIFT")
                run=db.execute(select(Run).where(Run.run_sha256==key)).scalar_one_or_none()
                if run is None:
                    run=Run(run_sha256=key,source_manifest_sha256=plan.source_manifest_sha256,history_start=plan.policy.history_start,
                        history_end=plan.policy.cutoff,policy_json=safe_json(plan.policy),planned_work_count=len(plan.queries),
                        scope_json={"queries":safe_json(plan.queries),"representatives":safe_json(plan.representatives)})
                    db.add(run);db.flush();attempted+=1
                page_ids=[]
                for page,rows in normalized:
                    root=page.query.model_copy(update={"offset":0});qhash=sha(root)
                    work=db.execute(select(Work).where(Work.run_id==run.id,Work.query_sha256==qhash)).scalar_one_or_none()
                    if qhash not in {sha(q) for q in run.scope_json["queries"]}:raise ValueError("PARTITION_NOT_IN_FROZEN_RUN")
                    if work is None:
                        if page.query.offset!=0:raise ValueError("MISSING_PREVIOUS_PAGE")
                        work=Work(run_id=run.id,query_sha256=qhash,query_json=safe_json(root),next_offset=0,status="PENDING")
                        db.add(work);db.flush();attempted+=1
                    if work.next_offset!=page.query.offset or work.status=="COMPLETE":raise ValueError("CHECKPOINT_DRIFT")
                    prior=db.execute(select(Page).where(Page.work_id==work.id).order_by(Page.offset).execution_options(yield_per=1)).scalars()
                    hashes={sha(r) for r in page.rows};prior_total=None
                    if len(hashes)!=len(page.rows):raise ValueError("DUPLICATE_SOURCE_ROWS")
                    for old in prior:
                        content=old.page_json
                        if hashes&{sha(r) for r in content["rows"]}:raise ValueError("OVERLAPPING_SOURCE_ROWS")
                        if content["cursor_total"] is not None:prior_total=content["cursor_total"]
                    if prior_total is not None and prior_total!=page.cursor_total:raise ValueError("SOURCE_CURSOR_CHANGED_OR_MISSING")
                    record=Page(work_id=work.id,offset=page.query.offset,page_sha256=page.page_sha256,page_json=safe_json(page),observed_at=page.observed_at)
                    db.add(record);db.flush();attempted+=1;page_ids.append(record.id)
                    for ordinal,data in enumerate(rows):
                        sec=None
                        if data["secid"]:
                            identity=sha({"secid":data["secid"],"isin":data["isin"]})
                            sec=db.execute(select(Security).where(Security.identity_sha256==identity)).scalar_one_or_none()
                            if sec is None:
                                sec=Security(identity_sha256=identity,secid=data["secid"],isin=data["isin"]);db.add(sec);db.flush();attempted+=1
                        normalized_json=safe_json({k:v for k,v in data.items() if k!="raw"})
                        observation=Observation(page_id=record.id,security_id=sec.id if sec else None,ordinal=ordinal,family=page.query.family,
                            event_date=data["event_date"],effective_date=data["event_date"],available_at=None,proof_state=data["proof_state"],
                            normalized_json=normalized_json,raw_json=data["raw"],row_sha256=sha({"normalized":normalized_json,"raw":data["raw"]}))
                        db.add(observation);db.flush();attempted+=1
                    work.next_offset=page.next_offset if page.next_offset is not None else page.query.offset+len(page.rows)
                    work.status="COMPLETE" if page.complete else "PENDING";work.failure_code=None
                    db.flush();attempted+=1
                after=state_hash(db,key)
                receipt=Receipt(run_id=run.id,batch_sha256=batch,operation="ACQUIRE",plan_sha256=plan.plan_sha256,before_sha256=plan.current_db_sha256,
                    after_sha256=after,receipt_json={"page_ids":page_ids,"mutations":attempted})
                db.add(receipt);db.flush();self._audit_archive_receipt(db,receipt)
                committing=True;db.commit()
        except Exception:
            try:db.rollback();rollback=True
            except Exception:rollback=False
            return failed("ACQUIRE",plan,"ARCHIVE_COMMIT_OUTCOME_UNKNOWN" if committing else "ARCHIVE_BATCH_REJECTED",attempted=attempted,unknown=committing or not rollback,rollback=rollback)
        finally:db.close()
        return self._post_archive(batch,plan,attempted)

    @staticmethod
    def _audit_archive_receipt(db,receipt):
        run=db.get(Run,receipt.run_id);pages=[]
        if not run or len(set(receipt.receipt_json["page_ids"]))!=len(receipt.receipt_json["page_ids"]):raise ValueError("ARCHIVE_RECEIPT_DRIFT")
        for pid in receipt.receipt_json["page_ids"]:
            page=db.get(Page,pid)
            if page is None:raise ValueError("ARCHIVE_PAGE_MISSING")
            from app.schemas.historical_evidence_foundation import HistoricalSourcePage
            frozen=checked(HistoricalSourcePage.model_validate_json(__import__("json").dumps(page.page_json)),"page_sha256")
            work=db.get(Work,page.work_id)
            if not work or work.run_id!=run.id:raise ValueError("ARCHIVE_RECEIPT_RUN_DRIFT")
            pages.append({"query":frozen.query,"rows":frozen.rows,"complete":frozen.complete,"next":frozen.next_offset,"total":frozen.cursor_total})
            if page.page_sha256!=frozen.page_sha256 or page.offset!=frozen.query.offset:raise ValueError("ARCHIVE_PAGE_DRIFT")
            rows=db.execute(select(Observation).where(Observation.page_id==pid).order_by(Observation.ordinal)).scalars()
            count=0
            for row in rows:
                verify_observation(db,row,frozen=frozen)
                if sha({"normalized":row.normalized_json,"raw":row.raw_json})!=row.row_sha256:raise ValueError("ARCHIVE_ROW_DRIFT")
                if row.ordinal!=count or row.raw_json!=safe_json(frozen.rows[count]):raise ValueError("ARCHIVE_SOURCE_BINDING_DRIFT")
                expected=normalize(frozen.query,frozen.rows[count])
                if row.normalized_json!=safe_json({k:v for k,v in expected.items() if k!="raw"}):raise ValueError("ARCHIVE_MAPPING_DRIFT")
                count+=1
            if count!=len(page.page_json["rows"]):raise ValueError("ARCHIVE_ROW_COUNT_MISMATCH")
        if sha({"run":run.run_sha256,"pages":tuple(pages)})!=receipt.batch_sha256:raise ValueError("ARCHIVE_RECEIPT_CONTENT_DRIFT")

    def _post_archive(self,batch,plan,attempted):
        try:
            with fresh(self.factory) as db,db.no_autoflush:
                receipt=db.execute(select(Receipt).where(Receipt.batch_sha256==batch)).scalar_one()
                self._audit_archive_receipt(db,receipt)
        except Exception:
            return HistoricalBatchReceipt(operation="ACQUIRE",status="POST_COMMIT_AUDIT_FAILED",plan_sha256=plan.plan_sha256,batch_sha256=batch,
                attempted_mutations=attempted,committed_mutations=attempted,db_mutated=True,commit_count=1,blockers=("ARCHIVE_POST_COMMIT_AUDIT_FAILED",))
        return HistoricalBatchReceipt(operation="ACQUIRE",status="COMMITTED",plan_sha256=plan.plan_sha256,batch_sha256=batch,
            attempted_mutations=attempted,committed_mutations=attempted,db_mutated=True,commit_count=1)

    def apply_batch(self,*,reviewed_plan,authorization):
        plan=reviewed_plan
        try:authorize(plan,authorization,"APPLY");db=fresh(self.factory)
        except (ValueError,TypeError):return failed("APPLY",plan,"PROJECTION_INPUT_OR_AUTHORIZATION_INVALID")
        batch=sha({"operation":"APPLY","plan":plan.plan_sha256});attempted=0;committing=False
        try:
            with db.no_autoflush:
                lock(db,"APPLY")
                rebuilt=HistoricalEvidencePlanService(self.factory)._projection(db,plan.run_sha256,plan.observation_ids,plan.bindings)
                if rebuilt!=plan:raise ValueError("PROJECTION_STATE_DRIFT")
                if all(a.action=="RETAIN" for a in plan.actions):
                    return HistoricalBatchReceipt(operation="APPLY",status="IDEMPOTENT_NOOP",batch_sha256=batch,plan_sha256=plan.plan_sha256)
                run=db.execute(select(Run).where(Run.run_sha256==plan.run_sha256)).scalar_one()
                bonds={};changes=[]
                for action in plan.actions:
                    bond=db.get(Bond,action.bond_id) if action.bond_id else bonds.get((action.secid,action.isin))
                    if bond is None:
                        company=db.execute(select(Company).where(Company.inn==action.issuer_inn)).scalar_one_or_none()
                        if company is None:
                            company=Company(inn=action.issuer_inn,name=action.issuer_title,ticker="MOEX_"+action.issuer_inn,signal="insufficient_data")
                            db.add(company);db.flush();attempted+=1
                        bond=Bond(company_id=company.id,secid=action.secid,isin=action.isin,**decode_values(action.values["_new_bond"],Bond))
                        db.add(bond);db.flush();attempted+=1;bonds[(action.secid,action.isin)]=bond
                        security=db.execute(select(Security).where(Security.secid==action.secid,Security.isin==action.isin)).scalar_one()
                        security.bond_id=bond.id;db.flush()
                    model=Market if action.family=="MARKET" else Cashflow
                    row=db.get(model,action.target_id) if action.target_id else None
                    observation=db.get(Observation,action.observation_id)
                    values=decode_values(action.values,model)
                    before=model_values(row) if row else {}
                    if row is None:
                        values.update(bond_id=bond.id,source="moex")
                        values["trade_date" if model is Market else "event_date"]=observation.event_date
                        if model is Cashflow:values["raw_payload"]={"moex":observation.raw_json,"historical_evidence_observation_id":observation.id}
                        row=model(**values);db.add(row)
                    elif action.action=="ENRICH_NULL":
                        for field,value in values.items():
                            if field!="raw_payload" and value is not None and getattr(row,field) is None:setattr(row,field,value)
                    db.flush();attempted+=action.action!="RETAIN"
                    changes.append({"family":action.family,"id":row.id,"before":before,"after":model_values(row),"observation_id":observation.id})
                # Audit persisted values, not the identity map's pre-bind Decimals.
                # In particular SQLite Numeric can lose precision via its adapter.
                db.expire_all()
                result=HistoricalEvidencePlanService(self.factory)._projection(db,plan.run_sha256,plan.observation_ids,plan.bindings)
                if result.status!="EXECUTABLE" or any(a.action!="RETAIN" for a in result.actions):raise ValueError("PROJECTION_PRE_COMMIT_AUDIT_FAILED")
                receipt=Receipt(run_id=run.id,batch_sha256=batch,operation="APPLY",plan_sha256=plan.plan_sha256,before_sha256=plan.current_db_sha256,
                    after_sha256=result.current_db_sha256,receipt_json={"changes":changes,"mutations":attempted})
                db.add(receipt);db.flush();committing=True;db.commit()
        except Exception:
            try:db.rollback();rollback=True
            except Exception:rollback=False
            return failed("APPLY",plan,"PROJECTION_COMMIT_OUTCOME_UNKNOWN" if committing else "PROJECTION_BATCH_REJECTED",attempted=attempted,unknown=committing or not rollback,rollback=rollback)
        finally:db.close()
        try:
            with fresh(self.factory) as db,db.no_autoflush:
                final=HistoricalEvidencePlanService(self.factory)._projection(db,plan.run_sha256,plan.observation_ids,plan.bindings)
                if final.status!="EXECUTABLE" or any(a.action!="RETAIN" for a in final.actions):raise ValueError("PROJECTION_POST_COMMIT_AUDIT_FAILED")
        except Exception:
            return HistoricalBatchReceipt(operation="APPLY",status="POST_COMMIT_AUDIT_FAILED",plan_sha256=plan.plan_sha256,batch_sha256=batch,
                attempted_mutations=attempted,committed_mutations=attempted,db_mutated=True,commit_count=1,blockers=("PROJECTION_POST_COMMIT_AUDIT_FAILED",))
        return HistoricalBatchReceipt(operation="APPLY",status="COMMITTED",plan_sha256=plan.plan_sha256,batch_sha256=batch,
            attempted_mutations=attempted,committed_mutations=attempted,db_mutated=True,commit_count=1)
