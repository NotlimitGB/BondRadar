"""Read-only archive integrity, coverage inventory and resumable-work diagnostics."""
from datetime import date
from sqlalchemy import select, func, text
from app.models.historical_evidence_foundation import (
    HistoricalEvidenceRun as Run, HistoricalEvidenceWorkItem as Work, HistoricalEvidenceSourcePage as Page,
    HistoricalEvidenceSecurity as Security, HistoricalEvidenceObservation as Observation,
    HistoricalEvidenceApplicationReceipt as Receipt,
)
from app.models.bond_market_snapshot import BondMarketSnapshot as Market
from app.models.bond_cashflow_event import BondCashflowEvent as Cashflow
from app.models.bond import Bond
from app.schemas.historical_evidence_foundation import HistoricalFoundationAudit
from app.services.historical_evidence_plan_service import fresh, policy_from_json, model_values, run_key
from app.schemas.historical_evidence_foundation import RepresentativeBinding
from app.services.historical_evidence_normalization import hash_syntax, signed, sha
from app.services.historical_evidence_execution_service import HistoricalEvidenceExecutionService

LIMITATIONS=("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN","SURVIVORSHIP_BIAS_RESIDUAL_EXPLICIT",
    "CURRENT_TERMS_AND_ISSUER_ARE_NOT_HISTORICAL_PROOF","CONTRACTUAL_HISTORY_COMPLETENESS_UNPROVEN",
    "HISTORICAL_PUBLICATION_AVAILABILITY_UNPROVEN","TASK272_AND_DURATION_MATCH_NOT_EVALUATED",
    "DATA_QUALIFIED_WINDOWS_REQUIRE_SEPARATELY_AUTHORIZED_A2H_RERUN")


class HistoricalEvidenceAuditService:
    def __init__(self,session_factory):self.factory=session_factory

    def audit(self,*,run_sha256):
        hash_syntax(run_sha256)
        try:
            with fresh(self.factory) as db,db.no_autoflush:
                if db.bind.dialect.name=="postgresql":db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                elif db.bind.dialect.name=="sqlite":db.execute(text("BEGIN"))
                else:raise ValueError("READ_ONLY_DIALECT_NOT_SUPPORTED")
                run=db.execute(select(Run).where(Run.run_sha256==run_sha256)).scalar_one_or_none()
                if run is None:raise ValueError("RUN_NOT_FOUND")
                policy=policy_from_json(run.policy_json)
                import json
                reps=tuple(RepresentativeBinding.model_validate_json(json.dumps(r)) for r in run.scope_json["representatives"])
                if run_key(policy,run.source_manifest_sha256,reps)!=run_sha256:raise ValueError("RUN_PROVENANCE_DRIFT")
                planned={sha(q) for q in run.scope_json["queries"]}
                if len(planned)!=run.planned_work_count:raise ValueError("RUN_SCOPE_COUNT_DRIFT")
                projections={"CREATE":0,"ENRICH_NULL":0,"RETAIN":0}
                receipts=db.execute(select(Receipt).where(Receipt.run_id==run.id).order_by(Receipt.id).execution_options(yield_per=100)).scalars()
                for receipt in receipts:
                    if receipt.operation=="ACQUIRE":HistoricalEvidenceExecutionService._audit_archive_receipt(db,receipt)
                    else:
                        for change in receipt.receipt_json["changes"]:
                            classification="CREATE" if not change["before"] else "RETAIN" if change["before"]==change["after"] else "ENRICH_NULL"
                            projections[classification]+=1
                            model=Market if change["family"]=="MARKET" else Cashflow
                            row=db.get(model,change["id"])
                            # Later authorized null enrichment is allowed; past nonnull facts must remain unchanged.
                            if row is None:raise ValueError("PROJECTED_ROW_MISSING")
                            current=model_values(row)
                            if any(v is not None and current.get(k)!=v for k,v in change["after"].items() if k!="raw_payload"):
                                raise ValueError("PROJECTED_NON_NULL_HISTORY_DRIFT")
                statement=select(Observation.family,func.count(),func.min(Observation.event_date),func.max(Observation.event_date)).join(Page,Observation.page_id==Page.id).join(Work,Page.work_id==Work.id).where(Work.run_id==run.id).group_by(Observation.family).order_by(Observation.family)
                groups=tuple(db.execute(statement));counts=tuple((g[0],g[1]) for g in groups);ranges=tuple((g[0],g[2],g[3]) for g in groups)
                market=next((r for r in ranges if r[0]=="MARKET"),("MARKET",None,None));last=market[2]
                complete=db.execute(select(func.count()).select_from(Work).where(Work.run_id==run.id,Work.status=="COMPLETE")).scalar_one()
                failed=db.execute(select(func.count()).select_from(Work).where(Work.run_id==run.id,Work.status=="FAILED")).scalar_one()
                states={r.query_sha256:r.status for r in db.execute(select(Work.query_sha256,Work.status).where(Work.run_id==run.id))}
                if set(states)-planned:raise ValueError("PARTITION_NOT_IN_FROZEN_SCOPE")
                pending=tuple(sorted(k for k in planned if states.get(k)!="COMPLETE"))
                failures=tuple(sorted(k for k,s in states.items() if s=="FAILED"))
                absent=tuple(sorted(db.execute(select(Work.query_sha256).join(Page,Page.work_id==Work.id)
                    .outerjoin(Observation,Observation.page_id==Page.id).where(Work.run_id==run.id,Work.status=="COMPLETE")
                    .group_by(Work.query_sha256).having(func.count(Observation.id)==0)).scalars()))
                unresolved=tuple(db.execute(select(Security.id).join(Observation,Observation.security_id==Security.id).join(Page,Observation.page_id==Page.id)
                    .join(Work,Page.work_id==Work.id).where(Work.run_id==run.id,
                    ~select(Bond.id).where(Bond.secid==Security.secid,Bond.isin==Security.isin).exists()).distinct().order_by(Security.id)).scalars())
                applications=db.execute(select(func.count()).select_from(Receipt).where(Receipt.run_id==run.id,Receipt.operation=="APPLY")).scalar_one()
                unresolved_rows=tuple(tuple(r) for r in db.execute(select(Security.id,Security.secid,Security.isin).where(Security.id.in_(unresolved)).order_by(Security.id))) if unresolved else ()
                slots=0
                for year in range(2020,2026):
                    for month in range(1,13):
                        entry=date(year,month,1)
                        if date(2020,10,1)<=entry<=date(2025,9,1) and last is not None and (last-entry).days>=365:slots+=1
                return signed(HistoricalFoundationAudit,"audit_sha256",status="COMPLETE",policy=policy,run_sha256=run_sha256,
                    observation_counts=counts,observed_range=(market[1],market[2]),family_ranges=ranges,
                    unresolved_security_count=len(unresolved),unresolved_security_ids=unresolved,unresolved_securities=unresolved_rows,pending_work_count=len(pending),
                    pending_partition_sha256s=pending,absent_partition_sha256s=absent,failed_partition_sha256s=failures,
                    failed_work_count=failed,application_count=applications,projection_counts=tuple(sorted(projections.items())),annual_calendar_slot_capacity=slots,limitations=LIMITATIONS)
        except Exception:
            return signed(HistoricalFoundationAudit,"audit_sha256",status="BLOCKED",run_sha256=run_sha256,
                blockers=("HISTORICAL_ARCHIVE_OR_PROJECTION_AUDIT_FAILED",),limitations=LIMITATIONS)
