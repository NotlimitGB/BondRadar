from __future__ import annotations

import ast
import json
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.tinvest_bond_admission_manifest import (
    MoexBondResolutionProjection,
    MoexSecurityMatchStatus,
    TInvestAdmissionErrorCode,
    TInvestAdmissionMetadataState,
    TInvestAdmissionReason,
    TInvestAdmissionState,
    TInvestBondAdmissionManifestError,
    TInvestBondAdmissionManifestView,
)
from app.schemas.tinvest_bond_identity_bridge import (
    BondIdentityProjection,
    TInvestBondBridgeMatchState,
    TInvestBridgeAvailabilityClass,
)
from app.schemas.tinvest_instrument_universe import (
    TInvestAvailabilityClass,
    TInvestBondUniverseInstrument,
)
from app.services.tinvest_bond_admission_manifest_service import (
    TInvestBondAdmissionManifestService,
)
from app.services.tinvest_bond_identity_bridge_service import (
    TInvestBondIdentityBridgeService,
)


def source(
    uid: str,
    isin: str | None,
    *,
    sector: Any = "financial",
    currency: Any = "rub",
    bond_type: Any = "BOND_TYPE_CORPORATE",
    country: Any = "RU",
    class_code: str | None = "TQCB",
    api_trade: bool | None = True,
    buy: bool | None = True,
    qual: bool | None = False,
    extras: dict[str, Any] | None = None,
) -> TInvestBondUniverseInstrument:
    raw: dict[str, Any] = {
        "sector": sector,
        "currency": currency,
        "bondType": bond_type,
        "countryOfRisk": country,
        "classCode": class_code,
        "exchange": "MOEX",
        "realExchange": "MOEX_RF",
    }
    if extras:
        raw.update(extras)
    availability = None
    if api_trade is False:
        availability = TInvestAvailabilityClass.API_TRADE_UNAVAILABLE
    elif api_trade is True and buy is True:
        availability = TInvestAvailabilityClass.API_BUY_AVAILABLE
    elif api_trade is True and buy is False:
        availability = TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE
    return TInvestBondUniverseInstrument(
        uid=uid,
        isin=isin,
        class_code=class_code,
        currency=currency if type(currency) is str else None,
        api_trade_available=api_trade,
        buy_available=buy,
        sell_available=True,
        for_qual_investor=qual,
        required_tests=(),
        required_tests_state="SOURCE_EMPTY",
        availability_classification=availability,
        source_fields=raw,
    )


def resolution(
    isin: str,
    *,
    status: MoexSecurityMatchStatus = MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED,
    secid: str | None = "CORP001",
    matched_isin: str | None = None,
    board: str | None = "TQCB",
    candidates: int = 1,
    matched_candidates: int = 1,
) -> MoexBondResolutionProjection:
    return MoexBondResolutionProjection(
        source_isin=isin,
        security_match_status=status,
        matched_secid=secid,
        matched_isin=isin if matched_isin is None and status is MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED else matched_isin,
        candidate_count=candidates,
        matched_candidate_count=matched_candidates,
        primary_board=board,
        issuer_metadata_status="ISSUER_COMPLETE",
        issuer_id="MOEX-ISSUER-1",
        issuer_title="Issuer",
        issuer_inn="7700000000",
        issuer_okpo=None,
    )


def build(
    sources: list[TInvestBondUniverseInstrument],
    internal: list[BondIdentityProjection] | None = None,
    resolutions: list[MoexBondResolutionProjection] | None = None,
    core: list[int] | None = None,
):
    internal = internal or []
    core = core or []
    bridge = TInvestBondIdentityBridgeService.build(sources, internal, core)
    unmatched_isins = sorted(
        {
            row.source_isin
            for row in bridge.bridge_rows
            if row.match_state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
            and row.source_isin is not None
            and row.source_isin.strip()
        }
    )
    if resolutions is None:
        resolutions = [resolution(isin) for isin in unmatched_isins]
    return TInvestBondAdmissionManifestService.build(
        source_bonds=sources,
        identity_bridge=bridge,
        internal_bonds=internal,
        moex_resolutions=resolutions,
        core_m3_complete_bond_ids=core,
    )


def reason_codes(row: Any) -> set[str]:
    return {item.value for item in row.reason_codes}


def test_exact_moex_candidate_and_required_factual_reason() -> None:
    result = build([source("UID-A", "RU000A100AA1")])

    row = result.uid_admissions[0]
    aggregate = result.import_candidate_manifest[0]
    assert row.admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
    assert aggregate.isin == "RU000A100AA1"
    assert aggregate.reason_codes == (TInvestAdmissionReason.MOEX_EXACT_ISIN_RECOVERED,)
    assert TInvestAdmissionReason.SOURCE_COUNTRY_MISSING not in row.reason_codes
    assert result.coverage.import_candidate_uid_count == 1
    assert row.required_tests == ()
    assert row.required_tests_state == "SOURCE_EMPTY"
    assert result.pit_ready is False


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"sector": "government"}, TInvestAdmissionReason.SOURCE_SECTOR_GOVERNMENT),
        ({"sector": "municipal"}, TInvestAdmissionReason.SOURCE_SECTOR_MUNICIPAL),
        ({"currency": "cny"}, TInvestAdmissionReason.CURRENCY_NOT_CURRENTLY_SUPPORTED),
        ({"bond_type": "BOND_TYPE_REPLACED"}, TInvestAdmissionReason.REPLACED_BOND_REVIEW),
        ({"qual": True}, TInvestAdmissionReason.QUAL_RESTRICTED),
        ({"qual": None}, TInvestAdmissionReason.QUAL_UNKNOWN),
        ({"api_trade": True, "buy": False}, TInvestAdmissionReason.API_NOT_BUYABLE),
        ({"api_trade": False, "buy": True}, TInvestAdmissionReason.API_NOT_BUYABLE),
        ({"api_trade": None, "buy": None}, TInvestAdmissionReason.API_AVAILABILITY_UNKNOWN),
    ],
)
def test_non_candidate_source_evidence_requires_review(changes: dict[str, Any], expected_reason: TInvestAdmissionReason) -> None:
    result = build([source("UID-A", "RU000A100AA1", **changes)])
    row = result.uid_admissions[0]
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert expected_reason in row.reason_codes
    assert not result.import_candidate_manifest


def test_canonical_ofz_and_source_government_remain_separate_evidence() -> None:
    ofz = build(
        [source("UID-OFZ", "SU26238RMFS4", sector="government")],
        resolutions=[resolution("SU26238RMFS4", secid="SU26238RMFS4")],
    )
    row = ofz.uid_admissions[0]
    assert row.canonical_ofz is True
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.CANONICAL_OFZ in row.reason_codes
    # Canonical OFZ already explains government review in v1.
    assert TInvestAdmissionReason.SOURCE_SECTOR_GOVERNMENT not in row.reason_codes
    assert ofz.coverage.existing_api_buyable_non_government_unique_bond_count == 0

    government_non_ofz = build(
        [source("UID-GOV", "RU000A100AA2", sector="government")]
    )
    assert government_non_ofz.uid_admissions[0].canonical_ofz is False
    assert TInvestAdmissionReason.SOURCE_SECTOR_GOVERNMENT in government_non_ofz.uid_admissions[0].reason_codes
    assert government_non_ofz.uid_admissions[0].no_government_evidence is None

    su_secid = build(
        [source("UID-SU-SECID", "RU000A100AA8")],
        resolutions=[resolution("RU000A100AA8", secid="SU26238RMFS4")],
    )
    assert su_secid.uid_admissions[0].canonical_ofz is True
    assert su_secid.uid_admissions[0].admission_state is TInvestAdmissionState.REVIEW_REQUIRED


def test_name_and_ticker_do_not_establish_ofz_identity() -> None:
    ordinary = source("UID-NAME", "RU000A100AA7")
    with_name = ordinary.model_copy(
        update={
            "ticker": "SU-NOT-IDENTITY",
            "name": "ОФЗ-ПД name is not canonical identity",
            "source_fields": {
                **ordinary.source_fields,
                "name": "ОФЗ-ПД name is not canonical identity",
            },
        }
    )
    result = build(
        [with_name],
        resolutions=[resolution("RU000A100AA7", secid="RU000A100AA7")],
    )
    assert result.uid_admissions[0].canonical_ofz is False
    assert result.uid_admissions[0].admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE


@pytest.mark.parametrize(
    ("status", "expected_state", "expected_reason"),
    [
        (MoexSecurityMatchStatus.SECURITY_NOT_FOUND, TInvestAdmissionState.MOEX_NOT_RESOLVED, TInvestAdmissionReason.MOEX_NOT_FOUND),
        (MoexSecurityMatchStatus.SOURCE_ERROR, TInvestAdmissionState.MOEX_NOT_RESOLVED, TInvestAdmissionReason.MOEX_SOURCE_ERROR),
        (MoexSecurityMatchStatus.SECURITY_AMBIGUOUS, TInvestAdmissionState.IDENTITY_CONFLICT, TInvestAdmissionReason.MOEX_AMBIGUOUS),
        (MoexSecurityMatchStatus.SECURITY_IDENTIFIER_CONFLICT, TInvestAdmissionState.IDENTITY_CONFLICT, TInvestAdmissionReason.MOEX_IDENTIFIER_CONFLICT),
    ],
)
def test_moex_resolution_states_fail_closed(status: MoexSecurityMatchStatus, expected_state: TInvestAdmissionState, expected_reason: TInvestAdmissionReason) -> None:
    matched_count = 0
    candidates = 2 if status is MoexSecurityMatchStatus.SECURITY_AMBIGUOUS else 1
    result = build(
        [source("UID-A", "RU000A100AA1")],
        resolutions=[
            resolution(
                "RU000A100AA1",
                status=status,
                candidates=candidates,
                matched_candidates=matched_count,
                secid=None,
                matched_isin=None,
            )
        ],
    )
    assert result.unmatched_isin_aggregates[0].admission_state is expected_state
    assert expected_reason in result.unmatched_isin_aggregates[0].reason_codes
    assert not result.import_candidate_manifest


def test_exact_status_with_wrong_isin_is_identity_conflict() -> None:
    result = build(
        [source("UID-A", "RU000A100AA1")],
        resolutions=[resolution("RU000A100AA1", matched_isin="RU000A100ZZ9")],
    )
    assert result.uid_admissions[0].admission_state is TInvestAdmissionState.IDENTITY_CONFLICT
    assert TInvestAdmissionReason.MOEX_IDENTIFIER_CONFLICT in result.uid_admissions[0].reason_codes
    assert not result.import_candidate_manifest


def test_exact_moex_does_not_promote_task296_normalized_only_single_candidate() -> None:
    sources = [source("UID-NORM", "ru000a100aa1")]
    internal = [BondIdentityProjection(bond_id=41, isin="RU000A100AA1", secid="CORP41")]
    result = build(sources, internal=internal, resolutions=[resolution("ru000a100aa1")])
    row = result.uid_admissions[0]
    assert row.task296_match_state is TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE
    assert row.matched_bond_id is None
    assert row.normalized_only_candidate_bond_id == 41
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE in row.reason_codes
    assert not result.import_candidate_manifest


def test_multiple_normalized_only_internal_candidates_are_identity_conflict() -> None:
    sources = [source("UID-NORM", "ru000a100aa1")]
    internal = [
        BondIdentityProjection(bond_id=41, isin="RU000A100AA1", secid="CORP41"),
        BondIdentityProjection(bond_id=42, isin=" RU000A100AA1 ", secid="CORP42"),
    ]
    result = build(sources, internal=internal, resolutions=[resolution("ru000a100aa1")])
    assert result.uid_admissions[0].admission_state is TInvestAdmissionState.IDENTITY_CONFLICT
    assert TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS in result.uid_admissions[0].reason_codes
    assert result.identity_conflict_manifest[0].isin == "ru000a100aa1"


def test_multiple_exact_internal_bonds_are_identity_conflict_not_existing() -> None:
    sources = [source("UID-AMBIG", "RU000A100AA1")]
    internal = [
        BondIdentityProjection(bond_id=41, isin="RU000A100AA1", secid="A"),
        BondIdentityProjection(bond_id=42, isin="RU000A100AA1", secid="B"),
    ]
    result = build(sources, internal=internal, resolutions=[resolution("RU000A100AA1")])
    assert result.uid_admissions[0].admission_state is TInvestAdmissionState.IDENTITY_CONFLICT
    assert result.uid_admissions[0].matched_bond_id is None
    assert TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS in result.uid_admissions[0].reason_codes


def test_many_source_uids_preserve_mapping_but_count_one_existing_bond() -> None:
    sources = [
        source("UID-A", "RU000A100AA1"),
        source("UID-B", "RU000A100AA1", qual=True),
    ]
    result = build(
        sources,
        internal=[BondIdentityProjection(bond_id=7, isin="RU000A100AA1", secid="C7")],
    )
    assert {row.source_uid: row.matched_bond_id for row in result.uid_admissions} == {
        "UID-A": 7,
        "UID-B": 7,
    }
    assert result.coverage.existing_matched_uid_count == 2
    assert result.coverage.existing_unique_bond_count == 1
    assert result.coverage.existing_api_buyable_non_government_unique_bond_count == 1


def test_exact_moex_identity_without_secid_is_review_only() -> None:
    result = build([source("UID-A", "RU000A100AA1")], resolutions=[resolution("RU000A100AA1", secid=None)])
    row = result.uid_admissions[0]
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.MOEX_SECID_MISSING in row.reason_codes


@pytest.mark.parametrize(
    ("board", "expected_reason"),
    [
        ("TQRD", TInvestAdmissionReason.PRIMARY_BOARD_NOT_CURRENTLY_SUPPORTED),
        (None, TInvestAdmissionReason.MOEX_PRIMARY_BOARD_MISSING),
    ],
)
def test_unmatched_pipeline_board_comes_from_moex_and_is_exact(
    board: str | None,
    expected_reason: TInvestAdmissionReason,
) -> None:
    result = build(
        [source("UID-A", "RU000A100AA1", class_code="TQCB")],
        resolutions=[resolution("RU000A100AA1", board=board)],
    )
    row = result.uid_admissions[0]
    assert row.pipeline_board_source.value == "MOEX_PRIMARY_BOARD"
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert expected_reason in row.reason_codes


def test_missing_country_is_nonblocking_but_reported() -> None:
    result = build([source("UID-A", "RU000A100AA1", country=None)])
    row = result.uid_admissions[0]
    assert row.admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
    assert row.country_of_risk_state is TInvestAdmissionMetadataState.NOT_SUPPLIED
    assert TInvestAdmissionReason.SOURCE_COUNTRY_MISSING in row.reason_codes


def test_invalid_optional_source_metadata_is_audited_without_coercion() -> None:
    result = build(
        [source("UID-A", "RU000A100AA1", extras={"sector": 17, "countryOfRisk": False})]
    )
    row = result.uid_admissions[0]
    assert row.source_sector_state is TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE
    assert row.country_of_risk_state is TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE
    assert row.source_sector is None
    assert row.country_of_risk is None
    assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED


def test_blank_hygiene_and_exact_metadata_comparisons() -> None:
    for blank in (None, "", " ", "\t"):
        result = build(
            [source("UID-A", "RU000A100AA1", sector=blank, country=blank, bond_type=blank)],
        )
        row = result.uid_admissions[0]
        assert row.source_sector_state is TInvestAdmissionMetadataState.NOT_SUPPLIED
        assert row.source_sector is None
        assert row.country_of_risk_state is TInvestAdmissionMetadataState.NOT_SUPPLIED
        assert row.bond_type_state is TInvestAdmissionMetadataState.NOT_SUPPLIED
        assert row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
        assert TInvestAdmissionReason.SOURCE_SECTOR_MISSING in row.reason_codes
        assert TInvestAdmissionReason.SOURCE_BOND_TYPE_MISSING in row.reason_codes
        assert TInvestAdmissionReason.SOURCE_COUNTRY_MISSING in row.reason_codes

    padded = build([source("UID-P", "RU000A100AA2", sector=" financial ", currency=" rub ")])
    assert padded.uid_admissions[0].source_sector == " financial "
    assert padded.uid_admissions[0].admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.CURRENCY_NOT_CURRENTLY_SUPPORTED in padded.uid_admissions[0].reason_codes


def test_missing_source_isin_is_unresolved_without_moex_projection() -> None:
    result = build([source("UID-NO-ISIN", None)], resolutions=[])
    row = result.uid_admissions[0]
    assert row.admission_state is TInvestAdmissionState.MOEX_NOT_RESOLVED
    assert row.reason_codes[0] is TInvestAdmissionReason.SOURCE_ISIN_MISSING
    assert result.coverage.moex_not_resolved_unique_isin_count == 0


def test_missing_and_duplicate_moex_resolution_fail_as_typed_errors() -> None:
    src = [source("UID-A", "RU000A100AA1")]
    bridge = TInvestBondIdentityBridgeService.build(src, [], [])
    with pytest.raises(TInvestBondAdmissionManifestError) as missing:
        TInvestBondAdmissionManifestService.build(
            source_bonds=src,
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[],
            core_m3_complete_bond_ids=[],
        )
    assert missing.value.code is TInvestAdmissionErrorCode.MOEX_RESOLUTION_MISSING

    duplicate = resolution("RU000A100AA1")
    with pytest.raises(TInvestBondAdmissionManifestError) as repeated:
        TInvestBondAdmissionManifestService.build(
            source_bonds=src,
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[duplicate, duplicate],
            core_m3_complete_bond_ids=[],
        )
    assert repeated.value.code is TInvestAdmissionErrorCode.MOEX_RESOLUTION_CONFLICT


@pytest.mark.parametrize(
    ("field", "second"),
    [
        ("sector", "government"),
        ("currency", "cny"),
        ("bond_type", "BOND_TYPE_REPLACED"),
    ],
)
def test_critical_source_classification_conflict_blocks_isin(field: str, second: str) -> None:
    first = source("UID-A", "RU000A100AA1")
    other = source("UID-B", "RU000A100AA1", **{field: second})
    result = build([first, other])
    aggregate = result.unmatched_isin_aggregates[0]
    assert aggregate.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT in aggregate.reason_codes
    assert not result.import_candidate_manifest
    assert all(row.admission_state is TInvestAdmissionState.REVIEW_REQUIRED for row in result.uid_admissions)


def test_multi_uid_candidate_uses_any_eligible_uid_but_preserves_uid_status() -> None:
    eligible = source("UID-A", "RU000A100AA1")
    restricted = source("UID-B", "RU000A100AA1", qual=True)
    result = build([restricted, eligible])
    assert result.unmatched_isin_aggregates[0].admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
    by_uid = {row.source_uid: row for row in result.uid_admissions}
    assert by_uid["UID-A"].admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
    assert by_uid["UID-B"].admission_state is TInvestAdmissionState.REVIEW_REQUIRED
    assert TInvestAdmissionReason.QUAL_RESTRICTED in by_uid["UID-B"].reason_codes
    assert result.coverage.import_candidate_uid_count == 2


def test_existing_exact_bond_actionable_coverage_excludes_ofz_and_uses_class_code() -> None:
    sources = [
        source("UID-CORP", "RU000A100AA1", class_code="TQCB"),
        source("UID-OFZ", "SU26238RMFS4", sector="government", class_code="TQCB"),
        source("UID-OTHER", "RU000A100AA3", class_code="TQRD"),
    ]
    internal = [
        BondIdentityProjection(bond_id=1, isin="RU000A100AA1", secid="CORP"),
        BondIdentityProjection(bond_id=2, isin="SU26238RMFS4", secid="SU26238RMFS4"),
        BondIdentityProjection(bond_id=3, isin="RU000A100AA3", secid="CORP3"),
    ]
    result = build(sources, internal=internal, core=[1, 2])
    assert result.coverage.existing_unique_bond_count == 3
    assert result.identity_bridge.coverage.matched_ofz_unique_bond_count == 1
    assert result.coverage.existing_api_buyable_non_government_unique_bond_count == 2
    assert result.coverage.existing_api_buyable_non_government_core_m3_complete_count == 1
    assert result.coverage.existing_api_buyable_non_government_core_coverage_pct == Decimal("50")
    assert result.coverage.existing_current_pipeline_compatible_unique_bond_count == 1
    assert result.coverage.existing_current_pipeline_compatible_core_coverage_pct == Decimal("100")
    assert result.coverage.existing_current_pipeline_missing_bond_type_evidence_count == 0


def test_existing_current_pipeline_uses_task295_class_code_and_missing_type_is_diagnostic() -> None:
    source_row = source("UID-CORP", "RU000A100AA1", class_code="TQCB", bond_type=None)
    result = build(
        [source_row],
        internal=[BondIdentityProjection(bond_id=10, isin="RU000A100AA1", secid="CORP")],
        core=[10],
    )
    assert result.coverage.existing_current_pipeline_compatible_unique_bond_count == 1
    assert result.coverage.existing_current_pipeline_missing_bond_type_evidence_count == 1
    existing = result.existing_bond_aggregates[0]
    uid_row = result.uid_admissions[0]
    assert existing.pipeline_board == "TQCB"
    assert uid_row.pipeline_board_source.value == "TASK295_CLASS_CODE"
    assert uid_row.pipeline_board == "TQCB"
    assert uid_row.primary_board is None


def test_existing_uses_normalized_task295_class_code_even_if_raw_copy_disagrees() -> None:
    src = source("UID-E", "RU000A100AA1", class_code="TQCB")
    contradictory_raw = src.model_copy(
        update={
            "source_fields": {
                **src.source_fields,
                "classCode": "TQRD",
                "currency": "cny",
            }
        }
    )
    result = build(
        [contradictory_raw],
        internal=[BondIdentityProjection(bond_id=1, isin="RU000A100AA1", secid="E")],
    )
    assert result.coverage.existing_current_pipeline_compatible_unique_bond_count == 1
    assert result.uid_admissions[0].class_code == "TQRD"
    assert result.uid_admissions[0].currency == "cny"
    assert result.uid_admissions[0].pipeline_board == "TQCB"


def test_decimal_coverage_and_caller_context_are_isolated() -> None:
    old_prec = getcontext().prec
    old_rounding = getcontext().rounding
    getcontext().prec = 4
    getcontext().rounding = "ROUND_DOWN"
    try:
        sources = [
            source("UID-1", "RU000A100AA1"),
            source("UID-2", "RU000A100AA2"),
            source("UID-3", "RU000A100AA3"),
        ]
        internal = [
            BondIdentityProjection(bond_id=1, isin="RU000A100AA1", secid="A"),
            BondIdentityProjection(bond_id=2, isin="RU000A100AA2", secid="B"),
            BondIdentityProjection(bond_id=3, isin="RU000A100AA3", secid="C"),
        ]
        result = build(sources, internal=internal, core=[1])
        assert result.coverage.existing_api_buyable_non_government_core_coverage_pct == Decimal("33.33333333333333333333333333")
        assert getcontext().prec == 4
        assert getcontext().rounding == "ROUND_DOWN"
    finally:
        getcontext().prec = old_prec
        getcontext().rounding = old_rounding


def test_hashes_and_serialization_are_order_independent_and_inputs_unmodified() -> None:
    source_rows = [source("UID-B", "RU000A100AA2"), source("UID-A", "RU000A100AA1")]
    before = [json.loads(item.model_dump_json()) for item in source_rows]
    first = build(source_rows)
    second = build(list(reversed(source_rows)))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.provenance == second.provenance
    assert [json.loads(item.model_dump_json()) for item in source_rows] == before
    assert len(first.provenance.admission_row_set_sha256) == 64
    assert len(first.provenance.import_candidate_isin_set_sha256) == 64
    with pytest.raises(ValidationError):
        first.coverage.import_candidate_uid_count = 99  # type: ignore[misc]
    with pytest.raises(ValidationError):
        TInvestBondAdmissionManifestView(**first.model_dump(), extra_field=True)


@pytest.mark.parametrize(
    "bad_sources",
    [{"UID-A"}, iter([source("UID-A", "RU000A100AA1")]), "not a source sequence"],
)
def test_nondeterministic_or_wrong_source_containers_fail_closed(bad_sources: Any) -> None:
    valid_sources = [source("UID-A", "RU000A100AA1")]
    bridge = TInvestBondIdentityBridgeService.build(valid_sources, [], [])
    with pytest.raises(TInvestBondAdmissionManifestError):
        TInvestBondAdmissionManifestService.build(
            source_bonds=bad_sources,
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[resolution("RU000A100AA1")],
            core_m3_complete_bond_ids=[],
        )


def test_duplicate_source_uid_and_invalid_core_ids_fail_closed() -> None:
    source_row = source("UID-A", "RU000A100AA1")
    bridge = TInvestBondIdentityBridgeService.build([source_row], [], [])
    with pytest.raises(TInvestBondAdmissionManifestError):
        TInvestBondAdmissionManifestService.build(
            source_bonds=[source_row, source_row],
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[resolution("RU000A100AA1")],
            core_m3_complete_bond_ids=[],
        )

    existing = source("UID-E", "RU000A100AA9")
    internal = [BondIdentityProjection(bond_id=9, isin="RU000A100AA9", secid="E")]
    bridge = TInvestBondIdentityBridgeService.build([existing], internal, [9])
    for core in ([True], [10], [9, 9]):
        with pytest.raises(TInvestBondAdmissionManifestError):
            TInvestBondAdmissionManifestService.build(
                source_bonds=[existing],
                identity_bridge=bridge,
                internal_bonds=internal,
                moex_resolutions=[],
                core_m3_complete_bond_ids=core,
            )


def test_bridge_identity_or_provenance_tampering_is_rejected() -> None:
    sources = [source("UID-A", "RU000A100AA1")]
    bridge = TInvestBondIdentityBridgeService.build(sources, [], [])
    broken = bridge.model_copy(
        update={
            "provenance": bridge.provenance.model_copy(
                update={"source_uid_count": 42}
            )
        }
    )
    with pytest.raises(TInvestBondAdmissionManifestError) as error:
        TInvestBondAdmissionManifestService.build(
            source_bonds=sources,
            identity_bridge=broken,
            internal_bonds=[],
            moex_resolutions=[resolution("RU000A100AA1")],
            core_m3_complete_bond_ids=[],
        )
    assert error.value.code is TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID


def test_bridge_matched_bond_must_match_supplied_internal_projection() -> None:
    src = source("UID-E", "RU000A100AA1")
    original = [BondIdentityProjection(bond_id=1, isin="RU000A100AA1", secid="SECID-1")]
    bridge = TInvestBondIdentityBridgeService.build([src], original, [])
    changed = [BondIdentityProjection(bond_id=1, isin="RU000A100ZZ9", secid="SECID-1")]
    with pytest.raises(TInvestBondAdmissionManifestError) as error:
        TInvestBondAdmissionManifestService.build(
            source_bonds=[src],
            identity_bridge=bridge,
            internal_bonds=changed,
            moex_resolutions=[],
            core_m3_complete_bond_ids=[],
        )
    assert error.value.code is TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID


def test_invalid_availability_envelope_is_rejected_before_admission() -> None:
    src = source("UID-A", "RU000A100AA1")
    invalid = src.model_copy(
        update={"availability_classification": TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE}
    )
    bridge = TInvestBondIdentityBridgeService.build([src], [], [])
    with pytest.raises(TInvestBondAdmissionManifestError) as error:
        TInvestBondAdmissionManifestService.build(
            source_bonds=[invalid],
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[resolution("RU000A100AA1")],
            core_m3_complete_bond_ids=[],
        )
    assert error.value.code is TInvestAdmissionErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    ("required_tests", "required_tests_state"),
    [
        (None, "SOURCE_EMPTY"),
        ((), "NOT_SUPPLIED"),
        (("exam",), "SOURCE_EMPTY"),
        (("exam",), "NOT_SUPPLIED"),
    ],
)
def test_required_tests_value_and_state_must_agree(
    required_tests: tuple[str, ...] | None,
    required_tests_state: str,
) -> None:
    valid = source("UID-A", "RU000A100AA1")
    invalid = valid.model_copy(
        update={
            "required_tests": required_tests,
            "required_tests_state": required_tests_state,
        }
    )
    bridge = TInvestBondIdentityBridgeService.build([invalid], [], [])
    with pytest.raises(TInvestBondAdmissionManifestError) as error:
        TInvestBondAdmissionManifestService.build(
            source_bonds=[invalid],
            identity_bridge=bridge,
            internal_bonds=[],
            moex_resolutions=[resolution("RU000A100AA1")],
            core_m3_complete_bond_ids=[],
        )
    assert error.value.code is TInvestAdmissionErrorCode.INVALID_INPUT


def test_metadata_breakdown_fields_are_unique_and_deterministic() -> None:
    result = build([source("UID-A", "RU000A100AA1")])
    fields = tuple(item.field for item in result.metadata_breakdowns)
    assert fields == (
        "sector",
        "countryOfRisk",
        "countryOfRiskName",
        "bondType",
        "exchange",
        "realExchange",
        "class_code",
        "currency",
        "primary_board",
    )
    assert len(fields) == len(set(fields))


def test_moex_projection_is_strict_and_status_is_typed() -> None:
    valid = resolution("RU000A100AA1")
    with pytest.raises(ValidationError):
        MoexBondResolutionProjection(**valid.model_dump(), extra_field=True)
    with pytest.raises(ValidationError):
        MoexBondResolutionProjection(
            source_isin="RU000A100AA1",
            security_match_status="UNKNOWN_STATUS",
            candidate_count=1,
            matched_candidate_count=0,
        )


def test_runtime_service_static_boundary() -> None:
    service_path = Path(__file__).resolve().parents[1] / "app" / "services" / "tinvest_bond_admission_manifest_service.py"
    tree = ast.parse(service_path.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not ({"sqlalchemy", "httpx", "requests", "urllib", "os", "pathlib"} & imports)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    forbidden = {
        "MoexIssClient",
        "TInvestInstrumentUniverseClient",
        "M3AuditSnapshotService",
        "M3CoverageAuditReducer",
        "flush",
        "commit",
        "write_text",
        "open",
        "environ",
    }
    assert not forbidden.intersection(names | attributes)
    assert "is_corporate" not in names | attributes
