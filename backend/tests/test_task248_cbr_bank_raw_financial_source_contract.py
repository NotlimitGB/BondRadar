from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK248_CBR_BANK_RAW_FINANCIAL_SOURCE_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task248_contract_has_exact_sections_baseline_and_blocked_result() -> None:
    text = _contract()
    section_numbers = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert text.startswith(
        "# Task248 — CBR 0409806/0409807 Public-Value Source Blocker"
    )
    assert section_numbers == list(range(1, 40))
    for projection in (
        "STARTING_SHA=6eebd5be76cbcf70666b33ce5bd2500fb22939b1",
        "ALEMBIC_HEAD=202608280002",
        "CHANGED_FILE_COUNT=2",
        "STATUS=BLOCKED",
        "REASON=PUBLIC_SCHEMA_EXISTS_BUT_ACTUAL_REPORT_VALUES_ARTIFACT_NOT_PROVEN",
        "IMPLEMENTATION=DOCUMENTATION_ONLY",
    ):
        assert projection in text


def test_task248_repository_and_official_artifact_evidence_is_exact() -> None:
    text = _contract()

    for capability in (
        "EXISTING_CBR_REPORTING_CLIENT=NONE",
        "EXISTING_ARCHIVE_PARSER=NONE",
        "EXISTING_DBF_SUPPORT=NONE",
        "EXISTING_XML_SUPPORT=NONE",
        "EXISTING_HASHING_SUPPORT=hashlib.sha256",
        "EXISTING_BANK_IDENTITY_SUPPORT=PARTIAL_LEGALISSUER_INN_ONLY_NO_CBR_REGN_CONTRACT",
    ):
        assert capability in text

    for artifact in (
        "ARTIFACT_1_URL=https://www.cbr.ru/vfs/credit/forms/nfo-201901.zip",
        "ARTIFACT_1_BYTES=648329",
        "ARTIFACT_1_SHA256=d1834cee43ef0207463d318330242466827175a3bb2f48d106188b936710e073",
        "ARTIFACT_2_URL=https://www.cbr.ru/vfs/credit/forms/nfo-201810.zip",
        "ARTIFACT_2_BYTES=19676",
        "ARTIFACT_2_SHA256=a69be11535a068d43ae7df750b115169c6cf58784875b6021d65226e8771a9e5",
        "LIVE_ARTIFACT_COUNT=2",
        "TOTAL_COMPRESSED_BYTES=668005",
    ):
        assert artifact in text

    for locator in (
        "https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/",
        "https://www.cbr.ru/development/kliko/xml_f/",
        "https://www.cbr.ru/vfs/credit/formats/nfo-20180101.PDF",
    ):
        assert locator in text


def test_task248_legacy_dbf_mapping_and_target_absence_are_not_conflated() -> None:
    text = _contract()

    for mapping in (
        "I815 -> 0409815",
        "I816 -> 0409816",
        "I817 -> 0409817",
        "I818 -> 0409818",
        "B818 -> 0409818",
        "REGN=CBR_CREDIT_ORGANIZATION_REGISTRATION_NUMBER",
        "LEGACY_NFO_VALUES_UNIT=THOUSANDS_OF_RUBLES",
    ):
        assert mapping in text

    for projection in (
        "PUBLIC_DOWNLOAD_PROVEN=true",
        "DATA_ARTIFACT_PROVEN=true",
        "ACTUAL_CONTAINER_FORMAT=ZIP_WITH_DBF_MEMBERS",
        "ACTUAL_REPORT_FAMILY=LEGACY_NONCONSOLIDATED_0409815_0409816_0409817_0409818",
        "CONTAINS_0409806=false",
        "CONTAINS_0409807=false",
        "TARGET_0409806_VALUES_PROVEN=false",
        "TARGET_0409807_VALUES_PROVEN=false",
        "SCHEMA_ARTIFACT_PROVEN=true",
        "800P_ROLE=SUBMISSION_SCHEMA_FAMILY",
        "800P_ACTUAL_VALUES_ARTIFACT_PROVEN=false",
    ):
        assert projection in text

    assert "are not substitutes\nfor 0409806/0409807" in text
    assert "A schema, template\nor regulation cannot be promoted" in text


def test_task248_future_raw_identity_unit_period_and_failure_contracts_fail_closed() -> None:
    text = _contract()

    for requirement in (
        "cbr_registration_number",
        "source_artifact_sha256",
        "source_schema_version",
        "source_row_code",
        "source_column_code",
        "raw_value",
        "raw_unit",
        "raw_currency",
        "raw_multiplier",
        "source_as_of_date",
        "period_start",
        "period_end",
        "source_published_at",
        "observed_at",
        "retrieved_at",
        "report_scope=UNRESOLVED_FOR_TARGET_ADAPTER",
        "accounting_standard=UNRESOLVED_FOR_TARGET_ADAPTER",
        "missing currency != RUB",
        "missing multiplier != 1",
        "missing value != zero",
        "PIT_PARTIAL",
    ):
        assert requirement in text

    for failure in (
        "FORM_NOT_PRESENT",
        "UNSUPPORTED_SCHEMA_VERSION",
        "REPORTING_SUBJECT_UNRESOLVED",
        "INVALID_ARCHIVE",
        "INVALID_DBF",
        "INVALID_XML",
        "VALUE_PARSE_ERROR",
        "SOURCE_ERROR",
        "TIMEOUT",
        "RATE_LIMITED",
    ):
        assert failure in text

    assert "SOURCE_ERROR != FORM_NOT_PRESENT" in text
    assert "VALUE_PARSE_ERROR != zero" in text
    assert "path traversal" in text
    assert "archive bombs" in text


def test_task248_has_no_operational_implementation_or_unsafe_side_effect() -> None:
    text = _contract()

    for invariant in (
        "TARGET_CLIENT_IMPLEMENTED=false",
        "TARGET_PARSER_IMPLEMENTED=false",
        "LIVE_PROBE_IMPLEMENTED=false",
        "LIVE_PROBE_RUN=false",
        "MIGRATION=NONE",
        "DATABASE_PERSISTENCE=false",
        "DATABASE_ACCESSED=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "FINANCIAL_REPORT_MUTATION_EXECUTED=false",
        "LEGAL_ISSUER_MUTATION_EXECUTED=false",
        "DEPENDENCY_ADDED=false",
        "APPLICATION_CODE_CHANGED=false",
        "PRODUCTION_ACTIONS=NONE",
    ):
        assert invariant in text

    for forbidden in (
        "No client, parser, CLI, dependency, migration, model, service, persistence,",
        "normalization, scaling, metric calculation, scoring",
        "browser automation, production/VDS access, broker call or trading",
    ):
        assert forbidden in text


def test_task248_handoff_and_validation_scope_are_exact() -> None:
    text = _contract()

    assert (
        "RECOMMENDED_TASK249=Task249 — CBR Forms 101/102 Raw Regulatory Source "
        "Suitability Contract"
    ) in text
    assert "TASK249_AUTOMATICALLY_STARTED=false" in text
    assert "TASK249_INGESTION_AUTHORIZED=false" in text
    assert "must not rename 101/102 as 0409806/0409807" in text
    assert "The full backend suite is intentionally not run" in text
    assert "The focused test" in text
    assert "performs no network or database work" in text
    assert "CI=NOT_WAITED_BY_DESIGN" in text
    assert "LIVE_NETWORK_USED=true" in text
