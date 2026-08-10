from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.schemas.cashflow import BondCashflowEventCreate
from app.schemas.moex import (
    MoexCashflowSyncError,
    MoexCashflowSyncRequest,
    MoexCashflowSyncResult,
    MoexCashflowSyncWarning,
)
from app.services.bond_cashflow_service import BondCashflowService
from app.services.moex_iss_client import (
    MoexCashflowScheduleResult,
    MoexIssClient,
    MoexIssClientError,
)
from app.services.moex_normalization import canonicalize_moex_currency


class MoexCashflowService:
    SOURCE = "moex"

    TABLE_CONFIG = {
        "coupons": {
            "event_type": "coupon",
            "date": ("coupondate", "coupon_date", "COUPONDATE", "date"),
            "amount": ("value", "couponvalue", "coupon_value", "VALUE", "COUPONVALUE"),
            "percent": (
                "valueprc",
                "couponpercent",
                "coupon_percent",
                "VALUEPRC",
                "COUPONPERCENT",
            ),
            "currency": ("currencyid", "currency", "CURRENCYID", "CURRENCY"),
        },
        "amortizations": {
            "event_type": "amortization",
            "date": (
                "amortdate",
                "amortizationdate",
                "amort_date",
                "AMORTDATE",
                "date",
            ),
            "amount": ("value", "amortvalue", "amortization_value", "VALUE"),
            "percent": (
                "valueprc",
                "amortpercent",
                "amortization_percent",
                "VALUEPRC",
            ),
            "currency": ("currencyid", "currency", "CURRENCYID", "CURRENCY"),
        },
        "offers": {
            "event_type": "offer_redemption",
            "date": ("offerdate", "offer_date", "OFFERDATE", "date"),
            "amount": ("price", "offerprice", "offer_price", "PRICE"),
            "percent": (),
            "currency": ("currencyid", "currency", "CURRENCYID", "CURRENCY"),
        },
        "redemptions": {
            "event_type": "redemption",
            "date": (
                "redemptiondate",
                "redemption_date",
                "maturitydate",
                "maturity_date",
                "date",
            ),
            "amount": ("value", "redemptionvalue", "redemption_value", "VALUE"),
            "percent": (
                "valueprc",
                "redemptionpercent",
                "redemption_percent",
                "VALUEPRC",
            ),
            "currency": ("currencyid", "currency", "CURRENCYID", "CURRENCY"),
        },
    }

    def __init__(
        self,
        db: Session,
        *,
        moex_client: MoexIssClient | None = None,
        cashflow_service: BondCashflowService | None = None,
    ) -> None:
        self.db = db
        self.moex_client = moex_client or MoexIssClient()
        self.cashflow_service = cashflow_service or BondCashflowService(db)

    def sync(self, request: MoexCashflowSyncRequest) -> MoexCashflowSyncResult:
        self._validate_request(request)
        bonds, errors, total_bonds = self._resolve_bonds(request.bond_ids)
        warnings: list[MoexCashflowSyncWarning] = []
        created = 0
        updated = 0
        skipped = 0
        processed_bonds = 0
        skipped_bonds = len(errors)

        for bond in bonds:
            if not bond.secid:
                skipped_bonds += 1
                errors.append(
                    MoexCashflowSyncError(
                        bond_id=bond.id,
                        secid=None,
                        message="Bond secid is missing",
                    )
                )
                continue

            try:
                schedule = self.moex_client.fetch_bond_cashflows(bond.secid)
                processed_bonds += 1
                warnings.extend(
                    MoexCashflowSyncWarning(
                        bond_id=bond.id,
                        secid=bond.secid,
                        message=message,
                    )
                    for message in schedule.warnings
                )
            except Exception as exc:
                skipped_bonds += 1
                errors.append(
                    MoexCashflowSyncError(
                        bond_id=bond.id,
                        secid=bond.secid,
                        message=self._error_message(exc),
                    )
                )
                continue

            for event_in, row_warnings in self._event_inputs(bond, schedule):
                warnings.extend(row_warnings)
                if event_in is None:
                    skipped += 1
                    continue
                if not self._matches_date_filter(event_in.event_date, request):
                    skipped += 1
                    continue

                _, action = self.cashflow_service.create_or_update_event_with_action(
                    event_in,
                    rebuild_existing=request.rebuild_existing,
                )
                if action == "created":
                    created += 1
                elif action == "updated":
                    updated += 1
                else:
                    skipped += 1

        if total_bonds == 0:
            warnings.append(
                MoexCashflowSyncWarning(
                    bond_id=None,
                    secid=None,
                    message="No bonds selected for MOEX cashflow sync",
                )
            )

        return MoexCashflowSyncResult(
            total_bonds=total_bonds,
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
    ) -> tuple[list[Bond], list[MoexCashflowSyncError], int]:
        errors: list[MoexCashflowSyncError] = []
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
                    MoexCashflowSyncError(
                        bond_id=missing_id,
                        secid=None,
                        message="Bond not found",
                    )
                )
            return bonds, errors, len(bond_ids)

        bonds = list(
            self.db.execute(
                select(Bond)
                .where(Bond.secid.is_not(None), Bond.secid != "")
                .order_by(Bond.id)
            ).scalars()
        )
        return bonds, errors, len(bonds)

    def _event_inputs(
        self,
        bond: Bond,
        schedule: MoexCashflowScheduleResult,
    ):
        for table_name in ("coupons", "amortizations", "offers", "redemptions"):
            rows = getattr(schedule, table_name)
            for row in rows:
                yield self._map_row(bond, row, table_name)

    def _map_row(
        self,
        bond: Bond,
        row: dict[str, Any],
        table_name: str,
    ) -> tuple[BondCashflowEventCreate | None, list[MoexCashflowSyncWarning]]:
        warnings: list[MoexCashflowSyncWarning] = []
        config = self.TABLE_CONFIG[table_name]
        event_date = self._parse_date(
            self._first_value(row, config["date"]),
            bond=bond,
            warnings=warnings,
            table_name=table_name,
        )
        if event_date is None:
            return None, warnings

        amount, invalid_amount = self._parse_decimal(
            self._first_value(row, config["amount"]),
            bond=bond,
            warnings=warnings,
            table_name=table_name,
            field_name="amount",
        )
        amount_percent, invalid_percent = self._parse_decimal(
            self._first_value(row, config["percent"]),
            bond=bond,
            warnings=warnings,
            table_name=table_name,
            field_name="amount_percent",
        )
        if invalid_amount or invalid_percent:
            return None, warnings
        if amount is None and amount_percent is None:
            warnings.append(
                self._warning(
                    bond,
                    f"MOEX {table_name} row skipped: amount or percent is missing",
                )
            )
            return None, warnings

        raw_currency = self._first_value(row, config["currency"])
        if self._has_value(raw_currency):
            currency = canonicalize_moex_currency(raw_currency)
        else:
            currency = canonicalize_moex_currency(bond.currency)
        if currency is None:
            warnings.append(
                self._warning(
                    bond,
                    f"MOEX {table_name} row skipped: bond_currency_unresolved",
                )
            )
            return None, warnings
        mapping_notes = [
            f"normalized_table={table_name}",
            f"source_table={row.get('__moex_source_table') or table_name}",
        ]
        return (
            BondCashflowEventCreate(
                bond_id=bond.id,
                event_date=event_date,
                event_type=config["event_type"],
                amount=amount,
                amount_percent=amount_percent,
                currency=currency,
                source=self.SOURCE,
                raw_payload={
                    "moex": self._raw_row(row),
                    "normalized_table": table_name,
                    "source_table": row.get("__moex_source_table") or table_name,
                    "mapping_notes": mapping_notes,
                },
            ),
            warnings,
        )

    @staticmethod
    def _validate_request(request: MoexCashflowSyncRequest) -> None:
        if (
            request.date_from is not None
            and request.date_to is not None
            and request.date_from > request.date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )

    @staticmethod
    def _matches_date_filter(
        event_date: date,
        request: MoexCashflowSyncRequest,
    ) -> bool:
        if request.date_from is not None and event_date < request.date_from:
            return False
        if request.date_to is not None and event_date > request.date_to:
            return False
        return True

    @staticmethod
    def _first_value(row: dict[str, Any], aliases) -> Any:
        normalized = {str(key).lower(): value for key, value in row.items()}
        for alias in aliases:
            value = normalized.get(str(alias).lower())
            if MoexCashflowService._has_value(value):
                return value
        return None

    @staticmethod
    def _parse_date(
        value: Any,
        *,
        bond: Bond,
        warnings: list[MoexCashflowSyncWarning],
        table_name: str,
    ) -> date | None:
        if not MoexCashflowService._has_value(value):
            warnings.append(
                MoexCashflowService._warning(
                    bond,
                    f"MOEX {table_name} row skipped: event date is missing",
                )
            )
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            warnings.append(
                MoexCashflowService._warning(
                    bond,
                    f"MOEX {table_name} row skipped: event date is invalid",
                )
            )
            return None

    @staticmethod
    def _parse_decimal(
        value: Any,
        *,
        bond: Bond,
        warnings: list[MoexCashflowSyncWarning],
        table_name: str,
        field_name: str,
    ) -> tuple[Decimal | None, bool]:
        if not MoexCashflowService._has_value(value):
            return None, False
        try:
            return Decimal(str(value).replace(",", ".")), False
        except (InvalidOperation, ValueError):
            warnings.append(
                MoexCashflowService._warning(
                    bond,
                    f"MOEX {table_name} row skipped: {field_name} is invalid",
                )
            )
            return None, True

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != ""

    @staticmethod
    def _raw_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if not str(key).startswith("__moex_")
        }

    @staticmethod
    def _warning(bond: Bond, message: str) -> MoexCashflowSyncWarning:
        return MoexCashflowSyncWarning(
            bond_id=bond.id,
            secid=bond.secid,
            message=message,
        )

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, MoexIssClientError):
            return str(exc)
        return str(exc)
