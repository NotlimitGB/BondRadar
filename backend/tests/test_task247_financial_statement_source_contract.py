from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK247_FINANCIAL_STATEMENT_SOURCE_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task247_contract_has_all_numbered_sections_and_locked_scope() -> None:
    text = _contract()
    section_numbers = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert text.startswith(
        "# Task247 — Financial Statement Source Contract & Coverage Audit"
    )
    assert section_numbers == list(range(1, 41))
    assert "STARTING_COMMIT=70ae11270d3f720f09cfc2677ce0d28163ba091a" in text
    assert "ALEMBIC_HEAD=202608280002" in text
    assert "CHANGED_FILE_COUNT=2" in text
    assert "MIGRATION=NONE" in text
    assert "APPLICATION_CODE_CHANGED=false" in text


def test_task247_current_financial_model_audit_is_complete() -> None:
    text = _contract()

    for projection in (
        "CURRENT_FINANCIAL_OWNER_MODEL=Company",
        "CURRENT_REPORT_IDENTITY=Company+period_year+period_quarter",
        "CURRENT_PERIOD_MODEL=year+quarter_with_optional_start_end",
        "CURRENT_ACCOUNTING_STANDARD_MODEL=NONE",
        "CURRENT_CONSOLIDATION_MODEL=NONE",
        "CURRENT_RESTATEMENT_MODEL=OVERWRITE_OR_COLLISION",
        "CURRENT_SOURCE_DOCUMENT_MODEL=Company_scoped_without_content_hash_or_stable_source_report_id",
        "CURRENT_PIT_CAPABILITY=PARTIAL_BUT_UNSAFE_LEGACY_FALLBACK",
        "CURRENT_CURRENCY_UNIT_MODEL=currency_with_unsafe_RUB_default_and_no_report_unit_multiplier",
    ):
        assert projection in text

    for component in (
        "`LegalIssuer` | `KEEP`",
        "`FinancialReport` | `LEGACY_ONLY`, `UNSAFE_FOR_M2`",
        "`FinancialReportSourceDocument` | provenance pattern `ADAPT`, current contract `UNSAFE_FOR_M2`",
        "`FinancialReportIngestionService` | `LEGACY_ONLY`, `UNSAFE_FOR_M2`",
        "Company credit/scoring consumers | `LEGACY_ONLY`, `UNSAFE_FOR_M2`",
        "`ControlledFinancialStatementValue` evidence/checksum mechanics | `KEEP`",
        "`ControlledFinancialStatementValue` identity/period model | `ADAPT`",
    ):
        assert component in text

    assert "not a canonical LegalIssuer credit-data pipeline" in text
    assert "not the source raw layer" in text


def test_task247_source_matrix_and_frozen_recommendations_are_exact() -> None:
    text = _contract()

    for locator in (
        "https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/",
        "https://www.cbr.ru/projects_xbrl/",
        "https://www.nalog.gov.ru/rn77/bo/",
        "https://bo.nalog.gov.ru/subscriptions-service",
        "https://e-disclosure.ru/poluchenie-informacii/shlyuz-api",
        "https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0",
    ):
        assert locator in text

    for status in (
        "CBR_BANK_REPORTING=PRIMARY_BUILD_NOW",
        "GIRS_BO_SUBSCRIPTION=SECONDARY_BUILD_LATER",
        "GIRS_BO_PUBLIC_PER_ORGANIZATION=TARGETED_FALLBACK",
        "CBR_NFO_XBRL=SECONDARY_BUILD_LATER",
        "ACCREDITED_DISCLOSURE_IFRS=TARGETED_FALLBACK",
        "ISSUER_WEBSITES=TARGETED_FALLBACK",
        "MOEX_FINANCIAL_STATEMENT_TRUTH=NO_GO",
        "FOREIGN_FINANCIAL_REGIMES=NO_GO_PENDING_SOURCE_SPECIFIC_CONTRACT",
    ):
        assert status in text

    for authority in (
        "OFFICIAL_REGULATOR",
        "OFFICIAL_REGISTRY",
        "ISSUER_PRIMARY",
        "ACCREDITED_DISCLOSURE",
        "DISCOVERY_ONLY",
    ):
        assert authority in text

    for data_form in (
        "STRUCTURED_API",
        "STRUCTURED_FILE",
        "XBRL",
        "HTML_TABLE",
        "SEARCHABLE_DOCUMENT",
        "DOCUMENT_ONLY",
    ):
        assert data_form in text

    for access in ("PUBLIC", "AUTHORIZED", "SUBSCRIPTION", "RESTRICTED", "UNKNOWN"):
        assert access in text

    for automation in ("READY", "REQUIRES_REVIEW", "RESTRICTED", "NO_GO"):
        assert automation in text

    for pit_state in ("PIT_READY", "PIT_PARTIAL", "CURRENT_ONLY", "PIT_UNKNOWN"):
        assert pit_state in text

    for licensing in (
        "CLEAR_FOR_CURRENT_RESEARCH_USE",
        "REQUIRES_REVIEW",
        "RESTRICTED",
        "UNKNOWN",
    ):
        assert licensing in text


def test_task247_identity_period_restated_currency_and_raw_contracts_fail_closed() -> None:
    text = _contract()

    for scope in (
        "LEGAL_ENTITY_STANDALONE",
        "CONSOLIDATED_GROUP",
        "BANK_REGULATORY",
        "INSURANCE_REGULATORY",
        "SPV_SPECIAL_PURPOSE",
        "STANDALONE",
        "CONSOLIDATED",
        "REGULATORY",
    ):
        assert scope in text

    for standard in ("RAS", "IFRS", "BANK_RAS", "BANK_IFRS", "OTHER_REGULATORY"):
        assert standard in text

    for period in ("as_of_date", "period_start", "period_end", "published_at"):
        assert period in text

    for boundary in (
        "LegalIssuer != ReportingEntity",
        "standalone != consolidated",
        "unknown != zero",
        "period end != publication date",
        "REPORTING_SUBJECT_UNRESOLVED",
        "REPORTING_ENTITY_MAPPING_REQUIRED",
        "financial_information_available_at_T only if published_at <= T",
        "Missing currency never\ndefaults to RUB",
        "Missing multiplier never defaults to `1`",
        "A restatement published at T2 is unavailable at\nT1",
    ):
        assert boundary in text

    for raw_field in (
        "source form and schema version",
        "source line/code",
        "source label",
        "raw value",
        "source unit",
        "source currency",
        "exact-byte SHA-256",
    ):
        assert raw_field in text


def test_task247_failure_coverage_architecture_and_handoff_are_frozen() -> None:
    text = _contract()

    for status in (
        "REPORT_FOUND",
        "NO_REPORT_FOUND",
        "SUBJECT_NOT_FOUND",
        "SUBJECT_IDENTITY_INCOMPLETE",
        "REPORT_SCOPE_UNKNOWN",
        "STANDARD_UNKNOWN",
        "AUTH_REQUIRED",
        "SUBSCRIPTION_REQUIRED",
        "RATE_LIMITED",
        "SOURCE_ERROR",
        "SCHEMA_ERROR",
        "SOURCE_NOT_APPLICABLE",
    ):
        assert status in text

    assert "SOURCE_ERROR != NO_REPORT_FOUND" in text
    assert "SOURCE_NOT_APPLICABLE != NO_REPORT_FOUND" in text
    assert "COVERAGE=NOT_MEASURED_DURING_IMPLEMENTATION" in text
    assert "Source Raw Layer" in text
    assert "Report Identity / Provenance" in text
    assert "Raw Financial Items" in text
    assert "Reviewed Normalization Layer" in text
    assert "Credit Metrics" in text
    assert "Credit Risk Engine" in text
    assert "Direct `source -> score` processing is forbidden" in text
    assert (
        "RECOMMENDED_TASK248=Task248 — CBR Bank Published Financial Forms "
        "0409806/0409807 Raw Source v1"
    ) in text
    assert "Task248 is a read-only, source-specific adapter" in text


def test_task247_has_no_probe_persistence_or_unsafe_execution_contract() -> None:
    text = _contract()

    for invariant in (
        "PROBE_IMPLEMENTED=false",
        "LIVE_PROBE_RUN=false",
        "NETWORK_USED_FOR_PROBE=false",
        "DB_COVERAGE_RUN=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "FINANCIAL_REPORT_MUTATION_EXECUTED=false",
        "LEGAL_ISSUER_MUTATION_EXECUTED=false",
        "PERSISTENCE_IMPLEMENTED=false",
        "PRODUCTION_ACTIONS=NONE",
        "CI=NOT_WAITED_BY_DESIGN",
    ):
        assert invariant in text

    assert "No probe is implemented" in text
    assert "Task247 performs no conversion, scaling, rounding or normalization" in text
    assert "No live network, database or application fixture is part of the test" in text
    assert "The full backend suite is intentionally not run" in text
