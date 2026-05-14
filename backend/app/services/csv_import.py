from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.financial_report import FinancialReport


BOND_HEADERS = {
    "company_inn",
    "company_name",
    "company_ticker",
    "company_sector",
    "company_country",
    "company_credit_rating",
    "company_notes",
    "isin",
    "secid",
    "bond_name",
    "currency",
    "nominal_value",
    "current_price",
    "coupon_rate",
    "yield_to_maturity",
    "duration_years",
    "volume",
    "maturity_date",
    "offer_date",
    "is_floating_coupon",
    "is_subordinated",
    "is_perpetual",
    "amortization",
    "liquidity_score",
    "risk_notes",
}
BOND_REQUIRED_HEADERS = {"company_inn", "bond_name"}

REPORT_HEADERS = {
    "company_inn",
    "company_name",
    "company_ticker",
    "company_sector",
    "company_country",
    "company_credit_rating",
    "period_year",
    "period_quarter",
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
    "source",
}
REPORT_REQUIRED_HEADERS = {"company_inn", "period_year"}

COMPANY_FIELD_MAP = {
    "company_name": "name",
    "company_ticker": "ticker",
    "company_sector": "sector",
    "company_country": "country",
    "company_credit_rating": "credit_rating",
    "company_notes": "notes",
}
BOND_FIELD_MAP = {
    "bond_name": "name",
    "isin": "isin",
    "secid": "secid",
    "currency": "currency",
    "nominal_value": "nominal_value",
    "current_price": "current_price",
    "coupon_rate": "coupon_rate",
    "yield_to_maturity": "yield_to_maturity",
    "duration_years": "duration_years",
    "volume": "volume",
    "maturity_date": "maturity_date",
    "offer_date": "offer_date",
    "is_floating_coupon": "is_floating_coupon",
    "is_subordinated": "is_subordinated",
    "is_perpetual": "is_perpetual",
    "amortization": "amortization",
    "liquidity_score": "liquidity_score",
    "risk_notes": "risk_notes",
}
REPORT_FIELD_MAP = {
    "period_year": "period_year",
    "period_quarter": "period_quarter",
    "revenue": "revenue",
    "ebitda": "ebitda",
    "net_debt": "net_debt",
    "total_debt": "total_debt",
    "cash": "cash",
    "equity": "equity",
    "short_term_debt": "short_term_debt",
    "operating_cash_flow": "operating_cash_flow",
    "net_profit": "net_profit",
    "interest_expense": "interest_expense",
    "source": "source",
}
DECIMAL_FIELDS = {
    "nominal_value",
    "current_price",
    "coupon_rate",
    "yield_to_maturity",
    "duration_years",
    "volume",
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
}
DATE_FIELDS = {"maturity_date", "offer_date"}
BOOL_FIELDS = {
    "is_floating_coupon",
    "is_subordinated",
    "is_perpetual",
    "amortization",
}
INT_FIELDS = {"liquidity_score", "period_year", "period_quarter"}


class RowImportError(Exception):
    def __init__(self, message: str, identifier: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.identifier = identifier


@dataclass
class ImportStats:
    total_rows: int
    created: int = 0
    updated: int = 0
    skipped: int = 0
    companies_created: int = 0
    companies_updated: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "processed_rows": self.total_rows,
            "failed_rows": self.skipped,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "companies_created": self.companies_created,
            "companies_updated": self.companies_updated,
            "errors": self.errors,
        }


class CSVImportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def import_bonds_csv(self, file: UploadFile) -> dict[str, Any]:
        rows = await self._read_csv(file, BOND_REQUIRED_HEADERS)
        stats = ImportStats(total_rows=len(rows))

        for row_number, row in rows:
            try:
                with self.db.begin_nested():
                    result = self._process_bond_row(row)
                    self.db.flush()
                self._apply_result(stats, result)
            except RowImportError as exc:
                self._add_error(stats, row_number, exc.identifier, exc.message)
            except IntegrityError as exc:
                self._add_error(stats, row_number, self._row_identifier(row), str(exc.orig))

        self.db.commit()
        return stats.as_dict()

    async def import_reports_csv(self, file: UploadFile) -> dict[str, Any]:
        rows = await self._read_csv(file, REPORT_REQUIRED_HEADERS)
        stats = ImportStats(total_rows=len(rows))

        for row_number, row in rows:
            try:
                with self.db.begin_nested():
                    result = self._process_report_row(row)
                    self.db.flush()
                self._apply_result(stats, result)
            except RowImportError as exc:
                self._add_error(stats, row_number, exc.identifier, exc.message)
            except IntegrityError as exc:
                self._add_error(stats, row_number, self._row_identifier(row), str(exc.orig))

        self.db.commit()
        return stats.as_dict()

    async def _read_csv(
        self, file: UploadFile, required_headers: set[str]
    ) -> list[tuple[int, dict[str, str | None]]]:
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CSV files are supported",
            )

        try:
            content = await file.read()
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid CSV file",
            ) from exc

        try:
            if text.strip():
                try:
                    dialect = csv.Sniffer().sniff(text[:4096])
                except csv.Error:
                    dialect = csv.excel
            else:
                dialect = csv.excel
            reader = csv.reader(StringIO(text), dialect)
            header_row = next(reader, None)
            if header_row is None:
                raise csv.Error("missing header")

            headers = [header.strip() for header in header_row]
            missing_headers = sorted(required_headers - set(headers))
            if missing_headers:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Missing required headers: {', '.join(missing_headers)}",
                )

            rows: list[tuple[int, dict[str, str | None]]] = []
            for row_number, raw_row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in raw_row):
                    continue
                values = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
                rows.append(
                    (
                        row_number,
                        {
                            header: values[index].strip()
                            if index < len(values)
                            else None
                            for index, header in enumerate(headers)
                        },
                    )
                )
            return rows
        except HTTPException:
            raise
        except csv.Error as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid CSV file",
            ) from exc

    def _process_bond_row(self, row: dict[str, str | None]) -> dict[str, str | None]:
        company = self._upsert_company(row)
        isin = self._text(row, "isin")
        secid = self._text(row, "secid")
        if not isin and not secid:
            raise RowImportError("Either isin or secid is required", None)

        if isin:
            bond = self.db.execute(
                select(Bond).where(Bond.isin == isin)
            ).scalar_one_or_none()
        else:
            bond = self.db.execute(
                select(Bond).where(Bond.secid == secid)
            ).scalar_one_or_none()

        bond_data = self._bond_data(row, include_defaults=bond is None)
        bond_data["company_id"] = company["company"].id
        if bond is None:
            if not self._text(row, "bond_name"):
                raise RowImportError("bond_name is required", isin or secid)
            bond = Bond(**bond_data)
            self.db.add(bond)
            entity_action = "created"
        else:
            for field, value in bond_data.items():
                setattr(bond, field, value)
            self.db.add(bond)
            entity_action = "updated"

        return {
            "entity_action": entity_action,
            "company_action": company["action"],
        }

    def _process_report_row(self, row: dict[str, str | None]) -> dict[str, str | None]:
        company = self._upsert_company(row)
        period_year = self._int(row, "period_year", required=True)
        period_quarter = self._int(row, "period_quarter") or 0
        if period_quarter < 0 or period_quarter > 4:
            raise RowImportError("period_quarter must be between 0 and 4")

        report = self.db.execute(
            select(FinancialReport).where(
                FinancialReport.company_id == company["company"].id,
                FinancialReport.period_year == period_year,
                FinancialReport.period_quarter == period_quarter,
            )
        ).scalar_one_or_none()
        report_data = self._report_data(row)
        report_data["company_id"] = company["company"].id
        report_data["period_year"] = period_year
        report_data["period_quarter"] = period_quarter

        if report is None:
            report = FinancialReport(**report_data)
            self.db.add(report)
            entity_action = "created"
        else:
            for field, value in report_data.items():
                setattr(report, field, value)
            self.db.add(report)
            entity_action = "updated"

        return {
            "entity_action": entity_action,
            "company_action": company["action"],
        }

    def _upsert_company(self, row: dict[str, str | None]) -> dict[str, Any]:
        company_inn = self._text(row, "company_inn")
        if not company_inn:
            raise RowImportError("company_inn is required", None)

        company = self.db.execute(
            select(Company).where(Company.inn == company_inn)
        ).scalar_one_or_none()
        data = self._company_data(row)

        if company is None:
            company_name = self._text(row, "company_name")
            if not company_name:
                raise RowImportError(
                    "Company with provided inn not found and company_name is missing",
                    company_inn,
                )
            data["inn"] = company_inn
            data["name"] = company_name
            data["ticker"] = self._text(row, "company_ticker") or company_inn
            data["country"] = self._text(row, "company_country") or "RU"
            company = Company(**data)
            self.db.add(company)
            self.db.flush()
            return {"company": company, "action": "created"}

        changed = False
        for field, value in data.items():
            if value is not None and getattr(company, field) != value:
                setattr(company, field, value)
                changed = True
        if changed:
            self.db.add(company)
            return {"company": company, "action": "updated"}
        return {"company": company, "action": None}

    def _company_data(self, row: dict[str, str | None]) -> dict[str, Any]:
        data = {}
        for csv_field, model_field in COMPANY_FIELD_MAP.items():
            value = self._text(row, csv_field)
            if value is not None:
                data[model_field] = value
        return data

    def _bond_data(
        self, row: dict[str, str | None], *, include_defaults: bool
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for csv_field, model_field in BOND_FIELD_MAP.items():
            value = self._parsed_value(row, csv_field)
            if value is not None:
                data[model_field] = value

        if include_defaults:
            data.setdefault("currency", "RUB")
            data.setdefault("is_floating_coupon", False)
            data.setdefault("is_subordinated", False)
            data.setdefault("is_perpetual", False)
        return data

    def _report_data(self, row: dict[str, str | None]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for csv_field, model_field in REPORT_FIELD_MAP.items():
            value = self._parsed_value(row, csv_field)
            if value is not None:
                data[model_field] = value
        return data

    def _parsed_value(self, row: dict[str, str | None], field: str) -> Any:
        if field in DECIMAL_FIELDS:
            return self._decimal(row, field)
        if field in DATE_FIELDS:
            return self._date(row, field)
        if field in BOOL_FIELDS:
            return self._bool(row, field)
        if field in INT_FIELDS:
            return self._int(row, field)
        return self._text(row, field)

    def _text(self, row: dict[str, str | None], field: str) -> str | None:
        value = row.get(field)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def _decimal(self, row: dict[str, str | None], field: str) -> Decimal | None:
        value = self._text(row, field)
        if value is None:
            return None
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise RowImportError(f"Invalid decimal value for {field}") from exc

    def _date(self, row: dict[str, str | None], field: str) -> date | None:
        value = self._text(row, field)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RowImportError(f"Invalid date value for {field}") from exc

    def _bool(self, row: dict[str, str | None], field: str) -> bool | None:
        value = self._text(row, field)
        if value is None:
            return None
        normalized = value.lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise RowImportError(f"Invalid boolean value for {field}")

    def _int(
        self,
        row: dict[str, str | None],
        field: str,
        *,
        required: bool = False,
    ) -> int | None:
        value = self._text(row, field)
        if value is None:
            if required:
                raise RowImportError(f"{field} is required")
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise RowImportError(f"Invalid integer value for {field}") from exc

    @staticmethod
    def _row_identifier(row: dict[str, str | None]) -> str | None:
        for field in ("isin", "secid", "company_inn"):
            value = row.get(field)
            if value:
                return value
        return None

    @staticmethod
    def _apply_result(stats: ImportStats, result: dict[str, str | None]) -> None:
        if result["entity_action"] == "created":
            stats.created += 1
        elif result["entity_action"] == "updated":
            stats.updated += 1

        if result["company_action"] == "created":
            stats.companies_created += 1
        elif result["company_action"] == "updated":
            stats.companies_updated += 1

    @staticmethod
    def _add_error(
        stats: ImportStats,
        row_number: int,
        identifier: str | None,
        error: str,
    ) -> None:
        stats.skipped += 1
        stats.errors.append(
            {
                "row_number": row_number,
                "identifier": identifier,
                "error": error,
            }
        )
