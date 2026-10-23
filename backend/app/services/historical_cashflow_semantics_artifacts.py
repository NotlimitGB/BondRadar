"""Independent immutable R3 reports, with semantics JSON as completion artifact."""
from pathlib import Path
import tempfile
from app.schemas.historical_cashflow_semantics import SemanticsPackage, SourceRowIntegrityReport, CashflowSemanticsReport
from app.schemas.historical_cashflow_source_forensics import DiagnosticLimits
from app.services.historical_cashflow_qualification_service import input_fingerprints
from app.services.historical_cashflow_semantics_service import FIELDS
from app.services.historical_identity_probe_service import safe_path, read_model, read_bytes, writer_lock, atomic, assert_capacity
from app.services.historical_identity_recovery_artifacts import disjoint, file_sha
from app.services.historical_identity_feasibility_service import require
from app.services.historical_evidence_normalization import checked
from app.services.historical_audit_canonical_json import chunks

INTEGRITY = "task308b2r3-source-row-integrity.json"
SEMANTICS = "task308b2r3-cashflow-semantics.json"
SUMMARY = "task308b2r3-cashflow-semantics-summary.md"


def validate_package(package):
    require(type(package) is SemanticsPackage, "SEMANTICS_PACKAGE_REQUIRED")
    SemanticsPackage.model_validate(package)
    checked(package.integrity, "report_sha256"); checked(package.semantics, "report_sha256")
    i, s = package.integrity, package.semantics
    require(package.status == s.status and s.source_row_integrity_sha256 == i.report_sha256 and
            s.structural_integrity_status == i.status, "R3_REPORT_LINKAGE_MISMATCH")
    if package.status == "BLOCKED":
        require(i.status == "BLOCKED" and not i.observations and not s.events and not s.coupon_coverage and
                not s.canonical_observations, "BLOCKED_PARTIAL_OUTPUT")
        return package
    require(i.status == "PASS" and not i.blockers and not s.blockers and
            i.retained_row_count == sum(o.retained_rows_checked for o in i.observations) == len(s.events), "INTEGRITY_COUNT_MISMATCH")
    bindings = {o.request_id: o for o in i.observations}
    require(len(bindings) == len(i.observations), "OBSERVATION_ID_CONFLICT")
    event_ids = set()
    for event in s.events:
        key = (event.request_id, event.row_index)
        require(key not in event_ids and event.request_id in bindings, "EVENT_OCCURRENCE_INVALID")
        event_ids.add(key); finding = bindings[event.request_id]
        require(event.secid == finding.secid and event.table == finding.table and 0 <= event.row_index < finding.retained_rows_checked and
                event.source_row_sha256 == finding.typed_row_sha256s[event.row_index], "EVENT_ROW_LINEAGE_MISMATCH")
        require(tuple(f.field_name for f in event.fields) == tuple(FIELDS) and all(f.proof_status != "DOCUMENTED" for f in event.fields),
                "FIELD_AUTHORITY_NOT_REVIEWED")
        require(event.semantic_classification != "SCHEDULED_PARTIAL_AMORTIZATION", "PARTIAL_AMORTIZATION_PROOF_UNAVAILABLE")
    for canonical in s.canonical_observations:
        require(set(canonical.observation_request_ids) <= set(bindings) and
                (canonical.canonical_request_id is None or canonical.canonical_request_id in canonical.observation_request_ids), "CANONICAL_LINEAGE_INVALID")
    for coverage in s.coupon_coverage:
        require(coverage.coupon_amount_present_count + coverage.coupon_amount_missing_count == coverage.coupon_row_count and
                0 <= coverage.coupon_amount_invalid_count <= coverage.coupon_amount_present_count and
                len(coverage.coupon_amount_missing_dates) == coverage.coupon_amount_missing_count, "COUPON_COUNT_MISMATCH")
    return package


def summary(package):
    lines = ["# Task308B2R3 cashflow semantics", "", "Execution: " + package.status,
             "Source-row integrity: " + package.integrity.status, "Amount units: UNPROVEN; actual payments: NOT_CONFIRMED; PIT: false.", "",
             "| SECID | Coupon cursor | Rows | Present | Missing | Invalid present |", "|---|---|---:|---:|---:|---:|"]
    for c in package.semantics.coupon_coverage:
        lines.append(f"| {c.secid} | {c.source_schedule_completeness} | {c.coupon_row_count} | {c.coupon_amount_present_count} | {c.coupon_amount_missing_count} | {c.coupon_amount_invalid_count} |")
        if c.coupon_amount_missing_dates: lines.append("\nMissing amount dates: " + ", ".join(str(d) if d else "UNKNOWN" for d in c.coupon_amount_missing_dates))
    for c in package.semantics.canonical_observations:
        lines.append(f"\n{c.secid}/{c.table}: canonical={c.canonical_request_id or 'UNAVAILABLE'}; {c.comparison_status}; " + ", ".join(c.diagnostics))
    for reason in package.integrity.blockers: lines.append("\nBlocked: " + reason)
    lines += ["", "Events are separate source occurrences, not an aggregated payment schedule.", ""]
    return "\n".join(lines).encode("utf-8")


def publish(*, package, output_dir, files, roots):
    validate_package(package)
    root = safe_path(output_dir); require(root.is_dir(), "OUTPUT_DIRECTORY_REQUIRED")
    for p in (*roots.values(), *(Path(p).parent for p in files.values())):
        require(disjoint(root, safe_path(p)), "OUTPUT_OVERLAPS_INPUT")
    def unchanged():
        if package.status != "BLOCKED":
            require(input_fingerprints(files=files, roots=roots) == package.integrity.input_fingerprints, "INPUT_EVIDENCE_CHANGED")
    unchanged()
    entries = ((SUMMARY, summary(package)), (INTEGRITY, package.integrity), (SEMANTICS, package.semantics))
    with writer_lock(root), tempfile.TemporaryDirectory(prefix=".task308b2r3-", dir=root) as directory:
        staging = safe_path(directory); total = 0
        for name, value in entries:
            with (staging / name).open("xb") as stream:
                for block in ((value,) if type(value) is bytes else (p.encode("ascii") for p in chunks(value))):
                    total += len(block); require(total <= 32 * 1024**2, "REPORT_BYTE_LIMIT")
                    assert_capacity(root, DiagnosticLimits(), extra=2 * total)
                    stream.write(block)
        for name, _ in entries:
            if (root / name).exists(): require(file_sha(root / name) == file_sha(staging / name), "CONFLICTING_OUTPUT")
        unchanged(); created = []
        try:
            for name, _ in entries:
                path = root / name; existed = path.exists()
                atomic(path, read_bytes(staging / name, 32 * 1024**2), immutable=True)
                if not existed: created.append(path)
        except BaseException:
            for path in reversed(created): path.unlink(missing_ok=True)
            raise
    return package.semantics.report_sha256


def verify_published(*, output_dir):
    root = safe_path(output_dir)
    integrity = read_model(root / INTEGRITY, SourceRowIntegrityReport, 32 * 1024**2)
    semantics = read_model(root / SEMANTICS, CashflowSemanticsReport, 32 * 1024**2)
    package = validate_package(SemanticsPackage(status=semantics.status, integrity=integrity, semantics=semantics))
    require(read_bytes(root / SUMMARY, 32 * 1024**2) == summary(package), "SUMMARY_MISMATCH")
    return package
