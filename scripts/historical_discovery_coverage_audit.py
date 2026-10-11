"""Standalone Task307 REPORT. No application DB, credentials or source client."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.schemas.historical_discovery_coverage_audit import AuditLimits, HistoricalDiscoveryCoverageAudit
from app.services.historical_discovery_coverage_audit import HistoricalDiscoveryCoverageAuditService, blocked
from app.services.historical_discovery_coverage_reader import safe_path, disjoint, AuditFailure, stream_hash
from app.services.historical_audit_canonical_json import chunks, digest


JSON_NAME = "task307-coverage-audit.json"
MD_NAME = "task307-coverage-summary.md"


def summary(report):
    yield "# Task307 — Historical discovery coverage and PIT audit\n\n"
    yield f"Execution: **{report.status}**. Source manifest: `{report.source_manifest_sha256}`.\n\n"
    yield f"Audit SHA: `{report.audit_sha256}`. Integrity: {report.evidence_integrity.status}.\n\n"
    yield "Historical universe complete=false; PIT ready=false; acquisition ready=false.\n\n"
    if report.status == "COMPLETE":
        u, d, i = report.universe_coverage, report.discovery_coverage, report.identity_coverage
        yield "## Universe and identifiers\n\n"
        for name in type(u).model_fields:
            yield f"- {name}: {getattr(u,name)}\n"
        yield f"\nMissing-ISIN definition: {i.unresolved_definition}. Listing records: {i.missing_isin_listing_records}; history records: {i.missing_isin_history_records}.\n\n"
        yield "Absence from later observations is diagnostic; it does not establish delisting, maturity or default.\n\n"
        yield "## Observations and trading activity\n\n"
        yield f"Range: {d.history_start} through {d.cutoff}; accepted pages: {d.accepted_pages}; partitions: {d.completed_partitions}; attempts: {d.http_attempts}; retries: {d.retries}.\n\n"
        for title, metrics in (("Market quality",report.market_quality),("Trading activity",report.trading_activity),("Price/NKD",report.price_nkd_coverage),("OFZ source inventory",report.ofz_coverage)):
            yield f"### {title}\n\n"
            for metric in metrics:
                yield f"- {metric.name}: {metric.count} / {metric.denominator} ({metric.percentage}%)\n"
            yield "\n"
        yield "Usable mapped price/NKD permits the preserved price fallback; strict clean-price coverage is separate. Neither proves bid/ask, depth, fill or transaction costs. OFZ terms, duration matching and performance are not evaluated.\n\n"
        yield "## Annual and monthly coverage\n\n"
        for row in (*report.date_coverage.annual,*report.date_coverage.monthly):
            yield f"- {row.period}: {row.market_rows} rows; {row.observed_dates} observed dates; {row.distinct_identities} identities; {row.actual_and_usable_rows} actual+usable rows.\n"
        yield "\n## Frozen monthly entry grid\n\n"
        for row in report.date_coverage.research_entries:
            yield f"- {row.month}: {row.entry_date or 'NO_ENTRY_DATE'}; identities={row.identities}; jointly covered={row.jointly_covered_identities}; "
            yield "; ".join(f"{h.horizon_days}d {h.status} ({h.entry_identities_with_subsequent_observation})" for h in row.horizons)+".\n"
        yield "\nSubsequent observations are descriptive source coverage, not verified exits or total returns. Empty calendar partitions do not establish market closure.\n\n"
        yield "## Boards\n\n"
        for row in report.board_coverage:
            yield f"- {row.board}: {row.market_rows} rows; {row.distinct_identities} identities; {row.first_date}–{row.last_date}; eligibility UNVERIFIED.\n"
        yield "\n## PIT and missing families\n\n"
        yield "Decision cutoff: 00:00 UTC T. Market decision evidence must be <T; DATE-only publication requires D+1 authority. Fetch time or effective date cannot backdate availability.\n\n"
        for row in report.pit_evidence:
            yield f"- {row.family}: {row.available_evidence}; usability={row.decision_time_usability}; {row.blocker}.\n"
    yield "\n## Blockers and prioritized roadmap\n\n"
    for row in report.remediation_priorities:
        yield f"- {row.priority} {row.reason}: {row.required_step}. Required proof: {row.missing_proof}; validation: {row.validation}; dependency: {row.dependency}.\n"
    yield "\nNext step: independent audit review and separately authorized evidence-request planning. No acquisition, projection, replay, deployment or official Day 0 is authorized.\n"


def publish(report, output, limits):
    if digest(report) != report.audit_sha256:
        raise AuditFailure("AUDIT_REPORT_SHA_INVALID")
    HistoricalDiscoveryCoverageAudit.model_validate({k:getattr(report,k) for k in type(report).model_fields})
    targets = [safe_path(output / name) for name in (JSON_NAME,MD_NAME)]
    existing = [p.exists() for p in targets]
    if any(existing):
        if not all(existing): raise AuditFailure("REPORT_PUBLICATION_INCOMPLETE")
        with targets[0].open("rb") as stream:
            content = stream.read(limits.output_bytes+1)
        if len(content)>limits.output_bytes: raise AuditFailure("EXISTING_REPORT_TOO_LARGE")
        old = HistoricalDiscoveryCoverageAudit.model_validate_json(content)
        if digest(old)!=old.audit_sha256 or old.source_manifest_sha256!=report.source_manifest_sha256 or old.audit_sha256!=report.audit_sha256:
            raise AuditFailure("EXISTING_REPORT_CONFLICT")
    temporary = []
    try:
        size = 0
        for parts in (chunks(report),summary(report)):
            descriptor,name = tempfile.mkstemp(prefix=".task307-publish-",dir=output)
            path = Path(name); temporary.append(path)
            with os.fdopen(descriptor,"wb") as stream:
                for part in parts:
                    raw=part.encode("ascii" if len(temporary)==1 else "utf-8")
                    size+=len(raw)
                    if size>limits.output_bytes: raise AuditFailure("OUTPUT_BUDGET_EXCEEDED")
                    if shutil.disk_usage(output).free-len(raw)<limits.reserve_bytes:
                        raise AuditFailure("FREE_SPACE_RESERVE_NOT_MET")
                    stream.write(raw)
                stream.flush();os.fsync(stream.fileno())
        if all(existing):
            # Idempotent publication does not silently rewrite existing reports.
            for temp,target in zip(temporary,targets):
                if stream_hash(temp)!=stream_hash(target):
                    raise AuditFailure("EXISTING_REPORT_CONFLICT")
            return
        created=[]
        try:
            for temp,target in zip(temporary,targets):
                # Same-filesystem exclusive hard-link publication is atomic per artifact.
                os.link(temp,target)
                created.append(target)
            if os.name!="nt":
                descriptor=os.open(output,os.O_RDONLY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
        except Exception:
            for target in created: target.unlink(missing_ok=True)
            raise
    finally:
        for path in temporary: path.unlink(missing_ok=True)


def main(argv=None, *, stdout=None, stderr=None):
    stdout,stderr = stdout or sys.stdout,stderr or sys.stderr
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("REPORT",),default="REPORT")
    parser.add_argument("--evidence-root",required=True)
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--scratch-dir",required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--max-scratch-bytes",type=int,default=4*1024**3)
    parser.add_argument("--max-output-bytes",type=int,default=32*1024**2)
    parser.add_argument("--reserve-bytes",type=int,default=8*1024**3)
    args=parser.parse_args(argv)
    try:
        limits=AuditLimits(scratch_bytes=args.max_scratch_bytes,output_bytes=args.max_output_bytes,reserve_bytes=args.reserve_bytes)
        root,output,scratch = (safe_path(p) for p in (args.evidence_root,args.output_dir,args.scratch_dir))
        disjoint(root,output);disjoint(root,scratch);disjoint(output,scratch)
        if not output.is_dir() or not scratch.is_dir():raise AuditFailure("AUDIT_DIRECTORY_MISSING")
        if shutil.disk_usage(output).free<limits.reserve_bytes:raise AuditFailure("FREE_SPACE_RESERVE_NOT_MET")
        report=HistoricalDiscoveryCoverageAuditService().build(evidence_root=root,scratch_dir=scratch,
            expected_manifest_sha256=args.expected_manifest_sha256,limits=limits)
        publish(report,output,limits)
    except AuditFailure as exc:
        report=blocked(exc.code)
    except (ValueError,TypeError,OSError):
        report=blocked("AUDIT_RUNNER_FAILURE")
    for part in chunks(report):stdout.write(part)
    stdout.write("\n")
    stderr.write(f"TASK307={report.status}; audit_sha256={report.audit_sha256}; PIT=false; acquisition=false\n")
    return 0 if report.status=="COMPLETE" else 2


if __name__=="__main__":
    raise SystemExit(main())
