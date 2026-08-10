from __future__ import annotations

import importlib.util
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.schemas.pilot_universe import PilotUniverseEvaluationRequest
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.bond_score_service import BondScoreService
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.company_scoring import CompanyScoreService
from app.services.moex_iss_client import MoexIssClient
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_service import PaperTradingService
from app.services.pilot_universe_service import (
    SYSTEM_CAPABILITY_BLOCKERS,
    PilotUniverseService,
)


AS_OF = date(2026, 8, 10)
REQUIRED_MARKET = date(2026, 8, 7)


def load_cli_module() -> Any:
    path = Path(__file__).parents[2] / "scripts" / "pilot_universe_contract_audit.py"
    spec = importlib.util.spec_from_file_location("pilot_universe_contract_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_company(
    db: Session,
    suffix: int,
    *,
    name: str | None = None,
    inn: str | None = None,
) -> Company:
    company = Company(
        name=name or f"Issuer {suffix}",
        ticker=f"PU{suffix}",
        inn=inn if inn is not None else f"{suffix:010d}",
        country="RU",
        signal="neutral",
    )
    db.add(company)
    db.flush()
    return company


def add_profile(
    db: Session,
    company: Company,
    *,
    status: str = "verified",
    review: str = "accepted",
    role: str = "legal_issuer",
    inn: str | None = None,
) -> CompanyIdentityProfile:
    profile = CompanyIdentityProfile(
        company_id=company.id,
        inn=company.inn if inn is None else inn,
        issuer_role=role,
        identity_status=status,
        identity_source="manual_review",
        review_status=review,
    )
    db.add(profile)
    db.flush()
    return profile


def add_bond(db: Session, company: Company, suffix: int, **overrides: Any) -> Bond:
    values: dict[str, Any] = {
        "company_id": company.id,
        "isin": f"RU{suffix:010d}",
        "secid": f"SEC{suffix}",
        "name": f"Bond {suffix}",
        "currency": "RUB",
        "nominal_value": Decimal("1000.00"),
        "current_price": None,
        "coupon_rate": Decimal("10.000"),
        "yield_to_maturity": None,
        "duration_years": None,
        "volume": None,
        "maturity_date": date(2027, 8, 10),
        "offer_date": None,
        "is_floating_coupon": False,
        "is_subordinated": False,
        "is_perpetual": False,
        "amortization": False,
        "liquidity_score": None,
        "signal": "neutral",
    }
    values.update(overrides)
    bond = Bond(**values)
    db.add(bond)
    db.flush()
    return bond


def add_market(
    db: Session,
    bond: Bond,
    *,
    trade_date: date = REQUIRED_MARKET,
    source: str = "moex",
    **overrides: Any,
) -> BondMarketSnapshot:
    values: dict[str, Any] = {
        "bond_id": bond.id,
        "trade_date": trade_date,
        "source": source,
        "price": Decimal("99.1"),
        "clean_price": None,
        "dirty_price": Decimal("100.2"),
        "nkd": None,
        "yield_to_maturity": Decimal("12.000"),
        "duration_years": Decimal("2.000"),
        "volume": Decimal("100000.00"),
        "liquidity_score": 70,
        "spread_to_ofz": Decimal("2.100000"),
    }
    values.update(overrides)
    snapshot = BondMarketSnapshot(**values)
    db.add(snapshot)
    db.flush()
    return snapshot


def add_cashflow(
    db: Session,
    bond: Bond,
    event_date: date,
    event_type: str,
    *,
    source: str = "moex",
) -> BondCashflowEvent:
    row = BondCashflowEvent(
        bond_id=bond.id,
        event_date=event_date,
        event_type=event_type,
        amount=Decimal("50.00"),
        currency="RUB",
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def add_ready_bond(db: Session, suffix: int) -> Bond:
    company = add_company(db, suffix)
    add_profile(db, company)
    bond = add_bond(db, company, suffix)
    add_market(db, bond)
    add_cashflow(db, bond, date(2026, 12, 1), "coupon")
    add_cashflow(db, bond, bond.maturity_date, "redemption")
    return bond


def evaluate(db: Session, *, sample_limit: int = 20):
    return PilotUniverseService(db).evaluate(
        PilotUniverseEvaluationRequest(
            as_of_date=AS_OF,
            required_market_trade_date=REQUIRED_MARKET,
            sample_limit=sample_limit,
        )
    )


def by_id(result, bond: Bond):
    return next(row for row in result.bond_evaluations if row.bond_id == bond.id)


def test_identity_and_legacy_terms_are_strict_and_fail_closed(
    db_session: Session,
) -> None:
    passing_company = add_company(db_session, 1001, inn=" 1234567890 ")
    add_profile(db_session, passing_company, inn="1234567890")
    passing = add_bond(db_session, passing_company, 1001)

    placeholder_company = add_company(
        db_session,
        1002,
        name="Unknown issuer for RU0000001002",
    )
    add_profile(db_session, placeholder_company)
    placeholder = add_bond(db_session, placeholder_company, 1002)

    identity_cases: list[tuple[Bond, str]] = []
    for suffix, status in ((1010, "weak"), (1011, "matched"), (1012, "conflict")):
        company = add_company(db_session, suffix)
        add_profile(db_session, company, status=status)
        identity_cases.append((add_bond(db_session, company, suffix), "IDENTITY_STATUS_NOT_VERIFIED"))
    missing_profile_company = add_company(db_session, 1013)
    identity_cases.append(
        (add_bond(db_session, missing_profile_company, 1013), "IDENTITY_PROFILE_MISSING")
    )
    pending_company = add_company(db_session, 1014)
    add_profile(db_session, pending_company, review="pending")
    identity_cases.append(
        (add_bond(db_session, pending_company, 1014), "IDENTITY_REVIEW_NOT_ACCEPTED")
    )
    mismatch_company = add_company(db_session, 1015)
    add_profile(db_session, mismatch_company, inn="9999999999")
    identity_cases.append(
        (add_bond(db_session, mismatch_company, 1015), "ISSUER_INN_MISMATCH")
    )
    for suffix, role in (
        (1016, "spv"),
        (1017, "finance_subsidiary"),
        (1018, "unknown"),
    ):
        company = add_company(db_session, suffix)
        add_profile(db_session, company, role=role)
        identity_cases.append((add_bond(db_session, company, suffix), "ISSUER_ROLE_UNSUPPORTED"))

    terms_company = add_company(db_session, 1100)
    add_profile(db_session, terms_company)
    terms_cases = {
        "CURRENCY_NOT_RUB": add_bond(db_session, terms_company, 1101, currency="USD"),
        "ISIN_MISSING": add_bond(db_session, terms_company, 1102, isin=None),
        "SECID_MISSING": add_bond(db_session, terms_company, 1103, secid=None),
        "NOMINAL_MISSING": add_bond(db_session, terms_company, 1104, nominal_value=None),
        "NOMINAL_NON_POSITIVE": add_bond(
            db_session, terms_company, 1105, nominal_value=Decimal("0")
        ),
        "COUPON_RATE_MISSING": add_bond(db_session, terms_company, 1106, coupon_rate=None),
        "COUPON_RATE_NON_POSITIVE": add_bond(
            db_session, terms_company, 1107, coupon_rate=Decimal("0")
        ),
        "MATURITY_MISSING": add_bond(db_session, terms_company, 1108, maturity_date=None),
        "MATURITY_NOT_FUTURE": add_bond(
            db_session, terms_company, 1109, maturity_date=AS_OF
        ),
        "FLOATING_COUPON_UNSUPPORTED": add_bond(
            db_session, terms_company, 1110, is_floating_coupon=True
        ),
        "SUBORDINATED_UNSUPPORTED": add_bond(
            db_session, terms_company, 1111, is_subordinated=True
        ),
        "PERPETUAL_UNSUPPORTED": add_bond(
            db_session, terms_company, 1112, is_perpetual=True
        ),
        "AMORTIZING_UNSUPPORTED": add_bond(
            db_session, terms_company, 1113, amortization=None
        ),
        "OFFER_BOND_UNSUPPORTED": add_bond(
            db_session, terms_company, 1114, offer_date=date(2027, 1, 1)
        ),
    }

    result = evaluate(db_session)
    assert by_id(result, passing).identity_gate == "PASS"
    assert by_id(result, passing).legacy_terms_gate == "PASS"
    assert by_id(result, placeholder).identity_blockers == ["ISSUER_PLACEHOLDER_NAME"]
    for bond, expected in identity_cases:
        row = by_id(result, bond)
        assert row.identity_gate == "FAIL"
        assert expected in row.identity_blockers
    for expected, bond in terms_cases.items():
        row = by_id(result, bond)
        assert row.legacy_terms_gate == "FAIL"
        assert expected in row.legacy_terms_blockers

    with pytest.raises(ValidationError):
        PilotUniverseEvaluationRequest(
            as_of_date=AS_OF,
            required_market_trade_date=date(2026, 8, 11),
        )
    with pytest.raises(ValidationError):
        PilotUniverseEvaluationRequest(
            as_of_date=AS_OF,
            required_market_trade_date=REQUIRED_MARKET,
            sample_limit=0,
            allow_stale_market=True,
        )


def test_market_selector_and_completeness_never_use_bond_fallback(
    db_session: Session,
) -> None:
    company = add_company(db_session, 2000)
    add_profile(db_session, company)

    latest_date_bond = add_bond(db_session, company, 2001)
    add_market(db_session, latest_date_bond, trade_date=REQUIRED_MARKET, source="moex")
    add_market(
        db_session,
        latest_date_bond,
        trade_date=date(2026, 8, 8),
        source="manual",
    )

    priority_bond = add_bond(db_session, company, 2002)
    add_market(db_session, priority_bond, source="vendor", yield_to_maturity=None)
    add_market(db_session, priority_bond, source="moex")

    highest_id_bond = add_bond(db_session, company, 2003)
    add_market(db_session, highest_id_bond, source="vendor")
    add_market(db_session, highest_id_bond, source="manual", yield_to_maturity=None)

    fallback_bond = add_bond(
        db_session,
        company,
        2004,
        current_price=Decimal("101"),
        yield_to_maturity=Decimal("11"),
        duration_years=Decimal("2"),
        volume=Decimal("1000"),
        liquidity_score=90,
    )
    add_market(
        db_session,
        fallback_bond,
        price=Decimal("99"),
        dirty_price=None,
        yield_to_maturity=None,
        duration_years=None,
        volume=None,
        liquidity_score=None,
        spread_to_ofz=None,
    )

    stale_bond = add_bond(db_session, company, 2005)
    add_market(db_session, stale_bond, trade_date=date(2026, 8, 6))
    clean_nkd_bond = add_bond(db_session, company, 2006)
    add_market(
        db_session,
        clean_nkd_bond,
        dirty_price=None,
        clean_price=Decimal("98.1"),
        nkd=Decimal("1.3"),
    )
    incomplete_bond = add_bond(db_session, company, 2007)
    add_market(
        db_session,
        incomplete_bond,
        dirty_price=None,
        clean_price=Decimal("98.1"),
        nkd=None,
        yield_to_maturity=None,
        duration_years=Decimal("0"),
        volume=Decimal("-1"),
        liquidity_score=None,
        spread_to_ofz=None,
    )

    result = evaluate(db_session)
    assert by_id(result, latest_date_bond).market_blockers == ["MARKET_SOURCE_NOT_MOEX"]
    assert by_id(result, priority_bond).market_gate == "PASS"
    assert by_id(result, highest_id_bond).market_blockers == [
        "MARKET_SOURCE_NOT_MOEX",
        "YTM_MISSING",
    ]
    assert by_id(result, fallback_bond).market_blockers == [
        "EXECUTABLE_PRICE_MISSING",
        "YTM_MISSING",
        "DURATION_MISSING",
        "VOLUME_MISSING",
        "LIQUIDITY_MISSING",
        "SPREAD_TO_OFZ_MISSING",
    ]
    assert by_id(result, stale_bond).market_blockers == ["MARKET_SNAPSHOT_STALE"]
    assert by_id(result, clean_nkd_bond).market_gate == "PASS"
    assert by_id(result, incomplete_bond).market_blockers == [
        "EXECUTABLE_PRICE_MISSING",
        "YTM_MISSING",
        "DURATION_NON_POSITIVE",
        "VOLUME_MISSING",
        "LIQUIDITY_MISSING",
        "SPREAD_TO_OFZ_MISSING",
    ]


def test_cashflow_semantics_candidate_intersection_and_determinism(
    db_session: Session,
) -> None:
    candidate = add_ready_bond(db_session, 3001)

    missing_company = add_company(db_session, 3002)
    add_profile(db_session, missing_company, status="matched")
    identity_fail = add_bond(db_session, missing_company, 3002)
    add_market(db_session, identity_fail)
    add_cashflow(db_session, identity_fail, date(2026, 12, 1), "coupon")
    add_cashflow(db_session, identity_fail, identity_fail.maturity_date, "redemption")

    cashflow_company = add_company(db_session, 3010)
    add_profile(db_session, cashflow_company)
    missing_events = add_bond(db_session, cashflow_company, 3011)
    add_market(db_session, missing_events)

    wrong_redemption = add_bond(db_session, cashflow_company, 3012)
    add_market(db_session, wrong_redemption)
    add_cashflow(db_session, wrong_redemption, date(2026, 12, 1), "coupon")
    add_cashflow(db_session, wrong_redemption, date(2027, 1, 1), "redemption")

    offer_only = add_bond(db_session, cashflow_company, 3013)
    add_market(db_session, offer_only)
    add_cashflow(db_session, offer_only, date(2026, 12, 1), "coupon")
    add_cashflow(db_session, offer_only, offer_only.maturity_date, "offer_redemption")

    ambiguous = add_bond(db_session, cashflow_company, 3014)
    add_market(db_session, ambiguous)
    add_cashflow(db_session, ambiguous, date(2026, 12, 1), "coupon", source="moex")
    add_cashflow(db_session, ambiguous, date(2026, 12, 1), "coupon", source="manual")
    add_cashflow(db_session, ambiguous, ambiguous.maturity_date, "redemption")

    unexpected = add_bond(db_session, cashflow_company, 3015)
    add_market(db_session, unexpected)
    add_cashflow(db_session, unexpected, date(2026, 12, 1), "coupon")
    add_cashflow(db_session, unexpected, unexpected.maturity_date, "redemption")
    add_cashflow(db_session, unexpected, date(2027, 2, 1), "amortization")

    first = evaluate(db_session, sample_limit=2)
    second = evaluate(db_session, sample_limit=2)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert by_id(first, candidate).observed_cashflow_gate == "PASS"
    assert by_id(first, missing_events).observed_cashflow_gate == "NOT_PROVEN"
    assert by_id(first, missing_events).cashflow_blockers == [
        "FUTURE_COUPON_MISSING",
        "MATURITY_REDEMPTION_MISSING",
    ]
    assert by_id(first, wrong_redemption).cashflow_blockers == [
        "MATURITY_REDEMPTION_MISSING"
    ]
    assert by_id(first, offer_only).observed_cashflow_gate == "FAIL"
    assert by_id(first, offer_only).cashflow_blockers == [
        "MATURITY_REDEMPTION_MISSING",
        "UNEXPECTED_OFFER_REDEMPTION_EVENT",
    ]
    assert by_id(first, ambiguous).cashflow_blockers == [
        "CASHFLOW_ECONOMIC_EVENT_AMBIGUOUS"
    ]
    assert by_id(first, unexpected).cashflow_blockers == [
        "UNEXPECTED_AMORTIZATION_EVENT"
    ]
    assert first.summary.pre_pilot_data_candidate_count == 1
    assert first.summary.final_pilot_eligible_count == 0
    assert first.summary.final_pilot_eligibility_evaluated is False
    assert all(not row.final_pilot_eligibility for row in first.bond_evaluations)
    assert list(first.summary.system_capability_blockers) == list(
        SYSTEM_CAPABILITY_BLOCKERS
    )
    assert first.summary.pre_pilot_candidate_samples[0].bond_id == candidate.id
    assert len(first.summary.excluded_bond_samples) == 2
    assert by_id(first, identity_fail).pre_pilot_data_candidate is False


def test_service_is_read_only_bounded_and_calls_no_external_paths(
    db_session: Session,
    monkeypatch,
) -> None:
    add_ready_bond(db_session, 4001)
    protected_models = (
        Company,
        CompanyIdentityProfile,
        Bond,
        BondMarketSnapshot,
        BondCashflowEvent,
    )
    before = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in protected_models
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Task238 called a forbidden external or mutation path")

    monkeypatch.setattr(MoexIssClient, "fetch_history", forbidden)
    monkeypatch.setattr(BondRiskAssessmentService, "assess_bond", forbidden)
    monkeypatch.setattr(BondRiskAssessmentService, "recalculate_all", forbidden)
    monkeypatch.setattr(CompanyCreditHealthService, "calculate_for_company", forbidden)
    monkeypatch.setattr(BondScoreService, "calculate_for_bond", forbidden)
    monkeypatch.setattr(CompanyScoreService, "calculate_for_company", forbidden)
    monkeypatch.setattr(PaperTradingService, "rebalance", forbidden)
    monkeypatch.setattr(LivePaperCycleService, "run", forbidden)

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        evaluate(db_session)
        small_count = len(statements)
        after_small_evaluation = {
            model: db_session.scalar(select(func.count()).select_from(model))
            for model in protected_models
        }
        assert after_small_evaluation == before
        for suffix in range(4010, 4030):
            add_ready_bond(db_session, suffix)
        after_large_seed = {
            model: db_session.scalar(select(func.count()).select_from(model))
            for model in protected_models
        }
        statements.clear()
        evaluate(db_session)
        large_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert small_count == 3
    assert large_count == small_count
    forbidden_sql = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE)\b",
        re.IGNORECASE,
    )
    assert all(not forbidden_sql.search(statement) for statement in statements)
    after = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in protected_models
    }
    assert after == after_large_seed


def test_cli_read_only_projection_rendering_and_failure_sanitization(
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    cli = load_cli_module()
    candidate = add_ready_bond(db_session, 5001)
    candidate.company.name = "Secret Company Name"
    db_session.flush()
    result = evaluate(db_session)

    calls: list[str] = []

    class FakeResult:
        def scalar_one(self):
            return "on"

    class FakePostgresSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            calls.append(str(statement))
            return FakeResult()

    assert cli.enforce_read_only_transaction(FakePostgresSession()) is True
    assert calls == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]

    projection = cli.build_cli_projection(result)
    encoded = json.dumps(projection, ensure_ascii=False)
    assert "Secret Company Name" not in encoded
    assert "bond_evaluations" not in projection
    assert projection["contract_version"] == "pilot-universe-v1"
    markdown = cli.serialize_report(projection, "markdown")
    assert "Final pilot eligible: 0 (not evaluated)" in markdown
    assert "Secret Company Name" not in markdown

    class Context:
        def __enter__(self):
            return db_session

        def __exit__(self, _type, _value, _traceback):
            return False

    import app.db.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", lambda: Context())
    monkeypatch.setattr(cli.PilotUniverseService, "evaluate", lambda _self, _request: result)
    output = tmp_path / "task238.json"
    assert cli.main(
        [
            "--as-of-date",
            AS_OF.isoformat(),
            "--required-market-date",
            REQUIRED_MARKET.isoformat(),
            "--format",
            "json",
            "--output",
            str(output),
        ]
    ) == 0
    written = output.read_text(encoding="utf-8")
    assert "Secret Company Name" not in written
    assert "bond_evaluations" not in written

    def secret_failure():
        raise RuntimeError("DATABASE_URL=postgresql://user:super-secret@production")

    monkeypatch.setattr(session_module, "SessionLocal", secret_failure)
    assert cli.main(
        [
            "--as-of-date",
            AS_OF.isoformat(),
            "--required-market-date",
            REQUIRED_MARKET.isoformat(),
        ]
    ) == 1
    captured = capsys.readouterr()
    assert "super-secret" not in captured.err
    assert "production" not in captured.err
    assert "pilot_universe_contract_audit_failed" in captured.err
