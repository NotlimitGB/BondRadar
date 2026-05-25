from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
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
    "document-intake-fill",
    "document-quality-gate",
    "document-candidate-discover",
    "operator-seed-template",
    "operator-seed-validate",
    "operator-seed-merge",
    "operator-seed-candidate-discover",
    "operator-seed-review-template",
    "operator-seed-promote-reviewed",
    "official-seed-resolve",
    "candidate-fill",
    "preview",
)
FORMAT_CHOICES = ("csv", "json")
DOCUMENT_REPORT_TYPES = ("annual", "quarterly")
DOCUMENT_ACCOUNTING_STANDARDS = ("IFRS", "RAS", "unknown")
DOCUMENT_INTAKE_FILL_SOURCES = ("local-candidates", "operator-candidates", "manual-candidates")
DOCUMENT_CANDIDATE_DISCOVERY_SOURCES = ("official-source-intake", "document-report", "manual-seeds")
OPERATOR_SEED_CANDIDATE_SOURCES = (
    "official-seed-pack",
    "operator-template-context",
    "official-disclosure-home",
    "issuer-official-site",
    "manual-candidates",
)
DOCUMENT_CONFIDENCE_LEVELS = {"low": 1, "medium": 2, "high": 3}
ALLOWED_SOURCE_TYPES = {
    "issuer_investor_relations",
    "official_issuer_report",
    "official_disclosure",
    "exchange_disclosure",
    "auditor_report",
    "issuer_annual_report_pdf",
}
OFFICIAL_SOURCE_DOMAIN_HINTS = tuple(
    dict.fromkeys([*collection_pack.OFFICIAL_SOURCE_DOMAIN_HINTS, "disclosure.ru"])
)
BLOCKED_SOURCE_HINTS = (
    "wikipedia",
    "wikimedia",
    "wikiwand",
    "google.",
    "yandex.",
    "bing.com",
    "duckduckgo",
    "yahoo.",
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
FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS = set(ALL_FINANCIAL_FIELDS) | {
    "debt",
    "financial_values",
}
FORBIDDEN_SEED_METADATA_FIELDS = FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS | {
    "exact_extracted_report_values",
    "field_evidence",
    "financial_metrics",
    "ocr_output",
    "parsed_table_values",
    "report_values",
}
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
DOCUMENT_CANDIDATE_FIELDS = [
    *DOCUMENT_INTAKE_TEMPLATE_FIELDS,
    "candidate_score",
    "candidate_confidence",
    "discovery_method",
    "source_page_url",
    "score_reasons",
    "negative_reasons",
]
SEED_DEFAULT_TYPES = (
    "issuer_home",
    "issuer_reports",
    "issuer_investor_relations",
    "official_disclosure_home",
    "official_disclosure_profile",
    "official_disclosure_reports",
)
OPERATOR_SEED_DEFAULT_REQUIRED_TYPES = (
    "official_disclosure_profile",
    "official_disclosure_reports",
    "issuer_reports",
)
OPERATOR_SEED_ALLOWED_TYPES = set(SEED_DEFAULT_TYPES) | {"manual_official_seed"}
OPERATOR_SEED_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "inn",
    "ogrn",
    "seed_type",
    "seed_url",
    "operator_review_status",
    "source_context",
    "notes",
]
OPERATOR_SEED_CANDIDATE_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "inn",
    "ogrn",
    "seed_type",
    "candidate_seed_url",
    "candidate_title",
    "candidate_source_url",
    "candidate_status",
    "operator_review_status",
    "candidate_rank",
    "candidate_score",
    "raw_score",
    "final_score",
    "candidate_confidence",
    "filter_status",
    "filter_reasons",
    "discovery_method",
    "score_reasons",
    "negative_reasons",
    "notes",
]
OPERATOR_SEED_REVIEW_DECISIONS = {"pending", "approve", "reject", "needs_more_review"}
OPERATOR_SEED_REVIEW_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "inn",
    "ogrn",
    "seed_type",
    "candidate_seed_url",
    "candidate_title",
    "candidate_source_url",
    "candidate_rank",
    "candidate_score",
    "candidate_confidence",
    "candidate_status",
    "operator_decision",
    "operator_review_status",
    "review_status",
    "review_notes",
    "suggested_action",
    "promotion_status",
    "score_reasons",
    "negative_reasons",
    "notes",
]
OPERATOR_SEED_RELEVANCE_TERMS = (
    "investor",
    "investors",
    "investment",
    "financial results",
    "financial statements",
    "financial reporting",
    "annual reports",
    "reports",
    "reporting",
    "disclosure",
    "information disclosure",
    "issuer profile",
    "company profile",
    "securities",
    "bondholders",
    "shareholders",
    "ir",
    "инвестор",
    "инвесторам",
    "инвесторы",
    "финансовые результаты",
    "финансовая отчетность",
    "финансовая отчётность",
    "отчетность",
    "отчётность",
    "годовой отчет",
    "годовой отчёт",
    "годовые отчеты",
    "годовые отчёты",
    "раскрытие информации",
    "раскрытие",
    "эмитент",
    "карточка эмитента",
    "акционерам",
    "облигации",
    "ценные бумаги",
)
OPERATOR_SEED_TYPE_RELEVANCE_TERMS = {
    "official_disclosure_profile": (
        "company.aspx",
        "emitent",
        "issuer",
        "profile",
        "card",
        "карточка",
        "эмитент",
        "раскрытие информации",
        "e-disclosure",
    ),
    "official_disclosure_reports": (
        "messages",
        "message",
        "disclosure",
        "reports",
        "reporting",
        "events",
        "сообщения",
        "отчетность",
        "отчётность",
        "раскрытие",
        "существенные факты",
    ),
    "issuer_reports": (
        "invest",
        "investor",
        "reports",
        "financial-results",
        "financial_results",
        "financial results",
        "annual",
        "reporting",
        "отчетность",
        "отчётность",
        "годовой",
        "финансовые результаты",
        "инвесторам",
    ),
}
OPERATOR_SEED_NOISE_TERMS = (
    "ticket",
    "tickets",
    "buy ticket",
    "train",
    "trains",
    "route",
    "routes",
    "station",
    "stations",
    "online board",
    "schedule",
    "timetable",
    "passenger",
    "cargo",
    "freight",
    "vacancy",
    "career",
    "press",
    "news",
    "contacts",
    "history",
    "about",
    "activity",
    "objects",
    "projects",
    "gallery",
    "photo",
    "video",
    "map",
    "login",
    "search",
    "купить билет",
    "билет",
    "билеты",
    "поезд",
    "поезда",
    "маршрут",
    "маршруты",
    "вокзал",
    "вокзалы",
    "онлайн-табло",
    "движение поездов",
    "пассажирам",
    "грузовые перевозки",
    "груз",
    "вакансии",
    "карьера",
    "пресс",
    "новости",
    "контакты",
    "история",
    "о компании",
    "деятельность",
    "объекты",
    "проекты",
    "галерея",
    "фото",
    "видео",
    "карта",
    "поиск",
)
SEED_TYPE_ALIASES = {
    "issuer_home": "issuer_home",
    "issuer_reports": "issuer_reports",
    "issuer_investor_relations": "issuer_investor_relations",
    "investor_relations": "issuer_investor_relations",
    "official_disclosure": "official_disclosure_home",
    "official_disclosure_home": "official_disclosure_home",
    "disclosure_profile": "official_disclosure_profile",
    "official_disclosure_profile": "official_disclosure_profile",
    "disclosure_reports": "official_disclosure_reports",
    "official_disclosure_reports": "official_disclosure_reports",
    "manual_official_seed": "manual_official_seed",
}
SEED_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "inn",
    "ogrn",
    "seed_type",
    "seed_url",
    "seed_status",
    "confidence",
    "source",
    "reason",
    "probe_status",
    "http_status",
    "content_type",
    "warnings",
    "errors",
]
PROBABLE_ISSUER_SEED_PATHS = ("/investors/", "/reports/", "/investors/reports/")
VALID_CANDIDATE_SEED_STATUSES = {"valid_seed", "needs_operator_review"}
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
    parser.add_argument(
        "--document-intake-fill-source",
        choices=DOCUMENT_INTAKE_FILL_SOURCES,
        default="local-candidates",
    )
    parser.add_argument("--exact-document-candidates-input", type=Path, default=None)
    parser.add_argument("--allow-reviewed-candidates", type=_parse_bool, default=True)
    parser.add_argument("--require-exact-document", type=_parse_bool, default=True)
    parser.add_argument(
        "--min-document-confidence",
        choices=tuple(DOCUMENT_CONFIDENCE_LEVELS.keys()),
        default="medium",
    )
    parser.add_argument("--prefer-official-issuer", type=_parse_bool, default=True)
    parser.add_argument("--prefer-disclosure", type=_parse_bool, default=True)
    parser.add_argument("--required-company-ids", default="")
    parser.add_argument("--required-company-names", default="")
    parser.add_argument("--require-all-required-issuers", type=_parse_bool, default=True)
    parser.add_argument("--require-one-valid-document-per-issuer", type=_parse_bool, default=True)
    parser.add_argument("--require-document-resolve", type=_parse_bool, default=True)
    parser.add_argument("--fail-on-unresolved-documents", type=_parse_bool, default=True)
    parser.add_argument("--fail-on-needs-operator-review", type=_parse_bool, default=True)
    parser.add_argument("--allow-partial-gate", type=_parse_bool, default=False)
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
    parser.add_argument("--candidate-csv-output", type=Path, default=None)
    parser.add_argument("--seed-input", type=Path, default=None)
    parser.add_argument("--seed-output", type=Path, default=None)
    parser.add_argument("--seed-csv-output", type=Path, default=None)
    parser.add_argument("--operator-seed-input", type=Path, default=None)
    parser.add_argument("--operator-seed-output", type=Path, default=None)
    parser.add_argument("--operator-seed-csv-output", type=Path, default=None)
    parser.add_argument("--operator-seed-template-status", default="operator_to_fill")
    parser.add_argument(
        "--operator-seed-required-types",
        default="official_disclosure_profile,official_disclosure_reports,issuer_reports",
    )
    parser.add_argument("--operator-seed-prefer-disclosure", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-prefer-issuer-site", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-require-reviewed", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-validate-only", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-candidate-output", type=Path, default=None)
    parser.add_argument("--operator-seed-candidate-csv-output", type=Path, default=None)
    parser.add_argument(
        "--operator-seed-candidate-source",
        choices=OPERATOR_SEED_CANDIDATE_SOURCES,
        default="official-seed-pack",
    )
    parser.add_argument(
        "--operator-seed-candidate-types",
        default="official_disclosure_profile,official_disclosure_reports,issuer_reports",
    )
    parser.add_argument("--operator-seed-candidate-min-score", type=int, default=60)
    parser.add_argument("--operator-seed-candidate-auto-review-threshold", type=int, default=90)
    parser.add_argument("--operator-seed-candidate-top-n-per-issuer", type=int, default=20)
    parser.add_argument("--operator-seed-candidate-top-n-per-type", type=int, default=5)
    parser.add_argument("--operator-seed-candidate-include-filtered", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-candidate-noise-filter", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-candidate-min-title-signal", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-candidate-min-path-signal", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-candidate-deduplicate-paths", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-candidate-max-autofill-review-needed", type=int, default=3)
    parser.add_argument("--operator-seed-candidate-allowed-domains", default="")
    parser.add_argument("--operator-seed-candidate-blocked-domains", default="")
    parser.add_argument("--operator-seed-candidate-probe-urls", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-candidate-fetch-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--operator-seed-candidate-max-response-bytes", type=int, default=500000)
    parser.add_argument(
        "--operator-seed-candidate-user-agent",
        default="BondRadar-operator-seed-candidate-helper/1.0",
    )
    parser.add_argument("--operator-seed-autofill-output", type=Path, default=None)
    parser.add_argument("--operator-seed-autofill-csv-output", type=Path, default=None)
    parser.add_argument("--operator-seed-candidate-input", type=Path, default=None)
    parser.add_argument("--operator-seed-review-input", type=Path, default=None)
    parser.add_argument("--operator-seed-review-output", type=Path, default=None)
    parser.add_argument("--operator-seed-review-csv-output", type=Path, default=None)
    parser.add_argument("--operator-seed-review-status", default="pending_review")
    parser.add_argument("--operator-seed-review-include-missing", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-review-include-not-found", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-review-top-n-per-issuer", type=int, default=20)
    parser.add_argument("--operator-seed-review-top-n-per-type", type=int, default=5)
    parser.add_argument("--operator-seed-review-default-decision", default="pending")
    parser.add_argument("--operator-seed-review-auto-approve-reviewed", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-review-auto-approve-threshold", type=int, default=999)
    parser.add_argument("--operator-seed-promotion-require-approve", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-promotion-allow-needs-review", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-promotion-include-rejected", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-promotion-dedupe", type=_parse_bool, default=True)
    parser.add_argument("--operator-seed-promotion-strict", type=_parse_bool, default=True)
    parser.add_argument("--run-operator-seed-validate", type=_parse_bool, default=False)
    parser.add_argument("--operator-seed-validation-json-output", type=Path, default=None)
    parser.add_argument("--operator-seed-validation-markdown-output", type=Path, default=None)
    parser.add_argument("--seed-allowed-domains", default="")
    parser.add_argument("--seed-blocked-domains", default="")
    parser.add_argument(
        "--seed-types",
        default="issuer_home,issuer_reports,investor_relations,official_disclosure,disclosure_profile,disclosure_reports",
    )
    parser.add_argument("--seed-probe-urls", type=_parse_bool, default=False)
    parser.add_argument("--seed-fetch-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--seed-max-response-bytes", type=int, default=500000)
    parser.add_argument(
        "--seed-user-agent",
        default="BondRadar-official-seed-resolver/1.0",
    )
    parser.add_argument("--run-candidate-discovery", type=_parse_bool, default=False)
    parser.add_argument(
        "--candidate-discovery-source",
        choices=DOCUMENT_CANDIDATE_DISCOVERY_SOURCES,
        default="official-source-intake",
    )
    parser.add_argument("--max-pages-per-issuer", type=int, default=10)
    parser.add_argument("--max-links-per-page", type=int, default=200)
    parser.add_argument("--max-candidate-links", type=int, default=20)
    parser.add_argument("--candidate-min-score", type=int, default=60)
    parser.add_argument("--candidate-auto-review-threshold", type=int, default=90)
    parser.add_argument("--candidate-require-pdf-or-report-page", type=_parse_bool, default=True)
    parser.add_argument("--candidate-allow-landing-pages", type=_parse_bool, default=False)
    parser.add_argument("--candidate-allowed-domains", default="")
    parser.add_argument("--candidate-blocked-domains", default="")
    parser.add_argument("--candidate-fetch-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--candidate-max-response-bytes", type=int, default=500000)
    parser.add_argument(
        "--candidate-user-agent",
        default="BondRadar-document-candidate-discovery/1.0",
    )
    parser.add_argument("--run-quality-gate", type=_parse_bool, default=False)
    parser.add_argument("--quality-gate-json-output", type=Path, default=None)
    parser.add_argument("--quality-gate-markdown-output", type=Path, default=None)
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
    elif args.mode == "document-intake-fill":
        report = run_document_intake_fill(args)
    elif args.mode == "document-quality-gate":
        report = run_document_quality_gate(args)
    elif args.mode == "document-candidate-discover":
        report = run_document_candidate_discover(args)
    elif args.mode == "operator-seed-template":
        report = run_operator_seed_template(args)
    elif args.mode == "operator-seed-validate":
        report = run_operator_seed_validate(args)
    elif args.mode == "operator-seed-merge":
        report = run_operator_seed_merge(args)
    elif args.mode == "operator-seed-candidate-discover":
        report = run_operator_seed_candidate_discover(args)
    elif args.mode == "operator-seed-review-template":
        report = run_operator_seed_review_template(args)
    elif args.mode == "operator-seed-promote-reviewed":
        report = run_operator_seed_promote_reviewed(args)
    elif args.mode == "official-seed-resolve":
        report = run_official_seed_resolve(args)
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


def run_document_intake_fill(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    candidate_documents: list[dict[str, Any]] = []
    source_context_by_key: dict[str, str] = {}
    document_context_by_key: dict[str, str] = {}

    if args.document_intake_input is None:
        errors.append({"message": "document-intake-fill mode requires --document-intake-input"})
    if not errors:
        try:
            documents = load_document_intake_file(args.document_intake_input)
            source_context_by_key = load_source_context_by_key(args.source_intake_input)
            document_context_by_key = load_document_context_by_key(args.document_output)
            candidate_documents = load_exact_document_candidate_items(args.exact_document_candidates_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if not errors and args.exact_document_candidates_input is None:
        warnings.append({"message": "exact document candidate file not provided"})

    candidates_by_key = _group_document_candidates(candidate_documents)
    filled_documents: list[dict[str, Any]] = []
    validation_results: list[dict[str, Any]] = []
    if not errors:
        for document in documents:
            filled, result = fill_document_intake_item(
                document,
                args,
                candidates=candidates_by_key.get(_document_period_key(document), []),
                source_context=(
                    document.get("source_url_context")
                    or source_context_by_key.get(_document_company_key(document))
                    or document_context_by_key.get(_document_company_key(document))
                    or ""
                ),
            )
            filled_documents.append(filled)
            validation_results.append(result)
            warnings.extend(result.get("warnings") or [])
            errors.extend(result.get("errors") or [])

    valid_count = sum(
        1
        for result in validation_results
        if not result.get("errors") and result.get("document_status") == "valid_official_document"
    )
    invalid_count = sum(1 for result in validation_results if result.get("errors"))
    filled_count = sum(
        1
        for document in filled_documents
        if document.get("operator_review_status") == "operator_reviewed"
        and document.get("document_url")
    )
    review_count = sum(
        1
        for document in filled_documents
        if document.get("operator_review_status") != "operator_reviewed"
        or not document.get("document_url")
    )
    if args.document_intake_output is not None and not errors:
        write_json_report(
            {
                "status": "filled",
                "mode": "document-intake-fill",
                "issuer_count": len({_document_company_key(item) for item in filled_documents}),
                "document_template_count": len(filled_documents),
                "documents": filled_documents,
                **SAFETY_FLAGS,
            },
            args.document_intake_output,
        )
    if args.document_intake_csv_output is not None and not errors:
        write_document_intake_csv(filled_documents, args.document_intake_csv_output)

    status = "failed" if errors else "passed" if valid_count and valid_count == len(filled_documents) else "warning"
    report = {
        "status": status,
        "mode": "document-intake-fill",
        "issuer_count": len({_document_company_key(item) for item in filled_documents}),
        "document_template_count": len(filled_documents),
        "filled_document_count": filled_count,
        "valid_document_count": valid_count,
        "needs_operator_review_count": review_count,
        "invalid_document_count": invalid_count,
        "documents": filled_documents,
        "document_results": validation_results,
        "warnings": warnings,
        "errors": errors,
        "document_intake_output": _path_value(args.document_intake_output),
        "document_intake_csv_output": _path_value(args.document_intake_csv_output),
        "next_steps": _next_steps("document-intake-fill", status),
        **SAFETY_FLAGS,
    }
    return report


def run_document_quality_gate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_documents: list[dict[str, Any]] = []
    filled_documents: list[dict[str, Any]] = []
    resolved_source_intake: list[dict[str, Any]] = []

    if args.document_intake_input is None:
        errors.append({"message": "document-quality-gate mode requires --document-intake-input"})
    if args.source_intake_input is None and args.require_document_resolve:
        errors.append({"message": "document-quality-gate mode requires --source-intake-input"})
    if not errors:
        try:
            input_documents = load_document_intake_file(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _parse_required_issuers(args, input_documents)
    if not required_issuers and args.require_all_required_issuers:
        errors.append({"message": "at least one required issuer is needed for quality gate"})
    if args.exact_document_candidates_input is None:
        errors.append({"message": "exact document candidate file is required for quality gate"})

    fill_report: dict[str, Any] = {}
    validation_report: dict[str, Any] = {}
    resolve_report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="bondradar-document-gate-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        filled_output = args.document_intake_output or tmp_path / "exact_document_intake_gate.json"
        source_output = args.source_intake_output or tmp_path / "official_source_intake_resolved_gate.json"
        document_output = args.document_output or tmp_path / "official_report_documents_gate.json"

        fill_args = _clone_args(
            args,
            document_intake_output=filled_output,
            document_output=args.document_input,
        )
        fill_report = run_document_intake_fill(fill_args)
        warnings.extend(fill_report.get("warnings") or [])
        errors.extend(fill_report.get("errors") or [])

        if filled_output.is_file():
            try:
                filled_documents = load_document_intake_file(filled_output)
            except Exception as exc:
                errors.append({"message": f"failed to read filled exact document intake: {exc}"})
        else:
            errors.append({"message": "document-intake-fill did not produce filled exact document intake"})

        if filled_output.is_file():
            validation_args = _clone_args(args, document_intake_input=filled_output)
            validation_report = run_document_intake_validate(validation_args)
            warnings.extend(validation_report.get("warnings") or [])
        else:
            validation_report = {
                "status": "failed",
                "mode": "document-intake-validate",
                "issuer_count": 0,
                "document_candidate_count": 0,
                "valid_document_count": 0,
                "invalid_document_count": 0,
                "needs_operator_review_count": 0,
                "document_results": [],
                "warnings": [],
                "errors": [{"message": "filled exact document intake is unavailable for validation"}],
                **SAFETY_FLAGS,
            }
        if args.require_document_resolve and filled_output.is_file():
            resolve_args = _clone_args(
                args,
                document_intake_input=filled_output,
                source_intake_output=source_output,
                document_output=document_output,
            )
            resolve_report = run_document_resolve(resolve_args)
            warnings.extend(resolve_report.get("warnings") or [])
            if source_output.is_file():
                try:
                    resolved_source_intake = load_source_intake(source_output)
                except Exception as exc:
                    errors.append({"message": f"failed to read resolved source intake: {exc}"})
        elif args.require_document_resolve:
            resolve_report = {
                "status": "failed",
                "mode": "document-resolve",
                "issuer_count": 0,
                "resolved_document_count": 0,
                "needs_operator_review_count": 0,
                "invalid_document_count": 0,
                "issuers": [],
                "warnings": [],
                "errors": [{"message": "filled exact document intake is unavailable for document-resolve"}],
                **SAFETY_FLAGS,
            }

    validation_errors = validation_report.get("errors") or []
    resolve_errors = resolve_report.get("errors") or []
    severe_pipeline_errors = [
        error
        for error in [*(fill_report.get("errors") or []), *validation_errors, *resolve_errors]
        if not _is_unresolved_document_gate_error(error)
    ]
    errors.extend(severe_pipeline_errors)

    required_statuses = _build_required_issuer_gate_statuses(
        required_issuers,
        input_documents=input_documents,
        filled_documents=filled_documents,
        validation_report=validation_report,
        resolve_report=resolve_report,
        resolved_source_intake=resolved_source_intake,
        args=args,
    )
    covered_required_count = sum(1 for item in required_statuses if item["gate_status"] == "passed")
    filled_required_count = sum(1 for item in required_statuses if item.get("filled"))
    valid_required_count = sum(
        1 for item in required_statuses if item.get("valid_document_count", 0) == 1
    )
    resolved_required_count = sum(
        1 for item in required_statuses if item.get("resolved_document_count", 0) >= 1
    )
    review_required_count = sum(
        1
        for item in required_statuses
        if item.get("document_status") in {"missing", "needs_operator_review"}
    )
    invalid_required_count = sum(
        1 for item in required_statuses if item.get("document_status") == "invalid_document"
    )

    required_blockers = [
        _required_issuer_error(item)
        for item in required_statuses
        if item["gate_status"] != "passed"
    ]
    if args.allow_partial_gate and covered_required_count > 0:
        warnings.extend(required_blockers)
    else:
        errors.extend(required_blockers)

    gate_passed = (
        not errors
        and bool(required_statuses)
        and covered_required_count == len(required_statuses)
        and valid_required_count >= len(required_statuses)
        and resolved_required_count >= len(required_statuses)
        and review_required_count == 0
        and invalid_required_count == 0
        and _safety_flags_clean(fill_report)
        and _safety_flags_clean(validation_report)
        and _safety_flags_clean(resolve_report)
    )
    partial_warning = (
        args.allow_partial_gate
        and not gate_passed
        and covered_required_count > 0
        and covered_required_count < len(required_statuses)
        and not severe_pipeline_errors
    )
    status = "passed" if gate_passed else "warning" if partial_warning else "failed"
    report = {
        "status": status,
        "mode": "document-quality-gate",
        "issuer_count": len({_document_company_key(item) for item in filled_documents}),
        "required_issuer_count": len(required_statuses),
        "covered_required_issuer_count": covered_required_count,
        "filled_document_count": filled_required_count,
        "valid_document_count": valid_required_count,
        "resolved_document_count": resolved_required_count,
        "needs_operator_review_count": review_required_count,
        "invalid_document_count": invalid_required_count,
        "gate_passed": gate_passed,
        "ready_for_value_extraction": gate_passed,
        "ready_for_import": False,
        "required_issuers": required_statuses,
        "fill_report": fill_report,
        "validation_report": validation_report,
        "resolve_report": resolve_report,
        "warnings": warnings,
        "errors": [] if partial_warning and not severe_pipeline_errors else errors,
        "document_intake_output": _path_value(args.document_intake_output),
        "document_intake_csv_output": _path_value(args.document_intake_csv_output),
        "source_intake_output": _path_value(args.source_intake_output),
        "document_output": _path_value(args.document_output),
        "document_checklist_output": _path_value(args.document_checklist_output),
        "next_steps": _next_steps("document-quality-gate", status),
        **SAFETY_FLAGS,
    }
    return report


def run_operator_seed_template(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_documents: list[dict[str, Any]] = []
    seed_issuers: list[dict[str, Any]] = []
    financial_rows: list[dict[str, Any]] = []

    try:
        if args.document_intake_input is not None:
            input_documents = load_document_intake_file(args.document_intake_input)
        else:
            warnings.append({"message": "document intake input is not provided; issuer context is reduced"})
        if args.seed_input is not None:
            seed_issuers = load_seed_pack_issuers(args.seed_input)
        else:
            warnings.append({"message": "seed input is not provided; source context is reduced"})
        if args.financial_template_input is not None:
            financial_rows = load_template_rows(args.financial_template_input)
        else:
            warnings.append({"message": "financial template input is not provided; INN/OGRN enrichment skipped"})
    except Exception as exc:
        errors.append({"message": str(exc)})

    required_issuers = _operator_seed_required_issuers(
        args,
        input_documents=input_documents,
        seed_issuers=seed_issuers,
        financial_rows=financial_rows,
    )
    seed_types = _operator_seed_required_types(args.operator_seed_required_types, args=args)
    if not required_issuers and not errors:
        errors.append({"message": "operator-seed-template mode requires required issuers or input issuer context"})
    if not seed_types and not errors:
        errors.append({"message": "operator seed required types are empty"})

    seeds: list[dict[str, Any]] = []
    if not errors:
        for required in required_issuers:
            issuer = _operator_seed_issuer_base(
                required,
                input_documents=input_documents,
                seed_issuers=seed_issuers,
                financial_rows=financial_rows,
            )
            source_context = _operator_seed_source_context(
                issuer,
                seed_issuers=seed_issuers,
                input_documents=input_documents,
            )
            if not issuer.get("inn") or not issuer.get("ogrn"):
                warnings.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name"),
                        "message": "INN/OGRN missing for operator seed template row",
                    }
                )
            for seed_type in seed_types:
                item = {
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name") or "",
                    "canonical_company_id": issuer.get("canonical_company_id"),
                    "canonical_company_name": issuer.get("canonical_company_name") or "",
                    "inn": issuer.get("inn") or "",
                    "ogrn": issuer.get("ogrn") or "",
                    "seed_type": seed_type,
                    "seed_url": "",
                    "operator_review_status": args.operator_seed_template_status,
                    "source_context": source_context,
                    "notes": _operator_seed_template_notes(seed_type),
                }
                _strip_financial_values(item)
                seeds.append(item)

    status = "failed" if errors else "template"
    report = {
        "status": status,
        "mode": "operator-seed-template",
        "issuer_count": len(required_issuers) if not errors else 0,
        "seed_template_count": len(seeds),
        "seeds": seeds,
        "warnings": warnings,
        "errors": errors,
        "operator_seed_output": _path_value(args.operator_seed_output),
        "operator_seed_csv_output": _path_value(args.operator_seed_csv_output),
        "next_steps": _next_steps("operator-seed-template", status),
        **SAFETY_FLAGS,
    }
    if args.operator_seed_output is not None and not errors:
        write_json_report(report, args.operator_seed_output)
    if args.operator_seed_csv_output is not None and not errors:
        write_operator_seed_csv(seeds, args.operator_seed_csv_output)
    return report


def run_operator_seed_validate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    if args.operator_seed_input is None:
        errors.append({"message": "operator-seed-validate mode requires --operator-seed-input"})
    if not errors:
        try:
            seeds = load_operator_seed_items(args.operator_seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    report = _build_operator_seed_validation_report(args, seeds=seeds, load_errors=errors)
    report["warnings"] = [*warnings, *(report.get("warnings") or [])]
    return report


def run_operator_seed_merge(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seed_issuers: list[dict[str, Any]] = []
    operator_seeds: list[dict[str, Any]] = []

    if args.seed_input is None:
        errors.append({"message": "operator-seed-merge mode requires --seed-input"})
    if args.operator_seed_input is None:
        errors.append({"message": "operator-seed-merge mode requires --operator-seed-input"})
    if not errors:
        try:
            seed_issuers = load_seed_pack_issuers(args.seed_input)
            operator_seeds = load_operator_seed_items(args.operator_seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    validation_report = _build_operator_seed_validation_report(args, seeds=operator_seeds, load_errors=errors)
    validation_results = validation_report.get("seeds") or []
    merged_issuers = copy.deepcopy(seed_issuers)
    existing_seed_count = sum(len(issuer.get("official_seeds") or []) for issuer in seed_issuers)

    if not args.operator_seed_validate_only:
        for result in validation_results:
            if not _operator_seed_merge_eligible(result, args=args):
                continue
            issuer = _find_or_create_seed_issuer(merged_issuers, result)
            issuer.setdefault("official_seeds", []).append(_operator_seed_result_to_official_seed(result))
        for issuer in merged_issuers:
            issuer["official_seeds"] = _dedupe_validated_seeds(issuer.get("official_seeds") or [])

    merged_seed_count = sum(len(issuer.get("official_seeds") or []) for issuer in merged_issuers)
    valid_merged_count = sum(
        1
        for issuer in merged_issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("source") == "operator_seed" and seed.get("seed_status") == "valid_seed"
    )
    review_needed_merged_count = sum(
        1
        for issuer in merged_issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("source") == "operator_seed" and seed.get("seed_status") == "needs_operator_review"
    )
    invalid_rejected_count = sum(
        1 for item in validation_results if item.get("seed_status") in {"invalid_seed", "blocked_seed"}
    )
    warnings.extend(validation_report.get("warnings") or [])
    errors.extend(validation_report.get("errors") or [])
    status = "failed" if errors else "warning" if warnings or review_needed_merged_count else "passed"
    report = {
        "status": status,
        "mode": "operator-seed-merge",
        "issuer_count": len(merged_issuers),
        "existing_seed_count": existing_seed_count,
        "operator_seed_count": len(operator_seeds),
        "merged_seed_count": merged_seed_count,
        "valid_merged_count": valid_merged_count,
        "review_needed_count": review_needed_merged_count,
        "invalid_rejected_count": invalid_rejected_count,
        "validate_only": bool(args.operator_seed_validate_only),
        "issuers": merged_issuers,
        "validation_report": validation_report,
        "warnings": warnings,
        "errors": errors,
        "seed_output": _path_value(args.seed_output),
        "seed_csv_output": _path_value(args.seed_csv_output),
        "next_steps": _next_steps("operator-seed-merge", status),
        **SAFETY_FLAGS,
    }
    if args.seed_output is not None and not args.operator_seed_validate_only:
        write_json_report(report, args.seed_output)
    if args.seed_csv_output is not None and not args.operator_seed_validate_only:
        write_seed_csv(merged_issuers, args.seed_csv_output)
    return report


def run_operator_seed_candidate_discover(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    operator_seed_rows: list[dict[str, Any]] = []
    seed_issuers: list[dict[str, Any]] = []
    financial_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    ranking_stats = {
        "candidate_count_before_filter": 0,
        "candidate_count_after_filter": 0,
        "filtered_candidate_count": 0,
        "filtered_noise_count": 0,
        "filtered_low_score_count": 0,
        "filtered_duplicate_count": 0,
        "top_ranked_candidate_count": 0,
        "invalid_candidate_count": 0,
        "blocked_candidate_count": 0,
    }

    if args.operator_seed_input is None:
        errors.append({"message": "operator-seed-candidate-discover mode requires --operator-seed-input"})
    if args.seed_input is None and args.operator_seed_candidate_source in {
        "official-seed-pack",
        "official-disclosure-home",
        "issuer-official-site",
    }:
        errors.append({"message": "operator-seed-candidate-discover mode requires --seed-input for selected source"})
    if not errors:
        try:
            operator_seed_rows = load_operator_seed_items(args.operator_seed_input)
            if args.seed_input is not None:
                seed_issuers = load_seed_pack_issuers(args.seed_input)
            if args.financial_template_input is not None:
                financial_rows = load_template_rows(args.financial_template_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _operator_seed_required_issuers(
        args,
        input_documents=operator_seed_rows,
        seed_issuers=seed_issuers,
        financial_rows=financial_rows,
    )
    candidate_types = _operator_seed_required_types(args.operator_seed_candidate_types, args=args)
    allowed_domains = _operator_seed_candidate_allowed_domains(args)
    blocked_hints = _operator_seed_candidate_blocked_hints(args)

    if not errors:
        manual = args.operator_seed_candidate_source == "manual-candidates"
        if manual:
            for row in operator_seed_rows:
                if not row.get("seed_url"):
                    continue
                candidate = build_operator_seed_candidate_from_url(
                    row,
                    str(row.get("seed_url") or ""),
                    str(row.get("notes") or row.get("seed_url") or ""),
                    str(row.get("source_context") or ""),
                    args=args,
                    allowed_domains=allowed_domains,
                    blocked_hints=blocked_hints,
                    discovery_method="manual_candidate",
                    page_text="",
                )
                candidates.append(candidate)
        else:
            for required in required_issuers:
                issuer = _operator_seed_issuer_base(
                    required,
                    input_documents=operator_seed_rows,
                    seed_issuers=seed_issuers,
                    financial_rows=financial_rows,
                )
                template_rows = _items_matching_required(operator_seed_rows, issuer)
                source_urls = build_operator_seed_candidate_source_urls(
                    issuer,
                    operator_seed_rows=template_rows,
                    seed_issuers=seed_issuers,
                    args=args,
                    allowed_domains=allowed_domains,
                    blocked_hints=blocked_hints,
                    warnings=warnings,
                )
                issuer_candidates: list[dict[str, Any]] = []
                for source_url in source_urls[: max(args.max_pages_per_issuer, 0)]:
                    fetch = _fetch_candidate_page(
                        source_url,
                        timeout_seconds=args.operator_seed_candidate_fetch_timeout_seconds,
                        max_bytes=args.operator_seed_candidate_max_response_bytes,
                        user_agent=args.operator_seed_candidate_user_agent,
                    )
                    if fetch.get("status") != "ok":
                        warnings.append(
                            {
                                "company_id": issuer.get("company_id"),
                                "candidate_source_url": source_url,
                                "message": "failed to fetch official seed candidate source page",
                                "error": fetch.get("error"),
                            }
                        )
                        continue
                    content_type = str(fetch.get("content_type") or "").casefold()
                    if "html" not in content_type:
                        warnings.append(
                            {
                                "company_id": issuer.get("company_id"),
                                "candidate_source_url": source_url,
                                "content_type": fetch.get("content_type"),
                                "message": "seed candidate source response is not HTML; skipped anchor extraction",
                            }
                        )
                        continue
                    body = str(fetch.get("body") or "")
                    anchors = _extract_html_anchors(body, source_url)
                    for anchor in anchors[: max(args.max_links_per_page, 0)]:
                        for seed_type in candidate_types:
                            if template_rows and not any(
                                _normalize_seed_type(row.get("seed_type")) == seed_type for row in template_rows
                            ):
                                continue
                            candidate = build_operator_seed_candidate_from_url(
                                {
                                    **issuer,
                                    "seed_type": seed_type,
                                },
                                str(anchor.get("href") or ""),
                                _candidate_title(anchor, str(anchor.get("href") or "")),
                                source_url,
                                args=args,
                                allowed_domains=allowed_domains,
                                blocked_hints=blocked_hints,
                                discovery_method="official_seed_anchor_scan",
                                page_text=body,
                            )
                            if candidate.get("candidate_seed_url"):
                                issuer_candidates.append(candidate)
                candidates.extend(issuer_candidates)

        candidates, ranking_stats = _select_top_operator_seed_candidates(candidates, args=args)
        if not manual:
            kept_candidates = [
                item
                for item in candidates
                if item.get("candidate_seed_url") and item.get("filter_status") == "kept"
            ]
            for required in required_issuers:
                issuer = _operator_seed_issuer_base(
                    required,
                    input_documents=operator_seed_rows,
                    seed_issuers=seed_issuers,
                    financial_rows=financial_rows,
                )
                template_rows = _items_matching_required(operator_seed_rows, issuer)
                for template in template_rows:
                    seed_type = _normalize_seed_type(template.get("seed_type"))
                    if seed_type not in candidate_types:
                        continue
                    if not any(
                        _matches_required_issuer(candidate, issuer)
                        and candidate.get("seed_type") == seed_type
                        for candidate in kept_candidates
                    ):
                        candidates.append(_operator_seed_not_found_candidate(template, issuer=issuer))

    candidate_rows_with_url = [
        item
        for item in candidates
        if item.get("candidate_seed_url") and item.get("filter_status") == "kept"
    ]
    reviewed_count = sum(1 for item in candidate_rows_with_url if item.get("operator_review_status") == "operator_reviewed")
    review_count = sum(1 for item in candidate_rows_with_url if item.get("operator_review_status") == "needs_operator_review")
    invalid_count = ranking_stats["invalid_candidate_count"]
    blocked_count = ranking_stats["blocked_candidate_count"]

    autofill_seeds = build_operator_seed_autofill(operator_seed_rows, candidates, args=args)
    autofill_reviewed_count = sum(1 for item in autofill_seeds if item.get("operator_review_status") == "operator_reviewed")
    autofill_review_needed_count = sum(1 for item in autofill_seeds if item.get("operator_review_status") == "needs_operator_review")
    autofill_candidate_count = autofill_reviewed_count + autofill_review_needed_count
    operator_seed_validation_report: dict[str, Any] | None = None

    if args.operator_seed_candidate_output is not None and not errors:
        write_json_report(
            {
                "status": "discovered",
                "mode": "operator-seed-candidate-discover",
                "issuer_count": len(required_issuers),
                "candidates": candidates,
                **SAFETY_FLAGS,
            },
            args.operator_seed_candidate_output,
        )
    if args.operator_seed_candidate_csv_output is not None and not errors:
        write_operator_seed_candidate_csv(candidates, args.operator_seed_candidate_csv_output)
    if args.operator_seed_autofill_output is not None and not errors:
        write_json_report(
            {
                "status": "autofill",
                "mode": "operator-seed-candidate-discover",
                "issuer_count": len(required_issuers),
                "seeds": autofill_seeds,
                **SAFETY_FLAGS,
            },
            args.operator_seed_autofill_output,
        )
    if args.operator_seed_autofill_csv_output is not None and not errors:
        write_operator_seed_csv(autofill_seeds, args.operator_seed_autofill_csv_output)

    if args.run_operator_seed_validate and not errors:
        if args.operator_seed_autofill_output is not None:
            validation_input = args.operator_seed_autofill_output
        else:
            with tempfile.TemporaryDirectory(prefix="bondradar-operator-seed-autofill-") as tmp_dir:
                validation_input = Path(tmp_dir) / "operator_seed_autofill.json"
                write_json_report({"status": "autofill", "seeds": autofill_seeds}, validation_input)
                validation_args = _clone_args(args, mode="operator-seed-validate", operator_seed_input=validation_input)
                operator_seed_validation_report = run_operator_seed_validate(validation_args)
        if operator_seed_validation_report is None:
            validation_args = _clone_args(args, mode="operator-seed-validate", operator_seed_input=validation_input)
            operator_seed_validation_report = run_operator_seed_validate(validation_args)
        if args.operator_seed_validation_json_output is not None:
            write_json_report(operator_seed_validation_report, args.operator_seed_validation_json_output)
        if args.operator_seed_validation_markdown_output is not None:
            write_markdown_report(operator_seed_validation_report, args.operator_seed_validation_markdown_output)
        warnings.extend(operator_seed_validation_report.get("warnings") or [])
        if operator_seed_validation_report.get("errors"):
            warnings.append(
                {
                    "message": "operator seed validation reported errors for autofill output",
                    "validation_status": operator_seed_validation_report.get("status"),
                }
            )

    if not candidate_rows_with_url and not errors:
        warnings.append({"message": "no official operator seed candidates found"})
    status = "failed" if errors else "warning" if warnings or review_count or not candidate_rows_with_url else "passed"
    report = {
        "status": status,
        "mode": "operator-seed-candidate-discover",
        "issuer_count": len(required_issuers),
        "candidate_count": len(candidate_rows_with_url),
        "reviewed_candidate_count": reviewed_count,
        "needs_operator_review_count": review_count,
        "invalid_candidate_count": invalid_count,
        "blocked_candidate_count": blocked_count,
        "candidate_count_before_filter": ranking_stats["candidate_count_before_filter"],
        "candidate_count_after_filter": ranking_stats["candidate_count_after_filter"],
        "filtered_candidate_count": ranking_stats["filtered_candidate_count"],
        "filtered_noise_count": ranking_stats["filtered_noise_count"],
        "filtered_low_score_count": ranking_stats["filtered_low_score_count"],
        "filtered_duplicate_count": ranking_stats["filtered_duplicate_count"],
        "top_ranked_candidate_count": ranking_stats["top_ranked_candidate_count"],
        "candidates": candidates,
        "autofill_output_written": bool(args.operator_seed_autofill_output and not errors),
        "autofill_candidate_count": autofill_candidate_count,
        "autofill_reviewed_count": autofill_reviewed_count,
        "autofill_review_needed_count": autofill_review_needed_count,
        "operator_seed_validation_report": operator_seed_validation_report,
        "warnings": warnings,
        "errors": errors,
        "operator_seed_candidate_output": _path_value(args.operator_seed_candidate_output),
        "operator_seed_candidate_csv_output": _path_value(args.operator_seed_candidate_csv_output),
        "operator_seed_autofill_output": _path_value(args.operator_seed_autofill_output),
        "operator_seed_autofill_csv_output": _path_value(args.operator_seed_autofill_csv_output),
        "operator_seed_validation_json_output": _path_value(args.operator_seed_validation_json_output),
        "operator_seed_validation_markdown_output": _path_value(args.operator_seed_validation_markdown_output),
        "next_steps": _next_steps("operator-seed-candidate-discover", status),
        **SAFETY_FLAGS,
    }
    if args.operator_seed_candidate_output is not None and not errors:
        write_json_report(report, args.operator_seed_candidate_output)
    return report


def run_operator_seed_review_template(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    operator_seed_rows: list[dict[str, Any]] = []
    if args.operator_seed_candidate_input is None:
        errors.append({"message": "operator-seed-review-template mode requires --operator-seed-candidate-input"})
    if args.operator_seed_input is None:
        errors.append({"message": "operator-seed-review-template mode requires --operator-seed-input"})
    if not errors:
        try:
            candidates = load_operator_seed_candidate_items(args.operator_seed_candidate_input)
            operator_seed_rows = load_operator_seed_items(args.operator_seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _operator_seed_required_issuers(
        args,
        input_documents=operator_seed_rows,
        seed_issuers=[],
        financial_rows=[],
    )
    review_items: list[dict[str, Any]] = []
    if args.operator_seed_review_default_decision not in OPERATOR_SEED_REVIEW_DECISIONS:
        errors.append({"message": "operator seed review default decision is not supported"})
    if not errors:
        candidate_items = _operator_seed_review_candidate_items(
            candidates,
            required_issuers=required_issuers,
            args=args,
            errors=errors,
        )
        review_items.extend(candidate_items)
        if args.operator_seed_review_include_missing:
            review_items.extend(
                _operator_seed_review_missing_items(
                    operator_seed_rows,
                    review_items=review_items,
                    required_issuers=required_issuers,
                    args=args,
                )
            )

    candidate_review_count = sum(1 for item in review_items if item.get("candidate_seed_url"))
    missing_review_count = sum(1 for item in review_items if item.get("review_status") == "missing_candidate")
    status = "failed" if errors else "template"
    report = {
        "status": status,
        "mode": "operator-seed-review-template",
        "issuer_count": len(required_issuers),
        "review_item_count": len(review_items),
        "candidate_review_item_count": candidate_review_count,
        "missing_review_item_count": missing_review_count,
        "review_items": review_items,
        "warnings": warnings,
        "errors": errors,
        "operator_seed_candidate_input": _path_value(args.operator_seed_candidate_input),
        "operator_seed_input": _path_value(args.operator_seed_input),
        "operator_seed_review_output": _path_value(args.operator_seed_review_output),
        "operator_seed_review_csv_output": _path_value(args.operator_seed_review_csv_output),
        "next_steps": _next_steps("operator-seed-review-template", status),
        **SAFETY_FLAGS,
    }
    if args.operator_seed_review_output is not None and not errors:
        write_json_report(report, args.operator_seed_review_output)
    if args.operator_seed_review_csv_output is not None and not errors:
        write_operator_seed_review_csv(review_items, args.operator_seed_review_csv_output)
    return report


def run_operator_seed_promote_reviewed(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    operator_seed_rows: list[dict[str, Any]] = []
    if args.operator_seed_review_input is None:
        errors.append({"message": "operator-seed-promote-reviewed mode requires --operator-seed-review-input"})
    if args.operator_seed_input is None:
        errors.append({"message": "operator-seed-promote-reviewed mode requires --operator-seed-input"})
    if not errors:
        try:
            review_items = load_operator_seed_review_items(args.operator_seed_review_input)
            operator_seed_rows = load_operator_seed_items(args.operator_seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _operator_seed_required_issuers(
        args,
        input_documents=[*operator_seed_rows, *review_items],
        seed_issuers=[],
        financial_rows=[],
    )
    promotion_results: list[dict[str, Any]] = []
    promoted_seed_rows: list[dict[str, Any]] = []
    if not errors:
        for index, item in enumerate(review_items, start=1):
            result = _operator_seed_promotion_result(item, row_index=index, args=args)
            promotion_results.append(result)
            warnings.extend(result.get("warnings") or [])
            errors.extend(result.get("errors") or [])
            if result.get("promotion_status") in {"promoted", "needs_more_review"}:
                promoted_seed_rows.append(result["seed"])

    promoted_seed_rows = _dedupe_promoted_operator_seed_rows(promoted_seed_rows) if args.operator_seed_promotion_dedupe else promoted_seed_rows
    seeds = _apply_promotions_to_operator_seed_rows(operator_seed_rows, promoted_seed_rows)
    approved_count = sum(1 for item in promotion_results if item.get("operator_decision") == "approve")
    promoted_count = sum(1 for item in promoted_seed_rows if item.get("operator_review_status") == "operator_reviewed")
    pending_count = sum(1 for item in promotion_results if item.get("operator_decision") in {"pending", "needs_more_review"})
    rejected_count = sum(1 for item in promotion_results if item.get("operator_decision") == "reject")
    invalid_count = sum(1 for item in promotion_results if item.get("promotion_status") == "invalid")

    operator_seed_validation_report: dict[str, Any] | None = None
    if args.operator_seed_output is not None and not errors:
        write_json_report(
            {
                "status": "operator_reviewed" if promoted_count else "template",
                "mode": "operator-seed-promote-reviewed",
                "issuer_count": len(required_issuers),
                "seeds": seeds,
                **SAFETY_FLAGS,
            },
            args.operator_seed_output,
        )
    if args.operator_seed_csv_output is not None and not errors:
        write_operator_seed_csv(seeds, args.operator_seed_csv_output)
    if args.run_operator_seed_validate and not errors:
        if args.operator_seed_output is not None:
            validation_input = args.operator_seed_output
        else:
            with tempfile.TemporaryDirectory(prefix="bondradar-operator-seed-promoted-") as tmp_dir:
                validation_input = Path(tmp_dir) / "operator_seed_promoted.json"
                write_json_report({"status": "operator_reviewed", "seeds": seeds}, validation_input)
                validation_args = _clone_args(args, mode="operator-seed-validate", operator_seed_input=validation_input)
                operator_seed_validation_report = run_operator_seed_validate(validation_args)
        if operator_seed_validation_report is None:
            validation_args = _clone_args(args, mode="operator-seed-validate", operator_seed_input=validation_input)
            operator_seed_validation_report = run_operator_seed_validate(validation_args)
        if args.operator_seed_validation_json_output is not None:
            write_json_report(operator_seed_validation_report, args.operator_seed_validation_json_output)
        if args.operator_seed_validation_markdown_output is not None:
            write_markdown_report(operator_seed_validation_report, args.operator_seed_validation_markdown_output)
        if operator_seed_validation_report.get("errors"):
            warnings.append(
                {
                    "message": "operator seed validation reported errors for promoted output",
                    "validation_status": operator_seed_validation_report.get("status"),
                }
            )

    if not errors and promoted_count == 0:
        warnings.append({"message": "no approved operator seed review rows were promoted"})
    status = "failed" if errors else "warning" if warnings or promoted_count == 0 else "passed"
    report = {
        "status": status,
        "mode": "operator-seed-promote-reviewed",
        "issuer_count": len(required_issuers),
        "review_item_count": len(review_items),
        "approved_count": approved_count,
        "promoted_seed_count": promoted_count,
        "pending_count": pending_count,
        "rejected_count": rejected_count,
        "invalid_review_item_count": invalid_count,
        "seeds": seeds,
        "promotion_results": promotion_results,
        "not_promoted_items": [item for item in promotion_results if item.get("promotion_status") != "promoted"],
        "operator_seed_validation_report": operator_seed_validation_report,
        "warnings": warnings,
        "errors": errors,
        "operator_seed_review_input": _path_value(args.operator_seed_review_input),
        "operator_seed_output": _path_value(args.operator_seed_output),
        "operator_seed_csv_output": _path_value(args.operator_seed_csv_output),
        "operator_seed_validation_json_output": _path_value(args.operator_seed_validation_json_output),
        "operator_seed_validation_markdown_output": _path_value(args.operator_seed_validation_markdown_output),
        "next_steps": _next_steps("operator-seed-promote-reviewed", status),
        **SAFETY_FLAGS,
    }
    return report


def run_official_seed_resolve(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_documents: list[dict[str, Any]] = []
    source_issuers: list[dict[str, Any]] = []
    document_issuers: list[dict[str, Any]] = []
    financial_rows: list[dict[str, Any]] = []
    operator_seeds: list[dict[str, Any]] = []

    if args.document_intake_input is None:
        errors.append({"message": "official-seed-resolve mode requires --document-intake-input"})
    if not errors:
        try:
            input_documents = load_document_intake_file(args.document_intake_input)
            if args.source_intake_input is not None:
                source_issuers = load_source_intake(args.source_intake_input)
            else:
                warnings.append({"message": "source intake input is not provided; using exact intake context only"})
            if args.document_input is not None:
                document_issuers = load_document_issuers(args.document_input)
            else:
                warnings.append({"message": "document resolver output is not provided; seed context is reduced"})
            if args.financial_template_input is not None:
                financial_rows = load_template_rows(args.financial_template_input)
            else:
                warnings.append({"message": "financial template input is not provided; INN/OGRN enrichment skipped"})
            if args.operator_seed_input is not None:
                operator_seeds = load_operator_seed_items(args.operator_seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _parse_required_issuers(args, input_documents) if input_documents else []
    if not required_issuers and not errors:
        warnings.append({"message": "no issuers found for official seed resolution"})

    seed_types = _normalized_seed_types(args.seed_types)
    allowed_domains = _seed_allowed_domains(args)
    blocked_hints = _seed_blocked_hints(args)
    issuers: list[dict[str, Any]] = []
    if not errors:
        for required in required_issuers:
            issuer = _official_seed_issuer_base(
                required,
                input_documents=input_documents,
                source_issuers=source_issuers,
                document_issuers=document_issuers,
                financial_rows=financial_rows,
            )
            issuer_warnings: list[dict[str, Any]] = []
            issuer_errors: list[dict[str, Any]] = []
            raw_seeds = collect_official_seed_candidates(
                issuer,
                input_documents=input_documents,
                source_issuers=source_issuers,
                document_issuers=document_issuers,
                operator_seeds=operator_seeds,
                seed_types=seed_types,
            )
            official_seeds = _dedupe_validated_seeds(
                [
                    validate_official_seed_candidate(
                        raw_seed,
                        issuer=issuer,
                        args=args,
                        allowed_domains=allowed_domains,
                        blocked_hints=blocked_hints,
                    )
                    for raw_seed in raw_seeds
                ]
            )
            for seed in official_seeds:
                issuer_warnings.extend(seed.get("warnings") or [])
                issuer_errors.extend(seed.get("errors") or [])
            if not official_seeds:
                issuer_warnings.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name"),
                        "message": "no official seed URLs resolved for issuer",
                    }
                )
            issuers.append(
                {
                    **issuer,
                    "official_seeds": official_seeds,
                    "warnings": issuer_warnings,
                    "errors": issuer_errors,
                }
            )
            warnings.extend(issuer_warnings)
            errors.extend(issuer_errors)

    seed_count = sum(len(issuer.get("official_seeds") or []) for issuer in issuers)
    valid_seed_count = sum(
        1
        for issuer in issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("seed_status") == "valid_seed"
    )
    review_seed_count = sum(
        1
        for issuer in issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("seed_status") == "needs_operator_review"
    )
    invalid_seed_count = sum(
        1
        for issuer in issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("seed_status") in {"invalid_seed", "blocked_seed"}
    )
    blocked_seed_count = sum(
        1
        for issuer in issuers
        for seed in issuer.get("official_seeds") or []
        if seed.get("seed_status") == "blocked_seed"
    )
    seed_pack_status = (
        "failed"
        if errors
        else "passed"
        if issuers and all(
            any(seed.get("seed_status") == "valid_seed" for seed in issuer.get("official_seeds") or [])
            for issuer in issuers
        )
        else "warning"
    )
    seed_pack_report = {
        "status": seed_pack_status,
        "mode": "official-seed-resolve",
        "issuer_count": len(issuers),
        "seed_count": seed_count,
        "valid_seed_count": valid_seed_count,
        "needs_operator_review_count": review_seed_count,
        "invalid_seed_count": invalid_seed_count,
        "blocked_seed_count": blocked_seed_count,
        "issuers": issuers,
        "warnings": warnings,
        "errors": errors,
        "seed_output": _path_value(args.seed_output),
        "seed_csv_output": _path_value(args.seed_csv_output),
        "next_steps": _next_steps("official-seed-resolve", seed_pack_status),
        **SAFETY_FLAGS,
    }

    if args.seed_output is not None:
        write_json_report(seed_pack_report, args.seed_output)
    if args.seed_csv_output is not None:
        write_seed_csv(issuers, args.seed_csv_output)

    candidate_discovery_report: dict[str, Any] | None = None
    if args.run_quality_gate and not args.run_candidate_discovery:
        warnings.append(
            {
                "message": "run-quality-gate requested without run-candidate-discovery; quality gate skipped",
            }
        )
    if args.run_candidate_discovery and not errors:
        with tempfile.TemporaryDirectory(prefix="bondradar-official-seeds-") as tmp_dir:
            seed_input = args.seed_output or Path(tmp_dir) / "official_seed_pack.json"
            if not seed_input.is_file():
                write_json_report(seed_pack_report, seed_input)
            discovery_args = _clone_args(
                args,
                mode="document-candidate-discover",
                seed_input=seed_input,
            )
            candidate_discovery_report = run_document_candidate_discover(discovery_args)
            warnings.extend(candidate_discovery_report.get("warnings") or [])
            if candidate_discovery_report.get("errors"):
                errors.extend(candidate_discovery_report.get("errors") or [])

    quality_gate_report = (
        (candidate_discovery_report or {}).get("quality_gate_report")
        if candidate_discovery_report
        else None
    )
    status = (
        "failed"
        if errors
        else "warning"
        if warnings
        or (
            candidate_discovery_report is not None
            and candidate_discovery_report.get("status") != "passed"
        )
        or (
            quality_gate_report is not None
            and not quality_gate_report.get("gate_passed")
        )
        else seed_pack_status
    )
    report = {
        **seed_pack_report,
        "status": status,
        "candidate_discovery_report": candidate_discovery_report,
        "quality_gate_report": quality_gate_report,
        "candidate_output": _path_value(args.candidate_output),
        "candidate_csv_output": _path_value(args.candidate_csv_output),
        "seed_input": _path_value(args.seed_input),
        "quality_gate_json_output": _path_value(args.quality_gate_json_output),
        "quality_gate_markdown_output": _path_value(args.quality_gate_markdown_output),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("official-seed-resolve", status),
    }
    return report


def run_document_candidate_discover(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_documents: list[dict[str, Any]] = []
    source_issuers: list[dict[str, Any]] = []
    document_issuers: list[dict[str, Any]] = []
    seed_issuers: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    blocked_candidate_count = 0

    if args.document_intake_input is None:
        errors.append({"message": "document-candidate-discover mode requires --document-intake-input"})
    if args.source_intake_input is None:
        warnings.append({"message": "source intake input is not provided; using exact intake context only"})
    if not errors:
        try:
            input_documents = load_document_intake_file(args.document_intake_input)
            if args.source_intake_input is not None:
                source_issuers = load_source_intake(args.source_intake_input)
            if args.document_input is not None:
                document_issuers = load_document_issuers(args.document_input)
            if args.seed_input is not None:
                seed_issuers = load_seed_pack_issuers(args.seed_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    required_issuers = _parse_required_issuers(args, input_documents)
    allowed_domains = _candidate_allowed_domains(args)
    blocked_hints = _candidate_blocked_hints(args)
    if not errors:
        for issuer in required_issuers:
            seed_urls = build_candidate_seed_urls(
                issuer,
                input_documents=input_documents,
                source_issuers=source_issuers,
                document_issuers=document_issuers,
                seed_issuers=seed_issuers,
                args=args,
                allowed_domains=allowed_domains,
                blocked_hints=blocked_hints,
                warnings=warnings,
            )
            issuer_candidates: list[dict[str, Any]] = []
            for seed_url in seed_urls[: max(args.max_pages_per_issuer, 0)]:
                fetch = _fetch_candidate_page(
                    seed_url,
                    timeout_seconds=args.candidate_fetch_timeout_seconds,
                    max_bytes=args.candidate_max_response_bytes,
                    user_agent=args.candidate_user_agent,
                )
                if fetch.get("status") != "ok":
                    warnings.append(
                        {
                            "company_id": issuer.get("company_id"),
                            "source_page_url": seed_url,
                            "message": "failed to fetch official seed page",
                            "error": fetch.get("error"),
                        }
                    )
                    continue
                content_type = str(fetch.get("content_type") or "").casefold()
                if "html" not in content_type:
                    warnings.append(
                        {
                            "company_id": issuer.get("company_id"),
                            "source_page_url": seed_url,
                            "content_type": fetch.get("content_type"),
                            "message": "seed response is not HTML; skipped anchor extraction",
                        }
                    )
                    continue
                anchors = _extract_html_anchors(str(fetch.get("body") or ""), seed_url)
                for anchor in anchors[: max(args.max_links_per_page, 0)]:
                    candidate, blocked = build_document_candidate_from_anchor(
                        issuer,
                        anchor,
                        seed_url,
                        source_context=_source_url_context_from_strings(seed_urls),
                        args=args,
                        allowed_domains=allowed_domains,
                        blocked_hints=blocked_hints,
                    )
                    if blocked:
                        blocked_candidate_count += 1
                    if candidate is not None:
                        issuer_candidates.append(candidate)
            documents.extend(
                _select_top_document_candidates(
                    issuer_candidates,
                    max_candidates=max(args.max_candidate_links, 0),
                )
            )
            if not issuer_candidates:
                warnings.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name"),
                        "message": "no exact official document candidates found",
                    }
                )

    reviewed_count = sum(1 for item in documents if item.get("operator_review_status") == "operator_reviewed")
    review_count = sum(1 for item in documents if item.get("operator_review_status") == "needs_operator_review")
    if args.candidate_output is not None and not errors:
        write_json_report(
            {
                "status": "discovered",
                "mode": "document-candidate-discover",
                "issuer_count": len(required_issuers),
                "documents": documents,
                **SAFETY_FLAGS,
            },
            args.candidate_output,
        )
    if args.candidate_csv_output is not None and not errors:
        write_document_candidate_csv(documents, args.candidate_csv_output)

    quality_gate_report: dict[str, Any] | None = None
    if args.run_quality_gate and not errors:
        with tempfile.TemporaryDirectory(prefix="bondradar-candidate-gate-") as tmp_dir:
            candidate_path = args.candidate_output or Path(tmp_dir) / "exact_document_candidates.json"
            if not candidate_path.is_file():
                write_json_report(
                    {
                        "status": "discovered",
                        "mode": "document-candidate-discover",
                        "issuer_count": len(required_issuers),
                        "documents": documents,
                        **SAFETY_FLAGS,
                    },
                    candidate_path,
                )
            gate_args = _clone_args(
                args,
                mode="document-quality-gate",
                exact_document_candidates_input=candidate_path,
                document_output=args.document_input,
            )
            quality_gate_report = run_document_quality_gate(gate_args)
            if args.quality_gate_json_output is not None:
                write_json_report(quality_gate_report, args.quality_gate_json_output)
            if args.quality_gate_markdown_output is not None:
                write_markdown_report(quality_gate_report, args.quality_gate_markdown_output)

    status = (
        "failed"
        if errors
        else "passed"
        if reviewed_count >= len(required_issuers) and required_issuers
        else "warning"
    )
    report = {
        "status": status,
        "mode": "document-candidate-discover",
        "issuer_count": len(required_issuers),
        "candidate_count": len(documents),
        "reviewed_candidate_count": reviewed_count,
        "needs_operator_review_count": review_count,
        "blocked_candidate_count": blocked_candidate_count,
        "documents": documents,
        "quality_gate_report": quality_gate_report,
        "candidate_output": _path_value(args.candidate_output),
        "candidate_csv_output": _path_value(args.candidate_csv_output),
        "quality_gate_json_output": _path_value(args.quality_gate_json_output),
        "quality_gate_markdown_output": _path_value(args.quality_gate_markdown_output),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("document-candidate-discover", status),
        **SAFETY_FLAGS,
    }
    return report


def fill_document_intake_item(
    document: dict[str, Any],
    args: argparse.Namespace,
    *,
    candidates: list[dict[str, Any]],
    source_context: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base = _document_intake_base(document, source_context=source_context)
    base_validation = _not_filled_result(base, "exact document candidate not provided")
    if not candidates:
        return base, base_validation

    candidate_results = [
        validate_fill_candidate(candidate, args=args, base_document=base)
        for candidate in candidates
    ]
    valid_candidates = [
        item
        for item in candidate_results
        if not item["result"]["errors"]
        and item["result"].get("document_status") == "valid_official_document"
    ]
    review_candidates = [
        item
        for item in candidate_results
        if not item["result"]["errors"]
        and item["result"].get("document_status") == "needs_operator_review"
    ]
    error_results = [item["result"] for item in candidate_results if item["result"]["errors"]]
    if valid_candidates:
        selected = sorted(
            valid_candidates,
            key=lambda item: _candidate_sort_key(item["candidate"], args),
            reverse=True,
        )[0]
        filled = merge_document_candidate(base, selected["candidate"], operator_status="operator_reviewed")
        validation = validate_document_intake_item(
            filled,
            args=args,
            issuer=_issuer_from_document_intake_item(filled),
        )
        _attach_optional_document_metadata(filled, validation, args)
        return filled, validation

    if review_candidates:
        selected = sorted(
            review_candidates,
            key=lambda item: _candidate_sort_key(item["candidate"], args),
            reverse=True,
        )[0]
        filled = merge_document_candidate(base, selected["candidate"], operator_status="needs_operator_review")
        validation = selected["result"]
        validation["warnings"] = list(validation.get("warnings") or []) + [
            {
                "company_id": base.get("company_id"),
                "message": "candidate requires operator review before it can fill exact document intake",
            }
        ]
        return filled, validation

    if error_results:
        return base, _combine_error_results(base, error_results)
    return base, base_validation


def validate_fill_candidate(
    candidate: dict[str, Any],
    *,
    args: argparse.Namespace,
    base_document: dict[str, Any],
) -> dict[str, Any]:
    normalized = merge_document_candidate(base_document, candidate, operator_status=None)
    if not normalized.get("confidence"):
        normalized["confidence"] = "high"
    if not args.allow_reviewed_candidates:
        normalized["operator_review_status"] = "needs_operator_review"
    validation_payload = dict(normalized)
    for field in FORBIDDEN_DOCUMENT_FINANCIAL_FIELDS:
        if candidate.get(field) not in (None, ""):
            validation_payload[field] = candidate.get(field)
    if candidate.get("values"):
        validation_payload["values"] = candidate.get("values")
    result = validate_document_intake_item(
        validation_payload,
        args=args,
        issuer=_issuer_from_document_intake_item(validation_payload),
    )
    if _confidence_rank(normalized.get("confidence")) < _confidence_rank(args.min_document_confidence):
        result["warnings"].append(
            {
                "company_id": normalized.get("company_id"),
                "document_url": normalized.get("document_url"),
                "message": "document confidence is below minimum threshold",
                "confidence": normalized.get("confidence"),
                "min_document_confidence": args.min_document_confidence,
            }
        )
        if args.require_exact_document:
            result["document_status"] = "needs_operator_review"
    if result.get("document_status") == "needs_operator_review":
        result["errors"] = []
    return {"candidate": normalized, "result": result}


def merge_document_candidate(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    operator_status: str | None,
) -> dict[str, Any]:
    merged = dict(base)
    for field in (
        "source_type",
        "document_url",
        "document_title",
        "document_date",
        "source_file_name",
        "notes",
    ):
        value = candidate.get(field)
        if value not in (None, ""):
            merged[field] = value
    merged["report_period"] = str(candidate.get("report_period") or merged.get("report_period") or "")
    merged["report_type"] = candidate.get("report_type") or merged.get("report_type")
    merged["accounting_standard"] = candidate.get("accounting_standard") or merged.get("accounting_standard")
    if operator_status is not None:
        merged["operator_review_status"] = operator_status
    else:
        merged["operator_review_status"] = (
            candidate.get("operator_review_status")
            or merged.get("operator_review_status")
            or "operator_to_fill"
        )
    if candidate.get("confidence"):
        merged["confidence"] = candidate.get("confidence")
    _strip_financial_values(merged)
    return merged


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


def load_document_intake_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".csv":
        return load_document_intake_csv(path)
    return load_document_intake_items(path)


def load_exact_document_candidate_items(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return load_document_intake_file(path)


def load_document_intake_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"document intake CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [
            {key: _normalize_cell(value) for key, value in row.items() if key}
            for row in reader
        ]


def load_source_context_by_key(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    result: dict[str, str] = {}
    for issuer in load_source_intake(path):
        key = _issuer_source_key(issuer)
        sources = issuer.get("source_candidates") or []
        result[key] = _source_url_context(sources)
    return result


def load_document_context_by_key(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    result: dict[str, str] = {}
    for issuer in load_document_issuers(path):
        key = _issuer_source_key(issuer)
        result[key] = _source_url_context(issuer.get("document_candidates") or [])
    return result


def load_document_issuers(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"document input does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    issuers = payload.get("issuers") if isinstance(payload, dict) else payload
    if not isinstance(issuers, list) or not all(isinstance(item, dict) for item in issuers):
        raise ValueError("document JSON must contain issuers")
    return issuers


def load_operator_seed_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"operator seed input does not exist: {path}")
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    seeds = payload.get("seeds") if isinstance(payload, dict) else payload
    if not isinstance(seeds, list) or not all(isinstance(item, dict) for item in seeds):
        raise ValueError("operator seed JSON must contain seeds")
    return seeds


def load_operator_seed_candidate_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"operator seed candidate input does not exist: {path}")
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    candidates = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
        raise ValueError("operator seed candidate JSON must contain candidates")
    return candidates


def load_operator_seed_review_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"operator seed review input does not exist: {path}")
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader]
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    review_items = payload.get("review_items") if isinstance(payload, dict) else payload
    if not isinstance(review_items, list) or not all(isinstance(item, dict) for item in review_items):
        raise ValueError("operator seed review JSON must contain review_items")
    return review_items


def load_seed_pack_issuers(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"seed input does not exist: {path}")
    if path.suffix.casefold() == ".csv":
        grouped: dict[str, dict[str, Any]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            for raw in reader:
                row = {key: _normalize_cell(value) for key, value in raw.items() if key}
                key = str(row.get("canonical_company_id") or row.get("company_id") or "")
                issuer = grouped.setdefault(
                    key,
                    {
                        "company_id": _maybe_int(row.get("company_id")),
                        "company_name": row.get("company_name") or "",
                        "canonical_company_id": _maybe_int(row.get("canonical_company_id") or row.get("company_id")),
                        "canonical_company_name": row.get("canonical_company_name") or row.get("company_name") or "",
                        "inn": row.get("inn") or "",
                        "ogrn": row.get("ogrn") or "",
                        "official_seeds": [],
                    },
                )
                issuer["official_seeds"].append(
                    {
                        "seed_type": row.get("seed_type") or "",
                        "seed_url": row.get("seed_url") or "",
                        "seed_status": row.get("seed_status") or "",
                        "confidence": row.get("confidence") or "",
                        "source": row.get("source") or "",
                        "reason": row.get("reason") or "",
                    }
                )
        return list(grouped.values())
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    issuers = payload.get("issuers") if isinstance(payload, dict) else payload
    if not isinstance(issuers, list) or not all(isinstance(item, dict) for item in issuers):
        raise ValueError("seed JSON must contain issuers")
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


def write_document_candidate_csv(documents: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCUMENT_CANDIDATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for document in documents:
            writer.writerow({field: _csv_value(document.get(field)) for field in DOCUMENT_CANDIDATE_FIELDS})


def write_seed_csv(issuers: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SEED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for issuer in issuers:
            for seed in issuer.get("official_seeds") or []:
                row = {
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name"),
                    "canonical_company_id": issuer.get("canonical_company_id"),
                    "canonical_company_name": issuer.get("canonical_company_name"),
                    "inn": issuer.get("inn"),
                    "ogrn": issuer.get("ogrn"),
                    **seed,
                }
                writer.writerow({field: _csv_value(row.get(field)) for field in SEED_FIELDS})


def write_operator_seed_csv(seeds: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_SEED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for seed in seeds:
            writer.writerow({field: _csv_value(seed.get(field)) for field in OPERATOR_SEED_FIELDS})


def write_operator_seed_candidate_csv(candidates: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_SEED_CANDIDATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({field: _csv_value(candidate.get(field)) for field in OPERATOR_SEED_CANDIDATE_FIELDS})


def write_operator_seed_review_csv(review_items: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_SEED_REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for item in review_items:
            writer.writerow({field: _csv_value(item.get(field)) for field in OPERATOR_SEED_REVIEW_FIELDS})


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
        else "Exact Document Intake Fill"
        if report.get("mode") == "document-intake-fill"
        else "Exact Document Quality Gate"
        if report.get("mode") == "document-quality-gate"
        else "Exact Official Document Candidate Discovery"
        if report.get("mode") == "document-candidate-discover"
        else "Operator Official Seed Template"
        if report.get("mode") == "operator-seed-template"
        else "Operator Official Seed Validation"
        if report.get("mode") == "operator-seed-validate"
        else "Operator Official Seed Merge"
        if report.get("mode") == "operator-seed-merge"
        else "Operator Official Seed Candidate Discovery"
        if report.get("mode") == "operator-seed-candidate-discover"
        else "Operator Seed Review Template"
        if report.get("mode") == "operator-seed-review-template"
        else "Operator Seed Promotion"
        if report.get("mode") == "operator-seed-promote-reviewed"
        else "Official Seed Resolver"
        if report.get("mode") == "official-seed-resolve"
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
    if report.get("mode") in {"document-intake-template", "document-intake-validate", "document-intake-fill"}:
        lines.extend(_render_document_intake_markdown_sections(report))
    if report.get("mode") == "document-quality-gate":
        lines.extend(_render_document_quality_gate_markdown_sections(report))
    if report.get("mode") == "document-candidate-discover":
        lines.extend(_render_document_candidate_discovery_markdown_sections(report))
    if report.get("mode") == "operator-seed-template":
        lines.extend(_render_operator_seed_template_markdown_sections(report))
    if report.get("mode") == "operator-seed-validate":
        lines.extend(_render_operator_seed_validation_markdown_sections(report))
    if report.get("mode") == "operator-seed-merge":
        lines.extend(_render_operator_seed_merge_markdown_sections(report))
    if report.get("mode") == "operator-seed-candidate-discover":
        lines.extend(_render_operator_seed_candidate_markdown_sections(report))
    if report.get("mode") == "operator-seed-review-template":
        lines.extend(_render_operator_seed_review_template_markdown_sections(report))
    if report.get("mode") == "operator-seed-promote-reviewed":
        lines.extend(_render_operator_seed_promote_markdown_sections(report))
    if report.get("mode") == "official-seed-resolve":
        lines.extend(_render_official_seed_markdown_sections(report))
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
        f"- filled_document_count: {report.get('filled_document_count', 0)}",
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


def _render_document_quality_gate_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Quality Gate",
        "",
        f"- gate_passed: {report.get('gate_passed')}",
        f"- ready_for_value_extraction: {report.get('ready_for_value_extraction')}",
        f"- ready_for_import: {report.get('ready_for_import')}",
        f"- required_issuer_count: {report.get('required_issuer_count', 0)}",
        f"- covered_required_issuer_count: {report.get('covered_required_issuer_count', 0)}",
        f"- filled_document_count: {report.get('filled_document_count', 0)}",
        f"- valid_document_count: {report.get('valid_document_count', 0)}",
        f"- resolved_document_count: {report.get('resolved_document_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        f"- invalid_document_count: {report.get('invalid_document_count', 0)}",
        "",
        "## Required Issuers",
        "",
        "| Company ID | Company | Document Status | Gate Status | Reason | URL | Title |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for item in report.get("required_issuers") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {document_status} | {gate_status} | {reason} | {url} | {title} |".format(
                company_id=item.get("company_id") or "",
                company_name=str(item.get("company_name") or "").replace("|", "/"),
                document_status=item.get("document_status") or "",
                gate_status=item.get("gate_status") or "",
                reason=str(item.get("reason") or "").replace("|", "/"),
                url=str(item.get("document_url") or "").replace("|", "/"),
                title=str(item.get("document_title") or "").replace("|", "/"),
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  | No required issuers |  |  |")
    fill_report = report.get("fill_report") or {}
    validation_report = report.get("validation_report") or {}
    resolve_report = report.get("resolve_report") or {}
    lines.extend(
        [
            "",
            "## Pipeline Steps",
            "",
            f"- document-intake-fill status: `{fill_report.get('status')}`",
            f"- document-intake-validate status: `{validation_report.get('status')}`",
            f"- document-resolve status: `{resolve_report.get('status')}`",
            "",
            "## Blocking Issues",
            "",
        ]
    )
    blockers = report.get("errors") or []
    if blockers:
        lines.extend(f"- {_message_text(item)}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
    return lines


def _render_document_candidate_discovery_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Candidate Discovery",
        "",
        f"- candidate_count: {report.get('candidate_count', 0)}",
        f"- reviewed_candidate_count: {report.get('reviewed_candidate_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        f"- blocked_candidate_count: {report.get('blocked_candidate_count', 0)}",
        "",
        "## Issuer Candidates",
        "",
        "| Company ID | Company | URL | Title | Score | Confidence | Operator Status | Source Page | Reasons | Negative Reasons |",
        "| ---: | --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for document in report.get("documents") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {url} | {title} | {score} | {confidence} | {status} | {source_page} | {reasons} | {negative} |".format(
                company_id=document.get("company_id") or "",
                company_name=str(document.get("company_name") or "").replace("|", "/"),
                url=str(document.get("document_url") or "").replace("|", "/"),
                title=str(document.get("document_title") or "").replace("|", "/"),
                score=document.get("candidate_score") or 0,
                confidence=document.get("candidate_confidence") or "",
                status=document.get("operator_review_status") or "",
                source_page=str(document.get("source_page_url") or "").replace("|", "/"),
                reasons=_csv_value(document.get("score_reasons")),
                negative=_csv_value(document.get("negative_reasons")),
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |  |")
    gate = report.get("quality_gate_report") or {}
    lines.extend(
        [
            "",
            "## Quality Gate",
            "",
            f"- gate status: `{gate.get('status')}`",
            f"- gate_passed: {gate.get('gate_passed')}",
            f"- ready_for_value_extraction: {gate.get('ready_for_value_extraction')}",
            f"- ready_for_import: {gate.get('ready_for_import')}",
            "",
            "## Blocking Issues",
            "",
        ]
    )
    blockers = report.get("errors") or report.get("warnings") or []
    if blockers:
        lines.extend(f"- {_message_text(item)}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
    return lines


def _render_operator_seed_template_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Seeds To Fill",
        "",
        f"- seed_template_count: {report.get('seed_template_count', 0)}",
        "",
        "| Company ID | Company | INN | OGRN | Seed Type | Seed URL | Operator Status | Source Context | Operator Action |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for seed in report.get("seeds") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {inn} | {ogrn} | {seed_type} | {seed_url} | {status} | {context} | {notes} |".format(
                company_id=seed.get("company_id") or "",
                company_name=str(seed.get("company_name") or "").replace("|", "/"),
                inn=seed.get("inn") or "",
                ogrn=seed.get("ogrn") or "",
                seed_type=seed.get("seed_type") or "",
                seed_url=seed.get("seed_url") or "",
                status=seed.get("operator_review_status") or "",
                context=str(seed.get("source_context") or "").replace("|", "/"),
                notes=str(seed.get("notes") or "").replace("|", "/"),
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Use official issuer/disclosure/MOEX pages only.",
            "- Do not use search results.",
            "- Do not use news/blog/forum/social pages.",
            "- Do not paste financial values.",
            "- Do not paste exact financial figures.",
            "- Seed pages do not bypass exact document quality gate.",
            "",
        ]
    )
    return lines


def _render_operator_seed_validation_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Operator Seed Validation",
        "",
        f"- valid_seed_count: {report.get('valid_seed_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        f"- invalid_seed_count: {report.get('invalid_seed_count', 0)}",
        "",
        "| Company ID | Company | INN | OGRN | Seed Type | Seed URL | Status | Confidence | Operator Status |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for seed in report.get("seeds") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {inn} | {ogrn} | {seed_type} | {seed_url} | {status} | {confidence} | {review} |".format(
                company_id=seed.get("company_id") or "",
                company_name=str(seed.get("company_name") or "").replace("|", "/"),
                inn=seed.get("inn") or "",
                ogrn=seed.get("ogrn") or "",
                seed_type=seed.get("seed_type") or "",
                seed_url=str(seed.get("seed_url") or "").replace("|", "/"),
                status=seed.get("seed_status") or "",
                confidence=seed.get("confidence") or "",
                review=seed.get("operator_review_status") or "",
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Blocking Issues",
            "",
        ]
    )
    blockers = report.get("errors") or []
    if blockers:
        lines.extend(f"- {_message_text(item)}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
    return lines


def _render_operator_seed_merge_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Operator Seed Merge",
        "",
        f"- existing seed count: {report.get('existing_seed_count', 0)}",
        f"- operator seed count: {report.get('operator_seed_count', 0)}",
        f"- merged seed count: {report.get('merged_seed_count', 0)}",
        f"- valid merged count: {report.get('valid_merged_count', 0)}",
        f"- review-needed count: {report.get('review_needed_count', 0)}",
        f"- invalid rejected count: {report.get('invalid_rejected_count', 0)}",
        "",
    ]
    validation = report.get("validation_report") or {}
    lines.extend(
        [
            "## Validation Summary",
            "",
            f"- valid_seed_count: {validation.get('valid_seed_count', 0)}",
            f"- needs_operator_review_count: {validation.get('needs_operator_review_count', 0)}",
            f"- invalid_seed_count: {validation.get('invalid_seed_count', 0)}",
            "",
        ]
    )
    return lines


def _render_operator_seed_candidate_markdown_sections(report: dict[str, Any]) -> list[str]:
    validation = report.get("operator_seed_validation_report") or {}
    lines = [
        "## Ranking Summary",
        "",
        f"- candidate_count_before_filter: {report.get('candidate_count_before_filter', 0)}",
        f"- candidate_count_after_filter: {report.get('candidate_count_after_filter', 0)}",
        f"- filtered_candidate_count: {report.get('filtered_candidate_count', 0)}",
        f"- filtered_noise_count: {report.get('filtered_noise_count', 0)}",
        f"- filtered_low_score_count: {report.get('filtered_low_score_count', 0)}",
        f"- filtered_duplicate_count: {report.get('filtered_duplicate_count', 0)}",
        f"- top_ranked_candidate_count: {report.get('top_ranked_candidate_count', 0)}",
        "",
        "## Candidates",
        "",
        f"- reviewed_candidate_count: {report.get('reviewed_candidate_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        f"- invalid_candidate_count: {report.get('invalid_candidate_count', 0)}",
        f"- blocked_candidate_count: {report.get('blocked_candidate_count', 0)}",
        "",
        "## Top Candidates",
        "",
        "| Company ID | Company | Seed Type | Rank | Candidate URL | Title | Final Score | Status | Operator Status | Score Reasons | Negative Reasons |",
        "| ---: | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    rows = 0
    for candidate in report.get("candidates") or []:
        if candidate.get("candidate_seed_url") and candidate.get("filter_status") != "kept":
            continue
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {seed_type} | {rank} | {url} | {title} | {score} | {status} | {review} | {reasons} | {negatives} |".format(
                company_id=candidate.get("company_id") or "",
                company_name=str(candidate.get("company_name") or "").replace("|", "/"),
                seed_type=candidate.get("seed_type") or "",
                rank=candidate.get("candidate_rank") or "",
                url=str(candidate.get("candidate_seed_url") or "").replace("|", "/"),
                title=str(candidate.get("candidate_title") or "").replace("|", "/"),
                score=candidate.get("final_score", candidate.get("candidate_score", 0)) or 0,
                status=candidate.get("candidate_status") or "",
                review=candidate.get("operator_review_status") or "",
                reasons=_csv_value(candidate.get("score_reasons") or []).replace("|", "/"),
                negatives=_csv_value(candidate.get("negative_reasons") or []).replace("|", "/"),
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |  |  |")
    filtered_candidates = [
        candidate
        for candidate in report.get("candidates") or []
        if candidate.get("candidate_seed_url") and candidate.get("filter_status") != "kept"
    ]
    if filtered_candidates:
        lines.extend(
            [
                "",
                "## Filtered Candidates",
                "",
                "| Candidate URL | Title | Filter Status | Filter Reasons | Raw Score | Final Score |",
                "| --- | --- | --- | --- | ---: | ---: |",
            ]
        )
        for candidate in filtered_candidates:
            lines.append(
                "| {url} | {title} | {status} | {reasons} | {raw} | {final} |".format(
                    url=str(candidate.get("candidate_seed_url") or "").replace("|", "/"),
                    title=str(candidate.get("candidate_title") or "").replace("|", "/"),
                    status=candidate.get("filter_status") or "",
                    reasons=_csv_value(candidate.get("filter_reasons") or []).replace("|", "/"),
                    raw=candidate.get("raw_score") or 0,
                    final=candidate.get("final_score", candidate.get("candidate_score", 0)) or 0,
                )
            )
    lines.extend(
        [
            "",
            "## Autofill",
            "",
            f"- autofill_output_written: {report.get('autofill_output_written')}",
            f"- autofill_candidate_count: {report.get('autofill_candidate_count', 0)}",
            f"- autofill_reviewed_count: {report.get('autofill_reviewed_count', 0)}",
            f"- autofill_review_needed_count: {report.get('autofill_review_needed_count', 0)}",
            "",
            "## Validation",
            "",
            f"- operator-seed-validate status: `{validation.get('status')}`",
            f"- valid_seed_count: {validation.get('valid_seed_count')}",
            f"- invalid_seed_count: {validation.get('invalid_seed_count')}",
            "",
            "## Blocking Issues",
            "",
        ]
    )
    blockers = report.get("errors") or report.get("warnings") or []
    if blockers:
        lines.extend(f"- {_message_text(item)}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
    return lines


def _render_operator_seed_review_template_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Review Items",
        "",
        f"- review_item_count: {report.get('review_item_count', 0)}",
        f"- candidate_review_item_count: {report.get('candidate_review_item_count', 0)}",
        f"- missing_review_item_count: {report.get('missing_review_item_count', 0)}",
        "",
        "| Company ID | Company | Seed Type | Rank | Score | Candidate URL | Title | Decision | Review Status | Suggested Action |",
        "| ---: | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    missing: list[dict[str, Any]] = []
    for item in report.get("review_items") or []:
        if item.get("review_status") == "missing_candidate":
            missing.append(item)
            continue
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {seed_type} | {rank} | {score} | {url} | {title} | {decision} | {status} | {action} |".format(
                company_id=item.get("company_id") or "",
                company_name=str(item.get("company_name") or "").replace("|", "/"),
                seed_type=item.get("seed_type") or "",
                rank=item.get("candidate_rank") or "",
                score=item.get("candidate_score") or 0,
                url=str(item.get("candidate_seed_url") or "").replace("|", "/"),
                title=str(item.get("candidate_title") or "").replace("|", "/"),
                decision=item.get("operator_decision") or "",
                status=item.get("review_status") or "",
                action=item.get("suggested_action") or "",
            )
        )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Missing Items",
            "",
            "| Company ID | Company | Seed Type | Suggested Action |",
            "| ---: | --- | --- | --- |",
        ]
    )
    if missing:
        for item in missing:
            lines.append(
                "| {company_id} | {company_name} | {seed_type} | {action} |".format(
                    company_id=item.get("company_id") or "",
                    company_name=str(item.get("company_name") or "").replace("|", "/"),
                    seed_type=item.get("seed_type") or "",
                    action=item.get("suggested_action") or "",
                )
            )
    else:
        lines.append("|  |  |  |  |")
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Approve only official issuer/disclosure/reporting seed pages.",
            "- Do not approve search/news/blog/forum/social pages.",
            "- Do not approve exact financial values.",
            "- Seed approval does not bypass exact document quality gate.",
            "",
        ]
    )
    return lines


def _render_operator_seed_promote_markdown_sections(report: dict[str, Any]) -> list[str]:
    validation = report.get("operator_seed_validation_report") or {}
    lines = [
        "## Promotion Summary",
        "",
        f"- review_item_count: {report.get('review_item_count', 0)}",
        f"- approved_count: {report.get('approved_count', 0)}",
        f"- promoted_seed_count: {report.get('promoted_seed_count', 0)}",
        f"- pending_count: {report.get('pending_count', 0)}",
        f"- rejected_count: {report.get('rejected_count', 0)}",
        f"- invalid_review_item_count: {report.get('invalid_review_item_count', 0)}",
        "",
        "## Promoted Seeds",
        "",
        "| Company ID | Company | Seed Type | Seed URL | Operator Status |",
        "| ---: | --- | --- | --- | --- |",
    ]
    promoted_rows = [
        item
        for item in report.get("seeds") or []
        if item.get("seed_url") and item.get("operator_review_status") == "operator_reviewed"
    ]
    if promoted_rows:
        for item in promoted_rows:
            lines.append(
                "| {company_id} | {company_name} | {seed_type} | {url} | {status} |".format(
                    company_id=item.get("company_id") or "",
                    company_name=str(item.get("company_name") or "").replace("|", "/"),
                    seed_type=item.get("seed_type") or "",
                    url=str(item.get("seed_url") or "").replace("|", "/"),
                    status=item.get("operator_review_status") or "",
                )
            )
    else:
        lines.append("|  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Not Promoted",
            "",
            "| Company ID | Company | Seed Type | Decision | Status | Reason |",
            "| ---: | --- | --- | --- | --- | --- |",
        ]
    )
    not_promoted = report.get("not_promoted_items") or []
    if not_promoted:
        for item in not_promoted:
            lines.append(
                "| {company_id} | {company_name} | {seed_type} | {decision} | {status} | {reason} |".format(
                    company_id=item.get("company_id") or "",
                    company_name=str(item.get("company_name") or "").replace("|", "/"),
                    seed_type=item.get("seed_type") or "",
                    decision=item.get("operator_decision") or "",
                    status=item.get("promotion_status") or "",
                    reason=str(item.get("promotion_reason") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("|  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- operator-seed-validate status: `{validation.get('status')}`",
            f"- valid_seed_count: {validation.get('valid_seed_count')}",
            f"- invalid_seed_count: {validation.get('invalid_seed_count')}",
            f"- needs_operator_review_count: {validation.get('needs_operator_review_count')}",
            "",
        ]
    )
    return lines


def _render_official_seed_markdown_sections(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Official Seeds",
        "",
        f"- seed_count: {report.get('seed_count', 0)}",
        f"- valid_seed_count: {report.get('valid_seed_count', 0)}",
        f"- needs_operator_review_count: {report.get('needs_operator_review_count', 0)}",
        f"- invalid_seed_count: {report.get('invalid_seed_count', 0)}",
        "",
        "## Issuer Seeds",
        "",
        "| Company ID | Company | INN | OGRN | Seed Type | Seed URL | Status | Confidence | Source | Reason |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for issuer in report.get("issuers") or []:
        for seed in issuer.get("official_seeds") or []:
            rows += 1
            lines.append(
                "| {company_id} | {company_name} | {inn} | {ogrn} | {seed_type} | {seed_url} | {status} | {confidence} | {source} | {reason} |".format(
                    company_id=issuer.get("company_id") or "",
                    company_name=str(issuer.get("company_name") or "").replace("|", "/"),
                    inn=issuer.get("inn") or "",
                    ogrn=issuer.get("ogrn") or "",
                    seed_type=seed.get("seed_type") or "",
                    seed_url=str(seed.get("seed_url") or "").replace("|", "/"),
                    status=seed.get("seed_status") or "",
                    confidence=seed.get("confidence") or "",
                    source=seed.get("source") or "",
                    reason=str(seed.get("reason") or "").replace("|", "/"),
                )
            )
    if rows == 0:
        lines.append("|  |  |  |  |  |  |  |  |  |  |")
    discovery = report.get("candidate_discovery_report") or {}
    gate = report.get("quality_gate_report") or {}
    lines.extend(
        [
            "",
            "## Candidate Discovery",
            "",
            f"- candidate discovery status: `{discovery.get('status')}`",
            f"- candidate_count: {discovery.get('candidate_count')}",
            f"- reviewed_candidate_count: {discovery.get('reviewed_candidate_count')}",
            f"- needs_operator_review_count: {discovery.get('needs_operator_review_count')}",
            f"- blocked_candidate_count: {discovery.get('blocked_candidate_count')}",
            "",
            "## Quality Gate",
            "",
            f"- gate status: `{gate.get('status')}`",
            f"- gate_passed: {gate.get('gate_passed')}",
            f"- ready_for_value_extraction: {gate.get('ready_for_value_extraction')}",
            f"- ready_for_import: {gate.get('ready_for_import')}",
            "",
            "## Blocking Issues",
            "",
        ]
    )
    blockers = report.get("errors") or report.get("warnings") or []
    if blockers:
        lines.extend(f"- {_message_text(item)}" for item in blockers)
    else:
        lines.append("- None")
    lines.append("")
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
    for field in FORBIDDEN_SEED_METADATA_FIELDS:
        candidate.pop(field, None)
    candidate.pop("values", None)
    candidate.pop("field_evidence", None)


class _AnchorExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.anchors: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        href = attr_map.get("href", "").strip()
        if not href:
            return
        self._current = {
            "href": urllib.parse.urljoin(self.base_url, href),
            "title": attr_map.get("title", "").strip(),
        }
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._current is None:
            return
        text = " ".join(" ".join(self._text).split())
        self.anchors.append({**self._current, "text": text})
        self._current = None
        self._text = []


def build_candidate_seed_urls(
    issuer: dict[str, Any],
    *,
    input_documents: list[dict[str, Any]],
    source_issuers: list[dict[str, Any]],
    document_issuers: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    warnings: list[dict[str, Any]],
) -> list[str]:
    urls: list[str] = []
    for document in _items_matching_required(input_documents, issuer):
        urls.extend(_split_source_context_urls(str(document.get("source_url_context") or "")))
        if document.get("document_url"):
            urls.append(str(document["document_url"]))
    if args.candidate_discovery_source in {"official-source-intake", "manual-seeds"}:
        for source_issuer in _items_matching_required(source_issuers, issuer):
            for source in source_issuer.get("source_candidates") or []:
                url = source.get("url") or source.get("source_url")
                if url:
                    urls.append(str(url))
    if args.candidate_discovery_source in {"document-report", "manual-seeds"} or document_issuers:
        for document_issuer in _items_matching_required(document_issuers, issuer):
            for document in document_issuer.get("document_candidates") or []:
                for key in ("source_url", "document_url"):
                    if document.get(key):
                        urls.append(str(document[key]))
    for seed_issuer in _items_matching_required(seed_issuers, issuer):
        for seed in seed_issuer.get("official_seeds") or []:
            if seed.get("seed_status") not in VALID_CANDIDATE_SEED_STATUSES:
                continue
            url = seed.get("seed_url")
            if url:
                urls.append(str(url))
    seed_urls: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = _normalize_candidate_url(raw_url)
        if not url or url in seen:
            continue
        classification = _classify_candidate_url(
            url,
            allowed_domains=allowed_domains,
            blocked_hints=blocked_hints,
            allow_unknown_source=False,
        )
        if classification["status"] != "official":
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "source_url": url,
                    "message": "seed URL skipped because it is not allowlisted official source",
                    "classification": classification["status"],
                }
            )
            continue
        seen.add(url)
        seed_urls.append(url)
    return seed_urls


def build_document_candidate_from_anchor(
    issuer: dict[str, Any],
    anchor: dict[str, str],
    source_page_url: str,
    *,
    source_context: str,
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
) -> tuple[dict[str, Any] | None, bool]:
    document_url = _normalize_candidate_url(anchor.get("href") or "")
    if not document_url or _is_ignored_href(document_url):
        return None, False
    classification = _classify_candidate_url(
        document_url,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
        allow_unknown_source=args.allow_unknown_source,
    )
    if classification["status"] == "blocked":
        return None, True
    if classification["status"] == "unknown_error":
        return None, False
    title = _candidate_title(anchor, document_url)
    score, score_reasons, negative_reasons = score_document_candidate(
        document_url,
        title,
        source_page_url,
        args=args,
        domain_status=classification["status"],
    )
    if score < args.candidate_min_score:
        return None, False
    exact = _is_exact_document_candidate(document_url, title, args)
    strong = _has_strong_document_signals(document_url, title, args)
    official = classification["status"] == "official"
    operator_status = "needs_operator_review"
    if (
        official
        and exact
        and strong
        and score >= args.candidate_auto_review_threshold
    ):
        operator_status = "operator_reviewed"
    confidence = "high" if score >= args.candidate_auto_review_threshold else "medium"
    candidate = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name"),
        "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name"),
        "report_period": str(args.report_period),
        "report_type": args.report_type,
        "accounting_standard": args.accounting_standard,
        "source_type": _candidate_source_type(document_url, source_page_url),
        "source_url_context": source_context,
        "document_url": document_url,
        "document_title": title,
        "document_date": "",
        "source_file_name": _file_name_from_url(document_url),
        "operator_review_status": operator_status,
        "notes": classification["message"],
        "candidate_score": score,
        "candidate_confidence": confidence,
        "confidence": confidence,
        "discovery_method": "official_domain_anchor_scan",
        "source_page_url": source_page_url,
        "score_reasons": score_reasons,
        "negative_reasons": negative_reasons,
    }
    _strip_financial_values(candidate)
    return candidate, False


def score_document_candidate(
    document_url: str,
    title: str,
    source_page_url: str,
    *,
    args: argparse.Namespace,
    domain_status: str,
) -> tuple[int, list[str], list[str]]:
    text = f"{document_url} {title}".casefold()
    score = 0
    reasons: list[str] = []
    negatives: list[str] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    def subtract(points: int, reason: str) -> None:
        nonlocal score
        score -= points
        negatives.append(reason)

    if str(args.report_period) in text:
        add(25, "target report period")
    years = set(re.findall(r"20\d{2}", text))
    if years and str(args.report_period) not in years:
        subtract(60, "unrelated report year")
    if _contains_any(text, ("annual", "yearly", "\u0433\u043e\u0434\u043e\u0432", "\u0433\u043e\u0434\u043e\u0432\u0430")):
        add(15, "annual report signal")
    if _contains_any(text, ("ifrs", "\u043c\u0441\u0444\u043e")):
        add(15, "IFRS signal")
    if _contains_any(text, ("consolidated", "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434")):
        add(10, "consolidated signal")
    if _contains_any(text, ("audited", "auditor", "audit", "\u0430\u0443\u0434")):
        add(10, "audited report signal")
    if _contains_any(text, ("financial statements", "financial report", "statement", "\u0444\u0438\u043d\u0430\u043d\u0441", "\u043e\u0442\u0447\u0435\u0442")):
        add(15, "financial reporting signal")
    if _url_is_pdf(document_url):
        add(15, "PDF document")
    if _looks_like_report_document_url(document_url):
        add(10, "report page path")
    if domain_status == "official":
        add(10, "official allowlisted domain")
    if _candidate_source_type(document_url, source_page_url) == "official_disclosure":
        add(5, "official disclosure domain")
    if _contains_any(text, ("presentation", "presentaci", "\u043f\u0440\u0435\u0437\u0435\u043d\u0442")):
        subtract(60, "presentation document")
    if _contains_any(text, ("press", "news", "novosti", "\u043d\u043e\u0432\u043e\u0441")):
        subtract(60, "news or press release")
    if _contains_any(text, ("coupon", "bond terms", "emission", "prospectus", "securities", "\u043a\u0443\u043f\u043e\u043d", "\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442")):
        subtract(60, "bond/prospectus document")
    if args.report_type == "annual" and _contains_any(text, ("quarter", "quarterly", "q1", "q2", "q3", "q4")):
        subtract(45, "quarterly document in annual mode")
    if _looks_like_landing_page(document_url) and not args.candidate_allow_landing_pages:
        subtract(40, "landing page only")
    return max(score, 0), reasons, negatives


def _select_top_document_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        url = str(candidate.get("document_url") or "")
        existing = by_url.get(url)
        if existing is None or int(candidate.get("candidate_score") or 0) > int(existing.get("candidate_score") or 0):
            by_url[url] = candidate
    return sorted(
        by_url.values(),
        key=lambda item: int(item.get("candidate_score") or 0),
        reverse=True,
    )[:max_candidates]


def _fetch_candidate_page(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    user_agent: str,
) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read(max_bytes + 1)
            content_type = response.headers.get("Content-Type") or ""
            status = getattr(response, "status", 200)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": "error", "url": url, "error": str(exc)}
    if len(data) > max_bytes:
        return {"status": "error", "url": url, "error": "response exceeded max bytes"}
    body = data.decode("utf-8", errors="replace")
    return {
        "status": "ok",
        "url": url,
        "http_status": status,
        "content_type": content_type,
        "body": body,
        "size_bytes": len(data),
    }


def _extract_html_anchors(html: str, base_url: str) -> list[dict[str, str]]:
    parser = _AnchorExtractor(base_url)
    parser.feed(html)
    return parser.anchors


def _candidate_allowed_domains(args: argparse.Namespace) -> set[str]:
    domains = {domain.casefold() for domain in OFFICIAL_SOURCE_DOMAIN_HINTS}
    domains.update(item.casefold() for item in _split_cli_list(args.candidate_allowed_domains))
    return domains


def _candidate_blocked_hints(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *BLOCKED_SOURCE_HINTS,
                *(item.casefold() for item in _split_cli_list(args.candidate_blocked_domains)),
            ]
        )
    )


def _operator_seed_candidate_allowed_domains(args: argparse.Namespace) -> set[str]:
    domains = {domain.casefold() for domain in OFFICIAL_SOURCE_DOMAIN_HINTS}
    domains.update(item.casefold() for item in _split_cli_list(args.operator_seed_candidate_allowed_domains))
    return domains


def _operator_seed_candidate_blocked_hints(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *BLOCKED_SOURCE_HINTS,
                *(item.casefold() for item in _split_cli_list(args.operator_seed_candidate_blocked_domains)),
            ]
        )
    )


def build_operator_seed_candidate_source_urls(
    issuer: dict[str, Any],
    *,
    operator_seed_rows: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    warnings: list[dict[str, Any]],
) -> list[str]:
    urls: list[str] = []
    source = args.operator_seed_candidate_source
    if source == "operator-template-context":
        for row in operator_seed_rows:
            urls.extend(_split_source_context_urls(str(row.get("source_context") or "")))
    else:
        for seed_issuer in _items_matching_required(seed_issuers, issuer):
            for seed in seed_issuer.get("official_seeds") or []:
                if seed.get("seed_status") not in VALID_CANDIDATE_SEED_STATUSES:
                    continue
                seed_url = str(seed.get("seed_url") or "")
                if not seed_url:
                    continue
                seed_type = str(seed.get("seed_type") or "")
                host = _host(seed_url)
                if source == "official-disclosure-home" and "disclosure" not in host:
                    continue
                if source == "issuer-official-site" and host in {"e-disclosure.ru", "disclosure.ru", "moex.com", "moex.ru"}:
                    continue
                if source == "official-seed-pack" or seed_type:
                    urls.append(seed_url)
    result: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = _normalize_candidate_url(raw_url)
        if not url or url in seen:
            continue
        classification = _classify_candidate_url(
            url,
            allowed_domains=allowed_domains,
            blocked_hints=blocked_hints,
            allow_unknown_source=False,
        )
        if classification["status"] != "official":
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "candidate_source_url": url,
                    "message": "operator seed candidate source skipped because it is not allowlisted official source",
                }
            )
            continue
        seen.add(url)
        result.append(url)
    return result


def build_operator_seed_candidate_from_url(
    issuer: dict[str, Any],
    candidate_url: str,
    candidate_title: str,
    source_url: str,
    *,
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    discovery_method: str,
    page_text: str,
) -> dict[str, Any]:
    seed_url = _normalize_candidate_url(candidate_url)
    seed_type = _normalize_seed_type(issuer.get("seed_type"), seed_url)
    classification = _classify_candidate_url(
        seed_url,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
        allow_unknown_source=args.allow_unknown_source,
    )
    score_result = score_operator_seed_candidate(
        issuer,
        seed_type,
        seed_url,
        candidate_title,
        source_url,
        args=args,
        domain_status=classification["status"],
        page_text=page_text,
    )
    score = int(score_result["final_score"])
    raw_score = int(score_result["raw_score"])
    score_reasons = list(score_result["score_reasons"])
    negative_reasons = list(score_result["negative_reasons"])
    filter_reasons = list(score_result["filter_reasons"])
    strong_match = bool(score_result["strong_match"])
    official = classification["status"] == "official"
    unknown = classification["status"] == "unknown_warning"
    if classification["status"] == "blocked":
        candidate_status = "blocked_candidate"
        operator_review_status = "needs_operator_review"
        confidence = "low"
    elif classification["status"] == "unknown_error":
        candidate_status = "invalid_candidate"
        operator_review_status = "needs_operator_review"
        confidence = "low"
    elif unknown:
        candidate_status = "needs_operator_review"
        operator_review_status = "needs_operator_review"
        confidence = "low"
    elif (
        official
        and strong_match
        and not _url_is_pdf(seed_url)
        and not score_result["wrong_seed_type"]
        and not score_result["noise_without_relevance"]
        and score >= args.operator_seed_candidate_auto_review_threshold
    ):
        candidate_status = "operator_reviewed"
        operator_review_status = "operator_reviewed"
        confidence = "high"
    elif score >= args.operator_seed_candidate_min_score:
        candidate_status = "needs_operator_review"
        operator_review_status = "needs_operator_review"
        confidence = "medium"
    else:
        candidate_status = "not_found"
        operator_review_status = "operator_to_fill"
        confidence = "low"
    candidate = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name") or "",
        "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
        "inn": issuer.get("inn") or "",
        "ogrn": issuer.get("ogrn") or "",
        "seed_type": seed_type,
        "candidate_seed_url": seed_url,
        "candidate_title": candidate_title,
        "candidate_source_url": source_url,
        "candidate_status": candidate_status,
        "operator_review_status": operator_review_status,
        "candidate_rank": None,
        "candidate_score": score,
        "raw_score": raw_score,
        "final_score": score,
        "candidate_confidence": confidence,
        "filter_status": "kept",
        "filter_reasons": filter_reasons,
        "discovery_method": discovery_method,
        "score_reasons": score_reasons,
        "negative_reasons": negative_reasons,
        "notes": classification["message"],
    }
    if args.operator_seed_candidate_probe_urls and seed_url and candidate_status not in {"blocked_candidate", "invalid_candidate"}:
        probe = _fetch_candidate_page(
            seed_url,
            timeout_seconds=args.operator_seed_candidate_fetch_timeout_seconds,
            max_bytes=args.operator_seed_candidate_max_response_bytes,
            user_agent=args.operator_seed_candidate_user_agent,
        )
        candidate["probe_status"] = probe.get("status")
        candidate["probe_http_status"] = probe.get("http_status")
        candidate["probe_content_type"] = probe.get("content_type") or ""
        if probe.get("status") != "ok":
            candidate["negative_reasons"] = [*negative_reasons, "candidate probe failed"]
            candidate["filter_reasons"] = [*filter_reasons, "candidate probe failed"]
    _strip_financial_values(candidate)
    return candidate


def score_operator_seed_candidate(
    issuer: dict[str, Any],
    seed_type: str,
    candidate_url: str,
    candidate_title: str,
    source_url: str,
    *,
    args: argparse.Namespace,
    domain_status: str,
    page_text: str,
) -> dict[str, Any]:
    host = _host(candidate_url)
    source_host = _host(source_url)
    parsed = urllib.parse.urlparse(candidate_url)
    path = urllib.parse.unquote(parsed.path or "/").casefold()
    path_words = re.sub(r"[_\-/]+", " ", path)
    title_text = str(candidate_title or "").casefold()
    text = f"{candidate_url} {candidate_title}".casefold()
    signal_text = f"{path} {path_words} {title_text}"
    score = 0
    reasons: list[str] = []
    negatives: list[str] = []
    filter_reasons: list[str] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    def subtract(points: int, reason: str) -> None:
        nonlocal score
        score -= points
        negatives.append(reason)

    if domain_status == "official":
        add(10, "official allowlisted domain")
    elif domain_status == "unknown_warning":
        subtract(15, "unknown domain review only")
    if seed_type.startswith("official_disclosure") and "disclosure" in host:
        add(20, "official disclosure domain for disclosure seed")
    if seed_type.startswith("issuer_") and host and host == source_host and "disclosure" not in host:
        add(15, "issuer official domain for issuer seed")

    names = [
        str(issuer.get("company_name") or ""),
        str(issuer.get("canonical_company_name") or ""),
        str(issuer.get("legal_name") or ""),
    ]
    name_tokens = [
        token
        for name in names
        for token in re.split(r"[\s\"'.,()/\\_-]+", name.casefold())
        if len(token) >= 3
    ]
    matched_name = any(token in text for token in name_tokens)
    inn = str(issuer.get("inn") or "").strip()
    ogrn = str(issuer.get("ogrn") or "").strip()
    matched_inn = bool(inn and inn in text)
    matched_ogrn = bool(ogrn and ogrn in text)
    if matched_name:
        add(18, "company name match")
    if matched_inn:
        add(35, "INN match")
    if matched_ogrn:
        add(25, "OGRN match")
    if not (matched_name or matched_inn or matched_ogrn):
        subtract(20, "no company signal")

    title_signal = _contains_any(title_text, OPERATOR_SEED_RELEVANCE_TERMS)
    path_signal = _contains_any(f"{path} {path_words}", OPERATOR_SEED_RELEVANCE_TERMS)
    seed_terms = OPERATOR_SEED_TYPE_RELEVANCE_TERMS.get(seed_type, ())
    seed_title_signal = _contains_any(title_text, seed_terms)
    seed_path_signal = _contains_any(f"{path} {path_words}", seed_terms)
    if title_signal:
        add(20, "title seed relevance signal")
    if path_signal:
        add(25, "path seed relevance signal")
    if seed_title_signal:
        add(30, f"{seed_type} title signal")
    if seed_path_signal:
        add(45, f"{seed_type} path signal")
    if _contains_any(signal_text, ("financial-results", "financial results", "финансовые результаты")):
        reasons.append("financial-results signal")
    if _contains_any(signal_text, ("information-disclosure", "information disclosure", "disclosure", "раскрытие")):
        reasons.append("disclosure signal")
    if _contains_any(path, ("company.aspx", "emitent", "profile")):
        add(20, "company profile URL shape")
    if _contains_any(path, ("reports", "reporting", "messages", "disclosure", "events", "financial-results")):
        add(20, "reports/messages URL shape")

    has_relevance_signal = bool(title_signal or path_signal or seed_title_signal or seed_path_signal)
    raw_score = max(score, 0)

    if _looks_like_generic_seed_page(candidate_url):
        subtract(30, "generic homepage only")
        filter_reasons.append("generic homepage only")
    if _contains_any(text, ("search", "google", "yandex", "news", "press", "blog", "forum", "social", "telegram")):
        subtract(60, "search/news/blog/forum/social URL")
        filter_reasons.append("search/news/blog/forum/social URL")
    noise_without_relevance = bool(args.operator_seed_candidate_noise_filter and _contains_any(signal_text, OPERATOR_SEED_NOISE_TERMS) and not has_relevance_signal)
    if noise_without_relevance:
        subtract(90, "noise navigation page without seed relevance")
        filter_reasons.append("noise navigation page without seed relevance")
    if args.operator_seed_candidate_min_title_signal and not (title_signal or seed_title_signal or matched_name or matched_inn or matched_ogrn):
        subtract(20, "missing title seed relevance")
        filter_reasons.append("missing title seed relevance")
    if args.operator_seed_candidate_min_path_signal and not (path_signal or seed_path_signal):
        subtract(20, "missing path seed relevance")
        filter_reasons.append("missing path seed relevance")
    wrong_seed_type = False
    if seed_type == "official_disclosure_profile" and _url_is_pdf(candidate_url):
        wrong_seed_type = True
        subtract(100, "exact PDF document is not profile seed")
        filter_reasons.append("exact PDF document is not profile seed")
    if seed_type == "official_disclosure_profile" and _contains_any(path, ("annual", "ifrs", "financial-statement")):
        wrong_seed_type = True
        subtract(40, "report document path is not profile seed")
        filter_reasons.append("report document path is not profile seed")
    if seed_type == "issuer_reports" and _url_is_pdf(candidate_url):
        subtract(45, "exact PDF needs operator review; seed page preferred")
        filter_reasons.append("exact PDF needs operator review; seed page preferred")

    return {
        "raw_score": raw_score,
        "final_score": max(score, 0),
        "score_reasons": reasons,
        "negative_reasons": negatives,
        "filter_reasons": filter_reasons,
        "strong_match": bool(matched_name or matched_inn or matched_ogrn),
        "has_relevance_signal": has_relevance_signal,
        "has_title_signal": bool(title_signal or seed_title_signal),
        "has_path_signal": bool(path_signal or seed_path_signal),
        "noise_without_relevance": noise_without_relevance,
        "wrong_seed_type": wrong_seed_type,
    }


def _select_top_operator_seed_candidates(
    candidates: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prepared = [copy.deepcopy(candidate) for candidate in candidates if candidate.get("candidate_seed_url")]
    stats = {
        "candidate_count_before_filter": len(prepared),
        "candidate_count_after_filter": 0,
        "filtered_candidate_count": 0,
        "filtered_noise_count": 0,
        "filtered_low_score_count": 0,
        "filtered_duplicate_count": 0,
        "top_ranked_candidate_count": 0,
        "invalid_candidate_count": 0,
        "blocked_candidate_count": 0,
    }
    for candidate in prepared:
        _apply_operator_seed_candidate_filter(candidate, args=args)

    best_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in sorted(prepared, key=_operator_seed_candidate_sort_key, reverse=True):
        if candidate.get("filter_status") not in {"kept", "filtered_low_score"}:
            continue
        key = _operator_seed_candidate_dedupe_key(candidate, args=args)
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = candidate
            continue
        _mark_operator_seed_candidate_filtered(
            candidate,
            "filtered_duplicate",
            "duplicate normalized candidate URL",
        )

    kept_candidates = [item for item in prepared if item.get("filter_status") == "kept"]
    _apply_operator_seed_candidate_top_n(kept_candidates, args=args)
    kept_candidates = [item for item in prepared if item.get("filter_status") == "kept"]
    _assign_operator_seed_candidate_ranks(kept_candidates)

    stats["candidate_count_after_filter"] = len(kept_candidates)
    stats["top_ranked_candidate_count"] = len(kept_candidates)
    filtered = [item for item in prepared if item.get("filter_status") != "kept"]
    stats["filtered_candidate_count"] = len(filtered)
    stats["filtered_noise_count"] = sum(1 for item in filtered if item.get("filter_status") == "filtered_noise")
    stats["filtered_low_score_count"] = sum(1 for item in filtered if item.get("filter_status") == "filtered_low_score")
    stats["filtered_duplicate_count"] = sum(1 for item in filtered if item.get("filter_status") == "filtered_duplicate")
    stats["invalid_candidate_count"] = sum(1 for item in prepared if item.get("candidate_status") == "invalid_candidate")
    stats["blocked_candidate_count"] = sum(1 for item in prepared if item.get("candidate_status") == "blocked_candidate")

    output = kept_candidates
    if args.operator_seed_candidate_include_filtered:
        output = [*kept_candidates, *filtered]
    return sorted(output, key=_operator_seed_candidate_output_sort_key), stats


def _apply_operator_seed_candidate_filter(candidate: dict[str, Any], *, args: argparse.Namespace) -> None:
    score = int(candidate.get("final_score") or candidate.get("candidate_score") or 0)
    negative_reasons = [str(item) for item in candidate.get("negative_reasons") or []]
    filter_reasons = [str(item) for item in candidate.get("filter_reasons") or []]
    status = str(candidate.get("candidate_status") or "")
    if status == "blocked_candidate":
        _mark_operator_seed_candidate_filtered(candidate, "filtered_blocked", "blocked official seed candidate URL")
        return
    if status == "invalid_candidate":
        _mark_operator_seed_candidate_filtered(candidate, "filtered_low_score", "invalid or unknown official seed candidate URL")
        return
    if any("not profile seed" in reason for reason in negative_reasons + filter_reasons):
        _mark_operator_seed_candidate_filtered(candidate, "filtered_wrong_seed_type", "candidate URL does not match requested seed type")
        return
    if any("noise navigation page" in reason for reason in negative_reasons + filter_reasons):
        _mark_operator_seed_candidate_filtered(candidate, "filtered_noise", "noise navigation page")
        return
    if score < args.operator_seed_candidate_min_score:
        _mark_operator_seed_candidate_filtered(candidate, "filtered_low_score", "candidate score below minimum")
        return
    candidate["filter_status"] = "kept"
    candidate["filter_reasons"] = []


def _mark_operator_seed_candidate_filtered(candidate: dict[str, Any], status: str, reason: str) -> None:
    candidate["filter_status"] = status
    reasons = [str(item) for item in candidate.get("filter_reasons") or []]
    if reason not in reasons:
        reasons.append(reason)
    candidate["filter_reasons"] = reasons
    if status == "filtered_noise":
        candidate["candidate_status"] = "not_found"
        candidate["operator_review_status"] = "operator_to_fill"
    elif status == "filtered_low_score" and candidate.get("candidate_status") not in {"invalid_candidate", "blocked_candidate"}:
        candidate["candidate_status"] = "not_found"
        candidate["operator_review_status"] = "operator_to_fill"


def _operator_seed_candidate_dedupe_key(
    candidate: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> tuple[str, str, str]:
    normalized_url = _normalized_operator_seed_candidate_url(str(candidate.get("candidate_seed_url") or ""))
    if args.operator_seed_candidate_deduplicate_paths:
        parsed = urllib.parse.urlparse(normalized_url)
        path = (parsed.path or "/").rstrip("/") or "/"
        normalized_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return (
        str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
        str(candidate.get("seed_type") or ""),
        normalized_url,
    )


def _normalized_operator_seed_candidate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    scheme = parsed.scheme.casefold()
    host = parsed.netloc.casefold()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunparse((scheme, host, path or "/", "", parsed.query, ""))


def _apply_operator_seed_candidate_top_n(candidates: list[dict[str, Any]], *, args: argparse.Namespace) -> None:
    by_type: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
            str(candidate.get("seed_type") or ""),
        )
        by_type.setdefault(key, []).append(candidate)
    per_type_limit = max(int(args.operator_seed_candidate_top_n_per_type or 0), 0)
    for group in by_type.values():
        ranked = sorted(group, key=_operator_seed_candidate_sort_key, reverse=True)
        for index, candidate in enumerate(ranked, start=1):
            if per_type_limit and index > per_type_limit:
                _mark_operator_seed_candidate_filtered(candidate, "filtered_low_score", "outside top-N per seed type")

    by_issuer: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("filter_status") != "kept":
            continue
        company_id = str(candidate.get("company_id") or candidate.get("canonical_company_id") or "")
        by_issuer.setdefault(company_id, []).append(candidate)
    per_issuer_limit = max(int(args.operator_seed_candidate_top_n_per_issuer or 0), 0)
    for group in by_issuer.values():
        ranked = sorted(group, key=_operator_seed_candidate_sort_key, reverse=True)
        for index, candidate in enumerate(ranked, start=1):
            if per_issuer_limit and index > per_issuer_limit:
                _mark_operator_seed_candidate_filtered(candidate, "filtered_low_score", "outside top-N per issuer")


def _assign_operator_seed_candidate_ranks(candidates: list[dict[str, Any]]) -> None:
    by_type: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
            str(candidate.get("seed_type") or ""),
        )
        by_type.setdefault(key, []).append(candidate)
    for group in by_type.values():
        for rank, candidate in enumerate(sorted(group, key=_operator_seed_candidate_sort_key, reverse=True), start=1):
            candidate["candidate_rank"] = rank


def _operator_seed_candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    score = int(candidate.get("final_score") or candidate.get("candidate_score") or 0)
    reviewed_rank = 1 if candidate.get("operator_review_status") == "operator_reviewed" else 0
    exact_rank = _operator_seed_candidate_exact_type_rank(candidate)
    path = urllib.parse.urlparse(str(candidate.get("candidate_seed_url") or "")).path or "/"
    path_specificity = min(path.count("/"), 20)
    path_length_rank = -len(path)
    return (score, reviewed_rank, exact_rank, path_specificity, path_length_rank, str(candidate.get("candidate_seed_url") or ""))


def _operator_seed_candidate_exact_type_rank(candidate: dict[str, Any]) -> int:
    seed_type = str(candidate.get("seed_type") or "")
    text = f"{candidate.get('candidate_seed_url') or ''} {candidate.get('candidate_title') or ''}".casefold()
    terms = OPERATOR_SEED_TYPE_RELEVANCE_TERMS.get(seed_type, ())
    return 1 if _contains_any(text, terms) else 0


def _operator_seed_candidate_output_sort_key(candidate: dict[str, Any]) -> tuple[str, str, int, int, str]:
    company_id = str(candidate.get("company_id") or candidate.get("canonical_company_id") or "")
    seed_type = str(candidate.get("seed_type") or "")
    rank = int(candidate.get("candidate_rank") or 999999)
    kept = 0 if candidate.get("filter_status") == "kept" else 1
    return (company_id, seed_type, kept, rank, str(candidate.get("candidate_seed_url") or ""))


def _operator_seed_not_found_candidate(template: dict[str, Any], *, issuer: dict[str, Any]) -> dict[str, Any]:
    candidate = {
        "company_id": template.get("company_id") or issuer.get("company_id"),
        "company_name": template.get("company_name") or issuer.get("company_name") or "",
        "canonical_company_id": template.get("canonical_company_id") or issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": template.get("canonical_company_name") or issuer.get("canonical_company_name") or issuer.get("company_name") or "",
        "inn": template.get("inn") or issuer.get("inn") or "",
        "ogrn": template.get("ogrn") or issuer.get("ogrn") or "",
        "seed_type": _normalize_seed_type(template.get("seed_type")),
        "candidate_seed_url": "",
        "candidate_title": "",
        "candidate_source_url": template.get("source_context") or "",
        "candidate_status": "not_found",
        "operator_review_status": "operator_to_fill",
        "candidate_rank": None,
        "candidate_score": 0,
        "raw_score": 0,
        "final_score": 0,
        "candidate_confidence": "low",
        "filter_status": "kept",
        "filter_reasons": [],
        "discovery_method": "official_seed_anchor_scan",
        "score_reasons": [],
        "negative_reasons": ["missing official candidate"],
        "notes": "No official seed candidate found; operator must fill manually.",
    }
    _strip_financial_values(candidate)
    return candidate


def build_operator_seed_autofill(
    template_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    review_needed_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        if not candidate.get("candidate_seed_url"):
            continue
        if candidate.get("filter_status") != "kept":
            continue
        if _url_is_pdf(str(candidate.get("candidate_seed_url") or "")):
            continue
        if candidate.get("candidate_status") not in {"operator_reviewed", "needs_operator_review"}:
            continue
        key = (
            str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
            str(candidate.get("seed_type") or ""),
        )
        existing = best_by_key.get(key)
        if existing is None or _operator_seed_candidate_rank(candidate) > _operator_seed_candidate_rank(existing):
            best_by_key[key] = candidate
    review_needed_limit = max(int(args.operator_seed_candidate_max_autofill_review_needed or 0), 0)
    review_needed_candidates = sorted(
        [
            candidate
            for candidate in best_by_key.values()
            if candidate.get("operator_review_status") == "needs_operator_review"
        ],
        key=_operator_seed_candidate_sort_key,
        reverse=True,
    )
    for candidate in review_needed_candidates[:review_needed_limit]:
        review_needed_keys.add(
            (
                str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
                str(candidate.get("seed_type") or ""),
            )
        )
    autofill: list[dict[str, Any]] = []
    for row in template_rows:
        seed_type = _normalize_seed_type(row.get("seed_type"))
        key = (str(row.get("company_id") or row.get("canonical_company_id") or ""), seed_type)
        candidate = best_by_key.get(key)
        item = {field: row.get(field) or "" for field in OPERATOR_SEED_FIELDS}
        item["seed_type"] = seed_type
        if candidate is not None and (
            candidate.get("operator_review_status") == "operator_reviewed"
            or key in review_needed_keys
        ):
            item["seed_url"] = candidate.get("candidate_seed_url") or ""
            item["operator_review_status"] = candidate.get("operator_review_status") or "needs_operator_review"
            item["notes"] = candidate.get("notes") or candidate.get("candidate_title") or item.get("notes") or ""
        else:
            item["seed_url"] = ""
            item["operator_review_status"] = "operator_to_fill"
        _strip_financial_values(item)
        autofill.append(item)
    return autofill


def _operator_seed_candidate_rank(candidate: dict[str, Any]) -> tuple[int, int]:
    status_rank = 2 if candidate.get("operator_review_status") == "operator_reviewed" else 1
    return (status_rank, int(candidate.get("candidate_score") or 0))


def _operator_seed_required_types(value: str | None, *, args: argparse.Namespace) -> list[str]:
    raw_values = _split_cli_list(value) or list(OPERATOR_SEED_DEFAULT_REQUIRED_TYPES)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        canonical = SEED_TYPE_ALIASES.get(item.strip().casefold().replace("-", "_"))
        if canonical and canonical in OPERATOR_SEED_ALLOWED_TYPES and canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    if not normalized:
        normalized = list(OPERATOR_SEED_DEFAULT_REQUIRED_TYPES)

    def sort_key(seed_type: str) -> tuple[int, int]:
        original_index = normalized.index(seed_type)
        if args.operator_seed_prefer_disclosure and seed_type.startswith("official_disclosure"):
            return (0, original_index)
        if args.operator_seed_prefer_issuer_site and seed_type.startswith("issuer_"):
            return (1, original_index)
        return (2, original_index)

    return sorted(normalized, key=sort_key)


def _operator_seed_required_issuers(
    args: argparse.Namespace,
    *,
    input_documents: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    financial_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = _split_cli_list(args.required_company_ids)
    names = _split_cli_list(args.required_company_names)
    sources = [*input_documents, *seed_issuers, *financial_rows]
    required: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(item: dict[str, Any]) -> None:
        key = (str(item.get("company_id") or ""), _normalize_name(str(item.get("company_name") or "")))
        if key in seen:
            return
        seen.add(key)
        required.append(item)

    for index, company_id in enumerate(ids):
        matched = _first_matching_company_id(sources, company_id)
        add(
            {
                "company_id": _maybe_int(company_id),
                "company_name": (
                    names[index]
                    if index < len(names)
                    else matched.get("company_name") or matched.get("canonical_company_name") or ""
                ),
            }
        )
    for name in names[len(ids):]:
        add({"company_id": None, "company_name": name})
    if required:
        return required
    for source in sources:
        add(
            {
                "company_id": _maybe_int(_document_company_key(source)),
                "company_name": source.get("company_name") or source.get("canonical_company_name") or "",
            }
        )
    return required


def _operator_seed_issuer_base(
    required: dict[str, Any],
    *,
    input_documents: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    financial_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        *_items_matching_required(input_documents, required),
        *_items_matching_required(seed_issuers, required),
        *_items_matching_required(financial_rows, required),
    ]
    chosen = candidates[0] if candidates else required
    identity = (_items_matching_required(financial_rows, required) or _items_matching_required(seed_issuers, required) or [{}])[0]
    return {
        "company_id": chosen.get("company_id") or required.get("company_id"),
        "company_name": chosen.get("company_name") or required.get("company_name") or "",
        "canonical_company_id": chosen.get("canonical_company_id") or chosen.get("company_id") or required.get("company_id"),
        "canonical_company_name": chosen.get("canonical_company_name") or chosen.get("company_name") or required.get("company_name") or "",
        "inn": identity.get("inn") or "",
        "ogrn": identity.get("ogrn") or "",
        "legal_name": identity.get("legal_name") or "",
    }


def _operator_seed_source_context(
    issuer: dict[str, Any],
    *,
    seed_issuers: list[dict[str, Any]],
    input_documents: list[dict[str, Any]],
) -> str:
    preferred_urls: list[str] = []
    fallback_urls: list[str] = []
    for seed_issuer in _items_matching_required(seed_issuers, issuer):
        for seed in seed_issuer.get("official_seeds") or []:
            url = str(seed.get("seed_url") or "").strip()
            if not url:
                continue
            if seed.get("seed_status") == "valid_seed" and seed.get("source") != "generated_official_path":
                preferred_urls.append(url)
            elif seed.get("seed_status") in {"valid_seed", "needs_operator_review"}:
                fallback_urls.append(url)
    if not preferred_urls:
        for document in _items_matching_required(input_documents, issuer):
            fallback_urls.extend(_split_source_context_urls(str(document.get("source_url_context") or "")))
    return _source_url_context_from_strings((preferred_urls or fallback_urls)[:6])


def _operator_seed_template_notes(seed_type: str) -> str:
    notes = {
        "official_disclosure_profile": (
            "Paste exact official disclosure company profile URL. Do not paste search results, "
            "news, blogs, or financial values."
        ),
        "official_disclosure_reports": (
            "Paste exact official disclosure reports/messages page URL. Do not paste report values."
        ),
        "issuer_reports": (
            "Paste exact official issuer annual reports/reporting page URL. Do not paste financial values."
        ),
        "issuer_investor_relations": (
            "Paste exact official issuer investor relations page URL. Do not paste search results or figures."
        ),
    }
    return notes.get(seed_type, "Paste reviewed official seed page URL only. Do not paste financial values.")


def _operator_seed_review_candidate_items(
    candidates: list[dict[str, Any]],
    *,
    required_issuers: list[dict[str, Any]],
    args: argparse.Namespace,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    review_items: list[dict[str, Any]] = []
    for candidate in candidates:
        forbidden_fields = _forbidden_seed_metadata_fields(candidate)
        if forbidden_fields:
            errors.append(
                {
                    "company_id": candidate.get("company_id"),
                    "candidate_seed_url": candidate.get("candidate_seed_url") or "",
                    "message": "financial values are forbidden in operator seed review metadata",
                    "fields": forbidden_fields,
                }
            )
            continue
        if not _candidate_matches_required_any(candidate, required_issuers):
            continue
        if candidate.get("filter_status") not in {None, "", "kept"}:
            continue
        if candidate.get("candidate_status") not in {"needs_operator_review", "operator_reviewed"}:
            continue
        if not candidate.get("candidate_seed_url"):
            continue
        review_items.append(_operator_seed_review_item_from_candidate(candidate, args=args))
    review_items = _dedupe_operator_seed_review_items(review_items)
    review_items = _limit_operator_seed_review_items(review_items, args=args)
    return review_items


def _operator_seed_review_item_from_candidate(candidate: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    score = int(candidate.get("final_score") or candidate.get("candidate_score") or 0)
    auto_approve = bool(
        args.operator_seed_review_auto_approve_reviewed
        and candidate.get("operator_review_status") == "operator_reviewed"
        and score >= int(args.operator_seed_review_auto_approve_threshold or 0)
    )
    operator_decision = "approve" if auto_approve else str(args.operator_seed_review_default_decision or "pending")
    operator_review_status = "operator_reviewed" if auto_approve else "needs_operator_review"
    review_status = "operator_reviewed" if auto_approve else args.operator_seed_review_status
    item = {
        "company_id": candidate.get("company_id"),
        "company_name": candidate.get("company_name") or "",
        "canonical_company_id": candidate.get("canonical_company_id") or candidate.get("company_id"),
        "canonical_company_name": candidate.get("canonical_company_name") or candidate.get("company_name") or "",
        "inn": candidate.get("inn") or "",
        "ogrn": candidate.get("ogrn") or "",
        "seed_type": _normalize_seed_type(candidate.get("seed_type"), str(candidate.get("candidate_seed_url") or "")),
        "candidate_seed_url": candidate.get("candidate_seed_url") or "",
        "candidate_title": candidate.get("candidate_title") or "",
        "candidate_source_url": candidate.get("candidate_source_url") or "",
        "candidate_rank": candidate.get("candidate_rank"),
        "candidate_score": score,
        "candidate_confidence": candidate.get("candidate_confidence") or "low",
        "candidate_status": candidate.get("candidate_status") or "",
        "operator_decision": operator_decision,
        "operator_review_status": operator_review_status,
        "review_status": review_status,
        "review_notes": "",
        "suggested_action": "approve_if_official_seed_page",
        "promotion_status": "not_promoted",
        "score_reasons": candidate.get("score_reasons") or [],
        "negative_reasons": candidate.get("negative_reasons") or [],
        "notes": "Review official seed URL. Do not approve if this is not an issuer/disclosure/reporting seed page.",
    }
    _strip_financial_values(item)
    return item


def _operator_seed_review_missing_items(
    operator_seed_rows: list[dict[str, Any]],
    *,
    review_items: list[dict[str, Any]],
    required_issuers: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    existing = {
        (
            str(item.get("company_id") or item.get("canonical_company_id") or ""),
            str(item.get("seed_type") or ""),
        )
        for item in review_items
    }
    missing: list[dict[str, Any]] = []
    for row in operator_seed_rows:
        if not _candidate_matches_required_any(row, required_issuers):
            continue
        seed_type = _normalize_seed_type(row.get("seed_type"))
        key = (str(row.get("company_id") or row.get("canonical_company_id") or ""), seed_type)
        if key in existing and not args.operator_seed_review_include_not_found:
            continue
        if key in existing:
            continue
        item = {
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name") or "",
            "canonical_company_id": row.get("canonical_company_id") or row.get("company_id"),
            "canonical_company_name": row.get("canonical_company_name") or row.get("company_name") or "",
            "inn": row.get("inn") or "",
            "ogrn": row.get("ogrn") or "",
            "seed_type": seed_type,
            "candidate_seed_url": "",
            "candidate_title": "",
            "candidate_source_url": row.get("source_context") or "",
            "candidate_rank": None,
            "candidate_score": 0,
            "candidate_confidence": "low",
            "candidate_status": "not_found",
            "operator_decision": "pending",
            "operator_review_status": "operator_to_fill",
            "review_status": "missing_candidate",
            "review_notes": "",
            "suggested_action": "operator_to_find_official_seed",
            "promotion_status": "not_promoted",
            "score_reasons": [],
            "negative_reasons": ["missing official seed candidate"],
            "notes": "No official seed candidate found; operator must find an official seed URL manually.",
        }
        _strip_financial_values(item)
        missing.append(item)
    return missing


def _dedupe_operator_seed_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (
            str(item.get("company_id") or item.get("canonical_company_id") or ""),
            str(item.get("seed_type") or ""),
            _normalize_candidate_url(str(item.get("candidate_seed_url") or "")),
        )
        existing = by_key.get(key)
        if existing is None or _operator_seed_review_sort_key(item) > _operator_seed_review_sort_key(existing):
            by_key[key] = item
    return sorted(by_key.values(), key=_operator_seed_review_sort_key, reverse=True)


def _limit_operator_seed_review_items(items: list[dict[str, Any]], *, args: argparse.Namespace) -> list[dict[str, Any]]:
    per_type: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        key = (
            str(item.get("company_id") or item.get("canonical_company_id") or ""),
            str(item.get("seed_type") or ""),
        )
        per_type.setdefault(key, []).append(item)
    kept_ids: set[int] = set()
    type_limit = max(int(args.operator_seed_review_top_n_per_type or 0), 0)
    for group in per_type.values():
        ranked = sorted(group, key=_operator_seed_review_sort_key, reverse=True)
        for item in ranked[: type_limit or None]:
            kept_ids.add(id(item))
    per_issuer: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if id(item) not in kept_ids:
            continue
        company_id = str(item.get("company_id") or item.get("canonical_company_id") or "")
        per_issuer.setdefault(company_id, []).append(item)
    final_ids: set[int] = set()
    issuer_limit = max(int(args.operator_seed_review_top_n_per_issuer or 0), 0)
    for group in per_issuer.values():
        ranked = sorted(group, key=_operator_seed_review_sort_key, reverse=True)
        for item in ranked[: issuer_limit or None]:
            final_ids.add(id(item))
    return [item for item in items if id(item) in final_ids]


def _operator_seed_review_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    reviewed_rank = 1 if item.get("candidate_status") == "operator_reviewed" else 0
    rank = int(item.get("candidate_rank") or 999999)
    return (
        int(item.get("candidate_score") or 0),
        reviewed_rank,
        -rank,
        str(item.get("candidate_seed_url") or ""),
    )


def _candidate_matches_required_any(item: dict[str, Any], required_issuers: list[dict[str, Any]]) -> bool:
    return not required_issuers or any(_matches_required_issuer(item, required) for required in required_issuers)


def _operator_seed_promotion_result(item: dict[str, Any], *, row_index: int, args: argparse.Namespace) -> dict[str, Any]:
    decision = str(item.get("operator_decision") or "pending").strip().casefold()
    seed_url = _normalize_candidate_url(str(item.get("candidate_seed_url") or item.get("seed_url") or ""))
    seed_type = _normalize_seed_type(item.get("seed_type"), seed_url)
    base = {
        "row_index": row_index,
        "company_id": item.get("company_id"),
        "company_name": item.get("company_name") or "",
        "canonical_company_id": item.get("canonical_company_id") or item.get("company_id"),
        "canonical_company_name": item.get("canonical_company_name") or item.get("company_name") or "",
        "inn": item.get("inn") or "",
        "ogrn": item.get("ogrn") or "",
        "seed_type": seed_type,
        "candidate_seed_url": seed_url,
        "candidate_title": item.get("candidate_title") or "",
        "candidate_source_url": item.get("candidate_source_url") or "",
        "operator_decision": decision,
        "review_status": item.get("review_status") or "",
        "promotion_status": "not_promoted",
        "promotion_reason": "",
        "warnings": [],
        "errors": [],
    }
    errors: list[dict[str, Any]] = []
    if decision not in OPERATOR_SEED_REVIEW_DECISIONS:
        errors.append({**base, "message": "operator_decision is not supported"})
    forbidden_fields = _forbidden_seed_metadata_fields(item)
    if forbidden_fields:
        errors.append({**base, "message": "financial values are forbidden in operator seed review metadata", "fields": forbidden_fields})
    if decision != "approve":
        if decision == "needs_more_review" and args.operator_seed_promotion_allow_needs_review and seed_url:
            review_seed = _operator_seed_review_item_to_seed(item, seed_url=seed_url, seed_type=seed_type, reviewed=False)
            base["promotion_status"] = "needs_more_review"
            base["promotion_reason"] = "review item retained as needs_operator_review"
            base["seed"] = review_seed
        elif decision == "reject" and args.operator_seed_promotion_include_rejected:
            base["promotion_reason"] = "operator rejected seed candidate"
        else:
            base["promotion_reason"] = f"operator decision {decision or 'pending'} is not promoted"
        if errors:
            base["promotion_status"] = "invalid"
            base["errors"] = errors
        _strip_financial_values(base)
        return base

    if not seed_url:
        errors.append({**base, "message": "approve requires candidate_seed_url"})
    if item.get("candidate_status") == "not_found" or item.get("review_status") == "missing_candidate":
        errors.append({**base, "message": "cannot approve a not_found review row"})
    if seed_type not in OPERATOR_SEED_ALLOWED_TYPES:
        errors.append({**base, "message": "seed_type is not allowed"})
    classification = _classify_candidate_url(
        seed_url,
        allowed_domains=_seed_allowed_domains(args),
        blocked_hints=_seed_blocked_hints(args),
        allow_unknown_source=False,
    ) if seed_url else {"status": "unknown_error", "message": "seed URL is missing"}
    if classification["status"] != "official":
        errors.append({**base, "message": classification["message"]})
    if errors:
        base["promotion_status"] = "invalid"
        base["errors"] = errors
        _strip_financial_values(base)
        return base
    seed = _operator_seed_review_item_to_seed(item, seed_url=seed_url, seed_type=seed_type, reviewed=True)
    base["promotion_status"] = "promoted"
    base["promotion_reason"] = "approved official seed candidate promoted"
    base["seed"] = seed
    _strip_financial_values(base)
    return base


def _operator_seed_review_item_to_seed(
    item: dict[str, Any],
    *,
    seed_url: str,
    seed_type: str,
    reviewed: bool,
) -> dict[str, Any]:
    seed = {
        "company_id": item.get("company_id"),
        "company_name": item.get("company_name") or "",
        "canonical_company_id": item.get("canonical_company_id") or item.get("company_id"),
        "canonical_company_name": item.get("canonical_company_name") or item.get("company_name") or "",
        "inn": item.get("inn") or "",
        "ogrn": item.get("ogrn") or "",
        "seed_type": seed_type,
        "seed_url": seed_url,
        "operator_review_status": "operator_reviewed" if reviewed else "needs_operator_review",
        "source_context": item.get("candidate_source_url") or "",
        "notes": "Promoted from Task 107 operator review." if reviewed else "Retained from Task 107 operator review for more review.",
    }
    _strip_financial_values(seed)
    return seed


def _dedupe_promoted_operator_seed_rows(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for seed in seeds:
        key = (
            str(seed.get("company_id") or seed.get("canonical_company_id") or ""),
            str(seed.get("seed_type") or ""),
            _normalize_candidate_url(str(seed.get("seed_url") or "")),
        )
        existing = by_key.get(key)
        if existing is None or _operator_seed_candidate_rank({"operator_review_status": seed.get("operator_review_status"), "candidate_score": 1}) > _operator_seed_candidate_rank({"operator_review_status": existing.get("operator_review_status"), "candidate_score": 1}):
            by_key[key] = seed
    return list(by_key.values())


def _apply_promotions_to_operator_seed_rows(
    template_rows: list[dict[str, Any]],
    promoted_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = [{field: row.get(field) or "" for field in OPERATOR_SEED_FIELDS} for row in template_rows]
    used_indexes: set[int] = set()
    for seed in promoted_rows:
        target_index = None
        for index, row in enumerate(output):
            if index in used_indexes:
                continue
            if (
                str(row.get("company_id") or row.get("canonical_company_id") or "")
                == str(seed.get("company_id") or seed.get("canonical_company_id") or "")
                and _normalize_seed_type(row.get("seed_type")) == _normalize_seed_type(seed.get("seed_type"))
            ):
                target_index = index
                break
        item = {field: seed.get(field) or "" for field in OPERATOR_SEED_FIELDS}
        if target_index is None:
            output.append(item)
        else:
            output[target_index] = {**output[target_index], **item}
            used_indexes.add(target_index)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in output:
        key = (
            str(row.get("company_id") or row.get("canonical_company_id") or ""),
            str(row.get("seed_type") or ""),
            _normalize_candidate_url(str(row.get("seed_url") or "")),
        )
        if key in seen:
            continue
        seen.add(key)
        _strip_financial_values(row)
        deduped.append(row)
    return deduped


def _forbidden_seed_metadata_fields(item: dict[str, Any]) -> list[str]:
    fields = [
        field
        for field in FORBIDDEN_SEED_METADATA_FIELDS
        if item.get(field) not in (None, "")
    ]
    if item.get("values") not in (None, "", []):
        fields.append("values")
    if item.get("field_evidence") not in (None, "", []):
        fields.append("field_evidence")
    return fields


def _build_operator_seed_validation_report(
    args: argparse.Namespace,
    *,
    seeds: list[dict[str, Any]],
    load_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = list(load_errors)
    required_issuers = _operator_seed_required_issuers(
        args,
        input_documents=seeds,
        seed_issuers=[],
        financial_rows=[],
    )
    duplicate_keys = _operator_seed_duplicate_keys(seeds)
    results: list[dict[str, Any]] = []
    if not load_errors:
        for index, seed in enumerate(seeds, start=1):
            result = validate_operator_seed_item(
                seed,
                row_index=index,
                args=args,
                duplicate_keys=duplicate_keys,
            )
            results.append(result)
            warnings.extend(result.get("warnings") or [])
            errors.extend(result.get("errors") or [])
        required_types = set(_operator_seed_required_types(args.operator_seed_required_types, args=args))
        for required in required_issuers:
            issuer_results = _items_matching_required(results, required)
            for seed_type in required_types:
                if not any(item.get("seed_type") == seed_type for item in issuer_results):
                    warnings.append(
                        {
                            "company_id": required.get("company_id"),
                            "company_name": required.get("company_name"),
                            "seed_type": seed_type,
                            "message": "required operator seed type is missing",
                        }
                    )

    valid_count = sum(1 for item in results if item.get("seed_status") == "valid_seed")
    review_count = sum(1 for item in results if item.get("seed_status") == "needs_operator_review")
    invalid_count = sum(1 for item in results if item.get("seed_status") in {"invalid_seed", "blocked_seed"})
    blocked_count = sum(1 for item in results if item.get("seed_status") == "blocked_seed")
    status = "failed" if errors else "warning" if warnings or review_count else "passed"
    return {
        "status": status,
        "mode": "operator-seed-validate",
        "issuer_count": len(required_issuers),
        "seed_count": len(results),
        "valid_seed_count": valid_count,
        "needs_operator_review_count": review_count,
        "invalid_seed_count": invalid_count,
        "blocked_seed_count": blocked_count,
        "seeds": results,
        "warnings": warnings,
        "errors": errors,
        "operator_seed_input": _path_value(args.operator_seed_input),
        "next_steps": _next_steps("operator-seed-validate", status),
        **SAFETY_FLAGS,
    }


def validate_operator_seed_item(
    seed: dict[str, Any],
    *,
    row_index: int,
    args: argparse.Namespace,
    duplicate_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    seed_url = _normalize_candidate_url(str(seed.get("seed_url") or seed.get("url") or ""))
    raw_seed_type = str(seed.get("seed_type") or "").strip()
    seed_type = _normalize_seed_type(raw_seed_type, seed_url, str(seed.get("source_type") or ""))
    operator_review_status = str(seed.get("operator_review_status") or "").strip()
    issuer = {
        "company_id": seed.get("company_id"),
        "company_name": seed.get("company_name") or "",
        "canonical_company_id": seed.get("canonical_company_id") or seed.get("company_id"),
        "canonical_company_name": seed.get("canonical_company_name") or seed.get("company_name") or "",
    }
    allowed_domains = _seed_allowed_domains(args)
    blocked_hints = _seed_blocked_hints(args)
    validation_seed = {
        **seed,
        "seed_type": seed_type,
        "seed_url": seed_url,
        "source": "operator_seed",
        "reason": str(seed.get("notes") or "operator-provided official seed"),
    }
    validation = validate_official_seed_candidate(
        validation_seed,
        issuer=issuer,
        args=args,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
    )
    result = {
        "row_index": row_index,
        "company_id": seed.get("company_id"),
        "company_name": seed.get("company_name") or "",
        "canonical_company_id": seed.get("canonical_company_id") or seed.get("company_id"),
        "canonical_company_name": seed.get("canonical_company_name") or seed.get("company_name") or "",
        "inn": seed.get("inn") or "",
        "ogrn": seed.get("ogrn") or "",
        "seed_type": validation.get("seed_type") or seed_type,
        "seed_url": validation.get("seed_url") or seed_url,
        "seed_status": validation.get("seed_status") or "invalid_seed",
        "confidence": validation.get("confidence") or "low",
        "source": "operator_seed",
        "reason": validation.get("reason") or str(seed.get("notes") or "operator-provided official seed"),
        "operator_review_status": operator_review_status,
        "source_context": seed.get("source_context") or "",
        "notes": seed.get("notes") or "",
        "domain_status": "",
        "warnings": list(validation.get("warnings") or []),
        "errors": list(validation.get("errors") or []),
    }
    extra_errors: list[dict[str, Any]] = []
    extra_warnings: list[dict[str, Any]] = []
    base = {
        "row_index": row_index,
        "company_id": seed.get("company_id"),
        "company_name": seed.get("company_name") or "",
        "seed_type": result["seed_type"],
        "seed_url": result["seed_url"],
    }
    if seed.get("company_id") in (None, ""):
        extra_errors.append({**base, "message": "company_id is required"})
    if not raw_seed_type:
        extra_errors.append({**base, "message": "seed_type is required"})
    elif result["seed_type"] not in OPERATOR_SEED_ALLOWED_TYPES:
        extra_errors.append({**base, "message": "seed_type is not allowed"})
    if args.operator_seed_require_reviewed and operator_review_status not in DOCUMENT_INTAKE_REVIEWED_STATUSES:
        extra_errors.append({**base, "message": "operator_review_status must be reviewed or operator_reviewed"})
    if seed_url:
        classification = _classify_candidate_url(
            seed_url,
            allowed_domains=allowed_domains,
            blocked_hints=blocked_hints,
            allow_unknown_source=args.allow_unknown_source,
        )
        result["domain_status"] = classification["status"]
        inferred_type = _normalize_seed_type("", seed_url, str(seed.get("source_type") or ""))
        if _looks_like_generic_seed_page(seed_url):
            extra_warnings.append({**base, "message": "seed_url is official-like but looks like generic home page"})
        if inferred_type != result["seed_type"] and inferred_type not in {"issuer_home", "official_disclosure_home"}:
            extra_warnings.append({**base, "message": "seed_type does not match URL path"})
    key = (
        str(seed.get("company_id") or seed.get("canonical_company_id") or ""),
        str(result["seed_type"] or ""),
        str(result["seed_url"] or ""),
    )
    if key in duplicate_keys:
        extra_warnings.append({**base, "message": "duplicate seed for same company/type/url"})
    if _operator_seed_looks_generated(seed):
        extra_warnings.append({**base, "message": "seed looks generated/probable; operator must confirm exact page"})
    if seed.get("inn") in (None, "") or seed.get("ogrn") in (None, ""):
        extra_warnings.append({**base, "message": "INN/OGRN missing for operator seed"})
    if extra_errors and result["seed_status"] != "blocked_seed":
        result["seed_status"] = "invalid_seed"
        result["confidence"] = "low"
    result["warnings"].extend(extra_warnings)
    result["errors"].extend(extra_errors)
    _strip_financial_values(result)
    return result


def _operator_seed_duplicate_keys(seeds: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    counts: dict[tuple[str, str, str], int] = {}
    for seed in seeds:
        seed_url = _normalize_candidate_url(str(seed.get("seed_url") or seed.get("url") or ""))
        if not seed_url:
            continue
        key = (
            str(seed.get("company_id") or seed.get("canonical_company_id") or ""),
            _normalize_seed_type(seed.get("seed_type"), seed_url, str(seed.get("source_type") or "")),
            seed_url,
        )
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _operator_seed_looks_generated(seed: dict[str, Any]) -> bool:
    text = " ".join(
        str(seed.get(field) or "")
        for field in ("source", "reason", "notes", "discovery_method")
    ).casefold()
    return "generated" in text or "probable" in text


def _looks_like_generic_seed_page(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url))
    path = (parsed.path or "/").rstrip("/")
    if path in {"", "/"}:
        return True
    lower_path = path.casefold()
    return lower_path in {"/investors", "/investor", "/ir", "/reports", "/disclosure"}


def _operator_seed_merge_eligible(result: dict[str, Any], *, args: argparse.Namespace) -> bool:
    if result.get("errors"):
        return False
    if result.get("operator_review_status") not in DOCUMENT_INTAKE_REVIEWED_STATUSES:
        return False
    if result.get("seed_status") == "valid_seed":
        return True
    return bool(
        args.allow_unknown_source
        and result.get("seed_status") == "needs_operator_review"
        and result.get("domain_status") == "unknown_warning"
    )


def _find_or_create_seed_issuer(issuers: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    for issuer in issuers:
        if _matches_required_issuer(issuer, result):
            return issuer
    issuer = {
        "company_id": result.get("company_id"),
        "company_name": result.get("company_name") or "",
        "canonical_company_id": result.get("canonical_company_id") or result.get("company_id"),
        "canonical_company_name": result.get("canonical_company_name") or result.get("company_name") or "",
        "inn": result.get("inn") or "",
        "ogrn": result.get("ogrn") or "",
        "official_seeds": [],
        "warnings": [],
        "errors": [],
    }
    issuers.append(issuer)
    return issuer


def _operator_seed_result_to_official_seed(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "seed_type": result.get("seed_type") or "",
        "seed_url": result.get("seed_url") or "",
        "seed_status": result.get("seed_status") or "",
        "confidence": result.get("confidence") or "low",
        "source": "operator_seed",
        "reason": result.get("notes") or result.get("reason") or "operator-provided official seed",
        "operator_review_status": result.get("operator_review_status") or "",
        "source_context": result.get("source_context") or "",
        "probe_status": "",
        "http_status": None,
        "content_type": "",
        "warnings": result.get("warnings") or [],
        "errors": [],
    }


def _classify_candidate_url(
    url: str,
    *,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    allow_unknown_source: bool,
) -> dict[str, str]:
    text = url.casefold()
    host = _host(url)
    if not host:
        return {"status": "unknown_error", "message": "source URL host is missing"}
    if any(hint in text for hint in blocked_hints):
        return {"status": "blocked", "message": "blocked unofficial source domain"}
    if any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        return {"status": "official", "message": "recognized official-like source domain"}
    if allow_unknown_source:
        return {
            "status": "unknown_warning",
            "message": "source URL domain is not in the official allowlist; operator review required",
        }
    return {"status": "unknown_error", "message": "source URL domain is not in the official allowlist"}


def _normalized_seed_types(value: str | None) -> set[str]:
    raw_values = _split_cli_list(value)
    if not raw_values:
        return set(SEED_DEFAULT_TYPES)
    normalized: set[str] = set()
    for item in raw_values:
        canonical = SEED_TYPE_ALIASES.get(item.strip().casefold().replace("-", "_"))
        if canonical:
            normalized.add(canonical)
    return normalized or set(SEED_DEFAULT_TYPES)


def _normalize_seed_type(value: Any, url: str = "", source_type: str = "") -> str:
    raw = str(value or "").strip().casefold().replace("-", "_")
    if raw in SEED_TYPE_ALIASES:
        return SEED_TYPE_ALIASES[raw]
    source = str(source_type or "").casefold()
    host = _host(url)
    path = urllib.parse.urlparse(str(url)).path.casefold()
    if "disclosure" in host:
        if "company" in path or "issuer" in path or "emitent" in path:
            return "official_disclosure_profile"
        if "event" in path or "report" in path or "account" in path:
            return "official_disclosure_reports"
        return "official_disclosure_home"
    if source == "official_disclosure":
        return "official_disclosure_home"
    if "invest" in path or source == "issuer_investor_relations":
        return "issuer_investor_relations"
    if _contains_any(path, ("report", "annual", "ifrs", "otchet")):
        return "issuer_reports"
    return "issuer_home"


def _seed_allowed_domains(args: argparse.Namespace) -> set[str]:
    domains = {domain.casefold() for domain in OFFICIAL_SOURCE_DOMAIN_HINTS}
    domains.update(item.casefold() for item in _split_cli_list(args.seed_allowed_domains))
    return domains


def _seed_blocked_hints(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *BLOCKED_SOURCE_HINTS,
                *(item.casefold() for item in _split_cli_list(args.seed_blocked_domains)),
            ]
        )
    )


def _official_seed_issuer_base(
    required: dict[str, Any],
    *,
    input_documents: list[dict[str, Any]],
    source_issuers: list[dict[str, Any]],
    document_issuers: list[dict[str, Any]],
    financial_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        *_items_matching_required(input_documents, required),
        *_items_matching_required(source_issuers, required),
        *_items_matching_required(document_issuers, required),
        *_items_matching_required(financial_rows, required),
    ]
    chosen = candidates[0] if candidates else required
    identity = (_items_matching_required(financial_rows, required) or [{}])[0]
    return {
        "company_id": chosen.get("company_id") or required.get("company_id"),
        "company_name": chosen.get("company_name") or required.get("company_name") or "",
        "canonical_company_id": chosen.get("canonical_company_id") or chosen.get("company_id") or required.get("company_id"),
        "canonical_company_name": chosen.get("canonical_company_name") or chosen.get("company_name") or required.get("company_name") or "",
        "inn": identity.get("inn") or "",
        "ogrn": identity.get("ogrn") or "",
        "legal_name": identity.get("legal_name") or identity.get("canonical_company_name") or "",
    }


def collect_official_seed_candidates(
    issuer: dict[str, Any],
    *,
    input_documents: list[dict[str, Any]],
    source_issuers: list[dict[str, Any]],
    document_issuers: list[dict[str, Any]],
    operator_seeds: list[dict[str, Any]],
    seed_types: set[str],
) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []

    def add_seed(
        url: Any,
        *,
        seed_type: str = "",
        source_type: str = "",
        source: str,
        reason: str,
        operator_review_status: str = "",
        notes: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        seed_url = _normalize_candidate_url(str(url or ""))
        if not seed_url:
            return
        canonical_type = _normalize_seed_type(seed_type, seed_url, source_type)
        if source != "operator_seed" and canonical_type not in seed_types:
            return
        payload = dict(extra or {})
        payload.update(
            {
                "seed_type": canonical_type,
                "seed_url": seed_url,
                "source": source,
                "source_type": source_type,
                "reason": reason,
                "operator_review_status": operator_review_status,
                "notes": notes,
            }
        )
        seeds.append(payload)

    for document in _items_matching_required(input_documents, issuer):
        for url in _split_source_context_urls(str(document.get("source_url_context") or "")):
            add_seed(
                url,
                source_type=str(document.get("source_type") or ""),
                source="document_intake",
                reason="official seed from exact document intake source context",
            )
        if document.get("document_url"):
            add_seed(
                document.get("document_url"),
                source_type=str(document.get("source_type") or ""),
                source="document_intake",
                reason="existing document URL carried as navigation seed",
            )
    for source_issuer in _items_matching_required(source_issuers, issuer):
        for source_candidate in source_issuer.get("source_candidates") or []:
            add_seed(
                source_candidate.get("url") or source_candidate.get("source_url"),
                source_type=str(source_candidate.get("source_type") or ""),
                source="source_intake",
                reason="official source URL from discovered source intake",
            )
    for document_issuer in _items_matching_required(document_issuers, issuer):
        for document in document_issuer.get("document_candidates") or []:
            for key in ("source_url", "document_url"):
                add_seed(
                    document.get(key),
                    source_type=str(document.get("source_type") or ""),
                    source="document_report",
                    reason="official seed from exact document resolver output",
                )
    for seed in _items_matching_required(operator_seeds, issuer):
        add_seed(
            seed.get("seed_url") or seed.get("url"),
            seed_type=str(seed.get("seed_type") or "manual_official_seed"),
            source="operator_seed",
            reason=str(seed.get("notes") or "operator-provided official seed"),
            operator_review_status=str(seed.get("operator_review_status") or ""),
            notes=str(seed.get("notes") or ""),
            extra=seed,
        )

    home_urls = [
        seed["seed_url"]
        for seed in seeds
        if seed["source"] != "operator_seed"
        and urllib.parse.urlparse(seed["seed_url"]).path in {"", "/"}
        and _host(seed["seed_url"]) not in {"e-disclosure.ru", "disclosure.ru", "moex.com", "moex.ru"}
    ]
    for home_url in home_urls:
        parsed = urllib.parse.urlparse(home_url)
        root = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
        for path in PROBABLE_ISSUER_SEED_PATHS:
            add_seed(
                urllib.parse.urljoin(root, path),
                seed_type="issuer_reports" if "report" in path else "issuer_investor_relations",
                source_type="issuer_investor_relations",
                source="generated_official_path",
                reason="probable issuer reporting seed generated from official issuer home",
            )
    return seeds


def validate_official_seed_candidate(
    seed: dict[str, Any],
    *,
    issuer: dict[str, Any],
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
) -> dict[str, Any]:
    seed_url = _normalize_candidate_url(str(seed.get("seed_url") or ""))
    seed_type = _normalize_seed_type(seed.get("seed_type"), seed_url, str(seed.get("source_type") or ""))
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    base = {
        "seed_type": seed_type,
        "seed_url": seed_url,
        "seed_status": "invalid_seed",
        "confidence": "low",
        "source": seed.get("source") or "",
        "reason": seed.get("reason") or "",
        "probe_status": "",
        "http_status": None,
        "content_type": "",
        "warnings": warnings,
        "errors": errors,
    }
    forbidden_fields = [
        field
        for field in FORBIDDEN_SEED_METADATA_FIELDS
        if seed.get(field) not in (None, "")
    ]
    if forbidden_fields or seed.get("values") or seed.get("field_evidence"):
        errors.append(
            {
                "company_id": issuer.get("company_id"),
                "seed_url": seed_url,
                "message": "financial values are forbidden in official seed metadata",
                "fields": forbidden_fields or ["values"],
            }
        )
    if not seed_url:
        errors.append({"company_id": issuer.get("company_id"), "message": "seed_url is required"})
        return base
    classification = _classify_candidate_url(
        seed_url,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
        allow_unknown_source=args.allow_unknown_source,
    )
    if classification["status"] == "blocked":
        base["seed_status"] = "blocked_seed"
        errors.append({"company_id": issuer.get("company_id"), "seed_url": seed_url, "message": classification["message"]})
    elif classification["status"] == "unknown_error":
        base["seed_status"] = "invalid_seed"
        errors.append({"company_id": issuer.get("company_id"), "seed_url": seed_url, "message": classification["message"]})
    elif classification["status"] == "unknown_warning":
        base["seed_status"] = "needs_operator_review"
        base["confidence"] = "low"
        warnings.append({"company_id": issuer.get("company_id"), "seed_url": seed_url, "message": classification["message"]})
    else:
        operator_reviewed = str(seed.get("operator_review_status") or "").strip() in DOCUMENT_INTAKE_REVIEWED_STATUSES
        generated = seed.get("source") == "generated_official_path"
        base["seed_status"] = "needs_operator_review" if generated else "valid_seed"
        base["confidence"] = "high" if operator_reviewed else "medium"
        if seed.get("source") == "operator_seed" and not operator_reviewed:
            base["seed_status"] = "needs_operator_review"
            base["confidence"] = "medium"
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "seed_url": seed_url,
                    "message": "operator seed is not reviewed; operator review required",
                }
            )
    if args.seed_probe_urls and classification["status"] == "official":
        probe = _fetch_candidate_page(
            seed_url,
            timeout_seconds=args.seed_fetch_timeout_seconds,
            max_bytes=args.seed_max_response_bytes,
            user_agent=args.seed_user_agent,
        )
        base["probe_status"] = probe.get("status")
        base["http_status"] = probe.get("http_status")
        base["content_type"] = probe.get("content_type") or ""
        if probe.get("status") != "ok":
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "seed_url": seed_url,
                    "message": "seed probe failed",
                    "error": probe.get("error"),
                }
            )
        elif "html" in str(probe.get("content_type") or "").casefold():
            base["confidence"] = "high"
            if seed.get("source") == "generated_official_path":
                base["seed_status"] = "valid_seed"
    if errors and base["seed_status"] != "blocked_seed":
        base["seed_status"] = "invalid_seed"
        base["confidence"] = "low"
    return base


def _dedupe_validated_seeds(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for seed in seeds:
        key = (str(seed.get("seed_type") or ""), str(seed.get("seed_url") or ""))
        existing = by_key.get(key)
        if existing is None or _seed_sort_key(seed) > _seed_sort_key(existing):
            by_key[key] = seed
    return sorted(by_key.values(), key=_seed_sort_key, reverse=True)


def _seed_sort_key(seed: dict[str, Any]) -> tuple[int, int, str]:
    status_rank = {
        "valid_seed": 3,
        "needs_operator_review": 2,
        "invalid_seed": 1,
        "blocked_seed": 0,
    }.get(str(seed.get("seed_status") or ""), 0)
    return (status_rank, _confidence_rank(seed.get("confidence")), str(seed.get("seed_url") or ""))


def _split_source_context_urls(value: str) -> list[str]:
    normalized = value.replace("|", ";").replace(",", ";")
    return [item.strip() for item in normalized.split(";") if item.strip()]


def _normalize_candidate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _is_ignored_href(url: str) -> bool:
    lowered = str(url).casefold()
    return lowered.startswith(("mailto:", "tel:", "javascript:"))


def _candidate_title(anchor: dict[str, str], document_url: str) -> str:
    return (
        anchor.get("text")
        or anchor.get("title")
        or urllib.parse.unquote(_file_name_from_url(document_url))
        or document_url
    ).strip()


def _source_url_context_from_strings(urls: list[str]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return " | ".join(result)


def _candidate_source_type(document_url: str, source_page_url: str) -> str:
    host = _host(document_url) or _host(source_page_url)
    if "moex" in host:
        return "exchange_disclosure"
    if "disclosure" in host:
        return "official_disclosure"
    return "official_issuer_report"


def _url_is_pdf(url: str) -> bool:
    return urllib.parse.urlparse(str(url)).path.casefold().endswith(".pdf")


def _looks_like_report_document_url(url: str) -> bool:
    path = urllib.parse.urlparse(str(url)).path.casefold()
    return _url_is_pdf(url) or _contains_any(path, ("report", "annual", "ifrs", "statement", "disclosure", "otchet"))


def _is_exact_document_candidate(url: str, title: str, args: argparse.Namespace) -> bool:
    text = f"{url} {title}".casefold()
    if _looks_like_landing_page(url) and not args.candidate_allow_landing_pages:
        return False
    if args.candidate_require_pdf_or_report_page and not (_url_is_pdf(url) or _looks_like_report_document_url(url)):
        return False
    return str(args.report_period) in text and _contains_any(
        text,
        ("annual", "yearly", "ifrs", "financial statements", "report", "\u043c\u0441\u0444\u043e", "\u043e\u0442\u0447\u0435\u0442"),
    )


def _has_strong_document_signals(url: str, title: str, args: argparse.Namespace) -> bool:
    text = f"{url} {title}".casefold()
    period_ok = str(args.report_period) in text
    annual_ok = args.report_type != "annual" or _contains_any(text, ("annual", "yearly", "\u0433\u043e\u0434\u043e\u0432"))
    standard_ok = args.accounting_standard == "unknown" or _contains_any(
        text,
        (args.accounting_standard.casefold(), "\u043c\u0441\u0444\u043e"),
    )
    report_ok = _contains_any(text, ("financial statements", "financial report", "report", "\u043e\u0442\u0447\u0435\u0442"))
    bad = _contains_any(text, ("presentation", "press", "news", "prospectus", "coupon", "quarter"))
    return period_ok and annual_ok and standard_ok and report_ok and not bad


def _clone_args(args: argparse.Namespace, **updates: Any) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(updates)
    return argparse.Namespace(**values)


def _parse_required_issuers(
    args: argparse.Namespace,
    input_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = _split_cli_list(args.required_company_ids)
    names = _split_cli_list(args.required_company_names)
    required: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, company_id in enumerate(ids):
        matched = _first_matching_company_id(input_documents, company_id)
        item = {
            "company_id": _maybe_int(company_id),
            "company_name": (
                names[index]
                if index < len(names)
                else matched.get("company_name") or matched.get("canonical_company_name") or ""
            ),
        }
        key = (str(item["company_id"] or ""), _normalize_name(str(item["company_name"] or "")))
        if key not in seen:
            seen.add(key)
            required.append(item)
    for name in names[len(ids):]:
        item = {"company_id": None, "company_name": name}
        key = ("", _normalize_name(name))
        if key not in seen:
            seen.add(key)
            required.append(item)
    if required:
        return required
    for document in input_documents:
        item = {
            "company_id": _maybe_int(_document_company_key(document)),
            "company_name": document.get("company_name") or document.get("canonical_company_name") or "",
        }
        key = (str(item["company_id"] or ""), _normalize_name(str(item["company_name"] or "")))
        if key not in seen:
            seen.add(key)
            required.append(item)
    return required


def _build_required_issuer_gate_statuses(
    required_issuers: list[dict[str, Any]],
    *,
    input_documents: list[dict[str, Any]],
    filled_documents: list[dict[str, Any]],
    validation_report: dict[str, Any],
    resolve_report: dict[str, Any],
    resolved_source_intake: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    validation_results = validation_report.get("document_results") or []
    resolve_issuers = resolve_report.get("issuers") or []
    required_statuses: list[dict[str, Any]] = []
    for required in required_issuers:
        input_matches = _items_matching_required(input_documents, required)
        filled_matches = _items_matching_required(filled_documents, required)
        validation_matches = _items_matching_required(validation_results, required)
        resolve_matches = _items_matching_required(resolve_issuers, required)
        source_matches = _items_matching_required(resolved_source_intake, required)
        valid_matches = [
            item
            for item in validation_matches
            if not item.get("errors")
            and item.get("document_status") == "valid_official_document"
        ]
        severe_errors = [
            error
            for item in validation_matches
            for error in item.get("errors") or []
            if not _is_unresolved_document_gate_error(error)
        ]
        resolved_valid_documents = [
            document
            for issuer in resolve_matches
            for document in issuer.get("document_candidates") or []
            if document.get("document_status") == "valid_official_document"
        ]
        filled = any(
            item.get("document_url")
            and item.get("operator_review_status") in DOCUMENT_INTAKE_REVIEWED_STATUSES
            for item in filled_matches
        )
        reason = _required_gate_reason(
            required,
            input_matches=input_matches,
            filled_matches=filled_matches,
            validation_matches=validation_matches,
            valid_matches=valid_matches,
            resolved_valid_documents=resolved_valid_documents,
            resolve_matches=resolve_matches,
            source_matches=source_matches,
            severe_errors=severe_errors,
            args=args,
        )
        gate_status = "passed" if reason == "" else "failed"
        if severe_errors:
            document_status = "invalid_document"
        elif gate_status == "passed":
            document_status = "valid_official_document"
        elif filled:
            document_status = "needs_operator_review"
        else:
            document_status = "missing"
        chosen = (valid_matches or filled_matches or input_matches or [{}])[0]
        required_statuses.append(
            {
                "company_id": required.get("company_id") or chosen.get("company_id"),
                "company_name": required.get("company_name") or chosen.get("company_name") or "",
                "document_status": document_status,
                "gate_status": gate_status,
                "reason": reason or "exact reviewed official document is resolve-ready",
                "document_url": chosen.get("document_url") or "",
                "document_title": chosen.get("document_title") or "",
                "filled": bool(filled),
                "input_present": bool(input_matches),
                "filled_present": bool(filled_matches),
                "validation_present": bool(validation_matches),
                "resolve_present": bool(resolve_matches),
                "resolved_source_present": bool(source_matches),
                "valid_document_count": len(valid_matches),
                "resolved_document_count": len(resolved_valid_documents),
            }
        )
    return required_statuses


def _required_gate_reason(
    required: dict[str, Any],
    *,
    input_matches: list[dict[str, Any]],
    filled_matches: list[dict[str, Any]],
    validation_matches: list[dict[str, Any]],
    valid_matches: list[dict[str, Any]],
    resolved_valid_documents: list[dict[str, Any]],
    resolve_matches: list[dict[str, Any]],
    source_matches: list[dict[str, Any]],
    severe_errors: list[dict[str, Any]],
    args: argparse.Namespace,
) -> str:
    label = required.get("company_id") or required.get("company_name") or "required issuer"
    if args.require_all_required_issuers and not input_matches:
        return f"missing required issuer in input exact document intake: {label}"
    if args.require_all_required_issuers and not filled_matches:
        return f"missing required issuer in filled exact document intake: {label}"
    if args.require_all_required_issuers and not validation_matches:
        return f"missing required issuer in validation results: {label}"
    if severe_errors:
        return _message_text(severe_errors[0])
    if args.require_one_valid_document_per_issuer and len(valid_matches) == 0:
        return "exact reviewed official document is missing"
    if args.require_one_valid_document_per_issuer and len(valid_matches) > 1:
        return "required issuer has more than one valid official document"
    if args.require_document_resolve and not resolve_matches:
        return f"missing required issuer in document-resolve output: {label}"
    if args.require_document_resolve and source_matches == [] and not resolve_matches:
        return f"missing required issuer in resolved source intake: {label}"
    if args.fail_on_unresolved_documents and len(resolved_valid_documents) == 0:
        return "document-resolve did not produce a valid official document"
    if args.fail_on_needs_operator_review:
        unresolved = [
            item
            for item in validation_matches
            if item.get("document_status") == "needs_operator_review"
        ]
        if unresolved:
            return "exact document still needs operator review"
    return ""


def _required_issuer_error(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": item.get("company_id"),
        "company_name": item.get("company_name"),
        "document_status": item.get("document_status"),
        "message": item.get("reason") or "required issuer did not pass quality gate",
    }


def _is_unresolved_document_gate_error(error: dict[str, Any]) -> bool:
    message = _message_text(error).casefold()
    return any(
        phrase in message
        for phrase in (
            "operator_review_status must be reviewed",
            "document_url is required",
            "document_title is required",
            "document_url is missing",
            "document_title is missing",
            "exact document candidate not provided",
            "exact document candidate file not provided",
            "exact reviewed official document is missing",
        )
    )


def _items_matching_required(
    items: list[dict[str, Any]],
    required: dict[str, Any],
) -> list[dict[str, Any]]:
    return [item for item in items if _matches_required_issuer(item, required)]


def _matches_required_issuer(item: dict[str, Any], required: dict[str, Any]) -> bool:
    required_id = str(required.get("company_id") or "").strip()
    item_id = _document_company_key(item)
    if required_id and item_id == required_id:
        return True
    required_name = _normalize_name(str(required.get("company_name") or ""))
    if not required_name:
        return False
    item_names = {
        _normalize_name(str(item.get("company_name") or "")),
        _normalize_name(str(item.get("canonical_company_name") or "")),
    }
    return required_name in item_names


def _first_matching_company_id(
    documents: list[dict[str, Any]],
    company_id: str,
) -> dict[str, Any]:
    for document in documents:
        if _document_company_key(document) == str(company_id):
            return document
    return {}


def _split_cli_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _maybe_int(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return int(text) if text.isdigit() else text


def _safety_flags_clean(report: dict[str, Any]) -> bool:
    return all(report.get(key) == expected for key, expected in SAFETY_FLAGS.items())


def _document_intake_base(document: dict[str, Any], *, source_context: str) -> dict[str, Any]:
    base = {
        field: document.get(field)
        for field in DOCUMENT_INTAKE_TEMPLATE_FIELDS
    }
    base["source_url_context"] = source_context or document.get("source_url_context") or ""
    base["operator_review_status"] = document.get("operator_review_status") or "operator_to_fill"
    base["notes"] = document.get("notes") or "Exact official report document is still required."
    _strip_financial_values(base)
    return base


def _not_filled_result(document: dict[str, Any], message: str) -> dict[str, Any]:
    warning = {
        "company_id": document.get("company_id"),
        "company_name": document.get("company_name"),
        "document_url": document.get("document_url"),
        "document_status": "needs_operator_review",
        "message": message,
    }
    return {
        "company_id": document.get("company_id"),
        "company_name": document.get("company_name"),
        "source_type": document.get("source_type"),
        "document_url": document.get("document_url") or "",
        "document_status": "needs_operator_review",
        "operator_review_status": document.get("operator_review_status") or "operator_to_fill",
        "report_period": document.get("report_period") or "",
        "domain_status": None,
        "status": "warning",
        "warnings": [warning],
        "errors": [],
    }


def _group_document_candidates(documents: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for document in documents:
        grouped.setdefault(_document_period_key(document), []).append(document)
    return grouped


def _document_period_key(document: dict[str, Any]) -> tuple[str, str]:
    return (
        _document_company_key(document),
        str(document.get("report_period") or ""),
    )


def _candidate_sort_key(candidate: dict[str, Any], args: argparse.Namespace) -> tuple[int, int, int]:
    source_type = str(candidate.get("source_type") or "")
    official_issuer_bonus = 1 if args.prefer_official_issuer and source_type in {
        "official_issuer_report",
        "issuer_annual_report_pdf",
    } else 0
    disclosure_bonus = 1 if args.prefer_disclosure and source_type in {
        "official_disclosure",
        "exchange_disclosure",
    } else 0
    return (
        official_issuer_bonus,
        disclosure_bonus,
        _confidence_rank(candidate.get("confidence") or "high"),
    )


def _confidence_rank(value: Any) -> int:
    return DOCUMENT_CONFIDENCE_LEVELS.get(str(value or "").casefold(), 0)


def _combine_error_results(
    document: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = [
        error
        for result in results
        for error in result.get("errors") or []
    ]
    warnings = [
        warning
        for result in results
        for warning in result.get("warnings") or []
    ]
    return {
        "company_id": document.get("company_id"),
        "company_name": document.get("company_name"),
        "source_type": document.get("source_type"),
        "document_url": document.get("document_url") or "",
        "document_status": "invalid_document",
        "operator_review_status": document.get("operator_review_status") or "operator_to_fill",
        "report_period": document.get("report_period") or "",
        "domain_status": None,
        "status": "invalid",
        "warnings": warnings,
        "errors": errors,
    }


def _attach_optional_document_metadata(
    document: dict[str, Any],
    validation: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    if not document.get("document_url"):
        return
    classification = classify_source_url(str(document["document_url"]), allow_unknown_source=False)
    if classification["status"] != "official":
        return
    if args.probe_urls:
        probe = _probe_url(
            str(document["document_url"]),
            timeout_seconds=args.probe_timeout_seconds,
            max_bytes=args.max_probe_bytes,
        )
        document["probe"] = probe
        document["probe_status"] = probe.get("status")
        document["probe_http_status"] = probe.get("http_status")
        document["probe_content_type"] = probe.get("content_type")
        if probe.get("status") != "ok":
            validation["warnings"].append(
                {
                    "company_id": document.get("company_id"),
                    "document_url": document.get("document_url"),
                    "message": "document probe failed",
                    "error": probe.get("error"),
                }
            )
    if args.download_documents and args.document_download_dir is not None:
        candidate = dict(document)
        candidate["document_status"] = "valid_official_document"
        download = _download_valid_document(candidate, args.document_download_dir)
        document["download"] = download
        validation["warnings"].extend(download.get("warnings") or [])
        validation["errors"].extend(download.get("errors") or [])


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
    if mode == "document-intake-fill":
        return ["Validate filled exact document intake, then run document-resolve before candidate-fill."]
    if mode == "document-quality-gate":
        return ["Run financial value collection only after this gate passes; import remains disabled."]
    if mode == "document-candidate-discover":
        return ["Review discovered exact document candidates or run the quality gate before value collection."]
    if mode == "operator-seed-template":
        return ["Fill seed_url with reviewed official navigation pages, then run operator-seed-validate."]
    if mode == "operator-seed-validate":
        return ["Use reviewed valid seeds with official-seed-resolve; exact documents still require the quality gate."]
    if mode == "operator-seed-merge":
        return ["Use the merged seed pack for official-seed-resolve or controlled candidate discovery."]
    if mode == "operator-seed-candidate-discover":
        return ["Review candidate seeds, validate autofill if written, then use reviewed seeds with official-seed-resolve."]
    if mode == "operator-seed-review-template":
        return ["Fill operator_decision for reviewed seed candidates, then run operator-seed-promote-reviewed."]
    if mode == "operator-seed-promote-reviewed":
        return ["Validate promoted reviewed seeds, then use them with official-seed-resolve; exact documents still require the quality gate."]
    if mode == "official-seed-resolve":
        return ["Use resolved official seeds for controlled candidate discovery; exact documents still require the quality gate."]
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
