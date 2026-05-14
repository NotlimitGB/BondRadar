from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.ml_dataset import BondMarketSnapshotCreate


class MarketSnapshotService:
    MAX_FUTURE_LOOKUP_DAYS = 7

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_or_update(
        self, snapshot_in: BondMarketSnapshotCreate
    ) -> BondMarketSnapshot:
        if self.db.get(Bond, snapshot_in.bond_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )

        source = snapshot_in.source or "manual"
        snapshot = self.db.execute(
            select(BondMarketSnapshot).where(
                BondMarketSnapshot.bond_id == snapshot_in.bond_id,
                BondMarketSnapshot.trade_date == snapshot_in.trade_date,
                BondMarketSnapshot.source == source,
            )
        ).scalar_one_or_none()
        data = snapshot_in.model_dump()
        data["source"] = source

        if snapshot is None:
            snapshot = BondMarketSnapshot(**data)
            self.db.add(snapshot)
        else:
            for field, value in data.items():
                setattr(snapshot, field, value)
            self.db.add(snapshot)

        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def list_snapshots(
        self,
        *,
        bond_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[BondMarketSnapshot]:
        stmt = select(BondMarketSnapshot)
        if bond_id is not None:
            stmt = stmt.where(BondMarketSnapshot.bond_id == bond_id)
        if date_from is not None:
            stmt = stmt.where(BondMarketSnapshot.trade_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(BondMarketSnapshot.trade_date <= date_to)
        if source is not None:
            stmt = stmt.where(BondMarketSnapshot.source == source)
        stmt = stmt.order_by(
            BondMarketSnapshot.trade_date.desc(),
            BondMarketSnapshot.id.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def get_latest_for_bond(
        self,
        bond_id: int,
        as_of_date: date,
    ) -> BondMarketSnapshot | None:
        return self.db.execute(
            select(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id == bond_id,
                BondMarketSnapshot.trade_date <= as_of_date,
            )
            .order_by(
                BondMarketSnapshot.trade_date.desc(),
                BondMarketSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def get_future_for_bond(
        self,
        bond_id: int,
        as_of_date: date,
        horizon_days: int,
        *,
        max_future_lookup_days: int = MAX_FUTURE_LOOKUP_DAYS,
    ) -> BondMarketSnapshot | None:
        target_date = as_of_date + timedelta(days=horizon_days)
        max_date = target_date + timedelta(days=max_future_lookup_days)
        return self.db.execute(
            select(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id == bond_id,
                BondMarketSnapshot.trade_date >= target_date,
                BondMarketSnapshot.trade_date <= max_date,
            )
            .order_by(
                BondMarketSnapshot.trade_date.asc(),
                BondMarketSnapshot.id.asc(),
            )
            .limit(1)
        ).scalar_one_or_none()
