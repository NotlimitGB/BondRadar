from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.schemas.ml_dataset import BondMarketSnapshotCreate
from app.schemas.moex import (
    MoexMarketDataSyncError,
    MoexMarketDataSyncRequest,
    MoexMarketDataSyncResult,
)
from app.services.market_snapshot_service import MarketSnapshotService
from app.services.moex_iss_client import MoexIssClient, MoexIssClientError


class MoexMarketDataService:
    SOURCE = "moex"

    def __init__(
        self,
        db: Session,
        *,
        moex_client: MoexIssClient | None = None,
        market_snapshot_service: MarketSnapshotService | None = None,
    ) -> None:
        self.db = db
        self.moex_client = moex_client or MoexIssClient()
        self.market_snapshot_service = market_snapshot_service or MarketSnapshotService(
            db
        )

    def sync(self, request: MoexMarketDataSyncRequest) -> MoexMarketDataSyncResult:
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )

        bonds, errors = self._resolve_bonds(request.bond_ids)
        warnings: list[str] = []
        created = 0
        updated = 0
        skipped = 0
        processed_bonds = 0
        skipped_bonds = len(errors)

        for bond in bonds:
            if not bond.secid:
                skipped_bonds += 1
                errors.append(
                    MoexMarketDataSyncError(
                        bond_id=bond.id,
                        secid=None,
                        message="Bond secid is missing",
                    )
                )
                continue

            try:
                result = self.moex_client.fetch_history(
                    secid=bond.secid,
                    board=request.board,
                    date_from=request.date_from,
                    date_to=request.date_to,
                )
                processed_bonds += 1
                warnings.extend(result.warnings)
            except Exception as exc:
                skipped_bonds += 1
                errors.append(
                    MoexMarketDataSyncError(
                        bond_id=bond.id,
                        secid=bond.secid,
                        message=self._error_message(exc),
                    )
                )
                continue

            if not result.rows:
                warnings.append(f"No MOEX history rows found for {bond.secid}")
                continue

            for row in result.rows:
                snapshot_in, row_warnings, row_error = self._map_row(bond, row)
                warnings.extend(row_warnings)
                if row_error is not None:
                    skipped += 1
                    errors.append(
                        MoexMarketDataSyncError(
                            bond_id=bond.id,
                            secid=bond.secid,
                            message=row_error,
                        )
                    )
                    continue

                _, action = self.market_snapshot_service.create_or_update_with_action(
                    snapshot_in,
                    rebuild_existing=request.rebuild_existing,
                )
                if action == "created":
                    created += 1
                elif action == "updated":
                    updated += 1
                else:
                    skipped += 1

        total_bonds = (
            len(request.bond_ids)
            if request.bond_ids
            else self.db.execute(select(Bond.id)).scalars().unique().all()
        )
        total_bonds_count = (
            total_bonds if isinstance(total_bonds, int) else len(total_bonds)
        )
        if total_bonds_count == 0:
            warnings.append("No bonds selected for MOEX sync")

        return MoexMarketDataSyncResult(
            total_bonds=total_bonds_count,
            processed_bonds=processed_bonds,
            skipped_bonds=skipped_bonds,
            created=created,
            updated=updated,
            skipped=skipped,
            errors=errors,
            warnings=warnings,
        )

    def _resolve_bonds(
        self,
        bond_ids: list[int] | None,
    ) -> tuple[list[Bond], list[MoexMarketDataSyncError]]:
        errors: list[MoexMarketDataSyncError] = []
        if bond_ids:
            requested_ids = set(bond_ids)
            bonds = list(
                self.db.execute(
                    select(Bond).where(Bond.id.in_(requested_ids)).order_by(Bond.id)
                ).scalars()
            )
            found_ids = {bond.id for bond in bonds}
            for missing_id in sorted(requested_ids - found_ids):
                errors.append(
                    MoexMarketDataSyncError(
                        bond_id=missing_id,
                        secid=None,
                        message="Bond not found",
                    )
                )
            return bonds, errors

        return list(self.db.execute(select(Bond).order_by(Bond.id)).scalars()), errors

    def _map_row(
        self,
        bond: Bond,
        row: dict[str, Any],
    ) -> tuple[BondMarketSnapshotCreate | None, list[str], str | None]:
        warnings: list[str] = []
        mapping_notes: list[str] = []
        trade_date = self._parse_date(row.get("TRADEDATE"))
        if trade_date is None:
            return None, warnings, "MOEX row trade date is missing or invalid"

        price = self._first_decimal(
            row,
            ("CLOSE", "WAPRICE", "PRICE"),
            warnings,
            bond.secid,
        )
        clean_price = self._decimal(row.get("LEGALCLOSEPRICE"), "LEGALCLOSEPRICE", warnings, bond.secid)
        nkd = self._decimal(row.get("ACCRUEDINT"), "ACCRUEDINT", warnings, bond.secid)
        yield_to_maturity = self._first_decimal(
            row,
            ("YIELDATWAPRICE", "YIELDCLOSE"),
            warnings,
            bond.secid,
        )
        duration = self._decimal(row.get("DURATION"), "DURATION", warnings, bond.secid)
        if duration is not None and duration > Decimal("50"):
            duration = duration / Decimal("365")
            mapping_notes.append("DURATION looked like days and was divided by 365")

        volume = self._decimal(row.get("VOLUME"), "VOLUME", warnings, bond.secid)
        if volume is None and self._has_value(row.get("VALUE")):
            volume = self._decimal(row.get("VALUE"), "VALUE", warnings, bond.secid)
            if volume is not None:
                mapping_notes.append("VALUE was used as volume fallback")

        raw_payload: dict[str, Any] = {"moex": row}
        if mapping_notes:
            raw_payload["mapping_notes"] = mapping_notes

        return (
            BondMarketSnapshotCreate(
                bond_id=bond.id,
                trade_date=trade_date,
                price=price,
                clean_price=clean_price,
                dirty_price=None,
                nkd=nkd,
                yield_to_maturity=yield_to_maturity,
                duration_years=duration,
                volume=volume,
                liquidity_score=None,
                spread_to_ofz=None,
                source=self.SOURCE,
                raw_payload=raw_payload,
            ),
            warnings,
            None,
        )

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if value is None or value == "":
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    def _first_decimal(
        self,
        row: dict[str, Any],
        keys: tuple[str, ...],
        warnings: list[str],
        secid: str | None,
    ) -> Decimal | None:
        for key in keys:
            if self._has_value(row.get(key)):
                return self._decimal(row.get(key), key, warnings, secid)
        return None

    @staticmethod
    def _decimal(
        value: Any,
        field_name: str,
        warnings: list[str],
        secid: str | None,
    ) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            warnings.append(f"Invalid numeric value for {field_name} in {secid}")
            return None

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != ""

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, MoexIssClientError):
            return str(exc)
        return str(exc)
