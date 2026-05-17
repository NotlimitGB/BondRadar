from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.schemas.ml_dataset import BondMarketSnapshotCreate
from app.schemas.moex import (
    MoexMarketDataSyncError,
    MoexMarketDataSyncRequest,
    MoexMarketDataSyncResult,
)
from app.schemas.moex_market_history import (
    MoexBondMarketHistoryBackfillBondResult,
    MoexBondMarketHistoryBackfillError,
    MoexBondMarketHistoryBackfillRequest,
    MoexBondMarketHistoryBackfillResult,
    MoexBondMarketHistoryBackfillWarning,
)
from app.services.market_snapshot_service import MarketSnapshotService
from app.services.moex_iss_client import MoexIssClient, MoexIssClientError


@dataclass
class HistoryBondSelection:
    bond: Bond | None
    bond_id: int | None
    secid: str | None
    error: MoexBondMarketHistoryBackfillError | None = None


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

    def backfill_history(
        self,
        request: MoexBondMarketHistoryBackfillRequest,
    ) -> MoexBondMarketHistoryBackfillResult:
        board, source = self._validate_history_request(request)
        selections, warnings = self._resolve_history_bonds(request)
        errors: list[MoexBondMarketHistoryBackfillError] = []
        bond_results: list[MoexBondMarketHistoryBackfillBondResult] = []
        bonds_processed = 0
        bonds_skipped = 0
        bonds_failed = 0
        rows_fetched = 0
        snapshots_created = 0
        snapshots_updated = 0
        snapshots_skipped = 0

        for selection in selections:
            if selection.error is not None:
                errors.append(selection.error)
                bond_results.append(
                    MoexBondMarketHistoryBackfillBondResult(
                        bond_id=selection.bond_id,
                        secid=selection.secid,
                        status="failed",
                        rows_fetched=0,
                        snapshots_created=0,
                        snapshots_updated=0,
                        snapshots_skipped=0,
                        warnings=[],
                        errors=[selection.error],
                    )
                )
                bonds_failed += 1
                continue

            bond = selection.bond
            if bond is None:
                continue
            secid = self._text(bond.secid, upper=True)
            if not secid:
                message = "Bond secid is missing"
                if request.skip_bonds_without_secid:
                    warning = MoexBondMarketHistoryBackfillWarning(
                        bond_id=bond.id,
                        secid=None,
                        message=message,
                    )
                    warnings.append(warning)
                    bond_results.append(
                        MoexBondMarketHistoryBackfillBondResult(
                            bond_id=bond.id,
                            secid=None,
                            status="skipped",
                            rows_fetched=0,
                            snapshots_created=0,
                            snapshots_updated=0,
                            snapshots_skipped=0,
                            warnings=[warning],
                            errors=[],
                        )
                    )
                    bonds_skipped += 1
                    continue

                error = MoexBondMarketHistoryBackfillError(
                    bond_id=bond.id,
                    secid=None,
                    message=message,
                )
                errors.append(error)
                bond_results.append(
                    MoexBondMarketHistoryBackfillBondResult(
                        bond_id=bond.id,
                        secid=None,
                        status="failed",
                        rows_fetched=0,
                        snapshots_created=0,
                        snapshots_updated=0,
                        snapshots_skipped=0,
                        warnings=[],
                        errors=[error],
                    )
                )
                bonds_failed += 1
                continue

            result = self._backfill_history_for_bond(
                bond,
                secid=secid,
                request=request,
                board=board,
                source=source,
            )
            bond_results.append(result)
            warnings.extend(result.warnings)
            errors.extend(result.errors)
            rows_fetched += result.rows_fetched
            snapshots_created += result.snapshots_created
            snapshots_updated += result.snapshots_updated
            snapshots_skipped += result.snapshots_skipped
            if result.status == "failed":
                bonds_failed += 1
            elif result.status == "skipped":
                bonds_skipped += 1
            else:
                bonds_processed += 1

        if not selections:
            warnings.append(
                MoexBondMarketHistoryBackfillWarning(
                    message="No bonds selected for MOEX history backfill"
                )
            )

        return MoexBondMarketHistoryBackfillResult(
            date_from=request.date_from,
            date_to=request.date_to,
            board=board,
            source=source,
            bonds_requested=len(selections),
            bonds_processed=bonds_processed,
            bonds_skipped=bonds_skipped,
            bonds_failed=bonds_failed,
            rows_fetched=rows_fetched,
            snapshots_created=snapshots_created,
            snapshots_updated=snapshots_updated,
            snapshots_skipped=snapshots_skipped,
            bond_results=bond_results,
            warnings=warnings,
            errors=errors,
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

    def _resolve_history_bonds(
        self,
        request: MoexBondMarketHistoryBackfillRequest,
    ) -> tuple[
        list[HistoryBondSelection],
        list[MoexBondMarketHistoryBackfillWarning],
    ]:
        warnings: list[MoexBondMarketHistoryBackfillWarning] = []

        if request.bond_ids is not None:
            bond_ids = self._dedupe_ints(request.bond_ids)
            if len(bond_ids) != len(request.bond_ids):
                warnings.append(
                    MoexBondMarketHistoryBackfillWarning(
                        message="Duplicate bond ids were ignored"
                    )
                )
            if not bond_ids:
                return [], warnings
            bonds = list(
                self.db.execute(
                    select(Bond).where(Bond.id.in_(bond_ids))
                ).scalars()
            )
            by_id = {bond.id: bond for bond in bonds}
            selections: list[HistoryBondSelection] = []
            for bond_id in bond_ids:
                bond = by_id.get(bond_id)
                if bond is None:
                    selections.append(
                        HistoryBondSelection(
                            bond=None,
                            bond_id=bond_id,
                            secid=None,
                            error=MoexBondMarketHistoryBackfillError(
                                bond_id=bond_id,
                                secid=None,
                                message="Bond not found",
                            ),
                        )
                    )
                else:
                    selections.append(
                        HistoryBondSelection(
                            bond=bond,
                            bond_id=bond.id,
                            secid=self._text(bond.secid, upper=True),
                        )
                    )
            return selections, warnings

        if request.secids is not None:
            secids = self._dedupe_secids(request.secids)
            if len(secids) != len(request.secids):
                warnings.append(
                    MoexBondMarketHistoryBackfillWarning(
                        message="Duplicate secids were ignored"
                    )
                )
            if not secids:
                return [], warnings
            bonds = list(
                self.db.execute(select(Bond).where(Bond.secid.in_(secids))).scalars()
            )
            by_secid = {
                self._text(bond.secid, upper=True): bond
                for bond in bonds
                if self._text(bond.secid, upper=True)
            }
            selections = []
            for secid in secids:
                bond = by_secid.get(secid)
                if bond is None:
                    selections.append(
                        HistoryBondSelection(
                            bond=None,
                            bond_id=None,
                            secid=secid,
                            error=MoexBondMarketHistoryBackfillError(
                                bond_id=None,
                                secid=secid,
                                message="Bond not found for secid",
                            ),
                        )
                    )
                else:
                    selections.append(
                        HistoryBondSelection(
                            bond=bond,
                            bond_id=bond.id,
                            secid=secid,
                        )
                    )
            return selections, warnings

        bonds = list(
            self.db.execute(
                select(Bond)
                .where(Bond.secid.is_not(None), Bond.secid != "")
                .order_by(Bond.id)
            ).scalars()
        )
        return [
            HistoryBondSelection(
                bond=bond,
                bond_id=bond.id,
                secid=self._text(bond.secid, upper=True),
            )
            for bond in bonds
        ], warnings

    def _backfill_history_for_bond(
        self,
        bond: Bond,
        *,
        secid: str,
        request: MoexBondMarketHistoryBackfillRequest,
        board: str,
        source: str,
    ) -> MoexBondMarketHistoryBackfillBondResult:
        warnings: list[MoexBondMarketHistoryBackfillWarning] = []
        errors: list[MoexBondMarketHistoryBackfillError] = []
        rows_fetched = 0
        snapshots_created = 0
        snapshots_updated = 0
        snapshots_skipped = 0
        start = 0

        try:
            for _ in range(request.max_pages_per_bond):
                rows, page_warnings = self.moex_client.fetch_bond_market_history(
                    secid,
                    board=board,
                    date_from=request.date_from,
                    date_to=request.date_to,
                    start=start,
                    limit=request.page_size,
                )
                warnings.extend(
                    MoexBondMarketHistoryBackfillWarning(
                        bond_id=bond.id,
                        secid=secid,
                        message=message,
                    )
                    for message in page_warnings
                )
                if not rows:
                    break

                rows_fetched += len(rows)
                for row in rows:
                    snapshot_in, row_warnings, row_error = self._map_history_row(
                        bond,
                        secid=secid,
                        row=row,
                        board=board,
                        source=source,
                    )
                    warnings.extend(row_warnings)
                    if row_error is not None:
                        errors.append(row_error)
                        snapshots_skipped += 1
                        continue

                    _, action = self._upsert_history_snapshot(
                        snapshot_in,
                        rebuild_existing=request.rebuild_existing,
                    )
                    if action == "created":
                        snapshots_created += 1
                    elif action == "updated":
                        snapshots_updated += 1
                    else:
                        snapshots_skipped += 1

                start += request.page_size
            else:
                warnings.append(
                    MoexBondMarketHistoryBackfillWarning(
                        bond_id=bond.id,
                        secid=secid,
                        message="MOEX history pagination stopped by max_pages_per_bond",
                    )
                )
        except Exception as exc:
            error = MoexBondMarketHistoryBackfillError(
                bond_id=bond.id,
                secid=secid,
                message=self._error_message(exc),
            )
            return MoexBondMarketHistoryBackfillBondResult(
                bond_id=bond.id,
                secid=secid,
                status="failed",
                rows_fetched=rows_fetched,
                snapshots_created=snapshots_created,
                snapshots_updated=snapshots_updated,
                snapshots_skipped=snapshots_skipped,
                warnings=warnings,
                errors=errors + [error],
            )

        if rows_fetched == 0:
            warnings.append(
                MoexBondMarketHistoryBackfillWarning(
                    bond_id=bond.id,
                    secid=secid,
                    message="No MOEX history rows found",
                )
            )

        status_value = "completed_with_warnings" if warnings or errors else "completed"
        return MoexBondMarketHistoryBackfillBondResult(
            bond_id=bond.id,
            secid=secid,
            status=status_value,
            rows_fetched=rows_fetched,
            snapshots_created=snapshots_created,
            snapshots_updated=snapshots_updated,
            snapshots_skipped=snapshots_skipped,
            warnings=warnings,
            errors=errors,
        )

    def _upsert_history_snapshot(
        self,
        snapshot_in: BondMarketSnapshotCreate,
        *,
        rebuild_existing: bool,
    ) -> tuple[BondMarketSnapshot, str]:
        source = snapshot_in.source or self.SOURCE
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
            self.db.commit()
            self.db.refresh(snapshot)
            return snapshot, "created"

        if not rebuild_existing:
            return snapshot, "skipped"

        for field, value in data.items():
            if field in {"id", "bond_id", "trade_date", "source"}:
                continue
            if value is not None:
                setattr(snapshot, field, value)
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot, "updated"

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

    def _map_history_row(
        self,
        bond: Bond,
        *,
        secid: str,
        row: dict[str, Any],
        board: str,
        source: str,
    ) -> tuple[
        BondMarketSnapshotCreate | None,
        list[MoexBondMarketHistoryBackfillWarning],
        MoexBondMarketHistoryBackfillError | None,
    ]:
        warnings: list[MoexBondMarketHistoryBackfillWarning] = []
        mapping_notes: list[str] = []
        row_secid = self._text(row.get("secid"), upper=True)
        effective_secid = row_secid or secid
        trade_date = self._parse_date(row.get("trade_date"))

        if not effective_secid:
            return None, warnings, MoexBondMarketHistoryBackfillError(
                bond_id=bond.id,
                secid=None,
                trade_date=trade_date,
                message="MOEX history row secid is missing or invalid",
            )
        if row_secid is not None and row_secid != secid:
            return None, warnings, MoexBondMarketHistoryBackfillError(
                bond_id=bond.id,
                secid=row_secid,
                trade_date=trade_date,
                message="MOEX history row secid does not match selected bond",
                details={"expected_secid": secid},
            )
        if trade_date is None:
            return None, warnings, MoexBondMarketHistoryBackfillError(
                bond_id=bond.id,
                secid=effective_secid,
                message="MOEX history row trade date is missing or invalid",
            )

        price = self._first_history_decimal(
            row,
            (
                "close_price",
                "market_price",
                "weighted_average_price",
                "last_price",
            ),
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )
        clean_price = self._history_decimal(
            row.get("legal_close_price"),
            "legal_close_price",
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )
        nkd = self._history_decimal(
            row.get("accrued_interest"),
            "accrued_interest",
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )
        yield_to_maturity = self._history_decimal(
            row.get("yield_to_maturity"),
            "yield_to_maturity",
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )
        duration = self._history_decimal(
            row.get("duration"),
            "duration",
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )
        if duration is not None and duration > Decimal("50"):
            duration = duration / Decimal("365")
            mapping_notes.append("DURATION looked like days and was divided by 365")
        volume = self._history_decimal(
            row.get("volume"),
            "volume",
            warnings=warnings,
            bond_id=bond.id,
            secid=effective_secid,
            trade_date=trade_date,
        )

        if price is None and yield_to_maturity is None:
            warnings.append(
                MoexBondMarketHistoryBackfillWarning(
                    bond_id=bond.id,
                    secid=effective_secid,
                    trade_date=trade_date,
                    message="MOEX history row has no price or yield values",
                )
            )

        canonical_payload = {key: value for key, value in row.items() if key != "raw"}
        raw_payload: dict[str, Any] = {
            "moex": row.get("raw") or dict(row),
            "canonical": canonical_payload,
            "board": board,
            "value": row.get("value"),
            "num_trades": row.get("num_trades"),
            "currency": row.get("currency"),
        }
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
                source=source,
                raw_payload=raw_payload,
            ),
            warnings,
            None,
        )

    @staticmethod
    def _validate_history_request(
        request: MoexBondMarketHistoryBackfillRequest,
    ) -> tuple[str, str]:
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if (request.date_to - request.date_from).days > 3660:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date range must not exceed 3660 days",
            )
        if request.page_size < 1 or request.page_size > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="page_size must be between 1 and 500",
            )
        if request.max_pages_per_bond < 1 or request.max_pages_per_bond > 10000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_pages_per_bond must be between 1 and 10000",
            )
        if request.bond_ids is not None and request.secids is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use bond_ids or secids, not both",
            )
        board = str(request.board).strip()
        if not board:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="board must not be empty",
            )
        source = str(request.source).strip()
        if not source:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source must not be empty",
            )
        return board, source

    def _first_history_decimal(
        self,
        row: dict[str, Any],
        keys: tuple[str, ...],
        *,
        warnings: list[MoexBondMarketHistoryBackfillWarning],
        bond_id: int,
        secid: str,
        trade_date: date,
    ) -> Decimal | None:
        for key in keys:
            if self._has_value(row.get(key)):
                return self._history_decimal(
                    row.get(key),
                    key,
                    warnings=warnings,
                    bond_id=bond_id,
                    secid=secid,
                    trade_date=trade_date,
                )
        return None

    @staticmethod
    def _history_decimal(
        value: Any,
        field_name: str,
        *,
        warnings: list[MoexBondMarketHistoryBackfillWarning],
        bond_id: int,
        secid: str,
        trade_date: date,
    ) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError):
            warnings.append(
                MoexBondMarketHistoryBackfillWarning(
                    bond_id=bond_id,
                    secid=secid,
                    trade_date=trade_date,
                    message=f"Invalid numeric value for {field_name}; field was ignored",
                )
            )
            return None

    @staticmethod
    def _dedupe_ints(values: list[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _dedupe_secids(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            secid = str(value).strip().upper()
            if not secid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="secids cannot contain empty values",
                )
            if secid in seen:
                continue
            seen.add(secid)
            result.append(secid)
        return result

    @staticmethod
    def _text(
        value: Any,
        *,
        upper: bool = False,
    ) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if not text:
            return None
        return text.upper() if upper else text

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
