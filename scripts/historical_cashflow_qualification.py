"""Task308B2R2: offline qualification only; no network or DB operation."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.schemas.historical_cashflow_qualification import ExpectedHashes, OperationEnvelope
from app.schemas.historical_cashflow_source_forensics import OriginalHashes
from app.services.historical_cashflow_qualification_service import HistoricalCashflowQualificationService
from app.services.historical_cashflow_qualification_artifacts import publish, verify_published
from app.services.historical_identity_probe_service import encode
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_identity_feasibility_service import require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("QUALIFY_EXISTING_EVIDENCE",))
    names = ("b2-plan", "b2-authorization", "b2-evidence-root", "b2-evaluation", "r1-forensics", "r1-plan",
             "r1-authorization", "r1-evidence-root", "r1-report")
    for name in (*names, "output"):
        parser.add_argument("--" + name, required=True)
    for name in ("b2-plan", "b2-authorization", "b2-probe", "b2-evaluation", "forensics", "diagnostic-plan", "diagnostic-authorization", "diagnostic-report"):
        parser.add_argument("--expected-" + name + "-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        expected = ExpectedHashes(b2=OriginalHashes(**{k + "_sha256": getattr(args, "expected_b2_" + k + "_sha256")
            for k in ("plan", "authorization", "probe", "evaluation")}), **{k + "_sha256": getattr(args, "expected_" + k + "_sha256")
            for k in ("forensics", "diagnostic_plan", "diagnostic_authorization", "diagnostic_report")})
        kwargs = {name.replace("-", "_"): getattr(args, name.replace("-", "_")) for name in names}
        result = HistoricalCashflowQualificationService.qualify_existing_evidence(**kwargs, expected=expected)
        files = {k: v for k, v in kwargs.items() if not k.endswith("evidence_root")}
        roots = {"b2": args.b2_evidence_root, "r1": args.r1_evidence_root}
        publish(package=result, output_dir=args.output, files=files, roots=roots)
        require(verify_published(output_dir=args.output) == result, "PUBLISHED_PACKAGE_MISMATCH")
        envelope = OperationEnvelope(status=result.status, blockers=result.source_contract.blockers, result=result)
    except (IdentityError, ValueError, TypeError, OSError):
        envelope = OperationEnvelope(status="BLOCKED", blockers=("OFFLINE_QUALIFICATION_FAILED",))
    # Console contains concise evidence hashes, never complete source rows.
    print(encode(envelope.model_copy(update={"result": None})).decode())
    if envelope.result is not None:
        print(encode({"source_contract_sha256": envelope.result.source_contract.report_sha256,
                      "qualification_sha256": envelope.result.qualification.report_sha256}).decode(), file=sys.stderr)
    return 2 if envelope.status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
