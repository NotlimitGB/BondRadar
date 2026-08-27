from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.services.bond_security_master_service import (
    BondSecurityMasterService,
    execution_terms_blockers,
    plain_vanilla_strategy_blockers,
    research_terms_blockers,
)
from app.services.moex_iss_client import MoexCashflowScheduleResult


OBSERVED = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def create_bond(db: Session, suffix: str = "1") -> Bond:
    company = Company(
        name=f"Security Master Issuer {suffix}",
        ticker=f"SM{suffix}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    bond = Bond(
        company_id=company.id,
        isin=f"RUSM{suffix:0>8}"[:12],
        secid=f"SM{suffix}",
        name=f"Security Master Bond {suffix}",
        currency="RUB",
        nominal_value=Decimal("1000"),
        maturity_date=date(2035, 1, 1),
        is_floating_coupon=False,
        is_subordinated=False,
        is_perpetual=False,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


@pytest.mark.parametrize(
    "profile_values",
    [
        {"currency_state": "invalid"},
        {"currency_state": "unknown", "currency_code": "RUB"},
        {"currency_state": "verified", "currency_code": None},
        {"currency_state": "conflict", "currency_code": "RUB"},
        {"nominal_state": "verified", "nominal_value": Decimal("0")},
        {"lot_size_state": "verified", "lot_size": 0},
        {"coupon_frequency_state": "verified", "coupon_frequency_per_year": 0},
        {"outstanding_nominal_state": "verified", "outstanding_nominal": Decimal("0")},
    ],
)
def test_profile_database_constraints_fail_closed(
    db_session: Session, profile_values: dict
) -> None:
    bond = create_bond(db_session)
    db_session.add(BondSecurityMasterProfile(bond_id=bond.id, **profile_values))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_one_profile_per_bond_and_bond_delete_cascades(
    db_session: Session,
) -> None:
    bond = create_bond(db_session)
    service = BondSecurityMasterService(db_session)
    service.ingest_moex_metadata(
        bond,
        {"raw": {"FACEUNIT": "RUB"}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    db_session.commit()
    db_session.add(BondSecurityMasterProfile(bond_id=bond.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    bond = db_session.get(Bond, bond.id)
    assert bond is not None
    assert len(bond.security_master_evidence) == 1
    assert bond.security_master_profile is not None
    db_session.delete(bond)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(BondSecurityMasterProfile)) == 0
    assert db_session.scalar(select(func.count()).select_from(BondSecurityMasterEvidence)) == 0


def test_missing_source_flags_remain_unknown_and_explicit_flags_resolve(
    db_session: Session,
) -> None:
    missing = create_bond(db_session, "11")
    service = BondSecurityMasterService(db_session)
    profile = service.ingest_moex_metadata(
        missing,
        {
            "raw": {
                "FACEUNIT": "SUR",
                "nominal_value": "1000",
                "coupon_rate": "0",
                "maturity_date": "2035-01-01",
            }
        },
        source="moex_description",
        board="TQCB",
        board_observed=True,
        observed_at=OBSERVED,
    )
    assert profile is not None
    assert profile.currency_code == "RUB"
    assert profile.currency_state == "verified"
    assert profile.nominal_state == "verified"
    assert profile.coupon_rate == Decimal("0")
    assert profile.maturity_state == "verified"
    assert profile.coupon_structure == "unknown"
    assert profile.amortization_structure == "unknown"
    assert profile.subordination_structure == "unknown"
    assert profile.perpetual_structure == "unknown"
    assert profile.offer_structure == "unknown"
    assert profile.trading_board == "TQCB"
    assert profile.lot_size is None and profile.lot_size_state == "unknown"
    assert profile.coupon_frequency_per_year is None
    assert profile.outstanding_nominal is None

    negative = create_bond(db_session, "12")
    negative_profile = service.ingest_moex_metadata(
        negative,
        {
            "raw": {
                "is_floating_coupon": False,
                "has_amortization": False,
                "is_subordinated": False,
                "is_perpetual": False,
            }
        },
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    assert negative_profile is not None
    assert negative_profile.coupon_structure == "fixed"
    assert negative_profile.amortization_structure == "bullet"
    assert negative_profile.subordination_structure == "senior"
    assert negative_profile.perpetual_structure == "dated"

    positive = create_bond(db_session, "13")
    positive_profile = service.ingest_moex_metadata(
        positive,
        {
            "raw": {
                "is_floating_coupon": True,
                "has_amortization": True,
                "is_subordinated": True,
                "is_perpetual": True,
                "offer_date": "2030-02-01",
            }
        },
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    assert positive_profile is not None
    assert positive_profile.coupon_structure == "floating"
    assert positive_profile.amortization_structure == "amortizing"
    assert positive_profile.subordination_structure == "subordinated"
    assert positive_profile.perpetual_structure == "perpetual"
    assert positive_profile.offer_structure == "present"


def test_faceunit_nominal_currency_agrees_across_sources_and_is_idempotent(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "14")
    service = BondSecurityMasterService(db_session)
    universe_metadata = {
        "raw": {"CURRENCYID": "SUR", "FACEUNIT": "USD"}
    }

    service.ingest_moex_metadata(
        bond,
        universe_metadata,
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    first_count = db_session.scalar(
        select(func.count())
        .select_from(BondSecurityMasterEvidence)
        .where(BondSecurityMasterEvidence.bond_id == bond.id)
    )
    service.ingest_moex_metadata(
        bond,
        universe_metadata,
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc),
    )
    service.ingest_moex_metadata(
        bond,
        {"raw": {"FACEUNIT": "USD"}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
    )

    profile = service.resolve_profile(bond)
    evidence_rows = list(
        db_session.execute(
            select(BondSecurityMasterEvidence)
            .where(
                BondSecurityMasterEvidence.bond_id == bond.id,
                BondSecurityMasterEvidence.field_name == "currency_code",
            )
            .order_by(BondSecurityMasterEvidence.source)
        ).scalars()
    )
    assert first_count == 1
    assert len(evidence_rows) == 2
    assert {row.source for row in evidence_rows} == {
        "moex_universe",
        "moex_description",
    }
    assert {row.normalized_value_json["value"] for row in evidence_rows} == {
        "USD"
    }
    assert all(
        row.raw_value_json == {"source_field": "FACEUNIT", "value": "USD"}
        for row in evidence_rows
    )
    assert profile.currency_state == "verified"
    assert profile.currency_code == "USD"


def test_faceunit_is_the_only_security_master_nominal_currency_source(
    db_session: Session,
) -> None:
    service = BondSecurityMasterService(db_session)

    foreign = create_bond(db_session, "15")
    foreign_profile = service.ingest_moex_metadata(
        foreign,
        {"raw": {"CURRENCYID": "USD", "FACEUNIT": "CNY"}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    assert foreign_profile is not None
    assert foreign_profile.currency_code == "CNY"

    rub = create_bond(db_session, "16")
    rub_profile = service.ingest_moex_metadata(
        rub,
        {"raw": {"CURRENCYID": "USD", "FACEUNIT": "SUR"}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    assert rub_profile is not None
    assert rub_profile.currency_code == "RUB"

    trading_only = create_bond(db_session, "17")
    assert service.ingest_moex_metadata(
        trading_only,
        {"raw": {"CURRENCYID": "SUR"}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    ) is None

    generic_only = create_bond(db_session, "18")
    assert service.ingest_moex_metadata(
        generic_only,
        {"raw": {"currency": "RUB"}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    ) is None

    blocked_bond_ids = {trading_only.id, generic_only.id}
    assert db_session.scalar(
        select(func.count())
        .select_from(BondSecurityMasterEvidence)
        .where(BondSecurityMasterEvidence.bond_id.in_(blocked_bond_ids))
    ) == 0


def test_cashflow_presence_conflict_and_idempotency(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "21")
    service = BondSecurityMasterService(db_session)
    profile = service.ingest_moex_cashflow_structure(
        bond,
        MoexCashflowScheduleResult(),
        observed_at=OBSERVED,
    )
    assert profile is None

    service.ingest_moex_metadata(
        bond,
        {"raw": {"has_amortization": False}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    schedule = MoexCashflowScheduleResult(
        amortizations=[
            {
                "amortdate": "2030-01-01",
                "valueprc": "5",
                "__moex_source_table": "amortizations",
            }
        ],
        offers=[
            {
                "offerdate": "2029-01-01",
                "__moex_source_table": "offers",
            }
        ],
    )
    first = service.ingest_moex_cashflow_structure(
        bond, schedule, observed_at=OBSERVED
    )
    before = db_session.scalar(
        select(func.count()).select_from(BondSecurityMasterEvidence)
    )
    second = service.ingest_moex_cashflow_structure(
        bond,
        schedule,
        observed_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    after = db_session.scalar(
        select(func.count()).select_from(BondSecurityMasterEvidence)
    )
    assert first is not None and second is not None
    assert first.amortization_structure == "conflict"
    assert first.offer_structure == "present"
    assert after == before
    cashflow_evidence = db_session.execute(
        select(BondSecurityMasterEvidence).where(
            BondSecurityMasterEvidence.bond_id == bond.id,
            BondSecurityMasterEvidence.field_name == "amortization_structure",
            BondSecurityMasterEvidence.source == "moex_cashflows",
        )
    ).scalar_one()
    assert cashflow_evidence.normalized_value_json == {"value": "amortizing"}
    assert cashflow_evidence.raw_value_json == {
        "classification_basis": "partial_principal_percent",
        "observed_row_count": 1,
        "usable_principal_row_count": 1,
        "source_tables": ["amortizations"],
    }


def _ingest_verified_maturity(
    service: BondSecurityMasterService,
    bond: Bond,
    maturity: date,
) -> None:
    service.ingest_moex_metadata(
        bond,
        {"raw": {"maturity_date": maturity.isoformat()}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )


def _cashflow_amortization_evidence(
    db_session: Session,
    bond: Bond,
) -> list[BondSecurityMasterEvidence]:
    return list(
        db_session.execute(
            select(BondSecurityMasterEvidence)
            .where(
                BondSecurityMasterEvidence.bond_id == bond.id,
                BondSecurityMasterEvidence.field_name == "amortization_structure",
                BondSecurityMasterEvidence.source == "moex_cashflows",
            )
            .order_by(BondSecurityMasterEvidence.id)
        ).scalars()
    )


@pytest.mark.parametrize("with_offer", [False, True], ids=["without-offer", "with-offer"])
def test_single_100_percent_at_verified_maturity_is_bullet(
    db_session: Session,
    with_offer: bool,
) -> None:
    bond = create_bond(db_session, f"40{int(with_offer)}")
    service = BondSecurityMasterService(db_session)
    maturity = date(2031, 4, 15)
    _ingest_verified_maturity(service, bond, maturity)
    rows = [
        {
            "AMORTIZATIONDATE": maturity.isoformat(),
            "AMORTIZATION_VALUE": "1000",
            "AMORTIZATION_PERCENT": "100",
            "__moex_source_table": "amortization_schedule",
        }
    ]
    offers = (
        [{"offerdate": "2030-01-01", "__moex_source_table": "offers"}]
        if with_offer
        else []
    )

    profile = service.ingest_moex_cashflow_structure(
        bond,
        MoexCashflowScheduleResult(amortizations=rows, offers=offers),
        observed_at=OBSERVED,
    )

    assert profile is not None
    assert profile.amortization_structure == "bullet"
    assert profile.offer_structure == ("present" if with_offer else "unknown")
    evidence = _cashflow_amortization_evidence(db_session, bond)
    assert len(evidence) == 1
    assert evidence[0].raw_value_json == {
        "classification_basis": "single_100pct_at_verified_maturity",
        "observed_row_count": 1,
        "usable_principal_row_count": 1,
        "source_tables": ["amortization_schedule"],
    }


@pytest.mark.parametrize(
    ("rows", "maturity", "expected_basis"),
    [
        (
            [{"amortdate": "2031-04-15", "valueprc": "25"}],
            None,
            "partial_principal_percent",
        ),
        (
            [
                {"amort_date": "2031-04-15", "amortvalue": "500"},
                {"date": "2032-04-15", "value": "500"},
            ],
            None,
            "multiple_principal_dates",
        ),
        (
            [{"amortizationdate": "2030-04-15", "value": "1000"}],
            date(2031, 4, 15),
            "principal_before_verified_maturity",
        ),
    ],
    ids=["partial-percent", "multiple-dates", "before-maturity"],
)
def test_genuine_principal_schedules_are_amortizing(
    db_session: Session,
    rows: list[dict[str, str]],
    maturity: date | None,
    expected_basis: str,
) -> None:
    suffix = {
        "partial_principal_percent": "411",
        "multiple_principal_dates": "412",
        "principal_before_verified_maturity": "413",
    }[expected_basis]
    bond = create_bond(db_session, suffix)
    service = BondSecurityMasterService(db_session)
    if maturity is not None:
        _ingest_verified_maturity(service, bond, maturity)
    for row in rows:
        row["__moex_source_table"] = "amortizations"

    profile = service.ingest_moex_cashflow_structure(
        bond,
        MoexCashflowScheduleResult(amortizations=rows),
        observed_at=OBSERVED,
    )

    assert profile is not None
    assert profile.amortization_structure == "amortizing"
    evidence = _cashflow_amortization_evidence(db_session, bond)
    assert len(evidence) == 1
    assert evidence[0].raw_value_json["classification_basis"] == expected_basis
    assert evidence[0].raw_value_json["observed_row_count"] == len(rows)
    assert evidence[0].raw_value_json["usable_principal_row_count"] == len(rows)


@pytest.mark.parametrize(
    ("suffix", "rows", "verified_maturity"),
    [
        ("421", [], date(2031, 4, 15)),
        ("422", [{"amortdate": "not-a-date", "valueprc": "25"}], date(2031, 4, 15)),
        (
            "423",
            [
                {"amortdate": "2031-04-15", "value": "0", "valueprc": "0"},
                {"amortdate": "2031-04-15", "value": "-1", "valueprc": "-1"},
                {"amortdate": "2031-04-15", "value": "NaN", "valueprc": "Infinity"},
            ],
            date(2031, 4, 15),
        ),
        ("424", [{"amortdate": "0001-01-01", "valueprc": "100"}], date(2031, 4, 15)),
        ("425", [{"amortdate": "2031-04-15", "valueprc": "100"}], None),
        ("426", [{"amortdate": "2031-04-15", "value": "1000"}], date(2031, 4, 15)),
        ("427", [{"amortdate": "2032-04-15", "valueprc": "100"}], date(2031, 4, 15)),
    ],
    ids=[
        "empty",
        "malformed-date",
        "non-positive-and-non-finite",
        "placeholder-date",
        "unknown-maturity",
        "missing-percent",
        "after-maturity",
    ],
)
def test_ambiguous_or_unusable_principal_schedules_make_no_assertion(
    db_session: Session,
    suffix: str,
    rows: list[dict[str, str]],
    verified_maturity: date | None,
) -> None:
    bond = create_bond(db_session, suffix)
    service = BondSecurityMasterService(db_session)
    if verified_maturity is not None:
        _ingest_verified_maturity(service, bond, verified_maturity)

    profile = service.ingest_moex_cashflow_structure(
        bond,
        MoexCashflowScheduleResult(amortizations=rows),
        observed_at=OBSERVED,
    )

    assert _cashflow_amortization_evidence(db_session, bond) == []
    if profile is not None:
        assert profile.amortization_structure == "unknown"


def test_metadata_bullet_agrees_with_cashflow_bullet_and_legacy_terms_are_ignored(
    db_session: Session,
) -> None:
    service = BondSecurityMasterService(db_session)
    agreeing = create_bond(db_session, "431")
    maturity = date(2031, 4, 15)
    service.ingest_moex_metadata(
        agreeing,
        {"raw": {"maturity_date": maturity.isoformat(), "has_amortization": False}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    profile = service.ingest_moex_cashflow_structure(
        agreeing,
        MoexCashflowScheduleResult(
            amortizations=[
                {"amortdate": maturity.isoformat(), "value": "1000", "valueprc": "100"}
            ]
        ),
        observed_at=OBSERVED,
    )
    assert profile is not None
    assert profile.amortization_structure == "bullet"
    assertions = list(
        db_session.execute(
            select(BondSecurityMasterEvidence).where(
                BondSecurityMasterEvidence.bond_id == agreeing.id,
                BondSecurityMasterEvidence.field_name == "amortization_structure",
            )
        ).scalars()
    )
    assert {row.source for row in assertions} == {"moex_description", "moex_cashflows"}
    assert {row.normalized_value_json["value"] for row in assertions} == {"bullet"}

    legacy_only = create_bond(db_session, "432")
    assert legacy_only.maturity_date == date(2035, 1, 1)
    legacy_profile = service.ingest_moex_cashflow_structure(
        legacy_only,
        MoexCashflowScheduleResult(
            amortizations=[
                {"amortdate": "2035-01-01", "value": "1000", "valueprc": "100"}
            ]
        ),
        observed_at=OBSERVED,
    )
    assert legacy_profile is None
    assert _cashflow_amortization_evidence(db_session, legacy_only) == []


def test_scalar_conflict_multi_source_and_point_in_time_history(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "31")
    service = BondSecurityMasterService(db_session)
    service.ingest_moex_metadata(
        bond,
        {"raw": {"FACEUNIT": "RUB"}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    service.ingest_moex_metadata(
        bond,
        {"raw": {"FACEUNIT": "RUB"}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
    )
    agreeing = service.resolve_profile(bond)
    assert agreeing.currency_state == "verified"
    assert agreeing.currency_code == "RUB"
    assert db_session.scalar(
        select(func.count())
        .select_from(BondSecurityMasterEvidence)
        .where(BondSecurityMasterEvidence.field_name == "currency_code")
    ) == 2

    service.ingest_moex_metadata(
        bond,
        {"raw": {"FACEUNIT": "USD"}},
        source="moex_description",
        board=None,
        board_observed=False,
        observed_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    profile = service.resolve_profile(bond)
    assert profile.currency_state == "conflict"
    assert profile.currency_code is None

    effective_one = datetime(2027, 1, 1, tzinfo=timezone.utc)
    effective_two = datetime(2028, 1, 1, tzinfo=timezone.utc)
    for observed_at, effective_at in (
        (OBSERVED, effective_one),
        (datetime(2026, 8, 27, tzinfo=timezone.utc), effective_two),
    ):
        service.record_assertion(
            bond=bond,
            field_name="coupon_rate",
            source="moex_description",
            assertion_type="scalar_value",
            normalized_value="10",
            observed_at=observed_at,
            effective_at=effective_at,
            source_key=bond.secid,
            raw_value={"source_field": "coupon_rate", "value": "10"},
        )
    history = list(
        db_session.execute(
            select(BondSecurityMasterEvidence)
            .where(BondSecurityMasterEvidence.field_name == "coupon_rate")
            .order_by(BondSecurityMasterEvidence.effective_at)
        ).scalars()
    )
    assert len(history) == 2
    assert history[0].observed_at != history[1].observed_at
    assert history[0].effective_at != history[1].effective_at
    assert all(row.ingestion_at not in {row.observed_at, row.effective_at} for row in history)


def test_moex_universe_float_metadata_is_preserved_and_idempotent(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "61")
    service = BondSecurityMasterService(db_session)

    first_profile = service.ingest_moex_metadata(
        bond,
        {"raw": {"COUPONPERCENT": 7.625}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    first_evidence = db_session.execute(
        select(BondSecurityMasterEvidence).where(
            BondSecurityMasterEvidence.bond_id == bond.id,
            BondSecurityMasterEvidence.field_name == "coupon_rate",
        )
    ).scalar_one()
    first_fingerprint = first_evidence.evidence_fingerprint
    first_observed_at = first_evidence.observed_at

    second_profile = service.ingest_moex_metadata(
        bond,
        {"raw": {"COUPONPERCENT": 7.625}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )
    db_session.flush()
    db_session.expire_all()
    evidence_rows = list(
        db_session.execute(
            select(BondSecurityMasterEvidence).where(
                BondSecurityMasterEvidence.bond_id == bond.id,
                BondSecurityMasterEvidence.field_name == "coupon_rate",
            )
        ).scalars()
    )

    assert first_profile is not None and second_profile is not None
    assert first_profile.coupon_rate == Decimal("7.625")
    assert first_profile.coupon_rate_state == "verified"
    assert second_profile.coupon_rate == Decimal("7.625")
    assert second_profile.coupon_rate_state == "verified"
    assert len(evidence_rows) == 1
    assert evidence_rows[0].source == "moex_universe"
    assert evidence_rows[0].normalized_value_json == {"value": "7.625"}
    assert evidence_rows[0].raw_value_json == {
        "source_field": "COUPONPERCENT",
        "value": 7.625,
    }
    assert evidence_rows[0].evidence_fingerprint == first_fingerprint
    assert evidence_rows[0].observed_at == first_observed_at


@pytest.mark.parametrize("raw_float", [7.625, 1000.0, 0.0, -1.25])
def test_record_assertion_accepts_finite_float_raw_scalars(
    db_session: Session,
    raw_float: float,
) -> None:
    bond = create_bond(db_session, "62")
    evidence, created = BondSecurityMasterService(db_session).record_assertion(
        bond=bond,
        field_name="coupon_rate",
        source="moex_universe",
        assertion_type="scalar_value",
        normalized_value="1",
        observed_at=OBSERVED,
        source_key=bond.secid,
        raw_value={"source_field": "COUPONPERCENT", "value": raw_float},
    )

    assert created is True
    assert evidence.raw_value_json == {
        "source_field": "COUPONPERCENT",
        "value": raw_float,
    }
    assert isinstance(evidence.raw_value_json["value"], float)


@pytest.mark.parametrize(
    "raw_float",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_record_assertion_rejects_non_finite_float_raw_scalars(
    db_session: Session,
    raw_float: float,
) -> None:
    bond = create_bond(db_session, "63")
    service = BondSecurityMasterService(db_session)

    with pytest.raises(
        ValueError,
        match="Security-master raw evidence value must be scalar",
    ):
        service.record_assertion(
            bond=bond,
            field_name="coupon_rate",
            source="moex_universe",
            assertion_type="scalar_value",
            normalized_value="1",
            observed_at=OBSERVED,
            source_key=bond.secid,
            raw_value={"source_field": "COUPONPERCENT", "value": raw_float},
        )

    assert db_session.scalar(
        select(func.count()).select_from(BondSecurityMasterEvidence)
    ) == 0


@pytest.mark.parametrize(
    "raw_value",
    [
        {"value": {"nested": "not-allowed"}},
        {"value": object()},
        {"value": [7.625]},
        {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5},
        {"x" * 65: "too-long"},
    ],
    ids=["nested-dict", "arbitrary-object", "float-list", "too-many-fields", "long-key"],
)
def test_record_assertion_preserves_narrow_raw_evidence_rejections(
    db_session: Session,
    raw_value: dict[str, object],
) -> None:
    bond = create_bond(db_session, "64")
    service = BondSecurityMasterService(db_session)

    with pytest.raises(ValueError):
        service.record_assertion(
            bond=bond,
            field_name="coupon_rate",
            source="moex_universe",
            assertion_type="scalar_value",
            normalized_value="1",
            observed_at=OBSERVED,
            source_key=bond.secid,
            raw_value=raw_value,
        )

    assert db_session.scalar(
        select(func.count()).select_from(BondSecurityMasterEvidence)
    ) == 0


def qualified_profile() -> BondSecurityMasterProfile:
    return BondSecurityMasterProfile(
        bond_id=1,
        currency_code="RUB",
        currency_state="verified",
        nominal_value=Decimal("1000"),
        nominal_state="verified",
        maturity_date=date(2035, 1, 1),
        maturity_state="verified",
        coupon_structure="fixed",
        amortization_structure="bullet",
        subordination_structure="senior",
        perpetual_structure="dated",
        offer_structure="none",
        lot_size=1,
        lot_size_state="verified",
        trading_board="TQCB",
        trading_board_state="verified",
        coupon_frequency_per_year=4,
        coupon_frequency_state="verified",
        outstanding_nominal=Decimal("1000000"),
        outstanding_nominal_state="verified",
    )


def test_research_strategy_and_execution_blockers_are_separate() -> None:
    as_of = date(2026, 8, 26)
    assert research_terms_blockers(None, as_of) == [
        "SECURITY_MASTER_PROFILE_MISSING"
    ]
    profile = qualified_profile()
    assert research_terms_blockers(profile, as_of) == []
    assert plain_vanilla_strategy_blockers(profile, as_of) == []
    assert execution_terms_blockers(profile) == []

    profile.coupon_structure = "floating"
    profile.amortization_structure = "amortizing"
    profile.subordination_structure = "subordinated"
    profile.perpetual_structure = "perpetual"
    profile.offer_structure = "present"
    assert research_terms_blockers(profile, as_of) == []
    assert plain_vanilla_strategy_blockers(profile, as_of) == [
        "COUPON_STRUCTURE_NOT_FIXED",
        "AMORTIZATION_STRUCTURE_NOT_BULLET",
        "SUBORDINATION_STRUCTURE_NOT_SENIOR",
        "PERPETUAL_STRUCTURE_NOT_DATED",
        "OFFER_STRUCTURE_NOT_NONE",
    ]

    profile.lot_size = None
    profile.lot_size_state = "unknown"
    profile.trading_board = None
    profile.trading_board_state = "unknown"
    profile.coupon_frequency_per_year = None
    profile.coupon_frequency_state = "unknown"
    profile.outstanding_nominal = None
    profile.outstanding_nominal_state = "unknown"
    assert execution_terms_blockers(profile) == [
        "LOT_SIZE_NOT_VERIFIED",
        "TRADING_BOARD_NOT_VERIFIED",
        "COUPON_FREQUENCY_NOT_VERIFIED",
        "OUTSTANDING_NOMINAL_NOT_VERIFIED",
    ]
