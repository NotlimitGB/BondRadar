from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK250_CBR_BANK_REGULATORY_BUNDLE_SOURCE_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task250_has_exact_sections_baseline_and_documentation_scope() -> None:
    text = _contract()
    sections = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert text.startswith(
        "# Task250 — CBR Bank Regulatory Raw Bundle 101/102/123/135 "
        "Source Contract"
    )
    assert sections == list(range(1, 43))
    for projection in (
        "STARTING_SHA=12a54027487f29c9813a9d257cf32f5685b3a88f",
        "ALEMBIC_HEAD=202608280002",
        "EXPECTED_CHANGED_FILE_COUNT=2",
        "IMPLEMENTATION=DOCUMENTATION_ONLY",
        "MIGRATION=NONE",
        "APPLICATION_CODE_CHANGED=false",
        "DATABASE_PERSISTENCE=false",
        "EXISTING_BANK_BUNDLE_CLIENT=NONE",
        "EXISTING_RAR_SUPPORT=NONE",
        "EXISTING_DBF_SUPPORT=NONE",
        "EXISTING_REGN_IDENTITY_BRIDGE=NONE",
        "EXISTING_BANK_RAW_MODEL=NONE",
    ):
        assert projection in text


def test_task250_artifact_inventory_hashes_and_download_budget_are_exact() -> None:
    text = _contract()

    for artifact in (
        "`101-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a`",
        "`102-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21`",
        "`123-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/123-20260801.rar` | 33042 | `6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab`",
        "`135-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/135-20260801.rar` | 33181 | `061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776`",
        "`123-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/123-20210101.rar` | 153297 | `77da8e43ac061190a6c6eea5ea99fc4ef80ac574e52635ec3abac6333f5bae50`",
        "`135-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/135-20210101.rar` | 1351714 | `15e55cb21a555d0e9c62d23c651b05517793048985737d64c3e3fec65477bcde`",
    ):
        assert artifact in text

    for invariant in (
        "LIVE_NETWORK_USED=true",
        "DATA_ARTIFACTS_DOWNLOADED=6",
        "TOTAL_BYTES=2005672",
        "MAX_ARTIFACTS=6",
        "ADDITIONAL_DOWNLOADS_AFTER_LOCK=0",
        "CONTAINER_FORMAT=RAR",
        "MASS_ARCHIVE_CRAWL=false",
        "DATABASE_ACCESSED=false",
    ):
        assert invariant in text


def test_task250_exact_forms_members_and_public_123_contract_are_frozen() -> None:
    text = _contract()

    for identity in (
        "101=0409101",
        "102=0409102",
        "123=0409123",
        "135=0409135",
        "101!=0409806",
        "102!=0409807",
    ):
        assert identity in text

    for projection in (
        "FORM_123_CURRENT_MEMBERS=072026_123B.dbf,072026_123D.dbf,072026_123N.dbf",
        "FORM_123_CURRENT_SUBJECTS=352",
        "FORM_123_CURRENT_DATA_ROWS=1400",
        "FORM_123_CURRENT_NOMENCLATURE_ROWS=156",
        "FORM_123_CURRENT_CODES=000,102,105,203",
        "FORM_123_NONBLANK_COUNTS=000:352,102:350,105:349,203:349",
        "FORM_123_UNIT=RUB_THOUSANDS",
        "`REGN N(4)`, `C1 C(15)`, `C3 N(16)`",
        "123_2021_SUBJECTS=406",
        "123_2021_DATA_ROWS=48133",
        "123_2021_CODES=163",
        "122020_123S.DBF",
    ):
        assert projection in text


def test_task250_public_135_contract_reduction_and_version_boundary_are_exact() -> None:
    text = _contract()

    for projection in (
        "FORM_135_CURRENT_MEMBERS=072026_135_3.dbf,072026_135B.dbf",
        "FORM_135_CURRENT_SUBJECTS=345",
        "FORM_135_CURRENT_DATA_ROWS=1709",
        "FORM_135_ACTUAL_CODES=N1.0,N1.1,N1.2,N1.3,N2,N3,N4,N15,N15.1,N16,N16.1,N16.2,N27",
        "FORM_135_UNIT=PERCENT",
        "`REGN N(4)`, `C1_3 C(6)`, `C2_3 N(19,3)`,",
        "`C3_3 N(19,3)`, `C4_3 C(12)`",
        "135_2021_SUBJECTS=403",
        "135_2021_SECTION3_ROWS=2921",
        "122020_135_8.DBF",
    ):
        assert projection in text

    assert "FORM_SCHEMA!=PUBLIC_DISCLOSURE" in text
    assert "`N18` is `DISCLOSABLE_BY_RULE`" in text
    assert "ACTUAL_STATE=UNKNOWN_NOT_OBSERVED" in text
    assert "not zero, blank, suppressed or automatically not applicable" in text

    for boundary in (
        "CURRENT_135_REGULATORY_FORM_REGIME_EFFECTIVE_FROM=2026-07-01",
        "PREVIOUS_135_REGULATORY_FORM_REGIME_END=2026-06-30",
        "CURRENT_135_PUBLIC_DBF_FORMAT_EFFECTIVE_FROM=2023-06-01",
        "CURRENT_135_PUBLIC_DBF_FORMAT_PROVEN=true",
        "PREVIOUS_135_PUBLIC_ARCHIVE_PROVEN=true",
        "REGULATORY_SCHEMA_BOUNDARY!=PUBLIC_DBF_LAYOUT_BOUNDARY",
        "UNSUPPORTED_SCHEMA_VERSION",
    ):
        assert boundary in text


def test_task250_regn_bridge_and_cross_form_coverage_are_measured() -> None:
    text = _contract()

    for identity in (
        "101_HAS_REGN=true",
        "102_HAS_REGN=true",
        "123_HAS_REGN=true",
        "135_HAS_REGN=true",
        "REGN_TO_OGRN_PROVEN=true",
        "REGN_TO_INN_PROVEN=false",
        "CURRENT_BRIDGE_AVAILABLE=true",
        "HISTORICAL_BRIDGE_AVAILABLE=PARTIAL",
        "LEGALISSUER_MAPPING_IMPLEMENTED=false",
        "TITLE_ONLY_MAPPING_ALLOWED=false",
        "FUZZY_NAME_MATCHING=false",
    ):
        assert identity in text

    for count in (
        "101_SUBJECTS=353",
        "102_SUBJECTS=212",
        "123_SUBJECTS=352",
        "135_SUBJECTS=345",
        "ALL_FOUR_INTERSECTION=211",
        "101_102_INTERSECTION=212",
        "101_123_INTERSECTION=352",
        "101_135_INTERSECTION=345",
        "102_123_INTERSECTION=211",
        "102_135_INTERSECTION=211",
        "123_135_INTERSECTION=345",
    ):
        assert count in text

    for combination in (
        "101+102 `1`",
        "101+123 `7`",
        "101+123+135\n`134`",
        "all four `211`",
    ):
        assert combination in text

    for correction in (
        "TASK250_POST_IMPLEMENTATION_CORRECTION=true",
        "CORRECTION_SOURCE=TASK251_EXACT_IMMUTABLE_FIXTURE_VALUE_MEMBER_AUDIT",
        "PREVIOUS_ALL_FOUR_INTERSECTION=170",
        "CORRECTED_ALL_FOUR_INTERSECTION=211",
        "VALUE_MEMBER_101=072026B1.dbf",
        "VALUE_MEMBER_102=072026_P1.dbf",
        "VALUE_MEMBER_123=072026_123D.dbf",
        "VALUE_MEMBER_135=072026_135_3.dbf",
        "SUBJECT_SET_HASH_101=692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
        "SUBJECT_SET_HASH_102=90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
        "SUBJECT_SET_HASH_123=5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
        "SUBJECT_SET_HASH_135=660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
    ):
        assert correction in text

    assert "Every other exact combination has count `0`" in text


def test_task250_units_period_pit_failures_and_runtime_are_fail_closed() -> None:
    text = _contract()

    for invariant in (
        "RAR_RUNTIME_STRATEGY=UNRESOLVED",
        "RAR_DEPENDENCY_ADDED=false",
        "PRE_EXTRACTED_SOURCE_AVAILABLE=false",
        "missing currency != RUB",
        "missing unit != assumed unit",
        "missing multiplier != 1",
        "missing value != zero",
        "VALUE_CONVERSION_EXECUTED=false",
        "same archive month != identical financial period semantics",
        "FORM_101_PIT=PIT_PARTIAL",
        "FORM_102_PIT=PIT_PARTIAL",
        "FORM_123_PIT=PIT_PARTIAL",
        "FORM_135_PIT=PIT_PARTIAL",
        "REPORT_DATE!=PUBLICATION_DATE",
        "REGULATORY_NON_DISCLOSURE!=BANK_DATA_MISSING",
    ):
        assert invariant in text

    for failure in (
        "SOURCE_ERROR",
        "ARTIFACT_NOT_FOUND",
        "FORM_NOT_AVAILABLE_FOR_PERIOD",
        "REGULATORY_DISCLOSURE_RESTRICTED",
        "SUBJECT_NOT_DISCLOSED",
        "INVALID_ARCHIVE",
        "INVALID_DBF",
        "UNSUPPORTED_SCHEMA_VERSION",
    ):
        assert failure in text


def test_task250_completeness_matrix_contains_every_required_credit_domain() -> None:
    text = _contract()

    for status in (
        "`STRONG_RAW_SUPPORT`",
        "`PARTIAL_RAW_SUPPORT`",
        "`AGGREGATED_ONLY`",
        "`NOT_SUPPORTED`",
    ):
        assert status in text

    for domain in (
        "balance-sheet scale",
        "asset composition",
        "customer funding",
        "bank funding",
        "accounting capital",
        "regulatory capital",
        "capital adequacy",
        "mandatory liquidity",
        "mandatory prudential ratios",
        "profitability",
        "interest income/expense",
        "provisioning",
        "net income",
        "concentration",
        "maturity structure",
        "asset quality",
        "IFRS/group-level view",
    ):
        assert f"| {domain} |" in text


def test_task250_unique_decisions_handoff_and_safety_are_enforced() -> None:
    text = _contract()

    assert text.count("SOURCE_DECISION=BANK_REGULATORY_BUNDLE_READY_WITH_LIMITATIONS") == 1
    assert text.count("ECONOMIC_GATE=BUILD_BANK_BUNDLE_ADAPTER_WITH_LIMITATIONS") == 1
    assert (
        text.count(
            "RECOMMENDED_TASK251=Task251 — CBR Bank Regulatory Bundle "
            "Read-Only Source v1"
        )
        == 1
    )

    for safety in (
        "LEGACY_COMPANY_MAPPING_REUSED=false",
        "UNSAFE_RUB_DEFAULT_REUSED=false",
        "MISSING_VALUE_ZERO_REUSED=false",
        "LEGACY_PERIOD_OVERWRITE_REUSED=false",
        "RAW_SOURCE_TABLE_CREATED=false",
        "BANK_FINANCIAL_TABLE_CREATED=false",
        "NORMALIZATION_EXECUTED=false",
        "METRIC_CALCULATION_EXECUTED=false",
        "SCORING_EXECUTED=false",
        "TASK251_AUTOMATICALLY_STARTED=false",
        "TASK251_IMPLEMENTATION_AUTHORIZED=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "PRODUCTION_ACTIONS=NONE",
        "CI=NOT_WAITED_BY_DESIGN",
    ):
        assert safety in text

    assert "Tests read local text only" in text
    assert "perform no network or database operation" in text
    assert "The full backend suite is intentionally not run" in text
