"""Task308A: offline planning/evaluation and separately authorized small probe."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from app.schemas.historical_discovery_coverage_audit import HistoricalDiscoveryCoverageAudit
from app.schemas.historical_discovery_checkpoint import DiscoveryManifestIndex
from app.schemas.historical_identity_feasibility import SamplePlan, ProbeAuthorization, ProbeLimits, OperationResult
from app.services.historical_identity_feasibility_service import HistoricalIdentityFeasibilityService, IdentityError, require
from app.services.historical_identity_probe_service import (
    HistoricalIdentityProbeService, read_model, read_bytes, encode, atomic, safe_path, independent_root, writer_lock, OUTPUT_LIMIT, INPUT_LIMIT,
)


def summary(artifact):
    lines=["# Task308A — Historical identity source feasibility","",
        f"Contract: {artifact.contract_version}","",
        "Diagnostic sample only. PIT=false; acquisition=false; historical publication unproven.",""]
    if isinstance(artifact,SamplePlan):
        lines += [f"Plan SHA: {artifact.plan_sha256}",f"Task307 SHA: {artifact.context.task307_audit_sha256}",
            f"Sample: {len(artifact.samples)} / {artifact.population_secids} exact SECIDs","",
            "PLAN does not authorize a network probe.",""]
        lines += [f"- {s.secid}: {s.selection_reason}; strata={','.join(s.strata)}" for s in artifact.samples]
        lines += ["","## Stratum coverage",""]+[f"- {s.name}: population={s.eligible_count}; selected={s.selected_count}; represented={s.represented}" for s in artifact.strata]
        lines += ["","## Diagnosis",""]+[f"- {d.classification}: {d.explanation}. {d.evidence}" for d in artifact.diagnoses]
        lines += ["","## Source matrix",""]+[f"- {s.source}: {s.documentation_url}; {s.historical_limitations}" for s in artifact.source_matrix]
    else:
        lines += [f"Report SHA: {artifact.report_sha256}",f"Feasibility: {artifact.feasibility}",""]
        lines += [f"- {m.name}: {m.count}/{m.denominator} ({m.percentage}%)" for m in artifact.metrics]
        lines += [""]+[f"- {u.sample.secid}: {u.status}; current ISIN={u.recovered_current_isin}; review={u.requires_review}" for u in artifact.units]
        lines += ["","## Limitations",""]+[f"- {v}" for v in artifact.limitations]
        lines += ["","## Next reviewed steps",""]+[f"- {v}" for v in artifact.next_steps]
    return ("\n".join(lines)+"\n").encode("utf-8")


def publish(output, files):
    root=safe_path(output);require(root.is_dir(),"OUTPUT_DIRECTORY_REQUIRED")
    independent_root(root)
    require(sum(len(body) for body in files.values())<=OUTPUT_LIMIT,"OUTPUT_SIZE_LIMIT")
    with writer_lock(root):
        # Validate the whole set before publication; clean our new files on a controlled failure.
        for name,body in files.items():
            path=safe_path(root/name)
            if path.exists():require(read_bytes(path,OUTPUT_LIMIT)==body,"EXISTING_ARTIFACT_CONFLICT")
        created=[]
        try:
            for name,body in files.items():
                path=safe_path(root/name)
                if not path.exists():
                    atomic(path,body,immutable=True);created.append(path)
        except BaseException:
            for path in created:path.unlink(missing_ok=True)
            raise


def main(argv=None, *, stdout=None, stderr=None, probe_service=None, source_client=None):
    stdout,stderr=stdout or sys.stdout,stderr or sys.stderr
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("PLAN","PROBE_AUTHORIZE","PROBE","PROBE_EVALUATE"),default="PLAN")
    parser.add_argument("--task307-report")
    parser.add_argument("--manifest")
    parser.add_argument("--expected-audit-sha256")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--sample-size",type=int,default=36)
    parser.add_argument("--sample-plan")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--secids",nargs="+")
    parser.add_argument("--sources",nargs="+",choices=("REFERENCE","DESCRIPTION"),default=["REFERENCE","DESCRIPTION"])
    parser.add_argument("--authorization")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--confirm-authorize",action="store_true")
    parser.add_argument("--confirm-network",action="store_true")
    parser.add_argument("--max-requests",type=int,default=72)
    parser.add_argument("--max-pages",type=int,default=48)
    parser.add_argument("--response-bytes",type=int,default=8*1024**2)
    parser.add_argument("--total-response-bytes",type=int,default=64*1024**2)
    parser.add_argument("--max-seconds",type=int,default=300)
    args=parser.parse_args(argv)
    try:
        output=safe_path(args.output_dir);require(output.is_dir(),"OUTPUT_DIRECTORY_REQUIRED")
        for name in (args.task307_report,args.manifest,args.sample_plan,args.authorization):
            if name:
                p=safe_path(name)
                require(output!=p,"OUTPUT_OVERLAPS_INPUT")
                if name==args.manifest:
                    require(output!=p.parent and p.parent not in output.parents and output not in p.parent.parents,"OUTPUT_OVERLAPS_INPUT")
        service=probe_service or HistoricalIdentityProbeService()
        if args.mode=="PLAN":
            require(args.task307_report is not None,"TASK307_REPORT_REQUIRED")
            report=read_model(args.task307_report,HistoricalDiscoveryCoverageAudit,INPUT_LIMIT)
            manifest=read_model(args.manifest,DiscoveryManifestIndex) if args.manifest else None
            artifact=HistoricalIdentityFeasibilityService().build(report=report,manifest=manifest,sample_size=args.sample_size,
                expected_audit_sha256=args.expected_audit_sha256,expected_manifest_sha256=args.expected_manifest_sha256)
            publish(output,{"task308a-sample-plan.json":encode(artifact),"task308a-sample-summary.md":summary(artifact)})
            key=artifact.plan_sha256
        else:
            require(args.sample_plan is not None and args.evidence_dir is not None,"PLAN_AND_EVIDENCE_DIRECTORY_REQUIRED")
            plan=read_model(args.sample_plan,SamplePlan)
            evidence=safe_path(args.evidence_dir)
            require(output!=evidence and output not in evidence.parents and evidence not in output.parents,"PROBE_OUTPUT_DIRECTORIES_OVERLAP")
            if args.mode in ("PROBE_AUTHORIZE","PROBE"):
                require(args.expected_plan_sha256 is not None,"EXPECTED_PLAN_SHA_REQUIRED")
            if args.mode=="PROBE_AUTHORIZE":
                limits=ProbeLimits(max_requests=args.max_requests,max_pages=args.max_pages,response_bytes=args.response_bytes,
                    total_response_bytes=args.total_response_bytes,max_seconds=args.max_seconds)
                artifact=service.authorize(plan=plan,expected_plan_sha256=args.expected_plan_sha256,secids=args.secids,
                    sources=args.sources,destination=evidence,explicit_authorization=args.confirm_authorize,limits=limits)
                publish(output,{"task308a-probe-authorization.json":encode(artifact)});key=artifact.authorization_sha256
            else:
                require(args.authorization is not None,"PROBE_AUTHORIZATION_REQUIRED")
                authorization=read_model(args.authorization,ProbeAuthorization)
                if args.mode=="PROBE":
                    artifact=service.probe(plan=plan,authorization=authorization,expected_plan_sha256=args.expected_plan_sha256,
                        destination=evidence,confirm_network=args.confirm_network,source_client=source_client)
                    publish(output,{"task308a-probe-manifest.json":encode(artifact)});key=artifact.evidence_sha256
                else:
                    artifact=service.evaluate(plan=plan,authorization=authorization,evidence_root=evidence)
                    publish(output,{"task308a-feasibility-report.json":encode(artifact),"task308a-feasibility-summary.md":summary(artifact)})
                    key=artifact.report_sha256
        status="INCOMPLETE" if args.mode=="PROBE" and artifact.status!="COMPLETE" else "COMPLETE"
        result=OperationResult(mode=args.mode,status=status,artifact_sha256=key)
    except IdentityError as exc:
        result=OperationResult(mode=args.mode,status="BLOCKED",blockers=(exc.code,))
    except (ValueError,TypeError,OSError,KeyError,OverflowError,RecursionError):
        result=OperationResult(mode=args.mode,status="BLOCKED",blockers=("INPUT_OR_ARTIFACT_INVALID",))
    stdout.write(encode(result).decode("ascii")+"\n")
    stderr.write(f"TASK308A {args.mode}: {result.status}; PIT=false; acquisition=false\n")
    return 0 if result.status=="COMPLETE" else 2


if __name__=="__main__":raise SystemExit(main())
