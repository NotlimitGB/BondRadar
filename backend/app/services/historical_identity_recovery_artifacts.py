"""Read-only upstream verification and bounded, audit-last publication for Task308B1."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from itertools import chain
from app.schemas.historical_discovery_coverage_audit import HistoricalDiscoveryCoverageAudit
from app.schemas.historical_discovery_checkpoint import DiscoveryManifestIndex
from app.schemas.historical_identity_feasibility import SamplePlan, ProbeAuthorization, ProbeManifest, FeasibilityReport, AttemptReceipt
from app.schemas.historical_identity_recovery_inventory import InventoryLimits, Expectations, OperationResult, InventoryAudit, InventoryRow, ExceptionProposal, AcquisitionRoadmap, SourceFieldObservation
from app.services.historical_identity_feasibility_service import HistoricalIdentityFeasibilityService, require, field
from app.services.historical_identity_probe_service import (
    HistoricalIdentityProbeService, read_model, read_bytes, safe_path, independent_root, writer_lock,
)
from app.services.historical_identity_probe_client import parse_page
from app.services.historical_identity_recovery_inventory_service import HistoricalIdentityRecoveryInventoryService
from app.services.historical_evidence_normalization import checked, signed
from app.services.historical_audit_canonical_json import chunks


NAMES=("task308b1-identity-inventory.jsonl","task308b1-exception-probe-plan.json","task308b1-acquisition-plan.json","task308b1-summary.md","task308b1-identity-audit.json")


def file_sha(path,limit=32*1024**2):
    h=hashlib.sha256();size=0
    with safe_path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(65536),b""):
            size+=len(block);require(size<=limit,"INPUT_SIZE_LIMIT");h.update(block)
    return h.hexdigest()


def disjoint(left,right):return left!=right and left not in right.parents and right not in left.parents


@dataclass(frozen=True)
class VerifiedInputs:
    analysis: object
    files: tuple
    probe_root: Path
    limits: InventoryLimits
    protected_roots: tuple

    def verify_unchanged(self):
        current=_snapshot(tuple((p,cap) for p,_,cap in self.files),self.probe_root,self.limits)
        require(current==self.files,"INPUT_EVIDENCE_CHANGED")


def _snapshot(inputs,probe_root,limits):
    require(probe_root.is_dir(),"PROBE_EVIDENCE_DIRECTORY_REQUIRED")
    paths={p:cap for p,cap in inputs}
    probe_size=0
    # A permits <=72 intents/receipts and <=48 bodies (196 normal files).
    # Bound even empty/unreferenced files, which byte ceilings alone cannot limit.
    for index,entry in enumerate(probe_root.iterdir(),1):
        require(index<=256,"PROBE_FILE_COUNT_LIMIT")
        p=safe_path(entry);require(p.is_file(),"PROBE_DIRECTORY_ENTRY_INVALID")
        size=p.stat().st_size;probe_size+=size
        require(probe_size<=limits.probe_bytes,"PROBE_EVIDENCE_BYTE_LIMIT")
        paths[p]=min(paths.get(p,limits.probe_bytes),limits.probe_bytes)
    require(sum(p.stat().st_size for p in paths)<=limits.total_input_bytes,"TOTAL_INPUT_BYTE_LIMIT")
    result=[]
    for p,cap in sorted(paths.items(),key=lambda item:str(item[0])):
        require(p.is_file() and p.stat().st_size<=cap,"INPUT_SIZE_LIMIT")
        result.append((p,file_sha(p,min(cap,p.stat().st_size)),cap))
    return tuple(result)


def verify_inputs(*,task307_report,task308a_plan,task308a_authorization,task308a_evidence_root,task308a_evaluation,
    task306a4_manifest=None,expected_task307_sha256=None,expected_task308a_evaluation_sha256=None,
    expected_manifest_sha256=None,limits=None,expectations=None):
    limits=InventoryLimits() if limits is None else limits
    InventoryLimits.model_validate(limits)
    root=safe_path(task308a_evidence_root)
    paths=tuple((safe_path(p),cap) for p,cap in ((task307_report,limits.task307_bytes),(task308a_plan,limits.auxiliary_bytes),
        (task308a_authorization,limits.auxiliary_bytes),(task308a_evaluation,limits.auxiliary_bytes))+
        (((task306a4_manifest,limits.auxiliary_bytes),) if task306a4_manifest is not None else ()))
    before=_snapshot(paths,root,limits)
    r=read_model(task307_report,HistoricalDiscoveryCoverageAudit,limits.task307_bytes)
    require(len(r.identity_coverage.identities)<=limits.max_identity_rows,"MEMORY_IDENTITY_ROW_LIMIT")
    p=read_model(task308a_plan,SamplePlan,limits.auxiliary_bytes)
    a=read_model(task308a_authorization,ProbeAuthorization,limits.auxiliary_bytes)
    e=read_model(task308a_evaluation,FeasibilityReport,limits.auxiliary_bytes)
    m=read_model(task306a4_manifest,DiscoveryManifestIndex,limits.auxiliary_bytes) if task306a4_manifest is not None else None
    rebuilt=HistoricalIdentityFeasibilityService().build(report=r,sample_size=p.requested_sample_size,manifest=m,
        expected_audit_sha256=expected_task307_sha256,expected_manifest_sha256=expected_manifest_sha256)
    require(rebuilt==p,"TASK308A_SAMPLE_REBUILD_MISMATCH")
    probe=read_model(root/"task308a-probe-manifest.json",ProbeManifest,limits.auxiliary_bytes)
    checked(probe,"evidence_sha256");checked(e,"report_sha256")
    require(probe.status=="COMPLETE" and not probe.blockers,"TASK308A_PROBE_NOT_COMPLETE")
    if expected_task308a_evaluation_sha256 is not None:
        require(e.report_sha256==expected_task308a_evaluation_sha256,"EXPECTED_EVALUATION_SHA_MISMATCH")
    # This calls only Task308A's existing offline replay. Never authorize/probe/fetch.
    require(HistoricalIdentityProbeService().evaluate(plan=p,authorization=a,evidence_root=root)==e,"TASK308A_EVALUATION_REPLAY_MISMATCH")
    observations={}
    for receipt_sha in probe.receipt_sha256s:
        receipt=read_model(root/(receipt_sha+".receipt.json"),AttemptReceipt,limits.auxiliary_bytes)
        if receipt.outcome!="ACCEPTED":continue
        page=parse_page(read_bytes(root/(receipt.response_sha256+".response.json"),a.limits.response_bytes),receipt.request)
        for row in page.rows:
            if field(row,"SECID")!=receipt.request.secid:continue
            for name,value in row.items():
                present,supplied=observations.setdefault((receipt.request.source,name),(set(),set()))
                present.add(receipt.request.secid)
                if value is not None and (type(value) is not str or value.strip()):supplied.add(receipt.request.secid)
    fields=tuple(SourceFieldObservation(source=source,source_field=name,exact_record_secids=tuple(sorted(present)),supplied_value_secids=tuple(sorted(supplied)))
        for (source,name),(present,supplied) in sorted(observations.items()))
    analysis=HistoricalIdentityRecoveryInventoryService().build(report=r,plan=p,authorization=a,evaluation=e,probe_manifest=probe,
        manifest=m,limits=limits,expectations=expectations,source_field_observations=fields)
    verified=VerifiedInputs(analysis,before,root,limits,(root,)+((safe_path(task306a4_manifest).parent,) if task306a4_manifest is not None else ()))
    verified.verify_unchanged()
    return verified


def summary(audit,analysis):
    yield "# Task308B1 — Historical identity inventory\n\n"
    yield f"Execution: {audit.status}. Inventory SHA: `{audit.inventory_sha256}`.\n\n"
    yield "Offline only; acquisition/PIT/historical authority/Day 0=false. No source requests or application DB.\n\n"
    yield "## Population (distinct raw SECIDs)\n\n"
    for name,count in audit.counts:yield f"- {name}: {count}\n"
    yield "\nChecksum validity is not official ISIN evidence. Joint identity execution coverage: NOT_ESTABLISHED.\n"
    yield "\n## Previously probed identities\n\n"
    for row in analysis.iter_inventory():
        if row.previously_probed:yield f"- {row.secid}: {row.current_binding}; current ISIN={row.confirmed_current_isin}; next={row.next_investigation}.\n"
    yield "\n## Proposed NEW exceptions (not authorized)\n\n"
    for row in analysis.proposal.selected:yield f"- {row.secid}: {row.role}; new features={','.join(row.new_features)}.\n"
    yield f"\nUnfilled total slots: {analysis.proposal.unfilled_total_slots}; unmet control quota: {analysis.proposal.unmet_control_slots}.\n"
    yield "\n## Acquisition roadmap\n\n"
    for stage in analysis.roadmap.stages:yield f"- {stage.stage}. {stage.name}: {stage.scope}. Gate: {stage.prerequisite}.\n"
    yield "\n## Measured sample cost (not a population forecast)\n\n"
    road=analysis.roadmap
    yield f"{road.measured_probed_secids} SECIDs; {road.measured_attempts} attempts; {road.measured_pages} pages; {road.measured_response_bytes} decoded bytes.\n"
    for key,value in road.descriptive_averages:yield f"- {key}: {value} (descriptive only).\n"
    yield "\n## Remaining blockers\n\n"
    for reason in audit.limitations:yield f"- {reason}\n"
    yield "\nNext: independent review and green CI, then separately authorized operator REPORT. No Task308B2 acquisition is authorized.\n"


def publish(verified,*,output_dir):
    require(type(verified) is VerifiedInputs,"VERIFIED_INPUTS_REQUIRED")
    root=safe_path(output_dir);require(root.is_dir(),"EXPLICIT_OUTPUT_DIRECTORY_REQUIRED")
    independent_root(root)
    require(all(disjoint(root,p) for p in verified.protected_roots),"OUTPUT_OVERLAPS_SOURCE_ROOT")
    for p,_,_ in verified.files:
        require(root!=p and root not in p.parents,"OUTPUT_OVERLAPS_INPUT")
        if p.name=="manifest.json":require(disjoint(root,p.parent),"OUTPUT_OVERLAPS_DISCOVERY_ROOT")
    analysis=verified.analysis;limits=verified.limits
    def reserve(extra=0):require(shutil.disk_usage(root).free-extra>=limits.reserve_bytes,"FREE_SPACE_RESERVE_NOT_MET")
    reserve()
    with writer_lock(root),tempfile.TemporaryDirectory(prefix=".task308b1-",dir=root) as directory:
        staging=safe_path(directory);total=0;hashes={}
        def write(name,parts,encoding="ascii"):
            nonlocal total
            h=hashlib.sha256()
            with (staging/name).open("xb") as stream:
                for part in parts:
                    raw=part.encode(encoding);total+=len(raw)
                    require(total<=limits.output_bytes,"OUTPUT_BYTE_LIMIT")
                    reserve(len(raw));stream.write(raw);h.update(raw)
                stream.flush();os.fsync(stream.fileno())
            hashes[name]=h.hexdigest()
        count=0
        def inventory():
            nonlocal count
            previous=None
            for row in analysis.iter_inventory():
                require(previous is None or previous<row.secid,"INVENTORY_ORDER_INVALID")
                previous=row.secid;count+=1;size=1
                for part in chunks(row):
                    size+=len(part)
                    require(size<=limits.working_row_bytes,"MEMORY_WORKING_ROW_LIMIT")
                    yield part
                yield "\n"
        write(NAMES[0],inventory())
        require(count==dict(analysis.counts)["total_secids"],"INVENTORY_COUNT_MISMATCH")
        audit=analysis.audit(hashes[NAMES[0]])
        write(NAMES[1],chain(chunks(analysis.proposal),("\n",)))
        write(NAMES[2],chain(chunks(analysis.roadmap),("\n",)))
        write(NAMES[3],summary(audit,analysis),"utf-8")
        audit=signed(InventoryAudit,"report_sha256",**{k:getattr(audit,k) for k in type(audit).model_fields if k not in ("report_sha256","summary_sha256")},summary_sha256=hashes[NAMES[3]])
        write(NAMES[4],chain(chunks(audit),("\n",)))
        verified.verify_unchanged()
        for name in NAMES:
            target=safe_path(root/name)
            if target.exists():require(file_sha(target)==hashes[name],"EXISTING_ARTIFACT_CONFLICT")
        # Audit is the completion artifact and is installed only after all dependencies.
        # Identical partial sets can be resumed; differing bytes never get overwritten.
        created=[]
        try:
            for name in NAMES:
                target=safe_path(root/name)
                if target.exists():continue
                if os.name=="nt":os.rename(staging/name,target)
                else:os.link(staging/name,target)
                created.append(target)
            if os.name!="nt":
                descriptor=os.open(root,os.O_RDONLY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
        except BaseException:
            for target in reversed(created):target.unlink(missing_ok=True)
            raise
        return OperationResult(status="COMPLETE",report_sha256=audit.report_sha256,input_hashes=audit.input_hashes,
            artifact_hashes=tuple(sorted(hashes.items())),resource_status="WITHIN_LIMITS")


def verify_published(*,output_dir,expected_report_sha256=None):
    """Validate the complete published set offline; this does not revalidate upstream sources."""
    root=safe_path(output_dir);limits=InventoryLimits()
    require(sum(safe_path(root/n).stat().st_size for n in NAMES)<=limits.output_bytes,"OUTPUT_BYTE_LIMIT")
    audit=read_model(root/NAMES[-1],InventoryAudit,limits.output_bytes);checked(audit,"report_sha256")
    require(audit.status=="COMPLETE" and not audit.blockers,"AUDIT_NOT_COMPLETE")
    if expected_report_sha256 is not None:require(audit.report_sha256==expected_report_sha256,"EXPECTED_REPORT_SHA_MISMATCH")
    proposal=read_model(root/NAMES[1],ExceptionProposal,limits.output_bytes);checked(proposal,"proposal_sha256")
    road=read_model(root/NAMES[2],AcquisitionRoadmap,limits.output_bytes);checked(road,"roadmap_sha256")
    require(proposal.proposal_sha256==audit.proposal_sha256 and road.roadmap_sha256==audit.roadmap_sha256 and
        file_sha(root/NAMES[0],limits.output_bytes)==audit.inventory_sha256 and file_sha(root/NAMES[3],limits.output_bytes)==audit.summary_sha256,"ARTIFACT_BINDING_MISMATCH")
    previous=None;count=0
    with safe_path(root/NAMES[0]).open("rb") as stream:
        while True:
            line=stream.readline(limits.working_row_bytes+1)
            if not line:break
            require(line.endswith(b"\n") and len(line)<=limits.working_row_bytes,"INVENTORY_LINE_INVALID")
            row=InventoryRow.model_validate_json(line)
            require(previous is None or previous<row.secid,"INVENTORY_ORDER_INVALID")
            previous=row.secid;count+=1
    require(count==dict(audit.counts)["total_secids"] and not set(proposal.previously_probed_secids)&{r.secid for r in proposal.selected},"PUBLISHED_INVARIANTS_FAILED")
    return audit
