from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK246A_OFFICIAL_ISSUE_DOCUMENT_SUPPORT_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task246a_contract_has_all_numbered_sections_in_order() -> None:
    text = _contract()
    section_numbers = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert section_numbers == list(range(1, 39))
    assert text.startswith(
        "# Task246A — Official Issue Document Locator & Reviewed Support-Party "
        "Evidence Contract"
    )


def test_task246a_source_and_document_contracts_are_complete() -> None:
    text = _contract()

    for locator in (
        "https://www.cbr.ru/registries/rcb/reestr-cb",
        "https://www.cbr.ru/vfs/registers/rcb/reestrcb.pdf",
        "https://www.cbr.ru/registries/rcb/ecb/",
        "https://www.cbr.ru/issuers_corporate/el_reg_issue/",
        "https://e-disclosure.ru/poluchenie-informacii/shlyuz-api",
    ):
        assert locator in text

    for status in (
        "EXACT_ISIN",
        "EXACT_REGISTRATION_NUMBER",
        "IDENTIFIER_MISSING",
        "SECURITY_NOT_FOUND",
        "IDENTIFIER_CONFLICT",
        "AMBIGUOUS",
        "SOURCE_ERROR",
        "SCHEMA_ERROR",
    ):
        assert status in text

    for document_type in (
        "ISSUE_DECISION",
        "PROSPECTUS",
        "BOND_PROGRAM",
        "PLACEMENT_TERMS",
        "MATERIAL_FACT",
        "ISSUER_REPORT",
        "OTHER_OFFICIAL_DISCLOSURE",
    ):
        assert document_type in text

    for integrity_or_time_field in (
        "content_sha256",
        "content_length",
        "media_type",
        "retrieved_at",
        "observed_at",
        "published_at",
        "registered_at",
        "effective_from",
        "effective_to",
    ):
        assert integrity_or_time_field in text


def test_task246a_roles_scope_target_and_review_contracts_fail_closed() -> None:
    text = _contract()

    for role in (
        "GUARANTOR",
        "SURETY_PROVIDER",
        "OFFEROR",
        "COLLATERAL_PROVIDER",
        "SPONSOR",
        "ORIGINATOR",
        "SERVICER",
        "BACKUP_SERVICER",
    ):
        assert role in text

    for scope in ("SECURITY", "ISSUE", "ISSUE_PROGRAM"):
        assert scope in text

    for identity_state in (
        "STABLE_ID_PRESENT",
        "NAME_ONLY",
        "IDENTIFIER_CONFLICT",
        "TARGET_IDENTITY_INCOMPLETE",
    ):
        assert identity_state in text

    for stable_identifier in ("INN", "OGRN", "LEI", "official registry ID"):
        assert stable_identifier in text

    assert "ISSUER_WIDE_PROPAGATION=false" in text
    assert "review_state=REVIEW_REQUIRED" in text
    assert "AUTOMATIC_VERIFICATION=false" in text
    assert "REVIEW_REQUIRED=true" in text
    assert "APPROVAL_WORKFLOW_IMPLEMENTED=false" in text
    assert "OFFEROR != GUARANTOR" in text
    assert "ORIGINATOR != PARENT" in text


def test_task246a_failure_temporal_and_readability_contracts_are_explicit() -> None:
    text = _contract()

    for outcome in (
        "EXPLICIT_RELATION_FOUND",
        "DOCUMENT_FOUND_NO_SUPPORTED_RELATION",
        "DOCUMENT_NOT_MACHINE_READABLE",
        "DOCUMENT_NOT_FOUND",
        "DOCUMENT_SCOPE_INCOMPLETE",
        "TARGET_IDENTITY_INCOMPLETE",
        "SOURCE_UNSUPPORTED",
        "SOURCE_ERROR",
        "SCHEMA_ERROR",
    ):
        assert outcome in text

    for readability in (
        "STRUCTURED",
        "SEARCHABLE_TEXT",
        "BINARY_SEARCHABLE_PDF",
        "NON_MACHINE_READABLE",
        "UNSUPPORTED_FORMAT",
        "RETRIEVAL_FAILED",
    ):
        assert readability in text

    assert "SOURCE_ERROR != DOCUMENT_FOUND_NO_SUPPORTED_RELATION" in text
    assert "SOURCE_ERROR != NO_RELATION" in text
    assert "Missing extraction does not prove `NO_GUARANTOR_EXISTS`" in text
    assert "CURRENT_DOCUMENT_MAY_NOT_BE_BACKCAST=true" in text
    assert "PIT_CAPABILITY=LIMITED" in text
    assert "neither OCR nor LLM extraction" in text
    assert "No fuzzy title mapping" in text


def test_task246a_is_documentation_only_and_recommends_locator_work() -> None:
    text = _contract()

    for invariant in (
        "ISSUE_IDENTITY=READY",
        "OFFICIAL_DOCUMENT_LOCATOR=NO_GO",
        "AUTOMATED_EXTRACTION=NO_GO_FOR_AUTOMATED_EXTRACTION",
        "MIGRATION=NONE",
        "PROBE_IMPLEMENTED=false",
        "LIVE_PROBE_RUN=false",
        "DB_COVERAGE_RUN=false",
        "NETWORK_USED=false",
        "PRODUCTION_ACTIONS=NONE",
        "DATABASE_MUTATION_EXECUTED=false",
        "RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false",
        "LEGAL_ISSUER_MUTATED=false",
        "FINANCIAL_REPORT_MUTATED=false",
        "ALEMBIC_EXECUTED=false",
        "BOND_MUTATED=false",
        "COMPANY_MUTATED=false",
        "SECURITY_MASTER_MUTATED=false",
        "SCORING_EXECUTED=false",
        "BROKER_USED=false",
        "TRADING_EXECUTED=false",
        "CI=NOT_WAITED_BY_DESIGN",
        "FINAL_DECISION=NO_GO_FOR_AUTOMATED_EXTRACTION",
        "RECOMMENDED_NEXT_ACTION=IMPROVE_OFFICIAL_DOCUMENT_LOCATOR_FIRST",
        "TASK246B_AUTOMATICALLY_UNLOCKED=false",
    ):
        assert invariant in text

    assert (
        "RECOMMENDED_TASK246B=Task246B — Authorized Official Disclosure Document "
        "Locator Contract"
    ) in text
    assert "No `CbrSecurityRegistryClient`" in text
    assert "No `scripts/issue_document_support_probe.py`" in text
    assert "No live sample was run" in text
