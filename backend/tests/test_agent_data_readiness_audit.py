from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.controlled_financial_statement_value import (
    ControlledFinancialStatementValue,
)
from app.models.financial_report import FinancialReport
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.bond_score_service import BondScoreService
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.company_scoring import CompanyScoreService
from app.services.moex_iss_client import MoexIssClient
from app.services.paper_trading_live_cycle_service import LivePaperCycleService


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "agent_data_readiness_audit.py"
)


def load_audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("agent_data_readiness_audit", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def company(db: Session, ticker: str, *, name: str | None = None) -> Company:
    row = Company(
        name=name or f"{ticker} Issuer",
        ticker=ticker,
        country="RU",
        signal="neutral",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def bond(db: Session, issuer: Company, suffix: int, *, complete_terms: bool = True) -> Bond:
    row = Bond(
        company_id=issuer.id,
        isin=f"RU{suffix:010d}" if complete_terms else None,
        secid=f"AUDIT{suffix}" if complete_terms else None,
        name=f"Audit Bond {suffix}",
        currency="RUB",
        nominal_value=Decimal("1000") if complete_terms else None,
        coupon_rate=Decimal("9.5") if complete_terms else None,
        maturity_date=date(2030, 1, 1) if complete_terms else None,
        offer_date=date(2028, 1, 1) if complete_terms else None,
        is_floating_coupon=suffix % 2 == 0,
        is_subordinated=False,
        is_perpetual=False,
        amortization=suffix % 3 == 0,
        signal="neutral",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def market(
    db: Session,
    target: Bond,
    *,
    trade_date: date,
    source: str,
    price: str | None = "101",
    ytm: str | None = "11",
    duration: str | None = "2.5",
    volume: str | None = "500000",
    liquidity: int | None = 70,
    nkd: str | None = "12",
    spread: str | None = "0.02",
) -> BondMarketSnapshot:
    row = BondMarketSnapshot(
        bond_id=target.id,
        trade_date=trade_date,
        source=source,
        price=None if price is None else Decimal(price),
        yield_to_maturity=None if ytm is None else Decimal(ytm),
        duration_years=None if duration is None else Decimal(duration),
        volume=None if volume is None else Decimal(volume),
        liquidity_score=liquidity,
        nkd=None if nkd is None else Decimal(nkd),
        spread_to_ofz=None if spread is None else Decimal(spread),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def risk(db: Session, target: Bond, *, as_of: date) -> BondRiskAssessment:
    row = BondRiskAssessment(
        bond_id=target.id,
        company_id=target.company_id,
        as_of_date=as_of,
        assessment_score=70,
        decision_status="eligible_for_analysis",
        risk_level="low",
        required_risk_premium=Decimal("0.005"),
        gates={},
        warnings=[],
        blocking_reasons=[],
        positive_factors=[],
        negative_factors=[],
        missing_data=[],
        explanation={},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def controlled_value(
    db: Session,
    *,
    company_id: str,
    metric_key: str,
    suffix: str,
) -> ControlledFinancialStatementValue:
    row = ControlledFinancialStatementValue(
        company_id=company_id,
        company_name="Synthetic controlled entity",
        report_year=2025,
        report_standard="IFRS",
        target_type="profit_or_loss",
        metric_key=metric_key,
        metric_role="aggregate",
        metric_name_ru="Synthetic metric",
        metric_name_en="Synthetic metric",
        statement_page=1,
        page_number=1,
        value_2025=Decimal("100"),
        value_2024=Decimal("90"),
        raw_value_2025="100",
        raw_value_2024="90",
        currency_2025="RUB",
        unit_2025="RUB million",
        scale_2025="1000000",
        currency_2024="RUB",
        unit_2024="RUB million",
        scale_2024="1000000",
        raw_line="Synthetic controlled line",
        note_reference="",
        source_pdf_sha256="a" * 64,
        plan_checksum_sha256="b" * 64,
        plan_rows_checksum_sha256="c" * 64,
        natural_key=f"synthetic-{suffix}",
        natural_key_sha256=(suffix * 64)[:64],
        row_checksum_sha256="d" * 64,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def synthetic_fixture(db: Session) -> dict[str, Any]:
    resolved = company(db, "AUDR")
    second = company(db, "AUDS")
    placeholder = company(
        db,
        "AUDP",
        name="Unknown issuer for RU000SYN237",
    )
    complete = bond(db, resolved, 2371)
    partial = bond(db, resolved, 2372)
    no_market = bond(db, second, 2373, complete_terms=False)
    placeholder_bond = bond(db, placeholder, 2374)

    market(
        db,
        complete,
        trade_date=date(2026, 8, 8),
        source="manual",
        price="99",
    )
    market(
        db,
        complete,
        trade_date=date(2026, 8, 9),
        source="vendor",
        price="100",
    )
    winning_market = market(
        db,
        complete,
        trade_date=date(2026, 8, 9),
        source="moex",
        price="101",
    )
    market(
        db,
        partial,
        trade_date=date(2026, 6, 1),
        source="moex",
        price=None,
        ytm=None,
        duration="3",
        volume=None,
        liquidity=None,
        nkd=None,
        spread=None,
    )
    market(
        db,
        placeholder_bond,
        trade_date=date(2026, 8, 11),
        source="moex",
    )
    risk(db, complete, as_of=date(2026, 8, 9))
    risk(db, placeholder_bond, as_of=date(2026, 8, 9))
    db.add(
        BondCashflowEvent(
            bond_id=complete.id,
            event_date=date(2027, 1, 1),
            event_type="coupon",
            amount=Decimal("50"),
            currency="RUB",
            source="manual",
        )
    )
    db.add(
        FinancialReport(
            company_id=resolved.id,
            period_year=2025,
            period_quarter=0,
            revenue=Decimal("1000"),
            ebitda=Decimal("200"),
            total_debt=Decimal("300"),
            cash=Decimal("100"),
            signal="neutral",
            published_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
    )
    db.commit()
    controlled_value(db, company_id="RZD", metric_key="total_revenue", suffix="1")
    controlled_value(db, company_id="RZD", metric_key="revenue", suffix="2")
    controlled_value(db, company_id="RZD", metric_key="revenue_growth", suffix="3")
    return {
        "resolved": resolved,
        "complete": complete,
        "winning_market": winning_market,
        "partial": partial,
        "no_market": no_market,
        "placeholder_bond": placeholder_bond,
    }


def test_deterministic_coverage_latest_semantics_and_intersections(
    db_session: Session,
) -> None:
    audit = load_audit_module()
    fixture = synthetic_fixture(db_session)
    generated_at = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)

    first = audit.build_agent_data_readiness_audit(
        db_session,
        as_of_date=date(2026, 8, 10),
        generated_at=generated_at,
    )
    second = audit.build_agent_data_readiness_audit(
        db_session,
        as_of_date=date(2026, 8, 10),
        generated_at=generated_at,
    )

    assert first == second
    assert first["schema"] == "bondradar.agent_data_readiness_audit.v1"
    assert set(first) >= {
        "universe",
        "issuer_identity",
        "market",
        "bond_terms",
        "cashflows",
        "credit",
        "financial",
        "intersections",
        "readiness",
    }
    assert first["universe"]["companies_total"] == 3
    assert first["universe"]["bonds_total"] == 4
    assert first["issuer_identity"]["placeholder_issuer_bonds"] == 1
    assert first["issuer_identity"]["resolved_issuer_bonds"] == 3
    assert first["market"]["market_snapshots_total"] == 5
    assert first["market"]["bonds_with_any_market"] == 3
    assert first["market"]["bonds_without_market"] == 1
    assert first["market"]["bonds_latest_price_present"] == 2
    assert first["market"]["bonds_latest_ytm_present"] == 2
    assert first["market"]["bonds_latest_duration_present"] == 3
    assert first["market"]["bonds_market_core_complete"] == 2
    assert first["market"]["latest_market_age_buckets"]["future"] == 1
    assert first["market"]["latest_market_age_buckets"]["age_0_1_days"] == 1
    assert first["market"]["latest_market_age_buckets"]["age_31_90_days"] == 1
    assert first["market"]["latest_market_age_buckets"]["missing"] == 1
    assert first["cashflows"]["bonds_with_cashflows"] == 1
    assert first["credit"]["bonds_with_latest_risk"] == 2
    assert first["financial"]["financial_reports_count"] == 1
    assert first["financial"]["companies_with_financial_reports"] == 1
    assert first["financial"]["controlled_financial_rows_count"] == 3
    assert first["financial"]["controlled_metric_coverage"]["revenue"] == {
        "status": "MEASURABLE",
        "exact_metric_keys": ["revenue", "total_revenue"],
        "controlled_entity_count": 1,
    }
    assert first["financial"]["controlled_company_linkage"] == "NOT_MEASURABLE"
    assert first["intersections"]["bonds_with_resolved_issuer_and_market"] == 2
    assert first["intersections"]["bonds_with_resolved_issuer_and_market_core"] == 1
    assert first["intersections"]["bonds_with_resolved_issuer_market_and_risk"] == 1
    assert (
        first["intersections"][
            "bonds_with_resolved_issuer_market_risk_and_financial_report"
        ]
        == 1
    )
    assert first["intersections"][
        "bonds_with_resolved_issuer_market_risk_and_controlled_financials"
    ] == "NOT_MEASURABLE"
    assert first["readiness"]["domain_database_mutation_executed"] is False
    assert fixture["winning_market"].source == "moex"


def test_audit_is_read_only_and_calls_no_external_or_calculation_paths(
    db_session: Session,
    monkeypatch,
) -> None:
    audit = load_audit_module()
    synthetic_fixture(db_session)
    protected_models = (
        Company,
        Bond,
        BondMarketSnapshot,
        BondRiskAssessment,
        FinancialReport,
        BondCashflowEvent,
        ControlledFinancialStatementValue,
    )
    before = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in protected_models
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("audit called a forbidden calculation, network, or paper path")

    monkeypatch.setattr(BondRiskAssessmentService, "assess_bond", forbidden)
    monkeypatch.setattr(BondRiskAssessmentService, "recalculate_all", forbidden)
    monkeypatch.setattr(CompanyCreditHealthService, "calculate_for_company", forbidden)
    monkeypatch.setattr(BondScoreService, "calculate_for_bond", forbidden)
    monkeypatch.setattr(CompanyScoreService, "calculate_for_company", forbidden)
    monkeypatch.setattr(MoexIssClient, "fetch_history", forbidden)
    monkeypatch.setattr(LivePaperCycleService, "run", forbidden)

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        report = audit.build_agent_data_readiness_audit(
            db_session,
            as_of_date=date(2026, 8, 10),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    forbidden_sql = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE)\b",
        re.IGNORECASE,
    )
    assert statements
    assert all(not forbidden_sql.search(statement) for statement in statements)
    assert report["readiness"]["network_used"] is False
    assert report["readiness"]["moex_called"] is False
    assert report["readiness"]["recalculation_executed"] is False
    after = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in protected_models
    }
    assert after == before


def test_query_count_is_bounded_and_postgres_read_only_is_verified(
    db_session: Session,
) -> None:
    audit = load_audit_module()
    issuer = company(db_session, "SCALE")
    bond(db_session, issuer, 2400)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        audit.build_agent_data_readiness_audit(db_session, as_of_date=date(2026, 8, 10))
        small_count = len(statements)
        for index in range(20):
            bond(db_session, issuer, 2500 + index)
        statements.clear()
        audit.build_agent_data_readiness_audit(db_session, as_of_date=date(2026, 8, 10))
        large_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert large_count == small_count
    assert large_count <= 15

    calls: list[str] = []

    class FakeResult:
        def scalar_one(self):
            return "on"

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            calls.append(str(statement))
            return FakeResult()

    assert audit.enforce_read_only_transaction(FakeSession()) is True
    assert calls == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]


def test_cli_rendering_output_and_failure_are_sanitized(
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    audit = load_audit_module()
    synthetic_fixture(db_session)
    report = audit.build_agent_data_readiness_audit(
        db_session,
        as_of_date=date(2026, 8, 10),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    markdown = audit.render_markdown(report)
    assert "## Universe" in markdown
    assert "## Intersections" in markdown
    assert "DATABASE_URL" not in markdown

    class SessionContext:
        def __enter__(self):
            return db_session

        def __exit__(self, _type, _value, _traceback):
            return False

    import app.db.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", SessionContext)
    output = tmp_path / "audit.json"
    assert audit.main(["--format", "json", "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == audit.SCHEMA

    def fail(*_args, **_kwargs):
        raise RuntimeError("DATABASE_URL=secret-value")

    monkeypatch.setattr(audit, "build_agent_data_readiness_audit", fail)
    assert audit.main(["--format", "json"]) == 1
    captured = capsys.readouterr()
    assert "secret-value" not in captured.err
    assert json.loads(captured.err) == {
        "error": "agent_data_readiness_audit_failed",
        "schema": audit.SCHEMA,
        "status": "failed",
    }
