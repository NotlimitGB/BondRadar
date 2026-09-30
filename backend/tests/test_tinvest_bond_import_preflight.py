from __future__ import annotations

import ast
import json
from datetime import date, datetime
from decimal import Decimal, getcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.tinvest_bond_admission_manifest import (
    MoexBondResolutionProjection,
    MoexSecurityMatchStatus,
    TInvestAdmissionReason,
    TInvestAdmissionState,
    TInvestBondAdmissionManifestView,
)
from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection
from app.schemas.tinvest_bond_import_preflight import (
    BondImportCompanyPlan,
    BondImportPreflightReason,
    BondImportPreflightState,
    CompanyIdentityProjection,
    MoexBondImportDescriptionProjection,
    MoexBondImportIssuerEvidence,
    MoexBondImportBoardObservation,
    MoexBondImportBoardCandidateEvidence,
    MoexBondImportCandidateEvidence,
    MoexBondImportEvidenceBatch,
    TInvestBondImportPreflightError,
    TInvestBondImportPreflightErrorCode,
    TInvestBondImportPreflightView,
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
from app.services.tinvest_bond_import_preflight_service import (
    TInvestBondImportPreflightService,
)


def source(
    uid: str,
    isin: str,
    *,
    qual: bool | None = False,
    buy: bool | None = True,
    required_tests: tuple[str, ...] | None = (),
    currency: str = "rub",
    sector: str = "financial",
    bond_type: str = "BOND_TYPE_CORPORATE",
) -> TInvestBondUniverseInstrument:
    availability = (
        TInvestAvailabilityClass.API_BUY_AVAILABLE
        if buy is True
        else TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE
        if buy is False
        else None
    )
    return TInvestBondUniverseInstrument(
        uid=uid,
        isin=isin,
        class_code="TQCB",
        currency=currency,
        buy_available=buy,
        sell_available=True,
        api_trade_available=True if buy is not None else None,
        for_qual_investor=qual,
        required_tests=required_tests,
        required_tests_state=(
            "NOT_SUPPLIED" if required_tests is None else "SOURCE_EMPTY" if not required_tests else "SOURCE_VALUES"
        ),
        availability_classification=availability,
        source_fields={
            "sector": sector,
            "currency": currency,
            "bondType": bond_type,
            "countryOfRisk": "RU",
            "classCode": "TQCB",
        },
    )


def resolution(isin: str, secid: str | None = None) -> MoexBondResolutionProjection:
    resolved_secid = secid or f"SEC-{isin[-4:]}"
    return MoexBondResolutionProjection(
        source_isin=isin,
        security_match_status=MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED,
        matched_secid=resolved_secid,
        matched_isin=isin,
        candidate_count=1,
        matched_candidate_count=1,
        primary_board="TQCB",
        issuer_metadata_status="ISSUER_COMPLETE",
        issuer_id="MOEX-ISSUER-1",
        issuer_title="Issuer",
        issuer_inn="7700000000",
    )


def manifest(
    sources: list[TInvestBondUniverseInstrument] | None = None,
    *,
    internal: list[BondIdentityProjection] | None = None,
) -> TInvestBondAdmissionManifestView:
    source_rows = [source("UID-1", "RU000A100AA1")] if sources is None else sources
    internal_rows = internal or []
    bridge = TInvestBondIdentityBridgeService.build(source_rows, internal_rows, [])
    unmatched_isins = sorted(
        {
            row.source_isin
            for row in bridge.bridge_rows
            if row.source_isin is not None
        }
    )
    resolutions = [resolution(isin) for isin in unmatched_isins]
    return TInvestBondAdmissionManifestService.build(
        source_bonds=source_rows,
        identity_bridge=bridge,
        internal_bonds=internal_rows,
        moex_resolutions=resolutions,
        core_m3_complete_bond_ids=[],
    )


def description(
    candidate,
    **overrides,
) -> MoexBondImportDescriptionProjection:
    values = {
        "requested_secid": candidate.matched_secid,
        "requested_board": None,
        "secid": candidate.matched_secid,
        "isin": candidate.isin,
        "name": "Issuer 2030 bond",
        "issuer_name": None,
        "issuer_inn": None,
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "coupon_rate": None,
        "maturity_date": "2030-07-15",
        "offer_date": None,
        "is_subordinated": False,
        "is_perpetual": False,
        "has_amortization": None,
        "status": None,
        "is_traded": None,
        "board_observed": None,
        "primary_board": None,
        "raw_structural_fields": (("BONDTYPE", "CORPORATE"),),
    }
    values.update(overrides)
    return MoexBondImportDescriptionProjection(**values)


def _run_preflight(
    *,
    admission_manifest,
    moex_descriptions,
    internal_bonds,
    company_projections,
    issuer_overrides=None,
    board_overrides=None,
    board_scan_status="COMPLETE",
):
    """Split old combined fixtures into truthful, independent source DTOs."""
    if not isinstance(moex_descriptions, (list, tuple)):
        return TInvestBondImportPreflightService.build(
            admission_manifest=admission_manifest,
            moex_evidence=moex_descriptions,
            internal_bonds=internal_bonds,
            company_projections=company_projections,
        )
    TInvestBondImportPreflightService.validate_admission_manifest(admission_manifest)
    candidates = admission_manifest.import_candidate_manifest
    by_request = {}
    for projection in moex_descriptions:
        by_request.setdefault(getattr(projection, "requested_secid", None), []).append(projection)
    bundles = []
    issuer_overrides = issuer_overrides or {}
    board_overrides = board_overrides or {}
    source_query_count = 0
    board_rows = 0
    for candidate in candidates:
        issuer_values = {
            "matched_secid": candidate.matched_secid,
            "matched_isin": candidate.isin,
            "security_match_status": "EXACT_SECID_ISIN_CORROBORATED",
            "issuer_metadata_status": "ISSUER_COMPLETE",
            "issuer_title": "Issuer Ltd",
            "issuer_inn": "7700000000",
            "primary_board": "TQCB",
            "source_query_count": 1,
        }
        issuer_values.update(issuer_overrides.get(candidate.isin, {}))
        issuer = MoexBondImportIssuerEvidence(
            requested_secid=candidate.matched_secid,
            expected_isin=candidate.isin,
            **issuer_values,
        )
        board_values = {
            "scan_status": board_scan_status,
            "observations": (
                MoexBondImportBoardObservation(
                    secid=candidate.matched_secid,
                    isin=candidate.isin,
                    is_traded=None,
                    status=None,
                ),
            ),
        }
        board_values.update(board_overrides.get(candidate.isin, {}))
        board = MoexBondImportBoardCandidateEvidence(
            requested_board="TQCB",
            warning_count=1 if board_scan_status != "COMPLETE" else 0,
            **board_values,
        )
        projections = tuple(by_request.get(candidate.matched_secid, ()))
        bundles.append(
            MoexBondImportCandidateEvidence(
                candidate_secid=candidate.matched_secid,
                candidate_isin=candidate.isin,
                issuer=issuer,
                board=board,
                description_status="OBSERVED" if projections else "MISSING",
                descriptions=projections,
            )
        )
        source_query_count += issuer.source_query_count
        board_rows += len(board.observations)
    expected_secids = {item.matched_secid for item in candidates}
    unrequested = tuple(
        item
        for item in moex_descriptions
        if getattr(item, "requested_secid", None) not in expected_secids
    )
    if unrequested and bundles:
        first = bundles[0]
        bundles[0] = first.model_copy(
            update={
                "descriptions": (*first.descriptions, *unrequested),
                "description_status": "OBSERVED",
            }
        )
    evidence_batch = MoexBondImportEvidenceBatch(
        candidate_evidence=tuple(bundles),
        candidate_isins=tuple(item.isin for item in candidates),
        candidate_secids=tuple(item.matched_secid for item in candidates),
        board_scan_status=board_scan_status,
        board_scan_pages_fetched=1 if candidates else 0,
        board_scan_rows_fetched=board_rows,
        board_scan_warning_count=1 if board_scan_status != "COMPLETE" else 0,
        issuer_lookup_count=len(candidates),
        issuer_source_query_count=source_query_count,
        description_lookup_count=len(candidates),
    )
    return TInvestBondImportPreflightService.build(
        admission_manifest=admission_manifest,
        moex_evidence=evidence_batch,
        internal_bonds=internal_bonds,
        company_projections=company_projections,
    )


def build(
    *,
    source_rows: list[TInvestBondUniverseInstrument] | None = None,
    descriptions: list[MoexBondImportDescriptionProjection] | None = None,
    internal: list[BondIdentityProjection] | None = None,
    companies: list[CompanyIdentityProjection] | None = None,
):
    batch = manifest(source_rows, internal=internal)
    if descriptions is None:
        descriptions = [description(item) for item in batch.import_candidate_manifest]
    return _run_preflight(
        admission_manifest=batch,
        moex_descriptions=descriptions,
        internal_bonds=internal or [],
        company_projections=companies or [],
    )


def codes(row) -> set[str]:
    return {item.value for item in row.reason_codes}


def test_exact_candidate_is_ready_and_unknown_activity_is_diagnostic() -> None:
    result = build()
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert BondImportPreflightReason.MOEX_DESCRIPTION_EXACT_IDENTITY in row.reason_codes
    assert BondImportPreflightReason.ACTIVE_STATUS_UNKNOWN in row.reason_codes
    assert row.active_status_unknown is True
    assert row.is_traded is None
    assert result.summary.ready_count == 1
    assert result.summary.active_unknown_count == 1
    assert result.ready_for_import_manifest[0].nominal_value == Decimal("1000.00")
    assert result.ready_for_import_manifest[0].currency == "RUB"
    assert result.ready_for_import_manifest[0].is_perpetual is False
    assert row.moex_description is not None
    assert row.moex_description.raw_structural_fields == (("BONDTYPE", "CORPORATE"),)
    assert result.capabilities.pit_ready is False
    assert result.capabilities.import_ready_means_m3_ready is False


def test_company_resolution_uses_exact_inn_then_unique_normalized_name() -> None:
    by_inn = CompanyIdentityProjection(
        company_id=10, name="Issuer Ltd", ticker="ISS", inn="7700000000"
    )
    result = build(companies=[by_inn])
    row = result.candidate_rows[0]
    assert row.company_plan is BondImportCompanyPlan.USE_EXISTING_BY_INN
    assert row.company_id == 10
    assert BondImportPreflightReason.COMPANY_EXISTING_BY_INN in row.reason_codes

    by_name = CompanyIdentityProjection(
        company_id=11, name="  ISSUER   Ltd ", ticker="OLD", inn="7700000001"
    )
    result = build(companies=[by_name])
    assert result.candidate_rows[0].company_plan is BondImportCompanyPlan.USE_EXISTING_BY_NAME
    assert result.candidate_rows[0].company_id == 11

    result = build()
    assert result.candidate_rows[0].company_plan is BondImportCompanyPlan.CREATE_NEW
    assert result.summary.new_company_count == 1

    ambiguous_names = [
        CompanyIdentityProjection(company_id=21, name="Issuer Ltd", ticker="A", inn=None),
        CompanyIdentityProjection(company_id=22, name=" issuer   LTD ", ticker="B", inn=None),
    ]
    ambiguous = build(companies=ambiguous_names)
    assert ambiguous.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert ambiguous.candidate_rows[0].company_plan is BondImportCompanyPlan.COMPANY_IDENTITY_CONFLICT


def test_inn_and_name_resolving_to_distinct_companies_requires_review() -> None:
    result = build(
        companies=[
            CompanyIdentityProjection(company_id=1, name="Other", ticker="A", inn="7700000000"),
            CompanyIdentityProjection(company_id=2, name="Issuer Ltd", ticker="B", inn="7700000002"),
        ]
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.REVIEW_REQUIRED
    assert row.company_plan is BondImportCompanyPlan.COMPANY_IDENTITY_CONFLICT
    assert BondImportPreflightReason.COMPANY_IDENTITY_CONFLICT in row.reason_codes
    assert result.summary.new_company_count == 0


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"nominal_value": Decimal("0")}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"nominal_value": Decimal("-1")}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"nominal_value": "NaN"}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"nominal_value": "Infinity"}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"nominal_value": True}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"nominal_value": "bad"}, BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID),
        ({"currency": "USD"}, BondImportPreflightReason.NOMINAL_CURRENCY_CONFLICT),
        ({"currency": " "}, BondImportPreflightReason.NOMINAL_CURRENCY_CONFLICT),
        ({"maturity_date": None, "is_perpetual": False}, BondImportPreflightReason.MATURITY_STRUCTURE_UNRESOLVED),
        ({"name": None}, BondImportPreflightReason.BOND_NAME_MISSING),
    ],
)
def test_missing_or_invalid_required_evidence_is_review_only(overrides, expected) -> None:
    batch = manifest()
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(batch.import_candidate_manifest[0], **overrides)],
        internal_bonds=[],
        company_projections=[],
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.REVIEW_REQUIRED
    assert expected.value in codes(row)


def test_perpetual_without_maturity_is_allowed_and_coupon_is_not_required() -> None:
    batch = manifest()
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[
            description(
                batch.import_candidate_manifest[0],
                maturity_date=None,
                is_perpetual=True,
                coupon_rate=None,
                has_amortization=None,
            )
        ],
        internal_bonds=[],
        company_projections=[],
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert row.maturity_date is None
    assert row.is_perpetual is True
    assert row.coupon_rate is None
    assert row.has_amortization is None
    assert result.summary.perpetual_count == 1
    assert result.summary.missing_maturity_count == 1
    assert result.summary.missing_maturity_count == 1


def test_maturity_dates_and_inn_follow_importer_formatting_without_extra_inference() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    valid = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[
            description(
                candidate,
                issuer_inn=" AB-123 ",
                maturity_date=date(2032, 4, 5),
                is_perpetual=None,
                currency="SUR",
            )
        ],
        internal_bonds=[],
        company_projections=[],
        issuer_overrides={
            batch.import_candidate_manifest[0].isin: {"issuer_inn": " AB-123 "}
        },
    )
    assert valid.candidate_rows[0].status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert valid.candidate_rows[0].issuer_inn == "AB-123"
    assert valid.candidate_rows[0].maturity_date.isoformat() == "2032-04-05"
    assert valid.candidate_rows[0].canonical_currency == "RUB"

    with pytest.raises(ValidationError):
        description(candidate, maturity_date=datetime(2032, 4, 5, 12, 0))


def test_description_cannot_supply_issuer_or_board_evidence() -> None:
    batch = manifest()
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[
            description(
                batch.import_candidate_manifest[0],
                issuer_name="Forged issuer",
                issuer_inn="BAD-INN",
                requested_board="TQCB",
                board_observed=True,
                primary_board="TQRD",
            )
        ],
        internal_bonds=[],
        company_projections=[],
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert row.issuer_name == "Issuer Ltd"
    assert row.issuer_inn == "7700000000"
    assert row.primary_board == "TQCB"
    assert row.board_observed is True
    assert row.moex_description is not None
    assert row.moex_description.issuer_name == "Forged issuer"
    assert row.moex_description.primary_board == "TQRD"
    assert result.provenance.board_ready_requires_primary_tqcb is True
    assert result.provenance.board_ready_requires_board_observed is True


def test_issuer_reference_missing_fields_and_source_failures_are_review_only() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    cases = (
        ({"security_match_status": "SECURITY_NOT_FOUND", "matched_secid": None, "matched_isin": None,
          "issuer_title": None, "issuer_inn": None, "primary_board": None}, "ISSUER_REFERENCE_MISSING"),
        ({"issuer_title": None}, "ISSUER_NAME_MISSING"),
        ({"issuer_inn": None}, "ISSUER_INN_MISSING_OR_INVALID"),
        ({"issuer_inn": "x" * 17}, "ISSUER_INN_MISSING_OR_INVALID"),
        ({"primary_board": None}, "PRIMARY_BOARD_MISSING"),
        ({"primary_board": "TQRD"}, "PRIMARY_BOARD_MISMATCH"),
        ({"primary_board": " TQCB "}, "PRIMARY_BOARD_MISMATCH"),
        ({"security_match_status": "SOURCE_ERROR", "matched_secid": None, "matched_isin": None,
          "issuer_title": None, "issuer_inn": None, "primary_board": None}, "ISSUER_REFERENCE_SOURCE_ERROR"),
    )
    for override, expected in cases:
        result = _run_preflight(
            admission_manifest=batch,
            moex_descriptions=[description(candidate)],
            internal_bonds=[],
            company_projections=[],
            issuer_overrides={candidate.isin: override},
        )
        assert result.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
        assert expected in codes(result.candidate_rows[0])


def test_board_membership_requires_complete_exact_tqcb_evidence() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    absent = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate)],
        internal_bonds=[],
        company_projections=[],
        board_overrides={candidate.isin: {"observations": ()}},
    )
    assert absent.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert "BOARD_OBSERVATION_MISSING" in codes(absent.candidate_rows[0])

    mismatched = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate)],
        internal_bonds=[],
        company_projections=[],
        board_overrides={
            candidate.isin: {
                "observations": (
                    MoexBondImportBoardObservation(
                        secid=candidate.matched_secid, isin="OTHER-ISIN"
                    ),
                )
            }
        },
    )
    assert mismatched.candidate_rows[0].status is BondImportPreflightState.IDENTITY_CONFLICT

    incomplete = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate)],
        internal_bonds=[],
        company_projections=[],
        board_scan_status="INCOMPLETE",
    )
    assert incomplete.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert "BOARD_SCAN_INCOMPLETE" in codes(incomplete.candidate_rows[0])
    assert incomplete.candidate_rows[0].board_observed is True


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"is_traded": False}, "NOT_ACTIVE_OR_NOT_TRADED"),
        ({"is_traded": None, "status": "delisted"}, "NOT_ACTIVE_OR_NOT_TRADED"),
        ({"is_traded": None, "status": "inactive"}, "NOT_ACTIVE_OR_NOT_TRADED"),
    ],
)
def test_explicit_inactive_evidence_blocks_but_traded_true_is_not_required(overrides, expected) -> None:
    batch = manifest()
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(batch.import_candidate_manifest[0], **overrides)],
        internal_bonds=[],
        company_projections=[],
    )
    assert result.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert expected in codes(result.candidate_rows[0])

    active = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(batch.import_candidate_manifest[0], is_traded=True)],
        internal_bonds=[],
        company_projections=[],
    )
    assert active.candidate_rows[0].status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert "ACTIVE_STATUS_UNKNOWN" not in codes(active.candidate_rows[0])


@pytest.mark.parametrize("identifier", ["isin", "secid"])
def test_exact_description_identity_mismatch_is_identity_conflict(identifier) -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    override = {identifier: "OTHER"}
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate, **override)],
        internal_bonds=[],
        company_projections=[],
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.IDENTITY_CONFLICT
    assert BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT in row.reason_codes
    assert result.ready_for_import_manifest == ()


def test_internal_bond_collision_and_cross_identifier_conflict() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    collision = BondIdentityProjection(
        bond_id=15, isin="ANOTHER-ISIN", secid=candidate.matched_secid
    )
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate)],
        internal_bonds=[collision],
        company_projections=[],
    )
    assert result.candidate_rows[0].status is BondImportPreflightState.ALREADY_IMPORTED_OR_COLLISION
    assert "ALREADY_IMPORTED_OR_COLLISION" in codes(result.candidate_rows[0])

    conflict = [
        BondIdentityProjection(bond_id=15, isin=candidate.isin, secid="OLD-SECID"),
        BondIdentityProjection(bond_id=16, isin="OTHER", secid=candidate.matched_secid),
    ]
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(candidate)],
        internal_bonds=conflict,
        company_projections=[],
    )
    assert result.candidate_rows[0].status is BondImportPreflightState.IDENTITY_CONFLICT
    assert "INTERNAL_IDENTITY_CONFLICT" in codes(result.candidate_rows[0])


def test_missing_and_duplicate_descriptions_remain_visible_per_candidate() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    missing = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[],
        internal_bonds=[],
        company_projections=[],
    )
    assert missing.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert "DESCRIPTION_MISSING" in codes(missing.candidate_rows[0])

    duplicate_projection = description(candidate)
    duplicate = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[duplicate_projection, duplicate_projection],
        internal_bonds=[],
        company_projections=[],
    )
    assert duplicate.candidate_rows[0].status is BondImportPreflightState.REVIEW_REQUIRED
    assert "DESCRIPTION_CONFLICT" in codes(duplicate.candidate_rows[0])
    assert duplicate.provenance.description_projection_count == 2


def test_task295_required_tests_are_preserved_but_do_not_gate_import() -> None:
    evidence = source("UID-TESTS", "RU000A100AA1", required_tests=("bond", "russian_bonds_foreign_law"))
    batch = manifest([evidence])
    result = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=[description(batch.import_candidate_manifest[0], name="СФО Кредитный поток")],
        internal_bonds=[],
        company_projections=[],
    )
    row = result.candidate_rows[0]
    assert row.status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
    assert row.uid_admissions[0].required_tests == ("bond", "russian_bonds_foreign_law")
    assert row.required_tests == ("bond", "russian_bonds_foreign_law")
    assert result.ready_for_import_manifest[0].required_tests == ("bond", "russian_bonds_foreign_law")
    assert row.name == "СФО Кредитный поток"


def test_hashes_and_serialization_are_order_independent_and_inputs_are_immutable() -> None:
    sources = [
        source("UID-B", "RU000A100AA2", required_tests=("exam",)),
        source("UID-A", "RU000A100AA1", required_tests=()),
    ]
    batch = manifest(sources)
    projections = [description(item) for item in batch.import_candidate_manifest]
    before_manifest = batch.model_dump(mode="json")
    before_projections = [item.model_dump(mode="json") for item in projections]
    first = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=projections,
        internal_bonds=[],
        company_projections=[],
    )
    second = _run_preflight(
        admission_manifest=batch,
        moex_descriptions=list(reversed(projections)),
        internal_bonds=[],
        company_projections=[],
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert len(first.candidate_isin_set_sha256) == 64
    assert len(first.candidate_secid_set_sha256) == 64
    assert len(first.candidate_row_set_sha256) == 64
    assert len(first.ready_for_import_manifest_sha256) == 64
    assert len(first.review_manifest_sha256) == 64
    assert batch.model_dump(mode="json") == before_manifest
    assert [item.model_dump(mode="json") for item in projections] == before_projections


def test_decimal_and_boolean_values_are_not_coerced_or_context_dependent() -> None:
    batch = manifest()
    old_prec = getcontext().prec
    old_rounding = getcontext().rounding
    getcontext().prec = 5
    getcontext().rounding = "ROUND_DOWN"
    try:
        result = _run_preflight(
            admission_manifest=batch,
            moex_descriptions=[description(batch.import_candidate_manifest[0], nominal_value="1250,50")],
            internal_bonds=[],
            company_projections=[],
        )
        assert result.candidate_rows[0].nominal_value == Decimal("1250.50")
        assert getcontext().prec == 5
        assert getcontext().rounding == "ROUND_DOWN"
    finally:
        getcontext().prec = old_prec
        getcontext().rounding = old_rounding


def test_input_containers_manifest_and_projection_contracts_are_strict() -> None:
    batch = manifest()
    candidate = batch.import_candidate_manifest[0]
    valid_projection = description(candidate)
    for invalid in ({valid_projection}, iter([valid_projection]), "text", {"x": valid_projection}):
        with pytest.raises(TInvestBondImportPreflightError):
            _run_preflight(
                admission_manifest=batch,
                moex_descriptions=invalid,
                internal_bonds=[],
                company_projections=[],
            )
    with pytest.raises(TInvestBondImportPreflightError):
        _run_preflight(
            admission_manifest=object(),
            moex_descriptions=[valid_projection],
            internal_bonds=[],
            company_projections=[],
        )
    unknown_projection = description(candidate, requested_secid="UNREQUESTED")
    with pytest.raises(TInvestBondImportPreflightError) as error:
        _run_preflight(
            admission_manifest=batch,
            moex_descriptions=[unknown_projection],
            internal_bonds=[],
            company_projections=[],
        )
    assert error.value.code is TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID
    with pytest.raises(ValidationError):
        MoexBondImportDescriptionProjection(**valid_projection.model_dump(), extra_field=True)
    with pytest.raises(ValidationError):
        valid_projection.name = "mutated"  # type: ignore[misc]
    output = build()
    with pytest.raises(ValidationError):
        TInvestBondImportPreflightView(**output.model_dump(), extra_field=True)


def test_manifest_hash_drift_and_duplicate_internal_ids_fail_closed() -> None:
    batch = manifest()
    broken = batch.model_copy(
        update={
            "provenance": batch.provenance.model_copy(
                update={"import_candidate_isin_set_sha256": "0" * 64}
            )
        }
    )
    with pytest.raises(TInvestBondImportPreflightError) as error:
        _run_preflight(
            admission_manifest=broken,
            moex_descriptions=[description(batch.import_candidate_manifest[0])],
            internal_bonds=[],
            company_projections=[],
        )
    assert error.value.code is TInvestBondImportPreflightErrorCode.CANDIDATE_HASH_MISMATCH

    duplicate_id = BondIdentityProjection(bond_id=2, isin="A", secid="B")
    with pytest.raises(TInvestBondImportPreflightError) as error:
        _run_preflight(
            admission_manifest=batch,
            moex_descriptions=[description(batch.import_candidate_manifest[0])],
            internal_bonds=[duplicate_id, duplicate_id],
            company_projections=[],
        )
    assert error.value.code is TInvestBondImportPreflightErrorCode.INTERNAL_IDENTITY_PROJECTION_INVALID

    wrong_version = batch.model_copy(update={"contract_version": "other"})
    for broken in (
        wrong_version,
        batch.model_copy(update={"pit_ready": True}),
    ):
        with pytest.raises(TInvestBondImportPreflightError) as error:
            _run_preflight(
                admission_manifest=broken,
                moex_descriptions=[description(batch.import_candidate_manifest[0])],
                internal_bonds=[],
                company_projections=[],
            )
        assert error.value.code is TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID


def test_empty_candidate_manifest_is_stable_and_hashable() -> None:
    result = build(source_rows=[])
    assert result.candidate_rows == ()
    assert result.ready_for_import_manifest == ()
    assert result.review_manifest == ()
    assert result.summary.candidate_count == 0
    assert result.summary.ready_count == 0
    assert result.candidate_isin_set_sha256 == result.identity.import_candidate_isin_set_sha256


def test_runtime_service_static_boundary() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "services" / "tinvest_bond_import_preflight_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
        "Session", "commit", "flush", "MoexBondUniverseService", "MoexIssClient",
        "TInvestInstrumentUniverseClient", "sync", "rollback", "environ",
    }
    assert not forbidden.intersection(names | attributes)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert not (
                node.func.attr in {"add", "flush", "commit", "rollback"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"db", "session"}
            )
    assert "is_corporate" not in names | attributes
