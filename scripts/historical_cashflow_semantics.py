"""Task308B2R3 offline-only semantics and source-row integrity runner."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.schemas.historical_cashflow_qualification import ExpectedHashes as UpstreamHashes
from app.schemas.historical_cashflow_source_forensics import OriginalHashes
from app.schemas.historical_cashflow_semantics import ExpectedHashes, OperationEnvelope
from app.services.historical_cashflow_semantics_service import HistoricalCashflowSemanticsService
from app.services.historical_cashflow_semantics_artifacts import publish, verify_published
from app.services.historical_identity_probe_service import encode
from app.services.historical_identity_feasibility_service import IdentityError, require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("QUALIFY_CASHFLOW_SEMANTICS",))
    paths = ("b2-plan", "b2-authorization", "b2-evidence-root", "b2-evaluation", "r1-forensics", "r1-plan",
             "r1-authorization", "r1-evidence-root", "r1-report", "r2-output-dir")
    for name in (*paths, "output"): parser.add_argument("--" + name, required=True)
    for name in ("b2-plan", "b2-authorization", "b2-probe", "b2-evaluation", "forensics", "diagnostic-plan",
                 "diagnostic-authorization", "diagnostic-report", "r2-source-contract", "r2-qualification"):
        parser.add_argument("--expected-" + name + "-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        expected = ExpectedHashes(upstream=UpstreamHashes(b2=OriginalHashes(**{
            name + "_sha256": getattr(args, "expected_b2_" + name + "_sha256") for name in ("plan", "authorization", "probe", "evaluation")}),
            **{name + "_sha256": getattr(args, "expected_" + name + "_sha256") for name in
               ("forensics", "diagnostic_plan", "diagnostic_authorization", "diagnostic_report")}),
            r2_source_contract_sha256=args.expected_r2_source_contract_sha256, r2_qualification_sha256=args.expected_r2_qualification_sha256)
        kwargs = {name.replace("-", "_"): getattr(args, name.replace("-", "_")) for name in paths}
        package = HistoricalCashflowSemanticsService.qualify_cashflow_semantics(**kwargs, expected=expected)
        files = {k: v for k, v in kwargs.items() if not k.endswith("evidence_root") and k != "r2_output_dir"}
        roots = {"b2": args.b2_evidence_root, "r1": args.r1_evidence_root, "r2": args.r2_output_dir}
        publish(package=package, output_dir=args.output, files=files, roots=roots)
        require(verify_published(output_dir=args.output) == package, "PUBLISHED_PACKAGE_MISMATCH")
        result = OperationEnvelope(status=package.status, blockers=package.integrity.blockers,
            source_row_integrity_sha256=package.integrity.report_sha256, cashflow_semantics_sha256=package.semantics.report_sha256)
    except (IdentityError, ValueError, TypeError, OSError):
        result = OperationEnvelope(status="BLOCKED", blockers=("OFFLINE_SEMANTICS_FAILED",))
    print(encode(result).decode())
    return 2 if result.status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
