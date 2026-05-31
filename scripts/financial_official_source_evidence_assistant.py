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
from datetime import date, timedelta
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
    "exact-document-discover-from-seeds",
    "operator-resolution-validate",
    "operator-resolution-apply-preview",
    "operator-resolution-apply-draft",
    "document-intake-draft-gate-preview",
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
EXACT_DOCUMENT_FROM_SEED_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "inn",
    "ogrn",
    "report_period",
    "report_type",
    "accounting_standard",
    "source_type",
    "source_page_url",
    "document_url",
    "document_title",
    "document_kind",
    "document_period_year",
    "document_period_quarter",
    "document_period_status",
    "period_confidence",
    "period_evidence",
    "report_type_match_status",
    "type_evidence",
    "accounting_standard_match_status",
    "standard_evidence",
    "fallback_status",
    "availability_status",
    "availability_reason_codes",
    "can_use_as_target_period_evidence",
    "historical_fallback_allowed",
    "historical_fallback_scope",
    "operator_action",
    "crawl_depth",
    "parent_seed_url",
    "source_chain",
    "is_category_page",
    "category_followed",
    "document_date",
    "source_file_name",
    "document_status",
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
AVAILABILITY_OPERATOR_SUMMARY_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "availability_status",
    "availability_reason_codes",
    "exact_target_period_document_count",
    "target_period_document_count",
    "historical_annual_ifrs_document_count",
    "interim_or_quarterly_document_count",
    "wrong_standard_document_count",
    "placeholder_not_found_count",
    "operator_review_required_count",
    "latest_available_period",
    "latest_available_report_type",
    "latest_available_standard",
    "latest_available_document_url",
    "can_use_as_target_period_evidence",
    "historical_fallback_allowed",
    "historical_fallback_scope",
    "operator_action",
    "reporting_policy_name",
    "target_period_end_date",
    "primary_deadline_days",
    "primary_expected_deadline_date",
    "expected_availability_date",
    "availability_current_date",
    "before_primary_deadline",
    "after_primary_deadline",
    "within_grace_window",
    "within_conservative_grace_window",
    "after_conservative_grace_window",
    "deadline_status",
    "gate_status",
    "gate_passed",
    "gate_reason",
    "ready_for_value_extraction",
    "ready_for_import",
    "recommended_next_step",
    "operator_note",
]
OPERATOR_REVIEW_QUEUE_FIELDS = [
    "action_id",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "availability_status",
    "availability_reason_codes",
    "operator_action",
    "recommended_next_step",
    "queue_action_type",
    "queue_action_label",
    "queue_priority",
    "queue_status",
    "is_blocking_next_stage",
    "blocked_stage",
    "manual_review_required",
    "can_unblock_extraction",
    "target_evidence_available",
    "gate_status",
    "gate_passed",
    "ready_for_value_extraction",
    "ready_for_import",
    "historical_fallback_scope",
    "historical_fallback_allowed",
    "latest_available_period",
    "latest_available_report_type",
    "latest_available_standard",
    "latest_available_document_url",
    "reporting_policy_name",
    "primary_deadline_days",
    "primary_expected_deadline_date",
    "expected_availability_date",
    "availability_current_date",
    "before_primary_deadline",
    "after_primary_deadline",
    "within_grace_window",
    "within_conservative_grace_window",
    "after_conservative_grace_window",
    "deadline_status",
    "operator_instruction",
    "operator_note",
    "source_context",
]
OFFICIAL_SOURCE_COVERAGE_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "coverage_status",
    "coverage_score",
    "coverage_grade",
    "coverage_reason_codes",
    "has_official_seed",
    "reviewed_official_seed_count",
    "valid_reviewed_seed_count",
    "invalid_reviewed_seed_count",
    "official_seed_url_count",
    "has_company_website_seed",
    "has_ir_or_investor_relations_seed",
    "has_e_disclosure_seed",
    "has_reporting_or_disclosure_page",
    "has_financial_results_page",
    "has_accounting_statements_page",
    "has_annual_reports_page",
    "has_ifrs_reporting_page",
    "category_page_count",
    "category_pages_followed_count",
    "exact_report_document_count",
    "target_period_document_count",
    "historical_annual_ifrs_document_count",
    "interim_or_quarterly_document_count",
    "wrong_standard_document_count",
    "placeholder_not_found_count",
    "availability_status",
    "deadline_status",
    "operator_action",
    "recommended_next_step",
    "queue_action_type",
    "queue_priority",
    "queue_status",
    "can_use_as_target_period_evidence",
    "gate_status",
    "gate_passed",
    "ready_for_value_extraction",
    "ready_for_import",
    "coverage_operator_action",
    "coverage_operator_instruction",
    "coverage_note",
]
HISTORICAL_FALLBACK_REGISTRY_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "historical_fallback_status",
    "historical_fallback_reason_codes",
    "historical_fallback_scope",
    "historical_fallback_allowed",
    "latest_available_period",
    "latest_available_report_type",
    "latest_available_standard",
    "latest_available_document_url",
    "latest_available_document_title",
    "latest_available_document_date",
    "latest_available_source_page_url",
    "latest_available_source_type",
    "historical_report_count",
    "historical_annual_ifrs_document_count",
    "historical_annual_ifrs_periods",
    "historical_annual_ifrs_latest_period",
    "historical_annual_ifrs_oldest_period",
    "target_period_document_count",
    "exact_target_period_document_count",
    "interim_or_quarterly_document_count",
    "wrong_standard_document_count",
    "placeholder_not_found_count",
    "availability_status",
    "deadline_status",
    "coverage_status",
    "coverage_grade",
    "coverage_score",
    "queue_action_type",
    "queue_priority",
    "queue_status",
    "can_use_as_target_period_evidence",
    "can_use_for_value_extraction",
    "can_use_for_import",
    "can_use_for_scoring",
    "can_use_for_paper_trading",
    "diagnostic_only_reason",
    "operator_action",
    "recommended_next_step",
    "coverage_operator_action",
    "gate_status",
    "gate_passed",
    "ready_for_value_extraction",
    "ready_for_import",
]
REPORTING_READINESS_MATRIX_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "reporting_readiness_status",
    "reporting_readiness_grade",
    "reporting_readiness_reason_codes",
    "primary_blocker",
    "blocking_layers",
    "availability_status",
    "deadline_status",
    "can_use_as_target_period_evidence",
    "target_evidence_available",
    "exact_target_period_document_count",
    "target_period_document_count",
    "gate_status",
    "gate_passed",
    "gate_reason",
    "ready_for_value_extraction",
    "ready_for_import",
    "coverage_status",
    "coverage_grade",
    "coverage_score",
    "coverage_operator_action",
    "historical_fallback_status",
    "historical_fallback_scope",
    "latest_available_period",
    "latest_available_report_type",
    "latest_available_standard",
    "latest_available_document_url",
    "can_use_for_value_extraction",
    "can_use_for_import",
    "can_use_for_scoring",
    "can_use_for_paper_trading",
    "queue_action_type",
    "queue_priority",
    "queue_status",
    "manual_review_required",
    "is_blocking_next_stage",
    "blocked_stage",
    "extraction_allowed",
    "import_allowed",
    "scoring_allowed",
    "paper_trading_allowed",
    "next_required_action",
    "operator_instruction",
    "readiness_note",
]
OPERATOR_RESOLUTION_PACK_FIELDS = [
    "resolution_id",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "resolution_status",
    "resolution_priority",
    "resolution_action_type",
    "resolution_action_label",
    "resolution_reason_codes",
    "source_readiness_status",
    "reporting_readiness_status",
    "primary_blocker",
    "blocking_layers",
    "availability_status",
    "deadline_status",
    "coverage_status",
    "coverage_grade",
    "historical_fallback_status",
    "historical_fallback_scope",
    "queue_action_type",
    "queue_priority",
    "queue_status",
    "target_evidence_available",
    "gate_status",
    "gate_passed",
    "ready_for_value_extraction",
    "ready_for_import",
    "extraction_allowed",
    "import_allowed",
    "scoring_allowed",
    "paper_trading_allowed",
    "can_unblock_extraction_if_completed",
    "requires_exact_document_url",
    "requires_official_seed_review",
    "requires_publication_verification",
    "requires_escalation",
    "is_wait_action",
    "is_diagnostic_only",
    "operator_input_required",
    "operator_input_schema_version",
    "operator_fill_exact_document_url",
    "operator_fill_document_title",
    "operator_fill_document_date",
    "operator_fill_source_page_url",
    "operator_fill_source_type",
    "operator_fill_report_period",
    "operator_fill_report_type",
    "operator_fill_accounting_standard",
    "operator_fill_decision",
    "operator_fill_notes",
    "current_known_document_url",
    "current_known_source_page_url",
    "latest_historical_document_url",
    "latest_historical_period",
    "operator_instruction",
    "validation_hint",
    "safety_note",
]
OPERATOR_RESOLUTION_DECISIONS = {
    "pending",
    "exact_document_found",
    "target_report_not_found",
    "wait_until_grace_date",
    "escalate_missing_target_report",
    "seed_review_required",
    "reject_invalid_input",
}
OPERATOR_RESOLUTION_VALIDATION_CRITICAL_COLUMNS = [
    "resolution_id",
    "company_id",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "resolution_action_type",
    "operator_fill_decision",
    "operator_fill_exact_document_url",
]
OPERATOR_RESOLUTION_VALIDATION_EXPECTED_COLUMNS = [
    "resolution_id",
    "company_id",
    "company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "resolution_action_type",
    "operator_fill_exact_document_url",
    "operator_fill_document_title",
    "operator_fill_document_date",
    "operator_fill_source_page_url",
    "operator_fill_source_type",
    "operator_fill_report_period",
    "operator_fill_report_type",
    "operator_fill_accounting_standard",
    "operator_fill_decision",
    "operator_fill_notes",
    "latest_historical_document_url",
    "latest_historical_period",
]
OPERATOR_RESOLUTION_VALIDATION_FIELDS = [
    "resolution_id",
    "company_id",
    "company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "operator_fill_decision",
    "operator_fill_exact_document_url",
    "operator_fill_document_title",
    "operator_fill_document_date",
    "operator_fill_source_page_url",
    "operator_fill_source_type",
    "operator_fill_report_period",
    "operator_fill_report_type",
    "operator_fill_accounting_standard",
    "operator_fill_notes",
    "validation_status",
    "validation_severity",
    "validation_reason_codes",
    "validation_errors",
    "validation_warnings",
    "url_validation_status",
    "domain_validation_status",
    "document_kind",
    "document_period_year",
    "document_period_status",
    "report_type_match_status",
    "accounting_standard_match_status",
    "latest_historical_document_url",
    "latest_historical_period",
    "historical_fallback_url_used_as_exact_document",
    "can_use_for_future_intake_review",
    "would_update_document_intake",
    "would_promote_seed",
    "would_extract_values",
    "would_import_report",
    "would_mutate_scores",
    "would_trigger_paper_trading",
    "operator_next_step",
    "validation_note",
]
OPERATOR_RESOLUTION_APPLY_PREVIEW_FIELDS = [
    "patch_id",
    "resolution_id",
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "patch_status",
    "patch_action",
    "patch_reason_codes",
    "patch_errors",
    "patch_warnings",
    "source_validation_status",
    "can_use_for_future_intake_review",
    "operator_fill_decision",
    "proposed_document_url",
    "proposed_document_title",
    "proposed_document_date",
    "proposed_source_page_url",
    "proposed_source_type",
    "proposed_report_period",
    "proposed_report_type",
    "proposed_accounting_standard",
    "document_kind",
    "document_period_year",
    "document_period_status",
    "report_type_match_status",
    "accounting_standard_match_status",
    "intake_target_status",
    "intake_existing_document_url",
    "intake_existing_document_status",
    "intake_existing_operator_review_status",
    "intake_existing_filter_status",
    "would_create_intake_row",
    "would_update_existing_intake_row",
    "would_replace_placeholder",
    "would_apply_to_document_intake",
    "would_promote_seed",
    "would_extract_values",
    "would_import_report",
    "would_mutate_scores",
    "would_trigger_paper_trading",
    "future_apply_allowed",
    "future_apply_blocked_reason",
    "operator_next_step",
    "preview_note",
]
OPERATOR_RESOLUTION_APPLY_DRAFT_FIELDS = [
    "apply_draft_id",
    "patch_id",
    "resolution_id",
    "company_id",
    "company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "apply_draft_status",
    "apply_draft_action",
    "apply_draft_reason_codes",
    "apply_draft_errors",
    "apply_draft_warnings",
    "source_patch_status",
    "source_patch_action",
    "future_apply_allowed",
    "draft_document_url",
    "draft_document_title",
    "draft_document_date",
    "draft_source_page_url",
    "draft_source_type",
    "matched_intake_status",
    "matched_intake_existing_document_url",
    "matched_intake_existing_document_status",
    "matched_intake_existing_filter_status",
    "draft_row_index",
    "would_change_draft_file",
    "would_overwrite_input_file",
    "would_update_original_intake",
    "would_update_database",
    "would_promote_seed",
    "would_extract_values",
    "would_import_report",
    "would_mutate_scores",
    "would_trigger_paper_trading",
    "operator_next_step",
    "apply_draft_note",
]
DOCUMENT_INTAKE_DRAFT_FIELDS = list(
    dict.fromkeys(
        [
            *DOCUMENT_INTAKE_TEMPLATE_FIELDS,
            "source_page_url",
            "document_status",
            "filter_status",
            "fallback_status",
            "operator_resolution_id",
            "operator_resolution_patch_id",
            "draft_source",
            "draft_note",
        ]
    )
)
DOCUMENT_INTAKE_DRAFT_GATE_SUMMARY_FIELDS = [
    "company_id",
    "company_name",
    "canonical_company_id",
    "canonical_company_name",
    "target_reporting_period",
    "required_report_type",
    "required_standard",
    "draft_row_status",
    "draft_document_url",
    "draft_document_status",
    "draft_operator_review_status",
    "draft_filter_status",
    "draft_fallback_status",
    "validation_status",
    "validation_errors",
    "validation_warnings",
    "document_kind",
    "document_period_year",
    "document_period_status",
    "report_type_match_status",
    "accounting_standard_match_status",
    "gate_status",
    "gate_passed",
    "gate_reason",
    "ready_for_value_extraction",
    "ready_for_import",
    "is_placeholder",
    "has_document_url",
    "has_exact_target_document",
    "blocked_reason_codes",
    "next_required_action",
    "would_extract_values",
    "would_import_report",
    "would_mutate_scores",
    "would_trigger_paper_trading",
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
EXACT_DOCUMENT_REVIEWED_SEED_SOURCES = {"operator_seed", "reviewed_official_source", "reviewed_seed"}
EXACT_DOCUMENT_DEFAULT_SEED_TYPES = (
    "issuer_reports",
    "official_disclosure_reports",
    "official_disclosure_profile",
)
EXACT_DOCUMENT_POSITIVE_TERMS = (
    "annual report",
    "annual reports",
    "annual financial statements",
    "financial statements",
    "consolidated financial statements",
    "ifrs",
    "audited",
    "audit",
    "auditor",
    "reporting",
    "report",
    "pdf",
    "download",
    "\u0433\u043e\u0434\u043e\u0432\u043e\u0439 \u043e\u0442\u0447\u0435\u0442",
    "\u0433\u043e\u0434\u043e\u0432\u043e\u0439 \u043e\u0442\u0447\u0451\u0442",
    "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0430\u044f",
    "\u043c\u0441\u0444\u043e",
    "\u0430\u0443\u0434\u0438\u0442",
    "\u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u043e\u0442\u0447\u0435\u0442",
    "\u043e\u0442\u0447\u0451\u0442",
    "\u0441\u043a\u0430\u0447\u0430\u0442\u044c",
)
EXACT_DOCUMENT_PATH_TERMS = (
    "annual",
    "report",
    "reports",
    "ifrs",
    "msfo",
    "financial",
    "statements",
    "audit",
    "audited",
    "consolidated",
    ".pdf",
)
EXACT_DOCUMENT_NEGATIVE_TERMS = (
    "presentation",
    "press release",
    "press",
    "news",
    "coupon",
    "bond terms",
    "emission",
    "prospectus",
    "securities",
    "quarterly",
    "quarter",
    "q1",
    "q2",
    "q3",
    "q4",
    "1q",
    "2q",
    "3q",
    "4q",
    "interim",
    "archive",
    "\u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\u0438\u044f",
    "\u043d\u043e\u0432\u043e\u0441\u0442\u044c",
    "\u043d\u043e\u0432\u043e\u0441\u0442\u0438",
    "\u043a\u0443\u043f\u043e\u043d",
    "\u044d\u043c\u0438\u0441\u0441\u0438\u044f",
    "\u044d\u043c\u0438\u0441\u0441\u0438\u043e\u043d\u043d\u044b\u0435",
    "\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442",
    "\u043a\u0432\u0430\u0440\u0442\u0430\u043b",
    "\u043a\u0432\u0430\u0440\u0442\u0430\u043b\u044c\u043d\u0430\u044f",
    "1 \u043a\u0432\u0430\u0440\u0442\u0430\u043b",
    "2 \u043a\u0432\u0430\u0440\u0442\u0430\u043b",
    "3 \u043a\u0432\u0430\u0440\u0442\u0430\u043b",
    "6 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
    "9 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
    "\u043f\u0440\u043e\u043c\u0435\u0436\u0443\u0442\u043e\u0447\u043d\u0430\u044f",
)
EXACT_DOCUMENT_LEGAL_POLICY_TERMS = (
    "privacy policy",
    "personal data",
    "data protection",
    "cookies",
    "cookie policy",
    "terms of use",
    "terms and conditions",
    "user agreement",
    "agreement",
    "confidentiality",
    "legal information",
    "site policy",
    "policy_conf",
    "privacy",
    "cookie",
    "cookies",
    "user_agreement",
    "terms",
    "personal-data",
    "personal_data",
    "confidential",
    "\u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0430 \u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u0438",
    "\u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c",
    "\u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435",
    "\u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0445 \u0434\u0430\u043d\u043d\u044b\u0445",
    "\u043a\u0443\u043a\u0438",
    "\u0444\u0430\u0439\u043b\u044b cookie",
    "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u043e\u0435 \u0441\u043e\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u0435",
    "\u0443\u0441\u043b\u043e\u0432\u0438\u044f \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u044f",
    "\u043f\u0440\u0430\u0432\u043e\u0432\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f",
    "\u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0430 \u0441\u0430\u0439\u0442\u0430",
    "\u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435 \u043d\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0443",
    "\u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f",
)
EXACT_DOCUMENT_CATEGORY_KINDS = {
    "report_category_page",
    "financial_results_page",
    "accounting_statements_page",
    "disclosure_category_page",
}
EXACT_DOCUMENT_FINAL_KINDS = {"exact_report_document"}
EXACT_DOCUMENT_WRONG_TYPE_KINDS = {
    "legal_policy_document",
    "privacy_policy_document",
    "cookie_policy_document",
    "user_agreement_document",
    "presentation_document",
    "prospectus_document",
    "news_or_press_document",
    "generic_navigation_page",
}
EXACT_DOCUMENT_KIND_COUNTERS = {
    "exact_report_document": "exact_report_document_count",
    "report_category_page": "category_page_count",
    "financial_results_page": "category_page_count",
    "accounting_statements_page": "category_page_count",
    "disclosure_category_page": "category_page_count",
    "legal_policy_document": "legal_policy_document_count",
    "privacy_policy_document": "privacy_policy_document_count",
    "cookie_policy_document": "cookie_policy_document_count",
    "user_agreement_document": "user_agreement_document_count",
    "presentation_document": "presentation_document_count",
    "prospectus_document": "prospectus_document_count",
    "quarterly_or_interim_document": "quarterly_or_interim_document_count",
    "news_or_press_document": "news_or_press_document_count",
    "generic_navigation_page": "generic_navigation_page_count",
    "unknown_document": "unknown_document_count",
}
EXACT_DOCUMENT_ANNUAL_TERMS = (
    "annual",
    "yearly",
    "12m",
    "12 months",
    "12 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
    "\u0433\u043e\u0434\u043e\u0432",
    "\u0433\u043e\u0434\u043e\u0432\u0430\u044f",
    "\u0433\u043e\u0434\u043e\u0432\u044b\u0435",
    "\u0437\u0430 \u0433\u043e\u0434",
    "31.12",
    "31-12",
)
EXACT_DOCUMENT_INTERIM_TERMS = (
    "1h",
    "h1",
    "half-year",
    "half year",
    "6m",
    "6 months",
    "6 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
    "9m",
    "9 months",
    "9 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
    "q1",
    "q2",
    "q3",
    "q4",
    "1q",
    "2q",
    "3q",
    "4q",
    "quarter",
    "quarterly",
    "3m",
    "3 months",
    "interim",
    "condensed",
    "\u043f\u043e\u043b\u0443\u0433\u043e\u0434",
    "\u043a\u0432\u0430\u0440\u0442\u0430\u043b",
    "\u043a\u0432\u0430\u0440\u0442\u0430\u043b\u044c\u043d",
    "\u043f\u0440\u043e\u043c\u0435\u0436\u0443\u0442",
    "\u0441\u043e\u043a\u0440\u0430\u0449\u0435\u043d",
    "\u0441\u043e\u043a\u0440\u0430\u0449\u0451\u043d",
)
EXACT_DOCUMENT_IFRS_TERMS = (
    "ifrs",
    "\u043c\u0441\u0444\u043e",
    "international financial reporting standards",
    "consolidated financial statements",
    "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0430\u044f \u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u0430\u044f \u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u044c",
)
EXACT_DOCUMENT_RAS_TERMS = (
    "ras",
    "\u0440\u0441\u0431\u0443",
    "individual financial statements",
    "standalone",
    "\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0441\u043a\u0430\u044f \u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0441\u043a\u0430\u044f \u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u044c",
    "\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0441\u043a\u043e\u0439 \u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u0438",
    "\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0441\u043a\u043e\u0439 \u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u0438",
)
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
    parser.add_argument("--exact-document-candidate-output", type=Path, default=None)
    parser.add_argument("--exact-document-candidate-csv-output", type=Path, default=None)
    parser.add_argument(
        "--exact-document-seed-types",
        default="issuer_reports,official_disclosure_reports,official_disclosure_profile",
    )
    parser.add_argument("--exact-document-max-pages-per-issuer", type=int, default=10)
    parser.add_argument("--exact-document-max-links-per-page", type=int, default=300)
    parser.add_argument("--exact-document-top-n-per-issuer", type=int, default=10)
    parser.add_argument("--exact-document-top-n-per-type", type=int, default=5)
    parser.add_argument("--exact-document-min-score", type=int, default=70)
    parser.add_argument("--exact-document-auto-review-threshold", type=int, default=95)
    parser.add_argument("--exact-document-include-filtered", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-noise-filter", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-allowed-domains", default="")
    parser.add_argument("--exact-document-blocked-domains", default="")
    parser.add_argument("--exact-document-fetch-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--exact-document-max-response-bytes", type=int, default=1000000)
    parser.add_argument(
        "--exact-document-user-agent",
        default="BondRadar-exact-document-discovery/1.0",
    )
    parser.add_argument("--exact-document-probe-urls", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-download-documents", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-download-dir", type=Path, default=None)
    parser.add_argument("--exact-document-second-level-crawl", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-max-crawl-depth", type=int, default=2)
    parser.add_argument("--exact-document-category-page-min-score", type=int, default=80)
    parser.add_argument(
        "--exact-document-category-page-types",
        default="issuer_reports,accounting_statements,financial_results,disclosure_reports",
    )
    parser.add_argument("--exact-document-follow-category-pages", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-follow-same-domain-only", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-legal-documents", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-policy-documents", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-generic-pdfs", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-include-category-pages", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-max-category-pages-per-issuer", type=int, default=5)
    parser.add_argument("--exact-document-max-second-level-links-per-page", type=int, default=300)
    parser.add_argument(
        "--exact-document-period-policy",
        choices=("target-only", "target-or-prior-year-fallback", "diagnostic-all"),
        default="target-only",
    )
    parser.add_argument("--exact-document-filter-wrong-period", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-interim-for-annual", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-wrong-report-type", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-filter-wrong-standard", type=_parse_bool, default=True)
    parser.add_argument("--exact-document-include-wrong-period", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-include-wrong-report-type", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-allow-prior-year-fallback", type=_parse_bool, default=False)
    parser.add_argument("--exact-document-max-prior-year-gap", type=int, default=1)
    parser.add_argument("--exact-document-target-period-required", type=_parse_bool, default=True)
    parser.add_argument(
        "--exact-document-unknown-period-policy",
        choices=("review-only", "filter", "diagnostic"),
        default="review-only",
    )
    parser.add_argument("--exact-document-min-period-confidence", default="medium")
    parser.add_argument("--exact-document-availability-policy-name", default="annual_ifrs_deadline_aware_grace_window")
    parser.add_argument("--exact-document-annual-ifrs-primary-deadline-days", type=int, default=120)
    parser.add_argument("--exact-document-annual-ifrs-grace-days", type=int, default=180)
    parser.add_argument("--exact-document-availability-current-date", default="")
    parser.add_argument("--availability-operator-summary-output", type=Path, default=None)
    parser.add_argument("--availability-operator-summary-csv-output", type=Path, default=None)
    parser.add_argument("--availability-operator-summary-markdown-output", type=Path, default=None)
    parser.add_argument("--operator-review-queue-output", type=Path, default=None)
    parser.add_argument("--operator-review-queue-csv-output", type=Path, default=None)
    parser.add_argument("--operator-review-queue-markdown-output", type=Path, default=None)
    parser.add_argument("--official-source-coverage-output", type=Path, default=None)
    parser.add_argument("--official-source-coverage-csv-output", type=Path, default=None)
    parser.add_argument("--official-source-coverage-markdown-output", type=Path, default=None)
    parser.add_argument("--historical-fallback-registry-output", type=Path, default=None)
    parser.add_argument("--historical-fallback-registry-csv-output", type=Path, default=None)
    parser.add_argument("--historical-fallback-registry-markdown-output", type=Path, default=None)
    parser.add_argument("--reporting-readiness-matrix-output", type=Path, default=None)
    parser.add_argument("--reporting-readiness-matrix-csv-output", type=Path, default=None)
    parser.add_argument("--reporting-readiness-matrix-markdown-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-pack-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-pack-csv-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-pack-markdown-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-input", type=Path, default=None)
    parser.add_argument("--operator-resolution-source-pack-input", type=Path, default=None)
    parser.add_argument("--operator-resolution-validation-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-validation-csv-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-validation-markdown-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-validation-input", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-preview-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-preview-csv-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-preview-markdown-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-preview-input", type=Path, default=None)
    parser.add_argument("--document-intake-draft-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-csv-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-draft-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-draft-csv-output", type=Path, default=None)
    parser.add_argument("--operator-resolution-apply-draft-markdown-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-input", type=Path, default=None)
    parser.add_argument("--document-intake-draft-validation-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-validation-markdown-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-gate-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-gate-markdown-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-gate-summary-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-gate-summary-csv-output", type=Path, default=None)
    parser.add_argument("--document-intake-draft-gate-summary-markdown-output", type=Path, default=None)
    parser.add_argument("--run-document-intake-fill", type=_parse_bool, default=False)
    parser.add_argument("--run-document-intake-validate", type=_parse_bool, default=False)
    parser.add_argument("--document-intake-validation-json-output", type=Path, default=None)
    parser.add_argument("--document-intake-validation-markdown-output", type=Path, default=None)
    parser.add_argument("--run-document-quality-gate", type=_parse_bool, default=False)
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
    elif args.mode == "exact-document-discover-from-seeds":
        report = run_exact_document_discover_from_seeds(args)
    elif args.mode == "operator-resolution-validate":
        report = run_operator_resolution_validate(args)
    elif args.mode == "operator-resolution-apply-preview":
        report = run_operator_resolution_apply_preview(args)
    elif args.mode == "operator-resolution-apply-draft":
        report = run_operator_resolution_apply_draft(args)
    elif args.mode == "document-intake-draft-gate-preview":
        report = run_document_intake_draft_gate_preview(args)
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


def run_operator_resolution_validate(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    input_columns: set[str] = set()
    source_pack_rows: list[dict[str, Any]] = []

    if args.operator_resolution_input is None:
        errors.append({"message": "operator-resolution-validate mode requires --operator-resolution-input"})
    if not errors:
        try:
            rows, input_columns = load_operator_resolution_input(args.operator_resolution_input)
        except Exception as exc:
            errors.append({"message": str(exc)})
    if args.operator_resolution_source_pack_input is None:
        warnings.append({"message": "source_pack_missing"})
    elif not errors:
        try:
            source_pack_rows, _source_columns = load_operator_resolution_input(args.operator_resolution_source_pack_input)
        except Exception as exc:
            warnings.append({"message": f"source_pack_load_failed: {exc}"})

    source_pack_by_id = {
        str(row.get("resolution_id") or ""): row
        for row in source_pack_rows
        if row.get("resolution_id")
    }
    validation_rows = _build_operator_resolution_validation_rows(
        args,
        rows=rows,
        input_columns=input_columns,
        source_pack_by_id=source_pack_by_id,
        source_pack_provided=args.operator_resolution_source_pack_input is not None,
    )
    report = _build_operator_resolution_validation_report(
        args,
        rows=validation_rows,
        load_warnings=warnings,
        load_errors=errors,
    )
    if args.operator_resolution_validation_output is not None and not errors:
        write_json_report(report, args.operator_resolution_validation_output)
    if args.operator_resolution_validation_csv_output is not None and not errors:
        write_operator_resolution_validation_csv(
            validation_rows,
            args.operator_resolution_validation_csv_output,
        )
    if args.operator_resolution_validation_markdown_output is not None and not errors:
        write_operator_resolution_validation_markdown(
            report,
            args.operator_resolution_validation_markdown_output,
        )
    return report


def run_operator_resolution_apply_preview(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    source_pack_rows: list[dict[str, Any]] = []
    intake_documents: list[dict[str, Any]] | None = None

    if args.operator_resolution_validation_input is None:
        errors.append({"message": "operator-resolution-apply-preview mode requires --operator-resolution-validation-input"})
    if not errors:
        try:
            validation_rows, _validation_columns = load_operator_resolution_input(args.operator_resolution_validation_input)
        except Exception as exc:
            errors.append({"message": str(exc)})
    if args.operator_resolution_source_pack_input is None:
        warnings.append({"message": "source_pack_missing"})
    elif not errors:
        try:
            source_pack_rows, _source_columns = load_operator_resolution_input(args.operator_resolution_source_pack_input)
        except Exception as exc:
            warnings.append({"message": f"source_pack_load_failed: {exc}"})
    if args.document_intake_input is None:
        warnings.append({"message": "document_intake_context_missing"})
    elif not errors:
        try:
            intake_documents = load_document_intake_file(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    source_pack_by_id = {
        str(row.get("resolution_id") or ""): row
        for row in source_pack_rows
        if row.get("resolution_id")
    }
    patch_rows = _build_operator_resolution_apply_preview_rows(
        validation_rows,
        source_pack_by_id=source_pack_by_id,
        intake_documents=intake_documents,
    )
    report = _build_operator_resolution_apply_preview_report(
        args,
        rows=patch_rows,
        load_warnings=warnings,
        load_errors=errors,
    )
    if args.operator_resolution_apply_preview_output is not None and not errors:
        write_json_report(report, args.operator_resolution_apply_preview_output)
    if args.operator_resolution_apply_preview_csv_output is not None and not errors:
        write_operator_resolution_apply_preview_csv(
            patch_rows,
            args.operator_resolution_apply_preview_csv_output,
        )
    if args.operator_resolution_apply_preview_markdown_output is not None and not errors:
        write_operator_resolution_apply_preview_markdown(
            report,
            args.operator_resolution_apply_preview_markdown_output,
        )
    return report


def run_operator_resolution_apply_draft(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    patch_rows: list[dict[str, Any]] = []
    intake_payload: Any = {"documents": []}
    intake_documents: list[dict[str, Any]] = []

    if args.operator_resolution_apply_preview_input is None:
        errors.append({"message": "operator-resolution-apply-draft mode requires --operator-resolution-apply-preview-input"})
    if args.document_intake_input is None:
        errors.append({"message": "operator-resolution-apply-draft mode requires --document-intake-input"})
    if args.document_intake_input is not None:
        for output_path in _operator_resolution_apply_draft_output_paths(args):
            if output_path is not None and _paths_equal(output_path, args.document_intake_input):
                errors.append({"message": "draft_output_must_not_equal_input", "path": str(output_path)})
    if not errors:
        try:
            patch_rows, _patch_columns = load_operator_resolution_apply_preview_input(
                args.operator_resolution_apply_preview_input
            )
        except Exception as exc:
            errors.append({"message": str(exc)})
    if not errors:
        try:
            intake_payload, intake_documents = load_document_intake_payload(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    draft_documents = copy.deepcopy(intake_documents)
    apply_rows = _build_operator_resolution_apply_draft_rows(
        patch_rows,
        draft_documents=draft_documents,
    )
    report = _build_operator_resolution_apply_draft_report(
        args,
        rows=apply_rows,
        draft_documents=draft_documents,
        load_warnings=warnings,
        load_errors=errors,
    )
    if not errors:
        if args.document_intake_draft_output is not None:
            write_document_intake_draft_json(
                intake_payload,
                draft_documents,
                args.document_intake_draft_output,
            )
        if args.document_intake_draft_csv_output is not None:
            write_document_intake_draft_csv(
                draft_documents,
                args.document_intake_draft_csv_output,
            )
        if args.operator_resolution_apply_draft_output is not None:
            write_json_report(report, args.operator_resolution_apply_draft_output)
        if args.operator_resolution_apply_draft_csv_output is not None:
            write_operator_resolution_apply_draft_csv(
                apply_rows,
                args.operator_resolution_apply_draft_csv_output,
            )
        if args.operator_resolution_apply_draft_markdown_output is not None:
            write_operator_resolution_apply_draft_markdown(
                report,
                args.operator_resolution_apply_draft_markdown_output,
            )
    return report


def run_document_intake_draft_gate_preview(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    draft_documents: list[dict[str, Any]] = []
    validation_report: dict[str, Any] = {}
    quality_gate_report: dict[str, Any] = {}
    strict_documents: list[dict[str, Any]] = []

    if args.document_intake_draft_input is None:
        errors.append({"message": "document_intake_draft_input_required"})
    if args.document_intake_draft_input is not None:
        for output_path in _document_intake_draft_gate_preview_output_paths(args):
            if output_path is not None and _paths_equal(output_path, args.document_intake_draft_input):
                errors.append({"message": "draft_gate_output_must_not_equal_input", "path": str(output_path)})
    if not errors:
        try:
            draft_documents = load_document_intake_file(args.document_intake_draft_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if not errors:
        validation_args = _clone_args(
            args,
            mode="document-intake-validate",
            document_intake_input=args.document_intake_draft_input,
        )
        validation_report = run_document_intake_validate(validation_args)
        strict_documents = [
            document
            for document in (
                _document_intake_draft_gate_classification(item, args=args)
                for item in draft_documents
            )
            if _exact_document_is_downstream_eligible(document)
        ]
        if args.source_intake_input is None:
            warnings.append({"message": "quality_gate_source_context_missing"})
        with tempfile.TemporaryDirectory(prefix="bondradar-draft-gate-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            strict_candidates = tmp_path / "strict_draft_candidates.json"
            write_json_report(
                {
                    "status": "preview",
                    "mode": "document-intake-draft-gate-preview-strict-candidates",
                    "documents": strict_documents,
                    **SAFETY_FLAGS,
                },
                strict_candidates,
            )
            gate_args = _clone_args(
                args,
                mode="document-quality-gate",
                document_intake_input=args.document_intake_draft_input,
                exact_document_candidates_input=strict_candidates,
                document_intake_output=None,
                document_intake_csv_output=None,
                source_intake_output=None,
                document_output=None,
                document_checklist_output=None,
            )
            quality_gate_report = run_document_quality_gate(gate_args)

    summary_rows = _build_document_intake_draft_gate_summary_rows(
        draft_documents,
        validation_report=validation_report,
        quality_gate_report=quality_gate_report,
        source_context_missing=args.source_intake_input is None,
        args=args,
    )
    report = _build_document_intake_draft_gate_preview_report(
        args,
        rows=summary_rows,
        validation_report=validation_report,
        quality_gate_report=quality_gate_report,
        load_warnings=warnings,
        load_errors=errors,
    )
    if not errors:
        if args.document_intake_draft_validation_output is not None:
            write_json_report(validation_report, args.document_intake_draft_validation_output)
        if args.document_intake_draft_validation_markdown_output is not None:
            write_markdown_report(validation_report, args.document_intake_draft_validation_markdown_output)
        if args.document_intake_draft_gate_output is not None:
            write_json_report(quality_gate_report, args.document_intake_draft_gate_output)
        if args.document_intake_draft_gate_markdown_output is not None:
            write_markdown_report(quality_gate_report, args.document_intake_draft_gate_markdown_output)
        if args.document_intake_draft_gate_summary_output is not None:
            write_json_report(report, args.document_intake_draft_gate_summary_output)
        if args.document_intake_draft_gate_summary_csv_output is not None:
            write_document_intake_draft_gate_summary_csv(
                summary_rows,
                args.document_intake_draft_gate_summary_csv_output,
            )
        if args.document_intake_draft_gate_summary_markdown_output is not None:
            write_document_intake_draft_gate_preview_markdown(
                report,
                args.document_intake_draft_gate_summary_markdown_output,
            )
    return report


def _operator_resolution_apply_draft_output_paths(args: argparse.Namespace) -> list[Path | None]:
    return [
        args.document_intake_draft_output,
        args.document_intake_draft_csv_output,
        args.operator_resolution_apply_draft_output,
        args.operator_resolution_apply_draft_csv_output,
        args.operator_resolution_apply_draft_markdown_output,
        args.json_output,
        args.markdown_output,
    ]


def _document_intake_draft_gate_preview_output_paths(args: argparse.Namespace) -> list[Path | None]:
    return [
        args.document_intake_draft_validation_output,
        args.document_intake_draft_validation_markdown_output,
        args.document_intake_draft_gate_output,
        args.document_intake_draft_gate_markdown_output,
        args.document_intake_draft_gate_summary_output,
        args.document_intake_draft_gate_summary_csv_output,
        args.document_intake_draft_gate_summary_markdown_output,
        args.json_output,
        args.markdown_output,
    ]


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


def run_exact_document_discover_from_seeds(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_documents: list[dict[str, Any]] = []
    seed_issuers: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    reviewed_seeds_used: list[dict[str, Any]] = []
    missing_issuers: list[dict[str, Any]] = []
    blocked_candidate_count = 0

    if args.seed_input is None:
        errors.append({"message": "exact-document-discover-from-seeds mode requires --seed-input"})
    if args.document_intake_input is None:
        warnings.append({"message": "document intake input is not provided; integrated fill/gate will be unavailable"})
    if not errors:
        try:
            seed_issuers = load_seed_pack_issuers(args.seed_input)
            if args.document_intake_input is not None:
                input_documents = load_document_intake_file(args.document_intake_input)
        except Exception as exc:
            errors.append({"message": str(exc)})

    if args.run_document_intake_fill and args.document_intake_input is None:
        errors.append({"message": "run-document-intake-fill requires --document-intake-input"})
    if args.run_document_intake_validate and args.document_intake_input is None:
        errors.append({"message": "run-document-intake-validate requires --document-intake-input"})
    if args.run_document_quality_gate and args.document_intake_input is None:
        errors.append({"message": "run-document-quality-gate requires --document-intake-input"})

    required_issuers = _parse_required_issuers(args, [*input_documents, *seed_issuers])
    seed_types = _normalized_seed_types(args.exact_document_seed_types)
    allowed_domains = _exact_document_allowed_domains(args)
    blocked_hints = _exact_document_blocked_hints(args)
    raw_documents: list[dict[str, Any]] = []

    if not errors:
        for required in required_issuers:
            issuer = _exact_document_issuer_base(required, input_documents=input_documents, seed_issuers=seed_issuers)
            seed_rows = _select_reviewed_exact_document_seeds(
                issuer,
                seed_issuers=seed_issuers,
                seed_types=seed_types,
                allowed_domains=allowed_domains,
                blocked_hints=blocked_hints,
                args=args,
                warnings=warnings,
                errors=errors,
            )
            if not seed_rows:
                missing_issuers.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name") or "",
                        "reason": "no reviewed valid official seed pages available",
                    }
                )
                warnings.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name") or "",
                        "message": "no reviewed valid official seed pages available",
                    }
                )
                continue
            for seed in seed_rows[: max(int(args.exact_document_max_pages_per_issuer or 0), 0)]:
                reviewed_seeds_used.append(_exact_document_reviewed_seed_used(issuer, seed))
                seed_url = str(seed.get("seed_url") or "")
                fetch = _fetch_candidate_page(
                    seed_url,
                    timeout_seconds=args.exact_document_fetch_timeout_seconds,
                    max_bytes=args.exact_document_max_response_bytes,
                    user_agent=args.exact_document_user_agent,
                )
                if fetch.get("status") != "ok":
                    warnings.append(
                        {
                            "company_id": issuer.get("company_id"),
                            "source_page_url": seed_url,
                            "message": "failed to fetch reviewed official seed page",
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
                            "message": "reviewed seed response is not HTML; skipped anchor extraction",
                        }
                    )
                    continue
                anchors = _extract_html_anchors(str(fetch.get("body") or ""), seed_url)
                for anchor in anchors[: max(int(args.exact_document_max_links_per_page or 0), 0)]:
                    candidate, blocked = build_exact_document_candidate_from_seed_anchor(
                        issuer,
                        anchor,
                        seed,
                        seed_url,
                        args=args,
                        allowed_domains=allowed_domains,
                        blocked_hints=blocked_hints,
                        crawl_depth=1,
                        parent_seed_url=seed_url,
                        source_chain=[seed_url],
                    )
                    if blocked:
                        blocked_candidate_count += 1
                    if candidate is not None:
                        raw_documents.append(candidate)
            if args.exact_document_second_level_crawl and args.exact_document_follow_category_pages:
                crawled_documents, crawled_blocked_count = _crawl_exact_document_category_pages(
                    issuer,
                    raw_documents,
                    args=args,
                    allowed_domains=allowed_domains,
                    blocked_hints=blocked_hints,
                    warnings=warnings,
                )
                blocked_candidate_count += crawled_blocked_count
                raw_documents.extend(crawled_documents)
            if not any(
                _matches_required_issuer(candidate, issuer)
                and candidate.get("document_url")
                and candidate.get("filter_status") == "kept"
                and candidate.get("document_kind") == "exact_report_document"
                for candidate in raw_documents
            ):
                missing_issuers.append(
                    {
                        "company_id": issuer.get("company_id"),
                        "company_name": issuer.get("company_name") or "",
                        "reason": "no exact official report documents found from reviewed seeds",
                    }
                )

    selected_documents, ranking_stats = _select_top_exact_document_candidates(raw_documents, args=args)
    category_pages_followed = [
        item
        for item in raw_documents
        if item.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS and item.get("category_followed")
    ]
    kept_documents = [
        item
        for item in selected_documents
        if _exact_document_is_downstream_eligible(item)
    ]
    if not errors:
        for issuer in required_issuers:
            if not any(_matches_required_issuer(item, issuer) for item in kept_documents):
                issuer_base = _exact_document_issuer_base(issuer, input_documents=input_documents, seed_issuers=seed_issuers)
                selected_documents.append(_exact_document_not_found_candidate(issuer_base))

    documents = sorted(selected_documents, key=_exact_document_output_sort_key)
    kept_documents = [
        item for item in documents if _exact_document_is_downstream_eligible(item)
    ]
    _attach_exact_document_optional_metadata(kept_documents, args=args, warnings=warnings, errors=errors)
    target_reporting_period_availability = _build_target_reporting_period_availability(
        args,
        required_issuers,
        documents=documents,
        raw_documents=raw_documents,
    )
    _annotate_exact_document_availability(
        documents,
        target_reporting_period_availability,
        args=args,
    )

    document_intake_fill_report: dict[str, Any] | None = None
    document_intake_validation_report: dict[str, Any] | None = None
    document_quality_gate_report: dict[str, Any] | None = None
    candidate_output_path = args.exact_document_candidate_output

    with tempfile.TemporaryDirectory(prefix="bondradar-exact-seed-docs-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        if candidate_output_path is None and (
            args.run_document_intake_fill
            or args.run_document_intake_validate
            or args.run_document_quality_gate
        ):
            candidate_output_path = tmp_path / "exact_document_candidates_from_seeds.json"
        if candidate_output_path is not None and not errors:
            _write_exact_document_discovery_payload(
                args,
                candidate_output_path,
                required_issuers=required_issuers,
                seed_issuers=seed_issuers,
                input_documents=input_documents,
                documents=documents,
                category_pages_followed=category_pages_followed,
                all_documents_for_counters=raw_documents,
                reviewed_seeds_used=reviewed_seeds_used,
                missing_issuers=missing_issuers,
                ranking_stats=ranking_stats,
                blocked_candidate_count=blocked_candidate_count,
                target_reporting_period_availability=target_reporting_period_availability,
                status="discovered",
                warnings=warnings,
                errors=errors,
            )
        if args.run_document_intake_fill and not errors and candidate_output_path is not None:
            fill_output = args.document_intake_output or tmp_path / "exact_document_intake_filled_from_seeds.json"
            fill_args = _clone_args(
                args,
                mode="document-intake-fill",
                exact_document_candidates_input=candidate_output_path,
                document_intake_output=fill_output,
                probe_urls=args.exact_document_probe_urls,
                download_documents=args.exact_document_download_documents,
                document_download_dir=args.exact_document_download_dir,
            )
            document_intake_fill_report = run_document_intake_fill(fill_args)
            if args.document_intake_output is None:
                args = _clone_args(args, document_intake_output=fill_output)
        if args.run_document_intake_validate and not errors:
            validation_input = args.document_intake_output
            if validation_input is None:
                if document_intake_fill_report is None and candidate_output_path is not None:
                    fill_output = tmp_path / "exact_document_intake_filled_for_validation.json"
                    fill_args = _clone_args(
                        args,
                        mode="document-intake-fill",
                        exact_document_candidates_input=candidate_output_path,
                        document_intake_output=fill_output,
                    )
                    document_intake_fill_report = run_document_intake_fill(fill_args)
                    validation_input = fill_output
                else:
                    validation_input = Path(str((document_intake_fill_report or {}).get("document_intake_output") or ""))
            if validation_input is not None and str(validation_input):
                validation_args = _clone_args(args, mode="document-intake-validate", document_intake_input=validation_input)
                document_intake_validation_report = run_document_intake_validate(validation_args)
                if args.document_intake_validation_json_output is not None:
                    write_json_report(document_intake_validation_report, args.document_intake_validation_json_output)
                if args.document_intake_validation_markdown_output is not None:
                    write_markdown_report(document_intake_validation_report, args.document_intake_validation_markdown_output)
        if args.run_document_quality_gate and not errors and candidate_output_path is not None:
            source_intake_input = args.source_intake_input
            if source_intake_input is None:
                source_intake_input = tmp_path / "source_intake_from_reviewed_seed_documents.json"
                write_json_report(
                    {
                        "status": "generated",
                        "mode": "exact-document-discover-from-seeds",
                        "issuer_count": len(required_issuers),
                        "issuer_sources": _source_intake_from_exact_document_candidates(
                            required_issuers,
                            documents=kept_documents,
                            reviewed_seeds_used=reviewed_seeds_used,
                        ),
                        **SAFETY_FLAGS,
                    },
                    source_intake_input,
                )
            gate_args = _clone_args(
                args,
                mode="document-quality-gate",
                exact_document_candidates_input=candidate_output_path,
                source_intake_input=source_intake_input,
                probe_urls=args.exact_document_probe_urls,
                download_documents=args.exact_document_download_documents,
                document_download_dir=args.exact_document_download_dir,
            )
            document_quality_gate_report = run_document_quality_gate(gate_args)
            if args.quality_gate_json_output is not None:
                write_json_report(document_quality_gate_report, args.quality_gate_json_output)
            if args.quality_gate_markdown_output is not None:
                write_markdown_report(document_quality_gate_report, args.quality_gate_markdown_output)

    reviewed_count = sum(1 for item in kept_documents if item.get("operator_review_status") == "operator_reviewed")
    review_count = sum(1 for item in kept_documents if item.get("operator_review_status") == "needs_operator_review")
    invalid_count = sum(
        1
        for item in raw_documents
        if item.get("document_status") in {"invalid_document", "blocked_document"}
    )
    status = (
        "failed"
        if errors
        else "passed"
        if required_issuers and reviewed_count >= len(required_issuers)
        else "warning"
    )
    report = _build_exact_document_discovery_report(
        args,
        status=status,
        required_issuers=required_issuers,
        seed_issuers=seed_issuers,
        input_documents=input_documents,
        documents=documents,
        category_pages_followed=category_pages_followed,
        all_documents_for_counters=raw_documents,
        reviewed_seeds_used=reviewed_seeds_used,
        missing_issuers=missing_issuers,
        ranking_stats=ranking_stats,
        blocked_candidate_count=blocked_candidate_count,
        invalid_candidate_count=invalid_count,
        reviewed_candidate_count=reviewed_count,
        needs_operator_review_count=review_count,
        target_reporting_period_availability=target_reporting_period_availability,
        document_intake_fill_report=document_intake_fill_report,
        document_intake_validation_report=document_intake_validation_report,
        document_quality_gate_report=document_quality_gate_report,
        warnings=warnings,
        errors=errors,
    )
    if args.exact_document_candidate_output is not None and not errors:
        write_json_report(report, args.exact_document_candidate_output)
    if args.exact_document_candidate_csv_output is not None and not errors:
        write_exact_document_from_seed_csv(documents, args.exact_document_candidate_csv_output)
    availability_operator_report = _build_availability_operator_summary_report(
        args,
        status=status,
        target_reporting_period_availability=target_reporting_period_availability,
        document_quality_gate_report=document_quality_gate_report,
        warnings=warnings,
        errors=errors,
    )
    if args.availability_operator_summary_output is not None and not errors:
        write_json_report(availability_operator_report, args.availability_operator_summary_output)
    if args.availability_operator_summary_csv_output is not None and not errors:
        write_availability_operator_summary_csv(
            availability_operator_report["issuers"],
            args.availability_operator_summary_csv_output,
        )
    if args.availability_operator_summary_markdown_output is not None and not errors:
        write_availability_operator_summary_markdown(
            availability_operator_report,
            args.availability_operator_summary_markdown_output,
        )
    operator_review_queue_report = _build_operator_review_queue_report(
        args,
        status=status,
        availability_operator_rows=availability_operator_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    if args.operator_review_queue_output is not None and not errors:
        write_json_report(operator_review_queue_report, args.operator_review_queue_output)
    if args.operator_review_queue_csv_output is not None and not errors:
        write_operator_review_queue_csv(
            operator_review_queue_report["actions"],
            args.operator_review_queue_csv_output,
        )
    if args.operator_review_queue_markdown_output is not None and not errors:
        write_operator_review_queue_markdown(
            operator_review_queue_report,
            args.operator_review_queue_markdown_output,
        )
    official_source_coverage_report = _build_official_source_coverage_report(
        args,
        status=status,
        required_issuers=required_issuers,
        seed_issuers=seed_issuers,
        input_documents=input_documents,
        reviewed_seeds_used=reviewed_seeds_used,
        documents=documents,
        all_documents_for_counters=raw_documents,
        category_pages_followed=category_pages_followed,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        warnings=warnings,
        errors=errors,
    )
    if args.official_source_coverage_output is not None and not errors:
        write_json_report(official_source_coverage_report, args.official_source_coverage_output)
    if args.official_source_coverage_csv_output is not None and not errors:
        write_official_source_coverage_csv(
            official_source_coverage_report["issuers"],
            args.official_source_coverage_csv_output,
        )
    if args.official_source_coverage_markdown_output is not None and not errors:
        write_official_source_coverage_markdown(
            official_source_coverage_report,
            args.official_source_coverage_markdown_output,
        )
    historical_fallback_registry_report = _build_historical_fallback_registry_report(
        args,
        status=status,
        required_issuers=required_issuers,
        documents=documents,
        all_documents_for_counters=raw_documents,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    if args.historical_fallback_registry_output is not None and not errors:
        write_json_report(historical_fallback_registry_report, args.historical_fallback_registry_output)
    if args.historical_fallback_registry_csv_output is not None and not errors:
        write_historical_fallback_registry_csv(
            historical_fallback_registry_report["issuers"],
            args.historical_fallback_registry_csv_output,
        )
    if args.historical_fallback_registry_markdown_output is not None and not errors:
        write_historical_fallback_registry_markdown(
            historical_fallback_registry_report,
            args.historical_fallback_registry_markdown_output,
        )
    reporting_readiness_matrix_report = _build_reporting_readiness_matrix_report(
        args,
        status=status,
        required_issuers=required_issuers,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        historical_fallback_registry_rows=historical_fallback_registry_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    if args.reporting_readiness_matrix_output is not None and not errors:
        write_json_report(reporting_readiness_matrix_report, args.reporting_readiness_matrix_output)
    if args.reporting_readiness_matrix_csv_output is not None and not errors:
        write_reporting_readiness_matrix_csv(
            reporting_readiness_matrix_report["issuers"],
            args.reporting_readiness_matrix_csv_output,
        )
    if args.reporting_readiness_matrix_markdown_output is not None and not errors:
        write_reporting_readiness_matrix_markdown(
            reporting_readiness_matrix_report,
            args.reporting_readiness_matrix_markdown_output,
        )
    operator_resolution_pack_report = _build_operator_resolution_pack_report(
        args,
        status=status,
        required_issuers=required_issuers,
        reporting_readiness_rows=reporting_readiness_matrix_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        availability_operator_rows=availability_operator_report["issuers"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        historical_fallback_registry_rows=historical_fallback_registry_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    if args.operator_resolution_pack_output is not None and not errors:
        write_json_report(operator_resolution_pack_report, args.operator_resolution_pack_output)
    if args.operator_resolution_pack_csv_output is not None and not errors:
        write_operator_resolution_pack_csv(
            operator_resolution_pack_report["resolutions"],
            args.operator_resolution_pack_csv_output,
        )
    if args.operator_resolution_pack_markdown_output is not None and not errors:
        write_operator_resolution_pack_markdown(
            operator_resolution_pack_report,
            args.operator_resolution_pack_markdown_output,
        )
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


def load_operator_resolution_input(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.is_file():
        raise ValueError(f"operator resolution input does not exist: {path}")
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            rows = payload.get("resolutions")
            if rows is None:
                rows = payload.get("rows")
            if rows is None:
                rows = payload.get("validation_rows")
        else:
            rows = payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("operator resolution JSON must be a list or object with resolutions/rows")
        normalized = [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]
        columns = {key for row in normalized for key in row}
        return normalized, columns
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], set()
        fieldnames = {field for field in reader.fieldnames if field}
        return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader], fieldnames


def load_operator_resolution_apply_preview_input(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.is_file():
        raise ValueError(f"operator resolution apply preview input does not exist: {path}")
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            rows = payload.get("patch_rows")
            if rows is None:
                rows = payload.get("rows")
        else:
            rows = payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("operator resolution apply preview JSON must be a list or object with patch_rows/rows")
        normalized = [{str(key): _normalize_cell(value) for key, value in row.items()} for row in rows]
        columns = {key for row in normalized for key in row}
        return normalized, columns
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], set()
        fieldnames = {field for field in reader.fieldnames if field}
        return [{key: _normalize_cell(value) for key, value in row.items() if key} for row in reader], fieldnames


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
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    documents = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
        raise ValueError("document intake JSON must contain documents")
    return documents


def load_document_intake_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".csv":
        return load_document_intake_csv(path)
    return load_document_intake_items(path)


def load_document_intake_payload(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    if path.suffix.casefold() == ".csv":
        documents = load_document_intake_csv(path)
        return copy.deepcopy(documents), documents
    if not path.is_file():
        raise ValueError(f"document intake input does not exist: {path}")
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    documents = payload.get("documents") if isinstance(payload, dict) else payload
    if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
        raise ValueError("document intake JSON must contain documents")
    return payload, documents


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


def write_exact_document_from_seed_csv(documents: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXACT_DOCUMENT_FROM_SEED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for document in documents:
            writer.writerow({field: _csv_value(document.get(field)) for field in EXACT_DOCUMENT_FROM_SEED_FIELDS})


def write_availability_operator_summary_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AVAILABILITY_OPERATOR_SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in AVAILABILITY_OPERATOR_SUMMARY_FIELDS})


def write_availability_operator_summary_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_availability_operator_summary_markdown(report), encoding="utf-8")


def write_operator_review_queue_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_REVIEW_QUEUE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OPERATOR_REVIEW_QUEUE_FIELDS})


def write_operator_review_queue_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_operator_review_queue_markdown(report), encoding="utf-8")


def write_official_source_coverage_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OFFICIAL_SOURCE_COVERAGE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OFFICIAL_SOURCE_COVERAGE_FIELDS})


def write_official_source_coverage_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_official_source_coverage_markdown(report), encoding="utf-8")


def write_historical_fallback_registry_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORICAL_FALLBACK_REGISTRY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in HISTORICAL_FALLBACK_REGISTRY_FIELDS})


def write_historical_fallback_registry_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_historical_fallback_registry_markdown(report), encoding="utf-8")


def write_reporting_readiness_matrix_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORTING_READINESS_MATRIX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in REPORTING_READINESS_MATRIX_FIELDS})


def write_reporting_readiness_matrix_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_reporting_readiness_matrix_markdown(report), encoding="utf-8")


def write_operator_resolution_pack_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_RESOLUTION_PACK_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OPERATOR_RESOLUTION_PACK_FIELDS})


def write_operator_resolution_pack_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_operator_resolution_pack_markdown(report), encoding="utf-8")


def write_operator_resolution_validation_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_RESOLUTION_VALIDATION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OPERATOR_RESOLUTION_VALIDATION_FIELDS})


def write_operator_resolution_validation_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_operator_resolution_validation_markdown(report), encoding="utf-8")


def write_operator_resolution_apply_preview_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_RESOLUTION_APPLY_PREVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OPERATOR_RESOLUTION_APPLY_PREVIEW_FIELDS})


def write_operator_resolution_apply_preview_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_operator_resolution_apply_preview_markdown(report), encoding="utf-8")


def write_document_intake_draft_json(original_payload: Any, documents: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(original_payload, dict):
        payload = copy.deepcopy(original_payload)
        payload["documents"] = documents
    else:
        payload = documents
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_document_intake_draft_csv(documents: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCUMENT_INTAKE_DRAFT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for document in documents:
            writer.writerow({field: _csv_value(document.get(field)) for field in DOCUMENT_INTAKE_DRAFT_FIELDS})


def write_operator_resolution_apply_draft_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPERATOR_RESOLUTION_APPLY_DRAFT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in OPERATOR_RESOLUTION_APPLY_DRAFT_FIELDS})


def write_operator_resolution_apply_draft_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_operator_resolution_apply_draft_markdown(report), encoding="utf-8")


def write_document_intake_draft_gate_summary_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOCUMENT_INTAKE_DRAFT_GATE_SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in DOCUMENT_INTAKE_DRAFT_GATE_SUMMARY_FIELDS})


def write_document_intake_draft_gate_preview_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_document_intake_draft_gate_preview_markdown(report), encoding="utf-8")


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
        else "Operator Resolution Validation"
        if report.get("mode") == "operator-resolution-validation"
        else "Operator Resolution Apply Preview"
        if report.get("mode") == "operator-resolution-apply-preview"
        else "Operator Resolution Apply Draft"
        if report.get("mode") == "operator-resolution-apply-draft"
        else "Document Intake Draft Gate Preview"
        if report.get("mode") == "document-intake-draft-gate-preview"
        else "Exact Official Report Document Discovery From Reviewed Seeds"
        if report.get("mode") == "exact-document-discover-from-seeds"
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
    if report.get("mode") == "operator-resolution-validation":
        lines.extend(_render_operator_resolution_validation_sections(report))
    if report.get("mode") == "operator-resolution-apply-preview":
        lines.extend(_render_operator_resolution_apply_preview_sections(report))
    if report.get("mode") == "operator-resolution-apply-draft":
        lines.extend(_render_operator_resolution_apply_draft_sections(report))
    if report.get("mode") == "document-intake-draft-gate-preview":
        lines.extend(_render_document_intake_draft_gate_preview_sections(report))
    if report.get("mode") == "exact-document-discover-from-seeds":
        lines.extend(_render_exact_document_from_seeds_markdown_sections(report))
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


def render_availability_operator_summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Availability Operator Summary",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_availability_operator_view_sections(report.get("summary") or {}, report.get("issuers") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_operator_review_queue_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Operator Review Action Queue",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_operator_review_queue_sections(report.get("summary") or {}, report.get("actions") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_official_source_coverage_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Official Source Coverage Matrix",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_official_source_coverage_sections(report.get("summary") or {}, report.get("issuers") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_historical_fallback_registry_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Historical Fallback Registry",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_historical_fallback_registry_sections(report.get("summary") or {}, report.get("issuers") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_reporting_readiness_matrix_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reporting Readiness Matrix Before Extraction",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_reporting_readiness_matrix_sections(report.get("summary") or {}, report.get("issuers") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_operator_resolution_pack_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Operator Resolution Pack",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- target_reporting_period: {report.get('target_reporting_period')}",
        f"- required_report_type: {report.get('required_report_type')}",
        f"- required_standard: {report.get('required_standard')}",
        "",
    ]
    lines.extend(_render_operator_resolution_pack_sections(report.get("summary") or {}, report.get("resolutions") or []))
    lines.extend(
        [
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
        ]
    )
    return "\n".join(lines) + "\n"


def render_operator_resolution_validation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Operator Resolution Validation",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- row_count: {report.get('operator_resolution_validation_row_count', 0)}",
        "",
    ]
    lines.extend(_render_operator_resolution_validation_sections(report))
    lines.extend(
        [
            "## Safety Notes",
            "",
            "- This validation does not apply operator decisions.",
            "- This validation does not update exact document intake.",
            "- This validation does not extract/import/score/trade.",
            "- Historical fallback URLs cannot be used as target-period exact documents.",
            "",
            "## Safety Flags",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- would_update_document_intake: {report.get('would_update_document_intake')}",
            f"- would_promote_seed: {report.get('would_promote_seed')}",
            f"- would_extract_values: {report.get('would_extract_values')}",
            f"- would_import_report: {report.get('would_import_report')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_operator_resolution_apply_preview_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Operator Resolution Apply Preview",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- row_count: {report.get('operator_resolution_apply_preview_row_count', 0)}",
        "",
    ]
    lines.extend(_render_operator_resolution_apply_preview_sections(report))
    lines.extend(
        [
            "## Safety Notes",
            "",
            "- This preview does not apply operator decisions.",
            "- This preview does not update exact document intake.",
            "- This preview does not extract/import/score/trade.",
            "- Only rows validated as target-period annual IFRS exact reports can become future apply candidates.",
            "",
            "## Safety Flags",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- would_apply_to_document_intake: {report.get('would_apply_to_document_intake')}",
            f"- would_promote_seed: {report.get('would_promote_seed')}",
            f"- would_extract_values: {report.get('would_extract_values')}",
            f"- would_import_report: {report.get('would_import_report')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_operator_resolution_apply_draft_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Operator Resolution Apply Draft",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- row_count: {report.get('operator_resolution_apply_draft_row_count', 0)}",
        "",
    ]
    lines.extend(_render_operator_resolution_apply_draft_sections(report))
    lines.extend(
        [
            "## Draft Outputs",
            "",
            f"- draft JSON: `{report.get('document_intake_draft_output')}`",
            f"- draft CSV: `{report.get('document_intake_draft_csv_output')}`",
            "",
            "## Safety Notes",
            "",
            "- This task writes only a new draft intake file.",
            "- This task does not overwrite the original exact document intake.",
            "- This task does not update DB.",
            "- This task does not extract/import/score/trade.",
            "- Only Task 120 future-apply-allowed rows can be included in the draft.",
            "",
            "## Safety Flags",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- would_update_original_intake: {report.get('would_update_original_intake')}",
            f"- would_update_database: {report.get('would_update_database')}",
            f"- would_promote_seed: {report.get('would_promote_seed')}",
            f"- would_extract_values: {report.get('would_extract_values')}",
            f"- would_import_report: {report.get('would_import_report')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_document_intake_draft_gate_preview_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Document Intake Draft Gate Preview",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- status: `{report.get('status')}`",
        f"- row_count: {report.get('document_intake_draft_gate_preview_row_count', 0)}",
        "",
    ]
    lines.extend(_render_document_intake_draft_gate_preview_sections(report))
    lines.extend(
        [
            "## Safety Notes",
            "",
            "- This task validates only the draft intake file.",
            "- This task does not overwrite original intake.",
            "- This task does not modify the draft intake.",
            "- This task does not extract/import/score/trade.",
            "- Only exact target-period annual IFRS documents can pass the gate.",
            "",
            "## Safety Flags",
            "",
            f"- read_only: {report.get('read_only')}",
            f"- dry_run_only: {report.get('dry_run_only')}",
            f"- import_executed: {report.get('import_executed')}",
            f"- paper_trading_called: {report.get('paper_trading_called')}",
            f"- would_extract_values: {report.get('would_extract_values')}",
            f"- would_import_report: {report.get('would_import_report')}",
            f"- would_mutate_scores: {report.get('would_mutate_scores')}",
            f"- would_trigger_paper_trading: {report.get('would_trigger_paper_trading')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_availability_operator_view_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Target Reporting Period Availability - Operator View",
        "",
        f"- availability rows: {summary.get('target_reporting_period_availability_count', len(rows))}",
        f"- target evidence available: {summary.get('target_evidence_available_count', 0)}",
        f"- historical fallback diagnostic only: {summary.get('historical_fallback_diagnostic_only_count', 0)}",
        f"- extraction ready: {summary.get('extraction_ready_count', 0)}",
        f"- import ready: {summary.get('import_ready_count', 0)}",
        f"- primary expected deadline: {summary.get('primary_expected_deadline_date', '')}",
        f"- conservative expected availability: {summary.get('expected_availability_date', '')}",
        f"- current date: {summary.get('availability_current_date', '')}",
        "",
        "### Availability Status Counts",
        "",
    ]
    status_counts = summary.get("availability_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Deadline Status Counts", ""])
    deadline_counts = summary.get("deadline_status_counts") or {}
    if deadline_counts:
        lines.extend(f"- {key}: {value}" for key, value in deadline_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Operator Action Counts", ""])
    action_counts = summary.get("operator_action_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Issuer Operator Rows",
            "",
            "| Company | Target | Availability | Deadline | Reasons | Historical fallback | Target evidence | Gate | Extraction ready | Operator action | Next step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {company} | {target} {report_type} {standard} | {availability} | {deadline} | {reasons} | {fallback} | {target_evidence} | {gate} | {ready} | {action} | {next_step} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    target=row.get("target_reporting_period") or "",
                    report_type=row.get("required_report_type") or "",
                    standard=row.get("required_standard") or "",
                    availability=row.get("availability_status") or "",
                    deadline=row.get("deadline_status") or "",
                    reasons=_csv_value(row.get("availability_reason_codes")).replace("|", "/"),
                    fallback=row.get("historical_fallback_scope") or "none",
                    target_evidence=row.get("can_use_as_target_period_evidence"),
                    gate=row.get("gate_status") or "",
                    ready=row.get("ready_for_value_extraction"),
                    action=row.get("operator_action") or "",
                    next_step=row.get("recommended_next_step") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_official_source_coverage_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Official Source Coverage Matrix",
        "",
        f"- issuer count: {summary.get('official_source_coverage_issuer_count', len(rows))}",
        f"- strong: {summary.get('official_source_coverage_strong_count', 0)}",
        f"- partial: {summary.get('official_source_coverage_partial_count', 0)}",
        f"- weak: {summary.get('official_source_coverage_weak_count', 0)}",
        f"- missing: {summary.get('official_source_coverage_missing_count', 0)}",
        f"- needs operator: {summary.get('official_source_coverage_needs_operator_count', 0)}",
        "",
        "### Coverage Status Counts",
        "",
    ]
    status_counts = summary.get("official_source_coverage_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Coverage Grade Counts", ""])
    grade_counts = summary.get("official_source_coverage_grade_counts") or {}
    if grade_counts:
        lines.extend(f"- {key}: {value}" for key, value in grade_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Coverage Operator Action Counts", ""])
    action_counts = summary.get("official_source_coverage_action_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Issuer Coverage",
            "",
            "| Company | Coverage | Grade | Score | Seeds | Reporting pages | Docs | Availability | Queue action | Coverage action |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            seed_summary = "{valid}/{total}".format(
                valid=row.get("valid_reviewed_seed_count", 0),
                total=row.get("official_seed_url_count", 0),
            )
            reporting = "reporting={reporting}; category_followed={followed}".format(
                reporting=row.get("has_reporting_or_disclosure_page"),
                followed=row.get("category_pages_followed_count", 0),
            )
            docs = "target={target}; historical={historical}; interim={interim}".format(
                target=row.get("target_period_document_count", 0),
                historical=row.get("historical_annual_ifrs_document_count", 0),
                interim=row.get("interim_or_quarterly_document_count", 0),
            )
            lines.append(
                "| {company} | {coverage} | {grade} | {score} | {seeds} | {reporting} | {docs} | {availability} | {queue} | {action} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    coverage=row.get("coverage_status") or "",
                    grade=row.get("coverage_grade") or "",
                    score=row.get("coverage_score", 0),
                    seeds=seed_summary,
                    reporting=reporting,
                    docs=docs,
                    availability=row.get("availability_status") or "",
                    queue=row.get("queue_action_type") or "",
                    action=row.get("coverage_operator_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_historical_fallback_registry_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Historical Fallback Registry",
        "",
        f"- issuer count: {summary.get('historical_fallback_registry_issuer_count', len(rows))}",
        f"- historical annual IFRS reports: {summary.get('historical_fallback_registry_report_count', 0)}",
        f"- latest historical reports: {summary.get('historical_fallback_registry_latest_report_count', 0)}",
        f"- diagnostic only: {summary.get('historical_fallback_registry_diagnostic_only_count', 0)}",
        f"- target evidence: {summary.get('historical_fallback_registry_target_evidence_count', 0)}",
        f"- extraction ready: {summary.get('historical_fallback_registry_extraction_ready_count', 0)}",
        f"- import ready: {summary.get('historical_fallback_registry_import_ready_count', 0)}",
        "",
        "### Historical Fallback Status Counts",
        "",
    ]
    status_counts = summary.get("historical_fallback_registry_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Issuer Historical Fallback",
            "",
            "| Company | Target | Fallback status | Latest period | Type | Standard | Scope | Target evidence | Extraction ready | Source | Next step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            source = str(row.get("latest_available_document_url") or "").replace("|", "/")
            lines.append(
                "| {company} | {target} {report_type} {standard} | {status} | {latest_period} | {latest_type} | {latest_standard} | {scope} | target evidence {target_evidence} | {ready} | {source} | {next_step} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    target=row.get("target_reporting_period") or "",
                    report_type=row.get("required_report_type") or "",
                    standard=row.get("required_standard") or "",
                    status=row.get("historical_fallback_status") or "",
                    latest_period=row.get("latest_available_period") or "",
                    latest_type=row.get("latest_available_report_type") or "",
                    latest_standard=row.get("latest_available_standard") or "",
                    scope=row.get("historical_fallback_scope") or "none",
                    target_evidence=row.get("can_use_as_target_period_evidence"),
                    ready=row.get("ready_for_value_extraction"),
                    source=source,
                    next_step=str(row.get("recommended_next_step") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_reporting_readiness_matrix_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Reporting Readiness Matrix Before Extraction",
        "",
        f"- issuer count: {summary.get('reporting_readiness_issuer_count', len(rows))}",
        f"- ready: {summary.get('reporting_readiness_ready_count', 0)}",
        f"- blocked: {summary.get('reporting_readiness_blocked_count', 0)}",
        f"- needs operator: {summary.get('reporting_readiness_needs_operator_count', 0)}",
        f"- target evidence available: {summary.get('reporting_readiness_target_evidence_available_count', 0)}",
        f"- gate passed: {summary.get('reporting_readiness_gate_passed_count', 0)}",
        f"- historical only: {summary.get('reporting_readiness_historical_only_count', 0)}",
        f"- source coverage blocked: {summary.get('reporting_readiness_source_coverage_blocked_count', 0)}",
        "",
        "### Readiness Status Counts",
        "",
    ]
    status_counts = summary.get("reporting_readiness_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Readiness Blocker Counts", ""])
    blocker_counts = summary.get("reporting_readiness_blocker_counts") or {}
    if blocker_counts:
        lines.extend(f"- {key}: {value}" for key, value in blocker_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Issuer Readiness",
            "",
            "| Company | Readiness | Grade | Primary blocker | Layers | Target evidence | Gate | Coverage | Fallback | Queue action | Next action |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            fallback = "{status}; {scope}".format(
                status=row.get("historical_fallback_status") or "",
                scope=row.get("historical_fallback_scope") or "none",
            )
            lines.append(
                "| {company} | {readiness} | {grade} | {blocker} | {layers} | extraction {extraction} / target {target} | {gate} | {coverage} | {fallback} | {queue} | {next_action} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    readiness=row.get("reporting_readiness_status") or "",
                    grade=row.get("reporting_readiness_grade") or "",
                    blocker=row.get("primary_blocker") or "",
                    layers=_csv_value(row.get("blocking_layers")).replace("|", "/"),
                    extraction=row.get("extraction_allowed"),
                    target=row.get("target_evidence_available"),
                    gate=row.get("gate_status") or "",
                    coverage=row.get("coverage_status") or "",
                    fallback=fallback,
                    queue=row.get("queue_action_type") or "",
                    next_action=row.get("next_required_action") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_operator_resolution_pack_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Operator Resolution Pack",
        "",
        f"- issuer count: {summary.get('operator_resolution_pack_issuer_count', len(rows))}",
        f"- action count: {summary.get('operator_resolution_pack_action_count', len(rows))}",
        f"- manual actions: {summary.get('operator_resolution_pack_manual_action_count', 0)}",
        f"- wait actions: {summary.get('operator_resolution_pack_wait_action_count', 0)}",
        f"- can unblock extraction if completed: {summary.get('operator_resolution_pack_can_unblock_extraction_count', 0)}",
        f"- target document fills: {summary.get('operator_resolution_pack_target_document_fill_count', 0)}",
        f"- source reviews: {summary.get('operator_resolution_pack_source_review_count', 0)}",
        f"- escalations: {summary.get('operator_resolution_pack_escalation_count', 0)}",
        "",
        "### Resolution Action Type Counts",
        "",
    ]
    action_counts = summary.get("operator_resolution_pack_action_type_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Resolution Priority Counts", ""])
    priority_counts = summary.get("operator_resolution_pack_priority_counts") or {}
    if priority_counts:
        lines.extend(f"- {key}: {value}" for key, value in priority_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Resolution Status Counts", ""])
    status_counts = summary.get("operator_resolution_pack_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Issuer Resolution Table",
            "",
            "| Company | Action | Priority | Status | Blocker | Requires URL | Requires seed review | Can unblock | Current fallback | Operator instruction |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            fallback = "{period} {url}".format(
                period=row.get("latest_historical_period") or "",
                url=row.get("latest_historical_document_url") or "",
            ).strip()
            lines.append(
                "| {company} | {action} | {priority} | {status} | {blocker} | {requires_url} | {requires_seed} | {can_unblock} | {fallback} | {instruction} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    action=row.get("resolution_action_type") or "",
                    priority=row.get("resolution_priority") or "",
                    status=row.get("resolution_status") or "",
                    blocker=row.get("primary_blocker") or "",
                    requires_url=row.get("requires_exact_document_url"),
                    requires_seed=row.get("requires_official_seed_review"),
                    can_unblock=row.get("can_unblock_extraction_if_completed"),
                    fallback=fallback.replace("|", "/"),
                    instruction=str(row.get("operator_instruction") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "### Manual Input Instructions",
            "",
            "- Fill only `operator_fill_*` columns in a copied template.",
            "- Do not paste landing pages as exact document URLs.",
            "- Do not copy historical fallback URLs into `operator_fill_exact_document_url`.",
            "- This pack is not applied automatically; validation/application is a future step.",
            "",
            "### Safety Notes",
            "",
            "- Historical fallback remains diagnostic-only and never target-period evidence.",
            "- Extraction/import/scoring/paper-trading permissions are not changed by this pack.",
            "",
        ]
    )
    return lines


def _render_operator_resolution_validation_sections(report: dict[str, Any]) -> list[str]:
    rows = report.get("validation_rows") or []
    lines = [
        "## Validation Summary",
        "",
        f"- row count: {report.get('operator_resolution_validation_row_count', len(rows))}",
        f"- valid: {report.get('operator_resolution_validation_valid_count', 0)}",
        f"- incomplete: {report.get('operator_resolution_validation_incomplete_count', 0)}",
        f"- invalid: {report.get('operator_resolution_validation_invalid_count', 0)}",
        f"- future intake review: {report.get('operator_resolution_validation_future_intake_review_count', 0)}",
        f"- historical fallback rejected: {report.get('operator_resolution_validation_historical_fallback_rejected_count', 0)}",
        "",
        "### Validation Status Counts",
        "",
    ]
    status_counts = report.get("operator_resolution_validation_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Validation Error Counts", ""])
    error_counts = report.get("operator_resolution_validation_error_counts") or {}
    if error_counts:
        lines.extend(f"- {key}: {value}" for key, value in error_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Row Validation",
            "",
            "| Company | Decision | URL | Validation | Severity | Period | Type | Standard | Future intake review | Next step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {company} | {decision} | {url} | {status} | {severity} | {period} | {report_type} | {standard} | {future} | {next_step} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    decision=str(row.get("operator_fill_decision") or "").replace("|", "/"),
                    url=str(row.get("operator_fill_exact_document_url") or "").replace("|", "/"),
                    status=row.get("validation_status") or "",
                    severity=row.get("validation_severity") or "",
                    period=row.get("document_period_status") or "",
                    report_type=row.get("report_type_match_status") or "",
                    standard=row.get("accounting_standard_match_status") or "",
                    future=row.get("can_use_for_future_intake_review"),
                    next_step=str(row.get("operator_next_step") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("|  |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_operator_resolution_apply_preview_sections(report: dict[str, Any]) -> list[str]:
    rows = report.get("patch_rows") or []
    lines = [
        "## Apply Preview Summary",
        "",
        f"- row count: {report.get('operator_resolution_apply_preview_row_count', len(rows))}",
        f"- candidates: {report.get('operator_resolution_apply_preview_candidate_count', 0)}",
        f"- eligible: {report.get('operator_resolution_apply_preview_eligible_count', 0)}",
        f"- blocked: {report.get('operator_resolution_apply_preview_blocked_count', 0)}",
        f"- future apply allowed: {report.get('operator_resolution_apply_preview_future_apply_allowed_count', 0)}",
        "",
        "### Apply Preview Status Counts",
        "",
    ]
    status_counts = report.get("operator_resolution_apply_preview_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Apply Preview Action Counts", ""])
    action_counts = report.get("operator_resolution_apply_preview_action_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Patch Rows",
            "",
            "| Company | Validation | Patch status | Patch action | Future apply | Proposed URL | Intake target | Next step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {company} | {validation} | {status} | {action} | {future} | {url} | {intake} | {next_step} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    validation=row.get("source_validation_status") or "",
                    status=row.get("patch_status") or "",
                    action=row.get("patch_action") or "",
                    future=row.get("future_apply_allowed"),
                    url=str(row.get("proposed_document_url") or "").replace("|", "/"),
                    intake=row.get("intake_target_status") or "",
                    next_step=str(row.get("operator_next_step") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("|  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_operator_resolution_apply_draft_sections(report: dict[str, Any]) -> list[str]:
    rows = report.get("apply_draft_rows") or []
    lines = [
        "## Apply Draft Summary",
        "",
        f"- row count: {report.get('operator_resolution_apply_draft_row_count', len(rows))}",
        f"- applied: {report.get('operator_resolution_apply_draft_applied_count', 0)}",
        f"- skipped: {report.get('operator_resolution_apply_draft_skipped_count', 0)}",
        f"- failed: {report.get('operator_resolution_apply_draft_failed_count', 0)}",
        f"- output draft rows: {report.get('operator_resolution_apply_draft_output_row_count', 0)}",
        "",
        "### Apply Draft Status Counts",
        "",
    ]
    status_counts = report.get("operator_resolution_apply_draft_status_counts") or {}
    if status_counts:
        lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Apply Draft Action Counts", ""])
    action_counts = report.get("operator_resolution_apply_draft_action_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Apply Draft Rows",
            "",
            "| Company | Patch status | Draft status | Draft action | Draft URL | Matched intake | Draft changed | Next step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {company} | {patch_status} | {draft_status} | {draft_action} | {url} | {matched} | {changed} | {next_step} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    patch_status=row.get("source_patch_status") or "",
                    draft_status=row.get("apply_draft_status") or "",
                    draft_action=row.get("apply_draft_action") or "",
                    url=str(row.get("draft_document_url") or "").replace("|", "/"),
                    matched=row.get("matched_intake_status") or "",
                    changed=row.get("would_change_draft_file"),
                    next_step=str(row.get("operator_next_step") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("|  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_document_intake_draft_gate_preview_sections(report: dict[str, Any]) -> list[str]:
    rows = report.get("draft_gate_summary_rows") or []
    validation = report.get("document_intake_draft_validation_report") or {}
    gate = report.get("document_intake_draft_quality_gate_report") or {}
    lines = [
        "## Draft Gate Summary",
        "",
        f"- row count: {report.get('document_intake_draft_gate_preview_row_count', len(rows))}",
        f"- ready: {report.get('document_intake_draft_gate_preview_ready_count', 0)}",
        f"- blocked: {report.get('document_intake_draft_gate_preview_blocked_count', 0)}",
        f"- placeholders: {report.get('document_intake_draft_gate_preview_placeholder_count', 0)}",
        f"- invalid: {report.get('document_intake_draft_gate_preview_invalid_count', 0)}",
        f"- gate passed: {report.get('document_intake_draft_gate_preview_gate_passed')}",
        f"- ready for value extraction: {report.get('document_intake_draft_gate_preview_ready_for_value_extraction')}",
        f"- ready for import: {report.get('document_intake_draft_gate_preview_ready_for_import')}",
        f"- draft validation status: `{validation.get('status')}`",
        f"- quality gate status: `{gate.get('status')}`",
        "",
        "### Draft Gate Status Counts",
        "",
    ]
    status_counts = report.get("document_intake_draft_gate_preview_status_counts") or {}
    lines.extend(f"- {key}: {value}" for key, value in status_counts.items())
    if not status_counts:
        lines.append("- none")
    lines.extend(["", "### Draft Gate Blocker Counts", ""])
    blocker_counts = report.get("document_intake_draft_gate_preview_blocker_counts") or {}
    lines.extend(f"- {key}: {value}" for key, value in blocker_counts.items())
    if not blocker_counts:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Draft Gate Rows",
            "",
            "| Company | Draft status | URL present | Validation | Gate | Ready extraction | Ready import | Blockers | Next action |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {company} | {status} | {url} | {validation} | {gate} | {extraction} | {import_ready} | {blockers} | {next_action} |".format(
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    status=row.get("draft_row_status") or "",
                    url=row.get("has_document_url"),
                    validation=row.get("validation_status") or "",
                    gate=row.get("gate_status") or "",
                    extraction=row.get("ready_for_value_extraction"),
                    import_ready=row.get("ready_for_import"),
                    blockers=_csv_value(row.get("blocked_reason_codes")).replace("|", "/"),
                    next_action=str(row.get("next_required_action") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("|  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


def _render_operator_review_queue_sections(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Operator Review Action Queue",
        "",
        f"- queue actions: {summary.get('operator_review_queue_count', len(rows))}",
        f"- blocking actions: {summary.get('operator_review_queue_blocking_count', 0)}",
        f"- manual actions: {summary.get('operator_review_queue_manual_action_count', 0)}",
        f"- wait actions: {summary.get('operator_review_queue_wait_action_count', 0)}",
        f"- no-op actions: {summary.get('operator_review_queue_noop_count', 0)}",
        "",
        "### Queue Priority Counts",
        "",
    ]
    priority_counts = summary.get("operator_review_queue_priority_counts") or {}
    if priority_counts:
        lines.extend(f"- {key}: {value}" for key, value in priority_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "### Queue Action Type Counts", ""])
    action_counts = summary.get("operator_review_queue_action_type_counts") or {}
    if action_counts:
        lines.extend(f"- {key}: {value}" for key, value in action_counts.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Per-Action Queue",
            "",
            "| Priority | Status | Company | Target | Deadline | Action Type | Blocking | Blocked Stage | Instruction | Next Step |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                "| {priority} | {status} | {company} | {target} {report_type} {standard} | {deadline} | {action_type} | {blocking} | {blocked_stage} | {instruction} | {next_step} |".format(
                    priority=row.get("queue_priority") or "",
                    status=row.get("queue_status") or "",
                    company=str(row.get("company_name") or row.get("company_id") or "").replace("|", "/"),
                    target=row.get("target_reporting_period") or "",
                    report_type=row.get("required_report_type") or "",
                    standard=row.get("required_standard") or "",
                    deadline=row.get("deadline_status") or "",
                    action_type=row.get("queue_action_type") or "",
                    blocking=row.get("is_blocking_next_stage"),
                    blocked_stage=row.get("blocked_stage") or "",
                    instruction=str(row.get("operator_instruction") or "").replace("|", "/"),
                    next_step=str(row.get("recommended_next_step") or "").replace("|", "/"),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |  |  |")
    lines.append("")
    return lines


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


def _render_exact_document_from_seeds_markdown_sections(report: dict[str, Any]) -> list[str]:
    fill = report.get("document_intake_fill_report") or {}
    validation = report.get("document_intake_validation_report") or {}
    gate = report.get("document_quality_gate_report") or {}
    lines = [
        "## Reviewed Seeds Used",
        "",
        "| Company ID | Company | Seed Type | Seed URL | Seed Status | Confidence | Source |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    rows = 0
    for seed in report.get("reviewed_seeds_used") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {seed_type} | {seed_url} | {seed_status} | {confidence} | {source} |".format(
                company_id=seed.get("company_id") or "",
                company_name=str(seed.get("company_name") or "").replace("|", "/"),
                seed_type=seed.get("seed_type") or "",
                seed_url=str(seed.get("seed_url") or "").replace("|", "/"),
                seed_status=seed.get("seed_status") or "",
                confidence=seed.get("confidence") or "",
                source=seed.get("source") or "",
            )
        )
    if rows == 0:
        lines.append("|  |  |  | No reviewed seeds used |  |  |  |")
    lines.extend(
        [
            "",
            "## Document Kind Summary",
            "",
            f"- exact_report_document_count: {report.get('exact_report_document_count', 0)}",
            f"- category_page_count: {report.get('category_page_count', 0)}",
            f"- legal_policy_document_count: {report.get('legal_policy_document_count', 0)}",
            f"- privacy_policy_document_count: {report.get('privacy_policy_document_count', 0)}",
            f"- cookie_policy_document_count: {report.get('cookie_policy_document_count', 0)}",
            f"- user_agreement_document_count: {report.get('user_agreement_document_count', 0)}",
            f"- presentation_document_count: {report.get('presentation_document_count', 0)}",
            f"- prospectus_document_count: {report.get('prospectus_document_count', 0)}",
            f"- quarterly_or_interim_document_count: {report.get('quarterly_or_interim_document_count', 0)}",
            f"- generic_navigation_page_count: {report.get('generic_navigation_page_count', 0)}",
            "",
            "## Period/Type/Standard Summary",
            "",
            f"- target_period_document_count: {report.get('target_period_document_count', 0)}",
            f"- wrong_period_document_count: {report.get('wrong_period_document_count', 0)}",
            f"- unknown_period_document_count: {report.get('unknown_period_document_count', 0)}",
            f"- period_conflict_document_count: {report.get('period_conflict_document_count', 0)}",
            f"- annual_match_document_count: {report.get('annual_match_document_count', 0)}",
            f"- interim_or_quarterly_document_count: {report.get('interim_or_quarterly_document_count', 0)}",
            f"- unknown_report_type_document_count: {report.get('unknown_report_type_document_count', 0)}",
            f"- standard_match_document_count: {report.get('standard_match_document_count', 0)}",
            f"- standard_mismatch_document_count: {report.get('standard_mismatch_document_count', 0)}",
            f"- unknown_standard_document_count: {report.get('unknown_standard_document_count', 0)}",
            f"- kept_target_period_document_count: {report.get('kept_target_period_document_count', 0)}",
            f"- kept_fallback_document_count: {report.get('kept_fallback_document_count', 0)}",
            "",
        ]
    )
    lines.extend(
        _render_availability_operator_view_sections(
            report.get("availability_operator_summary") or {},
            report.get("availability_operator_rows") or [],
        )
    )
    lines.extend(
        _render_operator_review_queue_sections(
            report.get("operator_review_queue_summary") or {},
            report.get("operator_review_queue") or [],
        )
    )
    lines.extend(
        _render_official_source_coverage_sections(
            report.get("official_source_coverage_summary") or {},
            report.get("official_source_coverage_rows") or [],
        )
    )
    lines.extend(
        _render_historical_fallback_registry_sections(
            report.get("historical_fallback_registry_summary") or {},
            report.get("historical_fallback_registry_rows") or [],
        )
    )
    lines.extend(
        _render_reporting_readiness_matrix_sections(
            report.get("reporting_readiness_summary") or {},
            report.get("reporting_readiness_rows") or [],
        )
    )
    lines.extend(
        _render_operator_resolution_pack_sections(
            report.get("operator_resolution_pack_summary") or {},
            report.get("operator_resolution_pack_rows") or [],
        )
    )
    lines.extend(
        [
            "## Availability Policy",
            "",
            "| Company ID | Company | Target | Status | Deadline | Reasons | Exact Target Docs | Historical Annual IFRS | Interim/Quarterly | Wrong Standard | Placeholder | Operator Review | Primary Deadline | Expected Availability | Current Date | After Primary | Within Grace | After Grace | Fallback Scope | Target Evidence | Operator Action |",
            "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    availability_rows = 0
    for item in report.get("target_reporting_period_availability") or []:
        availability_rows += 1
        policy = item.get("reporting_window_policy") or {}
        lines.append(
            "| {company_id} | {company_name} | {target} {report_type} {standard} | {status} | {deadline} | {reasons} | {exact_count} | {historical_count} | {interim_count} | {wrong_standard_count} | {placeholder_count} | {operator_count} | {primary} | {expected} | {current} | {after_primary} | {within} | {after_grace} | {fallback} | {evidence} | {action} |".format(
                company_id=item.get("company_id") or "",
                company_name=str(item.get("company_name") or "").replace("|", "/"),
                target=item.get("target_reporting_period") or "",
                report_type=item.get("required_report_type") or "",
                standard=item.get("required_standard") or "",
                status=item.get("availability_status") or "",
                deadline=policy.get("deadline_status") or "",
                reasons=_csv_value(item.get("availability_reason_codes")).replace("|", "/"),
                exact_count=item.get("exact_target_period_document_count", 0),
                historical_count=item.get("historical_annual_ifrs_document_count", 0),
                interim_count=item.get("interim_or_quarterly_document_count", 0),
                wrong_standard_count=item.get("wrong_standard_document_count", 0),
                placeholder_count=item.get("placeholder_not_found_count", 0),
                operator_count=item.get("operator_review_required_count", 0),
                primary=policy.get("primary_expected_deadline_date") or "",
                expected=policy.get("expected_availability_date") or "",
                current=policy.get("current_date") or "",
                after_primary=policy.get("after_primary_deadline"),
                within=policy.get("within_conservative_grace_window", policy.get("within_grace_window")),
                after_grace=policy.get("after_conservative_grace_window"),
                fallback=item.get("historical_fallback_scope") or "none",
                evidence=item.get("can_use_as_target_period_evidence"),
                action=item.get("operator_action") or "",
            )
        )
    if availability_rows == 0:
        lines.append("|  |  |  | No availability policy rows |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Category Pages Followed",
            "",
            "| Company ID | Company | Category URL | Title | Kind | Depth | Followed |",
            "| ---: | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    category_rows = 0
    for document in report.get("category_pages_followed") or []:
        category_rows += 1
        lines.append(
            "| {company_id} | {company_name} | {url} | {title} | {kind} | {depth} | {followed} |".format(
                company_id=document.get("company_id") or "",
                company_name=str(document.get("company_name") or "").replace("|", "/"),
                url=str(document.get("document_url") or "").replace("|", "/"),
                title=str(document.get("document_title") or "").replace("|", "/"),
                kind=document.get("document_kind") or "",
                depth=document.get("crawl_depth") or "",
                followed=document.get("category_followed"),
            )
        )
    if category_rows == 0:
        lines.append("|  |  | None |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Exact Document Candidates",
            "",
            "| Company ID | Company | Rank | URL | Title | Kind | Period | Type | Standard | Depth | Parent | Score | Confidence | Operator Status | Document Status | Reasons | Negative Reasons |",
            "| ---: | --- | ---: | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    rows = 0
    for document in report.get("documents") or []:
        rows += 1
        lines.append(
            "| {company_id} | {company_name} | {rank} | {url} | {title} | {kind} | {period} | {type_status} | {standard} | {depth} | {parent} | {score} | {confidence} | {operator_status} | {document_status} | {reasons} | {negative} |".format(
                company_id=document.get("company_id") or "",
                company_name=str(document.get("company_name") or "").replace("|", "/"),
                rank=document.get("candidate_rank") or "",
                url=str(document.get("document_url") or "").replace("|", "/"),
                title=str(document.get("document_title") or "").replace("|", "/"),
                kind=document.get("document_kind") or "",
                period=f"{document.get('document_period_year') or ''} {document.get('document_period_status') or ''}".strip(),
                type_status=document.get("report_type_match_status") or "",
                standard=document.get("accounting_standard_match_status") or "",
                depth=document.get("crawl_depth") or "",
                parent=str(document.get("parent_seed_url") or "").replace("|", "/"),
                score=document.get("candidate_score") or 0,
                confidence=document.get("candidate_confidence") or "",
                operator_status=document.get("operator_review_status") or "",
                document_status=document.get("document_status") or "",
                reasons=_csv_value(document.get("score_reasons")),
                negative=_csv_value(document.get("negative_reasons")),
            )
        )
    if rows == 0:
        lines.append("|  |  |  | No candidates |  |  |  |  |  |  |  |  |  |  |  |  |  |")
    wrong_period_documents = [
        document
        for document in report.get("documents") or []
        if document.get("filter_status") in {"filtered_wrong_period", "filtered_wrong_report_type", "filtered_wrong_standard", "filtered_unknown_period"}
    ]
    lines.extend(
        [
            "",
            "## Filtered Wrong Period Documents",
            "",
            "| URL | Title | Year | Period Status | Type Status | Standard Status | Filter Status | Filter Reasons |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if wrong_period_documents:
        for document in wrong_period_documents:
            lines.append(
                "| {url} | {title} | {year} | {period} | {type_status} | {standard} | {filter_status} | {reasons} |".format(
                    url=str(document.get("document_url") or "").replace("|", "/"),
                    title=str(document.get("document_title") or "").replace("|", "/"),
                    year=document.get("document_period_year") or "",
                    period=document.get("document_period_status") or "",
                    type_status=document.get("report_type_match_status") or "",
                    standard=document.get("accounting_standard_match_status") or "",
                    filter_status=document.get("filter_status") or "",
                    reasons=_csv_value(document.get("filter_reasons")).replace("|", "/"),
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |  |")
    kept_target_documents = [
        document
        for document in report.get("documents") or []
        if _exact_document_is_downstream_eligible(document)
    ]
    lines.extend(
        [
            "",
            "## Kept Target-Period Documents",
            "",
            "| URL | Title | Year | Type Status | Standard Status | Score | Operator Status |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    if kept_target_documents:
        for document in kept_target_documents:
            lines.append(
                "| {url} | {title} | {year} | {type_status} | {standard} | {score} | {review} |".format(
                    url=str(document.get("document_url") or "").replace("|", "/"),
                    title=str(document.get("document_title") or "").replace("|", "/"),
                    year=document.get("document_period_year") or "",
                    type_status=document.get("report_type_match_status") or "",
                    standard=document.get("accounting_standard_match_status") or "",
                    score=document.get("candidate_score") or 0,
                    review=document.get("operator_review_status") or "",
                )
            )
    else:
        lines.append("| None |  |  |  |  |  |  |")
    fallback_documents = [
        document
        for document in report.get("documents") or []
        if document.get("fallback_status") == "fallback_candidate"
    ]
    if fallback_documents:
        lines.extend(
            [
                "",
                "## Fallback Candidates",
                "",
                "| URL | Title | Year | Filter Status | Operator Status |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for document in fallback_documents:
            lines.append(
                "| {url} | {title} | {year} | {filter_status} | {review} |".format(
                    url=str(document.get("document_url") or "").replace("|", "/"),
                    title=str(document.get("document_title") or "").replace("|", "/"),
                    year=document.get("document_period_year") or "",
                    filter_status=document.get("filter_status") or "",
                    review=document.get("operator_review_status") or "",
                )
            )
    legal_documents = [
        document
        for document in report.get("documents") or []
        if document.get("document_kind") in {
            "legal_policy_document",
            "privacy_policy_document",
            "cookie_policy_document",
            "user_agreement_document",
        }
    ]
    lines.extend(
        [
            "",
            "## Filtered Legal/Policy Documents",
            "",
            "| URL | Title | Kind | Filter Status | Filter Reasons |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if legal_documents:
        for document in legal_documents:
            lines.append(
                "| {url} | {title} | {kind} | {status} | {reasons} |".format(
                    url=str(document.get("document_url") or "").replace("|", "/"),
                    title=str(document.get("document_title") or "").replace("|", "/"),
                    kind=document.get("document_kind") or "",
                    status=document.get("filter_status") or "",
                    reasons=_csv_value(document.get("filter_reasons")).replace("|", "/"),
                )
            )
    else:
        lines.append("| None |  |  |  |  |")
    second_level_documents = [
        document
        for document in report.get("documents") or []
        if int(document.get("crawl_depth") or 0) >= 2 and document.get("document_url")
    ]
    lines.extend(
        [
            "",
            "## Second-Level Candidates",
            "",
            "| Company ID | Company | URL | Title | Kind | Depth | Parent | Score | Operator Status |",
            "| ---: | --- | --- | --- | --- | ---: | --- | ---: | --- |",
        ]
    )
    if second_level_documents:
        for document in second_level_documents:
            lines.append(
                "| {company_id} | {company_name} | {url} | {title} | {kind} | {depth} | {parent} | {score} | {review} |".format(
                    company_id=document.get("company_id") or "",
                    company_name=str(document.get("company_name") or "").replace("|", "/"),
                    url=str(document.get("document_url") or "").replace("|", "/"),
                    title=str(document.get("document_title") or "").replace("|", "/"),
                    kind=document.get("document_kind") or "",
                    depth=document.get("crawl_depth") or "",
                    parent=str(document.get("parent_seed_url") or "").replace("|", "/"),
                    score=document.get("candidate_score") or 0,
                    review=document.get("operator_review_status") or "",
                )
            )
    else:
        lines.append("|  |  | None |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Missing Issuers",
            "",
            "| Company ID | Company | Reason |",
            "| ---: | --- | --- |",
        ]
    )
    missing_rows = 0
    for item in report.get("missing_issuers") or []:
        missing_rows += 1
        lines.append(
            "| {company_id} | {company_name} | {reason} |".format(
                company_id=item.get("company_id") or "",
                company_name=str(item.get("company_name") or "").replace("|", "/"),
                reason=str(item.get("reason") or "").replace("|", "/"),
            )
        )
    if missing_rows == 0:
        lines.append("|  |  | None |")
    lines.extend(
        [
            "",
            "## Ranking And Filtering",
            "",
            f"- candidate_count_before_filter: {report.get('candidate_count_before_filter', 0)}",
            f"- candidate_count_after_filter: {report.get('candidate_count_after_filter', 0)}",
            f"- filtered_candidate_count: {report.get('filtered_candidate_count', 0)}",
            f"- filtered_noise_count: {report.get('filtered_noise_count', 0)}",
            f"- filtered_low_score_count: {report.get('filtered_low_score_count', 0)}",
            f"- filtered_duplicate_count: {report.get('filtered_duplicate_count', 0)}",
            f"- filtered_wrong_document_type_count: {report.get('filtered_wrong_document_type_count', 0)}",
            f"- filtered_wrong_period_count: {report.get('filtered_wrong_period_count', 0)}",
            f"- filtered_wrong_report_type_count: {report.get('filtered_wrong_report_type_count', 0)}",
            f"- filtered_wrong_standard_count: {report.get('filtered_wrong_standard_count', 0)}",
            f"- filtered_unknown_period_count: {report.get('filtered_unknown_period_count', 0)}",
            "",
            "## Integrated Document Intake / Gate",
            "",
            f"- document-intake-fill status: `{fill.get('status')}`",
            f"- document-intake-validate status: `{validation.get('status')}`",
            f"- document-quality-gate status: `{gate.get('status')}`",
            f"- gate_passed: {gate.get('gate_passed')}",
            f"- ready_for_value_extraction: {gate.get('ready_for_value_extraction')}",
            f"- ready_for_import: {gate.get('ready_for_import')}",
            "",
        ]
    )
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


def _exact_document_allowed_domains(args: argparse.Namespace) -> set[str]:
    domains = {domain.casefold() for domain in OFFICIAL_SOURCE_DOMAIN_HINTS}
    domains.update(item.casefold() for item in _split_cli_list(args.exact_document_allowed_domains))
    return domains


def _exact_document_blocked_hints(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *BLOCKED_SOURCE_HINTS,
                *(item.casefold() for item in _split_cli_list(args.exact_document_blocked_domains)),
            ]
        )
    )


def _exact_document_issuer_base(
    required: dict[str, Any],
    *,
    input_documents: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        *_items_matching_required(input_documents, required),
        *_items_matching_required(seed_issuers, required),
    ]
    chosen = candidates[0] if candidates else required
    return {
        "company_id": chosen.get("company_id") or required.get("company_id"),
        "company_name": chosen.get("company_name") or required.get("company_name") or "",
        "canonical_company_id": chosen.get("canonical_company_id") or chosen.get("company_id") or required.get("company_id"),
        "canonical_company_name": chosen.get("canonical_company_name") or chosen.get("company_name") or required.get("company_name") or "",
        "inn": chosen.get("inn") or "",
        "ogrn": chosen.get("ogrn") or "",
    }


def _select_reviewed_exact_document_seeds(
    issuer: dict[str, Any],
    *,
    seed_issuers: list[dict[str, Any]],
    seed_types: set[str],
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    args: argparse.Namespace,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for seed_issuer in _items_matching_required(seed_issuers, issuer):
        for raw_seed in seed_issuer.get("official_seeds") or []:
            seed = dict(raw_seed)
            seed_url = _normalize_candidate_url(str(seed.get("seed_url") or ""))
            seed_type = _normalize_seed_type(seed.get("seed_type"), seed_url)
            base = {
                "company_id": issuer.get("company_id"),
                "company_name": issuer.get("company_name") or "",
                "seed_type": seed_type,
                "seed_url": seed_url,
            }
            forbidden_fields = [
                field
                for field in FORBIDDEN_SEED_METADATA_FIELDS
                if seed.get(field) not in (None, "")
            ]
            if forbidden_fields or seed.get("values") or seed.get("financial_values"):
                errors.append(
                    {
                        **base,
                        "message": "financial values are forbidden in reviewed seed metadata",
                        "fields": forbidden_fields or ["values"],
                    }
                )
                continue
            if not seed_url or seed_type not in seed_types:
                continue
            if seed.get("seed_status") != "valid_seed":
                continue
            if _confidence_rank(seed.get("confidence")) < _confidence_rank("medium"):
                continue
            source = str(seed.get("source") or "")
            reviewed = str(seed.get("operator_review_status") or "").strip() in DOCUMENT_INTAKE_REVIEWED_STATUSES
            review_hint = "reviewed" in " ".join(
                str(seed.get(field) or "")
                for field in ("source", "reason", "notes", "resolution_method")
            ).casefold()
            if source not in EXACT_DOCUMENT_REVIEWED_SEED_SOURCES and not reviewed and not review_hint:
                continue
            classification = _classify_candidate_url(
                seed_url,
                allowed_domains=allowed_domains,
                blocked_hints=blocked_hints,
                allow_unknown_source=False,
            )
            if classification["status"] != "official":
                warnings.append(
                    {
                        **base,
                        "message": "reviewed seed skipped because it is not allowlisted official source",
                        "classification": classification["status"],
                    }
                )
                continue
            if source == "generated_official_path" or _operator_seed_looks_generated(seed):
                warnings.append({**base, "message": "generated/probable seed skipped for exact document discovery"})
                continue
            seed.update(
                {
                    "seed_url": seed_url,
                    "seed_type": seed_type,
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name") or "",
                    "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
                    "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
                    "inn": issuer.get("inn") or "",
                    "ogrn": issuer.get("ogrn") or "",
                }
            )
            candidates.append(seed)
    by_url: dict[str, dict[str, Any]] = {}
    for seed in candidates:
        url = str(seed.get("seed_url") or "")
        existing = by_url.get(url)
        if existing is None or _exact_document_seed_sort_key(seed) > _exact_document_seed_sort_key(existing):
            by_url[url] = seed
    return sorted(by_url.values(), key=_exact_document_seed_sort_key, reverse=True)


def _exact_document_seed_sort_key(seed: dict[str, Any]) -> tuple[int, int, int, str]:
    source_rank = 3 if seed.get("source") == "operator_seed" else 2 if "reviewed" in str(seed.get("source") or "") else 1
    type_rank = {
        "official_disclosure_reports": 5,
        "issuer_reports": 4,
        "official_disclosure_profile": 3,
        "issuer_investor_relations": 2,
    }.get(str(seed.get("seed_type") or ""), 1)
    return (source_rank, type_rank, _confidence_rank(seed.get("confidence")), str(seed.get("seed_url") or ""))


def _exact_document_reviewed_seed_used(issuer: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name") or "",
        "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
        "inn": issuer.get("inn") or "",
        "ogrn": issuer.get("ogrn") or "",
        "seed_type": seed.get("seed_type") or "",
        "seed_url": seed.get("seed_url") or "",
        "seed_status": seed.get("seed_status") or "",
        "confidence": seed.get("confidence") or "",
        "source": seed.get("source") or "",
    }


def classify_exact_document_kind(document_url: str, title: str, *, args: argparse.Namespace) -> str:
    text = f"{document_url} {title}".casefold()
    path = urllib.parse.urlparse(document_url).path.casefold()
    file_name = _file_name_from_url(document_url).casefold()
    combined = f"{text} {path} {file_name}"
    if args.exact_document_filter_policy_documents and _contains_any(combined, ("cookie", "cookies", "\u043a\u0443\u043a\u0438")):
        return "cookie_policy_document"
    if args.exact_document_filter_policy_documents and _contains_any(combined, ("privacy", "policy_conf", "confidential", "\u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446")):
        return "privacy_policy_document"
    if args.exact_document_filter_legal_documents and _contains_any(combined, ("user_agreement", "terms", "agreement", "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u043e\u0435 \u0441\u043e\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u0435", "\u0443\u0441\u043b\u043e\u0432\u0438\u044f")):
        return "user_agreement_document"
    if args.exact_document_filter_legal_documents and _contains_any(combined, EXACT_DOCUMENT_LEGAL_POLICY_TERMS):
        return "legal_policy_document"
    if _contains_any(combined, ("presentation", "\u043f\u0440\u0435\u0437\u0435\u043d\u0442")):
        return "presentation_document"
    if _contains_any(combined, ("prospectus", "emission", "bond terms", "\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442", "\u044d\u043c\u0438\u0441\u0441")):
        return "prospectus_document"
    if _contains_any(combined, ("quarter", "quarterly", "q1", "q2", "q3", "q4", "1q", "2q", "3q", "4q", "\u043a\u0432\u0430\u0440\u0442\u0430\u043b", "6 \u043c\u0435\u0441\u044f\u0446\u0435\u0432", "9 \u043c\u0435\u0441\u044f\u0446\u0435\u0432", "\u043f\u0440\u043e\u043c\u0435\u0436\u0443\u0442")):
        return "quarterly_or_interim_document"
    if _contains_any(combined, ("press", "news", "\u043d\u043e\u0432\u043e\u0441")):
        return "news_or_press_document"
    if _contains_any(combined, ("financial-results", "financial_results", "financial results", "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u044b\u0435 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442", "\u043c\u0441\u0444\u043e")) and not _url_is_pdf(document_url):
        return "financial_results_page"
    if _contains_any(combined, ("accounting-statements", "accounting_statements", "\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0441\u043a", "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0435\u0442\u043d\u043e\u0441\u0442\u044c", "\u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u0430\u044f \u043e\u0442\u0447\u0451\u0442\u043d\u043e\u0441\u0442\u044c")) and not _url_is_pdf(document_url):
        return "accounting_statements_page"
    if _contains_any(combined, ("issuer-reports", "annual reports", "\u0433\u043e\u0434\u043e\u0432\u044b\u0435 \u043e\u0442\u0447\u0435\u0442", "\u0433\u043e\u0434\u043e\u0432\u044b\u0435 \u043e\u0442\u0447\u0451\u0442", "\u044d\u043c\u0438\u0442\u0435\u043d\u0442")) and not _url_is_pdf(document_url):
        return "report_category_page"
    if _contains_any(combined, ("disclosure", "messages", "\u0440\u0430\u0441\u043a\u0440\u044b\u0442\u0438\u0435", "\u0441\u043e\u043e\u0431\u0449\u0435\u043d")) and not _url_is_pdf(document_url):
        return "disclosure_category_page"
    if _exact_document_has_strong_signals(document_url, title, args) or (
        _url_is_pdf(document_url)
        and _contains_any(combined, ("annual", "ifrs", "\u043c\u0441\u0444\u043e", "financial", "statements", "\u0433\u043e\u0434\u043e\u0432", "\u0444\u0438\u043d\u0430\u043d\u0441"))
    ):
        return "exact_report_document"
    if _url_is_pdf(document_url) and args.exact_document_filter_generic_pdfs:
        return "generic_navigation_page"
    if _exact_document_is_generic_page(document_url, title, args):
        return "generic_navigation_page"
    return "unknown_document"


def _exact_document_category_page_types(args: argparse.Namespace) -> set[str]:
    mapping = {
        "issuer_reports": "report_category_page",
        "report_category_page": "report_category_page",
        "accounting_statements": "accounting_statements_page",
        "accounting_statements_page": "accounting_statements_page",
        "financial_results": "financial_results_page",
        "financial_results_page": "financial_results_page",
        "disclosure_reports": "disclosure_category_page",
        "disclosure_category_page": "disclosure_category_page",
    }
    values: set[str] = set()
    for item in _split_cli_list(args.exact_document_category_page_types):
        kind = mapping.get(item.casefold(), item.casefold())
        if kind in EXACT_DOCUMENT_CATEGORY_KINDS:
            values.add(kind)
    return values or set(EXACT_DOCUMENT_CATEGORY_KINDS)


def classify_exact_document_period(
    document_url: str,
    title: str,
    source_page_url: str,
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    text = _exact_document_evidence_text(document_url, title)
    target = str(getattr(args, "report_period", "") or "")
    years = _extract_exact_document_years(text)
    evidence: list[str] = []
    period_year = ""
    status = "unknown_period"
    confidence = "low"
    fallback_status = "not_fallback"
    if years:
        evidence.extend(f"year:{year}" for year in years)
        if target and target in years:
            period_year = target
            status = "target_period"
            confidence = "high" if len(years) == 1 else "medium"
        elif len(years) > 1:
            period_year = ",".join(years)
            status = "period_conflict"
            confidence = "low"
        else:
            period_year = years[0]
            if _exact_document_prior_year_allowed(period_year, args):
                status = "prior_period_fallback_candidate"
                fallback_status = "fallback_candidate"
                confidence = "medium"
            else:
                status = "wrong_period"
                confidence = "high"
    quarter = _infer_exact_document_period_quarter(text)
    if quarter:
        evidence.append(f"period_marker:{quarter}")
        if not period_year and target and status == "unknown_period":
            confidence = "medium"
    if not years and target and str(getattr(args, "exact_document_period_policy", "")) == "diagnostic-all":
        fallback_status = "not_fallback"
    return {
        "document_period_year": period_year,
        "document_period_quarter": quarter,
        "document_period_status": status,
        "period_confidence": confidence,
        "period_evidence": evidence,
        "fallback_status": fallback_status,
    }


def classify_exact_document_report_type(
    document_url: str,
    title: str,
    *,
    args: argparse.Namespace,
    period_quarter: str = "",
) -> dict[str, Any]:
    text = _exact_document_evidence_text(document_url, title)
    evidence: list[str] = []
    interim = _contains_any(text, EXACT_DOCUMENT_INTERIM_TERMS) or period_quarter in {"Q1", "Q2", "Q3", "Q4", "H1", "1H", "6M", "9M"}
    annual = _contains_any(text, EXACT_DOCUMENT_ANNUAL_TERMS) or period_quarter in {"FY", "0"}
    if re.search(r"(?:20\d{2})[_-]12(?:\D|$)", text):
        annual = True
        evidence.append("year_month_12")
    if re.search(r"(?:31[.\-/]?12|12[.\-/]?31)", text):
        annual = True
        evidence.append("year_end_date")
    if interim:
        evidence.append("interim_or_quarterly_signal")
    if annual:
        evidence.append("annual_signal")
    if str(getattr(args, "report_type", "") or "").casefold() == "annual":
        if interim:
            status = "interim_or_quarterly_mismatch"
        elif annual:
            status = "annual_match"
        else:
            status = "unknown_report_type"
    else:
        status = "unknown_report_type"
    return {"report_type_match_status": status, "type_evidence": evidence}


def classify_exact_document_accounting_standard(
    document_url: str,
    title: str,
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    text = _exact_document_evidence_text(document_url, title)
    evidence: list[str] = []
    target = str(getattr(args, "accounting_standard", "") or "").casefold()
    ifrs = _contains_any(text, EXACT_DOCUMENT_IFRS_TERMS)
    ras = _contains_any(text, EXACT_DOCUMENT_RAS_TERMS)
    if ifrs:
        evidence.append("ifrs_signal")
    if ras:
        evidence.append("ras_or_standalone_signal")
    if target == "ifrs":
        if ifrs:
            status = "standard_match"
        elif ras:
            status = "standard_mismatch"
        else:
            status = "unknown_standard"
    else:
        status = "unknown_standard"
    return {"accounting_standard_match_status": status, "standard_evidence": evidence}


def _exact_document_evidence_text(document_url: str, title: str) -> str:
    path = urllib.parse.urlparse(document_url).path
    file_name = _file_name_from_url(document_url)
    return f"{document_url} {path} {file_name} {title}".casefold()


def _extract_exact_document_years(text: str) -> list[str]:
    years: set[str] = set()
    for match in re.finditer(r"(?<!\d)(20\d{2})(?!\d)", text):
        years.add(match.group(1))
    for match in re.finditer(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-3]\d)(?!\d)", text):
        years.add(match.group(1))
    for match in re.finditer(r"(?<!\d)([0-3]\d)(0[1-9]|1[0-2])(20\d{2})(?!\d)", text):
        years.add(match.group(3))
    for match in re.finditer(r"(?<!\d)([0-3]?\d)[.\-/](0?[1-9]|1[0-2])[.\-/](20\d{2})(?!\d)", text):
        years.add(match.group(3))
    return sorted(years)


def _infer_exact_document_period_quarter(text: str) -> str:
    if re.search(r"(?<![a-z0-9])(?:q1|1q)(?![a-z0-9])", text) or re.search(r"(?:31[.\-/]?03|0331|3103)", text):
        return "Q1"
    if re.search(r"(?<![a-z0-9])(?:q2|2q)(?![a-z0-9])", text):
        return "Q2"
    if re.search(r"(?<![a-z0-9])(?:q3|3q)(?![a-z0-9])", text) or re.search(r"(?:30[.\-/]?09|0930|3009)", text):
        return "Q3"
    if re.search(r"(?<![a-z0-9])(?:q4|4q)(?![a-z0-9])", text):
        return "Q4"
    if re.search(r"(?<![a-z0-9])(?:1h|h1|6m)(?![a-z0-9])", text) or re.search(r"(?:30[.\-/]?06|0630|3006)", text):
        return "H1"
    if re.search(r"(?<![a-z0-9])9m(?![a-z0-9])", text):
        return "9M"
    if re.search(r"(?:31[.\-/]?12|1231|3112)", text) or re.search(r"(?:20\d{2})[_-]12(?:\D|$)", text):
        return "FY"
    if _contains_any(text, ("12m", "12 months", "12 \u043c\u0435\u0441\u044f\u0446\u0435\u0432")):
        return "FY"
    return ""


def _exact_document_prior_year_allowed(period_year: str, args: argparse.Namespace) -> bool:
    if not args.exact_document_allow_prior_year_fallback:
        return False
    if str(args.exact_document_period_policy) != "target-or-prior-year-fallback":
        return False
    try:
        target = int(str(args.report_period))
        year = int(str(period_year))
    except (TypeError, ValueError):
        return False
    gap = target - year
    return 0 < gap <= max(int(args.exact_document_max_prior_year_gap or 0), 0)


def _parse_exact_document_availability_current_date(args: argparse.Namespace) -> date:
    value = str(getattr(args, "exact_document_availability_current_date", "") or "").strip()
    if value:
        return date.fromisoformat(value)
    return date.today()


def _exact_document_target_period_end_date(args: argparse.Namespace) -> date | None:
    try:
        year = int(str(getattr(args, "report_period", "") or ""))
    except (TypeError, ValueError):
        return None
    return date(year, 12, 31)


def _exact_document_reporting_window_policy(args: argparse.Namespace) -> dict[str, Any]:
    primary_days = max(int(getattr(args, "exact_document_annual_ifrs_primary_deadline_days", 120) or 0), 0)
    grace_days = max(int(getattr(args, "exact_document_annual_ifrs_grace_days", 180) or 0), 0)
    current_date = _parse_exact_document_availability_current_date(args)
    period_end = _exact_document_target_period_end_date(args)
    if period_end is None:
        primary_expected = None
        expected = None
        before_primary = False
        after_primary = False
        within = False
        after_grace = False
        deadline_status = "unknown_deadline"
    else:
        primary_expected = period_end + timedelta(days=primary_days)
        expected = period_end + timedelta(days=grace_days)
        before_primary = current_date <= primary_expected
        after_primary = current_date > primary_expected
        within = current_date <= expected
        after_grace = current_date > expected
        if before_primary:
            deadline_status = "before_primary_deadline"
        elif within:
            deadline_status = "after_primary_deadline_within_grace_window"
        else:
            deadline_status = "after_conservative_grace_window"
    return {
        "policy_name": getattr(args, "exact_document_availability_policy_name", "annual_ifrs_deadline_aware_grace_window"),
        "target_period_end_date": period_end.isoformat() if period_end is not None else "",
        "primary_deadline_days": primary_days,
        "primary_expected_deadline_date": primary_expected.isoformat() if primary_expected is not None else "",
        "grace_days": grace_days,
        "expected_availability_date": expected.isoformat() if expected is not None else "",
        "current_date": current_date.isoformat(),
        "before_primary_deadline": before_primary,
        "after_primary_deadline": after_primary,
        "within_grace_window": within,
        "within_conservative_grace_window": within,
        "after_grace_window": after_grace,
        "after_conservative_grace_window": after_grace,
        "deadline_status": deadline_status,
        "policy_inference": "Configurable reporting availability policy; not an official non-publication statement.",
    }


def _exact_document_int_year(value: Any) -> int | None:
    text = str(value or "").strip()
    if re.fullmatch(r"20\d{2}", text):
        return int(text)
    return None


def _exact_document_unique_url_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("document_url") or "")
        if not url:
            continue
        key = _normalized_operator_seed_candidate_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _exact_document_is_historical_annual_ifrs(document: dict[str, Any], args: argparse.Namespace) -> bool:
    year = _exact_document_int_year(document.get("document_period_year"))
    target = _exact_document_int_year(getattr(args, "report_period", ""))
    return bool(
        document.get("document_kind") == "exact_report_document"
        and year is not None
        and target is not None
        and year < target
        and document.get("report_type_match_status") == "annual_match"
        and document.get("accounting_standard_match_status") == "standard_match"
    )


def _exact_document_is_interim_or_quarterly(document: dict[str, Any]) -> bool:
    return bool(
        document.get("document_kind") == "quarterly_or_interim_document"
        or document.get("report_type_match_status") == "interim_or_quarterly_mismatch"
        or document.get("filter_status") == "filtered_wrong_report_type"
    )


def _exact_document_is_wrong_standard_candidate(document: dict[str, Any]) -> bool:
    return bool(
        document.get("document_kind") == "exact_report_document"
        and document.get("document_period_status") == "target_period"
        and document.get("accounting_standard_match_status") == "standard_mismatch"
    )


def _exact_document_requires_operator_review_for_availability(document: dict[str, Any]) -> bool:
    if document.get("document_kind") != "exact_report_document":
        return False
    if document.get("document_period_status") != "target_period":
        return False
    if document.get("report_type_match_status") == "interim_or_quarterly_mismatch":
        return False
    if document.get("accounting_standard_match_status") == "standard_mismatch":
        return False
    if _exact_document_is_downstream_eligible(document):
        return False
    return bool(
        document.get("operator_review_status") == "needs_operator_review"
        or document.get("report_type_match_status") in {"unknown_report_type", "report_type_conflict"}
        or document.get("accounting_standard_match_status") == "unknown_standard"
        or document.get("document_status") in {"needs_operator_review", "filtered_document"}
    )


def _exact_document_latest_historical(items: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any] | None:
    historical = [item for item in items if _exact_document_is_historical_annual_ifrs(item, args)]
    if not historical:
        return None
    return sorted(
        historical,
        key=lambda item: (
            _exact_document_int_year(item.get("document_period_year")) or 0,
            int(item.get("candidate_score") or item.get("final_score") or 0),
            str(item.get("document_url") or ""),
            str(item.get("document_title") or ""),
        ),
        reverse=True,
    )[0]


def _exact_document_availability_operator_action(status: str) -> str:
    return {
        "exact_target_period_document_found": "proceed_to_strict_quality_gate",
        "target_period_document_not_found": "continue_official_source_discovery",
        "target_period_likely_not_yet_published_by_policy_window": "wait_for_target_period_publication_or_review_official_sources",
        "target_period_likely_not_yet_published_before_primary_deadline": "wait_until_primary_deadline_or_monitor_publication",
        "target_period_not_found_after_primary_deadline_within_grace_window": "review_official_sources_or_wait_until_conservative_grace_date",
        "target_period_not_found_after_conservative_grace_window": "escalate_missing_target_report_and_review_source_coverage",
        "only_historical_annual_ifrs_available": "review_historical_diagnostic_only_or_wait_for_target_period",
        "only_interim_or_quarterly_available": "do_not_use_interim_for_annual_target_period",
        "only_wrong_standard_available": "find_ifrs_annual_report",
        "operator_exact_document_review_required": "review_exact_document_candidate",
        "placeholder_not_found": "operator_to_find_official_exact_document",
        "no_usable_official_report_candidates": "continue_official_source_discovery",
    }.get(status, "continue_official_source_discovery")


def _exact_document_availability_reason_codes(
    status: str,
    *,
    exact_target_count: int,
    target_period_count: int,
    historical_count: int,
    interim_count: int,
    wrong_standard_count: int,
    placeholder_count: int,
    operator_review_count: int,
    before_primary_deadline: bool,
    after_primary_deadline: bool,
    within_grace_window: bool,
    after_conservative_grace_window: bool,
) -> list[str]:
    reasons: list[str] = []
    if exact_target_count == 0:
        reasons.append("exact_target_period_document_not_found")
    if target_period_count == 0:
        reasons.append("target_period_document_not_found")
    if before_primary_deadline:
        reasons.append("before_primary_deadline")
    if after_primary_deadline:
        reasons.extend(["after_primary_deadline", "primary_deadline_passed"])
    if within_grace_window:
        reasons.append("within_conservative_grace_window")
    if after_conservative_grace_window:
        reasons.append("after_conservative_grace_window")
    if exact_target_count == 0 and after_primary_deadline:
        reasons.append("target_period_missing_after_primary_deadline")
    if exact_target_count == 0 and after_conservative_grace_window:
        reasons.append("target_period_missing_after_conservative_grace")
    if historical_count:
        reasons.append("historical_annual_ifrs_available")
    if interim_count:
        reasons.append("interim_or_quarterly_available")
    if wrong_standard_count:
        reasons.append("wrong_standard_available")
    if placeholder_count:
        reasons.append("placeholder_not_found")
    if operator_review_count:
        reasons.append("operator_review_required")
    status_reason = {
        "exact_target_period_document_found": "exact_target_period_document_found",
        "target_period_likely_not_yet_published_by_policy_window": "target_period_likely_not_yet_published_by_policy_window",
        "target_period_likely_not_yet_published_before_primary_deadline": "target_period_likely_not_yet_published_before_primary_deadline",
        "target_period_not_found_after_primary_deadline_within_grace_window": "target_period_not_found_after_primary_deadline_within_grace_window",
        "target_period_not_found_after_conservative_grace_window": "target_period_not_found_after_conservative_grace_window",
        "only_historical_annual_ifrs_available": "only_historical_annual_ifrs_available",
        "only_interim_or_quarterly_available": "only_interim_or_quarterly_available",
        "only_wrong_standard_available": "only_wrong_standard_available",
        "operator_exact_document_review_required": "operator_exact_document_review_required",
        "placeholder_not_found": "placeholder_not_found",
        "no_usable_official_report_candidates": "no_usable_official_report_candidates",
        "target_period_document_not_found": "target_period_document_not_found",
    }.get(status)
    if status_reason:
        reasons.insert(0, status_reason)
    return list(dict.fromkeys(reasons))


def _build_target_reporting_period_availability(
    args: argparse.Namespace,
    required_issuers: list[dict[str, Any]],
    *,
    documents: list[dict[str, Any]],
    raw_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reporting_window = _exact_document_reporting_window_policy(args)
    availability: list[dict[str, Any]] = []
    for required in required_issuers:
        issuer_documents = _items_matching_required([*raw_documents, *documents], required)
        unique_documents = _exact_document_unique_url_items(issuer_documents)
        placeholder_count = sum(
            1
            for item in issuer_documents
            if not item.get("document_url") and item.get("document_status") == "not_found"
        )
        exact_target_documents = [
            item
            for item in unique_documents
            if _exact_document_is_downstream_eligible(item)
        ]
        target_period_documents = [
            item
            for item in unique_documents
            if item.get("document_period_status") == "target_period"
        ]
        historical_documents = [
            item for item in unique_documents if _exact_document_is_historical_annual_ifrs(item, args)
        ]
        interim_documents = [
            item for item in unique_documents if _exact_document_is_interim_or_quarterly(item)
        ]
        wrong_standard_documents = [
            item for item in unique_documents if _exact_document_is_wrong_standard_candidate(item)
        ]
        operator_review_documents = [
            item for item in unique_documents if _exact_document_requires_operator_review_for_availability(item)
        ]

        exact_target_count = len(exact_target_documents)
        target_period_count = len(target_period_documents)
        historical_count = len(historical_documents)
        interim_count = len(interim_documents)
        wrong_standard_count = len(wrong_standard_documents)
        operator_review_count = len(operator_review_documents)
        latest_historical = _exact_document_latest_historical(unique_documents, args)
        before_primary_deadline = bool(reporting_window.get("before_primary_deadline"))
        after_primary_deadline = bool(reporting_window.get("after_primary_deadline"))
        within_grace_window = bool(reporting_window.get("within_grace_window"))
        after_conservative_grace_window = bool(reporting_window.get("after_conservative_grace_window"))

        if exact_target_count:
            status = "exact_target_period_document_found"
        elif operator_review_count:
            status = "operator_exact_document_review_required"
        elif placeholder_count and not unique_documents:
            status = "placeholder_not_found"
        elif before_primary_deadline:
            status = "target_period_likely_not_yet_published_before_primary_deadline"
        elif after_primary_deadline and within_grace_window:
            status = "target_period_not_found_after_primary_deadline_within_grace_window"
        elif after_conservative_grace_window and not (historical_count or wrong_standard_count or interim_count):
            status = "target_period_not_found_after_conservative_grace_window"
        elif historical_count:
            status = "only_historical_annual_ifrs_available"
        elif wrong_standard_count and not interim_count:
            status = "only_wrong_standard_available"
        elif interim_count and not wrong_standard_count:
            status = "only_interim_or_quarterly_available"
        elif wrong_standard_count:
            status = "only_wrong_standard_available"
        elif interim_count:
            status = "only_interim_or_quarterly_available"
        elif target_period_count == 0 and unique_documents:
            status = "target_period_document_not_found"
        else:
            status = "no_usable_official_report_candidates"

        can_use = status == "exact_target_period_document_found"
        historical_fallback_allowed = bool(historical_count)
        fallback_scope = "diagnostic_only" if historical_fallback_allowed else "none"
        reason_codes = _exact_document_availability_reason_codes(
            status,
            exact_target_count=exact_target_count,
            target_period_count=target_period_count,
            historical_count=historical_count,
            interim_count=interim_count,
            wrong_standard_count=wrong_standard_count,
            placeholder_count=placeholder_count,
            operator_review_count=operator_review_count,
            before_primary_deadline=before_primary_deadline,
            after_primary_deadline=after_primary_deadline,
            within_grace_window=within_grace_window,
            after_conservative_grace_window=after_conservative_grace_window,
        )
        availability.append(
            {
                "company_id": required.get("company_id"),
                "company_name": required.get("company_name") or "",
                "canonical_company_id": required.get("canonical_company_id") or required.get("company_id"),
                "canonical_company_name": required.get("canonical_company_name") or required.get("company_name") or "",
                "target_reporting_period": str(getattr(args, "report_period", "") or ""),
                "required_report_type": str(getattr(args, "report_type", "") or ""),
                "required_standard": str(getattr(args, "accounting_standard", "") or ""),
                "availability_status": status,
                "availability_reason_codes": reason_codes,
                "exact_target_period_document_count": exact_target_count,
                "target_period_document_count": target_period_count,
                "historical_annual_ifrs_document_count": historical_count,
                "interim_or_quarterly_document_count": interim_count,
                "wrong_standard_document_count": wrong_standard_count,
                "placeholder_not_found_count": placeholder_count,
                "operator_review_required_count": operator_review_count,
                "latest_available_period": latest_historical.get("document_period_year") if latest_historical else "",
                "latest_available_report_type": "annual" if latest_historical else "",
                "latest_available_standard": "IFRS" if latest_historical else "",
                "latest_available_document_url": latest_historical.get("document_url") if latest_historical else "",
                "can_use_as_target_period_evidence": can_use,
                "historical_fallback_allowed": historical_fallback_allowed,
                "historical_fallback_scope": fallback_scope,
                "operator_action": _exact_document_availability_operator_action(status),
                "reporting_window_policy": dict(reporting_window),
            }
        )
    return availability


def _availability_by_issuer(
    availability: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in availability:
        key = str(item.get("canonical_company_id") or item.get("company_id") or "")
        if key:
            grouped[key] = item
    return grouped


def _exact_document_row_availability_status(document: dict[str, Any], issuer_policy: dict[str, Any] | None, args: argparse.Namespace) -> str:
    if not document.get("document_url") and document.get("document_status") == "not_found":
        return "placeholder_not_found"
    if _exact_document_is_downstream_eligible(document):
        return "exact_target_period_document_found"
    if _exact_document_is_historical_annual_ifrs(document, args):
        return "only_historical_annual_ifrs_available"
    if _exact_document_is_interim_or_quarterly(document):
        return "only_interim_or_quarterly_available"
    if _exact_document_is_wrong_standard_candidate(document):
        return "only_wrong_standard_available"
    if _exact_document_requires_operator_review_for_availability(document):
        return "operator_exact_document_review_required"
    return str((issuer_policy or {}).get("availability_status") or "no_usable_official_report_candidates")


def _annotate_exact_document_availability(
    documents: list[dict[str, Any]],
    availability: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
) -> None:
    by_issuer = _availability_by_issuer(availability)
    for document in documents:
        key = str(document.get("canonical_company_id") or document.get("company_id") or "")
        policy = by_issuer.get(key, {})
        status = _exact_document_row_availability_status(document, policy, args)
        can_use = _exact_document_is_downstream_eligible(document)
        document["availability_status"] = status
        document["availability_reason_codes"] = list(policy.get("availability_reason_codes") or [])
        document["can_use_as_target_period_evidence"] = can_use
        document["historical_fallback_allowed"] = bool(policy.get("historical_fallback_allowed"))
        document["historical_fallback_scope"] = policy.get("historical_fallback_scope") or "none"
        document["operator_action"] = _exact_document_availability_operator_action(status)


def _availability_operator_next_step(status: str) -> str:
    return {
        "exact_target_period_document_found": "proceed_to_quality_gate_or_extraction_preview",
        "target_period_likely_not_yet_published_by_policy_window": "wait_for_target_period_publication_or_review_official_sources",
        "target_period_likely_not_yet_published_before_primary_deadline": "wait_until_primary_deadline_or_monitor_publication",
        "target_period_not_found_after_primary_deadline_within_grace_window": "review_official_sources_or_wait_until_conservative_grace_date",
        "target_period_not_found_after_conservative_grace_window": "escalate_missing_target_report_and_review_source_coverage",
        "only_historical_annual_ifrs_available": "keep_historical_report_as_diagnostic_only_and_continue_target_search",
        "only_interim_or_quarterly_available": "do_not_use_interim_as_annual_evidence",
        "only_wrong_standard_available": "search_for_ifrs_report_or_mark_ifrs_unavailable",
        "operator_exact_document_review_required": "review_exact_document_candidate",
        "placeholder_not_found": "fill_exact_official_document_url_or_improve_official_sources",
        "no_usable_official_report_candidates": "improve_official_source_coverage",
        "target_period_document_not_found": "improve_official_source_coverage",
    }.get(status, "improve_official_source_coverage")


def _availability_operator_note(row: dict[str, Any]) -> str:
    status = str(row.get("availability_status") or "")
    if row.get("ready_for_value_extraction"):
        return "strict quality gate passed; target-period extraction preview may proceed"
    if row.get("can_use_as_target_period_evidence") and row.get("gate_status") == "quality_gate_not_run":
        return "target evidence is available; run strict quality gate before extraction preview"
    if row.get("gate_status") == "quality_gate_not_run":
        gate_note = "quality_gate_not_run"
    else:
        gate_note = str(row.get("gate_reason") or row.get("gate_status") or "")
    if status in {
        "target_period_likely_not_yet_published_by_policy_window",
        "target_period_likely_not_yet_published_before_primary_deadline",
    }:
        return f"target report may still be inside policy grace window; {gate_note}"
    if status == "target_period_not_found_after_primary_deadline_within_grace_window":
        return f"primary expected deadline has passed; conservative grace window remains open; {gate_note}"
    if status == "target_period_not_found_after_conservative_grace_window":
        return f"target report was not found after the configured conservative grace window; {gate_note}"
    if status == "only_historical_annual_ifrs_available":
        return f"historical fallback is diagnostic only; {gate_note}"
    if status == "placeholder_not_found":
        return f"operator must provide exact official document URL or improve official sources; {gate_note}"
    return gate_note or "not ready for extraction"


def _gate_status_for_availability_item(
    item: dict[str, Any],
    document_quality_gate_report: dict[str, Any] | None,
) -> dict[str, Any]:
    if not document_quality_gate_report:
        return {
            "gate_status": "quality_gate_not_run",
            "gate_passed": False,
            "gate_reason": "quality_gate_not_run",
            "ready_for_value_extraction": False,
            "ready_for_import": False,
        }
    matches = _items_matching_required(document_quality_gate_report.get("required_issuers") or [], item)
    matched = matches[0] if matches else {}
    gate_status = str(matched.get("gate_status") or "missing_from_quality_gate")
    per_issuer_passed = gate_status == "passed"
    overall_ready = bool(document_quality_gate_report.get("ready_for_value_extraction"))
    overall_import_ready = bool(document_quality_gate_report.get("ready_for_import"))
    return {
        "gate_status": gate_status,
        "gate_passed": per_issuer_passed,
        "gate_reason": matched.get("reason") or ("missing required issuer in quality gate report" if not matches else ""),
        "ready_for_value_extraction": bool(overall_ready and per_issuer_passed),
        "ready_for_import": bool(overall_import_ready and per_issuer_passed),
    }


def _build_availability_operator_rows(
    availability: list[dict[str, Any]],
    *,
    document_quality_gate_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in availability:
        policy = item.get("reporting_window_policy") or {}
        gate = _gate_status_for_availability_item(item, document_quality_gate_report)
        status = str(item.get("availability_status") or "")
        row = {
            "company_id": item.get("company_id"),
            "company_name": item.get("company_name") or "",
            "canonical_company_id": item.get("canonical_company_id") or item.get("company_id"),
            "canonical_company_name": item.get("canonical_company_name") or item.get("company_name") or "",
            "target_reporting_period": item.get("target_reporting_period") or "",
            "required_report_type": item.get("required_report_type") or "",
            "required_standard": item.get("required_standard") or "",
            "availability_status": status,
            "availability_reason_codes": list(item.get("availability_reason_codes") or []),
            "exact_target_period_document_count": item.get("exact_target_period_document_count", 0),
            "target_period_document_count": item.get("target_period_document_count", 0),
            "historical_annual_ifrs_document_count": item.get("historical_annual_ifrs_document_count", 0),
            "interim_or_quarterly_document_count": item.get("interim_or_quarterly_document_count", 0),
            "wrong_standard_document_count": item.get("wrong_standard_document_count", 0),
            "placeholder_not_found_count": item.get("placeholder_not_found_count", 0),
            "operator_review_required_count": item.get("operator_review_required_count", 0),
            "latest_available_period": item.get("latest_available_period") or "",
            "latest_available_report_type": item.get("latest_available_report_type") or "",
            "latest_available_standard": item.get("latest_available_standard") or "",
            "latest_available_document_url": item.get("latest_available_document_url") or "",
            "can_use_as_target_period_evidence": bool(item.get("can_use_as_target_period_evidence")),
            "historical_fallback_allowed": bool(item.get("historical_fallback_allowed")),
            "historical_fallback_scope": item.get("historical_fallback_scope") or "none",
            "operator_action": item.get("operator_action") or "",
            "reporting_policy_name": policy.get("policy_name") or "",
            "target_period_end_date": policy.get("target_period_end_date") or "",
            "primary_deadline_days": policy.get("primary_deadline_days", ""),
            "primary_expected_deadline_date": policy.get("primary_expected_deadline_date") or "",
            "expected_availability_date": policy.get("expected_availability_date") or "",
            "availability_current_date": policy.get("current_date") or "",
            "before_primary_deadline": bool(policy.get("before_primary_deadline")),
            "after_primary_deadline": bool(policy.get("after_primary_deadline")),
            "within_grace_window": bool(policy.get("within_grace_window")),
            "within_conservative_grace_window": bool(policy.get("within_conservative_grace_window")),
            "after_conservative_grace_window": bool(policy.get("after_conservative_grace_window")),
            "deadline_status": policy.get("deadline_status") or "",
            **gate,
            "recommended_next_step": _availability_operator_next_step(status),
        }
        row["operator_note"] = _availability_operator_note(row)
        rows.append(row)
    return rows


def _count_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _build_availability_operator_summary_report(
    args: argparse.Namespace,
    *,
    status: str,
    target_reporting_period_availability: list[dict[str, Any]] | None,
    document_quality_gate_report: dict[str, Any] | None,
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    availability = target_reporting_period_availability or []
    rows = _build_availability_operator_rows(
        availability,
        document_quality_gate_report=document_quality_gate_report,
    )
    first_policy = (availability[0].get("reporting_window_policy") if availability else {}) or {}
    summary = {
        "target_reporting_period_availability_count": len(availability),
        "availability_policy_name": first_policy.get("policy_name") or getattr(args, "exact_document_availability_policy_name", ""),
        "availability_current_date": first_policy.get("current_date") or "",
        "annual_ifrs_primary_deadline_days": first_policy.get(
            "primary_deadline_days",
            getattr(args, "exact_document_annual_ifrs_primary_deadline_days", 120),
        ),
        "primary_expected_deadline_date": first_policy.get("primary_expected_deadline_date") or "",
        "annual_ifrs_grace_days": first_policy.get("grace_days", getattr(args, "exact_document_annual_ifrs_grace_days", 180)),
        "expected_availability_date": first_policy.get("expected_availability_date") or "",
        "deadline_status_counts": _count_by_key(rows, "deadline_status"),
        "availability_primary_deadline_status_counts": _count_by_key(rows, "deadline_status"),
        "availability_status_counts": _count_by_key(rows, "availability_status"),
        "target_evidence_available_count": sum(1 for row in rows if row.get("can_use_as_target_period_evidence")),
        "historical_fallback_diagnostic_only_count": sum(1 for row in rows if row.get("historical_fallback_scope") == "diagnostic_only"),
        "operator_action_counts": _count_by_key(rows, "operator_action"),
        "extraction_ready_count": sum(1 for row in rows if row.get("ready_for_value_extraction")),
        "import_ready_count": sum(1 for row in rows if row.get("ready_for_import")),
    }
    return {
        "status": status,
        "mode": "availability-operator-summary",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": summary,
        "issuers": rows,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _operator_review_queue_action_config(availability_status: str) -> dict[str, Any]:
    configs = {
        "placeholder_not_found": {
            "queue_action_type": "fill_exact_document_url",
            "queue_action_label": "Fill exact document URL",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Find and paste the exact official annual IFRS report page or PDF URL. Do not paste a landing page.",
        },
        "target_period_likely_not_yet_published_by_policy_window": {
            "queue_action_type": "wait_or_recheck_publication",
            "queue_action_label": "Wait or recheck publication",
            "queue_priority": "medium",
            "queue_status": "waiting",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": False,
            "can_unblock_extraction": False,
            "operator_instruction": "Wait until expected availability date or manually recheck official sources if needed.",
        },
        "target_period_likely_not_yet_published_before_primary_deadline": {
            "queue_action_type": "wait_until_primary_deadline",
            "queue_action_label": "Wait until primary deadline",
            "queue_priority": "low",
            "queue_status": "waiting",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": False,
            "can_unblock_extraction": False,
            "operator_instruction": "Wait until the primary expected reporting deadline or monitor official publication.",
        },
        "target_period_not_found_after_primary_deadline_within_grace_window": {
            "queue_action_type": "review_sources_or_wait_grace",
            "queue_action_label": "Review sources or wait grace",
            "queue_priority": "medium",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Primary expected reporting deadline has passed. Review official sources manually or wait until conservative grace date if justified.",
        },
        "target_period_not_found_after_conservative_grace_window": {
            "queue_action_type": "escalate_missing_target_report",
            "queue_action_label": "Escalate missing target report",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Target annual IFRS report was not found after the conservative grace window. Review official source coverage and verify report availability manually.",
        },
        "only_historical_annual_ifrs_available": {
            "queue_action_type": "continue_target_period_search",
            "queue_action_label": "Continue target-period search",
            "queue_priority": "medium",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Historical IFRS report is diagnostic-only. Continue searching for exact target-period annual IFRS report.",
        },
        "only_interim_or_quarterly_available": {
            "queue_action_type": "search_annual_report",
            "queue_action_label": "Search annual report",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Interim, quarterly, or half-year reports must not be used as annual evidence. Search for the exact annual IFRS report.",
        },
        "only_wrong_standard_available": {
            "queue_action_type": "search_ifrs_report",
            "queue_action_label": "Search IFRS report",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "RAS/РСБУ is not accepted when IFRS is required. Search for the official IFRS report.",
        },
        "operator_exact_document_review_required": {
            "queue_action_type": "review_exact_document_candidate",
            "queue_action_label": "Review exact document candidate",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Review the exact candidate manually and confirm whether it is the official target-period annual IFRS report.",
        },
        "no_usable_official_report_candidates": {
            "queue_action_type": "improve_official_source_coverage",
            "queue_action_label": "Improve official source coverage",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Add or review official source seeds before searching for exact report documents.",
        },
        "target_period_document_not_found": {
            "queue_action_type": "improve_official_source_coverage",
            "queue_action_label": "Improve official source coverage",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Improve official source coverage and continue searching for the exact target-period annual IFRS report.",
        },
        "exact_target_period_document_found": {
            "queue_action_type": "no_operator_action_required",
            "queue_action_label": "No operator action required",
            "queue_priority": "low",
            "queue_status": "resolved_or_not_required",
            "is_blocking_next_stage": False,
            "blocked_stage": "none",
            "manual_review_required": False,
            "can_unblock_extraction": False,
            "operator_instruction": "Exact target-period annual IFRS document is available. Follow the existing quality gate before extraction preview.",
        },
    }
    return configs.get(
        availability_status,
        {
            "queue_action_type": "improve_official_source_coverage",
            "queue_action_label": "Improve official source coverage",
            "queue_priority": "high",
            "queue_status": "open",
            "is_blocking_next_stage": True,
            "blocked_stage": "value_extraction",
            "manual_review_required": True,
            "can_unblock_extraction": True,
            "operator_instruction": "Review official source coverage and continue searching for the exact target-period annual IFRS report.",
        },
    )


def _operator_review_queue_action_id(row: dict[str, Any], queue_action_type: str) -> str:
    company_id = row.get("company_id") or row.get("canonical_company_id") or ""
    target = row.get("target_reporting_period") or ""
    report_type = row.get("required_report_type") or ""
    standard = row.get("required_standard") or ""
    return f"financial_report:{company_id}:{target}:{report_type}:{standard}:{queue_action_type}"


def _build_operator_review_queue_rows(availability_operator_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in availability_operator_rows:
        status = str(row.get("availability_status") or "")
        config = _operator_review_queue_action_config(status)
        queue_action_type = str(config.get("queue_action_type") or "")
        source_context = row.get("latest_available_document_url") or _csv_value(row.get("availability_reason_codes"))
        action = {
            "action_id": _operator_review_queue_action_id(row, queue_action_type),
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name") or "",
            "canonical_company_id": row.get("canonical_company_id") or row.get("company_id"),
            "canonical_company_name": row.get("canonical_company_name") or row.get("company_name") or "",
            "target_reporting_period": row.get("target_reporting_period") or "",
            "required_report_type": row.get("required_report_type") or "",
            "required_standard": row.get("required_standard") or "",
            "availability_status": status,
            "availability_reason_codes": list(row.get("availability_reason_codes") or []),
            "operator_action": row.get("operator_action") or "",
            "recommended_next_step": row.get("recommended_next_step") or "",
            **config,
            "target_evidence_available": bool(row.get("can_use_as_target_period_evidence")),
            "gate_status": row.get("gate_status") or "",
            "gate_passed": bool(row.get("gate_passed")),
            "ready_for_value_extraction": bool(row.get("ready_for_value_extraction")),
            "ready_for_import": bool(row.get("ready_for_import")),
            "historical_fallback_scope": row.get("historical_fallback_scope") or "none",
            "historical_fallback_allowed": bool(row.get("historical_fallback_allowed")),
            "latest_available_period": row.get("latest_available_period") or "",
            "latest_available_report_type": row.get("latest_available_report_type") or "",
            "latest_available_standard": row.get("latest_available_standard") or "",
            "latest_available_document_url": row.get("latest_available_document_url") or "",
            "reporting_policy_name": row.get("reporting_policy_name") or "",
            "primary_deadline_days": row.get("primary_deadline_days") or "",
            "primary_expected_deadline_date": row.get("primary_expected_deadline_date") or "",
            "expected_availability_date": row.get("expected_availability_date") or "",
            "availability_current_date": row.get("availability_current_date") or "",
            "before_primary_deadline": bool(row.get("before_primary_deadline")),
            "after_primary_deadline": bool(row.get("after_primary_deadline")),
            "within_grace_window": bool(row.get("within_grace_window")),
            "within_conservative_grace_window": bool(row.get("within_conservative_grace_window")),
            "after_conservative_grace_window": bool(row.get("after_conservative_grace_window")),
            "deadline_status": row.get("deadline_status") or "",
            "operator_note": row.get("operator_note") or "",
            "source_context": source_context,
        }
        actions.append(action)
    return sorted(actions, key=lambda item: str(item.get("action_id") or ""))


def _operator_review_queue_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    priority_counts = {"high": 0, "medium": 0, "low": 0}
    for action in actions:
        priority = str(action.get("queue_priority") or "")
        if priority:
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
    return {
        "operator_review_queue_count": len(actions),
        "operator_review_queue_blocking_count": sum(1 for action in actions if action.get("is_blocking_next_stage")),
        "operator_review_queue_manual_action_count": sum(1 for action in actions if action.get("manual_review_required")),
        "operator_review_queue_wait_action_count": sum(
            1 for action in actions if action.get("queue_status") == "waiting"
        ),
        "operator_review_queue_noop_count": sum(
            1 for action in actions if action.get("queue_action_type") == "no_operator_action_required"
        ),
        "operator_review_queue_priority_counts": dict(sorted(priority_counts.items())),
        "operator_review_queue_action_type_counts": _count_by_key(actions, "queue_action_type"),
    }


def _build_operator_review_queue_report(
    args: argparse.Namespace,
    *,
    status: str,
    availability_operator_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions = _build_operator_review_queue_rows(availability_operator_rows)
    return {
        "status": status,
        "mode": "operator-review-queue",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": _operator_review_queue_summary(actions),
        "actions": actions,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _seed_rows_for_required(seed_issuers: list[dict[str, Any]], required: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issuer in _items_matching_required(seed_issuers, required):
        for seed in issuer.get("official_seeds") or []:
            if not isinstance(seed, dict):
                continue
            rows.append(
                {
                    "company_id": issuer.get("company_id"),
                    "company_name": issuer.get("company_name") or "",
                    "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
                    "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
                    "inn": issuer.get("inn") or "",
                    "ogrn": issuer.get("ogrn") or "",
                    **seed,
                }
            )
    return rows


def _coverage_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).casefold()


def _coverage_has_source_context(items: list[dict[str, Any]]) -> bool:
    for item in items:
        for key in ("seed_url", "source_url_context", "source_page_url", "document_url", "candidate_seed_url", "source_url"):
            if str(item.get(key) or "").strip():
                return True
    return False


def _coverage_is_reviewed_seed(seed: dict[str, Any]) -> bool:
    return bool(
        seed.get("operator_review_status") in {"operator_reviewed", "reviewed"}
        or seed.get("source") in EXACT_DOCUMENT_REVIEWED_SEED_SOURCES
    )


def _coverage_seed_signals(seed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    seed_text = _coverage_text(
        *[
            f"{seed.get('seed_type') or ''} {seed.get('seed_url') or ''} {seed.get('candidate_title') or ''}"
            for seed in seed_rows
        ]
    )
    official_seed_url_count = sum(1 for seed in seed_rows if seed.get("seed_url"))
    reviewed_count = sum(1 for seed in seed_rows if _coverage_is_reviewed_seed(seed))
    valid_reviewed_count = sum(
        1
        for seed in seed_rows
        if _coverage_is_reviewed_seed(seed) and seed.get("seed_status") == "valid_seed" and seed.get("seed_url")
    )
    invalid_reviewed_count = sum(
        1
        for seed in seed_rows
        if _coverage_is_reviewed_seed(seed) and seed.get("seed_status") not in {"valid_seed", ""}
    )
    return {
        "has_official_seed": bool(official_seed_url_count),
        "reviewed_official_seed_count": reviewed_count,
        "valid_reviewed_seed_count": valid_reviewed_count,
        "invalid_reviewed_seed_count": invalid_reviewed_count,
        "official_seed_url_count": official_seed_url_count,
        "has_company_website_seed": any(str(seed.get("seed_type") or "") == "issuer_home" for seed in seed_rows),
        "has_ir_or_investor_relations_seed": any(
            str(seed.get("seed_type") or "") in {"issuer_reports", "issuer_investor_relations", "investor_relations"}
            for seed in seed_rows
        ),
        "has_e_disclosure_seed": any(
            str(seed.get("seed_type") or "").startswith("official_disclosure")
            or "e-disclosure" in str(seed.get("seed_url") or "").casefold()
            for seed in seed_rows
        ),
        "seed_text": seed_text,
    }


def _coverage_reporting_signals(seed_rows: list[dict[str, Any]], documents: list[dict[str, Any]], category_pages: list[dict[str, Any]]) -> dict[str, bool]:
    combined = _coverage_text(
        *[
            f"{seed.get('seed_type') or ''} {seed.get('seed_url') or ''} {seed.get('candidate_title') or ''}"
            for seed in seed_rows
        ],
        *[
            f"{item.get('document_kind') or ''} {item.get('document_url') or ''} {item.get('document_title') or ''} {item.get('source_page_url') or ''}"
            for item in documents
        ],
        *[
            f"{item.get('document_kind') or ''} {item.get('document_url') or ''} {item.get('document_title') or ''}"
            for item in category_pages
        ],
    )
    return {
        "has_reporting_or_disclosure_page": _contains_any(
            combined,
            (
                "report",
                "reports",
                "issuer_reports",
                "issuer-reports",
                "disclosure",
                "information-disclosure",
                "financial-results",
                "financial results",
                "accounting-statements",
                "annual",
                "годов",
                "отчет",
                "отчёт",
            ),
        ),
        "has_financial_results_page": _contains_any(
            combined,
            ("financial-results", "financial_results", "financial results", "финансовые результат", "ifrs", "мсфо"),
        ),
        "has_accounting_statements_page": _contains_any(
            combined,
            ("accounting-statements", "accounting_statements", "бухгалтерск", "финансовая отчетность", "финансовая отчётность"),
        ),
        "has_annual_reports_page": _contains_any(
            combined,
            ("issuer-reports", "issuer_reports", "annual report", "annual reports", "годовые отчет", "годовые отчёт", "годовой"),
        ),
        "has_ifrs_reporting_page": _contains_any(combined, ("ifrs", "мсфо")),
    }


def _coverage_score_and_grade(row: dict[str, Any]) -> tuple[int, str]:
    score = 0
    if row.get("valid_reviewed_seed_count"):
        score += 20
    if row.get("has_company_website_seed"):
        score += 15
    if row.get("has_ir_or_investor_relations_seed"):
        score += 15
    if row.get("has_e_disclosure_seed"):
        score += 15
    if row.get("has_reporting_or_disclosure_page"):
        score += 15
    if row.get("has_financial_results_page") or row.get("has_ifrs_reporting_page"):
        score += 10
    if int(row.get("category_pages_followed_count") or 0) > 0:
        score += 10
    if int(row.get("exact_report_document_count") or 0) > 0:
        score += 10
    if int(row.get("historical_annual_ifrs_document_count") or 0) > 0:
        score += 10
    if int(row.get("interim_or_quarterly_document_count") or 0) > 0:
        score += 5
    if not row.get("valid_reviewed_seed_count"):
        score -= 20
    if row.get("coverage_status") == "weak_only_generic_or_landing_pages":
        score -= 15
    if int(row.get("placeholder_not_found_count") or 0) > 0 and int(row.get("exact_report_document_count") or 0) == 0:
        score -= 20
    score = max(0, min(100, score))
    if score >= 80:
        grade = "strong"
    elif score >= 50:
        grade = "partial"
    elif score >= 20:
        grade = "weak"
    else:
        grade = "missing"
    return score, grade


def _coverage_operator_action(status: str) -> tuple[str, str]:
    mapping = {
        "missing_official_sources": (
            "add_official_sources",
            "Add official company website, IR/disclosure page, and e-disclosure source if available.",
        ),
        "weak_no_reviewed_seed": (
            "review_or_promote_official_seed",
            "Review candidate source seeds and promote at least one official reporting/disclosure source.",
        ),
        "weak_no_reporting_pages": (
            "add_reporting_page_seed",
            "Add or review official reporting, disclosure, financial results, annual reports, or IFRS page.",
        ),
        "weak_only_generic_or_landing_pages": (
            "replace_landing_page_with_reporting_page",
            "Replace generic landing page with exact official reporting/disclosure page.",
        ),
        "partial_historical_or_interim_only": (
            "continue_target_report_search",
            "Existing sources expose historical/interim reports only. Continue searching for exact target-period annual IFRS report.",
        ),
        "strong_but_target_report_missing": (
            "verify_target_report_publication",
            "Official reporting sources are available, but target annual IFRS report was not found. Verify publication status manually.",
        ),
        "strong_target_evidence_available": (
            "no_source_action_required",
            "Source coverage is sufficient. Continue with existing quality gate workflow.",
        ),
        "operator_review_required": (
            "review_source_or_document_candidate",
            "Review ambiguous official source or exact document candidate before using it as evidence.",
        ),
    }
    return mapping.get(status, mapping["missing_official_sources"])


def _coverage_status(row: dict[str, Any], *, has_source_context: bool, has_generic_only: bool, operator_review_required: bool) -> str:
    if row.get("can_use_as_target_period_evidence"):
        return "strong_target_evidence_available"
    if not row.get("has_official_seed") and not has_source_context:
        return "missing_official_sources"
    if not row.get("valid_reviewed_seed_count"):
        return "weak_no_reviewed_seed"
    if has_generic_only:
        return "weak_only_generic_or_landing_pages"
    if not row.get("has_reporting_or_disclosure_page") and int(row.get("category_page_count") or 0) == 0:
        return "weak_no_reporting_pages"
    if row.get("has_reporting_or_disclosure_page") or int(row.get("category_pages_followed_count") or 0) > 0:
        return "strong_but_target_report_missing"
    if int(row.get("historical_annual_ifrs_document_count") or 0) or int(row.get("interim_or_quarterly_document_count") or 0):
        return "partial_historical_or_interim_only"
    if operator_review_required:
        return "operator_review_required"
    return "weak_no_reporting_pages"


def _build_official_source_coverage_rows(
    args: argparse.Namespace,
    *,
    required_issuers: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    input_documents: list[dict[str, Any]],
    reviewed_seeds_used: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    all_documents_for_counters: list[dict[str, Any]],
    category_pages_followed: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    availability_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in availability_operator_rows}
    queue_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in operator_review_queue}
    rows: list[dict[str, Any]] = []
    for required in required_issuers:
        key = str(required.get("company_id") or "")
        seed_rows = _seed_rows_for_required(seed_issuers, required)
        reviewed_matches = _items_matching_required(reviewed_seeds_used, required)
        input_matches = _items_matching_required(input_documents, required)
        document_matches = _items_matching_required([*all_documents_for_counters, *documents], required)
        unique_documents = _exact_document_unique_url_items(document_matches)
        category_matches = [
            item
            for item in _items_matching_required(category_pages_followed, required)
            if item.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS
        ]
        availability = availability_by_key.get(key) or {}
        queue = queue_by_key.get(key) or {}
        seed_signals = _coverage_seed_signals(seed_rows)
        if reviewed_matches:
            seed_signals["reviewed_official_seed_count"] = max(seed_signals["reviewed_official_seed_count"], len(reviewed_matches))
            seed_signals["valid_reviewed_seed_count"] = max(seed_signals["valid_reviewed_seed_count"], len(reviewed_matches))
            seed_signals["has_official_seed"] = True
        reporting_signals = _coverage_reporting_signals(seed_rows, unique_documents, category_matches)
        category_page_count = sum(1 for item in unique_documents if item.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS)
        exact_report_document_count = sum(1 for item in unique_documents if item.get("document_kind") == "exact_report_document")
        has_source_context = _coverage_has_source_context([*seed_rows, *input_matches, *reviewed_matches])
        has_any_report_doc = bool(
            exact_report_document_count
            or int(availability.get("historical_annual_ifrs_document_count") or 0)
            or int(availability.get("interim_or_quarterly_document_count") or 0)
            or int(availability.get("wrong_standard_document_count") or 0)
        )
        has_generic_only = bool(
            seed_signals["official_seed_url_count"]
            and not reporting_signals["has_reporting_or_disclosure_page"]
            and category_page_count == 0
            and not has_any_report_doc
        )
        operator_review_required = bool(
            availability.get("availability_status") == "operator_exact_document_review_required"
            or queue.get("queue_action_type") == "review_exact_document_candidate"
        )
        row = {
            "company_id": required.get("company_id"),
            "company_name": required.get("company_name") or availability.get("company_name") or "",
            "canonical_company_id": availability.get("canonical_company_id") or required.get("company_id"),
            "canonical_company_name": availability.get("canonical_company_name") or required.get("company_name") or "",
            "target_reporting_period": str(getattr(args, "report_period", "") or ""),
            "required_report_type": str(getattr(args, "report_type", "") or ""),
            "required_standard": str(getattr(args, "accounting_standard", "") or ""),
            **{key_: value for key_, value in seed_signals.items() if key_ != "seed_text"},
            **reporting_signals,
            "category_page_count": category_page_count,
            "category_pages_followed_count": len(category_matches),
            "exact_report_document_count": exact_report_document_count,
            "target_period_document_count": availability.get("target_period_document_count", 0),
            "historical_annual_ifrs_document_count": availability.get("historical_annual_ifrs_document_count", 0),
            "interim_or_quarterly_document_count": availability.get("interim_or_quarterly_document_count", 0),
            "wrong_standard_document_count": availability.get("wrong_standard_document_count", 0),
            "placeholder_not_found_count": availability.get("placeholder_not_found_count", 0),
            "availability_status": availability.get("availability_status") or "",
            "deadline_status": availability.get("deadline_status") or "",
            "operator_action": availability.get("operator_action") or "",
            "recommended_next_step": availability.get("recommended_next_step") or "",
            "queue_action_type": queue.get("queue_action_type") or "",
            "queue_priority": queue.get("queue_priority") or "",
            "queue_status": queue.get("queue_status") or "",
            "can_use_as_target_period_evidence": bool(availability.get("can_use_as_target_period_evidence")),
            "gate_status": availability.get("gate_status") or "",
            "gate_passed": bool(availability.get("gate_passed")),
            "ready_for_value_extraction": bool(availability.get("ready_for_value_extraction")),
            "ready_for_import": bool(availability.get("ready_for_import")),
        }
        status = _coverage_status(
            row,
            has_source_context=has_source_context,
            has_generic_only=has_generic_only,
            operator_review_required=operator_review_required,
        )
        row["coverage_status"] = status
        reasons = []
        if not row.get("valid_reviewed_seed_count"):
            reasons.append("no_valid_reviewed_official_seed")
        if not row.get("has_reporting_or_disclosure_page"):
            reasons.append("no_reporting_or_disclosure_page")
        if has_generic_only:
            reasons.append("only_generic_or_landing_pages")
        if row.get("historical_annual_ifrs_document_count"):
            reasons.append("historical_annual_ifrs_available")
        if row.get("interim_or_quarterly_document_count"):
            reasons.append("interim_or_quarterly_available")
        if row.get("wrong_standard_document_count"):
            reasons.append("wrong_standard_available")
        if row.get("can_use_as_target_period_evidence"):
            reasons.append("target_period_evidence_available")
        if row.get("placeholder_not_found_count"):
            reasons.append("placeholder_not_found")
        row["coverage_reason_codes"] = list(dict.fromkeys([status, *reasons]))
        score, grade = _coverage_score_and_grade(row)
        row["coverage_score"] = score
        row["coverage_grade"] = grade
        action, instruction = _coverage_operator_action(status)
        row["coverage_operator_action"] = action
        row["coverage_operator_instruction"] = instruction
        row["coverage_note"] = _coverage_note(row)
        rows.append(row)
    return sorted(rows, key=lambda item: (str(item.get("company_id") or ""), str(item.get("company_name") or "")))


def _coverage_note(row: dict[str, Any]) -> str:
    if row.get("coverage_status") == "strong_target_evidence_available":
        return "source coverage is sufficient; strict quality gate still controls readiness"
    if row.get("coverage_status") == "strong_but_target_report_missing":
        return "reviewed reporting sources exist, but exact target-period annual IFRS evidence is missing"
    if row.get("coverage_status") == "weak_no_reviewed_seed":
        return "no valid reviewed official seed was available for exact document discovery"
    if row.get("coverage_status") == "missing_official_sources":
        return "no official source context was available in the reviewed seed pack or intake"
    return "coverage diagnostic only; does not change strict evidence eligibility"


def _official_source_coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grade_counts = {"strong": 0, "partial": 0, "weak": 0, "missing": 0}
    for row in rows:
        grade = str(row.get("coverage_grade") or "")
        if grade:
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
    return {
        "official_source_coverage_issuer_count": len(rows),
        "official_source_coverage_strong_count": grade_counts.get("strong", 0),
        "official_source_coverage_partial_count": grade_counts.get("partial", 0),
        "official_source_coverage_weak_count": grade_counts.get("weak", 0),
        "official_source_coverage_missing_count": grade_counts.get("missing", 0),
        "official_source_coverage_needs_operator_count": sum(
            1 for row in rows if row.get("coverage_operator_action") != "no_source_action_required"
        ),
        "official_source_coverage_status_counts": _count_by_key(rows, "coverage_status"),
        "official_source_coverage_grade_counts": dict(sorted(grade_counts.items())),
        "official_source_coverage_action_counts": _count_by_key(rows, "coverage_operator_action"),
    }


def _build_official_source_coverage_report(
    args: argparse.Namespace,
    *,
    status: str,
    required_issuers: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    input_documents: list[dict[str, Any]],
    reviewed_seeds_used: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    all_documents_for_counters: list[dict[str, Any]],
    category_pages_followed: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _build_official_source_coverage_rows(
        args,
        required_issuers=required_issuers,
        seed_issuers=seed_issuers,
        input_documents=input_documents,
        reviewed_seeds_used=reviewed_seeds_used,
        documents=documents,
        all_documents_for_counters=all_documents_for_counters,
        category_pages_followed=category_pages_followed,
        availability_operator_rows=availability_operator_rows,
        operator_review_queue=operator_review_queue,
    )
    return {
        "status": status,
        "mode": "official-source-coverage-matrix",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": _official_source_coverage_summary(rows),
        "issuers": rows,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _historical_fallback_is_historical(document: dict[str, Any], args: argparse.Namespace) -> bool:
    year = _exact_document_int_year(document.get("document_period_year"))
    target = _exact_document_int_year(getattr(args, "report_period", ""))
    return bool(document.get("document_url") and year is not None and target is not None and year < target)


def _historical_fallback_is_historical_report(document: dict[str, Any], args: argparse.Namespace) -> bool:
    if not _historical_fallback_is_historical(document, args):
        return False
    return bool(
        document.get("document_kind") in {"exact_report_document", "quarterly_or_interim_document"}
        or document.get("report_type_match_status") in {"annual_match", "interim_or_quarterly_mismatch"}
        or document.get("accounting_standard_match_status") in {"standard_match", "standard_mismatch"}
    )


def _historical_fallback_is_wrong_standard(document: dict[str, Any], args: argparse.Namespace) -> bool:
    return bool(
        _historical_fallback_is_historical(document, args)
        and document.get("document_kind") == "exact_report_document"
        and document.get("accounting_standard_match_status") == "standard_mismatch"
    )


def _historical_fallback_requires_operator_review(document: dict[str, Any], args: argparse.Namespace) -> bool:
    if not _historical_fallback_is_historical(document, args):
        return False
    if document.get("document_kind") != "exact_report_document":
        return False
    if _exact_document_is_historical_annual_ifrs(document, args):
        return False
    if _historical_fallback_is_wrong_standard(document, args):
        return False
    if _exact_document_is_interim_or_quarterly(document):
        return False
    return bool(
        document.get("operator_review_status") == "needs_operator_review"
        or document.get("report_type_match_status") in {"unknown_report_type", "report_type_conflict"}
        or document.get("accounting_standard_match_status") in {"unknown_standard", "standard_conflict"}
        or document.get("document_status") in {"needs_operator_review", "filtered_document"}
    )


def _historical_fallback_status(
    *,
    exact_target_count: int,
    historical_annual_count: int,
    ambiguous_count: int,
    historical_interim_count: int,
    historical_wrong_standard_count: int,
    historical_report_count: int,
) -> str:
    if exact_target_count:
        return "exact_target_period_available_no_fallback_needed"
    if historical_annual_count:
        return "latest_historical_annual_ifrs_available"
    if ambiguous_count:
        return "operator_review_required_for_historical_candidate"
    if historical_interim_count and not historical_wrong_standard_count:
        return "only_interim_or_quarterly_historical_available"
    if historical_wrong_standard_count and not historical_interim_count:
        return "only_wrong_standard_historical_available"
    if historical_report_count:
        return "historical_reports_available_but_not_ifrs_annual"
    return "no_historical_fallback_available"


def _historical_fallback_reason_codes(
    status: str,
    *,
    historical_annual_count: int,
    historical_interim_count: int,
    historical_wrong_standard_count: int,
    ambiguous_count: int,
    target_period_count: int,
    exact_target_count: int,
) -> list[str]:
    reasons = [status]
    if historical_annual_count:
        reasons.extend(
            [
                "historical_annual_ifrs_available",
                "latest_historical_report_selected",
                "historical_fallback_diagnostic_only",
                "not_target_reporting_period",
            ]
        )
    else:
        reasons.append("no_historical_annual_ifrs_available")
    if historical_interim_count:
        reasons.append("historical_interim_or_quarterly_available")
    if historical_wrong_standard_count:
        reasons.append("historical_wrong_standard_available")
    if ambiguous_count:
        reasons.append("historical_candidate_ambiguous")
    if target_period_count == 0:
        reasons.append("target_period_document_not_found")
    if exact_target_count == 0:
        reasons.append("target_period_evidence_required")
    reasons.extend(
        [
            "does_not_unblock_extraction",
            "does_not_unblock_import",
            "does_not_unblock_scoring",
        ]
    )
    return list(dict.fromkeys(reasons))


def _historical_fallback_note(row: dict[str, Any]) -> str:
    status = str(row.get("historical_fallback_status") or "")
    if status == "latest_historical_annual_ifrs_available":
        return "historical annual IFRS report is diagnostic only and cannot satisfy target-period evidence"
    if status == "exact_target_period_available_no_fallback_needed":
        return "exact target-period evidence exists; fallback is not needed and quality gate still controls readiness"
    if status == "no_historical_fallback_available":
        return "no older annual IFRS report was found in current exact-document diagnostics"
    if status == "only_interim_or_quarterly_historical_available":
        return "historical interim or quarterly reports cannot be used as annual target evidence"
    if status == "only_wrong_standard_historical_available":
        return "historical wrong-standard reports cannot be used when IFRS is required"
    if status == "operator_review_required_for_historical_candidate":
        return "historical-looking candidate is ambiguous and remains operator-review only"
    return "historical fallback registry is diagnostic only; it does not change strict evidence eligibility"


def _build_historical_fallback_registry_rows(
    args: argparse.Namespace,
    *,
    required_issuers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    all_documents_for_counters: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    availability_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in availability_operator_rows}
    queue_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in operator_review_queue}
    coverage_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in official_source_coverage_rows}
    rows: list[dict[str, Any]] = []
    for required in required_issuers:
        key = str(required.get("company_id") or "")
        availability = availability_by_key.get(key) or {}
        queue = queue_by_key.get(key) or {}
        coverage = coverage_by_key.get(key) or {}
        document_matches = _items_matching_required([*all_documents_for_counters, *documents], required)
        unique_documents = _exact_document_unique_url_items(document_matches)
        exact_target_documents = [item for item in unique_documents if _exact_document_is_downstream_eligible(item)]
        historical_reports = [
            item for item in unique_documents if _historical_fallback_is_historical_report(item, args)
        ]
        historical_annual = [
            item for item in unique_documents if _exact_document_is_historical_annual_ifrs(item, args)
        ]
        historical_interim = [
            item
            for item in unique_documents
            if _historical_fallback_is_historical(item, args) and _exact_document_is_interim_or_quarterly(item)
        ]
        historical_wrong_standard = [
            item for item in unique_documents if _historical_fallback_is_wrong_standard(item, args)
        ]
        ambiguous_historical = [
            item for item in unique_documents if _historical_fallback_requires_operator_review(item, args)
        ]
        latest = _exact_document_latest_historical(unique_documents, args)
        historical_years = sorted(
            {
                _exact_document_int_year(item.get("document_period_year"))
                for item in historical_annual
                if _exact_document_int_year(item.get("document_period_year")) is not None
            }
        )
        exact_target_count = len(exact_target_documents)
        historical_annual_count = len(historical_annual)
        status = _historical_fallback_status(
            exact_target_count=exact_target_count,
            historical_annual_count=historical_annual_count,
            ambiguous_count=len(ambiguous_historical),
            historical_interim_count=len(historical_interim),
            historical_wrong_standard_count=len(historical_wrong_standard),
            historical_report_count=len(historical_reports),
        )
        scope = "diagnostic_only" if status == "latest_historical_annual_ifrs_available" else "none"
        historical_fallback_allowed = scope == "diagnostic_only"
        can_use_as_target = bool(exact_target_count and status == "exact_target_period_available_no_fallback_needed")
        row = {
            "company_id": required.get("company_id"),
            "company_name": required.get("company_name") or availability.get("company_name") or "",
            "canonical_company_id": availability.get("canonical_company_id") or required.get("company_id"),
            "canonical_company_name": availability.get("canonical_company_name") or required.get("company_name") or "",
            "target_reporting_period": str(getattr(args, "report_period", "") or ""),
            "required_report_type": str(getattr(args, "report_type", "") or ""),
            "required_standard": str(getattr(args, "accounting_standard", "") or ""),
            "historical_fallback_status": status,
            "historical_fallback_scope": scope,
            "historical_fallback_allowed": historical_fallback_allowed,
            "latest_available_period": latest.get("document_period_year") if latest else "",
            "latest_available_report_type": "annual" if latest else "",
            "latest_available_standard": "IFRS" if latest else "",
            "latest_available_document_url": latest.get("document_url") if latest else "",
            "latest_available_document_title": latest.get("document_title") if latest else "",
            "latest_available_document_date": latest.get("document_date") if latest else "",
            "latest_available_source_page_url": latest.get("source_page_url") if latest else "",
            "latest_available_source_type": latest.get("source_type") if latest else "",
            "historical_report_count": len(historical_reports),
            "historical_annual_ifrs_document_count": historical_annual_count,
            "historical_annual_ifrs_periods": historical_years,
            "historical_annual_ifrs_latest_period": historical_years[-1] if historical_years else "",
            "historical_annual_ifrs_oldest_period": historical_years[0] if historical_years else "",
            "target_period_document_count": availability.get("target_period_document_count", 0),
            "exact_target_period_document_count": availability.get("exact_target_period_document_count", exact_target_count),
            "interim_or_quarterly_document_count": availability.get("interim_or_quarterly_document_count", len(historical_interim)),
            "wrong_standard_document_count": availability.get("wrong_standard_document_count", len(historical_wrong_standard)),
            "placeholder_not_found_count": availability.get("placeholder_not_found_count", 0),
            "availability_status": availability.get("availability_status") or "",
            "deadline_status": availability.get("deadline_status") or "",
            "coverage_status": coverage.get("coverage_status") or "",
            "coverage_grade": coverage.get("coverage_grade") or "",
            "coverage_score": coverage.get("coverage_score", ""),
            "queue_action_type": queue.get("queue_action_type") or "",
            "queue_priority": queue.get("queue_priority") or "",
            "queue_status": queue.get("queue_status") or "",
            "can_use_as_target_period_evidence": can_use_as_target,
            "can_use_for_value_extraction": False,
            "can_use_for_import": False,
            "can_use_for_scoring": False,
            "can_use_for_paper_trading": False,
            "operator_action": availability.get("operator_action") or "",
            "recommended_next_step": availability.get("recommended_next_step") or "",
            "coverage_operator_action": coverage.get("coverage_operator_action") or "",
            "gate_status": availability.get("gate_status") or "",
            "gate_passed": bool(availability.get("gate_passed")),
            "ready_for_value_extraction": bool(availability.get("ready_for_value_extraction")),
            "ready_for_import": bool(availability.get("ready_for_import")),
        }
        row["historical_fallback_reason_codes"] = _historical_fallback_reason_codes(
            status,
            historical_annual_count=historical_annual_count,
            historical_interim_count=len(historical_interim),
            historical_wrong_standard_count=len(historical_wrong_standard),
            ambiguous_count=len(ambiguous_historical),
            target_period_count=int(row.get("target_period_document_count") or 0),
            exact_target_count=exact_target_count,
        )
        row["diagnostic_only_reason"] = _historical_fallback_note(row)
        rows.append(row)
    return sorted(rows, key=lambda item: (str(item.get("company_id") or ""), str(item.get("company_name") or "")))


def _historical_fallback_registry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "historical_fallback_registry_issuer_count": len(rows),
        "historical_fallback_registry_report_count": sum(
            int(row.get("historical_annual_ifrs_document_count") or 0) for row in rows
        ),
        "historical_fallback_registry_latest_report_count": sum(
            1 for row in rows if row.get("historical_fallback_status") == "latest_historical_annual_ifrs_available"
        ),
        "historical_fallback_registry_diagnostic_only_count": sum(
            1 for row in rows if row.get("historical_fallback_scope") == "diagnostic_only"
        ),
        "historical_fallback_registry_target_evidence_count": sum(
            1 for row in rows if row.get("can_use_as_target_period_evidence")
        ),
        "historical_fallback_registry_extraction_ready_count": sum(
            1 for row in rows if row.get("ready_for_value_extraction")
        ),
        "historical_fallback_registry_import_ready_count": sum(
            1 for row in rows if row.get("ready_for_import")
        ),
        "historical_fallback_registry_status_counts": _count_by_key(rows, "historical_fallback_status"),
    }


def _build_historical_fallback_registry_report(
    args: argparse.Namespace,
    *,
    status: str,
    required_issuers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    all_documents_for_counters: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _build_historical_fallback_registry_rows(
        args,
        required_issuers=required_issuers,
        documents=documents,
        all_documents_for_counters=all_documents_for_counters,
        availability_operator_rows=availability_operator_rows,
        operator_review_queue=operator_review_queue,
        official_source_coverage_rows=official_source_coverage_rows,
    )
    return {
        "status": status,
        "mode": "historical-fallback-registry",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": _historical_fallback_registry_summary(rows),
        "issuers": rows,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _reporting_readiness_status(row: dict[str, Any]) -> str:
    target_evidence = bool(row.get("target_evidence_available"))
    if row.get("ready_for_value_extraction") and row.get("gate_passed") and target_evidence:
        return "ready_for_extraction_preview"
    if row.get("availability_status") == "placeholder_not_found":
        return "blocked_placeholder_not_found"
    coverage_status = str(row.get("coverage_status") or "")
    if coverage_status.startswith("weak_") or coverage_status == "missing_official_sources":
        return "blocked_weak_source_coverage"
    if not target_evidence or int(row.get("exact_target_period_document_count") or 0) == 0:
        return "blocked_missing_target_evidence"
    if row.get("historical_fallback_scope") == "diagnostic_only" and not target_evidence:
        return "blocked_historical_fallback_only"
    if row.get("manual_review_required") or row.get("queue_action_type") == "review_exact_document_candidate":
        return "blocked_operator_review_required"
    if row.get("deadline_status") == "after_primary_deadline_within_grace_window" and not target_evidence:
        return "blocked_after_primary_deadline_target_missing"
    if row.get("gate_status") == "failed" or not row.get("gate_passed"):
        return "blocked_quality_gate_failed"
    return "blocked_quality_gate_failed"


def _reporting_readiness_grade(row: dict[str, Any]) -> str:
    status = str(row.get("reporting_readiness_status") or "")
    if status == "ready_for_extraction_preview":
        return "ready"
    if status == "blocked_historical_fallback_only":
        return "diagnostic_only"
    if row.get("manual_review_required") or (
        row.get("queue_status") == "open"
        and row.get("queue_action_type") not in {"", "no_operator_action_required"}
    ):
        return "operator_required"
    return "blocked"


def _reporting_readiness_reason_codes(row: dict[str, Any]) -> list[str]:
    reasons = [str(row.get("reporting_readiness_status") or "")]
    target_evidence = bool(row.get("target_evidence_available"))
    exact_target_count = int(row.get("exact_target_period_document_count") or 0)
    if not target_evidence or exact_target_count == 0:
        reasons.extend(["missing_exact_target_period_annual_ifrs", "target_period_evidence_required"])
    if not row.get("gate_passed"):
        reasons.append("quality_gate_failed")
    if not row.get("ready_for_value_extraction"):
        reasons.append("not_ready_for_value_extraction")
    if not row.get("ready_for_import"):
        reasons.append("not_ready_for_import")
    if row.get("availability_status") == "placeholder_not_found":
        reasons.append("placeholder_not_found")
    coverage_status = str(row.get("coverage_status") or "")
    if coverage_status.startswith("weak_") or coverage_status == "missing_official_sources":
        reasons.append("weak_source_coverage")
    if coverage_status == "weak_no_reviewed_seed":
        reasons.append("no_valid_reviewed_official_seed")
    if row.get("historical_fallback_scope") == "diagnostic_only":
        reasons.extend(["historical_fallback_diagnostic_only", "historical_fallback_not_target_evidence"])
    if row.get("deadline_status") == "after_primary_deadline_within_grace_window":
        reasons.append("deadline_after_primary_within_grace")
    if row.get("is_blocking_next_stage") or row.get("queue_status") == "open":
        reasons.append("operator_action_required")
    if row.get("manual_review_required"):
        reasons.append("manual_review_required")
    if row.get("reporting_readiness_status") == "ready_for_extraction_preview":
        reasons.append("strict_quality_gate_ready")
    return [reason for reason in dict.fromkeys(reasons) if reason]


def _reporting_readiness_blocking_layers(row: dict[str, Any]) -> list[str]:
    layers: list[str] = []
    target_evidence = bool(row.get("target_evidence_available"))
    if row.get("availability_status") == "placeholder_not_found" or not target_evidence:
        layers.append("availability")
    if not row.get("gate_passed") or not row.get("ready_for_value_extraction"):
        layers.append("quality_gate")
    coverage_status = str(row.get("coverage_status") or "")
    if coverage_status.startswith("weak_") or coverage_status == "missing_official_sources":
        layers.append("source_coverage")
    if row.get("is_blocking_next_stage") or row.get("manual_review_required") or row.get("queue_status") == "open":
        layers.append("operator_queue")
    if row.get("historical_fallback_scope") == "diagnostic_only":
        layers.append("historical_fallback")
    return list(dict.fromkeys(layers))


def _reporting_readiness_primary_blocker(status: str) -> str:
    return {
        "ready_for_extraction_preview": "none",
        "blocked_placeholder_not_found": "placeholder_not_found",
        "blocked_weak_source_coverage": "weak_source_coverage",
        "blocked_missing_target_evidence": "missing_exact_target_period_annual_ifrs",
        "blocked_historical_fallback_only": "historical_fallback_diagnostic_only",
        "blocked_operator_review_required": "operator_action_required",
        "blocked_after_primary_deadline_target_missing": "deadline_after_primary_within_grace",
        "blocked_quality_gate_failed": "quality_gate_failed",
    }.get(status, "quality_gate_failed")


def _reporting_readiness_action(status: str) -> tuple[str, str]:
    mapping = {
        "blocked_placeholder_not_found": (
            "fill_exact_official_document_url_or_improve_official_sources",
            "Fill exact official annual IFRS report URL. Do not use landing pages.",
        ),
        "blocked_weak_source_coverage": (
            "review_or_promote_official_seed",
            "Promote at least one valid official reporting/disclosure source before extraction can be considered.",
        ),
        "blocked_missing_target_evidence": (
            "find_or_verify_exact_target_period_annual_ifrs_report",
            "Exact target-period annual IFRS report is required before extraction preview.",
        ),
        "blocked_historical_fallback_only": (
            "keep_historical_report_diagnostic_only_and_continue_target_search",
            "Historical report is diagnostic-only and cannot be used for target-period extraction.",
        ),
        "blocked_operator_review_required": (
            "complete_operator_review_queue_action",
            "Complete the open operator review action before extraction can be considered.",
        ),
        "blocked_after_primary_deadline_target_missing": (
            "review_sources_or_wait_grace",
            "Primary deadline has passed and target evidence is still missing; review official sources or wait until conservative grace date.",
        ),
        "blocked_quality_gate_failed": (
            "fix_quality_gate_blockers_before_extraction",
            "Fix strict quality-gate blockers before extraction preview can be considered.",
        ),
        "ready_for_extraction_preview": (
            "proceed_to_controlled_extraction_preview",
            "Existing quality gate indicates readiness for extraction preview. Do not import automatically.",
        ),
    }
    return mapping.get(status, mapping["blocked_quality_gate_failed"])


def _reporting_readiness_note(row: dict[str, Any]) -> str:
    if row.get("reporting_readiness_status") == "ready_for_extraction_preview":
        return "strict quality gate allows controlled extraction preview; import, scoring, and paper trading remain disabled here"
    if row.get("historical_fallback_scope") == "diagnostic_only":
        return "historical fallback is diagnostic only and does not unlock extraction, import, scoring, or paper trading"
    if row.get("availability_status") == "placeholder_not_found":
        return "exact target-period annual IFRS URL is missing; extraction remains blocked"
    return "readiness diagnostic only; strict quality gate remains the source of truth"


def _build_reporting_readiness_matrix_rows(
    args: argparse.Namespace,
    *,
    required_issuers: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
    historical_fallback_registry_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    availability_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in availability_operator_rows}
    queue_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in operator_review_queue}
    coverage_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in official_source_coverage_rows}
    fallback_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in historical_fallback_registry_rows}
    rows: list[dict[str, Any]] = []
    for required in required_issuers:
        key = str(required.get("company_id") or "")
        availability = availability_by_key.get(key) or {}
        queue = queue_by_key.get(key) or {}
        coverage = coverage_by_key.get(key) or {}
        fallback = fallback_by_key.get(key) or {}
        target_evidence = bool(availability.get("can_use_as_target_period_evidence"))
        row = {
            "company_id": required.get("company_id"),
            "company_name": required.get("company_name") or availability.get("company_name") or "",
            "canonical_company_id": availability.get("canonical_company_id") or required.get("company_id"),
            "canonical_company_name": availability.get("canonical_company_name") or required.get("company_name") or "",
            "target_reporting_period": str(getattr(args, "report_period", "") or ""),
            "required_report_type": str(getattr(args, "report_type", "") or ""),
            "required_standard": str(getattr(args, "accounting_standard", "") or ""),
            "availability_status": availability.get("availability_status") or "",
            "deadline_status": availability.get("deadline_status") or "",
            "can_use_as_target_period_evidence": target_evidence,
            "target_evidence_available": target_evidence,
            "exact_target_period_document_count": availability.get("exact_target_period_document_count", 0),
            "target_period_document_count": availability.get("target_period_document_count", 0),
            "gate_status": availability.get("gate_status") or "",
            "gate_passed": bool(availability.get("gate_passed")),
            "gate_reason": availability.get("gate_reason") or "",
            "ready_for_value_extraction": bool(availability.get("ready_for_value_extraction")),
            "ready_for_import": bool(availability.get("ready_for_import")),
            "coverage_status": coverage.get("coverage_status") or "",
            "coverage_grade": coverage.get("coverage_grade") or "",
            "coverage_score": coverage.get("coverage_score", ""),
            "coverage_operator_action": coverage.get("coverage_operator_action") or "",
            "historical_fallback_status": fallback.get("historical_fallback_status") or "",
            "historical_fallback_scope": fallback.get("historical_fallback_scope") or "none",
            "latest_available_period": fallback.get("latest_available_period") or "",
            "latest_available_report_type": fallback.get("latest_available_report_type") or "",
            "latest_available_standard": fallback.get("latest_available_standard") or "",
            "latest_available_document_url": fallback.get("latest_available_document_url") or "",
            "can_use_for_value_extraction": bool(fallback.get("can_use_for_value_extraction")),
            "can_use_for_import": bool(fallback.get("can_use_for_import")),
            "can_use_for_scoring": bool(fallback.get("can_use_for_scoring")),
            "can_use_for_paper_trading": bool(fallback.get("can_use_for_paper_trading")),
            "queue_action_type": queue.get("queue_action_type") or "",
            "queue_priority": queue.get("queue_priority") or "",
            "queue_status": queue.get("queue_status") or "",
            "manual_review_required": bool(queue.get("manual_review_required")),
            "is_blocking_next_stage": bool(queue.get("is_blocking_next_stage")),
            "blocked_stage": queue.get("blocked_stage") or "",
            "extraction_allowed": bool(availability.get("ready_for_value_extraction")),
            "import_allowed": bool(availability.get("ready_for_import")),
            "scoring_allowed": False,
            "paper_trading_allowed": False,
        }
        status = _reporting_readiness_status(row)
        row["reporting_readiness_status"] = status
        row["reporting_readiness_grade"] = _reporting_readiness_grade(row)
        row["primary_blocker"] = _reporting_readiness_primary_blocker(status)
        row["blocking_layers"] = _reporting_readiness_blocking_layers(row)
        row["reporting_readiness_reason_codes"] = _reporting_readiness_reason_codes(row)
        next_action, instruction = _reporting_readiness_action(status)
        row["next_required_action"] = next_action
        row["operator_instruction"] = instruction
        row["readiness_note"] = _reporting_readiness_note(row)
        rows.append(row)
    return sorted(rows, key=lambda item: (str(item.get("company_id") or ""), str(item.get("company_name") or "")))


def _reporting_readiness_matrix_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocker_counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("reporting_readiness_reason_codes") or []:
            blocker_counts[str(reason)] = blocker_counts.get(str(reason), 0) + 1
    return {
        "reporting_readiness_issuer_count": len(rows),
        "reporting_readiness_ready_count": sum(
            1 for row in rows if row.get("reporting_readiness_status") == "ready_for_extraction_preview"
        ),
        "reporting_readiness_blocked_count": sum(
            1 for row in rows if row.get("reporting_readiness_status") != "ready_for_extraction_preview"
        ),
        "reporting_readiness_needs_operator_count": sum(
            1 for row in rows if row.get("reporting_readiness_grade") == "operator_required"
        ),
        "reporting_readiness_target_evidence_available_count": sum(
            1 for row in rows if row.get("target_evidence_available")
        ),
        "reporting_readiness_gate_passed_count": sum(1 for row in rows if row.get("gate_passed")),
        "reporting_readiness_historical_only_count": sum(
            1
            for row in rows
            if row.get("historical_fallback_scope") == "diagnostic_only"
            and not row.get("target_evidence_available")
        ),
        "reporting_readiness_source_coverage_blocked_count": sum(
            1
            for row in rows
            if str(row.get("coverage_status") or "").startswith("weak_")
            or row.get("coverage_status") == "missing_official_sources"
        ),
        "reporting_readiness_status_counts": _count_by_key(rows, "reporting_readiness_status"),
        "reporting_readiness_blocker_counts": dict(sorted(blocker_counts.items())),
    }


def _build_reporting_readiness_matrix_report(
    args: argparse.Namespace,
    *,
    status: str,
    required_issuers: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
    historical_fallback_registry_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _build_reporting_readiness_matrix_rows(
        args,
        required_issuers=required_issuers,
        availability_operator_rows=availability_operator_rows,
        operator_review_queue=operator_review_queue,
        official_source_coverage_rows=official_source_coverage_rows,
        historical_fallback_registry_rows=historical_fallback_registry_rows,
    )
    return {
        "status": status,
        "mode": "reporting-readiness-matrix",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": _reporting_readiness_matrix_summary(rows),
        "issuers": rows,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _operator_resolution_action_type(row: dict[str, Any]) -> str:
    readiness = str(row.get("reporting_readiness_status") or "")
    coverage = str(row.get("coverage_status") or "")
    deadline = str(row.get("deadline_status") or "")
    if readiness == "ready_for_extraction_preview":
        return "no_operator_resolution_required"
    if readiness == "blocked_placeholder_not_found" or row.get("queue_action_type") == "fill_exact_document_url":
        return "fill_exact_document_url"
    if coverage.startswith("weak_") or coverage == "missing_official_sources":
        return "review_or_promote_official_seed"
    if deadline == "after_conservative_grace_window" and not row.get("target_evidence_available"):
        return "escalate_missing_target_report"
    if (
        coverage == "strong_but_target_report_missing"
        and deadline == "after_primary_deadline_within_grace_window"
        and not row.get("target_evidence_available")
    ):
        return "verify_target_report_publication"
    if not row.get("target_evidence_available") and (
        row.get("queue_status") == "waiting"
        or deadline in {"before_primary_deadline", "after_primary_deadline_within_grace_window"}
    ):
        return "review_sources_or_wait_grace"
    if row.get("historical_fallback_scope") == "diagnostic_only":
        return "keep_historical_fallback_diagnostic_only"
    return "review_sources_or_wait_grace"


def _operator_resolution_config(action_type: str) -> dict[str, Any]:
    configs = {
        "fill_exact_document_url": {
            "resolution_action_label": "Fill exact document URL",
            "resolution_priority": "high",
            "resolution_status": "open",
            "can_unblock_extraction_if_completed": True,
            "requires_exact_document_url": True,
            "requires_publication_verification": False,
            "requires_escalation": False,
            "is_wait_action": False,
            "is_diagnostic_only": False,
            "operator_instruction": "Fill exact official annual IFRS report URL and/or promote official reporting seed. Do not use landing pages or historical reports.",
        },
        "review_or_promote_official_seed": {
            "resolution_action_label": "Review or promote official seed",
            "resolution_priority": "high",
            "resolution_status": "open",
            "can_unblock_extraction_if_completed": True,
            "requires_exact_document_url": False,
            "requires_publication_verification": False,
            "requires_escalation": False,
            "is_wait_action": False,
            "is_diagnostic_only": False,
            "operator_instruction": "Review candidate source seeds and promote at least one official reporting or disclosure source before extraction can be considered.",
        },
        "verify_target_report_publication": {
            "resolution_action_label": "Verify target report publication",
            "resolution_priority": "medium",
            "resolution_status": "open",
            "can_unblock_extraction_if_completed": True,
            "requires_exact_document_url": False,
            "requires_publication_verification": True,
            "requires_escalation": False,
            "is_wait_action": False,
            "is_diagnostic_only": False,
            "operator_instruction": "Verify whether exact target-period annual IFRS report is published on official sources. If found, fill exact document URL. Historical fallback remains diagnostic-only.",
        },
        "review_sources_or_wait_grace": {
            "resolution_action_label": "Review sources or wait grace window",
            "resolution_priority": "low",
            "resolution_status": "waiting",
            "can_unblock_extraction_if_completed": False,
            "requires_exact_document_url": False,
            "requires_publication_verification": True,
            "requires_escalation": False,
            "is_wait_action": True,
            "is_diagnostic_only": False,
            "operator_instruction": "Monitor publication window or manually review official sources. Fill exact target-period annual IFRS URL only if found.",
        },
        "escalate_missing_target_report": {
            "resolution_action_label": "Escalate missing target report",
            "resolution_priority": "high",
            "resolution_status": "open",
            "can_unblock_extraction_if_completed": True,
            "requires_exact_document_url": False,
            "requires_publication_verification": True,
            "requires_escalation": True,
            "is_wait_action": False,
            "is_diagnostic_only": False,
            "operator_instruction": "Target annual IFRS report was not found after the conservative grace window. Review source coverage and verify report availability manually.",
        },
        "keep_historical_fallback_diagnostic_only": {
            "resolution_action_label": "Keep historical fallback diagnostic-only",
            "resolution_priority": "low",
            "resolution_status": "diagnostic_only",
            "can_unblock_extraction_if_completed": False,
            "requires_exact_document_url": False,
            "requires_publication_verification": False,
            "requires_escalation": False,
            "is_wait_action": False,
            "is_diagnostic_only": True,
            "operator_instruction": "Keep historical report as diagnostic-only context and continue searching for exact target-period annual IFRS evidence.",
        },
        "no_operator_resolution_required": {
            "resolution_action_label": "No operator resolution required",
            "resolution_priority": "low",
            "resolution_status": "resolved_or_not_required",
            "can_unblock_extraction_if_completed": False,
            "requires_exact_document_url": False,
            "requires_publication_verification": False,
            "requires_escalation": False,
            "is_wait_action": False,
            "is_diagnostic_only": False,
            "operator_instruction": "No operator resolution is required. Follow the controlled extraction preview workflow; do not import automatically.",
        },
    }
    return configs.get(action_type, configs["review_sources_or_wait_grace"])


def _operator_resolution_reason_codes(row: dict[str, Any]) -> list[str]:
    reasons = [str(row.get("resolution_action_type") or "")]
    reasons.extend(str(reason) for reason in (row.get("reporting_readiness_reason_codes") or []))
    if not row.get("target_evidence_available"):
        reasons.extend(["missing_exact_target_period_annual_ifrs", "target_period_evidence_required"])
    if row.get("availability_status") == "placeholder_not_found":
        reasons.append("placeholder_not_found")
    coverage = str(row.get("coverage_status") or "")
    if coverage.startswith("weak_") or coverage == "missing_official_sources":
        reasons.append("weak_source_coverage")
    if coverage == "weak_no_reviewed_seed":
        reasons.append("no_valid_reviewed_official_seed")
    if row.get("deadline_status") == "after_primary_deadline_within_grace_window":
        reasons.append("after_primary_deadline_within_grace_window")
    if row.get("historical_fallback_scope") == "diagnostic_only":
        reasons.extend(["historical_fallback_diagnostic_only", "historical_fallback_not_target_evidence"])
    if row.get("queue_status") == "open":
        reasons.append("operator_queue_open")
    if row.get("manual_review_required"):
        reasons.append("manual_review_required")
    if not row.get("gate_passed"):
        reasons.append("quality_gate_failed")
    return [reason for reason in dict.fromkeys(reasons) if reason]


def _operator_resolution_id(row: dict[str, Any], action_type: str) -> str:
    company_id = row.get("company_id") or row.get("canonical_company_id") or ""
    target = row.get("target_reporting_period") or ""
    report_type = row.get("required_report_type") or ""
    standard = row.get("required_standard") or ""
    return f"financial_report_resolution:{company_id}:{target}:{report_type}:{standard}:{action_type}"


def _operator_resolution_validation_hint(row: dict[str, Any]) -> str:
    if row.get("requires_exact_document_url"):
        return "Provide an exact official target-period annual IFRS report page or PDF URL; landing pages and historical reports are not accepted."
    if row.get("requires_official_seed_review"):
        return "Review/promote official reporting or disclosure seed before exact document discovery can be trusted."
    if row.get("requires_publication_verification"):
        return "Verify publication on official sources; fill exact document URL only if target-period annual IFRS report is found."
    if row.get("is_diagnostic_only"):
        return "Diagnostic-only row; do not use historical fallback as target-period evidence."
    return "No manual template fields are required for this row."


def _operator_resolution_safety_note(row: dict[str, Any]) -> str:
    if row.get("latest_historical_document_url"):
        return "Historical fallback is diagnostic-only, is not target evidence, and must not be pasted into operator_fill_exact_document_url."
    return "Resolution pack is preview-only and does not apply operator decisions or mutate pipeline state."


def _build_operator_resolution_pack_rows(
    args: argparse.Namespace,
    *,
    required_issuers: list[dict[str, Any]],
    reporting_readiness_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
    historical_fallback_registry_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    readiness_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in reporting_readiness_rows}
    queue_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in operator_review_queue}
    availability_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in availability_operator_rows}
    coverage_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in official_source_coverage_rows}
    fallback_by_key = {str(row.get("canonical_company_id") or row.get("company_id") or ""): row for row in historical_fallback_registry_rows}
    rows: list[dict[str, Any]] = []
    for required in required_issuers:
        key = str(required.get("company_id") or "")
        readiness = readiness_by_key.get(key) or {}
        queue = queue_by_key.get(key) or {}
        availability = availability_by_key.get(key) or {}
        coverage = coverage_by_key.get(key) or {}
        fallback = fallback_by_key.get(key) or {}
        action_type = _operator_resolution_action_type(readiness)
        config = dict(_operator_resolution_config(action_type))
        if (
            action_type == "review_sources_or_wait_grace"
            and str(readiness.get("deadline_status") or availability.get("deadline_status") or "") != "before_primary_deadline"
        ):
            config.update(
                {
                    "resolution_priority": "medium",
                    "resolution_status": "open",
                    "can_unblock_extraction_if_completed": True,
                    "is_wait_action": False,
                }
            )
        requires_seed_review = bool(
            str(readiness.get("coverage_status") or "").startswith("weak_")
            or readiness.get("coverage_status") == "missing_official_sources"
        )
        operator_input_required = action_type in {
            "fill_exact_document_url",
            "review_or_promote_official_seed",
            "verify_target_report_publication",
            "escalate_missing_target_report",
        } or (
            action_type == "review_sources_or_wait_grace"
            and not bool(config.get("is_wait_action"))
        )
        row = {
            "resolution_id": _operator_resolution_id(readiness or required, action_type),
            "company_id": required.get("company_id"),
            "company_name": required.get("company_name") or readiness.get("company_name") or "",
            "canonical_company_id": readiness.get("canonical_company_id") or required.get("company_id"),
            "canonical_company_name": readiness.get("canonical_company_name") or required.get("company_name") or "",
            "target_reporting_period": str(getattr(args, "report_period", "") or ""),
            "required_report_type": str(getattr(args, "report_type", "") or ""),
            "required_standard": str(getattr(args, "accounting_standard", "") or ""),
            "resolution_action_type": action_type,
            **config,
            "requires_official_seed_review": requires_seed_review,
            "source_readiness_status": coverage.get("coverage_status") or readiness.get("coverage_status") or "",
            "reporting_readiness_status": readiness.get("reporting_readiness_status") or "",
            "primary_blocker": readiness.get("primary_blocker") or "",
            "blocking_layers": list(readiness.get("blocking_layers") or []),
            "availability_status": readiness.get("availability_status") or availability.get("availability_status") or "",
            "deadline_status": readiness.get("deadline_status") or availability.get("deadline_status") or "",
            "coverage_status": readiness.get("coverage_status") or coverage.get("coverage_status") or "",
            "coverage_grade": readiness.get("coverage_grade") or coverage.get("coverage_grade") or "",
            "historical_fallback_status": readiness.get("historical_fallback_status") or fallback.get("historical_fallback_status") or "",
            "historical_fallback_scope": readiness.get("historical_fallback_scope") or fallback.get("historical_fallback_scope") or "none",
            "queue_action_type": readiness.get("queue_action_type") or queue.get("queue_action_type") or "",
            "queue_priority": readiness.get("queue_priority") or queue.get("queue_priority") or "",
            "queue_status": readiness.get("queue_status") or queue.get("queue_status") or "",
            "target_evidence_available": bool(readiness.get("target_evidence_available")),
            "gate_status": readiness.get("gate_status") or availability.get("gate_status") or "",
            "gate_passed": bool(readiness.get("gate_passed")),
            "ready_for_value_extraction": bool(readiness.get("ready_for_value_extraction")),
            "ready_for_import": bool(readiness.get("ready_for_import")),
            "extraction_allowed": bool(readiness.get("extraction_allowed")),
            "import_allowed": bool(readiness.get("import_allowed")),
            "scoring_allowed": False,
            "paper_trading_allowed": False,
            "operator_input_required": operator_input_required,
            "operator_input_schema_version": "operator_resolution_pack_v1",
            "operator_fill_exact_document_url": "",
            "operator_fill_document_title": "",
            "operator_fill_document_date": "",
            "operator_fill_source_page_url": "",
            "operator_fill_source_type": "",
            "operator_fill_report_period": str(getattr(args, "report_period", "") or ""),
            "operator_fill_report_type": str(getattr(args, "report_type", "") or ""),
            "operator_fill_accounting_standard": str(getattr(args, "accounting_standard", "") or ""),
            "operator_fill_decision": "",
            "operator_fill_notes": "",
            "current_known_document_url": "",
            "current_known_source_page_url": queue.get("source_context") or "",
            "latest_historical_document_url": fallback.get("latest_available_document_url") or "",
            "latest_historical_period": fallback.get("latest_available_period") or "",
        }
        row["resolution_reason_codes"] = _operator_resolution_reason_codes(
            {**readiness, **row, "requires_official_seed_review": requires_seed_review}
        )
        row["validation_hint"] = _operator_resolution_validation_hint(row)
        row["safety_note"] = _operator_resolution_safety_note(row)
        rows.append(row)
    return sorted(rows, key=lambda item: str(item.get("resolution_id") or ""))


def _operator_resolution_pack_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "operator_resolution_pack_issuer_count": len(rows),
        "operator_resolution_pack_action_count": len(rows),
        "operator_resolution_pack_manual_action_count": sum(
            1 for row in rows if row.get("operator_input_required") and not row.get("is_wait_action")
        ),
        "operator_resolution_pack_wait_action_count": sum(1 for row in rows if row.get("is_wait_action")),
        "operator_resolution_pack_can_unblock_extraction_count": sum(
            1 for row in rows if row.get("can_unblock_extraction_if_completed")
        ),
        "operator_resolution_pack_target_document_fill_count": sum(
            1 for row in rows if row.get("resolution_action_type") == "fill_exact_document_url"
        ),
        "operator_resolution_pack_source_review_count": sum(
            1 for row in rows if row.get("requires_official_seed_review")
        ),
        "operator_resolution_pack_escalation_count": sum(
            1 for row in rows if row.get("requires_escalation")
        ),
        "operator_resolution_pack_status_counts": _count_by_key(rows, "resolution_status"),
        "operator_resolution_pack_action_type_counts": _count_by_key(rows, "resolution_action_type"),
        "operator_resolution_pack_priority_counts": _count_by_key(rows, "resolution_priority"),
    }


def _build_operator_resolution_pack_report(
    args: argparse.Namespace,
    *,
    status: str,
    required_issuers: list[dict[str, Any]],
    reporting_readiness_rows: list[dict[str, Any]],
    operator_review_queue: list[dict[str, Any]],
    availability_operator_rows: list[dict[str, Any]],
    official_source_coverage_rows: list[dict[str, Any]],
    historical_fallback_registry_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _build_operator_resolution_pack_rows(
        args,
        required_issuers=required_issuers,
        reporting_readiness_rows=reporting_readiness_rows,
        operator_review_queue=operator_review_queue,
        availability_operator_rows=availability_operator_rows,
        official_source_coverage_rows=official_source_coverage_rows,
        historical_fallback_registry_rows=historical_fallback_registry_rows,
    )
    return {
        "status": status,
        "mode": "operator-resolution-pack",
        "target_reporting_period": str(getattr(args, "report_period", "") or ""),
        "required_report_type": str(getattr(args, "report_type", "") or ""),
        "required_standard": str(getattr(args, "accounting_standard", "") or ""),
        "summary": _operator_resolution_pack_summary(rows),
        "resolutions": rows,
        "warnings": warnings or [],
        "errors": errors or [],
        **SAFETY_FLAGS,
    }


def _operator_resolution_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on"}


def _operator_resolution_base_row(input_row: dict[str, Any], source_row: dict[str, Any]) -> dict[str, Any]:
    merged = dict(source_row)
    merged.update({key: value for key, value in input_row.items() if value is not None})
    return merged


def _operator_resolution_known_hosts(*rows: dict[str, Any]) -> set[str]:
    fields = (
        "current_known_document_url",
        "current_known_source_page_url",
        "latest_historical_document_url",
        "operator_fill_source_page_url",
    )
    hosts: set[str] = set()
    for row in rows:
        for field in fields:
            for item in _split_source_context_urls(str(row.get(field) or "")):
                parsed = urllib.parse.urlparse(item)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    host = _host(item)
                    if host:
                        hosts.add(host)
    return hosts


def _operator_resolution_domain_status(url: str, *, known_hosts: set[str]) -> str:
    classification = classify_source_url(url, allow_unknown_source=False)
    status = classification.get("status") or "unknown_error"
    host = _host(url)
    if status == "official":
        return "official"
    if status != "blocked" and host and host in known_hosts:
        return "official_known_source_pack_host"
    if status == "blocked":
        return "blocked"
    return "unofficial_or_unknown"


def _operator_resolution_validation_add(errors: list[str], reasons: list[str], code: str) -> None:
    errors.append(code)
    reasons.append(code)


def _validate_operator_resolution_exact_document(
    args: argparse.Namespace,
    row: dict[str, Any],
    source_row: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    target = str(row.get("target_reporting_period") or "")
    required_type = str(row.get("required_report_type") or "")
    required_standard = str(row.get("required_standard") or "")
    filled_period = str(row.get("operator_fill_report_period") or "")
    filled_type = str(row.get("operator_fill_report_type") or "")
    filled_standard = str(row.get("operator_fill_accounting_standard") or "")
    raw_url = str(row.get("operator_fill_exact_document_url") or "").strip()
    url = _normalize_candidate_url(raw_url)
    title = str(row.get("operator_fill_document_title") or "")
    source_page_url = str(row.get("operator_fill_source_page_url") or "")
    latest_historical_url = str(row.get("latest_historical_document_url") or "")
    normalized_historical = _normalize_candidate_url(latest_historical_url)
    historical_used = bool(url and normalized_historical and url == normalized_historical)

    if not url:
        _operator_resolution_validation_add(errors, reasons, "invalid_or_missing_http_url")
    if target and filled_period and filled_period != target:
        _operator_resolution_validation_add(errors, reasons, "operator_report_period_mismatch")
    if required_type and filled_type and filled_type.casefold() != required_type.casefold():
        _operator_resolution_validation_add(errors, reasons, "operator_report_type_mismatch")
    if required_standard and filled_standard and filled_standard.casefold() != required_standard.casefold():
        _operator_resolution_validation_add(errors, reasons, "operator_accounting_standard_mismatch")
    if historical_used:
        _operator_resolution_validation_add(errors, reasons, "historical_fallback_url_used_as_exact_document")

    domain_status = ""
    document_kind = ""
    period_year = ""
    period_status = ""
    report_type_status = ""
    standard_status = ""
    if url:
        domain_status = _operator_resolution_domain_status(
            url,
            known_hosts=_operator_resolution_known_hosts(row, source_row),
        )
        if domain_status not in {"official", "official_known_source_pack_host"}:
            _operator_resolution_validation_add(errors, reasons, "unofficial_or_blocked_domain")
        row_args = _clone_args(
            args,
            report_period=target or getattr(args, "report_period", ""),
            report_type=required_type or getattr(args, "report_type", ""),
            accounting_standard=required_standard or getattr(args, "accounting_standard", ""),
            exact_document_allow_prior_year_fallback=False,
        )
        document_kind = classify_exact_document_kind(url, title, args=row_args)
        period = classify_exact_document_period(url, title, source_page_url, args=row_args)
        period_year = str(period.get("document_period_year") or "")
        period_status = str(period.get("document_period_status") or "")
        report_type = classify_exact_document_report_type(
            url,
            title,
            args=row_args,
            period_quarter=str(period.get("document_period_quarter") or ""),
        )
        report_type_status = str(report_type.get("report_type_match_status") or "")
        standard = classify_exact_document_accounting_standard(url, title, args=row_args)
        standard_status = str(standard.get("accounting_standard_match_status") or "")
        if document_kind != "exact_report_document":
            if document_kind in EXACT_DOCUMENT_CATEGORY_KINDS or document_kind == "generic_navigation_page":
                _operator_resolution_validation_add(errors, reasons, "landing_page_not_allowed")
            _operator_resolution_validation_add(errors, reasons, "not_exact_report_document")
        if period_status != "target_period":
            if period_status in {"wrong_period", "prior_period_fallback_candidate", "period_conflict"}:
                _operator_resolution_validation_add(errors, reasons, "wrong_period")
                reasons.append("not_target_reporting_period")
            else:
                _operator_resolution_validation_add(errors, reasons, "unknown_period")
        if report_type_status != "annual_match":
            if report_type_status == "interim_or_quarterly_mismatch":
                _operator_resolution_validation_add(errors, reasons, "interim_or_quarterly_not_allowed_for_annual")
            else:
                _operator_resolution_validation_add(errors, reasons, "unknown_report_type")
        if standard_status != "standard_match":
            if standard_status == "standard_mismatch":
                _operator_resolution_validation_add(errors, reasons, "wrong_accounting_standard")
            else:
                _operator_resolution_validation_add(errors, reasons, "unknown_accounting_standard")

    valid = not errors
    return {
        "validation_status": "valid_for_future_controlled_intake_review" if valid else "invalid_operator_input",
        "validation_severity": "info" if valid else "error",
        "validation_reason_codes": ["exact_document_found", *reasons],
        "validation_errors": list(dict.fromkeys(errors)),
        "validation_warnings": list(dict.fromkeys(warnings)),
        "url_validation_status": "valid_http_url" if url else "invalid_or_missing_http_url",
        "domain_validation_status": domain_status or ("not_checked" if not url else "unofficial_or_unknown"),
        "document_kind": document_kind,
        "document_period_year": period_year,
        "document_period_status": period_status,
        "report_type_match_status": report_type_status,
        "accounting_standard_match_status": standard_status,
        "historical_fallback_url_used_as_exact_document": historical_used,
        "can_use_for_future_intake_review": valid,
    }


def _operator_resolution_manual_required(row: dict[str, Any]) -> bool:
    action_type = str(row.get("resolution_action_type") or "")
    if _operator_resolution_bool(row.get("operator_input_required")):
        return True
    return action_type in {
        "fill_exact_document_url",
        "review_or_promote_official_seed",
        "verify_target_report_publication",
        "escalate_missing_target_report",
    }


def _operator_resolution_validation_base_result(
    *,
    status: str,
    severity: str,
    reasons: list[str],
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "validation_status": status,
        "validation_severity": severity,
        "validation_reason_codes": list(dict.fromkeys(reasons)),
        "validation_errors": list(dict.fromkeys(errors or [])),
        "validation_warnings": list(dict.fromkeys(warnings or [])),
        "url_validation_status": "not_checked",
        "domain_validation_status": "not_checked",
        "document_kind": "",
        "document_period_year": "",
        "document_period_status": "",
        "report_type_match_status": "",
        "accounting_standard_match_status": "",
        "historical_fallback_url_used_as_exact_document": False,
        "can_use_for_future_intake_review": False,
    }


def validate_operator_resolution_row(
    args: argparse.Namespace,
    input_row: dict[str, Any],
    *,
    input_columns: set[str],
    source_row: dict[str, Any],
    source_pack_provided: bool,
) -> dict[str, Any]:
    row = _operator_resolution_base_row(input_row, source_row)
    missing_critical = [field for field in OPERATOR_RESOLUTION_VALIDATION_CRITICAL_COLUMNS if field not in input_row]
    missing_optional = [
        field
        for field in OPERATOR_RESOLUTION_VALIDATION_EXPECTED_COLUMNS
        if field not in input_columns and field not in OPERATOR_RESOLUTION_VALIDATION_CRITICAL_COLUMNS
    ]
    row_warnings = [f"missing_optional_column:{field}" for field in missing_optional]
    if source_pack_provided and not source_row:
        row_warnings.append("source_pack_row_missing")

    decision = str(row.get("operator_fill_decision") or "").strip().casefold()
    action_type = str(row.get("resolution_action_type") or "")
    exact_url = str(row.get("operator_fill_exact_document_url") or "").strip()
    reasons: list[str] = []
    errors: list[str] = []
    if missing_critical:
        for field in missing_critical:
            errors.append(f"missing_critical_column:{field}")
        result = _operator_resolution_validation_base_result(
            status="invalid_operator_input",
            severity="error",
            reasons=["missing_critical_column"],
            errors=errors,
            warnings=row_warnings,
        )
    elif action_type == "no_operator_resolution_required":
        result = _operator_resolution_validation_base_result(
            status="no_action_required",
            severity="info",
            reasons=["no_operator_resolution_required"],
            warnings=row_warnings,
        )
    elif decision in {"", "pending"}:
        if action_type == "review_sources_or_wait_grace" and (
            _operator_resolution_bool(row.get("is_wait_action")) or row.get("resolution_status") == "waiting"
        ):
            result = _operator_resolution_validation_base_result(
                status="waiting",
                severity="info",
                reasons=["wait_until_grace_date"],
                warnings=row_warnings,
            )
        elif _operator_resolution_manual_required(row):
            reasons.append("operator_decision_required")
            if row.get("requires_exact_document_url") or action_type == "fill_exact_document_url":
                reasons.append("exact_document_url_required")
                errors.append("exact_document_url_required")
            result = _operator_resolution_validation_base_result(
                status="incomplete_operator_input",
                severity="warning",
                reasons=reasons,
                errors=errors,
                warnings=row_warnings,
            )
        else:
            result = _operator_resolution_validation_base_result(
                status="incomplete_operator_input",
                severity="warning",
                reasons=["operator_decision_required"],
                warnings=row_warnings,
            )
    elif decision not in OPERATOR_RESOLUTION_DECISIONS:
        result = _operator_resolution_validation_base_result(
            status="invalid_operator_input",
            severity="error",
            reasons=["invalid_operator_decision"],
            errors=["invalid_operator_decision"],
            warnings=row_warnings,
        )
    elif decision == "wait_until_grace_date":
        result = _operator_resolution_validation_base_result(
            status="waiting",
            severity="info",
            reasons=["wait_until_grace_date"],
            warnings=row_warnings,
        )
    elif decision in {"target_report_not_found", "seed_review_required", "escalate_missing_target_report"}:
        result = _operator_resolution_validation_base_result(
            status="diagnostic_only",
            severity="info",
            reasons=[decision],
            warnings=row_warnings,
        )
    elif decision == "reject_invalid_input":
        result = _operator_resolution_validation_base_result(
            status="invalid_operator_input",
            severity="error",
            reasons=["operator_rejected_input"],
            errors=["operator_rejected_input"],
            warnings=row_warnings,
        )
    else:
        if not exact_url:
            result = _operator_resolution_validation_base_result(
                status="incomplete_operator_input",
                severity="warning",
                reasons=["exact_document_url_required"],
                errors=["exact_document_url_required"],
                warnings=row_warnings,
            )
        else:
            result = _validate_operator_resolution_exact_document(args, row, source_row)
            result["validation_warnings"] = list(dict.fromkeys([*result.get("validation_warnings", []), *row_warnings]))

    status = result["validation_status"]
    next_step = {
        "valid_for_future_controlled_intake_review": "send_to_future_controlled_intake_review",
        "incomplete_operator_input": "complete_operator_resolution_template",
        "invalid_operator_input": "fix_or_reject_operator_input",
        "waiting": "wait_until_grace_date_or_recheck",
        "diagnostic_only": "keep_diagnostic_only",
        "no_action_required": "no_operator_action_required",
    }.get(status, "review_validation_result")
    note = (
        "Valid only for a future controlled intake review; nothing is applied automatically."
        if status == "valid_for_future_controlled_intake_review"
        else "Validation is preview-only and does not mutate intake, sources, extraction, import, scoring, or trading."
    )
    return {
        "resolution_id": row.get("resolution_id") or "",
        "company_id": row.get("company_id") or "",
        "company_name": row.get("company_name") or "",
        "target_reporting_period": row.get("target_reporting_period") or "",
        "required_report_type": row.get("required_report_type") or "",
        "required_standard": row.get("required_standard") or "",
        "operator_fill_decision": decision,
        "operator_fill_exact_document_url": row.get("operator_fill_exact_document_url") or "",
        "operator_fill_document_title": row.get("operator_fill_document_title") or "",
        "operator_fill_document_date": row.get("operator_fill_document_date") or "",
        "operator_fill_source_page_url": row.get("operator_fill_source_page_url") or "",
        "operator_fill_source_type": row.get("operator_fill_source_type") or "",
        "operator_fill_report_period": row.get("operator_fill_report_period") or "",
        "operator_fill_report_type": row.get("operator_fill_report_type") or "",
        "operator_fill_accounting_standard": row.get("operator_fill_accounting_standard") or "",
        "operator_fill_notes": row.get("operator_fill_notes") or "",
        **result,
        "latest_historical_document_url": row.get("latest_historical_document_url") or "",
        "latest_historical_period": row.get("latest_historical_period") or "",
        "would_update_document_intake": False,
        "would_promote_seed": False,
        "would_extract_values": False,
        "would_import_report": False,
        "would_mutate_scores": False,
        "would_trigger_paper_trading": False,
        "operator_next_step": next_step,
        "validation_note": note,
    }


def _build_operator_resolution_validation_rows(
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    input_columns: set[str],
    source_pack_by_id: dict[str, dict[str, Any]],
    source_pack_provided: bool,
) -> list[dict[str, Any]]:
    validation_rows = [
        validate_operator_resolution_row(
            args,
            row,
            input_columns=input_columns,
            source_row=source_pack_by_id.get(str(row.get("resolution_id") or ""), {}),
            source_pack_provided=source_pack_provided,
        )
        for row in rows
    ]
    return sorted(validation_rows, key=lambda item: str(item.get("resolution_id") or ""))


def _operator_resolution_validation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    error_counts: dict[str, int] = {}
    for row in rows:
        for code in row.get("validation_errors") or []:
            error_counts[str(code)] = error_counts.get(str(code), 0) + 1
    return {
        "operator_resolution_validation_row_count": len(rows),
        "operator_resolution_validation_valid_count": sum(
            1 for row in rows if row.get("validation_status") == "valid_for_future_controlled_intake_review"
        ),
        "operator_resolution_validation_incomplete_count": sum(
            1 for row in rows if row.get("validation_status") == "incomplete_operator_input"
        ),
        "operator_resolution_validation_invalid_count": sum(
            1 for row in rows if row.get("validation_status") == "invalid_operator_input"
        ),
        "operator_resolution_validation_future_intake_review_count": sum(
            1 for row in rows if row.get("can_use_for_future_intake_review")
        ),
        "operator_resolution_validation_historical_fallback_rejected_count": sum(
            1 for row in rows if row.get("historical_fallback_url_used_as_exact_document")
        ),
        "operator_resolution_validation_status_counts": _count_by_key(rows, "validation_status"),
        "operator_resolution_validation_error_counts": dict(sorted(error_counts.items())),
    }


def _build_operator_resolution_validation_report(
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    load_warnings: list[dict[str, Any]] | None = None,
    load_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = _operator_resolution_validation_summary(rows)
    load_warnings = load_warnings or []
    load_errors = load_errors or []
    if load_errors:
        status = "failed"
    elif load_warnings or summary["operator_resolution_validation_incomplete_count"] or summary["operator_resolution_validation_invalid_count"]:
        status = "warning"
    else:
        status = "passed"
    return {
        "status": status,
        "mode": "operator-resolution-validation",
        "summary": summary,
        **summary,
        "validation_rows": rows,
        "operator_resolution_input": _path_value(args.operator_resolution_input),
        "operator_resolution_source_pack_input": _path_value(args.operator_resolution_source_pack_input),
        "operator_resolution_validation_output": _path_value(args.operator_resolution_validation_output),
        "operator_resolution_validation_csv_output": _path_value(args.operator_resolution_validation_csv_output),
        "operator_resolution_validation_markdown_output": _path_value(args.operator_resolution_validation_markdown_output),
        "warnings": load_warnings,
        "errors": load_errors,
        "next_steps": _next_steps("operator-resolution-validation", status),
        "would_update_document_intake": False,
        "would_promote_seed": False,
        "would_extract_values": False,
        "would_import_report": False,
        **SAFETY_FLAGS,
    }


def _operator_resolution_apply_validation_status(row: dict[str, Any]) -> str:
    return str(row.get("validation_status") or "")


def _operator_resolution_apply_bool(value: Any) -> bool:
    return _operator_resolution_bool(value)


def _operator_resolution_apply_code_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [item.strip() for item in re.split(r"[;,|]", str(value)) if item.strip()]


def _operator_resolution_apply_strict_mismatch(row: dict[str, Any]) -> bool:
    return not (
        str(row.get("document_kind") or "") == "exact_report_document"
        and str(row.get("document_period_status") or "") == "target_period"
        and str(row.get("report_type_match_status") or "") == "annual_match"
        and str(row.get("accounting_standard_match_status") or "") == "standard_match"
    )


def _operator_resolution_apply_base_status(row: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    validation_status = _operator_resolution_apply_validation_status(row)
    reasons = [validation_status] if validation_status else ["blocked_missing_validation"]
    errors: list[str] = []
    if not validation_status:
        errors.append("blocked_missing_validation")
        return "blocked_missing_validation", reasons, errors
    if validation_status == "incomplete_operator_input":
        return "not_eligible_incomplete_validation", reasons, errors
    if validation_status == "invalid_operator_input":
        reasons.extend(_operator_resolution_apply_code_list(row.get("validation_reason_codes")))
        errors.extend(_operator_resolution_apply_code_list(row.get("validation_errors")))
        return "not_eligible_invalid_validation", list(dict.fromkeys(reasons)), list(dict.fromkeys(errors))
    if validation_status == "diagnostic_only":
        return "not_eligible_diagnostic_only", reasons, errors
    if validation_status == "waiting":
        return "not_eligible_waiting", reasons, errors
    if validation_status == "no_action_required":
        return "not_eligible_no_action_required", reasons, errors
    if validation_status != "valid_for_future_controlled_intake_review":
        errors.append("blocked_missing_validation")
        return "blocked_missing_validation", reasons, errors
    if not _operator_resolution_apply_bool(row.get("can_use_for_future_intake_review")):
        errors.append("can_use_for_future_intake_review_false")
        return "blocked_missing_validation", [*reasons, "can_use_for_future_intake_review_false"], errors
    if str(row.get("operator_fill_decision") or "").casefold() != "exact_document_found":
        errors.append("operator_decision_not_exact_document_found")
        return "blocked_missing_validation", [*reasons, "operator_decision_not_exact_document_found"], errors
    if not str(row.get("operator_fill_exact_document_url") or "").strip():
        errors.append("blocked_missing_exact_document_url")
        return "blocked_missing_exact_document_url", [*reasons, "blocked_missing_exact_document_url"], errors
    if _operator_resolution_apply_bool(row.get("historical_fallback_url_used_as_exact_document")):
        errors.append("historical_fallback_url_used_as_exact_document")
        return "not_eligible_invalid_validation", [*reasons, "historical_fallback_url_used_as_exact_document"], errors
    if _operator_resolution_apply_strict_mismatch(row):
        errors.append("blocked_strict_document_mismatch")
        return "blocked_strict_document_mismatch", [*reasons, "blocked_strict_document_mismatch"], errors
    return "eligible_for_future_controlled_apply", [*reasons, "strict_target_annual_ifrs_exact_document"], errors


def _operator_resolution_apply_matching_intake(
    row: dict[str, Any],
    intake_documents: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if intake_documents is None:
        return None
    company_ids = {
        str(row.get("company_id") or ""),
        str(row.get("canonical_company_id") or ""),
    }
    company_ids.discard("")
    target = str(row.get("target_reporting_period") or "")
    report_type = str(row.get("required_report_type") or "")
    standard = str(row.get("required_standard") or "")
    for item in intake_documents:
        item_ids = {
            str(item.get("company_id") or ""),
            str(item.get("canonical_company_id") or ""),
        }
        if company_ids.isdisjoint(item_ids):
            continue
        if str(item.get("report_period") or "") != target:
            continue
        if report_type and str(item.get("report_type") or "") != report_type:
            continue
        if standard and str(item.get("accounting_standard") or "") != standard:
            continue
        return item
    return None


def _operator_resolution_apply_intake_status(
    intake: dict[str, Any] | None,
    *,
    intake_documents: list[dict[str, Any]] | None,
) -> str:
    if intake_documents is None:
        return "intake_context_missing"
    if not intake:
        return "no_matching_intake_row"
    if not intake.get("document_url") or intake.get("document_status") == "not_found":
        return "matching_not_found_placeholder"
    if str(intake.get("filter_status") or "") == "placeholder_not_found":
        return "matching_filter_placeholder"
    return "matching_existing_intake_row"


def _operator_resolution_apply_action(patch_status: str, intake_status: str) -> str:
    if patch_status != "eligible_for_future_controlled_apply":
        return "preview_noop"
    if intake_status == "matching_not_found_placeholder":
        return "preview_replace_not_found_placeholder"
    if intake_status == "matching_filter_placeholder":
        return "preview_update_placeholder_row"
    if intake_status == "no_matching_intake_row":
        return "preview_create_intake_row"
    if intake_status == "intake_context_missing":
        return "preview_noop"
    return "preview_update_placeholder_row"


def _operator_resolution_apply_proposed_row(row: dict[str, Any], source_row: dict[str, Any]) -> dict[str, Any]:
    company_id = row.get("company_id") or source_row.get("company_id") or ""
    company_name = row.get("company_name") or source_row.get("company_name") or ""
    canonical_company_id = row.get("canonical_company_id") or source_row.get("canonical_company_id") or company_id
    canonical_company_name = row.get("canonical_company_name") or source_row.get("canonical_company_name") or company_name
    return {
        "company_id": company_id,
        "company_name": company_name,
        "canonical_company_id": canonical_company_id,
        "canonical_company_name": canonical_company_name,
        "report_period": row.get("target_reporting_period") or "",
        "report_type": row.get("required_report_type") or "",
        "accounting_standard": row.get("required_standard") or "",
        "document_url": row.get("operator_fill_exact_document_url") or "",
        "document_title": row.get("operator_fill_document_title") or "",
        "document_date": row.get("operator_fill_document_date") or "",
        "source_page_url": row.get("operator_fill_source_page_url") or "",
        "source_type": row.get("operator_fill_source_type") or "",
        "document_status": "valid_official_document",
        "operator_review_status": "operator_reviewed",
        "operator_resolution_id": row.get("resolution_id") or "",
        "operator_resolution_validation_status": row.get("validation_status") or "",
        "notes": "Preview only: eligible for future controlled apply; not written by Task 120.",
    }


def _operator_resolution_apply_patch_id(row: dict[str, Any]) -> str:
    resolution_id = str(row.get("resolution_id") or "")
    return f"operator_resolution_apply_preview:{resolution_id}" if resolution_id else "operator_resolution_apply_preview:missing_resolution_id"


def _build_operator_resolution_apply_preview_rows(
    validation_rows: list[dict[str, Any]],
    *,
    source_pack_by_id: dict[str, dict[str, Any]],
    intake_documents: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    patch_rows: list[dict[str, Any]] = []
    for validation_row in validation_rows:
        source_row = source_pack_by_id.get(str(validation_row.get("resolution_id") or ""), {})
        row = {**source_row, **validation_row}
        patch_status, reasons, errors = _operator_resolution_apply_base_status(row)
        intake = _operator_resolution_apply_matching_intake(row, intake_documents)
        intake_status = _operator_resolution_apply_intake_status(intake, intake_documents=intake_documents)
        patch_action = _operator_resolution_apply_action(patch_status, intake_status)
        proposed = (
            _operator_resolution_apply_proposed_row(row, source_row)
            if patch_status == "eligible_for_future_controlled_apply"
            else {}
        )
        future_allowed = patch_status == "eligible_for_future_controlled_apply" and patch_action != "preview_noop"
        future_blocked_reason = "" if future_allowed else patch_status
        if patch_status == "eligible_for_future_controlled_apply" and intake_status == "intake_context_missing":
            reasons.append("document_intake_context_missing")
            errors.append("document_intake_context_missing")
            future_allowed = False
            future_blocked_reason = "document_intake_context_missing"
        warnings = []
        if not source_row:
            warnings.append("source_pack_row_missing")
        would_create = patch_action == "preview_create_intake_row"
        would_update = patch_action in {"preview_update_placeholder_row", "preview_replace_not_found_placeholder"}
        would_replace = patch_action == "preview_replace_not_found_placeholder"
        patch_rows.append(
            {
                "patch_id": _operator_resolution_apply_patch_id(row),
                "resolution_id": row.get("resolution_id") or "",
                "company_id": row.get("company_id") or "",
                "company_name": row.get("company_name") or "",
                "canonical_company_id": row.get("canonical_company_id") or row.get("company_id") or "",
                "canonical_company_name": row.get("canonical_company_name") or row.get("company_name") or "",
                "target_reporting_period": row.get("target_reporting_period") or "",
                "required_report_type": row.get("required_report_type") or "",
                "required_standard": row.get("required_standard") or "",
                "patch_status": patch_status,
                "patch_action": patch_action,
                "patch_reason_codes": list(dict.fromkeys(str(reason) for reason in reasons if reason)),
                "patch_errors": list(dict.fromkeys(str(error) for error in errors if error)),
                "patch_warnings": warnings,
                "source_validation_status": row.get("validation_status") or "",
                "can_use_for_future_intake_review": _operator_resolution_apply_bool(row.get("can_use_for_future_intake_review")),
                "operator_fill_decision": row.get("operator_fill_decision") or "",
                "proposed_document_url": proposed.get("document_url") or "",
                "proposed_document_title": proposed.get("document_title") or "",
                "proposed_document_date": proposed.get("document_date") or "",
                "proposed_source_page_url": proposed.get("source_page_url") or "",
                "proposed_source_type": proposed.get("source_type") or "",
                "proposed_report_period": proposed.get("report_period") or "",
                "proposed_report_type": proposed.get("report_type") or "",
                "proposed_accounting_standard": proposed.get("accounting_standard") or "",
                "proposed_intake_row": proposed,
                "document_kind": row.get("document_kind") or "",
                "document_period_year": row.get("document_period_year") or "",
                "document_period_status": row.get("document_period_status") or "",
                "report_type_match_status": row.get("report_type_match_status") or "",
                "accounting_standard_match_status": row.get("accounting_standard_match_status") or "",
                "intake_target_status": intake_status,
                "intake_existing_document_url": (intake or {}).get("document_url") or "",
                "intake_existing_document_status": (intake or {}).get("document_status") or "",
                "intake_existing_operator_review_status": (intake or {}).get("operator_review_status") or "",
                "intake_existing_filter_status": (intake or {}).get("filter_status") or "",
                "would_create_intake_row": would_create,
                "would_update_existing_intake_row": would_update,
                "would_replace_placeholder": would_replace,
                "would_apply_to_document_intake": False,
                "would_promote_seed": False,
                "would_extract_values": False,
                "would_import_report": False,
                "would_mutate_scores": False,
                "would_trigger_paper_trading": False,
                "future_apply_allowed": future_allowed,
                "future_apply_blocked_reason": future_blocked_reason,
                "operator_next_step": "future_controlled_apply_review" if future_allowed else "resolve_validation_or_intake_context_first",
                "preview_note": "Preview only: no intake, seed, extraction, import, scoring, or trading mutation is performed.",
            }
        )
    return sorted(patch_rows, key=lambda item: str(item.get("patch_id") or ""))


def _operator_resolution_apply_preview_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "operator_resolution_apply_preview_row_count": len(rows),
        "operator_resolution_apply_preview_candidate_count": sum(
            1 for row in rows if row.get("patch_status") == "eligible_for_future_controlled_apply"
        ),
        "operator_resolution_apply_preview_eligible_count": sum(
            1 for row in rows if row.get("future_apply_allowed")
        ),
        "operator_resolution_apply_preview_blocked_count": sum(
            1 for row in rows if not row.get("future_apply_allowed")
        ),
        "operator_resolution_apply_preview_create_count": sum(
            1 for row in rows if row.get("patch_action") == "preview_create_intake_row"
        ),
        "operator_resolution_apply_preview_update_placeholder_count": sum(
            1 for row in rows if row.get("patch_action") == "preview_update_placeholder_row"
        ),
        "operator_resolution_apply_preview_replace_not_found_count": sum(
            1 for row in rows if row.get("patch_action") == "preview_replace_not_found_placeholder"
        ),
        "operator_resolution_apply_preview_future_apply_allowed_count": sum(
            1 for row in rows if row.get("future_apply_allowed")
        ),
        "operator_resolution_apply_preview_status_counts": _count_by_key(rows, "patch_status"),
        "operator_resolution_apply_preview_action_counts": _count_by_key(rows, "patch_action"),
    }


def _build_operator_resolution_apply_preview_report(
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    load_warnings: list[dict[str, Any]] | None = None,
    load_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = _operator_resolution_apply_preview_summary(rows)
    load_warnings = load_warnings or []
    load_errors = load_errors or []
    status = "failed" if load_errors else "warning" if load_warnings or summary["operator_resolution_apply_preview_blocked_count"] else "passed"
    return {
        "status": status,
        "mode": "operator-resolution-apply-preview",
        "summary": summary,
        **summary,
        "patch_rows": rows,
        "operator_resolution_validation_input": _path_value(args.operator_resolution_validation_input),
        "operator_resolution_source_pack_input": _path_value(args.operator_resolution_source_pack_input),
        "document_intake_input": _path_value(args.document_intake_input),
        "operator_resolution_apply_preview_output": _path_value(args.operator_resolution_apply_preview_output),
        "operator_resolution_apply_preview_csv_output": _path_value(args.operator_resolution_apply_preview_csv_output),
        "operator_resolution_apply_preview_markdown_output": _path_value(args.operator_resolution_apply_preview_markdown_output),
        "warnings": load_warnings,
        "errors": load_errors,
        "next_steps": _next_steps("operator-resolution-apply-preview", status),
        "would_apply_to_document_intake": False,
        "would_promote_seed": False,
        "would_extract_values": False,
        "would_import_report": False,
        **SAFETY_FLAGS,
    }


def _operator_resolution_apply_draft_bool(value: Any) -> bool:
    return _operator_resolution_apply_bool(value)


def _operator_resolution_apply_draft_id(row: dict[str, Any]) -> str:
    patch_id = str(row.get("patch_id") or "")
    return f"operator_resolution_apply_draft:{patch_id}" if patch_id else "operator_resolution_apply_draft:missing_patch_id"


def _operator_resolution_apply_draft_strict_mismatch(row: dict[str, Any]) -> bool:
    return not (
        str(row.get("document_kind") or "") == "exact_report_document"
        and str(row.get("document_period_status") or "") == "target_period"
        and str(row.get("report_type_match_status") or "") == "annual_match"
        and str(row.get("accounting_standard_match_status") or "") == "standard_match"
    )


def _operator_resolution_apply_draft_has_unsafe_flags(row: dict[str, Any]) -> bool:
    return any(
        _operator_resolution_apply_draft_bool(row.get(flag))
        for flag in (
            "would_apply_to_document_intake",
            "would_promote_seed",
            "would_extract_values",
            "would_import_report",
            "would_mutate_scores",
            "would_trigger_paper_trading",
        )
    )


def _operator_resolution_apply_draft_skip_status(patch_status: str) -> str | None:
    return {
        "not_eligible_incomplete_validation": "skipped_not_eligible_incomplete_validation",
        "not_eligible_invalid_validation": "skipped_not_eligible_invalid_validation",
        "not_eligible_waiting": "skipped_not_eligible_waiting",
        "not_eligible_diagnostic_only": "skipped_not_eligible_diagnostic_only",
        "not_eligible_no_action_required": "skipped_not_eligible_no_action_required",
        "blocked_strict_document_mismatch": "skipped_strict_document_mismatch",
    }.get(patch_status)


def _operator_resolution_apply_draft_placeholder_match(intake: dict[str, Any] | None) -> bool:
    if not intake:
        return False
    return (
        not intake.get("document_url")
        or str(intake.get("document_status") or "") == "not_found"
        or str(intake.get("filter_status") or "") == "placeholder_not_found"
    )


def _operator_resolution_apply_draft_base_status(row: dict[str, Any]) -> tuple[str, str, list[str], list[str]]:
    patch_status = str(row.get("patch_status") or "")
    skip_status = _operator_resolution_apply_draft_skip_status(patch_status)
    if skip_status is not None:
        return skip_status, "skip", [patch_status], []
    if _operator_resolution_apply_draft_has_unsafe_flags(row):
        return "skipped_unsafe_mutation_flags", "skip", ["unsafe_mutation_flags"], []
    if not _operator_resolution_apply_draft_bool(row.get("future_apply_allowed")):
        return "skipped_future_apply_not_allowed", "skip", ["future_apply_not_allowed"], []
    if patch_status != "eligible_for_future_controlled_apply":
        return "skipped_future_apply_not_allowed", "skip", [patch_status or "patch_not_eligible"], []
    if not str(row.get("proposed_document_url") or "").strip():
        return "skipped_missing_proposed_document_url", "skip", ["missing_proposed_document_url"], []
    if _operator_resolution_apply_draft_strict_mismatch(row):
        return "skipped_strict_document_mismatch", "skip", ["strict_document_mismatch"], []
    patch_action = str(row.get("patch_action") or "")
    if patch_action == "preview_replace_not_found_placeholder":
        return "draft_applied_replace_not_found_placeholder", "replace_not_found_placeholder", ["future_apply_allowed"], []
    if patch_action == "preview_update_placeholder_row":
        return "draft_applied_update_placeholder", "update_placeholder", ["future_apply_allowed"], []
    if patch_action == "preview_create_intake_row":
        return "draft_applied_create_row", "create_row", ["future_apply_allowed"], []
    return "skipped_future_apply_not_allowed", "skip", ["unsupported_patch_action"], []


def _operator_resolution_apply_draft_intake_row(row: dict[str, Any]) -> dict[str, Any]:
    document_url = row.get("proposed_document_url") or ""
    source_page_url = row.get("proposed_source_page_url") or ""
    return {
        "company_id": row.get("company_id") or "",
        "company_name": row.get("company_name") or "",
        "canonical_company_id": row.get("canonical_company_id") or row.get("company_id") or "",
        "canonical_company_name": row.get("canonical_company_name") or row.get("company_name") or "",
        "report_period": row.get("target_reporting_period") or row.get("proposed_report_period") or "",
        "report_type": row.get("required_report_type") or row.get("proposed_report_type") or "",
        "accounting_standard": row.get("required_standard") or row.get("proposed_accounting_standard") or "",
        "source_type": row.get("proposed_source_type") or "",
        "source_url_context": source_page_url,
        "source_page_url": source_page_url,
        "document_url": document_url,
        "document_title": row.get("proposed_document_title") or "",
        "document_date": row.get("proposed_document_date") or "",
        "source_file_name": Path(str(document_url)).name if document_url else "",
        "document_status": "valid_official_document",
        "operator_review_status": "operator_reviewed",
        "filter_status": "kept",
        "fallback_status": "not_fallback",
        "operator_resolution_id": row.get("resolution_id") or "",
        "operator_resolution_patch_id": row.get("patch_id") or "",
        "draft_source": "operator_resolution_apply_draft",
        "draft_note": "Created in draft only; original intake was not modified.",
        "notes": "Created in draft only; original intake was not modified.",
    }


def _operator_resolution_apply_draft_update_row(target: dict[str, Any], row: dict[str, Any]) -> None:
    target.update(_operator_resolution_apply_draft_intake_row(row))


def _build_operator_resolution_apply_draft_rows(
    patch_rows: list[dict[str, Any]],
    *,
    draft_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    report_rows: list[dict[str, Any]] = []
    for patch_row in patch_rows:
        status, action, reasons, errors = _operator_resolution_apply_draft_base_status(patch_row)
        intake = _operator_resolution_apply_matching_intake(patch_row, draft_documents)
        matched_status = _operator_resolution_apply_intake_status(intake, intake_documents=draft_documents)
        existing_document_url = (intake or {}).get("document_url") or ""
        existing_document_status = (intake or {}).get("document_status") or ""
        existing_filter_status = (intake or {}).get("filter_status") or ""
        draft_row_index = ""
        changed = False
        warnings: list[str] = []
        if status in {"draft_applied_replace_not_found_placeholder", "draft_applied_update_placeholder"}:
            if not _operator_resolution_apply_draft_placeholder_match(intake):
                status = "failed_missing_matching_placeholder"
                action = "fail"
                errors.append("missing_matching_placeholder")
            else:
                match_index = draft_documents.index(intake) if intake in draft_documents else -1
                _operator_resolution_apply_draft_update_row(intake, patch_row)
                draft_row_index = match_index + 1 if match_index >= 0 else ""
                changed = True
        elif status == "draft_applied_create_row":
            if intake is not None:
                status = "failed_existing_matching_intake_row"
                action = "fail"
                errors.append("existing_matching_intake_row")
            else:
                draft_documents.append(_operator_resolution_apply_draft_intake_row(patch_row))
                draft_row_index = len(draft_documents)
                changed = True
        report_rows.append(
            {
                "apply_draft_id": _operator_resolution_apply_draft_id(patch_row),
                "patch_id": patch_row.get("patch_id") or "",
                "resolution_id": patch_row.get("resolution_id") or "",
                "company_id": patch_row.get("company_id") or "",
                "company_name": patch_row.get("company_name") or "",
                "target_reporting_period": patch_row.get("target_reporting_period") or "",
                "required_report_type": patch_row.get("required_report_type") or "",
                "required_standard": patch_row.get("required_standard") or "",
                "apply_draft_status": status,
                "apply_draft_action": action,
                "apply_draft_reason_codes": list(dict.fromkeys(str(reason) for reason in reasons if reason)),
                "apply_draft_errors": list(dict.fromkeys(str(error) for error in errors if error)),
                "apply_draft_warnings": warnings,
                "source_patch_status": patch_row.get("patch_status") or "",
                "source_patch_action": patch_row.get("patch_action") or "",
                "future_apply_allowed": _operator_resolution_apply_draft_bool(patch_row.get("future_apply_allowed")),
                "draft_document_url": patch_row.get("proposed_document_url") or "",
                "draft_document_title": patch_row.get("proposed_document_title") or "",
                "draft_document_date": patch_row.get("proposed_document_date") or "",
                "draft_source_page_url": patch_row.get("proposed_source_page_url") or "",
                "draft_source_type": patch_row.get("proposed_source_type") or "",
                "matched_intake_status": matched_status,
                "matched_intake_existing_document_url": existing_document_url,
                "matched_intake_existing_document_status": existing_document_status,
                "matched_intake_existing_filter_status": existing_filter_status,
                "draft_row_index": draft_row_index,
                "would_change_draft_file": changed,
                "would_overwrite_input_file": False,
                "would_update_original_intake": False,
                "would_update_database": False,
                "would_promote_seed": False,
                "would_extract_values": False,
                "would_import_report": False,
                "would_mutate_scores": False,
                "would_trigger_paper_trading": False,
                "operator_next_step": "validate_draft_intake_before_quality_gate" if changed else "resolve_patch_plan_or_operator_input_first",
                "apply_draft_note": "Draft output only; original intake, DB, seeds, extraction, import, scoring, and trading are unchanged.",
            }
        )
    return sorted(report_rows, key=lambda item: str(item.get("apply_draft_id") or ""))


def _operator_resolution_apply_draft_summary(
    rows: list[dict[str, Any]],
    draft_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "operator_resolution_apply_draft_row_count": len(rows),
        "operator_resolution_apply_draft_applied_count": sum(
            1 for row in rows if str(row.get("apply_draft_status") or "").startswith("draft_applied_")
        ),
        "operator_resolution_apply_draft_skipped_count": sum(
            1 for row in rows if str(row.get("apply_draft_status") or "").startswith("skipped_")
        ),
        "operator_resolution_apply_draft_failed_count": sum(
            1 for row in rows if str(row.get("apply_draft_status") or "").startswith("failed_")
        ),
        "operator_resolution_apply_draft_replace_placeholder_count": sum(
            1 for row in rows if row.get("apply_draft_status") == "draft_applied_replace_not_found_placeholder"
        ),
        "operator_resolution_apply_draft_update_placeholder_count": sum(
            1 for row in rows if row.get("apply_draft_status") == "draft_applied_update_placeholder"
        ),
        "operator_resolution_apply_draft_create_count": sum(
            1 for row in rows if row.get("apply_draft_status") == "draft_applied_create_row"
        ),
        "operator_resolution_apply_draft_output_row_count": len(draft_documents),
        "operator_resolution_apply_draft_status_counts": _count_by_key(rows, "apply_draft_status"),
        "operator_resolution_apply_draft_action_counts": _count_by_key(rows, "apply_draft_action"),
    }


def _build_operator_resolution_apply_draft_report(
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    draft_documents: list[dict[str, Any]],
    load_warnings: list[dict[str, Any]] | None = None,
    load_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = _operator_resolution_apply_draft_summary(rows, draft_documents)
    load_warnings = load_warnings or []
    load_errors = load_errors or []
    failed_count = summary["operator_resolution_apply_draft_failed_count"]
    skipped_count = summary["operator_resolution_apply_draft_skipped_count"]
    status = "failed" if load_errors or failed_count else "warning" if load_warnings or skipped_count else "passed"
    return {
        "status": status,
        "mode": "operator-resolution-apply-draft",
        "summary": summary,
        **summary,
        "apply_draft_rows": rows,
        "operator_resolution_apply_preview_input": _path_value(args.operator_resolution_apply_preview_input),
        "document_intake_input": _path_value(args.document_intake_input),
        "document_intake_draft_output": _path_value(args.document_intake_draft_output),
        "document_intake_draft_csv_output": _path_value(args.document_intake_draft_csv_output),
        "operator_resolution_apply_draft_output": _path_value(args.operator_resolution_apply_draft_output),
        "operator_resolution_apply_draft_csv_output": _path_value(args.operator_resolution_apply_draft_csv_output),
        "operator_resolution_apply_draft_markdown_output": _path_value(args.operator_resolution_apply_draft_markdown_output),
        "warnings": load_warnings,
        "errors": load_errors,
        "next_steps": _next_steps("operator-resolution-apply-draft", status),
        "would_update_original_intake": False,
        "would_update_database": False,
        "would_promote_seed": False,
        "would_extract_values": False,
        "would_import_report": False,
        **SAFETY_FLAGS,
    }


def _document_intake_draft_gate_classification(document: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    document_url = str(document.get("document_url") or "")
    title = str(document.get("document_title") or "")
    source_page_url = str(document.get("source_page_url") or document.get("source_url_context") or "")
    kind = classify_exact_document_kind(document_url, title, args=args) if document_url else "missing_document_url"
    period = classify_exact_document_period(document_url, title, source_page_url, args=args)
    report_type = classify_exact_document_report_type(
        document_url,
        title,
        args=args,
        period_quarter=str(period.get("document_period_quarter") or ""),
    )
    standard = classify_exact_document_accounting_standard(document_url, title, args=args)
    candidate = {
        **document,
        "document_kind": kind,
        **period,
        **report_type,
        **standard,
    }
    if not document_url:
        candidate["document_status"] = "not_found"
        candidate["filter_status"] = "placeholder_not_found"
    elif (
        kind == "exact_report_document"
        and period.get("document_period_status") == "target_period"
        and report_type.get("report_type_match_status") == "annual_match"
        and standard.get("accounting_standard_match_status") == "standard_match"
        and str(document.get("operator_review_status") or "") in DOCUMENT_INTAKE_REVIEWED_STATUSES
    ):
        candidate["document_status"] = "valid_official_document"
        candidate["filter_status"] = "kept"
        candidate["fallback_status"] = "not_fallback"
    else:
        candidate["document_status"] = "invalid_document"
        candidate["filter_status"] = "filtered_strict_document_mismatch"
    return candidate


def _document_intake_draft_gate_blockers(
    document: dict[str, Any],
    *,
    validation: dict[str, Any],
    gate: dict[str, Any],
    source_context_missing: bool,
) -> list[str]:
    reasons: list[str] = []
    document_url = str(document.get("document_url") or "")
    if not document_url:
        reasons.append("missing_exact_document_url")
    if validation.get("errors"):
        reasons.append("invalid_document_intake")
    if document_url and document.get("document_kind") != "exact_report_document":
        reasons.append("not_exact_report_document")
    period_status = str(document.get("document_period_status") or "")
    if document_url and period_status != "target_period":
        reasons.append("wrong_period")
    type_status = str(document.get("report_type_match_status") or "")
    if document_url and type_status != "annual_match":
        reasons.append(
            "interim_or_quarterly_not_allowed_for_annual"
            if type_status == "interim_or_quarterly_mismatch"
            else "wrong_report_type"
        )
    standard_status = str(document.get("accounting_standard_match_status") or "")
    if document_url and standard_status != "standard_match":
        reasons.append("wrong_standard")
    if document_url and (
        str(document.get("fallback_status") or "") != "not_fallback"
        or period_status in {"wrong_period", "prior_period_fallback_candidate"}
    ):
        reasons.append("historical_fallback_not_target_evidence")
    if source_context_missing:
        reasons.append("quality_gate_source_context_missing")
    if gate.get("gate_status") != "passed":
        reasons.append("quality_gate_failed")
    return list(dict.fromkeys(reasons))


def _document_intake_draft_gate_row_status(
    document: dict[str, Any],
    *,
    validation: dict[str, Any],
    gate: dict[str, Any],
) -> str:
    if (
        not document.get("document_url")
        or str(document.get("document_status") or "") == "not_found"
        or str(document.get("filter_status") or "") == "placeholder_not_found"
    ):
        return "draft_placeholder_not_ready"
    if validation.get("errors") or not _exact_document_is_downstream_eligible(document):
        return "draft_invalid_not_ready"
    if gate.get("gate_status") != "passed":
        return "draft_valid_but_gate_blocked"
    return "draft_ready_for_future_extraction_preview"


def _document_intake_draft_gate_next_action(status: str) -> str:
    return {
        "draft_placeholder_not_ready": "fill_exact_target_period_annual_ifrs_document_url",
        "draft_invalid_not_ready": "fix_draft_document_metadata_or_exact_document_mismatch",
        "draft_valid_but_gate_blocked": "provide_source_context_or_resolve_quality_gate_blockers",
        "draft_ready_for_future_extraction_preview": "proceed_to_controlled_extraction_preview_review",
    }[status]


def _build_document_intake_draft_gate_summary_rows(
    documents: list[dict[str, Any]],
    *,
    validation_report: dict[str, Any],
    quality_gate_report: dict[str, Any],
    source_context_missing: bool,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    validation_results = validation_report.get("document_results") or []
    gate_results = quality_gate_report.get("required_issuers") or []
    rows: list[dict[str, Any]] = []
    for draft_document in documents:
        document = _document_intake_draft_gate_classification(draft_document, args=args)
        validation = (_items_matching_required(validation_results, draft_document) or [{}])[0]
        gate = (_items_matching_required(gate_results, draft_document) or [{}])[0]
        status = _document_intake_draft_gate_row_status(document, validation=validation, gate=gate)
        blockers = _document_intake_draft_gate_blockers(
            document,
            validation=validation,
            gate=gate,
            source_context_missing=source_context_missing,
        )
        ready = status == "draft_ready_for_future_extraction_preview"
        rows.append(
            {
                "company_id": document.get("company_id") or "",
                "company_name": document.get("company_name") or "",
                "canonical_company_id": document.get("canonical_company_id") or document.get("company_id") or "",
                "canonical_company_name": document.get("canonical_company_name") or document.get("company_name") or "",
                "target_reporting_period": str(args.report_period),
                "required_report_type": args.report_type,
                "required_standard": args.accounting_standard,
                "draft_row_status": status,
                "draft_document_url": document.get("document_url") or "",
                "draft_document_status": document.get("document_status") or "",
                "draft_operator_review_status": document.get("operator_review_status") or "",
                "draft_filter_status": document.get("filter_status") or "",
                "draft_fallback_status": document.get("fallback_status") or "",
                "validation_status": validation.get("status") or validation_report.get("status") or "",
                "validation_errors": [_message_text(error) for error in validation.get("errors") or []],
                "validation_warnings": [_message_text(warning) for warning in validation.get("warnings") or []],
                "document_kind": document.get("document_kind") or "",
                "document_period_year": document.get("document_period_year") or "",
                "document_period_status": document.get("document_period_status") or "",
                "report_type_match_status": document.get("report_type_match_status") or "",
                "accounting_standard_match_status": document.get("accounting_standard_match_status") or "",
                "gate_status": gate.get("gate_status") or quality_gate_report.get("status") or "failed",
                "gate_passed": bool(ready and gate.get("gate_status") == "passed"),
                "gate_reason": gate.get("reason") or "quality_gate_failed",
                "ready_for_value_extraction": bool(ready and quality_gate_report.get("ready_for_value_extraction")),
                "ready_for_import": bool(ready and quality_gate_report.get("ready_for_import")),
                "is_placeholder": status == "draft_placeholder_not_ready",
                "has_document_url": bool(document.get("document_url")),
                "has_exact_target_document": _exact_document_is_downstream_eligible(document),
                "blocked_reason_codes": blockers,
                "next_required_action": _document_intake_draft_gate_next_action(status),
                "would_extract_values": False,
                "would_import_report": False,
                "would_mutate_scores": False,
                "would_trigger_paper_trading": False,
            }
        )
    return sorted(rows, key=lambda item: str(item.get("company_id") or ""))


def _document_intake_draft_gate_preview_summary(
    rows: list[dict[str, Any]],
    quality_gate_report: dict[str, Any],
) -> dict[str, Any]:
    blocker_counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("blocked_reason_codes") or []:
            blocker_counts[str(blocker)] = blocker_counts.get(str(blocker), 0) + 1
    ready_count = sum(1 for row in rows if row.get("draft_row_status") == "draft_ready_for_future_extraction_preview")
    return {
        "document_intake_draft_gate_preview_row_count": len(rows),
        "document_intake_draft_gate_preview_ready_count": ready_count,
        "document_intake_draft_gate_preview_blocked_count": len(rows) - ready_count,
        "document_intake_draft_gate_preview_placeholder_count": sum(1 for row in rows if row.get("is_placeholder")),
        "document_intake_draft_gate_preview_invalid_count": sum(
            1 for row in rows if row.get("draft_row_status") == "draft_invalid_not_ready"
        ),
        "document_intake_draft_gate_preview_gate_passed": bool(quality_gate_report.get("gate_passed")),
        "document_intake_draft_gate_preview_ready_for_value_extraction": bool(quality_gate_report.get("ready_for_value_extraction")),
        "document_intake_draft_gate_preview_ready_for_import": bool(quality_gate_report.get("ready_for_import")),
        "document_intake_draft_gate_preview_status_counts": _count_by_key(rows, "draft_row_status"),
        "document_intake_draft_gate_preview_blocker_counts": dict(sorted(blocker_counts.items())),
    }


def _build_document_intake_draft_gate_preview_report(
    args: argparse.Namespace,
    *,
    rows: list[dict[str, Any]],
    validation_report: dict[str, Any],
    quality_gate_report: dict[str, Any],
    load_warnings: list[dict[str, Any]] | None = None,
    load_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = _document_intake_draft_gate_preview_summary(rows, quality_gate_report)
    load_warnings = load_warnings or []
    load_errors = load_errors or []
    status = (
        "failed"
        if load_errors
        else "passed"
        if rows and summary["document_intake_draft_gate_preview_ready_count"] == len(rows)
        else "warning"
    )
    return {
        "status": status,
        "mode": "document-intake-draft-gate-preview",
        "summary": summary,
        **summary,
        "draft_gate_summary_rows": rows,
        "document_intake_draft_input": _path_value(args.document_intake_draft_input),
        "document_intake_draft_validation_output": _path_value(args.document_intake_draft_validation_output),
        "document_intake_draft_validation_markdown_output": _path_value(
            args.document_intake_draft_validation_markdown_output
        ),
        "document_intake_draft_gate_output": _path_value(args.document_intake_draft_gate_output),
        "document_intake_draft_gate_markdown_output": _path_value(args.document_intake_draft_gate_markdown_output),
        "document_intake_draft_gate_summary_output": _path_value(args.document_intake_draft_gate_summary_output),
        "document_intake_draft_gate_summary_csv_output": _path_value(
            args.document_intake_draft_gate_summary_csv_output
        ),
        "document_intake_draft_gate_summary_markdown_output": _path_value(
            args.document_intake_draft_gate_summary_markdown_output
        ),
        "document_intake_draft_validation_report": validation_report,
        "document_intake_draft_quality_gate_report": quality_gate_report,
        "warnings": load_warnings,
        "errors": load_errors,
        "next_steps": _next_steps("document-intake-draft-gate-preview", status),
        "would_extract_values": False,
        "would_import_report": False,
        **SAFETY_FLAGS,
    }


def _exact_document_is_downstream_eligible(document: dict[str, Any]) -> bool:
    if not document.get("document_url"):
        return False
    if document.get("filter_status") != "kept":
        return False
    if document.get("document_kind") != "exact_report_document":
        return False
    if document.get("document_period_status") != "target_period":
        return False
    if document.get("report_type_match_status") != "annual_match":
        return False
    if document.get("accounting_standard_match_status") != "standard_match":
        return False
    if document.get("fallback_status") != "not_fallback":
        return False
    return document.get("document_status") in {"valid_official_document", "needs_operator_review"}


def _exact_document_can_follow_category(
    candidate: dict[str, Any],
    *,
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
) -> tuple[bool, str]:
    document_url = str(candidate.get("document_url") or "")
    if not document_url:
        return False, "empty category URL"
    if candidate.get("document_kind") not in _exact_document_category_page_types(args):
        return False, "category kind is not enabled"
    if int(candidate.get("candidate_score") or 0) < int(args.exact_document_category_page_min_score or 0):
        return False, "category page score below follow threshold"
    if int(candidate.get("crawl_depth") or 1) >= int(args.exact_document_max_crawl_depth or 0):
        return False, "max crawl depth reached"
    classification = _classify_candidate_url(
        document_url,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
        allow_unknown_source=False,
    )
    if classification["status"] != "official":
        return False, "category URL is not allowlisted official source"
    return True, "eligible category page"


def _crawl_exact_document_category_pages(
    issuer: dict[str, Any],
    raw_documents: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    crawled_documents: list[dict[str, Any]] = []
    blocked_count = 0
    max_pages = max(int(args.exact_document_max_category_pages_per_issuer or 0), 0)
    max_depth = max(int(args.exact_document_max_crawl_depth or 0), 0)
    if max_depth < 2 or max_pages == 0:
        return crawled_documents, blocked_count

    category_candidates: list[dict[str, Any]] = []
    for candidate in raw_documents:
        if not _matches_required_issuer(candidate, issuer):
            continue
        should_follow, reason = _exact_document_can_follow_category(
            candidate,
            args=args,
            allowed_domains=allowed_domains,
            blocked_hints=blocked_hints,
        )
        if not should_follow:
            if candidate.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS:
                candidate.setdefault("filter_reasons", []).append(reason)
            continue
        category_candidates.append(candidate)

    visited_pages: set[str] = set()
    category_queue = sorted(category_candidates, key=_exact_document_candidate_sort_key, reverse=True)[:max_pages]
    followed_count = 0
    while category_queue and followed_count < max_pages:
        category = category_queue.pop(0)
        category_url = str(category.get("document_url") or "")
        normalized_category_url = _normalized_operator_seed_candidate_url(category_url)
        if not normalized_category_url or normalized_category_url in visited_pages:
            continue
        visited_pages.add(normalized_category_url)
        parent_seed_url = str(category.get("parent_seed_url") or category.get("source_page_url") or category_url)
        if args.exact_document_follow_same_domain_only and _host(category_url) != _host(parent_seed_url):
            category.setdefault("filter_reasons", []).append("category page skipped because it is outside reviewed seed domain")
            continue
        category["category_followed"] = True
        category["filter_status"] = "category_followed"
        category["document_status"] = "category_page"
        category["operator_review_status"] = "needs_operator_review"
        followed_count += 1
        fetch = _fetch_candidate_page(
            category_url,
            timeout_seconds=args.exact_document_fetch_timeout_seconds,
            max_bytes=args.exact_document_max_response_bytes,
            user_agent=args.exact_document_user_agent,
        )
        if fetch.get("status") != "ok":
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "source_page_url": category_url,
                    "message": "failed to fetch exact document category page",
                    "error": fetch.get("error"),
                }
            )
            continue
        content_type = str(fetch.get("content_type") or "").casefold()
        if "html" not in content_type:
            warnings.append(
                {
                    "company_id": issuer.get("company_id"),
                    "source_page_url": category_url,
                    "content_type": fetch.get("content_type"),
                    "message": "category page response is not HTML; skipped anchor extraction",
                }
            )
            continue
        source_chain = [str(item) for item in category.get("source_chain") or []]
        if not source_chain:
            source_chain = [parent_seed_url, category_url]
        anchors = _extract_html_anchors(str(fetch.get("body") or ""), category_url)
        for anchor in anchors[: max(int(args.exact_document_max_second_level_links_per_page or 0), 0)]:
            child_url = _normalize_candidate_url(anchor.get("href") or "")
            if not child_url or _is_ignored_href(child_url):
                continue
            if args.exact_document_follow_same_domain_only and _host(child_url) != _host(category_url):
                continue
            child_kind = classify_exact_document_kind(child_url, _candidate_title(anchor, child_url), args=args)
            if child_kind in {
                "legal_policy_document",
                "privacy_policy_document",
                "cookie_policy_document",
                "user_agreement_document",
                "news_or_press_document",
                "generic_navigation_page",
            }:
                # Still build the diagnostic candidate below; it will be filtered and excluded downstream.
                pass
            child_candidate, blocked = build_exact_document_candidate_from_seed_anchor(
                issuer,
                anchor,
                {"seed_type": category.get("source_type") or "issuer_reports"},
                category_url,
                args=args,
                allowed_domains=allowed_domains,
                blocked_hints=blocked_hints,
                crawl_depth=int(category.get("crawl_depth") or 1) + 1,
                parent_seed_url=category_url,
                source_chain=source_chain,
            )
            if blocked:
                blocked_count += 1
            if child_candidate is None:
                continue
            crawled_documents.append(child_candidate)
            if (
                args.exact_document_second_level_crawl
                and child_candidate.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS
                and int(child_candidate.get("crawl_depth") or 1) < max_depth
                and followed_count + len(category_queue) < max_pages
            ):
                should_follow, _ = _exact_document_can_follow_category(
                    child_candidate,
                    args=args,
                    allowed_domains=allowed_domains,
                    blocked_hints=blocked_hints,
                )
                if should_follow:
                    category_queue.append(child_candidate)
    return crawled_documents, blocked_count


def build_exact_document_candidate_from_seed_anchor(
    issuer: dict[str, Any],
    anchor: dict[str, str],
    seed: dict[str, Any],
    source_page_url: str,
    *,
    args: argparse.Namespace,
    allowed_domains: set[str],
    blocked_hints: tuple[str, ...],
    crawl_depth: int = 1,
    parent_seed_url: str = "",
    source_chain: list[str] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    document_url = _normalize_candidate_url(anchor.get("href") or "")
    if not document_url or _is_ignored_href(document_url):
        return None, False
    classification = _classify_candidate_url(
        document_url,
        allowed_domains=allowed_domains,
        blocked_hints=blocked_hints,
        allow_unknown_source=False,
    )
    title = _candidate_title(anchor, document_url)
    source_type = _exact_document_source_type(seed, document_url, source_page_url)
    document_kind = classify_exact_document_kind(document_url, title, args=args)
    period_classification = classify_exact_document_period(document_url, title, source_page_url, args=args)
    type_classification = classify_exact_document_report_type(
        document_url,
        title,
        args=args,
        period_quarter=str(period_classification.get("document_period_quarter") or ""),
    )
    standard_classification = classify_exact_document_accounting_standard(document_url, title, args=args)
    score, reasons, negatives = score_exact_document_candidate_from_seed(
        document_url,
        title,
        source_page_url,
        args=args,
        domain_status=classification["status"],
        document_kind=document_kind,
    )
    exact = _exact_document_is_document_like(document_url, title, args)
    strong = _exact_document_has_strong_signals(document_url, title, args)
    blocked = classification["status"] == "blocked"
    official = classification["status"] == "official"
    is_category_page = document_kind in EXACT_DOCUMENT_CATEGORY_KINDS
    if blocked:
        document_status = "blocked_document"
        operator_status = "operator_to_fill"
        confidence = "low"
        filter_status = "filtered_blocked"
        filter_reasons = ["blocked unofficial document URL"]
    elif document_kind in EXACT_DOCUMENT_WRONG_TYPE_KINDS:
        document_status = "invalid_document"
        operator_status = "operator_to_fill"
        confidence = "low"
        filter_status = "filtered_wrong_document_type"
        filter_reasons = [f"{document_kind} is not an exact annual report document"]
    elif classification["status"] != "official":
        document_status = "invalid_document"
        operator_status = "operator_to_fill"
        confidence = "low"
        filter_status = "filtered_blocked"
        filter_reasons = [classification["message"]]
    elif is_category_page:
        document_status = "category_page"
        operator_status = "needs_operator_review"
        confidence = "medium" if score >= args.exact_document_category_page_min_score else "low"
        filter_status = "category_page"
        filter_reasons = ["category/reporting page is a crawl source, not exact report evidence"]
    elif (
        document_kind == "exact_report_document"
        and official
        and exact
        and strong
        and period_classification.get("document_period_status") == "target_period"
        and type_classification.get("report_type_match_status") == "annual_match"
        and standard_classification.get("accounting_standard_match_status") == "standard_match"
        and score >= args.exact_document_auto_review_threshold
    ):
        document_status = "valid_official_document"
        operator_status = "operator_reviewed"
        confidence = "high"
        filter_status = "kept"
        filter_reasons = []
    else:
        document_status = "needs_operator_review"
        operator_status = "needs_operator_review"
        confidence = "medium" if score >= args.exact_document_min_score else "low"
        filter_status = "kept"
        filter_reasons = []
    candidate = {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name") or "",
        "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
        "inn": issuer.get("inn") or "",
        "ogrn": issuer.get("ogrn") or "",
        "report_period": str(args.report_period),
        "report_type": args.report_type,
        "accounting_standard": args.accounting_standard,
        "source_type": source_type,
        "source_url_context": source_page_url,
        "source_page_url": source_page_url,
        "document_url": document_url,
        "document_title": title,
        "document_kind": document_kind,
        **period_classification,
        **type_classification,
        **standard_classification,
        "crawl_depth": crawl_depth,
        "parent_seed_url": parent_seed_url or source_page_url,
        "source_chain": [*(source_chain or []), document_url],
        "is_category_page": is_category_page,
        "category_followed": False,
        "document_date": "",
        "source_file_name": _file_name_from_url(document_url),
        "document_status": document_status,
        "operator_review_status": operator_status,
        "candidate_rank": None,
        "candidate_score": score,
        "raw_score": score,
        "final_score": score,
        "candidate_confidence": confidence,
        "confidence": confidence,
        "filter_status": filter_status,
        "filter_reasons": filter_reasons,
        "discovery_method": "reviewed_seed_anchor_scan",
        "score_reasons": reasons,
        "negative_reasons": negatives,
        "notes": classification["message"],
    }
    _strip_financial_values(candidate)
    return candidate, blocked


def score_exact_document_candidate_from_seed(
    document_url: str,
    title: str,
    source_page_url: str,
    *,
    args: argparse.Namespace,
    domain_status: str,
    document_kind: str | None = None,
) -> tuple[int, list[str], list[str]]:
    text = f"{document_url} {title}".casefold()
    path = urllib.parse.urlparse(document_url).path.casefold()
    source_host = _host(source_page_url)
    document_host = _host(document_url)
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

    if domain_status == "official":
        add(20, "official allowlisted domain")
    if source_host and source_host == document_host:
        add(15, "same domain as reviewed seed")
    if _url_is_pdf(document_url) or path.endswith((".doc", ".docx", ".xls", ".xlsx", ".zip")):
        add(25, "document-like URL")
    if str(args.report_period) and str(args.report_period) in text:
        add(20, "target report period")
    years = set(re.findall(r"20\d{2}", text))
    if years and str(args.report_period) not in years:
        subtract(55, "wrong report year")
    if _contains_any(text, ("ifrs", "\u043c\u0441\u0444\u043e")):
        add(20, "IFRS signal")
    if _contains_any(text, ("annual", "yearly", "\u0433\u043e\u0434\u043e\u0432")):
        add(20, "annual report signal")
    if _contains_any(text, ("financial statements", "financial report", "statement", "\u0444\u0438\u043d\u0430\u043d\u0441", "\u043e\u0442\u0447\u0435\u0442")):
        add(20, "financial reporting signal")
    if _contains_any(text, ("consolidated", "\u043a\u043e\u043d\u0441\u043e\u043b\u0438\u0434")):
        add(10, "consolidated signal")
    if _contains_any(text, ("audited", "auditor", "audit", "\u0430\u0443\u0434")):
        add(10, "audited report signal")
    if _looks_like_report_document_url(document_url):
        add(15, "report document path")
    if _contains_any(path, EXACT_DOCUMENT_PATH_TERMS):
        add(10, "report file/path signal")
    if document_kind == "exact_report_document":
        add(20, "exact report document kind")
    if document_kind in EXACT_DOCUMENT_CATEGORY_KINDS:
        add(10, "report category page")
    if document_kind in EXACT_DOCUMENT_WRONG_TYPE_KINDS:
        subtract(90, f"{document_kind} is not exact report evidence")
    if _contains_any(text, EXACT_DOCUMENT_NEGATIVE_TERMS):
        subtract(50, "non-annual report document type")
    if args.report_type == "annual" and _contains_any(
        text,
        (
            "quarter",
            "quarterly",
            "q1",
            "q2",
            "q3",
            "q4",
            "\u043a\u0432\u0430\u0440\u0442\u0430\u043b",
            "6 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
            "9 \u043c\u0435\u0441\u044f\u0446\u0435\u0432",
        ),
    ):
        subtract(35, "quarterly/interim document in annual mode")
    if _exact_document_is_generic_page(document_url, title, args):
        subtract(40, "generic seed page instead of exact document")
    if not _contains_any(text, EXACT_DOCUMENT_POSITIVE_TERMS) and not _url_is_pdf(document_url):
        subtract(30, "no exact report/document signal")
    return max(score, 0), reasons, negatives


def _select_top_exact_document_candidates(
    candidates: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prepared = [dict(candidate) for candidate in candidates]
    for candidate in prepared:
        _apply_exact_document_candidate_filter(candidate, args=args)
    issuers_with_target = {
        str(item.get("company_id") or item.get("canonical_company_id") or "")
        for item in prepared
        if _exact_document_is_downstream_eligible(item)
    }
    for candidate in prepared:
        issuer_key = str(candidate.get("company_id") or candidate.get("canonical_company_id") or "")
        if candidate.get("filter_status") == "kept_fallback" and issuer_key in issuers_with_target:
            candidate["fallback_status"] = "fallback_rejected_target_required"
            candidate["document_status"] = "filtered_document"
            candidate["operator_review_status"] = "operator_to_fill"
            _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_period", "target-period document exists; fallback rejected")
    before_count = sum(1 for item in prepared if item.get("document_url"))

    by_url: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_count = 0
    for candidate in sorted(prepared, key=_exact_document_candidate_sort_key, reverse=True):
        if candidate.get("filter_status") == "filtered_blocked":
            continue
        key = (
            str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
            _normalized_operator_seed_candidate_url(str(candidate.get("document_url") or "")),
        )
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = candidate
            continue
        duplicate_count += 1
        _mark_exact_document_candidate_filtered(candidate, "filtered_duplicate", "duplicate document URL")
    kept_candidates = list(by_url.values())
    filtered_candidates = [
        item for item in prepared if item.get("filter_status") != "kept" and item not in kept_candidates
    ]
    _apply_exact_document_top_n(kept_candidates, args=args)
    _assign_exact_document_candidate_ranks(kept_candidates)
    output = [
        item
        for item in kept_candidates
        if _exact_document_is_downstream_eligible(item)
    ]
    if args.exact_document_include_category_pages:
        output.extend(
            item
            for item in kept_candidates
            if item.get("document_url")
            and item.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS
            and item.get("filter_status") in {"category_page", "category_followed", "diagnostic_category_page"}
        )
    if args.exact_document_period_policy == "target-or-prior-year-fallback" and args.exact_document_allow_prior_year_fallback:
        output.extend(
            item
            for item in kept_candidates
            if item.get("document_url") and item.get("filter_status") == "kept_fallback"
        )
    if args.exact_document_include_wrong_period:
        output.extend(
            item
            for item in prepared
            if item.get("document_url")
            and item.get("filter_status") in {"filtered_wrong_period", "filtered_unknown_period"}
        )
    if args.exact_document_include_wrong_report_type:
        output.extend(
            item
            for item in prepared
            if item.get("document_url")
            and item.get("filter_status") == "filtered_wrong_report_type"
        )
    if args.exact_document_include_filtered:
        output.extend(
            item
            for item in prepared
            if item.get("filter_status") != "kept"
            and item.get("document_url")
        )
    low_score_count = sum(
        1 for item in prepared if item.get("filter_status") == "filtered_low_score"
    )
    noise_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_noise")
    blocked_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_blocked")
    wrong_type_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_wrong_document_type")
    wrong_period_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_wrong_period")
    wrong_report_type_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_wrong_report_type")
    wrong_standard_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_wrong_standard")
    unknown_period_count = sum(1 for item in prepared if item.get("filter_status") == "filtered_unknown_period")
    kept_fallback_count = sum(1 for item in prepared if item.get("filter_status") == "kept_fallback")
    stats = {
        "candidate_count_before_filter": before_count,
        "candidate_count_after_filter": sum(1 for item in output if _exact_document_is_downstream_eligible(item)),
        "filtered_candidate_count": sum(1 for item in prepared if item.get("filter_status") != "kept"),
        "filtered_noise_count": noise_count,
        "filtered_low_score_count": low_score_count,
        "filtered_duplicate_count": duplicate_count,
        "filtered_blocked_count": blocked_count,
        "filtered_wrong_document_type_count": wrong_type_count,
        "filtered_wrong_period_count": wrong_period_count,
        "filtered_wrong_report_type_count": wrong_report_type_count,
        "filtered_wrong_standard_count": wrong_standard_count,
        "filtered_unknown_period_count": unknown_period_count,
        "kept_fallback_document_count": kept_fallback_count,
        "top_ranked_candidate_count": sum(1 for item in output if _exact_document_is_downstream_eligible(item)),
    }
    return sorted(output, key=_exact_document_output_sort_key), stats


def _apply_exact_document_candidate_filter(candidate: dict[str, Any], *, args: argparse.Namespace) -> None:
    if candidate.get("filter_status") in {
        "filtered_blocked",
        "filtered_duplicate",
        "filtered_wrong_document_type",
        "filtered_wrong_period",
        "filtered_wrong_report_type",
        "filtered_wrong_standard",
        "filtered_unknown_period",
        "kept_fallback",
        "category_followed",
        "diagnostic_category_page",
    }:
        return
    score = int(candidate.get("candidate_score") or candidate.get("final_score") or 0)
    candidate["raw_score"] = int(candidate.get("raw_score") or score)
    candidate["final_score"] = score
    candidate["candidate_score"] = score
    document_kind = str(candidate.get("document_kind") or "unknown_document")
    if candidate.get("document_status") in {"blocked_document", "invalid_document"}:
        status = "filtered_wrong_document_type" if document_kind in EXACT_DOCUMENT_WRONG_TYPE_KINDS else "filtered_blocked"
        reason = "wrong document type for exact annual report" if status == "filtered_wrong_document_type" else "blocked or invalid document URL"
        _mark_exact_document_candidate_filtered(candidate, status, reason)
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if document_kind in EXACT_DOCUMENT_WRONG_TYPE_KINDS:
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_document_type", "wrong document type for exact annual report")
        candidate["document_status"] = "invalid_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if document_kind in EXACT_DOCUMENT_CATEGORY_KINDS:
        candidate["document_status"] = "category_page"
        candidate["operator_review_status"] = "needs_operator_review"
        if candidate.get("category_followed"):
            candidate["filter_status"] = "category_followed"
        else:
            candidate["filter_status"] = "diagnostic_category_page"
        reasons = list(candidate.get("filter_reasons") or [])
        if "category/reporting page is not exact report evidence" not in reasons:
            reasons.append("category/reporting page is not exact report evidence")
        candidate["filter_reasons"] = reasons
        return
    period_status = str(candidate.get("document_period_status") or "unknown_period")
    type_status = str(candidate.get("report_type_match_status") or "unknown_report_type")
    standard_status = str(candidate.get("accounting_standard_match_status") or "unknown_standard")
    if (
        args.exact_document_filter_interim_for_annual
        and str(args.report_type).casefold() == "annual"
        and type_status == "interim_or_quarterly_mismatch"
    ):
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_report_type", "interim/quarterly document cannot satisfy annual report request")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if args.exact_document_filter_wrong_report_type and type_status == "report_type_conflict":
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_report_type", "report type evidence conflicts with requested report type")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if args.exact_document_filter_wrong_period and period_status in {"wrong_period", "period_conflict"}:
        status = "filtered_wrong_period"
        reason = "document period does not match requested report period"
        _mark_exact_document_candidate_filtered(candidate, status, reason)
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        candidate["fallback_status"] = "fallback_rejected_target_required"
        return
    if args.exact_document_filter_wrong_standard and standard_status == "standard_mismatch":
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_standard", "accounting standard does not match requested standard")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if (
        args.exact_document_filter_wrong_standard
        and str(args.accounting_standard).casefold() == "ifrs"
        and standard_status == "unknown_standard"
    ):
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_standard", "IFRS accounting standard evidence is missing")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if period_status == "prior_period_fallback_candidate":
        if args.exact_document_allow_prior_year_fallback and args.exact_document_period_policy == "target-or-prior-year-fallback":
            candidate["filter_status"] = "kept_fallback"
            candidate["document_status"] = "fallback_candidate"
            candidate["operator_review_status"] = "needs_operator_review"
            candidate["candidate_confidence"] = "medium"
            candidate["confidence"] = "medium"
            candidate["fallback_status"] = "fallback_candidate"
            reasons = list(candidate.get("filter_reasons") or [])
            if "prior-year fallback candidate; not target-period evidence" not in reasons:
                reasons.append("prior-year fallback candidate; not target-period evidence")
            candidate["filter_reasons"] = reasons
            return
        _mark_exact_document_candidate_filtered(candidate, "filtered_wrong_period", "prior-year fallback is disabled")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        candidate["fallback_status"] = "fallback_rejected_target_required"
        return
    if period_status == "unknown_period" and args.exact_document_target_period_required:
        _mark_exact_document_candidate_filtered(candidate, "filtered_unknown_period", "target report period is required but document period is unknown")
        candidate["document_status"] = "filtered_document"
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if document_kind != "exact_report_document":
        _mark_exact_document_candidate_filtered(candidate, "filtered_noise", "candidate is not classified as exact report document")
        candidate["operator_review_status"] = "operator_to_fill"
        return
    if args.exact_document_noise_filter and _exact_document_noise(candidate, args=args):
        _mark_exact_document_candidate_filtered(candidate, "filtered_noise", "non-annual report or generic navigation document")
        return
    if score < args.exact_document_min_score:
        _mark_exact_document_candidate_filtered(candidate, "filtered_low_score", "candidate score below minimum")
        return
    candidate.setdefault("filter_status", "kept")
    candidate.setdefault("filter_reasons", [])


def _mark_exact_document_candidate_filtered(candidate: dict[str, Any], status: str, reason: str) -> None:
    candidate["filter_status"] = status
    reasons = list(candidate.get("filter_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    candidate["filter_reasons"] = reasons


def _exact_document_noise(candidate: dict[str, Any], *, args: argparse.Namespace) -> bool:
    text = f"{candidate.get('document_url') or ''} {candidate.get('document_title') or ''}".casefold()
    if candidate.get("document_kind") in EXACT_DOCUMENT_WRONG_TYPE_KINDS:
        return True
    if candidate.get("document_status") in {"blocked_document", "invalid_document"}:
        return True
    if _contains_any(text, EXACT_DOCUMENT_NEGATIVE_TERMS):
        return True
    if _contains_any(text, EXACT_DOCUMENT_LEGAL_POLICY_TERMS):
        return True
    if _exact_document_is_generic_page(str(candidate.get("document_url") or ""), str(candidate.get("document_title") or ""), args):
        return True
    return False


def _apply_exact_document_top_n(candidates: list[dict[str, Any]], *, args: argparse.Namespace) -> None:
    per_type_limit = max(int(args.exact_document_top_n_per_type or 0), 0)
    if per_type_limit:
        by_type: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for candidate in candidates:
            if candidate.get("filter_status") != "kept":
                continue
            key = (
                str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
                str(candidate.get("source_type") or ""),
            )
            by_type.setdefault(key, []).append(candidate)
        for group in by_type.values():
            ranked = sorted(group, key=_exact_document_candidate_sort_key, reverse=True)
            for candidate in ranked[per_type_limit:]:
                _mark_exact_document_candidate_filtered(candidate, "filtered_low_score", "outside top-N per source type")
    per_issuer_limit = max(int(args.exact_document_top_n_per_issuer or 0), 0)
    if per_issuer_limit:
        by_issuer: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            if candidate.get("filter_status") != "kept":
                continue
            key = str(candidate.get("company_id") or candidate.get("canonical_company_id") or "")
            by_issuer.setdefault(key, []).append(candidate)
        for group in by_issuer.values():
            ranked = sorted(group, key=_exact_document_candidate_sort_key, reverse=True)
            for candidate in ranked[per_issuer_limit:]:
                _mark_exact_document_candidate_filtered(candidate, "filtered_low_score", "outside top-N per issuer")


def _assign_exact_document_candidate_ranks(candidates: list[dict[str, Any]]) -> None:
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("filter_status") != "kept":
            candidate["candidate_rank"] = None
            continue
        key = (
            str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
            str(candidate.get("source_type") or ""),
        )
        by_group.setdefault(key, []).append(candidate)
    for group in by_group.values():
        for rank, candidate in enumerate(sorted(group, key=_exact_document_candidate_sort_key, reverse=True), start=1):
            candidate["candidate_rank"] = rank


def _exact_document_candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int, int, int, str]:
    score = int(candidate.get("final_score") or candidate.get("candidate_score") or 0)
    reviewed = 1 if candidate.get("operator_review_status") == "operator_reviewed" else 0
    kind = str(candidate.get("document_kind") or "")
    exact_kind = 1 if kind == "exact_report_document" else 0
    exact = 1 if _exact_document_is_document_like(
        str(candidate.get("document_url") or ""),
        str(candidate.get("document_title") or ""),
        argparse.Namespace(report_period=candidate.get("report_period") or ""),
    ) else 0
    path_len = -len(urllib.parse.urlparse(str(candidate.get("document_url") or "")).path)
    return (score, reviewed, exact_kind, exact, _confidence_rank(candidate.get("candidate_confidence")), path_len, str(candidate.get("document_url") or ""))


def _exact_document_output_sort_key(candidate: dict[str, Any]) -> tuple[str, str, int, int, str]:
    return (
        str(candidate.get("company_id") or candidate.get("canonical_company_id") or ""),
        str(candidate.get("source_type") or ""),
        int(candidate.get("candidate_rank") or 9999),
        -int(candidate.get("candidate_score") or 0),
        str(candidate.get("document_url") or ""),
    )


def _exact_document_source_type(seed: dict[str, Any], document_url: str, source_page_url: str) -> str:
    seed_type = str(seed.get("seed_type") or "")
    host = _host(document_url) or _host(source_page_url)
    if "disclosure" in host or seed_type.startswith("official_disclosure"):
        return "official_disclosure"
    return "official_issuer_report"


def _exact_document_is_document_like(document_url: str, title: str, args: argparse.Namespace) -> bool:
    text = f"{document_url} {title}".casefold()
    if _url_is_pdf(document_url):
        return True
    if _looks_like_report_document_url(document_url):
        return True
    return _contains_any(text, ("annual report", "financial statements", "ifrs", "\u043c\u0441\u0444\u043e", "\u0433\u043e\u0434\u043e\u0432", "\u043e\u0442\u0447\u0435\u0442"))


def _exact_document_has_strong_signals(document_url: str, title: str, args: argparse.Namespace) -> bool:
    text = f"{document_url} {title}".casefold()
    target_period = str(getattr(args, "report_period", "") or "")
    if target_period and target_period not in text:
        return False
    annual = _contains_any(text, ("annual", "yearly", "\u0433\u043e\u0434\u043e\u0432"))
    standard = _contains_any(text, ("ifrs", "\u043c\u0441\u0444\u043e", "financial statements", "\u0444\u0438\u043d\u0430\u043d\u0441"))
    negative = _contains_any(text, EXACT_DOCUMENT_NEGATIVE_TERMS)
    return bool(annual and standard and not negative and _exact_document_is_document_like(document_url, title, args))


def _exact_document_is_generic_page(document_url: str, title: str, args: argparse.Namespace) -> bool:
    if _url_is_pdf(document_url):
        return False
    path = urllib.parse.urlparse(document_url).path.casefold().rstrip("/")
    text = f"{document_url} {title}".casefold()
    if path in {"", "/", "/invest", "/investor", "/investors", "/reports", "/disclosure", "/information-disclosure"}:
        return True
    return _looks_like_landing_page(document_url) and not _contains_any(text, ("annual", "ifrs", "\u043c\u0441\u0444\u043e", "financial statements"))


def _exact_document_not_found_candidate(issuer: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": issuer.get("company_id"),
        "company_name": issuer.get("company_name") or "",
        "canonical_company_id": issuer.get("canonical_company_id") or issuer.get("company_id"),
        "canonical_company_name": issuer.get("canonical_company_name") or issuer.get("company_name") or "",
        "inn": issuer.get("inn") or "",
        "ogrn": issuer.get("ogrn") or "",
        "report_period": "",
        "report_type": "",
        "accounting_standard": "",
        "source_type": "official_issuer_report",
        "source_page_url": "",
        "source_url_context": "",
        "document_url": "",
        "document_title": "",
        "document_kind": "unknown_document",
        "document_period_year": "",
        "document_period_quarter": "",
        "document_period_status": "unknown_period",
        "period_confidence": "low",
        "period_evidence": [],
        "report_type_match_status": "unknown_report_type",
        "type_evidence": [],
        "accounting_standard_match_status": "unknown_standard",
        "standard_evidence": [],
        "fallback_status": "not_fallback",
        "crawl_depth": None,
        "parent_seed_url": "",
        "source_chain": [],
        "is_category_page": False,
        "category_followed": False,
        "document_date": "",
        "source_file_name": "",
        "document_status": "not_found",
        "operator_review_status": "operator_to_fill",
        "candidate_rank": None,
        "candidate_score": 0,
        "raw_score": 0,
        "final_score": 0,
        "candidate_confidence": "low",
        "confidence": "low",
        "filter_status": "placeholder_not_found",
        "filter_reasons": ["placeholder not_found row; not target-period evidence"],
        "availability_status": "placeholder_not_found",
        "availability_reason_codes": ["placeholder_not_found", "exact_target_period_document_not_found"],
        "can_use_as_target_period_evidence": False,
        "historical_fallback_allowed": False,
        "historical_fallback_scope": "none",
        "operator_action": "operator_to_find_official_exact_document",
        "discovery_method": "reviewed_seed_anchor_scan",
        "score_reasons": [],
        "negative_reasons": [],
        "notes": "No exact official report document candidate found from reviewed seed pages.",
    }


def _attach_exact_document_optional_metadata(
    documents: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    for document in documents:
        document_url = str(document.get("document_url") or "")
        if not document_url:
            continue
        classification = _classify_candidate_url(
            document_url,
            allowed_domains=_exact_document_allowed_domains(args),
            blocked_hints=_exact_document_blocked_hints(args),
            allow_unknown_source=False,
        )
        if classification["status"] != "official":
            continue
        if args.exact_document_probe_urls:
            probe = _probe_url(
                document_url,
                timeout_seconds=args.exact_document_fetch_timeout_seconds,
                max_bytes=args.exact_document_max_response_bytes,
            )
            document["probe"] = probe
            document["probe_status"] = probe.get("status")
            document["probe_http_status"] = probe.get("http_status")
            document["probe_content_type"] = probe.get("content_type")
            if probe.get("status") != "ok":
                warnings.append(
                    {
                        "company_id": document.get("company_id"),
                        "document_url": document_url,
                        "message": "document probe failed",
                        "error": probe.get("error"),
                    }
                )
        if args.exact_document_download_documents and document.get("operator_review_status") == "operator_reviewed":
            if args.exact_document_download_dir is None:
                warnings.append(
                    {
                        "company_id": document.get("company_id"),
                        "document_url": document_url,
                        "message": "exact document download requested without --exact-document-download-dir",
                    }
                )
                continue
            download = _download_valid_document(document, args.exact_document_download_dir)
            document["download"] = download
            warnings.extend(download.get("warnings") or [])
            errors.extend(download.get("errors") or [])


def _source_intake_from_exact_document_candidates(
    required_issuers: list[dict[str, Any]],
    *,
    documents: list[dict[str, Any]],
    reviewed_seeds_used: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issuers: list[dict[str, Any]] = []
    for required in required_issuers:
        document_matches = [
            item
            for item in _items_matching_required(documents, required)
            if _exact_document_is_downstream_eligible(item)
        ]
        seed_matches = _items_matching_required(reviewed_seeds_used, required)
        chosen = (document_matches or seed_matches or [required])[0]
        source_candidates: list[dict[str, Any]] = []
        for document in document_matches:
            source_candidates.append(
                {
                    "source_type": document.get("source_type") or "official_issuer_report",
                    "url": document.get("document_url") or "",
                    "document_title": document.get("document_title") or "",
                    "document_date": document.get("document_date") or "",
                    "report_period": document.get("report_period") or "",
                    "source_file_name": document.get("source_file_name") or "",
                    "status": "valid_official_source"
                    if document.get("operator_review_status") == "operator_reviewed"
                    else "needs_operator_review",
                    "notes": "Generated from exact-document-discover-from-seeds candidate.",
                }
            )
        for seed in seed_matches:
            source_candidates.append(
                {
                    "source_type": "official_disclosure" if str(seed.get("seed_type") or "").startswith("official_disclosure") else "issuer_investor_relations",
                    "url": seed.get("seed_url") or "",
                    "document_title": "",
                    "document_date": "",
                    "report_period": "",
                    "source_file_name": "",
                    "status": "needs_operator_review",
                    "notes": "Reviewed seed page used for exact document discovery.",
                }
            )
        issuers.append(
            {
                "company_id": chosen.get("company_id") or required.get("company_id"),
                "company_name": chosen.get("company_name") or required.get("company_name") or "",
                "canonical_company_id": chosen.get("canonical_company_id") or chosen.get("company_id") or required.get("company_id"),
                "canonical_company_name": chosen.get("canonical_company_name") or chosen.get("company_name") or required.get("company_name") or "",
                "period_year": str(chosen.get("report_period") or ""),
                "source_candidates": source_candidates,
            }
        )
    return issuers


def _build_exact_document_discovery_report(
    args: argparse.Namespace,
    *,
    status: str,
    required_issuers: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    input_documents: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    reviewed_seeds_used: list[dict[str, Any]],
    missing_issuers: list[dict[str, Any]],
    ranking_stats: dict[str, int],
    blocked_candidate_count: int,
    invalid_candidate_count: int,
    reviewed_candidate_count: int,
    needs_operator_review_count: int,
    target_reporting_period_availability: list[dict[str, Any]] | None,
    document_intake_fill_report: dict[str, Any] | None,
    document_intake_validation_report: dict[str, Any] | None,
    document_quality_gate_report: dict[str, Any] | None,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    category_pages_followed: list[dict[str, Any]] | None = None,
    all_documents_for_counters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate_count = sum(1 for item in documents if _exact_document_is_downstream_eligible(item))
    counter_documents = all_documents_for_counters if all_documents_for_counters is not None else documents
    kind_counts = _exact_document_kind_counts(counter_documents)
    period_type_counts = _exact_document_period_type_counts(counter_documents)
    followed_category_pages = category_pages_followed or [
        item
        for item in documents
        if item.get("document_kind") in EXACT_DOCUMENT_CATEGORY_KINDS and item.get("category_followed")
    ]
    availability_operator_report = _build_availability_operator_summary_report(
        args,
        status=status,
        target_reporting_period_availability=target_reporting_period_availability,
        document_quality_gate_report=document_quality_gate_report,
        warnings=warnings,
        errors=errors,
    )
    availability_summary = availability_operator_report["summary"]
    operator_review_queue_report = _build_operator_review_queue_report(
        args,
        status=status,
        availability_operator_rows=availability_operator_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    operator_review_queue_summary = operator_review_queue_report["summary"]
    official_source_coverage_report = _build_official_source_coverage_report(
        args,
        status=status,
        required_issuers=required_issuers,
        seed_issuers=seed_issuers,
        input_documents=input_documents,
        reviewed_seeds_used=reviewed_seeds_used,
        documents=documents,
        all_documents_for_counters=counter_documents,
        category_pages_followed=followed_category_pages,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        warnings=warnings,
        errors=errors,
    )
    official_source_coverage_summary = official_source_coverage_report["summary"]
    historical_fallback_registry_report = _build_historical_fallback_registry_report(
        args,
        status=status,
        required_issuers=required_issuers,
        documents=documents,
        all_documents_for_counters=counter_documents,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    historical_fallback_registry_summary = historical_fallback_registry_report["summary"]
    reporting_readiness_matrix_report = _build_reporting_readiness_matrix_report(
        args,
        status=status,
        required_issuers=required_issuers,
        availability_operator_rows=availability_operator_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        historical_fallback_registry_rows=historical_fallback_registry_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    reporting_readiness_summary = reporting_readiness_matrix_report["summary"]
    operator_resolution_pack_report = _build_operator_resolution_pack_report(
        args,
        status=status,
        required_issuers=required_issuers,
        reporting_readiness_rows=reporting_readiness_matrix_report["issuers"],
        operator_review_queue=operator_review_queue_report["actions"],
        availability_operator_rows=availability_operator_report["issuers"],
        official_source_coverage_rows=official_source_coverage_report["issuers"],
        historical_fallback_registry_rows=historical_fallback_registry_report["issuers"],
        warnings=warnings,
        errors=errors,
    )
    operator_resolution_pack_summary = operator_resolution_pack_report["summary"]
    return {
        "status": status,
        "mode": "exact-document-discover-from-seeds",
        "issuer_count": len(required_issuers),
        "candidate_count_before_filter": ranking_stats.get("candidate_count_before_filter", 0),
        "candidate_count_after_filter": ranking_stats.get("candidate_count_after_filter", 0),
        "candidate_count": candidate_count,
        "reviewed_candidate_count": reviewed_candidate_count,
        "needs_operator_review_count": needs_operator_review_count,
        "invalid_candidate_count": invalid_candidate_count,
        "blocked_candidate_count": blocked_candidate_count,
        "filtered_candidate_count": ranking_stats.get("filtered_candidate_count", 0),
        "filtered_noise_count": ranking_stats.get("filtered_noise_count", 0),
        "filtered_low_score_count": ranking_stats.get("filtered_low_score_count", 0),
        "filtered_duplicate_count": ranking_stats.get("filtered_duplicate_count", 0),
        "filtered_wrong_document_type_count": ranking_stats.get("filtered_wrong_document_type_count", 0),
        "filtered_wrong_period_count": ranking_stats.get("filtered_wrong_period_count", 0),
        "filtered_wrong_report_type_count": ranking_stats.get("filtered_wrong_report_type_count", 0),
        "filtered_wrong_standard_count": ranking_stats.get("filtered_wrong_standard_count", 0),
        "filtered_unknown_period_count": ranking_stats.get("filtered_unknown_period_count", 0),
        "kept_fallback_document_count": ranking_stats.get("kept_fallback_document_count", 0),
        "top_ranked_candidate_count": ranking_stats.get("top_ranked_candidate_count", 0),
        **kind_counts,
        **period_type_counts,
        "target_reporting_period_availability": target_reporting_period_availability or [],
        **availability_summary,
        "availability_operator_summary": availability_summary,
        "availability_operator_rows": availability_operator_report["issuers"],
        **operator_review_queue_summary,
        "operator_review_queue_summary": operator_review_queue_summary,
        "operator_review_queue": operator_review_queue_report["actions"],
        **official_source_coverage_summary,
        "official_source_coverage_summary": official_source_coverage_summary,
        "official_source_coverage_rows": official_source_coverage_report["issuers"],
        **historical_fallback_registry_summary,
        "historical_fallback_registry_summary": historical_fallback_registry_summary,
        "historical_fallback_registry_rows": historical_fallback_registry_report["issuers"],
        **reporting_readiness_summary,
        "reporting_readiness_summary": reporting_readiness_summary,
        "reporting_readiness_rows": reporting_readiness_matrix_report["issuers"],
        **operator_resolution_pack_summary,
        "operator_resolution_pack_summary": operator_resolution_pack_summary,
        "operator_resolution_pack_rows": operator_resolution_pack_report["resolutions"],
        "reviewed_seeds_used": reviewed_seeds_used,
        "category_pages_followed": followed_category_pages,
        "missing_issuers": missing_issuers,
        "documents": documents,
        "document_intake_fill_report": document_intake_fill_report,
        "document_intake_validation_report": document_intake_validation_report,
        "document_quality_gate_report": document_quality_gate_report,
        "exact_document_candidate_output": _path_value(args.exact_document_candidate_output),
        "exact_document_candidate_csv_output": _path_value(args.exact_document_candidate_csv_output),
        "document_intake_output": _path_value(args.document_intake_output),
        "document_intake_csv_output": _path_value(args.document_intake_csv_output),
        "document_intake_validation_json_output": _path_value(args.document_intake_validation_json_output),
        "document_intake_validation_markdown_output": _path_value(args.document_intake_validation_markdown_output),
        "quality_gate_json_output": _path_value(args.quality_gate_json_output),
        "quality_gate_markdown_output": _path_value(args.quality_gate_markdown_output),
        "availability_operator_summary_output": _path_value(args.availability_operator_summary_output),
        "availability_operator_summary_csv_output": _path_value(args.availability_operator_summary_csv_output),
        "availability_operator_summary_markdown_output": _path_value(args.availability_operator_summary_markdown_output),
        "operator_review_queue_output": _path_value(args.operator_review_queue_output),
        "operator_review_queue_csv_output": _path_value(args.operator_review_queue_csv_output),
        "operator_review_queue_markdown_output": _path_value(args.operator_review_queue_markdown_output),
        "official_source_coverage_output": _path_value(args.official_source_coverage_output),
        "official_source_coverage_csv_output": _path_value(args.official_source_coverage_csv_output),
        "official_source_coverage_markdown_output": _path_value(args.official_source_coverage_markdown_output),
        "historical_fallback_registry_output": _path_value(args.historical_fallback_registry_output),
        "historical_fallback_registry_csv_output": _path_value(args.historical_fallback_registry_csv_output),
        "historical_fallback_registry_markdown_output": _path_value(args.historical_fallback_registry_markdown_output),
        "reporting_readiness_matrix_output": _path_value(args.reporting_readiness_matrix_output),
        "reporting_readiness_matrix_csv_output": _path_value(args.reporting_readiness_matrix_csv_output),
        "reporting_readiness_matrix_markdown_output": _path_value(args.reporting_readiness_matrix_markdown_output),
        "operator_resolution_pack_output": _path_value(args.operator_resolution_pack_output),
        "operator_resolution_pack_csv_output": _path_value(args.operator_resolution_pack_csv_output),
        "operator_resolution_pack_markdown_output": _path_value(args.operator_resolution_pack_markdown_output),
        "warnings": warnings,
        "errors": errors,
        "next_steps": _next_steps("exact-document-discover-from-seeds", status),
        **SAFETY_FLAGS,
    }


def _exact_document_kind_counts(documents: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "exact_report_document_count": 0,
        "category_page_count": 0,
        "legal_policy_document_count": 0,
        "privacy_policy_document_count": 0,
        "cookie_policy_document_count": 0,
        "user_agreement_document_count": 0,
        "presentation_document_count": 0,
        "prospectus_document_count": 0,
        "quarterly_or_interim_document_count": 0,
        "news_or_press_document_count": 0,
        "generic_navigation_page_count": 0,
        "unknown_document_count": 0,
    }
    for document in documents:
        if not document.get("document_url"):
            continue
        key = EXACT_DOCUMENT_KIND_COUNTERS.get(str(document.get("document_kind") or "unknown_document"), "unknown_document_count")
        counters[key] = counters.get(key, 0) + 1
    return counters


def _exact_document_period_type_counts(documents: list[dict[str, Any]]) -> dict[str, int]:
    counters = {
        "target_period_document_count": 0,
        "wrong_period_document_count": 0,
        "prior_period_fallback_candidate_count": 0,
        "unknown_period_document_count": 0,
        "period_conflict_document_count": 0,
        "annual_match_document_count": 0,
        "interim_or_quarterly_document_count": 0,
        "unknown_report_type_document_count": 0,
        "standard_match_document_count": 0,
        "standard_mismatch_document_count": 0,
        "unknown_standard_document_count": 0,
        "kept_target_period_document_count": 0,
        "kept_fallback_document_count": 0,
    }
    for document in documents:
        if not document.get("document_url"):
            continue
        period_status = str(document.get("document_period_status") or "")
        if period_status == "target_period":
            counters["target_period_document_count"] += 1
        elif period_status == "wrong_period":
            counters["wrong_period_document_count"] += 1
        elif period_status == "prior_period_fallback_candidate":
            counters["prior_period_fallback_candidate_count"] += 1
        elif period_status == "unknown_period":
            counters["unknown_period_document_count"] += 1
        elif period_status == "period_conflict":
            counters["period_conflict_document_count"] += 1
        type_status = str(document.get("report_type_match_status") or "")
        if type_status == "annual_match":
            counters["annual_match_document_count"] += 1
        elif type_status == "interim_or_quarterly_mismatch":
            counters["interim_or_quarterly_document_count"] += 1
        elif type_status == "unknown_report_type":
            counters["unknown_report_type_document_count"] += 1
        standard_status = str(document.get("accounting_standard_match_status") or "")
        if standard_status == "standard_match":
            counters["standard_match_document_count"] += 1
        elif standard_status == "standard_mismatch":
            counters["standard_mismatch_document_count"] += 1
        elif standard_status == "unknown_standard":
            counters["unknown_standard_document_count"] += 1
        if _exact_document_is_downstream_eligible(document):
            counters["kept_target_period_document_count"] += 1
        if document.get("filter_status") == "kept_fallback":
            counters["kept_fallback_document_count"] += 1
    return counters


def _write_exact_document_discovery_payload(
    args: argparse.Namespace,
    path: Path,
    *,
    required_issuers: list[dict[str, Any]],
    seed_issuers: list[dict[str, Any]],
    input_documents: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    reviewed_seeds_used: list[dict[str, Any]],
    missing_issuers: list[dict[str, Any]],
    ranking_stats: dict[str, int],
    blocked_candidate_count: int,
    target_reporting_period_availability: list[dict[str, Any]] | None,
    status: str,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    category_pages_followed: list[dict[str, Any]] | None = None,
    all_documents_for_counters: list[dict[str, Any]] | None = None,
) -> None:
    reviewed_count = sum(
        1
        for item in documents
        if _exact_document_is_downstream_eligible(item)
        and item.get("operator_review_status") == "operator_reviewed"
    )
    review_count = sum(
        1
        for item in documents
        if _exact_document_is_downstream_eligible(item)
        and item.get("operator_review_status") == "needs_operator_review"
    )
    invalid_count = sum(
        1
        for item in (all_documents_for_counters or documents)
        if item.get("document_status") in {"invalid_document", "blocked_document"}
    )
    report = _build_exact_document_discovery_report(
        args,
        status=status,
        required_issuers=required_issuers,
        seed_issuers=seed_issuers,
        input_documents=input_documents,
        documents=documents,
        category_pages_followed=category_pages_followed,
        reviewed_seeds_used=reviewed_seeds_used,
        missing_issuers=missing_issuers,
        ranking_stats=ranking_stats,
        blocked_candidate_count=blocked_candidate_count,
        invalid_candidate_count=invalid_count,
        reviewed_candidate_count=reviewed_count,
        needs_operator_review_count=review_count,
        target_reporting_period_availability=target_reporting_period_availability,
        document_intake_fill_report=None,
        document_intake_validation_report=None,
        document_quality_gate_report=None,
        warnings=warnings,
        errors=errors,
        all_documents_for_counters=all_documents_for_counters,
    )
    write_json_report(report, path)


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
        if not document.get("document_url") and document.get("document_status") == "not_found":
            continue
        if document.get("filter_status") and document.get("filter_status") != "kept":
            continue
        if document.get("document_kind") and document.get("document_kind") != "exact_report_document":
            continue
        if document.get("document_period_status") and document.get("document_period_status") != "target_period":
            continue
        if document.get("report_type_match_status") and document.get("report_type_match_status") != "annual_match":
            continue
        if document.get("accounting_standard_match_status") and document.get("accounting_standard_match_status") != "standard_match":
            continue
        if document.get("fallback_status") and document.get("fallback_status") != "not_fallback":
            continue
        if document.get("document_status") not in {None, "", "valid_official_document", "needs_operator_review"}:
            continue
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
    if mode == "exact-document-discover-from-seeds":
        return ["Review exact document candidates, then run document-intake-fill, document-intake-validate, and the strict document quality gate."]
    if mode == "operator-resolution-validation":
        return ["Review validation rows; valid rows are only eligible for a future controlled intake review step."]
    if mode == "operator-resolution-apply-preview":
        return ["Review patch rows; this preview does not update intake or trigger extraction/import."]
    if mode == "operator-resolution-apply-draft":
        return ["Validate the new draft intake before any quality gate; original intake remains unchanged."]
    if mode == "document-intake-draft-gate-preview":
        return ["Review draft gate blockers; extraction and import remain disabled in this preview workflow."]
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


def _paths_equal(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


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


def _generic_report_output_is_safe(args: argparse.Namespace, output_path: Path | None) -> bool:
    if output_path is None:
        return False
    protected_input = (
        args.document_intake_input
        if args.mode == "operator-resolution-apply-draft"
        else args.document_intake_draft_input
        if args.mode == "document-intake-draft-gate-preview"
        else None
    )
    return protected_input is None or not _paths_equal(output_path, protected_input)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report, exit_code = run_assistant(args)
    if _generic_report_output_is_safe(args, args.json_output):
        write_json_report(report, args.json_output)
        print(
            f"[financial-official-source-evidence-assistant] wrote JSON report: {args.json_output}",
            flush=True,
        )
    if _generic_report_output_is_safe(args, args.markdown_output):
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
