from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.label_builder_service import LabelBuilderService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


REPORT_URL = "/api/data-quality/labels/report"


def report_payload(**overrides) -> dict:
    payload = {
        "date_from": "2026-01-01",
        "date_to": "2026-03-31",
        "horizon_days": None,
        "return_methods": None,
        "include_bond_rows": True,
        "include_company_rows": True,
        "include_warning_breakdown": True,
        "include_component_summary": True,
        "include_return_distribution": True,
        "extreme_return_abs_limit": "0.50",
        "minimum_evaluable_rows": 2,
        "minimum_positive_rows": 1,
        "minimum_negative_rows": 1,
        "maximum_insufficient_ratio": "0.30",
        "limit": 100,
        "offset": 0,
    }
    payload.update(overrides)
    return payload


def create_company(db: Session, ticker: str = "LQ") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(db: Session, company: Company, index: int) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000LQ{index:05d}"[:12],
        secid=f"LQ{index:05d}",
        name=f"Label Quality Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        maturity_date=date(2030, 1, 1),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def add_snapshot(db: Session, bond: Bond, trade_date: date) -> BondMarketSnapshot:
    snapshot = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=trade_date,
        price=Decimal("100.000000"),
        clean_price=Decimal("100.000000"),
        dirty_price=Decimal("101.000000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        source="test",
        raw_payload={"test": True},
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def add_label(
    db: Session,
    bond: Bond,
    as_of_date: date,
    *,
    horizon_days: int = 30,
    return_method: str = "total_return",
    future_return: Decimal | None = Decimal("0.020000"),
    label_value: str = "positive_return",
    label_binary: int | None = 1,
    with_snapshots: bool = True,
    warnings: list[str] | None = None,
    details: dict | None = None,
    component_base: Decimal = Decimal("0.010000"),
) -> BondReturnLabel:
    start_snapshot = (
        add_snapshot(db, bond, as_of_date)
        if with_snapshots
        else None
    )
    end_snapshot = (
        add_snapshot(db, bond, as_of_date + timedelta(days=horizon_days))
        if with_snapshots
        else None
    )
    label = BondReturnLabel(
        bond_id=bond.id,
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        return_method=return_method,
        start_market_snapshot_id=start_snapshot.id if start_snapshot else None,
        end_market_snapshot_id=end_snapshot.id if end_snapshot else None,
        start_price=Decimal("100.000000") if with_snapshots else None,
        end_price=Decimal("102.000000") if with_snapshots else None,
        future_return=future_return,
        benchmark_return=Decimal("0.000000"),
        excess_return=future_return,
        price_return=future_return,
        coupon_return=component_base,
        amortization_return=component_base,
        redemption_return=component_base,
        gross_total_return=future_return,
        estimated_costs_return=Decimal("0.001000"),
        net_total_return=future_return,
        risk_adjusted_excess_return=future_return,
        required_risk_premium=Decimal("0.005000"),
        return_calculation_warnings=warnings or [],
        return_calculation_details=details or {"cashflows_included": True},
        label=label_value,
        label_binary=label_binary,
    )
    db.add(label)
    db.flush()
    return label


def count_rows(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def seed_basic_labels(db: Session) -> tuple[Company, list[Bond]]:
    company = create_company(db, "LQA")
    bonds = [create_bond(db, company, 1), create_bond(db, company, 2)]
    add_label(
        db,
        bonds[0],
        date(2026, 1, 5),
        future_return=Decimal("0.020000"),
        label_value="positive_return",
        label_binary=1,
    )
    add_label(
        db,
        bonds[0],
        date(2026, 1, 6),
        future_return=Decimal("-0.010000"),
        label_value="negative_return",
        label_binary=0,
    )
    add_label(
        db,
        bonds[1],
        date(2026, 1, 7),
        future_return=None,
        label_value="insufficient_data",
        label_binary=None,
        with_snapshots=False,
    )
    db.commit()
    return company, bonds


def test_empty_db_returns_stable_response(client: TestClient) -> None:
    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["selected_bond_count"] == 0
    assert payload["overview"]["label_row_count"] == 0
    assert payload["bond_rows"] == []
    assert payload["company_rows"] == []


def test_overview_counts_and_class_balance(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_basic_labels(db_session)

    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200
    overview = response.json()["overview"]
    assert overview["label_row_count"] == 3
    assert overview["evaluable_label_count"] == 2
    assert overview["positive_label_count"] == 1
    assert overview["negative_label_count"] == 1
    assert overview["insufficient_label_count"] == 1
    assert Decimal(str(overview["insufficient_ratio"])) == Decimal("0.3333333333333333333333333333")
    assert overview["ready_for_ml_dataset"] is False


def test_method_summaries_group_by_method_and_horizon(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MSM")
    bond = create_bond(db_session, company, 10)
    add_label(db_session, bond, date(2026, 1, 5), return_method="price")
    add_label(db_session, bond, date(2026, 1, 6), return_method="total_return")
    add_label(
        db_session,
        bond,
        date(2026, 1, 7),
        horizon_days=60,
        return_method="risk_adjusted",
        future_return=Decimal("-0.010000"),
        label_value="negative_return",
        label_binary=0,
    )
    db_session.commit()

    response = client.post(REPORT_URL, json=report_payload(horizon_days=None))

    assert response.status_code == 200
    methods = [
        (item["return_method"], item["horizon_days"])
        for item in response.json()["method_summaries"]
    ]
    assert methods == [("price", 30), ("risk_adjusted", 60), ("total_return", 30)]


def test_return_distribution_percentiles(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "DST")
    bond = create_bond(db_session, company, 20)
    for index, value in enumerate(
        [
            Decimal("-0.100000"),
            Decimal("-0.050000"),
            Decimal("0.000000"),
            Decimal("0.050000"),
            Decimal("0.100000"),
        ],
        start=1,
    ):
        add_label(
            db_session,
            bond,
            date(2026, 1, index),
            future_return=value,
            label_value="positive_return" if value >= 0 else "negative_return",
            label_binary=1 if value >= 0 else 0,
        )
    db_session.commit()

    response = client.post(REPORT_URL, json=report_payload(minimum_evaluable_rows=1))

    assert response.status_code == 200
    distribution = response.json()["return_distribution"]
    assert distribution["count"] == 5
    assert Decimal(str(distribution["average"])) == Decimal("0.000000")
    assert Decimal(str(distribution["median"])) == Decimal("0.000000")
    assert Decimal(str(distribution["minimum"])) == Decimal("-0.100000")
    assert Decimal(str(distribution["maximum"])) == Decimal("0.100000")
    assert Decimal(str(distribution["p10"])) == Decimal("-0.100000")
    assert Decimal(str(distribution["p25"])) == Decimal("-0.050000")
    assert Decimal(str(distribution["p75"])) == Decimal("0.050000")
    assert Decimal(str(distribution["p90"])) == Decimal("0.100000")


def test_component_summary_and_warning_breakdown(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CMP")
    bond = create_bond(db_session, company, 30)
    add_label(
        db_session,
        bond,
        date(2026, 1, 5),
        future_return=Decimal("0.020000"),
        warnings=[
            "Benchmark return is not provided, zero benchmark was used",
            "Required risk premium is missing, zero premium was used",
        ],
        details={"cashflows_included": True},
        component_base=Decimal("0.020000"),
    )
    add_label(
        db_session,
        bond,
        date(2026, 1, 6),
        future_return=Decimal("-0.010000"),
        label_value="negative_return",
        label_binary=0,
        warnings=["Benchmark return is not provided, zero benchmark was used"],
        details={"cashflows_included": False},
        component_base=Decimal("0.000000"),
    )
    db_session.commit()

    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200
    component = response.json()["component_summary"]
    assert Decimal(str(component["price_return_average"])) == Decimal("0.005000")
    assert Decimal(str(component["coupon_return_average"])) == Decimal("0.010000")
    assert component["cashflow_included_count"] == 1
    assert component["cashflow_disabled_count"] == 1
    assert component["benchmark_missing_count"] == 2
    assert component["risk_premium_missing_count"] == 1
    breakdown = response.json()["warning_breakdown"]
    assert breakdown[0]["count"] == 2
    assert "Benchmark return is not provided" in breakdown[0]["message"]


def test_bond_row_statuses_and_issues(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "BRS")
    no_label_bond = create_bond(db_session, company, 40)
    insufficient_bond = create_bond(db_session, company, 41)
    warning_bond = create_bond(db_session, company, 42)
    extreme_bond = create_bond(db_session, company, 43)
    add_label(
        db_session,
        insufficient_bond,
        date(2026, 1, 5),
        future_return=None,
        label_value="insufficient_data",
        label_binary=None,
        with_snapshots=False,
    )
    add_label(
        db_session,
        warning_bond,
        date(2026, 1, 5),
        warnings=["Benchmark return is not provided, zero benchmark was used"],
    )
    add_label(
        db_session,
        extreme_bond,
        date(2026, 1, 5),
        future_return=Decimal("0.900000"),
    )
    db_session.commit()

    response = client.post(
        REPORT_URL,
        json=report_payload(
            bond_ids=[
                no_label_bond.id,
                insufficient_bond.id,
                warning_bond.id,
                extreme_bond.id,
            ],
            minimum_evaluable_rows=1,
        ),
    )

    assert response.status_code == 200
    rows = {row["bond_id"]: row for row in response.json()["bond_rows"]}
    assert rows[no_label_bond.id]["status"] == "not_ready"
    assert "no_labels" in rows[no_label_bond.id]["issues"]
    assert rows[insufficient_bond.id]["status"] == "not_ready"
    assert "missing_start_snapshot" in rows[insufficient_bond.id]["issues"]
    assert rows[warning_bond.id]["status"] == "warning"
    assert "warning_labels" in rows[warning_bond.id]["issues"]
    assert rows[extreme_bond.id]["status"] == "not_ready"
    assert "extreme_return" in rows[extreme_bond.id]["issues"]


def test_company_row_aggregation(client: TestClient, db_session: Session) -> None:
    first = create_company(db_session, "CAG")
    second = create_company(db_session, "CBG")
    first_bond = create_bond(db_session, first, 50)
    second_bond = create_bond(db_session, second, 51)
    add_label(db_session, first_bond, date(2026, 1, 5))
    add_label(
        db_session,
        second_bond,
        date(2026, 1, 5),
        future_return=Decimal("0.900000"),
    )
    db_session.commit()

    response = client.post(REPORT_URL, json=report_payload(minimum_evaluable_rows=1))

    assert response.status_code == 200
    companies = {row["company_id"]: row for row in response.json()["company_rows"]}
    assert companies[first.id]["status"] == "warning"
    assert companies[first.id]["bond_count"] == 1
    assert companies[second.id]["status"] == "not_ready"
    assert companies[second.id]["extreme_return_count"] == 1


def test_scope_by_bond_company_and_secid(client: TestClient, db_session: Session) -> None:
    first = create_company(db_session, "SCA")
    second = create_company(db_session, "SCB")
    first_bond = create_bond(db_session, first, 60)
    second_bond = create_bond(db_session, second, 61)
    add_label(db_session, first_bond, date(2026, 1, 5))
    add_label(db_session, second_bond, date(2026, 1, 5))
    db_session.commit()

    by_bond = client.post(REPORT_URL, json=report_payload(bond_ids=[first_bond.id]))
    by_company = client.post(REPORT_URL, json=report_payload(company_ids=[second.id]))
    by_secid = client.post(
        REPORT_URL,
        json=report_payload(secids=[first_bond.secid.lower()]),
    )

    assert by_bond.status_code == 200
    assert by_company.status_code == 200
    assert by_secid.status_code == 200
    assert by_bond.json()["overview"]["selected_bond_count"] == 1
    assert by_company.json()["bond_rows"][0]["bond_id"] == second_bond.id
    assert by_secid.json()["bond_rows"][0]["bond_id"] == first_bond.id


def test_duplicate_selectors_and_include_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "INC")
    bonds = [create_bond(db_session, company, 70), create_bond(db_session, company, 71)]
    for index, bond in enumerate(bonds, start=1):
        add_label(db_session, bond, date(2026, 1, index))
    db_session.commit()

    response = client.post(
        REPORT_URL,
        json=report_payload(
            bond_ids=[bonds[0].id, bonds[0].id, bonds[1].id],
            include_bond_rows=False,
            include_company_rows=False,
            include_warning_breakdown=False,
            include_component_summary=False,
            include_return_distribution=False,
            limit=1,
            offset=1,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_bond_rows"] == 2
    assert payload["bond_rows"] == []
    assert payload["total_company_rows"] == 1
    assert payload["company_rows"] == []
    assert payload["warning_breakdown"] == []
    assert payload["component_summary"] is None
    assert payload["return_distribution"] is None
    assert any("Duplicate selectors were ignored" in item["message"] for item in payload["warnings"])


def test_invalid_requests_return_400(client: TestClient) -> None:
    cases = [
        (report_payload(date_from="2026-02-01", date_to="2026-01-01"), "Invalid date range"),
        (report_payload(date_from="2010-01-01", date_to="2026-01-01"), "date range must not exceed 3660 days"),
        (report_payload(horizon_days=0), "horizon_days must be positive"),
        (report_payload(return_methods=["not_real"]), "Invalid return method"),
        (report_payload(bond_ids=[1], company_ids=[1]), "Use only one selector type: bond_ids, company_ids, or secids"),
        (report_payload(extreme_return_abs_limit="0"), "extreme_return_abs_limit must be positive"),
        (report_payload(minimum_evaluable_rows=0), "minimum_evaluable_rows must be positive"),
        (report_payload(minimum_positive_rows=-1), "class minimums must be non-negative"),
        (report_payload(maximum_insufficient_ratio="1.5"), "maximum_insufficient_ratio must be between 0 and 1"),
        (report_payload(limit=0), "limit must be between 1 and 500"),
        (report_payload(offset=-1), "offset must be non-negative"),
    ]

    for payload, detail in cases:
        response = client.post(REPORT_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_report_is_read_only(client: TestClient, db_session: Session) -> None:
    company, bonds = seed_basic_labels(db_session)
    db_session.add(
        BondCashflowEvent(
            bond_id=bonds[0].id,
            event_date=date(2026, 1, 20),
            event_type="coupon",
            amount=Decimal("20.000000"),
            currency="RUB",
            source="test",
        )
    )
    db_session.add(
        BondFeatureSnapshot(
            bond_id=bonds[0].id,
            company_id=company.id,
            as_of_date=date(2026, 1, 5),
            missing_data_count=0,
            features_json={},
        )
    )
    db_session.commit()
    models = [
        Bond,
        Company,
        BondReturnLabel,
        BondMarketSnapshot,
        BondCashflowEvent,
        BondFeatureSnapshot,
    ]
    before = {model.__name__: count_rows(db_session, model) for model in models}

    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200
    after = {model.__name__: count_rows(db_session, model) for model in models}
    assert after == before


def test_report_does_not_call_generation_or_external_services(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    seed_basic_labels(db_session)

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    monkeypatch.setattr(TotalReturnLabelService, "build_labels", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(LabelBuilderService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(DatasetBuildService, "build", fail_call)
    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)

    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_basic_labels(db_session)

    response = client.post(REPORT_URL, json=report_payload())

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
