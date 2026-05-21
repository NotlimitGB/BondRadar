from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from financial_report_import import HttpResult, http_json, write_json_report
from issuer_identity_import import render_markdown as render_import_markdown

from app.services.moex_iss_client import MoexIssClient


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview issuer identity enrichment from MOEX ISS bond metadata.",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--company-id", type=int, default=None)
    parser.add_argument("--secid", default=None)
    parser.add_argument("--execute", choices=("yes", "no"), default="no")
    parser.add_argument("--confirm-apply", choices=("yes", "no"), default="no")
    parser.add_argument("--rebuild-existing", action="store_true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser.parse_args(argv)


def build_report(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
    moex_client: MoexIssClient | None = None,
) -> dict[str, Any]:
    http_request = http_request or http_json
    moex_client = moex_client or MoexIssClient()
    backend = args.backend_url.rstrip("/")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    targets = _targets(args, backend, http_request, warnings)
    rows: list[dict[str, Any]] = []

    for target in targets[: max(1, args.limit)]:
        company_id = target.get("company_id")
        secid = args.secid or (target.get("sample_secids") or [None])[0]
        if not company_id or not secid:
            warnings.append({"message": f"company {company_id} has no sample secid"})
            continue
        try:
            metadata, item_warnings = moex_client.fetch_bond_description(str(secid))
        except Exception as exc:
            warnings.append({"message": f"MOEX metadata fetch failed for {secid}: {exc}"})
            continue
        warnings.extend({"message": message, "secid": secid} for message in item_warnings)
        issuer_name = _clean(metadata.get("issuer_name"))
        issuer_inn = _clean(metadata.get("issuer_inn"))
        if not issuer_name and not issuer_inn:
            warnings.append({"message": f"MOEX issuer metadata missing for {secid}"})
            continue
        rows.append(
            {
                "company_id": company_id,
                "current_company_name": target.get("company_name"),
                "legal_name": issuer_name,
                "short_name": issuer_name,
                "display_name": issuer_name,
                "inn": issuer_inn,
                "country": "RU",
                "issuer_role": "legal_issuer",
                "identity_status": "matched" if issuer_name and issuer_inn else "weak",
                "identity_confidence": "0.8" if issuer_name and issuer_inn else "0.4",
                "identity_source": "moex_iss",
                "review_status": "pending",
                "review_notes": f"Previewed from MOEX ISS metadata for {secid}.",
                "source_payload": {"secid": secid, "metadata": metadata},
            }
        )

    preview = None
    apply_result = None
    if rows:
        preview_payload = {"rows": rows, "rebuild_existing": args.rebuild_existing}
        preview_result = http_request(
            "POST",
            f"{backend}/api/companies/identity/preview",
            preview_payload,
        )
        preview = _http_data_or_warning(preview_result, warnings, "identity preview")
        if args.execute == "yes":
            if args.confirm_apply != "yes":
                errors.append({"message": "execute=yes requires --confirm-apply yes"})
            else:
                apply_payload = {
                    "rows": rows,
                    "rebuild_existing": args.rebuild_existing,
                    "confirm_apply": True,
                    "allow_conflicts": False,
                }
                apply_response = http_request(
                    "POST",
                    f"{backend}/api/companies/identity/apply",
                    apply_payload,
                )
                apply_result = _http_data_or_error(apply_response, errors, "identity apply")

    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "rows": rows,
        "preview": preview,
        "apply": apply_result,
        "errors": errors,
        "warnings": warnings,
        "next_steps": ["Review proposed MOEX identity metadata before confirmed apply."],
    }


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_import_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(f"[issuer-identity-moex] wrote JSON report: {args.json_output}", flush=True)
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(f"[issuer-identity-moex] wrote Markdown report: {args.markdown_output}", flush=True)
    print(f"[issuer-identity-moex] {report['status']}", flush=True)
    return 1 if report["status"] == "failed" else 0


def _targets(
    args: argparse.Namespace,
    backend: str,
    http_request: Any,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if args.company_id is not None:
        company = _safe_http_data(
            http_request("GET", f"{backend}/api/companies/{args.company_id}", None)
        )
        return [
            {
                "company_id": args.company_id,
                "company_name": company.get("name") if isinstance(company, dict) else None,
                "sample_secids": [args.secid] if args.secid else [],
            }
        ]
    result = http_request(
        "GET",
        f"{backend}/api/companies/identity/diagnostics?active_only=true&limit={max(1, args.limit)}",
        None,
    )
    data = _http_data_or_warning(result, warnings, "identity diagnostics")
    return [] if not isinstance(data, dict) else data.get("top_unknown_issuers", [])


def _safe_http_data(result: Any) -> Any:
    if isinstance(result, HttpResult):
        return result.data if result.ok else None
    return result


def _http_data_or_warning(
    result: Any,
    warnings: list[dict[str, Any]],
    label: str,
) -> Any:
    if isinstance(result, HttpResult):
        if result.ok:
            return result.data
        warnings.append({"message": f"{label} request failed: {result.error or result.text}"})
        return None
    return result


def _http_data_or_error(
    result: Any,
    errors: list[dict[str, Any]],
    label: str,
) -> Any:
    if isinstance(result, HttpResult):
        if result.ok:
            return result.data
        errors.append({"message": f"{label} request failed: {result.error or result.text}"})
        return None
    return result


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    sys.exit(main())
