"""Immutable v2 publication and independent offline verification."""
from pathlib import Path
import tempfile
from app.schemas.historical_cashflow_qualification import QualificationPackage, SourceContractReport, QualificationReport
from app.schemas.historical_cashflow_source_forensics import DiagnosticLimits
from app.services.historical_cashflow_qualification_service import input_fingerprints, qualify_report, compare_profiles
from app.services.historical_identity_probe_service import safe_path, writer_lock, atomic, read_bytes, read_model, assert_capacity
from app.services.historical_identity_recovery_artifacts import disjoint, file_sha
from app.services.historical_identity_feasibility_service import require
from app.services.historical_evidence_normalization import checked
from app.services.historical_audit_canonical_json import chunks

SOURCE = "task308b2r2-source-contract-report.json"
QUALIFICATION = "task308b2r2-qualification-report.json"


def validate_package(package):
    require(type(package) is QualificationPackage, "QUALIFICATION_PACKAGE_REQUIRED")
    QualificationPackage.model_validate(package)
    checked(package.source_contract, "report_sha256"); checked(package.qualification, "report_sha256")
    require(package.status == package.source_contract.status == package.qualification.status and
            package.qualification == qualify_report(package.source_contract), "QUALIFICATION_LINKAGE_MISMATCH")
    if package.status != "BLOCKED":
        require(package.source_contract.comparisons == compare_profiles(package.source_contract.observations), "COMPARISON_MISMATCH")
        rows = package.source_contract.observations
        require(len({o.request_id for o in rows}) == len(rows), "DUPLICATE_OBSERVATION_ID")
        for observation in rows:
            view = observation.view
            require(view.unit_status != "DOCUMENTED", "UNIT_AUTHORITY_NOT_REVIEWED")
            require(view.requested_secid == observation.requested_secid and view.requested_table == observation.table,
                    "OBSERVATION_IDENTITY_MISMATCH")
            require(len(view.events) == len(view.source_rows_json) == len(view.source_row_sha256s)
                    == len(view.could_become_economic_after_qualification), "OBSERVATION_ROW_COUNT_MISMATCH")
    else:
        require(not package.source_contract.observations and not package.qualification.coverage, "BLOCKED_PARTIAL_OUTPUT")
    return package


def summaries(package):
    source = ["# Task308B2R2 source contract", "", "Status: " + package.status, "", "| SECID | Table | Profile | Structure | Completeness | Rows |", "|---|---|---|---|---|---:|"]
    for o in package.source_contract.observations:
        source.append(f"| {o.requested_secid} | {o.table} | {o.profile} | {o.view.structural_status} | {o.view.completeness} | {len(o.view.events)} |")
    economic = ["# Task308B2R2 qualification", "", "No payments, historical publication or total-return readiness established.", ""]
    for row in package.qualification.coverage:
        economic.append(f"- {row.requested_secid}/{row.table}: {row.cashflow_coverage_status}; units={row.monetary_unit_status}; blockers=" + ", ".join(row.blockers))
    for reason in package.source_contract.blockers:
        source.append("\nBlocked: " + reason)
    return ("\n".join(source) + "\n").encode(), ("\n".join(economic) + "\n").encode()


def publish(*, package, output_dir, files, roots):
    validate_package(package)
    root = safe_path(output_dir)
    require(root.is_dir(), "OUTPUT_DIRECTORY_REQUIRED")
    for p in (*roots.values(), *(Path(p).parent for p in files.values())):
        require(disjoint(root, safe_path(p)), "OUTPUT_OVERLAPS_INPUT")
    if package.status != "BLOCKED":
        require(input_fingerprints(files=files, roots=roots) == package.source_contract.input_fingerprints, "INPUT_EVIDENCE_CHANGED")
    s, q = summaries(package)
    entries = (("task308b2r2-source-contract-summary.md", s),
               ("task308b2r2-qualification-summary.md", q), (SOURCE, package.source_contract), (QUALIFICATION, package.qualification))
    with writer_lock(root), tempfile.TemporaryDirectory(prefix=".task308b2r2-", dir=root) as directory:
        staging = safe_path(directory); total = 0
        for name, value in entries:
            with (staging / name).open("xb") as stream:
                for part in ((value,) if type(value) is bytes else (p.encode("ascii") for p in chunks(value))):
                    total += len(part); require(total <= 32 * 1024**2, "REPORT_BYTE_LIMIT")
                    assert_capacity(root, DiagnosticLimits(), extra=2 * total)
                    stream.write(part)
        for name, _ in entries:
            if (root / name).exists(): require(file_sha(root / name) == file_sha(staging / name), "CONFLICTING_OUTPUT")
        if package.status != "BLOCKED":
            require(input_fingerprints(files=files, roots=roots) == package.source_contract.input_fingerprints, "INPUT_EVIDENCE_CHANGED")
        created = []
        try:
            for name, _ in entries:
                path = root / name; existed = path.exists()
                atomic(path, read_bytes(staging / name, 32 * 1024**2), immutable=True)
                if not existed: created.append(path)
        except BaseException:
            for path in reversed(created): path.unlink(missing_ok=True)
            raise
    return package.qualification.report_sha256


def verify_published(*, output_dir):
    root = safe_path(output_dir)
    source = read_model(root / SOURCE, SourceContractReport, 32 * 1024**2)
    qualification = read_model(root / QUALIFICATION, QualificationReport, 32 * 1024**2)
    package = validate_package(QualificationPackage(status=source.status, source_contract=source, qualification=qualification))
    for name, expected in zip(("task308b2r2-source-contract-summary.md", "task308b2r2-qualification-summary.md"), summaries(package)):
        require(read_bytes(root / name, 32 * 1024**2) == expected, "SUMMARY_MISMATCH")
    return package
