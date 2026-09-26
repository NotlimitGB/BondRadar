from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot


RepairStatus = Literal[
    "SAFE_TO_REPAIR",
    "REPAIRED",
    "ALREADY_POPULATED",
    "SNAPSHOT_NOT_FOUND",
    "SOURCE_NOT_MOEX",
    "RAW_PAYLOAD_MISSING",
    "RAW_PAYLOAD_INVALID",
    "MOEX_PAYLOAD_MISSING",
    "MOEX_PAYLOAD_INVALID",
    "NKD_MISSING",
    "NKD_INVALID",
    "NKD_ALIAS_CONFLICT",
    "BOND_MISSING",
    "RAW_SECID_MISSING",
    "RAW_SECID_INVALID",
    "BOND_SECID_MISSING",
    "SECID_MISMATCH",
    "RAW_TRADE_DATE_MISSING",
    "RAW_TRADE_DATE_INVALID",
    "TRADE_DATE_MISMATCH",
]


@dataclass(frozen=True, slots=True)
class NkdRepairResult:
    snapshot_id: int
    bond_id: int | None
    status: RepairStatus
    current_nkd: Decimal | None = None
    proposed_nkd: Decimal | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NkdRepairBatchResult:
    requested_count: int
    results: tuple[NkdRepairResult, ...]

    @property
    def safe_to_repair_count(self) -> int:
        return sum(result.status == "SAFE_TO_REPAIR" for result in self.results)

    @property
    def repaired_count(self) -> int:
        return sum(result.status == "REPAIRED" for result in self.results)

    @property
    def blocked_count(self) -> int:
        return sum(
            result.status not in {"SAFE_TO_REPAIR", "REPAIRED"}
            for result in self.results
        )


class MoexNkdRepairService:
    """Evaluate or apply repairs from raw evidence already stored per snapshot."""

    _NKD_ALIASES = {"accint", "accruedint"}
    _STATUS_PRECEDENCE: tuple[str, ...] = (
        "SOURCE_NOT_MOEX",
        "RAW_PAYLOAD_MISSING",
        "RAW_PAYLOAD_INVALID",
        "MOEX_PAYLOAD_MISSING",
        "MOEX_PAYLOAD_INVALID",
        "NKD_ALIAS_CONFLICT",
        "NKD_MISSING",
        "NKD_INVALID",
        "BOND_MISSING",
        "RAW_SECID_MISSING",
        "RAW_SECID_INVALID",
        "BOND_SECID_MISSING",
        "SECID_MISMATCH",
        "RAW_TRADE_DATE_MISSING",
        "RAW_TRADE_DATE_INVALID",
        "TRADE_DATE_MISMATCH",
    )

    def __init__(self, db: Session) -> None:
        self.db = db

    def preview(
        self, refs: Sequence[int | BondMarketSnapshot]
    ) -> NkdRepairBatchResult:
        normalized = self._validate_refs(refs)
        evaluated, _ = self._evaluate(normalized)
        return NkdRepairBatchResult(
            requested_count=len(normalized),
            results=tuple(result for _, result in evaluated),
        )

    def apply(
        self, refs: Sequence[int | BondMarketSnapshot]
    ) -> NkdRepairBatchResult:
        normalized = self._validate_refs(refs)
        evaluated, safe_rows = self._evaluate(normalized)
        repaired_by_id: dict[int, Decimal] = {}
        for snapshot, value in safe_rows:
            snapshot.nkd = value
            repaired_by_id[snapshot.id] = value

        results = tuple(
            replace(
                result,
                status="REPAIRED",
                current_nkd=repaired_by_id[result.snapshot_id],
                proposed_nkd=repaired_by_id[result.snapshot_id],
            )
            if result.snapshot_id in repaired_by_id
            else result
            for _, result in evaluated
        )
        return NkdRepairBatchResult(
            requested_count=len(normalized),
            results=results,
        )

    @classmethod
    def _validate_refs(
        cls, refs: Sequence[int | BondMarketSnapshot]
    ) -> tuple[int, ...]:
        if (
            isinstance(refs, (str, bytes, bytearray, Mapping, set, frozenset))
            or not isinstance(refs, Sequence)
            or not refs
        ):
            raise ValueError("refs must be a non-empty deterministic sequence")

        snapshot_ids: list[int] = []
        for item in tuple(refs):
            if type(item) is int:
                snapshot_id = item
            elif isinstance(item, BondMarketSnapshot):
                snapshot_id = item.id
            else:
                raise ValueError("refs must contain snapshot IDs or snapshot instances")
            if type(snapshot_id) is not int or snapshot_id <= 0:
                raise ValueError("snapshot references must have positive exact integer IDs")
            snapshot_ids.append(snapshot_id)

        if len(set(snapshot_ids)) != len(snapshot_ids):
            raise ValueError("duplicate snapshot references are not allowed")
        return tuple(sorted(snapshot_ids))

    def _evaluate(
        self, snapshot_ids: tuple[int, ...]
    ) -> tuple[
        list[tuple[BondMarketSnapshot | None, NkdRepairResult]],
        list[tuple[BondMarketSnapshot, Decimal]],
    ]:
        statement = (
            select(BondMarketSnapshot, Bond)
            .select_from(BondMarketSnapshot)
            .outerjoin(Bond, Bond.id == BondMarketSnapshot.bond_id)
            .where(BondMarketSnapshot.id.in_(snapshot_ids))
            .order_by(BondMarketSnapshot.id)
        )
        with self.db.no_autoflush:
            rows = self.db.execute(statement).all()

        by_id = {
            snapshot.id: (snapshot, bond)
            for snapshot, bond in rows
        }
        evaluated: list[tuple[BondMarketSnapshot | None, NkdRepairResult]] = []
        safe_rows: list[tuple[BondMarketSnapshot, Decimal]] = []
        for snapshot_id in snapshot_ids:
            pair = by_id.get(snapshot_id)
            if pair is None:
                evaluated.append(
                    (
                        None,
                        NkdRepairResult(
                            snapshot_id=snapshot_id,
                            bond_id=None,
                            status="SNAPSHOT_NOT_FOUND",
                            flags=("SNAPSHOT_NOT_FOUND",),
                        ),
                    )
                )
                continue

            snapshot, bond = pair
            result = self._evaluate_snapshot(snapshot, bond)
            evaluated.append((snapshot, result))
            if result.status == "SAFE_TO_REPAIR" and result.proposed_nkd is not None:
                safe_rows.append((snapshot, result.proposed_nkd))
        return evaluated, safe_rows

    @classmethod
    def _evaluate_snapshot(
        cls, snapshot: BondMarketSnapshot, bond: Bond | None
    ) -> NkdRepairResult:
        if snapshot.nkd is not None:
            return NkdRepairResult(
                snapshot_id=snapshot.id,
                bond_id=snapshot.bond_id,
                status="ALREADY_POPULATED",
                current_nkd=snapshot.nkd,
                flags=("ALREADY_POPULATED",),
            )

        flags: set[str] = set()
        if snapshot.source != "moex":
            flags.add("SOURCE_NOT_MOEX")

        raw_payload = snapshot.raw_payload
        moex_payload: Mapping[str, Any] | None = None
        if raw_payload is None:
            flags.add("RAW_PAYLOAD_MISSING")
        elif not isinstance(raw_payload, Mapping):
            flags.add("RAW_PAYLOAD_INVALID")
        else:
            candidate = cls._mapping_value(raw_payload, "moex")
            if candidate is None:
                flags.add("MOEX_PAYLOAD_MISSING")
            elif not isinstance(candidate, Mapping):
                flags.add("MOEX_PAYLOAD_INVALID")
            else:
                moex_payload = candidate

        proposed_nkd: Decimal | None = None
        if moex_payload is not None:
            nkd_fields = cls._alias_values(moex_payload, cls._NKD_ALIASES)
            populated_fields = [
                (field, value)
                for field, value in nkd_fields
                if value is not None
                and not (isinstance(value, str) and not value.strip())
            ]
            if not populated_fields:
                flags.add("NKD_MISSING")
            else:
                parsed_values: list[Decimal] = []
                invalid_value = False
                for _, raw_value in populated_fields:
                    parsed = cls._parse_nkd(raw_value)
                    if parsed is None:
                        invalid_value = True
                        break
                    parsed_values.append(parsed)
                if invalid_value:
                    flags.add("NKD_INVALID")
                elif any(value != parsed_values[0] for value in parsed_values[1:]):
                    flags.add("NKD_ALIAS_CONFLICT")
                else:
                    proposed_nkd = parsed_values[0]

        if bond is None:
            flags.add("BOND_MISSING")

        raw_secid = cls._mapping_value(moex_payload, "SECID") if moex_payload else None
        if raw_secid is None or isinstance(raw_secid, str) and not raw_secid.strip():
            flags.add("RAW_SECID_MISSING")
        elif not isinstance(raw_secid, str):
            flags.add("RAW_SECID_INVALID")
        elif bond is not None:
            bond_secid = bond.secid
            if not isinstance(bond_secid, str) or not bond_secid.strip():
                flags.add("BOND_SECID_MISSING")
            elif raw_secid.strip().casefold() != bond_secid.strip().casefold():
                flags.add("SECID_MISMATCH")

        raw_trade_date = (
            cls._mapping_value(moex_payload, "TRADEDATE") if moex_payload else None
        )
        if raw_trade_date is None or isinstance(raw_trade_date, str) and not raw_trade_date.strip():
            flags.add("RAW_TRADE_DATE_MISSING")
        else:
            parsed_trade_date = cls._parse_trade_date(raw_trade_date)
            if parsed_trade_date is None:
                flags.add("RAW_TRADE_DATE_INVALID")
            elif parsed_trade_date != snapshot.trade_date:
                flags.add("TRADE_DATE_MISMATCH")

        if flags:
            status: RepairStatus = next(
                flag for flag in cls._STATUS_PRECEDENCE if flag in flags
            )
            return NkdRepairResult(
                snapshot_id=snapshot.id,
                bond_id=snapshot.bond_id,
                status=status,
                current_nkd=None,
                proposed_nkd=None,
                flags=tuple(sorted(flags)),
            )

        return NkdRepairResult(
            snapshot_id=snapshot.id,
            bond_id=snapshot.bond_id,
            status="SAFE_TO_REPAIR",
            current_nkd=None,
            proposed_nkd=proposed_nkd,
            flags=(),
        )

    @staticmethod
    def _mapping_value(
        mapping: Mapping[str, Any] | None, key: str
    ) -> Any:
        if mapping is None:
            return None
        wanted = key.casefold()
        for candidate, value in mapping.items():
            if isinstance(candidate, str) and candidate.casefold() == wanted:
                return value
        return None

    @staticmethod
    def _alias_values(
        mapping: Mapping[str, Any], aliases: set[str]
    ) -> list[tuple[str, Any]]:
        return [
            (str(key), value)
            for key, value in mapping.items()
            if isinstance(key, str) and key.casefold() in aliases
        ]

    @staticmethod
    def _parse_nkd(raw_value: Any) -> Decimal | None:
        if isinstance(raw_value, bool) or not isinstance(
            raw_value, (Decimal, int, float, str)
        ):
            return None
        try:
            value = Decimal(
                raw_value.strip() if isinstance(raw_value, str) else str(raw_value)
            )
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return None
        if not value.is_finite() or value < 0:
            return None
        return value

    @staticmethod
    def _parse_trade_date(raw_value: Any) -> date | None:
        if type(raw_value) is date:
            return raw_value
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        try:
            return date.fromisoformat(raw_value.strip())
        except ValueError:
            return None
