from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.services.market_snapshot_service import MarketSnapshotService


@dataclass(frozen=True)
class LabelBuildOutcome:
    label: BondReturnLabel
    action: str


class LabelBuilderService:
    def __init__(
        self,
        db: Session,
        market_service: MarketSnapshotService | None = None,
    ) -> None:
        self.db = db
        self.market_service = market_service or MarketSnapshotService(db)

    def build_for_bond_date(
        self,
        bond_id: int,
        as_of_date: date,
        horizon_days: int,
        *,
        rebuild_existing: bool = False,
    ) -> LabelBuildOutcome:
        if self.db.get(Bond, bond_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )

        existing = self.db.execute(
            select(BondReturnLabel).where(
                BondReturnLabel.bond_id == bond_id,
                BondReturnLabel.as_of_date == as_of_date,
                BondReturnLabel.horizon_days == horizon_days,
            )
        ).scalar_one_or_none()
        if existing is not None and not rebuild_existing:
            return LabelBuildOutcome(label=existing, action="skipped")

        payload = self._build_payload(bond_id, as_of_date, horizon_days)
        if existing is None:
            label = BondReturnLabel(**payload)
            self.db.add(label)
            self.db.flush()
            return LabelBuildOutcome(label=label, action="created")

        for field, value in payload.items():
            setattr(existing, field, value)
        self.db.add(existing)
        self.db.flush()
        return LabelBuildOutcome(label=existing, action="updated")

    def _build_payload(
        self,
        bond_id: int,
        as_of_date: date,
        horizon_days: int,
    ) -> dict:
        start_snapshot = self.market_service.get_latest_for_bond(bond_id, as_of_date)
        end_snapshot = self.market_service.get_future_for_bond(
            bond_id,
            as_of_date,
            horizon_days,
        )
        start_price = self._price(start_snapshot)
        end_price = self._price(end_snapshot)

        future_return: Decimal | None = None
        label = "insufficient_data"
        label_binary: int | None = None

        if (
            start_price is not None
            and end_price is not None
            and start_price != Decimal("0")
        ):
            future_return = (end_price - start_price) / start_price
            if future_return > 0:
                label = "positive_return"
                label_binary = 1
            else:
                label = "negative_return"
                label_binary = 0

        return {
            "bond_id": bond_id,
            "as_of_date": as_of_date,
            "horizon_days": horizon_days,
            "start_market_snapshot_id": start_snapshot.id if start_snapshot else None,
            "end_market_snapshot_id": end_snapshot.id if end_snapshot else None,
            "start_price": start_price,
            "end_price": end_price,
            "future_return": future_return,
            "benchmark_return": None,
            "excess_return": None,
            "label": label,
            "label_binary": label_binary,
        }

    @staticmethod
    def _price(snapshot: BondMarketSnapshot | None) -> Decimal | None:
        if snapshot is None:
            return None
        if snapshot.dirty_price is not None:
            return snapshot.dirty_price
        if snapshot.clean_price is not None:
            return snapshot.clean_price
        return snapshot.price
