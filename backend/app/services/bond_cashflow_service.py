from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.schemas.cashflow import BondCashflowEventCreate


ALLOWED_CASHFLOW_EVENT_TYPES = {
    "coupon",
    "amortization",
    "redemption",
    "offer_redemption",
    "other",
}
CashflowUpsertAction = Literal["created", "updated", "skipped"]


@dataclass(frozen=True)
class CashflowSums:
    coupon_cashflow: Decimal
    amortization_cashflow: Decimal
    redemption_cashflow: Decimal
    warnings: list[str]
    details: list[dict[str, Any]]
    event_count: int


class BondCashflowService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_or_update_event(
        self,
        event_in: BondCashflowEventCreate,
    ) -> BondCashflowEvent:
        event, _ = self.create_or_update_event_with_action(
            event_in,
            rebuild_existing=True,
        )
        return event

    def create_or_update_event_with_action(
        self,
        event_in: BondCashflowEventCreate,
        *,
        rebuild_existing: bool = True,
    ) -> tuple[BondCashflowEvent, CashflowUpsertAction]:
        bond = self.db.get(Bond, event_in.bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )
        self._validate_event(event_in)

        source = event_in.source or "manual"
        amount = event_in.amount
        if amount is None and event_in.amount_percent is not None:
            amount = self._amount_from_percent(bond, event_in.amount_percent)

        event = self.db.execute(
            select(BondCashflowEvent).where(
                BondCashflowEvent.bond_id == event_in.bond_id,
                BondCashflowEvent.event_date == event_in.event_date,
                BondCashflowEvent.event_type == event_in.event_type,
                BondCashflowEvent.source == source,
            )
        ).scalar_one_or_none()
        data = event_in.model_dump()
        data["source"] = source
        data["amount"] = amount

        if event is None:
            event = BondCashflowEvent(**data)
            self.db.add(event)
            action: CashflowUpsertAction = "created"
        elif not rebuild_existing:
            action = "skipped"
        else:
            for field, value in data.items():
                setattr(event, field, value)
            self.db.add(event)
            action = "updated"
        self.db.commit()
        self.db.refresh(event)
        return event, action

    def list_events(
        self,
        *,
        bond_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        event_type: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[BondCashflowEvent]:
        stmt = select(BondCashflowEvent)
        if bond_id is not None:
            stmt = stmt.where(BondCashflowEvent.bond_id == bond_id)
        if date_from is not None:
            stmt = stmt.where(BondCashflowEvent.event_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(BondCashflowEvent.event_date <= date_to)
        if event_type is not None:
            if event_type not in ALLOWED_CASHFLOW_EVENT_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cashflow event type",
                )
            stmt = stmt.where(BondCashflowEvent.event_type == event_type)
        if source is not None:
            stmt = stmt.where(BondCashflowEvent.source == source)
        stmt = stmt.order_by(
            BondCashflowEvent.event_date.asc(),
            BondCashflowEvent.id.asc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def sum_cashflows_for_period(
        self,
        *,
        bond: Bond,
        as_of_date: date,
        end_date: date,
    ) -> CashflowSums:
        events = self.list_events(
            bond_id=bond.id,
            date_from=as_of_date,
            date_to=end_date,
            limit=10000,
        )
        events = [
            event
            for event in events
            if event.event_date > as_of_date and event.event_date <= end_date
        ]
        warnings: list[str] = []
        details: list[dict[str, Any]] = []
        coupon = Decimal("0")
        amortization = Decimal("0")
        redemption = Decimal("0")

        if not events:
            warnings.append("No cashflow events found for calculation period")

        for event in events:
            amount = self._event_amount(bond, event, warnings)
            details.append(
                {
                    "id": event.id,
                    "event_date": event.event_date.isoformat(),
                    "event_type": event.event_type,
                    "amount": None if amount is None else str(amount),
                    "source": event.source,
                }
            )
            if amount is None:
                continue
            if event.event_type == "coupon":
                coupon += amount
            elif event.event_type == "amortization":
                amortization += amount
            elif event.event_type in {"redemption", "offer_redemption"}:
                redemption += amount

        return CashflowSums(
            coupon_cashflow=coupon,
            amortization_cashflow=amortization,
            redemption_cashflow=redemption,
            warnings=warnings,
            details=details,
            event_count=len(events),
        )

    @staticmethod
    def _validate_event(event_in: BondCashflowEventCreate) -> None:
        if event_in.event_type not in ALLOWED_CASHFLOW_EVENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cashflow event type",
            )
        if event_in.amount is None and event_in.amount_percent is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount or amount_percent is required",
            )
        if event_in.amount is not None and event_in.amount < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount cannot be negative",
            )
        if event_in.amount_percent is not None and event_in.amount_percent < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount_percent cannot be negative",
            )

    @staticmethod
    def _amount_from_percent(
        bond: Bond,
        amount_percent: Decimal,
    ) -> Decimal | None:
        if bond.nominal_value is None:
            return None
        return bond.nominal_value * amount_percent / Decimal("100")

    def _event_amount(
        self,
        bond: Bond,
        event: BondCashflowEvent,
        warnings: list[str],
    ) -> Decimal | None:
        if event.amount is not None:
            return event.amount
        if event.amount_percent is None:
            return None
        amount = self._amount_from_percent(bond, event.amount_percent)
        if amount is None and "Nominal value is missing" not in warnings:
            warnings.append("Nominal value is missing")
        return amount
