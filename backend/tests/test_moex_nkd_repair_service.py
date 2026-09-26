from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.services.moex_nkd_repair_service import MoexNkdRepairService


TRADE_DATE = date(2026, 1, 10)


def make_bond(db: Session, suffix: str = "1", *, secid: str | None = None) -> Bond:
    company = Company(
        name=f"NKD Repair Issuer {suffix}",
        ticker=f"NKDR{suffix}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    bond = Bond(
        company_id=company.id,
        isin=f"RUNKDR{suffix}",
        secid=secid or f"NKDR{suffix}",
        name=f"NKD Repair Bond {suffix}",
        currency="RUB",
        nominal_value=Decimal("1000"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def make_snapshot(
    db: Session,
    bond: Bond,
    *,
    raw_payload: object | None = None,
    source: str = "moex",
    nkd: Decimal | None = None,
) -> BondMarketSnapshot:
    snapshot = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=TRADE_DATE,
        source=source,
        price=Decimal("101.25"),
        clean_price=Decimal("100.50"),
        dirty_price=None,
        nkd=nkd,
        yield_to_maturity=Decimal("12.345"),
        duration_years=Decimal("2.125"),
        volume=Decimal("123456.78"),
        liquidity_score=41,
        spread_to_ofz=Decimal("1.25"),
        raw_payload=raw_payload,  # type: ignore[arg-type]
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def valid_raw(bond: Bond, **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "SECID": bond.secid,
        "TRADEDATE": TRADE_DATE.isoformat(),
        "ACCINT": "31.560",
    }
    payload.update(updates)
    return {"moex": payload}


def only_result(batch):
    assert len(batch.results) == 1
    return batch.results[0]


@pytest.mark.parametrize(
    "raw_values,expected",
    [
        ({"ACCINT": 31.56}, Decimal("31.56")),
        ({"ACCINT": "31.560"}, Decimal("31.560")),
        ({"ACCINT": 0}, Decimal("0")),
        ({"ACCINT": None, "ACCRUEDINT": "0.000"}, Decimal("0.000")),
        ({"ACCINT": "31.560", "ACCRUEDINT": 31.56}, Decimal("31.560")),
        ({"accint": "31.56", "accruedint": "31.5600"}, Decimal("31.56")),
    ],
)
def test_preview_and_apply_use_safe_source_evidence(
    db_session: Session,
    raw_values: dict[str, object],
    expected: Decimal,
) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond, **raw_values))
    service = MoexNkdRepairService(db_session)
    before_raw = deepcopy(snapshot.raw_payload)

    preview = only_result(service.preview([snapshot.id]))
    assert preview.status == "SAFE_TO_REPAIR"
    assert preview.current_nkd is None
    assert preview.proposed_nkd == expected
    assert snapshot.nkd is None
    assert snapshot.raw_payload == before_raw

    applied = only_result(service.apply([snapshot]))
    assert applied.status == "REPAIRED"
    assert applied.current_nkd == expected
    assert snapshot.nkd == expected
    assert snapshot.raw_payload == before_raw

    repeated = only_result(service.apply([snapshot.id]))
    assert repeated.status == "ALREADY_POPULATED"
    assert snapshot.nkd == expected


@pytest.mark.parametrize(
    "raw_values,expected_status",
    [
        ({"ACCINT": "31.56", "ACCRUEDINT": "30"}, "NKD_ALIAS_CONFLICT"),
        ({"ACCINT": "broken", "ACCRUEDINT": "31.56"}, "NKD_INVALID"),
        ({"ACCINT": True}, "NKD_INVALID"),
        ({"ACCINT": -1}, "NKD_INVALID"),
        ({"ACCINT": "NaN"}, "NKD_INVALID"),
        ({"ACCINT": "Infinity"}, "NKD_INVALID"),
        ({"ACCINT": "  "}, "NKD_MISSING"),
        ({"ACCINT": None}, "NKD_MISSING"),
    ],
)
def test_preview_blocks_missing_invalid_or_conflicting_nkd(
    db_session: Session,
    raw_values: dict[str, object],
    expected_status: str,
) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond, **raw_values))

    result = only_result(MoexNkdRepairService(db_session).preview([snapshot.id]))

    assert result.status == expected_status
    assert result.proposed_nkd is None
    assert expected_status in result.flags
    assert snapshot.nkd is None


@pytest.mark.parametrize(
    "source,raw_payload,existing_nkd,expected_status",
    [
        ("manual", None, None, "SOURCE_NOT_MOEX"),
        ("moex", None, None, "RAW_PAYLOAD_MISSING"),
        ("moex", [], None, "RAW_PAYLOAD_INVALID"),
        ("moex", {"canonical": {}}, None, "MOEX_PAYLOAD_MISSING"),
        ("moex", {"moex": []}, None, "MOEX_PAYLOAD_INVALID"),
        ("moex", {"moex": {"ACCINT": "2"}}, Decimal("7"), "ALREADY_POPULATED"),
    ],
)
def test_preview_respects_source_payload_and_existing_nkd_gates(
    db_session: Session,
    source: str,
    raw_payload: object,
    existing_nkd: Decimal | None,
    expected_status: str,
) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(
        db_session,
        bond,
        source=source,
        raw_payload=raw_payload,
        nkd=existing_nkd,
    )

    result = only_result(MoexNkdRepairService(db_session).preview([snapshot.id]))

    assert result.status == expected_status
    if existing_nkd is not None:
        assert result.current_nkd == existing_nkd
        assert result.proposed_nkd is None
        assert snapshot.nkd == existing_nkd


@pytest.mark.parametrize(
    "raw_updates,expected_status",
    [
        ({"SECID": None}, "RAW_SECID_MISSING"),
        ({"SECID": 123}, "RAW_SECID_INVALID"),
        ({"SECID": "OTHER"}, "SECID_MISMATCH"),
        ({"TRADEDATE": None}, "RAW_TRADE_DATE_MISSING"),
        ({"TRADEDATE": "not-a-date"}, "RAW_TRADE_DATE_INVALID"),
        ({"TRADEDATE": "2026-01-11"}, "TRADE_DATE_MISMATCH"),
    ],
)
def test_preview_requires_raw_security_and_trade_date_identity(
    db_session: Session,
    raw_updates: dict[str, object],
    expected_status: str,
) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond, **raw_updates))

    result = only_result(MoexNkdRepairService(db_session).preview([snapshot.id]))

    assert result.status == expected_status
    assert expected_status in result.flags
    assert result.proposed_nkd is None


def test_unrelated_bond_security_identity_cannot_repair_snapshot(db_session: Session) -> None:
    attached_bond = make_bond(db_session, "A")
    unrelated_bond = make_bond(db_session, "B")
    snapshot = make_snapshot(db_session, attached_bond, raw_payload=valid_raw(unrelated_bond))

    result = only_result(MoexNkdRepairService(db_session).preview([snapshot.id]))

    assert result.status == "SECID_MISMATCH"
    assert "SECID_MISMATCH" in result.flags


def test_missing_attached_bond_blocks_repair(db_session: Session) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond))

    result = MoexNkdRepairService._evaluate_snapshot(snapshot, None)

    assert result.status == "BOND_MISSING"
    assert "BOND_MISSING" in result.flags
    assert result.proposed_nkd is None


def test_missing_snapshot_is_reported_without_universe_discovery(db_session: Session) -> None:
    result = only_result(MoexNkdRepairService(db_session).preview([987654]))

    assert result.status == "SNAPSHOT_NOT_FOUND"
    assert result.snapshot_id == 987654
    assert result.bond_id is None


@pytest.mark.parametrize(
    "refs_factory",
    [
        lambda: [],
        lambda: (),
        lambda: "1",
        lambda: b"1",
        lambda: {1},
        lambda: iter([1]),
        lambda: [True],
        lambda: [0],
        lambda: [-1],
        lambda: [1.0],
        lambda: [object()],
        lambda: [1, 1],
    ],
)
def test_invalid_references_are_rejected_before_sql(
    db_session: Session,
    refs_factory,
) -> None:
    statements: list[str] = []
    engine = db_session.get_bind()
    listener = lambda conn, cursor, statement, params, context, executemany: statements.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        with pytest.raises(ValueError):
            MoexNkdRepairService(db_session).preview(refs_factory())
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert statements == []


def test_transient_snapshot_instance_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError):
        MoexNkdRepairService(db_session).preview([BondMarketSnapshot()])


def test_preview_is_select_only_and_preserves_pending_caller_state(
    db_session: Session,
) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond))
    pending = Company(
        name="Pending preview company",
        ticker="PEND294",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db_session.add(pending)
    db_session.autoflush = True
    statements: list[str] = []
    engine = db_session.get_bind()
    listener = lambda conn, cursor, statement, params, context, executemany: statements.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = only_result(MoexNkdRepairService(db_session).preview([snapshot.id]))
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert result.status == "SAFE_TO_REPAIR"
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
    assert pending in db_session.new
    assert pending.id is None
    assert snapshot.nkd is None


def test_apply_changes_only_nkd_and_leaves_rollback_to_caller(db_session: Session) -> None:
    bond = make_bond(db_session)
    snapshot = make_snapshot(db_session, bond, raw_payload=valid_raw(bond))
    before = {
        field: deepcopy(getattr(snapshot, field))
        for field in (
            "price",
            "clean_price",
            "dirty_price",
            "yield_to_maturity",
            "duration_years",
            "volume",
            "liquidity_score",
            "spread_to_ofz",
            "trade_date",
            "source",
            "bond_id",
            "raw_payload",
            "created_at",
        )
    }
    pending = Company(
        name="Pending apply company",
        ticker="PEND295",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    bond.name = "Caller-owned dirty name"
    db_session.add(pending)
    db_session.autoflush = True
    statements: list[str] = []
    engine = db_session.get_bind()
    listener = lambda conn, cursor, statement, params, context, executemany: statements.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = only_result(MoexNkdRepairService(db_session).apply([snapshot.id]))
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert result.status == "REPAIRED"
    assert result.current_nkd == Decimal("31.560")
    assert snapshot.nkd == Decimal("31.560")
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
    assert pending in db_session.new
    assert pending.id is None
    assert bond in db_session.dirty
    assert snapshot in db_session.dirty
    for field, value in before.items():
        assert getattr(snapshot, field) == value

    db_session.rollback()
    db_session.refresh(snapshot)
    assert snapshot.nkd is None
    assert snapshot.price == before["price"]
    assert snapshot.raw_payload == before["raw_payload"]


def test_service_has_no_network_or_moex_client_boundary() -> None:
    from app.services import moex_nkd_repair_service

    tree = ast.parse(Path(moex_nkd_repair_service.__file__).read_text(encoding="utf-8"))
    forbidden = {"httpx", "requests", "urllib", "MoexIssClient"}
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    referenced = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    assert not (imports & forbidden)
    assert not (referenced & forbidden)
    assert "commit" not in referenced
    assert "flush" not in referenced
