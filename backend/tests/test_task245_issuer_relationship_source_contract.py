from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK245_ISSUER_RELATIONSHIP_SOURCE_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task245_contract_has_all_numbered_sections_in_order() -> None:
    text = _contract()
    section_numbers = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert section_numbers == list(range(1, 35))
    assert text.startswith(
        "# Task245 — Issuer Relationship Source Contract & Coverage Audit"
    )


def test_task245_relationship_and_source_decisions_are_complete() -> None:
    text = _contract()
    relationship_decisions = {
        "`LEGAL_ISSUER_CLASSIFICATION` | `PARTIAL`",
        "`IMMEDIATE_PARENT` | `DISCOVERY_ONLY`",
        "`ULTIMATE_PARENT` | `NO_GO`",
        "`GROUP_MEMBERSHIP` | `DISCOVERY_ONLY`",
        "`GUARANTOR` | `PARTIAL`",
        "`SPV_SPONSOR_ORIGINATOR` | `PARTIAL`",
        "`REPORTING_ENTITY` | `DISCOVERY_ONLY`",
    }
    source_families = {
        "MOEX ISS",
        "Bank of Russia",
        "FNS / EGRUL / Transparent Business",
        "Official disclosures and disclosure agencies",
        "Issue and prospectus documents",
        "Issuer official sites",
        "Commercial and third-party aggregators",
    }
    official_locators = {
        "https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0",
        "https://www.cbr.ru/registries/rcb/reestr-cb",
        "https://cbr.ru/finorg/",
        "https://www.nalog.gov.ru/rn77/service/egrip2/egrip_vzayim/",
        "https://e-disclosure.ru/poluchenie-informacii/shlyuz-api",
    }

    for value in relationship_decisions | source_families | official_locators:
        assert value in text

    for tier in (
        "AUTHORITATIVE_STRUCTURED",
        "AUTHORITATIVE_DOCUMENT",
        "PRIMARY_SEMI_STRUCTURED",
        "DISCOVERY_ONLY",
        "UNSUITABLE",
    ):
        assert tier in text

    for scope in (
        "LEGAL_ISSUER",
        "SECURITY",
        "ISSUE",
        "ISSUE_PROGRAM",
        "REPORTING_SCOPE",
    ):
        assert scope in text


def test_task245_temporal_probe_and_licensing_contracts_fail_closed() -> None:
    text = _contract()

    for temporal_field in (
        "observed_at",
        "published_at",
        "effective_from",
        "effective_to",
        "CURRENT_RELATIONSHIP != PIT_RELATIONSHIP",
        "CURRENT_ONLY_MAY_NOT_BE_BACKCAST=true",
    ):
        assert temporal_field in text

    for status in (
        "SOURCE_SUCCESS",
        "SOURCE_SUCCESS_NO_RELATION",
        "SUBJECT_NOT_FOUND",
        "TARGET_IDENTITY_INCOMPLETE",
        "RELATION_AMBIGUOUS",
        "SOURCE_UNSUPPORTED_FOR_RELATION",
        "SOURCE_ERROR",
    ):
        assert status in text

    assert "`SOURCE_ERROR` must never become `SOURCE_SUCCESS_NO_RELATION`" in text
    assert "No source with unclear terms is labeled" in text
    assert "`CLEAR_FOR_CURRENT_RESEARCH_USE`" in text
    assert "`REQUIRES_REVIEW`" in text
    assert "`UNKNOWN` or `RESTRICTED`" in text
    assert "GUARANTOR_DEFAULT_SCOPE=SECURITY_OR_ISSUE" in text
    assert "ISSUER_WIDE_GUARANTEE_INFERRED=false" in text


def test_task245_is_documentation_only_and_preserves_safety_boundary() -> None:
    text = _contract()

    for invariant in (
        "MIGRATION=NONE",
        "PROBE_IMPLEMENTED=false",
        "LIVE_PROBE_RUN=false",
        "DB_COVERAGE_RUN=false",
        "PRODUCTION_ACTIONS=NONE",
        "RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false",
        "CURRENT_RELATIONSHIP_COVERAGE=NOT_MEASURED_DURING_IMPLEMENTATION",
        "DATABASE_MUTATION_EXECUTED=false",
        "ALEMBIC_EXECUTED=false",
        "LEGAL_ISSUER_MUTATED=false",
        "BOND_MUTATED=false",
        "COMPANY_MUTATED=false",
        "FINANCIAL_REPORT_MUTATED=false",
        "SCORING_EXECUTED=false",
        "BROKER_USED=false",
        "TRADING_EXECUTED=false",
        "TASK246_AUTOMATICALLY_UNLOCKED=false",
        "TASK246_EXECUTED=false",
        "CI=NOT_WAITED_BY_DESIGN",
    ):
        assert invariant in text

    assert "generic `issuer -> related_company` abstraction" in text
    assert "CompanyIdentityProfile.issuer_group_name" in text
    assert "Task246A — Issue Document Locator and Guarantor Evidence Contract" in text
    assert "never converts them to\n   parent/control automatically" in text
    assert "separate CBR/FNS registry-membership classification contract" in text
