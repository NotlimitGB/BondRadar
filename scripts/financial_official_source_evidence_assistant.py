from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

import financial_official_collection_pack as collection_pack
from financial_report_import import http_json, write_json_report


MODE_CHOICES = (
    "source-template",
    "source-discover",
    "source-validate",
    "document-resolve",
    "document-validate",
    "document-intake-template",
    "document-intake-validate",
    "candidate-fill",
    "preview",
)
FORMAT_CHOICES = ("csv", "json")
DOCUMENT_REPORT_TYPES = ("annual", "quarterly")
DOCUMENT_ACCOUNTING_STANDARDS = ("IFRS", "RAS", "unknown")
ALLOWED_SOURCE_TYPES = {
    "issuer_investor_relations",
    "official_issuer_report",
    "official_disclosure",
    "exchange_disclosure",
    "auditor_report",
    "issuer_annual_report_pdf",
}
OFFICIAL_SOURCE_DOMAIN_HINTS = tuple(collection_pack.OFFICIAL_SOURCE_DOMAIN_HINTS)
BLOCKED_SOURCE_HINTS = (
    "wikipedia",
    "wikimedia",
    "wikiwand",
    "vk.com",
    "telegram",
    "t.me",
    "reddit",
    "forum",
    "blog",
    "news",
    "aggregator",
    "social",
    "investing.com",
    "smart-lab",
    "banki.ru",
)
SOURCE_INTAKE_SOURCE_TYPES = [
    "issuer_investor_relations",
    "official_disclosure",
    "issuer_annual_report_pdf",
]
SOURCE_INTAKE_NOTES = {
    "issuer_investor_relations": "Prefer official issuer site annual IFRS consolidated report.",
    "official_disclosure": "Use official disclosure system if issuer site is unavailable.",
    "issuer_annual_report_pdf": "Use the issuer annual report PDF or audited IFRS report PDF.",
}
DISCOVERY_SOURCE_CONFIG = {
    "issuer_domain_hints": {
        "18": ["rzd.ru", "eng.rzd.ru", "e-disclosure.ru"],
        "67": ["mostotrest.ru", "e-disclosure.ru"],
    },
    "blocked_domains": list(BLOCKED_SOURCE_HINTS),
}
DISCOVERY_STATUSES = {
    "operator_to_fill",
    "discovered_candidate",
    "needs_operator_review",
    "valid_official_source",
    "invalid_source",
    "blocked_source",
}
EVIDENCE_FINANCIAL_FIELDS = list(collection_pack.FIELDS_TO_COLLECT)
ALL_FINANCIAL_FIELDS = list(collection_pack.FINANCIAL_FIELDS)
FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS = set(ALL_FINANCIAL_FIELDS) | {"debt"}
DOCUMENT_VALID_STATUSES = {
    "reviewed_official_document",
    "resolved_official_document",
    "valid_official_document",
}
DOCUMENT_REVIEW_STATUSES = {
    "operator_to_find",
    "needs_operator_review",
    "reviewed_official_document",
    "resolved_official_document",
    "valid_official_document",
    "invalid_document",
    "blocked_document",
}
DOCUMENT_INTAKE_UNRESOLVED_STATUSES = {"operator_to_find", "needs_operator_review"}
DOCUMENT_INTAKE_TEMPLATE_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "report_period",
    "report_type",
    "accounting_standard",
    "source_type",
    "source_url_context",
    "document_url",
    "document_title",
    "document_date",
    "source_file_name",
    "operator_review_status",
    "notes",
]
DOCUMENT_INTAKE_REVIEWED_STATUSES = {"reviewed", "operator_reviewed"}
DOCUMENT_CHECKLIST_FIELDS = [
    "rank",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "report_period",
    "report_type",
    "accounting_standard",
    "source_type",
    "source_url",
    "document_url",
    "document_title",
    "document_date",
    "source_file_name",
    "document_status",
    "confidence",
    "resolution_method",
    "operator_action",
    "notes",
]
SAFETY_FLAGS = {
    "read_only": True,
    "dry_run_only": True,
    "import_executed": False,
    "paper_trading_called": False,
    "identity_apply_executed": False,
    "would_mutate_scores": False,
    "would_trigger_paper_trading": False,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and validate official-source financial evidence candidates "
            "without importing reports."
        ),
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=MODE_CHOICES, required=True)
    parser.add_argument("--financial-template-input", type=Path, default=None)
    parser.add_argument("--evidence-template-input", type=Path, default=None)
    parser.add_argument("--source-checklist-input", type=Path, default=None)
    parser.add_argument("--source-intake-input", type=Path, default=None)
    parser.add_argument("--source-intake-output", type=Path, default=None)
    parser.add_argument("--document-input", type=Path, default=None)
    parser.add_argument("--document-intake-input", type=Path, default=None)
    parser.add_argument("--document-intake-output", type=Path, default=None)
    parser.add_argument("--document-intake-csv-output", type=Path, default=None)
    parser.add_argument("--document-intake-template-status", default="operator_to_fill")
    parser.add_argument("--require-operator-reviewed", type=_parse_bool, default=True)
    parser.add_argument("--document-output", type=Path, default=None)
    parser.add_argument("--document-checklist-output", type=Path, default=None)
    parser.add_argument("--report-period", default="2025")
    parser.add_argument("--report-type", choices=DOCUMENT_REPORT_TYPES, default="annual")
    parser.add_argument(
        "--accounting-standard",
        choices=DOCUMENT_ACCOUNTING_STANDARDS,
        default="IFRS",
    )
    parser.add_argument("--prefer-audited", action="store_true", default=True)
    parser.add_argument("--prefer-consolidated", action="store_true", default=True)
    parser.add_argument("--manual-values-json", type=Path, default=None)
    parser.add_argument("--manual-values-csv", type=Path, default=None)
    parser.add_argument("--candidate-input", type=Path, default=None)
    parser.add_argument("--candidate-output", type=Path, default=None)
    parser.add_argument("--candidate-format", choices=FORMAT_CHOICES, default="csv")
    parser.add_argument("--format", choices=FORMAT_CHOICES, default="csv")
    parser.add_argument("--evidence-output", type=Path, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    parser.add_argument("--allow-unknown-source", action="store_true")
    parser.add_argument("--probe-urls", action="store_true")
    parser.add_argument("--probe-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--max-probe-bytes", type=int, default=200000)
    parser.add_argument("--download-documents", action="store_true")
    parser.add_argument("--document-download-dir", type=Path, default=None)
    parser.add_argument("--download-source-documents", action="store_true")
    parser.add_argument("--source-download-dir", type=Path, default=None)
    return parser.parse_args(argv)


def run_assistant(
    args: argparse.Namespace,
    *,
    http_request: Any = None,
) -> tuple[dict[str, Any], int]:
    http_request = http_request or http_json
    if args.mode == "source-template":
        report = run_source_template(args)
    elif args.mode == "source-discover":
        report = run_source_discover(args)
    elif args.mode == "source-validate":
        report = run_source_validate(args)
    elif args.mode == "document-resolve":
        report = run_document_resolve(args)
    elif args.mode == "document-validate":
        report = run_document_validate(args)
    elif args.mode == "document-intake-template":
        report = run_document_intake_template(args)
    elif args.mode == "document-intake-validate":
        report = run_document_intake_validate(args)
    elif args.mode == "candidate-fill":
        report = run_candidate_fill(args)
    else:
        report = run_preview(args, http_request=http_request)
    return report, 1 if report["status"] == "failed" else 0


def run_source_template(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuer_sources: list[dict[str, Any]] = []

    if args.financial_template_input is None:
        errors.append({"message": "source-template mode requires --financial-template-input"})
    if args.evidence_template_input is None:
        errors.append({"message": "source-template mode requires --evidence-template-input"})
    if args.source_checklist_input is None:
        errors.append({"message": "source-template mode requires --source-checklist-input"})

    template_rows: list[dict[str, Any]] = []
    evidence_issuers: dict[str, dict[str, Any]] = {}
    checklist_by_company: dict[str, list[dict[str, Any]]] = {}
    if not errors:
        try:
            template_rows = load_template_rows(args.financial_template_input)
            evidence_issuers = _load_evidence_issuers(args.evidence_template_input)
            checklist_by_company = _load_checklist_by_company(args.source_checklist_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if not errors:
        for row in template_rows:
            key = _company_key(row)
            evidence = evidence_issuers.get(key) or {}
            checklist_rows = checklist_by_company.get(key) or []
            issuer_sources.append(
                build_source_intake_item(row, evidence=evidence, checklist_rows=checklist_rows)
            )

    source_intake = {
        "status": "template",
        "mode": "source-template",
        "issuer_count": len(issuer_sources),
        "issuer_sources": issuer_sources,
        **SAFETY_FLAGS,
    }
    if args.source_intake_output is not None and not errors:
        write_json_report(source_intake, args.source_intake_output)

    status = "failed" if errors else "warning" if warnings else "passed"
    report = {
        "status": status,
        "mode": "source-template",
        "issuer_count": len(issuer_sources),
        "issuer_sources": issuer_sources,
        "source_intake_output": _path_value(args.source_intake_output),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("source-template", status),
        **SAFETY_FLAGS,
    }
    return report


def build_source_intake_item(
    row: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
    checklist_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = evidence or {}
    checklist_rows = checklist_rows or []
    source_rows = checklist_rows or [
        {
            "recommended_source_type": source_type,
            "notes": SOURCE_INTAKE_NOTES[source_type],
        }
        for source_type in SOURCE_INTAKE_SOURCE_TYPES
    ]
    period = str(row.get("period_year") or evidence.get("period_year") or "")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_row in source_rows:
        source_type = (
            source_row.get("recommended_source_type")
            or source_row.get("source_type")
            or ""
        )
        if source_type not in SOURCE_INTAKE_SOURCE_TYPES or source_type in seen:
            continue
        seen.add(source_type)
        candidates.append(
            {
                "source_type": source_type,
                "url": "",
                "document_title": "",
                "document_date": "",
                "report_period": period,
                "status": "operator_to_fill",
                "notes": source_row.get("notes") or SOURCE_INTAKE_NOTES[source_type],
            }
        )
    return {
        "company_id": _as_int(row.get("company_id")),
        "company_name": row.get("company_name"),
        "canonical_company_id": _as_int(row.get("canonical_company_id") or row.get("company_id")),
        "canonical_company_name": row.get("canonical_company_name") or row.get("company_name"),
        "identity_status": row.get("identity_status"),
        "identity_confidence": _as_float(row.get("identity_confidence")),
        "source_candidates": candidates,
    }


def run_source_discover(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuer_sources: list[dict[str, Any]] = []
    discovered_sources: list[dict[str, Any]] = []

    if args.source_intake_input is None:
        errors.append({"message": "source-discover mode requires --source-intake-input"})
    if not errors:
        try:
            issuer_sources = load_source_intake(args.source_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if not errors:
        for issuer in issuer_sources:
            discovered, issuer_warnings = discover_issuer_sources(issuer, args)
            discovered_sources.append(discovered)
            warnings.extend(issuer_warnings)

    flat_candidates = [
        source
        for issuer in discovered_sources
        for source in issuer.get("source_candidates") or []
    ]
    candidate_count = len(flat_candidates)
    discovered_count = sum(
        1 for source in flat_candidates if source.get("status") == "discovered_candidate"
    )
    review_count = sum(
        1 for source in flat_candidates if source.get("status") == "needs_operator_review"
    )
    valid_count = sum(
        1 for source in flat_candidates if source.get("status") == "valid_official_source"
    )
    invalid_count = sum(
        1 for source in flat_candidates if source.get("status") == "invalid_source"
    )
    blocked_count = sum(
        1 for source in flat_candidates if source.get("status") == "blocked_source"
    )

    if not errors and args.source_intake_output is not None:
        write_json_report(
            {
                "status": "discovered",
                "mode": "source-discover",
                "issuer_count": len(discovered_sources),
                "issuer_sources": discovered_sources,
                **SAFETY_FLAGS,
            },
            args.source_intake_output,
        )

    if errors:
        status = "failed"
    elif candidate_count and invalid_count + blocked_count == candidate_count:
        status = "failed"
    elif valid_count >= len(discovered_sources) and discovered_sources:
        status = "passed"
    else:
        status = "warning"
    return {
        "status": status,
        "mode": "source-discover",
        "issuer_count": len(discovered_sources),
        "candidate_count": candidate_count,
        "discovered_candidate_count": discovered_count,
        "needs_operator_review_count": review_count,
        "valid_official_source_count": valid_count,
        "invalid_source_count": invalid_count,
        "blocked_source_count": blocked_count,
        "issuer_sources": discovered_sources,
        "source_intake_output": _path_value(args.source_intake_output),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("source-discover", status),
        **SAFETY_FLAGS,
    }


def discover_issuer_sources(
    issuer: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    issuer_copy = dict(issuer)
    hints = _issuer_domain_hints(issuer)
    if not hints:
        warnings.append(
            {
                "company_id": issuer.get("company_id"),
                "company_name": issuer.get("company_name"),
                "message": "no official source discovery hints configured; leaving sources for operator",
            }
        )
    candidates = issuer.get("source_candidates") or []
    source_types = [source.get("source_type") for source in candidates]
    for source_type in SOURCE_INTAKE_SOURCE_TYPES:
        if source_type not in source_types:
            candidates.append(
                {
                    "source_type": source_type,
                    "url": "",
                    "document_title": "",
                    "document_date": "",
                    "report_period": issuer.get("period_year") or "",
                    "status": "operator_to_fill",
                    "notes": SOURCE_INTAKE_NOTES[source_type],
                }
            )

    discovered_candidates = [
        discover_source_candidate(
            issuer,
            source,
            hints=hints,
            probe_urls=bool(args.probe_urls),
            probe_timeout_seconds=float(args.probe_timeout_seconds),
            max_probe_bytes=int(args.max_probe_bytes),
        )
        for source in candidates
    ]
    for source in discovered_candidates:
        if source.get("probe_status") == "failed":
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name"),
                    "source_type": source.get("source_type"),
                    "url": source.get("url"),
                    "message": "source probe failed; candidate requires operator review",
                }
            )
    issuer_copy["source_candidates"] = discovered_candidates
    return issuer_copy, warnings


def discover_source_candidate(
    issuer: dict[str, Any],
    source: dict[str, Any],
    *,
    hints: list[str],
    probe_urls: bool,
    probe_timeout_seconds: float,
    max_probe_bytes: int,
) -> dict[str, Any]:
    candidate = dict(source)
    source_type = str(candidate.get("source_type") or "").strip()
    url = str(candidate.get("url") or candidate.get("source_url") or "").strip()

    if url:
        _classify_existing_discovery_candidate(candidate)
    elif source_type == "issuer_investor_relations":
        domain = _issuer_site_domain(hints)
        if domain:
            candidate.update(
                {
                    "url": f"https://{domain}/",
                    "status": "needs_operator_review",
                    "confidence": "medium",
                    "discovery_method": "configured_issuer_domain_hint",
                    "notes": (
                        "Official-looking issuer site landing page; operator must "
                        "locate the exact annual/audited report source before values."
                    ),
                }
            )
        else:
            _mark_operator_to_fill(candidate, "No issuer domain hint is configured.")
    elif source_type == "official_disclosure":
        disclosure_domain = _disclosure_domain(hints)
        if disclosure_domain:
            candidate.update(
                {
                    "url": f"https://www.{disclosure_domain}/"
                    if disclosure_domain == "e-disclosure.ru"
                    else f"https://{disclosure_domain}/",
                    "status": "needs_operator_review",
                    "confidence": "medium",
                    "discovery_method": "configured_official_disclosure_hint",
                    "notes": (
                        "Official disclosure system candidate; operator must locate "
                        "the issuer report page and exact document."
                    ),
                }
            )
        else:
            _mark_operator_to_fill(candidate, "No official disclosure hint is configured.")
    elif source_type == "issuer_annual_report_pdf":
        candidate.update(
            {
                "url": "",
                "status": "operator_to_find",
                "confidence": "low",
                "discovery_method": "exact_pdf_not_invented",
                "notes": "Exact annual/audited report PDF is required before candidate-fill.",
            }
        )
    else:
        _mark_operator_to_fill(candidate, "Unsupported discovery source type.")

    if probe_urls and candidate.get("url") and candidate.get("status") != "blocked_source":
        probe = _probe_url(
            str(candidate["url"]),
            timeout_seconds=probe_timeout_seconds,
            max_bytes=max_probe_bytes,
        )
        candidate["probe_status"] = probe.get("status")
        candidate["probe_http_status"] = probe.get("http_status")
        candidate["probe_content_type"] = probe.get("content_type")
        candidate["probe_error"] = probe.get("error")
        if probe.get("status") == "ok" and candidate.get("status") == "needs_operator_review":
            candidate["status"] = "discovered_candidate"
            candidate["confidence"] = "high"
            candidate["notes"] = (
                str(candidate.get("notes") or "")
                + " Lightweight probe succeeded; still not approved for financial values."
            ).strip()
        elif probe.get("status") != "ok":
            candidate["notes"] = (
                str(candidate.get("notes") or "")
                + " Probe failed or was inconclusive; operator review required."
            ).strip()

    _strip_financial_values(candidate)
    return candidate


def _classify_existing_discovery_candidate(candidate: dict[str, Any]) -> None:
    source_type = str(candidate.get("source_type") or "").strip()
    url = str(candidate.get("url") or candidate.get("source_url") or "").strip()
    document_title = str(candidate.get("document_title") or "").strip()
    document_date = str(candidate.get("document_date") or "").strip()
    text = " ".join([source_type, url, document_title, str(candidate.get("notes") or "")])
    if _has_blocked_source_hint(text):
        candidate.update(
            {
                "status": "blocked_source",
                "confidence": "none",
                "discovery_method": "existing_source_blocked_domain",
                "notes": "Blocked source; do not use for financial report evidence.",
            }
        )
        return
    classification = classify_source_url(url, allow_unknown_source=True)
    if classification["status"] == "official" and source_type in ALLOWED_SOURCE_TYPES:
        if document_title and document_date:
            candidate.update(
                {
                    "status": "valid_official_source",
                    "confidence": "high",
                    "discovery_method": "existing_source_validated",
                }
            )
        else:
            candidate.update(
                {
                    "status": "needs_operator_review",
                    "confidence": "medium",
                    "discovery_method": "existing_official_like_source",
                    "notes": (
                        str(candidate.get("notes") or "")
                        or "Official-looking source; exact document metadata must be confirmed."
                    ),
                }
            )
        return
    candidate.update(
        {
            "status": "needs_operator_review",
            "confidence": "low",
            "discovery_method": "existing_unknown_domain",
            "notes": (
                str(candidate.get("notes") or "")
                or "Source domain is not blocked but is not in the official allowlist; operator review required."
            ),
        }
    )


def _mark_operator_to_fill(candidate: dict[str, Any], notes: str) -> None:
    candidate.update(
        {
            "url": "",
            "status": "operator_to_fill",
            "confidence": "low",
            "discovery_method": "no_configured_hint",
            "notes": notes,
        }
    )


def run_source_validate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuer_sources: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    downloaded_documents: list[dict[str, Any]] = []

    if args.source_intake_input is None:
        errors.append({"message": "source-validate mode requires --source-intake-input"})
    if not errors:
        try:
            issuer_sources = load_source_intake(args.source_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    for issuer in issuer_sources:
        for source in issuer.get("source_candidates") or []:
            result = validate_source_candidate(
                source,
                issuer=issuer,
                allow_unknown_source=args.allow_unknown_source,
            )
            source_results.append(result)
            warnings.extend(result["warnings"])
            errors.extend(result["errors"])
            if (
                args.download_source_documents
                and not result["errors"]
                and source.get("url")
                and args.source_download_dir is not None
            ):
                download = _download_source_document(source["url"], args.source_download_dir)
                downloaded_documents.append(download)
                warnings.extend(download.get("warnings") or [])
                errors.extend(download.get("errors") or [])

    invalid_count = sum(1 for item in source_results if item["errors"])
    valid_count = len(source_results) - invalid_count
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": "source-validate",
        "issuer_count": len(issuer_sources),
        "valid_source_count": valid_count,
        "invalid_source_count": invalid_count,
        "source_results": source_results,
        "downloaded_documents": downloaded_documents,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("source-validate", status),
        **SAFETY_FLAGS,
    }


def validate_source_candidate(
    source: dict[str, Any],
    *,
    issuer: dict[str, Any] | None = None,
    allow_unknown_source: bool = False,
) -> dict[str, Any]:
    issuer = issuer or {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source_type = str(source.get("source_type") or "").strip()
    url = str(source.get("url") or source.get("source_url") or "").strip()
    document_title = str(source.get("document_title") or source.get("source_document_title") or "").strip()
    document_date = str(source.get("document_date") or source.get("source_document_date") or "").strip()
    report_period = str(source.get("report_period") or "").strip()
    expected_period = str(issuer.get("period_year") or issuer.get("report_period") or "").strip()
    source_text = " ".join([source_type, url, document_title, str(source.get("notes") or "")]).casefold()
    source_status = str(source.get("status") or "").strip()
    discovery_method = str(source.get("discovery_method") or "").strip()

    base = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name"),
        "source_type": source_type,
        "url": url,
    }
    if source_type not in ALLOWED_SOURCE_TYPES:
        errors.append({**base, "message": "source_type is not allowed for Task 96 evidence"})
    if not url:
        if source_status in {"operator_to_find", "operator_to_fill"} and discovery_method:
            warnings.append(
                {
                    **base,
                    "message": "URL is still operator-provided; exact official source is required before values",
                }
            )
        else:
            errors.append({**base, "message": "URL is required for source validation"})
    if _has_blocked_source_hint(source_text):
        errors.append({**base, "message": "blocked source detected; unofficial source is not allowed"})
    if url and not _has_blocked_source_hint(source_text):
        domain_check = classify_source_url(url, allow_unknown_source=allow_unknown_source)
        if domain_check["status"] == "blocked":
            errors.append({**base, "message": domain_check["message"]})
        elif domain_check["status"] == "unknown_error":
            errors.append({**base, "message": domain_check["message"]})
        elif domain_check["status"] == "unknown_warning":
            warnings.append({**base, "message": domain_check["message"]})
    if not document_title:
        if source_status == "needs_operator_review" and url:
            warnings.append(
                {
                    **base,
                    "message": (
                        "document_title is missing because this is a discovery candidate; "
                        "operator must confirm exact report evidence"
                    ),
                }
            )
        elif source_status in {"operator_to_find", "operator_to_fill"} and discovery_method:
            warnings.append({**base, "message": "document_title is still operator-provided"})
        else:
            errors.append({**base, "message": "document_title is required"})
    if not document_date:
        warnings.append({**base, "message": "document_date is recommended when available"})
    if expected_period and report_period and report_period != expected_period:
        errors.append(
            {
                **base,
                "message": "report_period does not match requested period",
                "report_period": report_period,
                "expected_period": expected_period,
            }
        )

    return {
        **base,
        "status": "invalid" if errors else "warning" if warnings else "valid",
        "warnings": warnings,
        "errors": errors,
    }


def run_document_resolve(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuer_sources: list[dict[str, Any]] = []
    operator_documents: dict[tuple[str, str], list[dict[str, Any]]] = {}

    if args.source_intake_input is None:
        errors.append({"message": "document-resolve mode requires --source-intake-input"})
    if not errors:
        try:
            issuer_sources = load_source_intake(args.source_intake_input)
            operator_documents = load_document_intake_by_key(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    issuers: list[dict[str, Any]] = []
    resolved_source_intake: list[dict[str, Any]] = []
    if not errors:
        for issuer in issuer_sources:
            issuer_key = _issuer_source_key(issuer)
            resolved_issuer, source_issuer = resolve_issuer_documents(
                issuer,
                args,
                operator_documents=(
                    operator_documents.get((issuer_key, str(args.report_period)))
                    or operator_documents.get((issuer_key, ""))
                    or []
                ),
            )
            issuers.append(resolved_issuer)
            resolved_source_intake.append(source_issuer)
            warnings.extend(resolved_issuer.get("warnings") or [])
            errors.extend(resolved_issuer.get("errors") or [])

    flat_documents = [
        document
        for issuer in issuers
        for document in issuer.get("document_candidates") or []
    ]
    if args.download_documents and args.document_download_dir is not None:
        for document in flat_documents:
            if document.get("document_status") != "valid_official_document":
                continue
            download = _download_valid_document(document, args.document_download_dir)
            document["download"] = download
            warnings.extend(download.get("warnings") or [])
            errors.extend(download.get("errors") or [])

    document_candidate_count = len(flat_documents)
    resolved_document_count = sum(
        1
        for document in flat_documents
        if document.get("document_status") == "valid_official_document"
    )
    review_count = sum(
        1
        for document in flat_documents
        if document.get("document_status") in {"operator_to_find", "needs_operator_review"}
    )
    invalid_count = sum(
        1
        for document in flat_documents
        if document.get("document_status") in {"invalid_document", "blocked_document"}
    )
    if not errors and args.source_intake_output is not None:
        write_json_report(
            {
                "status": "resolved",
                "mode": "document-resolve",
                "issuer_count": len(resolved_source_intake),
                "issuer_sources": resolved_source_intake,
                **SAFETY_FLAGS,
            },
            args.source_intake_output,
        )
    document_report = {
        "status": "failed" if errors else "warning",
        "mode": "document-resolve",
        "issuer_count": len(issuers),
        "document_candidate_count": document_candidate_count,
        "resolved_document_count": resolved_document_count,
        "needs_operator_review_count": review_count,
        "invalid_document_count": invalid_count,
        "issuers": issuers,
        "warnings": warnings,
        "errors": errors,
        **SAFETY_FLAGS,
    }
    if not errors and resolved_document_count >= len(issuers) and issuers:
        document_report["status"] = "passed"
    elif errors:
        document_report["status"] = "failed"
    if args.document_output is not None:
        write_json_report(document_report, args.document_output)
    if args.document_checklist_output is not None:
        write_document_checklist(issuers, args.document_checklist_output)
    document_report["document_output"] = _path_value(args.document_output)
    document_report["document_checklist_output"] = _path_value(args.document_checklist_output)
    document_report["source_intake_output"] = _path_value(args.source_intake_output)
    document_report["next_steps"] = _next_steps("document-resolve", document_report["status"])
    return document_report


def resolve_issuer_documents(
    issuer: dict[str, Any],
    args: argparse.Namespace,
    *,
    operator_documents: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_issuer = dict(issuer)
    source_candidates = [dict(source) for source in issuer.get("source_candidates") or []]
    documents: list[dict[str, Any]] = [
        build_document_candidate_from_source(issuer, source, args)
        for source in source_candidates
    ]
    for document in operator_documents:
        merged = build_operator_document_candidate(issuer, document, args)
        validation = validate_document_intake_item(
            document,
            args=args,
            issuer=issuer,
        )
        merged["warnings"] = validation["warnings"]
        merged["errors"] = validation["errors"]
        if validation["errors"]:
            merged["document_status"] = "invalid_document"
        elif validation["document_status"] == "needs_operator_review":
            merged["document_status"] = "needs_operator_review"
            merged["confidence"] = "low"
        else:
            merged["document_status"] = "valid_official_document"
            merged["confidence"] = "high"
        _strip_financial_values(merged)
        documents.append(merged)
        _merge_document_into_source_candidates(source_candidates, merged)

    issuer_errors = [
        error
        for document in documents
        for error in document.get("errors") or []
    ]
    issuer_warnings = [
        warning
        for document in documents
        for warning in document.get("warnings") or []
    ]
    source_issuer["source_candidates"] = source_candidates
    resolved_issuer = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name"),
        "canonical_company_id": issuer.get("canonical_company_id"),
        "canonical_company_name": issuer.get("canonical_company_name"),
        "report_period": str(args.report_period),
        "report_type": args.report_type,
        "accounting_standard": args.accounting_standard,
        "prefer_audited": bool(args.prefer_audited),
        "prefer_consolidated": bool(args.prefer_consolidated),
        "document_candidates": documents,
        "recommended_operator_actions": _recommended_document_actions(args),
        "warnings": issuer_warnings,
        "errors": issuer_errors,
    }
    return resolved_issuer, source_issuer


def build_document_candidate_from_source(
    issuer: dict[str, Any],
    source: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    source_type = str(source.get("source_type") or "").strip()
    source_url = str(source.get("url") or source.get("source_url") or "").strip()
    source_status = str(source.get("status") or "").strip()
    document_url = ""
    document_title = ""
    document_date = ""
    source_file_name = ""
    document_status = "operator_to_find"
    confidence = "low"
    resolution_method = "landing_page_requires_operator"
    notes = "Exact annual/audited report document must be selected before candidate-fill."

    if source_url and source_status == "valid_official_source":
        document_url = source_url
        document_title = str(source.get("document_title") or "").strip()
        document_date = str(source.get("document_date") or "").strip()
        source_file_name = str(source.get("source_file_name") or _file_name_from_url(source_url) or "").strip()
        document_status = "reviewed_official_document"
        confidence = "medium"
        resolution_method = "source_candidate_already_exact"
        notes = "Existing source candidate appears exact; validation still required."
    elif source_url and source_type in {"issuer_investor_relations", "official_disclosure"}:
        document_status = "needs_operator_review"
        confidence = "medium"
    elif source_type == "issuer_annual_report_pdf":
        document_status = "operator_to_find"
        confidence = "low"
        resolution_method = "exact_pdf_not_invented"

    candidate = {
        "source_type": source_type,
        "source_url": source_url,
        "document_url": document_url,
        "document_title": document_title,
        "document_date": document_date,
        "report_period": str(args.report_period),
        "report_type": args.report_type,
        "accounting_standard": args.accounting_standard,
        "source_file_name": source_file_name,
        "document_status": document_status,
        "confidence": confidence,
        "resolution_method": resolution_method,
        "operator_action": "select_exact_official_report_document",
        "notes": notes,
    }
    _strip_financial_values(candidate)
    return candidate


def build_operator_document_candidate(
    issuer: dict[str, Any],
    document: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    candidate = {
        "source_type": document.get("source_type") or "official_issuer_report",
        "source_url": document.get("source_url") or document.get("document_url") or "",
        "document_url": document.get("document_url") or "",
        "document_title": document.get("document_title") or "",
        "document_date": document.get("document_date") or "",
        "report_period": str(document.get("report_period") or args.report_period),
        "report_type": document.get("report_type") or args.report_type,
        "accounting_standard": document.get("accounting_standard") or args.accounting_standard,
        "source_file_name": document.get("source_file_name") or _file_name_from_url(document.get("document_url") or ""),
        "document_status": document.get("document_status")
        or (
            "reviewed_official_document"
            if document.get("operator_review_status") in DOCUMENT_INTAKE_REVIEWED_STATUSES
            else "needs_operator_review"
        ),
        "confidence": document.get("confidence") or "medium",
        "resolution_method": "operator_reviewed_exact_document",
        "operator_action": "validate_exact_official_report_document",
        "operator_review_status": document.get("operator_review_status"),
        "notes": document.get("notes") or "Operator-provided exact official document candidate.",
    }
    for key, value in document.items():
        if key not in candidate and key not in ALL_FINANCIAL_FIELDS and key != "values":
            candidate[key] = value
    _strip_financial_values(candidate)
    return candidate


def run_document_validate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuers: list[dict[str, Any]] = []
    if args.document_input is None:
        errors.append({"message": "document-validate mode requires --document-input"})
    if not errors:
        try:
            issuers = load_document_issuers(args.document_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    validation_results: list[dict[str, Any]] = []
    for issuer in issuers:
        for document in issuer.get("document_candidates") or []:
            result = validate_document_candidate(
                document,
                issuer=issuer,
                allow_unknown_source=args.allow_unknown_source,
                target_report_period=str(args.report_period),
            )
            validation_results.append(result)
            warnings.extend(result["warnings"])
            errors.extend(result["errors"])

    valid_count = sum(
        1
        for result in validation_results
        if not result["errors"] and result.get("document_status") == "valid_official_document"
    )
    invalid_count = sum(1 for result in validation_results if result["errors"])
    review_count = sum(
        1
        for result in validation_results
        if result.get("document_status") in {"operator_to_find", "needs_operator_review"}
        and not result["errors"]
    )
    status = "failed" if errors else "warning" if warnings or review_count else "passed"
    return {
        "status": status,
        "mode": "document-validate",
        "issuer_count": len(issuers),
        "document_candidate_count": len(validation_results),
        "valid_document_count": valid_count,
        "invalid_document_count": invalid_count,
        "needs_operator_review_count": review_count,
        "document_results": validation_results,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("document-validate", status),
        **SAFETY_FLAGS,
    }


def run_document_intake_template(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issuers: list[dict[str, Any]] = []
    if args.document_input is None:
        errors.append({"message": "document-intake-template mode requires --document-input"})
    if not errors:
        try:
            issuers = load_document_issuers(args.document_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    documents: list[dict[str, Any]] = []
    if not errors:
        for issuer in issuers:
            item = build_document_intake_template_item(issuer, args)
            if item is not None:
                documents.append(item)

    report = {
        "status": "failed" if errors else "template",
        "mode": "document-intake-template",
        "issuer_count": len(documents),
        "document_template_count": len(documents),
        "documents": documents,
        "warnings": warnings,
        "errors": errors,
        "document_intake_output": _path_value(args.document_intake_output),
        "document_intake_csv_output": _path_value(args.document_intake_csv_output),
        "next_steps": _next_steps("document-intake-template", "template" if not errors else "failed"),
        **SAFETY_FLAGS,
    }
    if args.document_intake_output is not None and not errors:
        write_json_report(report, args.document_intake_output)
    if args.document_intake_csv_output is not None and not errors:
        write_document_intake_csv(documents, args.document_intake_csv_output)
    return report


def build_document_intake_template_item(
    issuer: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    candidates = issuer.get("document_candidates") or []
    unresolved = [
        document
        for document in candidates
        if str(document.get("document_status") or "") in DOCUMENT_INTAKE_UNRESOLVED_STATUSES
    ]
    if not unresolved:
        return None
    source_context = _source_url_context(unresolved)
    first = unresolved[0] if unresolved else {}
    item = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name"),
        "canonical_company_id": issuer.get("canonical_company_id"),
        "canonical_company_name": issuer.get("canonical_company_name"),
        "report_period": str(first.get("report_period") or issuer.get("report_period") or args.report_period),
        "report_type": first.get("report_type") or issuer.get("report_type") or args.report_type,
        "accounting_standard": (
            first.get("accounting_standard")
            or issuer.get("accounting_standard")
            or args.accounting_standard
        ),
        "source_type": "official_issuer_report",
        "source_url_context": source_context,
        "document_url": "",
        "document_title": "",
        "document_date": "",
        "source_file_name": "",
        "operator_review_status": args.document_intake_template_status,
        "notes": "Paste exact official annual/audited report page or PDF URL. Do not paste landing page.",
    }
    _strip_financial_values(item)
    return item


def run_document_intake_validate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    if args.document_intake_input is None:
        errors.append({"message": "document-intake-validate mode requires --document-intake-input"})
    if not errors:
        try:
            documents = load_document_intake_items(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    validation_results: list[dict[str, Any]] = []
    for document in documents:
        result = validate_document_intake_item(
            document,
            args=args,
            issuer=_issuer_from_document_intake_item(document),
        )
        validation_results.append(result)
        warnings.extend(result["warnings"])
        errors.extend(result["errors"])

    valid_count = sum(
        1
        for result in validation_results
        if not result["errors"] and result.get("document_status") == "valid_official_document"
    )
    invalid_count = sum(1 for result in validation_results if result["errors"])
    review_count = sum(
        1
        for result in validation_results
        if not result["errors"] and result.get("document_status") == "needs_operator_review"
    )
    status = "failed" if errors else "warning" if warnings or review_count else "passed"
    return {
        "status": status,
        "mode": "document-intake-validate",
        "issuer_count": len({_document_company_key(item) for item in documents}),
        "document_candidate_count": len(validation_results),
        "valid_document_count": valid_count,
        "invalid_document_count": invalid_count,
        "needs_operator_review_count": review_count,
        "document_results": validation_results,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("document-intake-validate", status),
        **SAFETY_FLAGS,
    }


def validate_document_intake_item(
    document: dict[str, Any],
    *,
    args: argparse.Namespace,
    issuer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issuer = issuer or {}
    operator_review_status = str(document.get("operator_review_status") or "").strip()
    document_url = str(document.get("document_url") or "").strip()
    document_title = str(document.get("document_title") or "").strip()
    report_period = str(document.get("report_period") or "").strip()
    base = {
        "company_id": issuer.get("company_id") or document.get("company_id"),
        "company_name": issuer.get("company_name") or document.get("company_name"),
        "source_type": document.get("source_type"),
        "document_url": document_url,
    }
    document_status = (
        "valid_official_document"
        if operator_review_status in DOCUMENT_INTAKE_REVIEWED_STATUSES
        else "needs_operator_review"
    )
    if document_url:
        classification = classify_source_url(
            document_url,
            allow_unknown_source=args.allow_unknown_source,
        )
        if classification["status"] == "unknown_warning":
            document_status = "needs_operator_review"
    candidate = dict(document)
    candidate["document_status"] = document.get("document_status") or document_status
    candidate["source_url"] = document.get("source_url") or document_url
    result = validate_document_candidate(
        candidate,
        issuer=issuer,
        allow_unknown_source=args.allow_unknown_source,
        target_report_period=str(args.report_period),
    )
    warnings = list(result["warnings"])
    errors = list(result["errors"])
    if args.require_operator_reviewed and operator_review_status not in DOCUMENT_INTAKE_REVIEWED_STATUSES:
        errors.append(
            {
                **base,
                "message": "operator_review_status must be reviewed or operator_reviewed",
                "operator_review_status": operator_review_status,
            }
        )
    if not document_url:
        errors.append({**base, "message": "document_url is required for operator exact document intake"})
    if not document_title:
        errors.append({**base, "message": "document_title is required for operator exact document intake"})
    if not report_period:
        errors.append({**base, "message": "report_period is required for operator exact document intake"})
    warnings.extend(_document_title_quality_warnings(candidate, base, args))
    effective_status = candidate["document_status"]
    if result.get("domain_status") == "unknown_warning":
        effective_status = "needs_operator_review"
    elif errors:
        effective_status = "invalid_document"
    elif effective_status in DOCUMENT_VALID_STATUSES:
        effective_status = "valid_official_document"
    else:
        effective_status = "needs_operator_review"
    return {
        **result,
        "document_status": effective_status,
        "operator_review_status": operator_review_status,
        "status": "invalid" if errors else "warning" if warnings else "valid",
        "warnings": warnings,
        "errors": errors,
    }


def validate_document_candidate(
    document: dict[str, Any],
    *,
    issuer: dict[str, Any] | None = None,
    allow_unknown_source: bool = False,
    target_report_period: str | None = None,
) -> dict[str, Any]:
    issuer = issuer or {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    document_status = str(document.get("document_status") or "").strip()
    source_type = str(document.get("source_type") or "").strip()
    document_url = str(document.get("document_url") or "").strip()
    document_title = str(document.get("document_title") or "").strip()
    document_date = str(document.get("document_date") or "").strip()
    report_period = str(document.get("report_period") or "").strip()
    source_file_name = str(document.get("source_file_name") or "").strip()
    base = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name"),
        "source_type": source_type,
        "document_url": document_url,
        "document_status": document_status,
    }
    forbidden_fields = [
        field
        for field in FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS
        if document.get(field) not in (None, "")
    ]
    if forbidden_fields or document.get("values"):
        errors.append(
            {
                **base,
                "message": "financial values are forbidden in document metadata",
                "fields": forbidden_fields or ["values"],
            }
        )
    if source_type not in ALLOWED_SOURCE_TYPES:
        errors.append({**base, "message": "source_type is not allowed for document evidence"})
    if document_status not in DOCUMENT_REVIEW_STATUSES:
        warnings.append({**base, "message": "document_status is not recognized; operator review required"})
    if not document_url:
        if document_status in DOCUMENT_VALID_STATUSES:
            errors.append({**base, "message": "document_url is required for resolved official documents"})
        else:
            warnings.append({**base, "message": "document_url is missing; exact report document required"})
    domain_status = None
    if document_url:
        classification = classify_source_url(document_url, allow_unknown_source=allow_unknown_source)
        domain_status = classification["status"]
        if classification["status"] == "blocked":
            errors.append({**base, "message": classification["message"]})
        elif classification["status"] == "unknown_error":
            errors.append({**base, "message": classification["message"]})
        elif classification["status"] == "unknown_warning":
            warnings.append({**base, "message": classification["message"]})
            if document_status in DOCUMENT_VALID_STATUSES:
                errors.append(
                    {
                        **base,
                        "message": "unknown domain cannot be marked valid_official_document",
                    }
                )
    if document_url and _looks_like_landing_page(document_url):
        message = "landing page is not exact annual/audited report evidence"
        if document_status in DOCUMENT_VALID_STATUSES:
            errors.append({**base, "message": message})
        else:
            warnings.append({**base, "message": message})
    if document_status in DOCUMENT_VALID_STATUSES and not document_title:
        errors.append({**base, "message": "document_title is required for exact documents"})
    elif not document_title:
        warnings.append({**base, "message": "document_title is missing; operator must confirm exact document"})
    if not document_date:
        warnings.append({**base, "message": "document_date is recommended when available"})
    if document_url and not source_file_name:
        warnings.append({**base, "message": "source_file_name is recommended"})
    if target_report_period and report_period and report_period != target_report_period:
        errors.append(
            {
                **base,
                "message": "report_period does not match target period",
                "report_period": report_period,
                "target_report_period": target_report_period,
            }
        )
    return {
        **base,
        "report_period": report_period,
        "domain_status": domain_status,
        "status": "invalid" if errors else "warning" if warnings else "valid",
        "warnings": warnings,
        "errors": errors,
    }


def run_candidate_fill(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    template_rows: list[dict[str, Any]] = []
    issuer_sources: list[dict[str, Any]] = []
    manual_items_by_key: dict[str, dict[str, Any]] = {}
    candidate_rows: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []

    if args.financial_template_input is None:
        errors.append({"message": "candidate-fill mode requires --financial-template-input"})
    if args.source_intake_input is None:
        errors.append({"message": "candidate-fill mode requires --source-intake-input"})
    if args.manual_values_json is None and args.manual_values_csv is None:
        warnings.append({"message": "no manual values file provided; candidate rows will remain empty"})

    if not errors:
        try:
            template_rows = load_template_rows(args.financial_template_input)
            issuer_sources = load_source_intake(args.source_intake_input)
            manual_items_by_key = load_manual_values_by_key(
                manual_values_json=args.manual_values_json,
                manual_values_csv=args.manual_values_csv,
            )
        except Exception as exc:
            errors.append({"message": str(exc)})

    source_by_key = {_issuer_source_key(item): item for item in issuer_sources}
    if not errors:
        for row in template_rows:
            key = _company_key(row)
            manual_item = manual_items_by_key.get(key)
            issuer_source = source_by_key.get(key)
            candidate, evidence = build_candidate_row(
                row,
                manual_item=manual_item,
                issuer_source=issuer_source,
                allow_unknown_source=args.allow_unknown_source,
            )
            candidate_rows.append(candidate)
            evidence_items.append(evidence)
            warnings.extend(evidence.get("warnings") or [])
            errors.extend(evidence.get("errors") or [])

    if args.candidate_output is not None and not errors:
        write_candidate_rows(candidate_rows, args.candidate_output, args.candidate_format)
    evidence_report = {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "mode": "candidate-fill",
        "read_only": True,
        "dry_run_only": True,
        "import_executed": False,
        "items": evidence_items,
        "warnings": warnings,
        "errors": errors,
    }
    if args.evidence_output is not None:
        write_json_report(evidence_report, args.evidence_output)

    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "mode": "candidate-fill",
        "issuer_count": len(template_rows),
        "candidate_rows": len(candidate_rows),
        "candidate_output": _path_value(args.candidate_output),
        "candidate_format": args.candidate_format,
        "evidence_output": _path_value(args.evidence_output),
        "evidence": evidence_report,
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("candidate-fill", status),
        **SAFETY_FLAGS,
    }


def build_candidate_row(
    template_row: dict[str, Any],
    *,
    manual_item: dict[str, Any] | None,
    issuer_source: dict[str, Any] | None,
    allow_unknown_source: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = dict(template_row)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    filled_fields: list[str] = []
    missing_fields: list[str] = []
    field_evidence: dict[str, Any] = {}
    company_id = row.get("company_id")
    company_name = row.get("company_name")
    source = _source_from_manual_or_intake(manual_item, issuer_source)

    if source:
        row["source"] = source.get("source_type") or row.get("source") or "official_issuer_report"
        row["source_url"] = source.get("source_url") or source.get("url") or ""
        row["source_file_name"] = source.get("source_file_name") or row.get("source_file_name") or ""
        row["source_document_title"] = source.get("source_document_title") or source.get("document_title") or ""
        row["source_document_date"] = source.get("source_document_date") or source.get("document_date") or ""

    values = (manual_item or {}).get("values") or {}
    if manual_item and not isinstance(values, dict):
        errors.append(
            {
                "company_id": company_id,
                "message": "manual values field must be an object",
            }
        )
        values = {}

    source_errors = _validate_source_for_candidate(
        row,
        values_present=bool(_present_value_fields(values)),
        allow_unknown_source=allow_unknown_source,
    )
    warnings.extend(source_errors["warnings"])
    errors.extend(source_errors["errors"])

    for field in EVIDENCE_FINANCIAL_FIELDS:
        evidence = values.get(field) if isinstance(values, dict) else None
        normalized = _normalize_evidence_value(evidence)
        field_evidence[field] = normalized
        if not normalized["has_value"]:
            row[field] = ""
            missing_fields.append(field)
            continue
        bad_data_message = _bad_data_message(field, normalized)
        if bad_data_message:
            row[field] = ""
            missing_fields.append(field)
            errors.append(
                {
                    "company_id": company_id,
                    "field": field,
                    "message": bad_data_message,
                }
            )
            continue
        if not normalized["has_evidence"]:
            row[field] = ""
            missing_fields.append(field)
            warnings.append(
                {
                    "company_id": company_id,
                    "field": field,
                    "message": f"{field} has a value but no page/table/evidence_note; left empty",
                }
            )
            continue
        row[field] = normalized["value"]
        filled_fields.append(field)

    zero_fields = [
        field
        for field in filled_fields
        if str(row.get(field) or "").strip() in {"0", "0.0", "0.00"}
    ]
    if len(zero_fields) >= 3:
        errors.append(
            {
                "company_id": company_id,
                "message": "many financial fields are zero; verify that placeholders were not entered as values",
                "fields": zero_fields,
            }
        )

    first_filled = next((field_evidence[field] for field in filled_fields), None)
    if first_filled:
        row["source_page"] = first_filled.get("page") or row.get("source_page") or ""
        row["source_table"] = first_filled.get("table") or row.get("source_table") or ""
        row["source_notes"] = first_filled.get("evidence_note") or row.get("source_notes") or ""
    row["review_status"] = (
        (manual_item or {}).get("operator_review_status")
        or (manual_item or {}).get("review_status")
        or "pending"
    )
    row["review_notes"] = _review_notes(filled_fields, missing_fields)

    evidence_status = "invalid_source" if any("source" in item.get("message", "") for item in errors) else "valid_official_source"
    if not source or not (row.get("source_url") or row.get("source_file_name")):
        evidence_status = "missing_source"
    if errors:
        evidence_status = "invalid_source"

    evidence_item = {
        "company_id": _as_int(company_id),
        "company_name": company_name,
        "canonical_company_id": _as_int(row.get("canonical_company_id") or company_id),
        "canonical_company_name": row.get("canonical_company_name") or company_name,
        "period_year": _as_int(row.get("period_year")),
        "source_status": evidence_status,
        "source_url": row.get("source_url"),
        "source_file_name": row.get("source_file_name"),
        "source_document_title": row.get("source_document_title"),
        "source_document_date": row.get("source_document_date"),
        "filled_fields": filled_fields,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "errors": errors,
        "field_evidence": field_evidence,
    }
    return row, evidence_item


def run_preview(args: argparse.Namespace, *, http_request: Any) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if args.candidate_input is None:
        errors.append({"message": "preview mode requires --candidate-input"})
        return {
            "status": "failed",
            "mode": "preview",
            "row_count": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "warnings": warnings,
            "errors": errors,
            "next_steps": _next_steps("preview", "failed"),
            **SAFETY_FLAGS,
        }

    preflight = validate_candidate_preview_sources(
        args.candidate_input,
        args.format,
        allow_unknown_source=args.allow_unknown_source,
    )
    warnings.extend(preflight.get("warnings") or [])
    errors.extend(preflight.get("errors") or [])
    if errors:
        return {
            "status": "failed",
            "mode": "preview",
            "candidate_input": str(args.candidate_input),
            "row_count": preflight.get("row_count", 0),
            "valid_rows": preflight.get("valid_rows", 0),
            "invalid_rows": preflight.get("invalid_rows", 0),
            "source_preflight": preflight,
            "warnings": warnings,
            "errors": errors,
            "next_steps": _next_steps("preview", "failed"),
            **SAFETY_FLAGS,
        }

    pack_args = argparse.Namespace(
        backend_url=args.backend_url,
        mode="preview",
        reviewed_input=args.candidate_input,
        format=args.format,
    )
    pack_report, _exit_code = collection_pack.run_pack(pack_args, http_request=http_request)
    report = {
        **pack_report,
        "mode": "preview",
        "candidate_input": str(args.candidate_input),
        "source_assistant_preview": True,
        "next_steps": _next_steps("preview", pack_report.get("status", "failed")),
        **SAFETY_FLAGS,
    }
    return report


def validate_candidate_preview_sources(
    path: Path,
    format_value: str,
    *,
    allow_unknown_source: bool = False,
) -> dict[str, Any]:
    try:
        rows = load_template_rows(path) if format_value == "csv" else _load_candidate_json_rows(path)
    except Exception as exc:
        return {
            "status": "failed",
            "row_count": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "warnings": [],
            "errors": [{"message": str(exc)}],
        }

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    invalid_indexes: set[int] = set()
    for row_index, row in enumerate(rows, start=1):
        values_present = any(row.get(field) not in (None, "") for field in ALL_FINANCIAL_FIELDS)
        check = _validate_source_for_candidate(
            row,
            values_present=values_present,
            allow_unknown_source=allow_unknown_source,
        )
        for warning in check["warnings"]:
            warnings.append({"row_index": row_index, **warning})
        for error in check["errors"]:
            errors.append({"row_index": row_index, **error})
            invalid_indexes.add(row_index)
    return {
        "status": "failed" if errors else "warning" if warnings else "passed",
        "row_count": len(rows),
        "valid_rows": len(rows) - len(invalid_indexes),
        "invalid_rows": len(invalid_indexes),
        "warnings": warnings,
        "errors": errors,
    }


def load_template_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"financial template input does not exist: {path}")
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("financial template JSON must be a list or object with rows")
        return [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]


def _load_candidate_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"candidate input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("candidate JSON must be a list or object with rows")
    return [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]


def load_source_intake(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"source intake input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("issuer_sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("source intake JSON must contain issuer_sources")
    return rows


def load_document_intake_by_key(path: Path | None) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if path is None:
        return {}
    documents = load_document_intake_items(path)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for document in documents:
        key = str(document.get("canonical_company_id") or document.get("company_id") or "")
        period = str(document.get("report_period") or "")
        grouped.setdefault((key, period), []).append(document)
    return grouped


def load_document_intake_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"document intake input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    documents = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
        raise ValueError("document intake JSON must contain documents")
    return documents


def load_document_issuers(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"document input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    issuers = payload.get("issuers") if isinstance(payload, dict) else payload
    if not isinstance(issuers, list) or not all(isinstance(item, dict) for item in issuers):
        raise ValueError("document JSON must contain issuers")
    return issuers


def write_document_checklist(issuers: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCUMENT_CHECKLIST_FIELDS)
        writer.writeheader()
        rank = 0
        for issuer in issuers:
            for document in issuer.get("document_candidates") or []:
                rank += 1
                writer.writerow(
                    {
                        "rank": rank,
                        "company_id": _csv_value(issuer.get("company_id")),
                        "company_name": _csv_value(issuer.get("company_name")),
                        "canonical_company_id": _csv_value(issuer.get("canonical_company_id")),
                        "canonical_company_name": _csv_value(issuer.get("canonical_company_name")),
                        "report_period": _csv_value(document.get("report_period") or issuer.get("report_period")),
                        "report_type": _csv_value(document.get("report_type") or issuer.get("report_type")),
                        "accounting_standard": _csv_value(
                            document.get("accounting_standard") or issuer.get("accounting_standard")
                        ),
                        "source_type": _csv_value(document.get("source_type")),
                        "source_url": _csv_value(document.get("source_url")),
                        "document_url": _csv_value(document.get("document_url")),
                        "document_title": _csv_value(document.get("document_title")),
                        "document_date": _csv_value(document.get("document_date")),
                        "source_file_name": _csv_value(document.get("source_file_name")),
                        "document_status": _csv_value(document.get("document_status")),
                        "confidence": _csv_value(document.get("confidence")),
                        "resolution_method": _csv_value(document.get("resolution_method")),
                        "operator_action": _csv_value(document.get("operator_action")),
                        "notes": _csv_value(document.get("notes")),
                    }
                )


def write_document_intake_csv(documents: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCUMENT_INTAKE_TEMPLATE_FIELDS)
        writer.writeheader()
        for document in documents:
            writer.writerow(
                {
                    field: _csv_value(document.get(field))
                    for field in DOCUMENT_INTAKE_TEMPLATE_FIELDS
                }
            )


def load_manual_values_by_key(
    *,
    manual_values_json: Path | None,
    manual_values_csv: Path | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if manual_values_json is not None:
        for item in load_manual_values_json(manual_values_json):
            result[_manual_key(item)] = item
    if manual_values_csv is not None:
        for item in load_manual_values_csv(manual_values_csv):
            result[_manual_key(item)] = item
    return result


def load_manual_values_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"manual values JSON does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        items = payload.get("items")
        if items is None and "values" in payload:
            items = [payload]
    else:
        items = payload
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("manual values JSON must contain items")
    return items


def load_manual_values_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"manual values CSV does not exist: {path}")
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        for raw in reader:
            row = {key: _normalize_cell(value) for key, value in raw.items() if key}
            key = (
                str(row.get("canonical_company_id") or row.get("company_id") or ""),
                str(row.get("company_id") or row.get("canonical_company_id") or ""),
                str(row.get("period_year") or ""),
            )
            item = grouped.setdefault(
                key,
                {
                    "canonical_company_id": row.get("canonical_company_id"),
                    "company_id": row.get("company_id"),
                    "period_year": row.get("period_year"),
                    "source_type": row.get("source_type"),
                    "source_url": row.get("source_url"),
                    "source_file_name": row.get("source_file_name"),
                    "source_document_title": row.get("source_document_title"),
                    "source_document_date": row.get("source_document_date"),
                    "operator_review_status": row.get("operator_review_status"),
                    "values": {},
                },
            )
            field_name = str(row.get("field_name") or "").strip()
            if field_name:
                item["values"][field_name] = {
                    "value": row.get("value"),
                    "page": row.get("page"),
                    "table": row.get("table"),
                    "evidence_note": row.get("evidence_note"),
                    "confidence": row.get("confidence"),
                }
    return list(grouped.values())


def write_candidate_rows(rows: list[dict[str, Any]], path: Path, format_value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if format_value == "json":
        write_json_report({"rows": rows}, path)
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=collection_pack.CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in collection_pack.CSV_FIELDS})


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    title = (
        "Official-Source Discovery"
        if report.get("mode") == "source-discover"
        else "Exact Document Intake Template"
        if report.get("mode") == "document-intake-template"
        else "Exact Document Intake Validation"
        if report.get("mode") == "document-intake-validate"
        else "Exact Official Report Document Resolver"
        if report.get("mode") in {"document-resolve", "document-validate"}
        else "Official-Source Evidence Assistant"
    )
    lines = [
        f"# {title}",
        "",
        "## Overall Status",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- issuer_count: {report.get('issuer_count', report.get('row_count', 0))}",
        f"- candidate_count: {report.get('candidate_count', 0)}",
        f"- read_only: {report.get('read_only')}",
        f"- dry_run_only: {report.get('dry_run_only')}",
        f"- import_executed: {report.get('import_executed')}",
        "",
    ]
    if report.get("mode") == "source-discover":
        lines.extend(_render_discovery_markdown_sections(report))
    if report.get("mode") in {"document-resolve", "document-validate"}:
        lines.extend(_render_document_markdown_sections(report))
    if report.get("mode") in {"document-intake-template", "document-intake-validate"}:
        lines.extend(_render_document_intake_markdown_sections(report))
    lines.extend(
        [
        "## Source Validation",
        "",
        f"- valid sources: {report.get('valid_source_count', 0)}",
        f"- invalid sources: {report.get('invalid_source_count', 0)}",
        "",
        "## Candidate Fill",
        "",
        f"- candidate rows: {report.get('candidate_rows', 0)}",
        f"- candidate output: `{report.get('candidate_output')}`",
        f"- evidence output: `{report.get('evidence_output')}`",
        "",
        "## Preview",
        "",
        f"- row_count: {report.get('row_count', 0)}",
        f"- valid_rows: {report.get('valid_rows', 0)}",
        f"- invalid_rows: {report.get('invalid_rows', 0)}",
        f"- import_executed: {report.get('import_executed')}",
        "",
        "## Warnings",
        "",
        ]
    )
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(f"- {_message_text(item)}" for item in warnings)
    else:
        lines.append("- None")
    lines.extend(["", "## Errors", ""])
    errors = report.get("errors") or []
    if errors:
        lines.extend(f"- {_message_text(item)}" for item in errors)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- identity_apply_executed: {report.get('identity_apply_executed')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
            "## Next Steps",
            "",
        ]
    )
    lines.extend(f"- {step}" for step in report.get("next_steps") or [])
    return "\n".join(lines) + "\n"


def _render_discovery_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Issuer Sources",
        "",
        "| Company ID | Company | Source Type | URL | Status | Confidence | Method | Notes |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for issuer in report.get("issuer_sources") or []:
        for source in issuer.get("source_candidates") or []:
            rows += 1
            lines.append(
                "| {company_id} | {company_name} | {source_type} | {url} | {status} | {confidence} | {method} | {notes} |".format(
                    company_id=issuer.get("company_id"),
                    company_name=issuer.get("company_name") or "",
                    source_type=source.get("source_type") or "",
                    url=source.get("url") or "",
                    status=source.get("status") or "",
                    confidence=source.get("confidence") or "",
                    method=source.get("discovery_method") or "",
                    notes=str(source.get("notes") or "").replace("|", "/"),
                )
            )
    if rows == 0:
        lines.append("| None |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Validation Meaning",
            "",
            "- `discovered_candidate` is not approved as a financial value source.",
            "- `needs_operator_review` requires manual confirmation of the exact report/document.",
            "- Exact annual or audited report evidence is required before candidate-fill.",
            "",
        ]
    )
    return lines


def _render_document_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Document Status",
        "",
        f"- document_candidate_count: {report.get('document_candidate_count', 0)}",
        f"- resolved_document_count: {report.get('resolved_document_count', report.get('valid_document_count', 0))}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        "",
        "## Issuers",
        "",
        "| Company ID | Company | Source Type | Source URL | Document URL | Status | Confidence | Method | Action |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    issuers = report.get("issuers") or []
    for issuer in issuers:
        for document in issuer.get("document_candidates") or []:
            rows += 1
            lines.append(
                "| {company_id} | {company_name} | {source_type} | {source_url} | {document_url} | {status} | {confidence} | {method} | {action} |".format(
                    company_id=issuer.get("company_id"),
                    company_name=issuer.get("company_name") or "",
                    source_type=document.get("source_type") or "",
                    source_url=document.get("source_url") or "",
                    document_url=document.get("document_url") or "",
                    status=document.get("document_status") or "",
                    confidence=document.get("confidence") or "",
                    method=document.get("resolution_method") or "",
                    action=document.get("operator_action") or "",
                )
            )
    if rows == 0 and report.get("document_results"):
        for result in report.get("document_results") or []:
            rows += 1
            lines.append(
                "| {company_id} | {company_name} | {source_type} |  | {document_url} | {status} |  | validation |  |".format(
                    company_id=result.get("company_id"),
                    company_name=result.get("company_name") or "",
                    source_type=result.get("source_type") or "",
                    document_url=result.get("document_url") or "",
                    status=result.get("document_status") or "",
                )
            )
    if rows == 0:
        lines.append("| None |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Document Validation Meaning",
            "",
            "- Landing page != exact report evidence.",
            "- `needs_operator_review` is not valid for financial values.",
            "- Exact annual/audited report URL and metadata are required before candidate-fill.",
            "",
        ]
    )
    return lines


def _render_document_intake_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Document Intake",
        "",
        f"- document_template_count: {report.get('document_template_count', 0)}",
        f"- valid_document_count: {report.get('valid_document_count', 0)}",
        f"- invalid_document_count: {report.get('invalid_document_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        "",
        "## Documents To Fill",
        "",
        "| Company ID | Company | Period | Source Context | Document URL | Document Title | Operator Status |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for document in report.get("documents") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {period} | {context} | {url} | {title} | {status} |".format(
                company_id=document.get("company_id"),
                company_name=document.get("company_name") or "",
                period=document.get("report_period") or "",
                context=str(document.get("source_url_context") or "").replace("|", "/"),
                url=document.get("document_url") or "",
                title=document.get("document_title") or "",
                status=document.get("operator_review_status") or "",
            )
        )
    if rows == 0 and report.get("document_results"):
        for result in report.get("document_results") or []:
            rows += 1
            lines.append(
                "| {company_id} | {company_name} | {period} |  | {url} |  | {status} |".format(
                    company_id=result.get("company_id"),
                    company_name=result.get("company_name") or "",
                    period=result.get("report_period") or "",
                    url=result.get("document_url") or "",
                    status=result.get("operator_review_status") or result.get("document_status") or "",
                )
            )
    if rows == 0:
        lines.append("| None |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Use exact official annual/audited report page or PDF.",
            "- Do not use landing pages as final document evidence.",
            "- Do not enter financial values.",
            "- Do not use Wikipedia, blog, forum, social, news, or aggregator sources.",
            "",
        ]
    )
    return lines


def classify_source_url(url: str, *, allow_unknown_source: bool = False) -> dict[str, str]:
    text = url.casefold()
    if _has_blocked_source_hint(text):
        return {"status": "blocked", "message": "blocked unofficial source domain"}
    host = _host(url)
    if not host:
        return {"status": "unknown_error", "message": "source URL host is missing"}
    if any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_SOURCE_DOMAIN_HINTS):
        return {"status": "official", "message": "recognized official-like source domain"}
    if allow_unknown_source:
        return {
            "status": "unknown_warning",
            "message": "source URL domain is not in the official allowlist; operator review required",
        }
    return {
        "status": "unknown_error",
        "message": "source URL domain is not in the official allowlist",
    }


def _validate_source_for_candidate(
    row: dict[str, Any],
    *,
    values_present: bool,
    allow_unknown_source: bool,
) -> dict[str, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    company_id = row.get("company_id")
    source_type = str(row.get("source") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    source_file_name = str(row.get("source_file_name") or "").strip()
    source_text = " ".join(
        [
            source_type,
            source_url,
            source_file_name,
            str(row.get("source_document_title") or ""),
        ]
    ).casefold()

    if source_type and source_type not in ALLOWED_SOURCE_TYPES and source_type != "operator_collection":
        errors.append({"company_id": company_id, "message": "source type is not official-source evidence"})
    if _has_blocked_source_hint(source_text):
        errors.append({"company_id": company_id, "message": "blocked source detected; unofficial source is not allowed"})
    if values_present and not source_url and not source_file_name:
        errors.append({"company_id": company_id, "message": "financial values require source_url or source_file_name"})
    if source_url and not _has_blocked_source_hint(source_text):
        classification = classify_source_url(source_url, allow_unknown_source=allow_unknown_source)
        if classification["status"] in {"blocked", "unknown_error"}:
            errors.append({"company_id": company_id, "message": classification["message"]})
        elif classification["status"] == "unknown_warning":
            warnings.append({"company_id": company_id, "message": classification["message"]})
    if values_present and source_url and not row.get("source_document_title"):
        errors.append({"company_id": company_id, "message": "source_document_title is required for values"})
    if values_present and source_url and not row.get("source_document_date"):
        warnings.append({"company_id": company_id, "message": "source_document_date is recommended for values"})
    if not values_present and not source_url and not source_file_name:
        warnings.append({"company_id": company_id, "message": "source_url is empty; no values will be filled"})
    return {"warnings": warnings, "errors": errors}


def _issuer_domain_hints(issuer: dict[str, Any]) -> list[str]:
    ids = {
        str(issuer.get("canonical_company_id") or ""),
        str(issuer.get("company_id") or ""),
    }
    for issuer_id in ids:
        hints = DISCOVERY_SOURCE_CONFIG["issuer_domain_hints"].get(issuer_id)
        if hints:
            return list(hints)
    name_text = " ".join(
        str(issuer.get(field) or "")
        for field in ("company_name", "canonical_company_name")
    ).casefold()
    if "rzd" in name_text or "ржд" in name_text:
        return list(DISCOVERY_SOURCE_CONFIG["issuer_domain_hints"]["18"])
    if "mostotrest" in name_text or "мостотрест" in name_text:
        return list(DISCOVERY_SOURCE_CONFIG["issuer_domain_hints"]["67"])
    return []


def _issuer_site_domain(hints: list[str]) -> str | None:
    for domain in hints:
        if domain not in {"e-disclosure.ru", "disclosure.ru", "moex.com", "moex.ru"}:
            return domain
    return None


def _disclosure_domain(hints: list[str]) -> str | None:
    for domain in hints:
        if domain in {"e-disclosure.ru", "disclosure.ru"}:
            return domain
    return None


def _strip_financial_values(candidate: dict[str, Any]) -> None:
    for field in FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS:
        candidate.pop(field, None)
    candidate.pop("values", None)
    candidate.pop("field_evidence", None)


def _source_url_context(documents: list[dict[str, Any]]) -> str:
    urls: list[str] = []
    seen: set[str] = set()
    for document in documents:
        url = str(document.get("source_url") or document.get("url") or "").strip()
        if not url or url in seen:
            continue
        classification = classify_source_url(url, allow_unknown_source=True)
        if classification["status"] != "official":
            continue
        urls.append(url)
        seen.add(url)
    return " | ".join(urls)


def _issuer_from_document_intake_item(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": document.get("company_id"),
        "company_name": document.get("company_name"),
        "canonical_company_id": document.get("canonical_company_id"),
        "canonical_company_name": document.get("canonical_company_name"),
    }


def _document_company_key(document: dict[str, Any]) -> str:
    return str(document.get("canonical_company_id") or document.get("company_id") or "")


def _document_title_quality_warnings(
    document: dict[str, Any],
    base: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    title = str(document.get("document_title") or "").casefold()
    if not title:
        return []
    warnings: list[dict[str, Any]] = []
    if args.report_type == "annual" and not _contains_any(title, ("annual", "year", "год", "годов")):
        warnings.append({**base, "message": "document_title does not clearly mention annual reporting"})
    accounting_standard = str(document.get("accounting_standard") or args.accounting_standard or "").casefold()
    if accounting_standard != "unknown" and accounting_standard not in title:
        warnings.append({**base, "message": "document_title does not clearly mention accounting standard"})
    if args.prefer_audited and not _contains_any(title, ("audit", "audited", "аудит")):
        warnings.append({**base, "message": "document_title does not clearly mention audited status"})
    if args.prefer_consolidated and not _contains_any(title, ("consolidated", "консолид")):
        warnings.append({**base, "message": "document_title does not clearly mention consolidated scope"})
    return warnings


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _merge_document_into_source_candidates(
    source_candidates: list[dict[str, Any]],
    document: dict[str, Any],
) -> None:
    source_candidate = {
        "source_type": document.get("source_type"),
        "url": document.get("document_url"),
        "document_title": document.get("document_title"),
        "document_date": document.get("document_date"),
        "source_file_name": document.get("source_file_name"),
        "report_period": document.get("report_period"),
        "status": "valid_official_source"
        if document.get("document_status") == "valid_official_document"
        else "needs_operator_review",
        "confidence": document.get("confidence"),
        "discovery_method": document.get("resolution_method"),
        "notes": document.get("notes"),
    }
    _strip_financial_values(source_candidate)
    for index, existing in enumerate(source_candidates):
        if existing.get("source_type") == source_candidate["source_type"]:
            source_candidates[index] = {**existing, **source_candidate}
            return
    source_candidates.append(source_candidate)


def _recommended_document_actions(args: argparse.Namespace) -> list[str]:
    audited = " audited" if args.prefer_audited else ""
    consolidated = " consolidated" if args.prefer_consolidated else ""
    return [
        "Open official issuer/disclosure source.",
        (
            f"Find latest{audited}{consolidated} {args.accounting_standard} "
            f"{args.report_type} report for {args.report_period}."
        ),
        "Copy exact report page/PDF URL, title, publication date, and file name.",
        "Run document-validate before candidate-fill.",
    ]


def _file_name_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url))
    return Path(parsed.path).name


def _looks_like_landing_page(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url))
    path = (parsed.path or "/").rstrip("/")
    if path in {"", "/"}:
        return True
    lower_path = path.casefold()
    if lower_path.endswith((".pdf", ".html", ".htm")):
        return False
    return lower_path.count("/") <= 1 and "report" not in lower_path and "отчет" not in lower_path


def _download_valid_document(document: dict[str, Any], download_dir: Path) -> dict[str, Any]:
    url = str(document.get("document_url") or "")
    validation = validate_document_candidate(
        document,
        issuer={},
        allow_unknown_source=False,
        target_report_period=str(document.get("report_period") or ""),
    )
    if validation["errors"]:
        return {
            "url": url,
            "local_path": None,
            "sha256": None,
            "size_bytes": None,
            "content_type": None,
            "warnings": [],
            "errors": [
                {
                    "message": "document download refused because validation failed",
                    "details": validation["errors"],
                }
            ],
        }
    download_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "BondRadar-document-resolver-preview/1.0"}
    try:
        request = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "local_path": None,
            "sha256": None,
            "size_bytes": None,
            "content_type": None,
            "warnings": [{"message": f"document download failed: {exc}", "url": url}],
            "errors": [],
        }
    digest = hashlib.sha256(data).hexdigest()
    file_name = document.get("source_file_name") or _file_name_from_url(url) or f"{digest}.bin"
    output = download_dir / str(file_name)
    output.write_bytes(data)
    return {
        "url": url,
        "local_path": str(output),
        "sha256": digest,
        "size_bytes": len(data),
        "content_type": content_type,
        "warnings": [],
        "errors": [],
    }


def _source_from_manual_or_intake(
    manual_item: dict[str, Any] | None,
    issuer_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    manual_item = manual_item or {}
    if manual_item.get("source_url") or manual_item.get("source_file_name"):
        return {
            "source_type": manual_item.get("source_type") or "official_issuer_report",
            "source_url": manual_item.get("source_url"),
            "source_file_name": manual_item.get("source_file_name"),
            "source_document_title": manual_item.get("source_document_title"),
            "source_document_date": manual_item.get("source_document_date"),
        }
    for source in (issuer_source or {}).get("source_candidates") or []:
        if source.get("url"):
            return {
                "source_type": source.get("source_type"),
                "source_url": source.get("url"),
                "source_document_title": source.get("document_title"),
                "source_document_date": source.get("document_date"),
            }
    return None


def _normalize_evidence_value(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return {
            "value": None,
            "page": None,
            "table": None,
            "evidence_note": None,
            "confidence": None,
            "has_value": False,
            "has_evidence": False,
        }
    value = _normalize_cell(evidence.get("value"))
    page = _normalize_cell(evidence.get("page"))
    table = _normalize_cell(evidence.get("table"))
    note = _normalize_cell(evidence.get("evidence_note"))
    return {
        "value": value,
        "page": page,
        "table": table,
        "evidence_note": note,
        "confidence": _normalize_cell(evidence.get("confidence")),
        "has_value": value not in (None, ""),
        "has_evidence": any(item not in (None, "") for item in (page, table, note)),
    }


def _bad_data_message(field: str, evidence: dict[str, Any]) -> str | None:
    text = " ".join(
        str(evidence.get(key) or "")
        for key in ("table", "evidence_note")
    ).casefold()
    if field == "interest_expense":
        has_coupon = "coupon" in text or "купон" in text
        has_interest_evidence = any(
            hint in text
            for hint in (
                "interest expense",
                "finance costs",
                "finance cost",
                "процент",
                "финансов",
            )
        )
        if has_coupon and not has_interest_evidence:
            return "coupon payments are not accepted as interest_expense evidence"
    if field == "equity":
        if "market cap" in text or "capitalization" in text or "капитализац" in text:
            return "market capitalization is not accepted as equity evidence"
    return None


def _present_value_fields(values: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for field, evidence in values.items():
        normalized = _normalize_evidence_value(evidence)
        if normalized["has_value"]:
            fields.append(field)
    return fields


def _review_notes(filled_fields: list[str], missing_fields: list[str]) -> str:
    return (
        "filled_fields="
        + ", ".join(filled_fields or ["none"])
        + "; missing_fields="
        + ", ".join(missing_fields or ["none"])
    )


def _load_evidence_issuers(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"evidence template input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    issuers = payload.get("issuers") if isinstance(payload, dict) else payload
    if not isinstance(issuers, list):
        return {}
    return {_issuer_source_key(issuer): issuer for issuer in issuers if isinstance(issuer, dict)}


def _load_checklist_by_company(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        raise ValueError(f"source checklist input does not exist: {path}")
    grouped: dict[str, list[dict[str, Any]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return grouped
        for raw in reader:
            row = {key: _normalize_cell(value) for key, value in raw.items() if key}
            grouped.setdefault(_company_key(row), []).append(row)
    return grouped


def _download_source_document(url: str, download_dir: Path) -> dict[str, Any]:
    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "path": None,
            "sha256": None,
            "warnings": [{"message": f"download failed: {exc}", "url": url}],
            "errors": [],
        }
    digest = hashlib.sha256(data).hexdigest()
    parsed_name = Path(urllib.parse.urlparse(url).path).name or f"{digest}.bin"
    output = download_dir / parsed_name
    output.write_bytes(data)
    return {
        "url": url,
        "path": str(output),
        "sha256": digest,
        "warnings": [],
        "errors": [],
    }


def _probe_url(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> dict[str, Any]:
    headers = {"User-Agent": "BondRadar-source-discovery-preview/1.0"}
    for method in ("HEAD", "GET"):
        try:
            request = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if method == "GET":
                    response.read(max(1, max_bytes))
                return {
                    "status": "ok",
                    "http_status": getattr(response, "status", None),
                    "content_type": response.headers.get("Content-Type"),
                    "error": None,
                }
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            if method == "HEAD":
                continue
            return {
                "status": "failed",
                "http_status": getattr(exc, "code", None),
                "content_type": None,
                "error": last_error,
            }
    return {
        "status": "failed",
        "http_status": None,
        "content_type": None,
        "error": "probe failed",
    }


def _manual_key(item: dict[str, Any]) -> str:
    return str(item.get("canonical_company_id") or item.get("company_id") or "")


def _company_key(row: dict[str, Any]) -> str:
    return str(row.get("canonical_company_id") or row.get("company_id") or "")


def _issuer_source_key(item: dict[str, Any]) -> str:
    return str(item.get("canonical_company_id") or item.get("company_id") or "")


def _has_blocked_source_hint(text: str) -> bool:
    folded = text.casefold()
    return any(hint in folded for hint in BLOCKED_SOURCE_HINTS) or "wiki" in folded


def _host(url: str) -> str:
    parsed = urllib.parse.urlparse(url.casefold())
    host = parsed.netloc or parsed.path.split("/")[0]
    return host.removeprefix("www.")


def _next_steps(mode: str, status: str) -> list[str]:
    if status == "failed":
        return ["Fix official-source evidence errors before preview or any future import task."]
    if mode == "source-template":
        return ["Fill source intake with official issuer/disclosure/auditor URLs only."]
    if mode == "source-validate":
        return ["Use only validated official sources before adding financial values."]
    if mode == "document-resolve":
        return ["Review exact report document checklist before candidate-fill."]
    if mode == "document-validate":
        return ["Only valid official documents may be used in candidate-fill."]
    if mode == "document-intake-template":
        return ["Fill exact document intake with reviewed official annual/audited report metadata."]
    if mode == "document-intake-validate":
        return ["Resolve documents with reviewed exact intake before candidate-fill."]
    if mode == "candidate-fill":
        return ["Run preview mode; do not import until a separate controlled import task."]
    return ["Review preview output; import remains disabled in this workflow."]


def _message_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("message") or item)
    return str(item)


def _path_value(path: Path | None) -> str | None:
    return None if path is None else str(path)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_assistant(args)
    if args.json_output is not None:
        write_json_report(report, args.json_output)
        print(
            f"[financial-official-source-evidence-assistant] wrote JSON report: {args.json_output}",
            flush=True,
        )
    if args.markdown_output is not None:
        write_markdown_report(report, args.markdown_output)
        print(
            "[financial-official-source-evidence-assistant] wrote Markdown report: "
            f"{args.markdown_output}",
            flush=True,
        )
    print(
        f"[financial-official-source-evidence-assistant] {report['status']}",
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
