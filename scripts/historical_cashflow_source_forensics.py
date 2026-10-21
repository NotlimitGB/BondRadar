"""Task308B2R1: four explicit operations; only confirmed DIAGNOSTIC_PROBE can request source data."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from app.schemas.historical_cashflow_source_forensics import OriginalHashes, DiagnosticPlan, DiagnosticAuthorization, DiagnosticLimits, TABLES, OperationEnvelope
from app.services.historical_cashflow_source_forensics_service import HistoricalCashflowSourceForensicsService as Service, publish, ensure_separate_source_roots
from app.services.historical_cashflow_diagnostic_probe_service import HistoricalCashflowDiagnosticProbeService
from app.services.historical_identity_probe_service import read_model, safe_path, encode, atomic
from app.services.historical_identity_feasibility_service import IdentityError, require
from app.services.historical_identity_recovery_artifacts import disjoint


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",required=True,choices=("FORENSIC_PLAN","DIAGNOSTIC_AUTHORIZE","DIAGNOSTIC_PROBE","DIAGNOSTIC_EVALUATE"))
    for name in ("b2-plan","b2-authorization","b2-evidence-root","b2-evaluation","plan","authorization","reviewed-plan-sha256","limits","evidence-root","output"):
        parser.add_argument("--"+name)
    for name in ("plan","authorization","probe","evaluation"):
        parser.add_argument("--expected-b2-"+name+"-sha256")
    parser.add_argument("--secids",nargs="+")
    parser.add_argument("--tables",nargs="+")
    parser.add_argument("--profiles",nargs="+")
    parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--confirm-network",action="store_true")
    args=parser.parse_args(argv)
    try:
        require(not args.confirm_network or args.mode=="DIAGNOSTIC_PROBE","NETWORK_CONFIRMATION_WRONG_MODE")
        require(not args.confirm or args.mode=="DIAGNOSTIC_AUTHORIZE","AUTHORIZATION_CONFIRMATION_WRONG_MODE")
        if args.mode in ("DIAGNOSTIC_PROBE","DIAGNOSTIC_EVALUATE"):
            require(not any((args.secids,args.tables,args.profiles,args.limits,args.reviewed_plan_sha256)),"SCOPE_OVERRIDES_NOT_ALLOWED")
        if args.mode=="FORENSIC_PLAN":require(args.profiles is None,"FORENSIC_QUERY_PROFILES_ARE_FIXED")
        if args.mode=="FORENSIC_PLAN":
            require(args.output and args.secids,"EXPLICIT_FORENSIC_SCOPE_AND_OUTPUT_REQUIRED")
            hashes=OriginalHashes(plan_sha256=args.expected_b2_plan_sha256,authorization_sha256=args.expected_b2_authorization_sha256,
                probe_sha256=args.expected_b2_probe_sha256,evaluation_sha256=args.expected_b2_evaluation_sha256)
            kwargs=dict(b2_plan=args.b2_plan,b2_authorization=args.b2_authorization,b2_evidence_root=args.b2_evidence_root,
                b2_evaluation=args.b2_evaluation,expected=hashes,secids=args.secids,tables=tuple(args.tables or TABLES),
                limits=read_model(args.limits,DiagnosticLimits) if args.limits else DiagnosticLimits())
            result=Service.forensic_plan(**kwargs)
            if result.status=="COMPLETE":require(result==Service.forensic_plan(**kwargs),"ORIGINAL_EVIDENCE_CHANGED_BEFORE_PUBLICATION")
            roots=(args.b2_evidence_root,Path(args.b2_plan).parent,Path(args.b2_authorization).parent,Path(args.b2_evaluation).parent)
            publish(package=result,output_dir=args.output,protected_roots=roots)
        else:
            plan=read_model(args.plan,DiagnosticPlan)
            if args.mode in ("DIAGNOSTIC_AUTHORIZE","DIAGNOSTIC_PROBE"):
                require(disjoint(safe_path(args.evidence_root),safe_path(args.plan).parent),"DIAGNOSTIC_EVIDENCE_OVERLAPS_PLAN_INPUT")
            service=HistoricalCashflowDiagnosticProbeService()
            if args.mode=="DIAGNOSTIC_AUTHORIZE":
                require(args.output and args.limits and args.secids and args.tables and args.profiles,"EXPLICIT_DIAGNOSTIC_AUTHORIZATION_INPUTS_REQUIRED")
                result=service.authorize(plan=plan,reviewed_plan_sha256=args.reviewed_plan_sha256,secids=args.secids,tables=args.tables,
                    profiles=args.profiles,limits=read_model(args.limits,DiagnosticLimits),evidence_root=args.evidence_root,confirm=args.confirm)
                target=safe_path(args.output)
                ensure_separate_source_roots(plan=plan,output_root=target.parent)
                require(disjoint(target.parent,safe_path(args.plan).parent),"AUTHORIZATION_OUTPUT_OVERLAPS_PLAN")
                require(not any((p/n).exists() for p in (target.parent,*target.parent.parents) for n in
                    ("scope.json","b2-state.json","task308a-scope.json","task308b1-identity-audit.json")),"AUTHORIZATION_OUTPUT_INSIDE_ORIGINAL_SOURCE")
                atomic(target,encode(result),immutable=True)
            else:
                auth=read_model(args.authorization,DiagnosticAuthorization)
                if args.mode=="DIAGNOSTIC_PROBE":
                    result=service.probe(plan=plan,authorization=auth,evidence_root=args.evidence_root,confirm_network=args.confirm_network)
                else:
                    require(args.output,"EXPLICIT_DIAGNOSTIC_OUTPUT_REQUIRED")
                    result=Service.diagnostic_evaluate(plan=plan,authorization=auth,evidence_root=args.evidence_root)
                    publish(package=result,output_dir=args.output,protected_roots=(args.evidence_root,Path(args.plan).parent,Path(args.authorization).parent))
        status=getattr(result,"status","COMPLETE")
        envelope=OperationEnvelope(operation=args.mode,status=status if status in ("BLOCKED","PARTIAL") else "COMPLETE",
            blockers=getattr(result,"blockers",getattr(getattr(result,"report",None),"blockers",())),result=result)
        sys.stdout.buffer.write(encode(envelope)+b"\n")
        return 2 if envelope.status=="BLOCKED" else 0
    except (IdentityError,ValueError,OSError,TypeError):
        envelope=OperationEnvelope(operation=args.mode,status="BLOCKED",blockers=("DIAGNOSTIC_OPERATION_REFUSED",))
        sys.stdout.buffer.write(encode(envelope)+b"\n")
        return 2


if __name__=="__main__":raise SystemExit(main())
