"""Task308B2 operator interface. Only PROBE can make a confirmed network request."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from app.schemas.historical_cashflow_feasibility import PilotPlan, Authorization
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_cashflow_feasibility_service import HistoricalCashflowFeasibilityService, publish
from app.services.historical_cashflow_probe_service import HistoricalCashflowProbeService
from app.services.historical_identity_probe_service import read_model, encode, atomic, safe_path
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_identity_feasibility_service import require
from app.services.historical_identity_recovery_artifacts import disjoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",required=True,choices=("PLAN","AUTHORIZE","PROBE","EVALUATE"))
    parser.add_argument("--b1-root")
    parser.add_argument("--expected-b1-report-sha256")
    parser.add_argument("--pilot-size",type=int)
    for name in ("task307-report","task308a-plan","task308a-authorization","task308a-evidence-root","task308a-evaluation","task306a4-manifest"):
        parser.add_argument("--"+name)
    parser.add_argument("--secids",nargs="+")
    parser.add_argument("--tables",nargs="+")
    parser.add_argument("--plan")
    parser.add_argument("--reviewed-plan-sha256")
    parser.add_argument("--authorization")
    parser.add_argument("--limits",help="Explicit JSON ProbeLimits for AUTHORIZE")
    parser.add_argument("--evidence-root")
    parser.add_argument("--output")
    parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--confirm-network",action="store_true")
    args = parser.parse_args(argv)
    try:
        require(not args.confirm_network or args.mode=="PROBE","NETWORK_CONFIRMATION_WRONG_MODE")
        require(not args.confirm or args.mode=="AUTHORIZE","AUTHORIZATION_CONFIRMATION_WRONG_MODE")
        replay_keys=("task307_report","task308a_plan","task308a_authorization","task308a_evidence_root","task308a_evaluation")
        replay_values={k:getattr(args,k) for k in replay_keys}
        optional_replay=None
        if any(replay_values.values()) or args.task306a4_manifest:
            require(args.mode=="PLAN" and all(replay_values.values()),"COMPLETE_OPTIONAL_REPLAY_INPUTS_REQUIRED")
            optional_replay=dict(replay_values,task306a4_manifest=args.task306a4_manifest)
        if args.mode == "PLAN":
            if not (args.b1_root and args.expected_b1_report_sha256 and args.pilot_size is not None and args.output):
                raise ValueError()
            result = HistoricalCashflowFeasibilityService.plan(b1_root=args.b1_root,expected_b1_report_sha256=args.expected_b1_report_sha256,
                pilot_size=args.pilot_size,candidate_secids=args.secids,optional_replay=optional_replay)
            if result.status == "PLANNED":
                require(result == HistoricalCashflowFeasibilityService.plan(b1_root=args.b1_root,expected_b1_report_sha256=args.expected_b1_report_sha256,
                    pilot_size=args.pilot_size,candidate_secids=args.secids,optional_replay=optional_replay),"INPUT_EVIDENCE_CHANGED")
            protected=(args.b1_root,)
            if optional_replay:
                protected+=tuple(v if k=="task308a_evidence_root" else Path(v).parent for k,v in optional_replay.items() if v is not None)
            publish(report=result,output_dir=args.output,protected_roots=protected)
        else:
            plan = read_model(args.plan,PilotPlan)
            service = HistoricalCashflowProbeService()
            if args.mode == "AUTHORIZE":
                if not (args.output and args.limits and args.secids and args.tables):raise ValueError()
                result = service.authorize(plan=plan,reviewed_plan_sha256=args.reviewed_plan_sha256,secids=args.secids,tables=args.tables,
                    limits=read_model(args.limits,ProbeLimits),evidence_root=args.evidence_root,confirm=args.confirm)
                destination = safe_path(args.output)
                require(disjoint(destination.parent,safe_path(args.plan).parent),"AUTHORIZATION_OUTPUT_OVERLAPS_INPUT")
                require(not any((p/n).exists() for p in (destination.parent,*destination.parent.parents) for n in
                    ("scope.json","task308b1-identity-audit.json","task308a-scope.json")),"AUTHORIZATION_OUTPUT_INSIDE_SOURCE")
                # Authorization is a private immutable file, never a probe or acquisition.
                atomic(destination,encode(result),immutable=True)
            else:
                auth = read_model(args.authorization,Authorization)
                if args.mode == "PROBE":
                    result = service.probe(plan=plan,authorization=auth,evidence_root=args.evidence_root,confirm_network=args.confirm_network)
                else:
                    if not args.output:raise ValueError()
                    result = HistoricalCashflowFeasibilityService.evaluate(plan=plan,authorization=auth,evidence_root=args.evidence_root)
                    publish(report=result,output_dir=args.output,protected_roots=(args.evidence_root,Path(args.plan).parent,Path(args.authorization).parent))
        sys.stdout.buffer.write(encode(result)+b"\n")
        return 2 if getattr(result,"status",None)=="BLOCKED" else 0
    except (IdentityError,ValueError,OSError,TypeError):
        sys.stdout.buffer.write(b'{"status":"BLOCKED","blockers":["OPERATION_REFUSED"],"pit_ready":false,"db_mutation":false}\n')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
