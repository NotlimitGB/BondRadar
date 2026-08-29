from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT
    / "docs"
    / "audits"
    / "TASK249_CBR_101_102_SOURCE_SUITABILITY_CONTRACT.md"
)


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def test_task249_has_exact_sections_baseline_and_documentation_scope() -> None:
    text = _contract()
    sections = [
        int(value)
        for value in re.findall(r"^## (\d+)\. ", text, flags=re.MULTILINE)
    ]

    assert text.startswith(
        "# Task249 — CBR Forms 101/102 Raw Regulatory Source Suitability Contract"
    )
    assert sections == list(range(1, 41))
    for projection in (
        "STARTING_SHA=5065c284f0d8052bc33122ebd3ce5b64d88deba0",
        "ALEMBIC_HEAD=202608280002",
        "EXPECTED_CHANGED_FILE_COUNT=2",
        "IMPLEMENTATION=DOCUMENTATION_ONLY",
        "MIGRATION=NONE",
        "APPLICATION_CODE_CHANGED=false",
        "DATABASE_PERSISTENCE=false",
    ):
        assert projection in text


def test_task249_artifact_identity_hashes_and_bounded_investigation_are_exact() -> None:
    text = _contract()

    for artifact in (
        "`101-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a`",
        "`102-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21`",
        "`101-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20210101.rar` | 2352938 | `d1a54ad2aabaf47263f2fb233430013d149c3962251c050666741fff1de3552c`",
        "`102-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20210101.rar` | 2278946 | `e82a18dccff959823c0821184b09ed720f9cad22ae64e86cecd715a329f6ef94`",
    ):
        assert artifact in text

    for invariant in (
        "LIVE_NETWORK_USED=true",
        "DATA_ARTIFACTS_DOWNLOADED=4",
        "TOTAL_BYTES=5066322",
        "CONTAINER_FORMAT=RAR",
        "FORM_101_ARTIFACTS=2",
        "FORM_102_ARTIFACTS=2",
        "MASS_ARCHIVE_CRAWL=false",
        "DATABASE_ACCESSED=false",
    ):
        assert invariant in text


def test_task249_member_inventories_schemas_and_measured_counts_are_frozen() -> None:
    text = _contract()

    for inventory in (
        "101_CURRENT_MEMBERS=072026B1.dbf,072026N1.dbf,NAMES.dbf",
        "101_HISTORICAL_MEMBERS=122020B1.DBF,122020N1.DBF,NAMES.DBF",
        "102_CURRENT_MEMBERS=072026_P1.dbf,072026NP1.dbf,072026SP1.dbf,SPRAV1.dbf,SPRAV11.dbf",
        "102_HISTORICAL_MEMBERS=42020_P1.DBF,42020NP1.DBF,42020SP1.DBF,SPRAV1.DBF",
        "REGN,PLAN,NUM_SC,A_P",
        "REGN,CODE,SIM_R,SIM_V,SIM_ITOGO,DT",
        "CODE_PUBL`/`VALUE_PUBL",
    ):
        assert inventory in text

    for count in (
        "FORM_101_CURRENT_DATA_ROWS=25654",
        "FORM_101_CURRENT_SUBJECTS=353",
        "FORM_101_CURRENT_ACCOUNT_CODES=178",
        "FORM_102_CURRENT_DATA_ROWS=10079",
        "FORM_102_CURRENT_SUBJECTS=212",
        "FORM_102_CURRENT_PUBLISHED_CODES=49",
        "FORM_102_CURRENT_BLANK_TOTAL_ROWS=6",
        "82,698/406/1,224",
        "788,327/406/2,139",
    ):
        assert count in text


def test_task249_form_identity_and_current_disclosure_fail_closed() -> None:
    text = _contract()

    for identity in (
        "101=0409101",
        "102=0409102",
        "101!=0409806",
        "102!=0409807",
    ):
        assert identity in text

    for disclosure in (
        "only `VITG` and `IITG` are\npopulated",
        "`SIM_R` and `SIM_V` are\nblank",
        "SCHEMA_FIELD_EXISTS!=PUBLIC_FIELD_POPULATED",
        "CURRENT_101_DETAIL=FIRST_ORDER_AGGREGATED_BALANCES",
        "CURRENT_102_DETAIL=SECTION_TOTALS_AND_SELECTED_RESULTS",
        "Six blank totals remain missing and never become zero",
    ):
        assert disclosure in text

    assert "first-order aggregated active/passive balances" in text
    assert "49 public rows are explicitly marked by `VALUE_PUBL=1`" in text


def test_task249_identity_units_period_and_pit_contracts_are_explicit() -> None:
    text = _contract()

    for identity in (
        "`REGN` is the primary raw reporting-subject identity",
        "IDENTITY_STATE=IDENTITY_BRIDGE_REQUIRED",
        "TITLE_ONLY_MAPPING_ALLOWED=false",
        "LEGAL_ISSUER_JOIN_EXECUTED=false",
        "CURRENT_MAPPING_STATE=IDENTITY_BRIDGE_REQUIRED",
        "FUZZY_NAME_MATCHING=false",
    ):
        assert identity in text

    for boundary in (
        "missing currency != RUB",
        "missing unit != assumed unit",
        "missing multiplier != 1",
        "missing numeric != zero",
        "PIT_CLASS=PIT_PARTIAL",
        "REPORT_DATE!=PUBLICATION_DATE",
        "HTTP_LAST_MODIFIED!=SOURCE_PUBLICATION_TIME",
        "current archives contain both `20260701` and `20260801`",
    ):
        assert boundary in text


def test_task249_history_failure_states_and_usefulness_matrix_are_complete() -> None:
    text = _contract()

    for history in (
        "PRE_2022_HISTORY=PAGE_LISTED_WITH_2021_FULL_DETAIL_SAMPLE",
        "2022_THROUGH_2023_05=REGULATORY_NON_DISCLOSURE",
        "2023_06_THROUGH_2026=AGGREGATED_DISCLOSURE",
        "REGULATORY_NON_DISCLOSURE!=BANK_DATA_MISSING",
    ):
        assert history in text

    for failure in (
        "ARTIFACT_NOT_FOUND",
        "FORM_NOT_AVAILABLE_FOR_PERIOD",
        "REGULATORY_DISCLOSURE_RESTRICTED",
        "SUBJECT_NOT_DISCLOSED",
        "SOURCE_ERROR",
        "RATE_LIMITED",
        "TIMEOUT",
        "INVALID_ARCHIVE",
        "INVALID_DBF",
        "UNSUPPORTED_SCHEMA_VERSION",
    ):
        assert failure in text

    for status in (
        "`PARTIAL_RAW_SUPPORT`",
        "`AGGREGATED_ONLY`",
        "`STRONG_RAW_SUPPORT`",
        "`NOT_SUPPORTED`",
    ):
        assert status in text

    for domain in (
        "asset composition",
        "cash/liquidity proxy",
        "loan book",
        "securities portfolio",
        "customer funding",
        "interest income/expense",
        "impairment/provisions",
        "net income",
        "capital adequacy",
    ):
        assert domain in text


def test_task249_decisions_123_135_handoff_and_safety_are_unique() -> None:
    text = _contract()

    assert text.count("SOURCE_DECISION=101_102_READY_AS_PART_OF_BANK_BUNDLE") == 1
    assert text.count("ECONOMIC_GATE=BUILD_BANK_REGULATORY_BUNDLE_NOW") == 2
    assert (
        text.count(
            "RECOMMENDED_TASK250=Task250 — CBR Bank Regulatory Raw Bundle "
            "101/102/123/135 Source Contract"
        )
        == 2
    )

    for complement in (
        "FORM_123_PUBLIC=true",
        "FORM_135_PUBLIC=true",
        "FORM_123_135_PARSED=false",
        "FORM_123_135_COMPLEMENT=MATERIALLY_REQUIRED",
    ):
        assert complement in text

    for safety in (
        "RAR_DEPENDENCY_ADDED=false",
        "LIVE_PROBE_IMPLEMENTED=false",
        "RAW_SOURCE_TABLE_CREATED=false",
        "BANK_FINANCIAL_TABLE_CREATED=false",
        "COMPANY_MUTATION_EXECUTED=false",
        "LEGAL_ISSUER_MUTATION_EXECUTED=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "PRODUCTION_ACTIONS=NONE",
        "TASK250_AUTOMATICALLY_STARTED=false",
        "TASK250_IMPLEMENTATION_AUTHORIZED=false",
        "CI=NOT_WAITED_BY_DESIGN",
    ):
        assert safety in text

    assert "The full backend suite is intentionally\nnot run" in text
    assert "Tests read local text only" in text
    assert "perform no network or database operation" in text
