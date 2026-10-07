"""Explicitly authorized offline artifacts and bounded historical evidence operations."""
import argparse
import json
from pathlib import Path
import sys
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.schemas.historical_evidence_foundation import (
    HistoricalEvidencePolicy, HistoricalAuthorization, HistoricalDiscovery,
    HistoricalAcquisitionPlan, HistoricalProjectionPlan, BoardBinding,
)
from app.services.historical_audit_canonical_json import write_json
from app.services.historical_evidence_audit_service import HistoricalEvidenceAuditService
from app.services.historical_evidence_plan_service import HistoricalEvidencePlanService
from app.services.historical_evidence_execution_service import HistoricalEvidenceExecutionService
from app.services.historical_evidence_discovery import HistoricalEvidenceDiscoveryService
from app.services.historical_evidence_source_client import HistoricalEvidenceSourceClient


def read_json(path):
    if path is None:raise ValueError("EXPLICIT_ARTIFACT_REQUIRED")
    if path.stat().st_size>8*1024*1024:raise ValueError("ARTIFACT_TOO_LARGE")
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("DUPLICATE_JSON_KEY")
            result[key]=value
        return result
    with path.open("rb") as source:content=source.read(8*1024*1024+1)
    if len(content)>8*1024*1024:raise ValueError("ARTIFACT_TOO_LARGE")
    return json.loads(content,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError("INVALID_JSON_NUMBER")))


def read(path,model):return model.model_validate_json(json.dumps(read_json(path)))


def main(argv=None,*,session_factory=None,source_client=None,stdout=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("REPORT","DISCOVER","ACQUISITION_PLAN","ACQUIRE","PROJECTION_PLAN","APPLY","RESUME_PLAN"),default="REPORT")
    parser.add_argument("--run-sha256");parser.add_argument("--policy",type=Path);parser.add_argument("--discovery",type=Path)
    parser.add_argument("--plan",type=Path);parser.add_argument("--authorization",type=Path);parser.add_argument("--bindings",type=Path)
    parser.add_argument("--observation-ids",type=int,nargs="+");parser.add_argument("--output",type=Path)
    parser.add_argument("--confirm-network",action="store_true");parser.add_argument("--confirm-apply",action="store_true")
    args=parser.parse_args(argv);engine=None;owned_client=None;stream=stdout or sys.stdout
    mutation=False;network=False;result=None
    try:
        if args.mode in ("DISCOVER","ACQUIRE") and not args.confirm_network:raise ValueError("EXPLICIT_NETWORK_CONFIRMATION_REQUIRED")
        if args.mode in ("ACQUIRE","APPLY") and not args.confirm_apply:raise ValueError("EXPLICIT_APPLY_CONFIRMATION_REQUIRED")
        if args.mode in ("DISCOVER","ACQUIRE") and source_client is None:
            owned_client=httpx.Client(trust_env=False,follow_redirects=False,timeout=30)
            source_client=HistoricalEvidenceSourceClient(owned_client)
        if args.mode=="DISCOVER":
            network=True
            result=HistoricalEvidenceDiscoveryService(source_client).discover(policy=read(args.policy,HistoricalEvidencePolicy),authorization=read(args.authorization,HistoricalAuthorization))
        else:
            if session_factory is None:
                from app.core.config import settings
                engine=create_engine(settings.DATABASE_URL,pool_pre_ping=True);session_factory=sessionmaker(engine,expire_on_commit=False)
            planner=HistoricalEvidencePlanService(session_factory)
            if args.mode=="REPORT":result=HistoricalEvidenceAuditService(session_factory).audit(run_sha256=args.run_sha256)
            elif args.mode in ("ACQUISITION_PLAN","RESUME_PLAN"):
                operation=planner.resume_plan if args.mode=="RESUME_PLAN" else planner.plan_acquisition
                result=operation(discovery=read(args.discovery,HistoricalDiscovery))
            elif args.mode=="PROJECTION_PLAN":
                content=read_json(args.bindings)
                bindings=tuple(BoardBinding.model_validate_json(json.dumps(b)) for b in content)
                result=planner.plan_projection(run_sha256=args.run_sha256,observation_ids=args.observation_ids,bindings=bindings)
            else:
                auth=read(args.authorization,HistoricalAuthorization)
                executor=HistoricalEvidenceExecutionService(session_factory,source_client=source_client)
                if args.mode=="ACQUIRE":
                    network=True;mutation=None
                    result=executor.acquire_batch(reviewed_plan=read(args.plan,HistoricalAcquisitionPlan),authorization=auth)
                else:
                    mutation=None
                    result=executor.apply_batch(reviewed_plan=read(args.plan,HistoricalProjectionPlan),authorization=auth)
                mutation=result.db_mutated
        if args.output:
            with args.output.open("w",encoding="utf-8",newline="\n") as output:write_json(result,output)
        else:write_json(result,stream)
        return 1 if result.status in ("BLOCKED","PARTIAL","ROLLED_BACK","COMMIT_OUTCOME_UNKNOWN","POST_COMMIT_AUDIT_FAILED") else 0
    except Exception:
        try:write_json({"status":"BLOCKED","blockers":["HISTORICAL_FOUNDATION_OPERATION_FAILED"],"pit_ready":False,
            "db_mutation":mutation,"network_access":network,"operation_result":result},stream)
        except OSError:pass
        return 1
    finally:
        if owned_client is not None:owned_client.close()
        if engine is not None:engine.dispose()


if __name__=="__main__":raise SystemExit(main())
