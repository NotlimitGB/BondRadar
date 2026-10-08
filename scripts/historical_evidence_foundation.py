"""Explicitly authorized offline artifacts and bounded historical evidence operations."""
import argparse
import json
from pathlib import Path
import sys
import httpx
from datetime import date
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
from app.schemas.historical_discovery_checkpoint import DiscoveryLimits, DiscoveryReadAuthorization, DiscoveryProgress
from app.services.historical_discovery_checkpoint import (
    authorize, CheckpointStore, VerifiedDiscoveryIndex, run_discovery, probe,
    finalize, atomic_json, safe_path,
)

SOURCE_MODES=("DISCOVERY_POLICY","DISCOVERY_AUTHORIZE","DISCOVERY_PROBE","DISCOVER","DISCOVERY_RESUME",
              "DISCOVERY_PROGRESS","DISCOVERY_VALIDATE","DISCOVERY_FINALIZE","DISCOVERY_RECHECK","DISCOVERY_SUMMARY")


def source_operation(args, source_client, stream):
    policy=read(args.policy,HistoricalEvidencePolicy) if args.policy else HistoricalEvidencePolicy(cutoff=date(2026,9,30))
    if policy.cutoff!=date(2026,9,30):raise ValueError("DISCOVERY_FROZEN_CUTOFF_REQUIRED")
    if args.mode=="DISCOVERY_POLICY":return policy
    if args.evidence_root is None:raise ValueError("EXPLICIT_DISCOVERY_ROOT_REQUIRED")
    limits=DiscoveryLimits(max_requests=args.max_requests,max_pages=args.max_pages,max_seconds=args.max_seconds)
    if args.mode=="DISCOVERY_AUTHORIZE":
        if not args.confirm_network:raise ValueError("EXPLICIT_SOURCE_READ_AUTHORIZATION_REQUIRED")
        return authorize(policy=policy,evidence_root=args.evidence_root,limits=limits,probe_dates=tuple(args.probe_dates or ()))
    if args.mode in ("DISCOVER","DISCOVERY_RESUME","DISCOVERY_PROBE"):
        if not args.confirm_network:raise ValueError("EXPLICIT_NETWORK_CONFIRMATION_REQUIRED")
        authorization=read(args.authorization,DiscoveryReadAuthorization)
        operation=probe if args.mode=="DISCOVERY_PROBE" else run_discovery
        return operation(source_client=source_client,policy=policy,evidence_root=args.evidence_root,authorization=authorization,limits=limits)
    store=CheckpointStore(args.evidence_root,policy)
    if args.mode=="DISCOVERY_FINALIZE":return finalize(store)
    with store.lock():
        if args.mode=="DISCOVERY_RECHECK":
            if not args.confirm_recheck or not args.partition_sha256:raise ValueError("EXPLICIT_PARTITION_RECHECK_REQUIRED")
            return store.recheck(args.partition_sha256)
        result=store.validate(deep=args.mode=="DISCOVERY_VALIDATE")
        if args.mode=="DISCOVERY_SUMMARY":
            print(f"{result.status}: completed={result.completed_partition_count}/{result.requested_partition_count}; "
                  f"failed={result.failed_partition_count}; absent_dates={result.absent_partition_count}; "
                  f"pages={result.accepted_page_count}; requests={result.http_attempt_count}; "
                  "PIT=false; acquisition_ready=false",file=sys.stderr)
        return result


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
    parser.add_argument("--mode",choices=("REPORT","ACQUISITION_PLAN","ACQUIRE","PROJECTION_PLAN","APPLY","RESUME_PLAN",*SOURCE_MODES),default="REPORT")
    parser.add_argument("--run-sha256");parser.add_argument("--policy",type=Path);parser.add_argument("--discovery",type=Path)
    parser.add_argument("--plan",type=Path);parser.add_argument("--authorization",type=Path);parser.add_argument("--bindings",type=Path)
    parser.add_argument("--observation-ids",type=int,nargs="+");parser.add_argument("--output",type=Path)
    parser.add_argument("--confirm-network",action="store_true");parser.add_argument("--confirm-apply",action="store_true")
    parser.add_argument("--evidence-root",type=Path)
    parser.add_argument("--max-requests",type=int,default=100);parser.add_argument("--max-pages",type=int,default=100)
    parser.add_argument("--max-seconds",type=int,default=120)
    parser.add_argument("--probe-dates",type=date.fromisoformat,nargs="+")
    parser.add_argument("--partition-sha256");parser.add_argument("--confirm-recheck",action="store_true")
    args=parser.parse_args(argv);engine=None;owned_client=None;stream=stdout or sys.stdout
    if args.mode=="REPORT" and (args.evidence_root is not None or (args.run_sha256 is None and session_factory is None)):
        args.mode="DISCOVERY_PROGRESS"
    mutation=False;network=False;result=None
    try:
        if args.mode in SOURCE_MODES and args.output:
            if args.evidence_root is None:raise ValueError("EXPLICIT_DISCOVERY_ROOT_REQUIRED")
            root=safe_path(args.evidence_root);output=safe_path(args.output)
            if output.parent!=root or output.suffix!=".json" or output.name in ("scope.json","state.json","manifest.json",".lock"):
                raise ValueError("DISCOVERY_OUTPUT_INVALID")
        network_modes=("DISCOVER","DISCOVERY_RESUME","DISCOVERY_PROBE","ACQUIRE")
        if args.mode in network_modes and not args.confirm_network:raise ValueError("EXPLICIT_NETWORK_CONFIRMATION_REQUIRED")
        if args.mode in ("ACQUIRE","APPLY") and not args.confirm_apply:raise ValueError("EXPLICIT_APPLY_CONFIRMATION_REQUIRED")
        if args.mode in network_modes and source_client is None:
            owned_client=httpx.Client(trust_env=False,follow_redirects=False,timeout=30)
            source_client=HistoricalEvidenceSourceClient(owned_client)
        if args.mode in SOURCE_MODES:
            result=source_operation(args,source_client,stream)
            network=getattr(result,"network_access",False)
        else:
            if session_factory is None:
                from app.core.config import settings
                engine=create_engine(settings.DATABASE_URL,pool_pre_ping=True);session_factory=sessionmaker(engine,expire_on_commit=False)
            planner=HistoricalEvidencePlanService(session_factory)
            if args.mode=="REPORT":result=HistoricalEvidenceAuditService(session_factory).audit(run_sha256=args.run_sha256)
            elif args.mode in ("ACQUISITION_PLAN","RESUME_PLAN"):
                operation=planner.resume_plan if args.mode=="RESUME_PLAN" else planner.plan_acquisition
                if args.evidence_root is not None:
                    policy=read(args.policy,HistoricalEvidencePolicy) if args.policy else HistoricalEvidencePolicy(cutoff=date(2026,9,30))
                    discovery=VerifiedDiscoveryIndex(args.evidence_root,policy)
                else:discovery=read(args.discovery,HistoricalDiscovery)
                result=operation(discovery=discovery)
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
            if args.mode in SOURCE_MODES:
                if args.evidence_root is None:raise ValueError("EXPLICIT_DISCOVERY_ROOT_REQUIRED")
                root=safe_path(args.evidence_root);output=safe_path(args.output)
                if output.parent!=root:raise ValueError("DISCOVERY_OUTPUT_OUTSIDE_ROOT")
                root.mkdir(mode=0o700,exist_ok=True)
                if output.name in ("scope.json","state.json","manifest.json",".lock") or output.suffix!=".json":
                    raise ValueError("DISCOVERY_OUTPUT_RESERVED")
                atomic_json(output,result)
            else:
                with args.output.open("w",encoding="utf-8",newline="\n") as output:write_json(result,output)
        else:write_json(result,stream)
        return 1 if getattr(result,"status",None) in ("BLOCKED","PARTIAL","ROLLED_BACK","COMMIT_OUTCOME_UNKNOWN","POST_COMMIT_AUDIT_FAILED",
            "SOURCE_FAILED","CHECKPOINT_INVALID","AUTHORIZATION_FAILED","EVIDENCE_INCONSISTENT") else 0
    except Exception:
        try:
            if args.mode in SOURCE_MODES:
                write_json(DiscoveryProgress(status="CHECKPOINT_INVALID",blockers=("HISTORICAL_DISCOVERY_OPERATION_FAILED",),network_access=network),stream)
            else:write_json({"status":"BLOCKED","blockers":["HISTORICAL_FOUNDATION_OPERATION_FAILED"],"pit_ready":False,
                "db_mutation":mutation,"network_access":network,"operation_result":result},stream)
        except OSError:pass
        return 1
    finally:
        if owned_client is not None:owned_client.close()
        if engine is not None:engine.dispose()


if __name__=="__main__":raise SystemExit(main())
