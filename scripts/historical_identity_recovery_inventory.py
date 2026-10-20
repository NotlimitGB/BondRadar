"""Task308B1 standalone offline REPORT; no application DB or network operations."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from app.schemas.historical_identity_recovery_inventory import InventoryLimits, Expectations, OperationResult
from app.services.historical_identity_recovery_artifacts import verify_inputs, publish
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_audit_canonical_json import chunks


def main(argv=None,*,stdout=None,stderr=None):
    stdout=sys.stdout if stdout is None else stdout
    stderr=sys.stderr if stderr is None else stderr
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("REPORT",),default="REPORT")
    for name in ("task307-report","task308a-plan","task308a-authorization","task308a-evidence-root","task308a-evaluation","output-dir"):
        parser.add_argument("--"+name,required=True)
    parser.add_argument("--task306a4-manifest")
    parser.add_argument("--expected-task307-sha256")
    parser.add_argument("--expected-task308a-evaluation-sha256")
    parser.add_argument("--expected-manifest-sha256")
    expectation_names=("total_secids","format_valid","checksum_valid","checksum_invalid","probed","current_bindings","mismatched_bindings")
    for name in expectation_names:parser.add_argument("--expect-"+name.replace("_","-"),type=int)
    parser.add_argument("--output-bytes",type=int,default=32*1024**2)
    parser.add_argument("--max-identity-rows",type=int,default=100000)
    parser.add_argument("--working-row-bytes",type=int,default=1024**2)
    parser.add_argument("--probe-bytes",type=int,default=128*1024**2)
    args=parser.parse_args(argv)
    try:
        limits=InventoryLimits(output_bytes=args.output_bytes,max_identity_rows=args.max_identity_rows,
            working_row_bytes=args.working_row_bytes,probe_bytes=args.probe_bytes)
        expected=Expectations(**{n:getattr(args,"expect_"+n) for n in expectation_names})
        verified=verify_inputs(task307_report=args.task307_report,task308a_plan=args.task308a_plan,
            task308a_authorization=args.task308a_authorization,task308a_evidence_root=args.task308a_evidence_root,
            task308a_evaluation=args.task308a_evaluation,task306a4_manifest=args.task306a4_manifest,
            expected_task307_sha256=args.expected_task307_sha256,expected_task308a_evaluation_sha256=args.expected_task308a_evaluation_sha256,
            expected_manifest_sha256=args.expected_manifest_sha256,limits=limits,expectations=expected)
        result=publish(verified,output_dir=args.output_dir)
    except IdentityError as exc:
        result=OperationResult(status="BLOCKED",blockers=(exc.code,),resource_status="LIMIT_EXCEEDED" if "LIMIT" in exc.code or "RESERVE" in exc.code else "NOT_ESTABLISHED")
    except (ValueError,TypeError,OSError,KeyError,RecursionError,OverflowError):
        result=OperationResult(status="BLOCKED",blockers=("INPUT_OR_PUBLICATION_INVALID",))
    except KeyboardInterrupt:
        result=OperationResult(status="BLOCKED",blockers=("OPERATION_INTERRUPTED",))
    for part in chunks(result):stdout.write(part)
    stdout.write("\n");stderr.write(f"TASK308B1 REPORT: {result.status}; network=false; DB mutation=false; PIT=false\n")
    return 0 if result.status=="COMPLETE" else 2


if __name__=="__main__":raise SystemExit(main())
