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
        {"raw": {"currency": "RUB"}},
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
                "currency": "SUR",
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


def test_scalar_conflict_multi_source_and_point_in_time_history(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "31")
    service = BondSecurityMasterService(db_session)
    service.ingest_moex_metadata(
        bond,
        {"raw": {"currency": "RUB"}},
        source="moex_universe",
        board=None,
        board_observed=False,
        observed_at=OBSERVED,
    )
    service.ingest_moex_metadata(
        bond,
        {"raw": {"currency": "RUB"}},
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
        {"raw": {"currency": "USD"}},
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
