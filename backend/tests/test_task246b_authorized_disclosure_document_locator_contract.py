from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK246B_AUTHORIZED_DISCLOSURE_DOCUMENT_LOCATOR_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task246b_contract_has_all_numbered_sections_in_order() -> None:
    text = _contract()
    section_numbers = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert section_numbers == list(range(1, 43))
    assert text.startswith(
        "# Task246B — Authorized Official Disclosure Document Locator Contract"
    )


def test_task246b_source_matrix_and_access_contract_are_complete() -> None:
    text = _contract()

    for locator in (
        "https://www.cbr.ru/registries/rcb/reestr-cb",
        "https://www.cbr.ru/issuers_corporate/el_reg_issue/",
        "https://gateway.e-disclosure.ru/swagger/ui/index.html",
        "https://e-disclosure.ru/poluchenie-informacii/shlyuz-api",
    ):
        assert locator in text

    for authority in (
        "OFFICIAL_REGULATOR",
        "ACCREDITED_DISCLOSURE",
        "ISSUER_PRIMARY",
        "DISCOVERY_ONLY",
    ):
        assert authority in text

    for access in (
        "PUBLIC_ACCESS",
        "AUTHORIZED_ACCESS_AVAILABLE",
        "AUTHORIZED_ACCESS_NOT_CONFIGURED",
        "SUBSCRIPTION_REQUIRED",
        "ACCESS_DENIED",
        "RATE_LIMITED",
        "AUTHENTICATION_ERROR",
        "TERMS_REVIEW_REQUIRED",
        "SOURCE_UNAVAILABLE",
    ):
        assert access in text

    for automation in (
        "AUTOMATION_ALLOWED_FOR_CURRENT_RESEARCH",
        "AUTOMATION_REQUIRES_REVIEW",
        "AUTOMATION_RESTRICTED",
        "AUTOMATION_UNKNOWN",
    ):
        assert automation in text


def test_task246b_cbr_and_interfax_projections_are_frozen() -> None:
    text = _contract()

    for cbr_value in (
        "ISSUE_IDENTITY=READY",
        "DOCUMENT_METADATA=PARTIAL",
        "DOCUMENT_LOCATOR=NO_GO",
        "BYTE_RETRIEVAL=NO_GO",
        "PIT=PIT_PARTIAL",
    ):
        assert cbr_value in text

    for interfax_value in (
        "API_DOCUMENTED=true",
        "AUTH_REQUIRED=true",
        "SUBSCRIPTION_REQUIRED=true",
        "EVENT_METADATA=true",
        "DOCUMENT_ID=true",
        "BYTE_DOWNLOAD=true",
        "HISTORICAL_QUERY=true_WITH_CONTRACT_LIMITS",
        "EXACT_ISSUE_BINDING=NOT_PROVEN",
        "CURRENT_ACCESS_AVAILABLE=false",
        "PRICE_FILES_REPORTS_RUB_MONTH_EX_VAT=16180",
        "MINIMUM_SUBSCRIPTION_MONTHS=3",
    ):
        assert interfax_value in text

    assert "PUBLIC_WEB_PATH=MANUAL_ONLY" in text
    assert "ISSUER_SITE_PATH=TARGETED_MANUAL_ONLY" in text
    assert "public documentation reviewed\ndoes not prove a universal exact ISIN" in text


def test_task246b_locator_byte_failure_and_temporal_contracts_fail_closed() -> None:
    text = _contract()

    for locator_status in (
        "DOCUMENT_REFERENCE_FOUND",
        "NO_DOCUMENT_REFERENCE_FOUND",
        "ISSUE_NOT_FOUND",
        "ISSUE_IDENTITY_INCOMPLETE",
        "DOCUMENT_SCOPE_AMBIGUOUS",
        "AUTH_REQUIRED",
        "SUBSCRIPTION_REQUIRED",
        "ACCESS_DENIED",
        "RATE_LIMITED",
        "SOURCE_ERROR",
        "SCHEMA_ERROR",
        "UNSUPPORTED_SOURCE",
    ):
        assert locator_status in text

    for retrieval_status in (
        "RETRIEVED",
        "NOT_FOUND",
        "TIMEOUT",
        "INVALID_CONTENT",
        "UNSUPPORTED_MEDIA_TYPE",
    ):
        assert retrieval_status in text

    for field in (
        "content_sha256",
        "content_length",
        "media_type",
        "observed_at",
        "source_published_at",
        "source_registered_at",
        "source_updated_at",
        "retrieved_at",
        "effective_from",
        "effective_to",
        "MUTABLE_LOCATOR=true",
    ):
        assert field in text

    for pit_state in ("PIT_READY", "PIT_PARTIAL", "CURRENT_ONLY", "PIT_UNKNOWN"):
        assert pit_state in text

    assert "No failure is converted to `NO_DOCUMENT_EXISTS`" in text
    assert "No\npreprocessing" in text
    assert "text normalization" in text
    assert "same bytes => same SHA256" in text
    assert "different bytes => different SHA256" in text


def test_task246b_decision_and_safety_contract_are_exact() -> None:
    text = _contract()

    for decision in (
        "LOCATOR_DECISION=TARGETED_MANUAL_ONLY",
        "ECONOMIC_GATE=TARGETED_MANUAL",
        "IMPLEMENTATION=DOCUMENTATION_ONLY",
        "RECOMMENDED_NEXT_STEP=DEFER_DOCUMENT_AUTOMATION_AND_MOVE_TO_M2_CREDIT_DATA",
    ):
        assert decision in text

    for invariant in (
        "CURRENT_ISSUE_IDENTIFIERS=Bond.isin,Bond.secid",
        "CURRENT_DOCUMENT_LOCATOR=NONE",
        "CURRENT_DOCUMENT_RETRIEVAL=TASK_SPECIFIC_ONLY_NOT_REUSABLE",
        "CURRENT_HASH_UTILITY=hashlib.sha256_LOCAL_CALL_SITES",
        "CURRENT_DISCLOSURE_CREDENTIAL_CONFIG=NONE",
        "AUTOMATIC_VERIFICATION=false",
        "REVIEW_REQUIRED=true",
        "ISSUER_WIDE_PROPAGATION=false",
        "OCR_ALLOWED=false",
        "LLM_EXTRACTION_ALLOWED=false",
        "FUZZY_TARGET_MATCHING=false",
        "MIGRATION=NONE",
        "LIVE_PROBE_RUN=false",
        "NETWORK_USED_FOR_PROBE=false",
        "AUTHORIZED_ENDPOINT_USED=false",
        "PROTECTED_ENDPOINT_CALLED=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "DOCUMENT_PERSISTENCE_IMPLEMENTED=false",
        "RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false",
        "REVIEW_PERSISTENCE_IMPLEMENTED=false",
        "PUBLIC_SITE_SCRAPER_IMPLEMENTED=false",
        "ISSUER_SITE_CRAWL_IMPLEMENTED=false",
        "PRODUCTION_ACTIONS=NONE",
        "TASK246C_STARTED=false",
        "CI=NOT_WAITED_BY_DESIGN",
    ):
        assert invariant in text

    assert "No disclosure client or generic locator service is implemented" in text
    assert "No `scripts/disclosure_document_locator_probe.py` is created" in text
    assert "The full backend suite is not run" in text
