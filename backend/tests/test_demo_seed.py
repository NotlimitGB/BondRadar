from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.financial_report import FinancialReport
from app.models.ml_model_run import MLModelRun
from app.scripts.seed_demo_data import (
    DEMO_BOND_SECIDS,
    DEMO_COMPANY_TICKERS,
    DEMO_SOURCE,
    HIGH_YIELD_WARNING,
    DemoSeedOptions,
    seed_demo_data,
)


def test_demo_seed_creates_core_data(db_session: Session) -> None:
    summary = seed_demo_data(db_session, DemoSeedOptions())

    assert summary.companies_created == len(DEMO_COMPANY_TICKERS)
    assert summary.bonds_created == len(DEMO_BOND_SECIDS)
    assert _count_demo_companies(db_session) == len(DEMO_COMPANY_TICKERS)
    assert _count_demo_bonds(db_session) == len(DEMO_BOND_SECIDS)
    assert _count(db_session, FinancialReport) >= len(DEMO_COMPANY_TICKERS)
    assert _count_demo_market_snapshots(db_session) > 0
    assert _count_demo_cashflows(db_session) > 0
    assert _count_demo_features(db_session) > 0


def test_demo_seed_is_idempotent_for_core_data(db_session: Session) -> None:
    seed_demo_data(db_session, DemoSeedOptions())
    counts_before = _core_counts(db_session)

    second_summary = seed_demo_data(db_session, DemoSeedOptions())
    counts_after = _core_counts(db_session)

    assert counts_after == counts_before
    assert second_summary.companies_created == 0
    assert second_summary.bonds_created == 0
    assert second_summary.market_snapshots_created == 0
    assert second_summary.cashflow_events_created == 0
    assert second_summary.price_labels_created == 0
    assert second_summary.total_return_labels_created == 0
    assert second_summary.risk_adjusted_labels_created == 0


def test_demo_seed_creates_all_return_methods_with_both_risk_adjusted_classes(
    db_session: Session,
) -> None:
    summary = seed_demo_data(db_session, DemoSeedOptions())
    bond_ids = _demo_bond_ids(db_session)

    for return_method in {"price", "total_return", "risk_adjusted"}:
        assert (
            _count_labels(db_session, bond_ids, return_method=return_method)
            > 0
        )

    distribution = summary.risk_adjusted_label_distribution
    assert distribution["positive_return"] > 0
    assert distribution["negative_return"] > 0


def test_demo_seed_business_checks_are_true(db_session: Session) -> None:
    summary = seed_demo_data(db_session, DemoSeedOptions())

    assert summary.cashflow_impact_example_found is True
    assert summary.high_yield_weak_issuer_warning_found is True
    assert summary.risk_adjusted_label_distribution["positive_return"] > 0
    assert summary.risk_adjusted_label_distribution["negative_return"] > 0

    bond_ids = _demo_bond_ids(db_session)
    impact_label = db_session.execute(
        select(BondReturnLabel).where(
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.price_return < 0,
            BondReturnLabel.net_total_return > 0,
        ).limit(1)
    ).scalar_one_or_none()
    assert impact_label is not None

    assessments = list(
        db_session.execute(
            select(BondRiskAssessment).where(BondRiskAssessment.bond_id.in_(bond_ids))
        ).scalars()
    )
    assert any(HIGH_YIELD_WARNING in assessment.warnings for assessment in assessments)


def test_demo_seed_skips_ml_by_default(db_session: Session) -> None:
    summary = seed_demo_data(db_session, DemoSeedOptions())

    assert summary.ml_run_id is None
    assert "ML training skipped" in summary.ml_message
    assert _count(db_session, MLModelRun) == 0


def _core_counts(db_session: Session) -> dict[str, int]:
    bond_ids = _demo_bond_ids(db_session)
    return {
        "companies": _count_demo_companies(db_session),
        "bonds": _count_demo_bonds(db_session),
        "reports": _count(db_session, FinancialReport),
        "market_snapshots": _count_demo_market_snapshots(db_session),
        "cashflows": _count_demo_cashflows(db_session),
        "credit_health": _count(db_session, CompanyCreditHealthSnapshot),
        "risk_assessments": _count(db_session, BondRiskAssessment),
        "features": _count_demo_features(db_session),
        "price_labels": _count_labels(db_session, bond_ids, return_method="price"),
        "total_return_labels": _count_labels(
            db_session,
            bond_ids,
            return_method="total_return",
        ),
        "risk_adjusted_labels": _count_labels(
            db_session,
            bond_ids,
            return_method="risk_adjusted",
        ),
    }


def _demo_bond_ids(db_session: Session) -> list[int]:
    return list(
        db_session.execute(
            select(Bond.id).where(Bond.secid.in_(DEMO_BOND_SECIDS))
        ).scalars()
    )


def _count(db_session: Session, model: type) -> int:
    return int(db_session.execute(select(func.count()).select_from(model)).scalar_one())


def _count_demo_companies(db_session: Session) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(Company)
            .where(Company.ticker.in_(DEMO_COMPANY_TICKERS))
        ).scalar_one()
    )


def _count_demo_bonds(db_session: Session) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(Bond)
            .where(Bond.secid.in_(DEMO_BOND_SECIDS))
        ).scalar_one()
    )


def _count_demo_market_snapshots(db_session: Session) -> int:
    bond_ids = _demo_bond_ids(db_session)
    return int(
        db_session.execute(
            select(func.count())
            .select_from(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id.in_(bond_ids),
                BondMarketSnapshot.source == DEMO_SOURCE,
            )
        ).scalar_one()
    )


def _count_demo_cashflows(db_session: Session) -> int:
    bond_ids = _demo_bond_ids(db_session)
    return int(
        db_session.execute(
            select(func.count())
            .select_from(BondCashflowEvent)
            .where(
                BondCashflowEvent.bond_id.in_(bond_ids),
                BondCashflowEvent.source == DEMO_SOURCE,
            )
        ).scalar_one()
    )


def _count_demo_features(db_session: Session) -> int:
    bond_ids = _demo_bond_ids(db_session)
    return int(
        db_session.execute(
            select(func.count())
            .select_from(BondFeatureSnapshot)
            .where(BondFeatureSnapshot.bond_id.in_(bond_ids))
        ).scalar_one()
    )


def _count_labels(
    db_session: Session,
    bond_ids: list[int],
    *,
    return_method: str,
) -> int:
    return int(
        db_session.execute(
            select(func.count())
            .select_from(BondReturnLabel)
            .where(
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.return_method == return_method,
            )
        ).scalar_one()
    )
