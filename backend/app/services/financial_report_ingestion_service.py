from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.financial_report import FinancialReport
from app.models.financial_report_import_run import FinancialReportImportRun
from app.models.financial_report_source_document import FinancialReportSourceDocument
from app.schemas.financial_report_ingestion import (
    FinancialReportIngestError,
    FinancialReportIngestRequest,
    FinancialReportIngestResult,
    FinancialReportIngestWarning,
    FinancialReportPreviewResult,
    FinancialReportPreviewRow,
    FinancialReportStatsRead,
    FinancialReportStructuredInput,
)


MONEY_FIELDS = (
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
)
RATIO_FIELDS = ("debt_to_ebitda", "interest_coverage")
NON_NEGATIVE_FIELDS = {
    "revenue",
    "total_debt",
    "cash",
    "short_term_debt",
    "interest_expense",
}
CORE_FIELDS = ("revenue", "ebitda", "total_debt", "equity", "interest_expense")


class FinancialReportRowError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FinancialReportIngestionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def preview(self, request: FinancialReportIngestRequest) -> FinancialReportPreviewResult:
        self._validate_request_shape(request)
        rows: list[FinancialReportPreviewRow] = []
        errors: list[FinancialReportIngestError] = []
        warnings: list[FinancialReportIngestWarning] = []
        valid_rows = would_create = would_update = would_skip = 0
        seen_keys: dict[tuple[int, int, int], int] = {}

        for row_index, row in enumerate(request.rows, start=1):
            period_quarter = self._period_quarter(row)
            row_errors: list[dict[str, Any]] = []
            row_warnings: list[dict[str, Any]] = []
            company: Company | None = None
            identifier_used: str | None = None
            action = "invalid"

            try:
                company, identifier_used = self._resolve_company_with_identifier(row)
                row_warnings.extend(self._row_warnings(row, row_index, period_quarter))
                self._raise_on_invalid_values(row)
                key = (company.id, row.period_year, period_quarter)
                duplicate_first_row = seen_keys.get(key)
                if duplicate_first_row is not None:
                    row_errors.append(
                        self._row_message(
                            row,
                            row_index,
                            f"Duplicate company-period row in request; first seen at row {duplicate_first_row}",
                            period_quarter=period_quarter,
                        )
                    )
                else:
                    seen_keys[key] = row_index
                if not row_errors:
                    report = self._existing_report(company.id, row.period_year, period_quarter)
                    if report is None:
                        action = "create"
                        would_create += 1
                    elif request.rebuild_existing:
                        action = "update"
                        would_update += 1
                    else:
                        action = "skip"
                        would_skip += 1
                    valid_rows += 1
            except FinancialReportRowError as exc:
                row_errors.append(
                    self._row_message(
                        row,
                        row_index,
                        exc.message,
                        period_quarter=period_quarter,
                    )
                )

            error_models = [FinancialReportIngestError(**item) for item in row_errors]
            warning_models = [
                FinancialReportIngestWarning(**item) for item in row_warnings
            ]
            errors.extend(error_models)
            warnings.extend(warning_models)
            rows.append(
                FinancialReportPreviewRow(
                    row_index=row_index,
                    company_id=row.company_id,
                    company_ticker=row.company_ticker,
                    company_inn=row.company_inn,
                    matched_company_id=None if company is None else company.id,
                    matched_company_name=None if company is None else company.name,
                    identifier_used=identifier_used,
                    period_year=row.period_year,
                    period_quarter=period_quarter,
                    would_action=action,
                    errors=error_models,
                    warnings=warning_models,
                )
            )

        return FinancialReportPreviewResult(
            status="failed" if errors else "warning" if warnings else "passed",
            total_rows=len(request.rows),
            valid_rows=valid_rows,
            invalid_rows=len(request.rows) - valid_rows,
            would_create=would_create,
            would_update=would_update,
            would_skip=would_skip,
            rows=rows,
            errors=errors,
            warnings=warnings,
        )

    def ingest(self, request: FinancialReportIngestRequest) -> FinancialReportIngestResult:
        self._validate_request(request)
        run = self._create_run(request)
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        created = updated = skipped = failed = 0

        try:
            for row_index, row in enumerate(request.rows, start=1):
                try:
                    with self.db.begin_nested():
                        action, row_warnings = self._process_row(
                            row,
                            row_index=row_index,
                            request_source=request.source,
                            rebuild_existing=request.rebuild_existing,
                        )
                        self.db.flush()
                    if action == "created":
                        created += 1
                    elif action == "updated":
                        updated += 1
                    elif action == "skipped":
                        skipped += 1
                    warnings.extend(row_warnings)
                except FinancialReportRowError as exc:
                    failed += 1
                    errors.append(self._row_message(row, row_index, exc.message))

            status_value = "completed_with_errors" if errors else "completed"
            self._finish_run(
                run,
                status_value=status_value,
                created=created,
                updated=updated,
                skipped=skipped,
                failed=failed,
                errors=errors,
                warnings=warnings,
            )
            return self._result(run)
        except Exception:
            self.db.rollback()
            run = self.db.get(FinancialReportImportRun, run.id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                self.db.add(run)
                self.db.commit()
            raise

    def list_runs(
        self,
        *,
        source: str | None = None,
        limit: int = 20,
    ) -> list[FinancialReportImportRun]:
        stmt = select(FinancialReportImportRun)
        if source is not None:
            stmt = stmt.where(FinancialReportImportRun.source == source)
        stmt = stmt.order_by(
            FinancialReportImportRun.started_at.desc(),
            FinancialReportImportRun.id.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def get_run(self, run_id: int) -> FinancialReportImportRun:
        run = self.db.get(FinancialReportImportRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="financial report import run not found",
            )
        return run

    def list_source_documents(
        self,
        *,
        company_id: int | None = None,
        source: str | None = None,
        period_year: int | None = None,
        period_quarter: int | None = None,
        limit: int = 100,
    ) -> list[FinancialReportSourceDocument]:
        stmt = select(FinancialReportSourceDocument)
        if company_id is not None:
            stmt = stmt.where(FinancialReportSourceDocument.company_id == company_id)
        if source is not None:
            stmt = stmt.where(FinancialReportSourceDocument.source == source)
        if period_year is not None:
            stmt = stmt.where(FinancialReportSourceDocument.period_year == period_year)
        if period_quarter is not None:
            stmt = stmt.where(
                FinancialReportSourceDocument.period_quarter == period_quarter
            )
        stmt = stmt.order_by(
            FinancialReportSourceDocument.period_year.desc(),
            FinancialReportSourceDocument.period_quarter.desc(),
            FinancialReportSourceDocument.id.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def stats(self) -> FinancialReportStatsRead:
        return FinancialReportStatsRead(
            financial_reports_count=self._count(FinancialReport),
            financial_report_source_documents_count=self._count(
                FinancialReportSourceDocument
            ),
            financial_report_import_runs_count=self._count(FinancialReportImportRun),
        )

    def _process_row(
        self,
        row: FinancialReportStructuredInput,
        *,
        row_index: int,
        request_source: str,
        rebuild_existing: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        company = self._resolve_company(row)
        period_quarter = self._period_quarter(row)
        row_warnings = self._row_warnings(row, row_index, period_quarter)
        self._raise_on_invalid_values(row)
        report = self._existing_report(company.id, row.period_year, period_quarter)

        report_data = self._report_data(row, request_source, period_quarter)
        if report is None:
            report = FinancialReport(company_id=company.id, **report_data)
            self.db.add(report)
            self.db.flush()
            action = "created"
        elif rebuild_existing:
            for field, value in report_data.items():
                setattr(report, field, value)
            self.db.add(report)
            self.db.flush()
            action = "updated"
        else:
            action = "skipped"

        self._upsert_source_document(
            row,
            company_id=company.id,
            financial_report_id=report.id,
            period_quarter=period_quarter,
            source_value=self._effective_source(row, request_source),
            raw_payload=row.model_dump(mode="json"),
            warnings=row_warnings,
            status_value="skipped" if action == "skipped" else "linked",
        )
        return action, row_warnings

    def _resolve_company(self, row: FinancialReportStructuredInput) -> Company:
        company, _identifier_used = self._resolve_company_with_identifier(row)
        return company

    def _resolve_company_with_identifier(
        self,
        row: FinancialReportStructuredInput,
    ) -> tuple[Company, str]:
        if row.company_id is not None:
            company = self.db.get(Company, row.company_id)
            if company is None:
                raise FinancialReportRowError(
                    f"Company not found for company_id={row.company_id}"
                )
            return company, "company_id"
        if row.company_ticker:
            company = self.db.execute(
                select(Company).where(Company.ticker == row.company_ticker)
            ).scalar_one_or_none()
            if company is None:
                raise FinancialReportRowError(
                    f"Company not found for company_ticker={row.company_ticker}"
                )
            return company, "company_ticker"
        if row.company_inn:
            company = self.db.execute(
                select(Company).where(Company.inn == row.company_inn)
            ).scalar_one_or_none()
            if company is None:
                raise FinancialReportRowError(
                    f"Company not found for company_inn={row.company_inn}"
                )
            return company, "company_inn"
        raise FinancialReportRowError("Company identifier is required")

    def _existing_report(
        self,
        company_id: int,
        period_year: int,
        period_quarter: int,
    ) -> FinancialReport | None:
        return self.db.execute(
            select(FinancialReport).where(
                FinancialReport.company_id == company_id,
                FinancialReport.period_year == period_year,
                FinancialReport.period_quarter == period_quarter,
            )
        ).scalar_one_or_none()

    def _report_data(
        self,
        row: FinancialReportStructuredInput,
        request_source: str,
        period_quarter: int,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "period_year": row.period_year,
            "period_quarter": period_quarter,
            "period_start_date": row.period_start_date,
            "period_end_date": row.period_end_date,
            "published_at": row.published_at,
            "currency": row.currency or "RUB",
            "source": self._effective_source(row, request_source),
        }
        for field in MONEY_FIELDS + RATIO_FIELDS:
            data[field] = self._decimal(getattr(row, field), field)
        return data

    def _upsert_source_document(
        self,
        row: FinancialReportStructuredInput,
        *,
        company_id: int,
        financial_report_id: int,
        period_quarter: int,
        source_value: str,
        raw_payload: dict[str, Any],
        warnings: list[dict[str, Any]],
        status_value: str,
    ) -> FinancialReportSourceDocument:
        stmt = select(FinancialReportSourceDocument).where(
            FinancialReportSourceDocument.company_id == company_id,
            FinancialReportSourceDocument.source == source_value,
            FinancialReportSourceDocument.period_year == row.period_year,
            FinancialReportSourceDocument.period_quarter == period_quarter,
        )
        if row.source_file_name:
            stmt = stmt.where(
                FinancialReportSourceDocument.source_file_name == row.source_file_name
            )
        elif row.source_url:
            stmt = stmt.where(FinancialReportSourceDocument.source_url == row.source_url)
        else:
            stmt = stmt.where(
                FinancialReportSourceDocument.source_file_name.is_(None),
                FinancialReportSourceDocument.source_url.is_(None),
            )

        document = self.db.execute(stmt).scalar_one_or_none()
        data = {
            "company_id": company_id,
            "financial_report_id": financial_report_id,
            "source": source_value,
            "source_url": row.source_url,
            "source_file_name": row.source_file_name,
            "report_type": row.report_type,
            "period_year": row.period_year,
            "period_quarter": period_quarter,
            "period_start_date": row.period_start_date,
            "period_end_date": row.period_end_date,
            "published_at": row.published_at,
            "document_date": row.document_date,
            "currency": row.currency or "RUB",
            "status": status_value,
            "raw_payload": raw_payload,
            "parse_warnings": warnings,
            "parse_errors": [],
        }
        if document is None:
            document = FinancialReportSourceDocument(**data)
            self.db.add(document)
            return document
        for field, value in data.items():
            setattr(document, field, value)
        self.db.add(document)
        return document

    @staticmethod
    def _effective_source(
        row: FinancialReportStructuredInput,
        request_source: str,
    ) -> str:
        if row.source == "manual" and request_source != "manual":
            return request_source
        return row.source or request_source

    def _row_warnings(
        self,
        row: FinancialReportStructuredInput,
        row_index: int,
        period_quarter: int,
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if row.published_at is None:
            warnings.append(
                self._row_message(
                    row,
                    row_index,
                    "published_at is missing",
                    period_quarter=period_quarter,
                )
            )
        if row.currency is None:
            warnings.append(
                self._row_message(
                    row,
                    row_index,
                    "currency is missing, default RUB was used",
                    period_quarter=period_quarter,
                )
            )
        for field in CORE_FIELDS:
            if self._decimal(getattr(row, field), field) is None:
                warnings.append(
                    self._row_message(
                        row,
                        row_index,
                        f"{field} is missing",
                        period_quarter=period_quarter,
                    )
                )
        for field in MONEY_FIELDS + RATIO_FIELDS:
            value = self._decimal(getattr(row, field), field)
            if value is None:
                continue
            if field in NON_NEGATIVE_FIELDS and value < 0:
                warnings.append(
                    self._row_message(
                        row,
                        row_index,
                        f"Suspicious negative {field}",
                        period_quarter=period_quarter,
                    )
                )
            elif field == "equity" and value < 0:
                warnings.append(
                    self._row_message(
                        row,
                        row_index,
                        "Suspicious negative equity",
                        period_quarter=period_quarter,
                    )
                )
        return warnings

    def _raise_on_invalid_values(self, row: FinancialReportStructuredInput) -> None:
        for field in MONEY_FIELDS + RATIO_FIELDS:
            value = self._decimal(getattr(row, field), field)
            if value is None:
                continue
            if field in NON_NEGATIVE_FIELDS and value < 0:
                raise FinancialReportRowError(f"{field} cannot be negative")

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            value = stripped.replace(" ", "").replace(",", ".")
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise FinancialReportRowError(f"Invalid decimal value for {field}") from exc

    def _validate_request(self, request: FinancialReportIngestRequest) -> None:
        self._validate_request_shape(request)
        seen: dict[tuple[str, str, int, int], int] = {}
        for row_index, row in enumerate(request.rows, start=1):
            identifier_key = self._input_identifier_key(row)
            if identifier_key is None:
                continue
            key = (*identifier_key, row.period_year, self._period_quarter(row))
            first_row = seen.get(key)
            if first_row is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "duplicate company-period rows in request: "
                        f"row {row_index} duplicates row {first_row}"
                    ),
                )
            seen[key] = row_index

    @staticmethod
    def _validate_request_shape(request: FinancialReportIngestRequest) -> None:
        if not request.rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rows cannot be empty",
            )
        for row in request.rows:
            period_quarter = FinancialReportIngestionService._period_quarter(row)
            if period_quarter < 0 or period_quarter > 4:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="period_quarter must be between 0 and 4",
                )
            if (
                row.period_start_date is not None
                and row.period_end_date is not None
                and row.period_start_date > row.period_end_date
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date range",
                )

    @staticmethod
    def _period_quarter(row: FinancialReportStructuredInput) -> int:
        return row.period_quarter if row.period_quarter is not None else 0

    @staticmethod
    def _input_identifier_key(
        row: FinancialReportStructuredInput,
    ) -> tuple[str, str] | None:
        if row.company_id is not None:
            return ("company_id", str(row.company_id))
        if row.company_ticker:
            return ("company_ticker", row.company_ticker.strip())
        if row.company_inn:
            return ("company_inn", row.company_inn.strip())
        return None

    def _create_run(
        self,
        request: FinancialReportIngestRequest,
    ) -> FinancialReportImportRun:
        run = FinancialReportImportRun(
            source=request.source,
            input_type="json",
            status="running",
            params_json={
                "source": request.source,
                "input_type": "json",
                "rebuild_existing": request.rebuild_existing,
            },
            errors_json=[],
            warnings_json=[],
            total_rows=len(request.rows),
            created=0,
            updated=0,
            skipped=0,
            failed=0,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _finish_run(
        self,
        run: FinancialReportImportRun,
        *,
        status_value: str,
        created: int,
        updated: int,
        skipped: int,
        failed: int,
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        run.status = status_value
        run.finished_at = datetime.now(timezone.utc)
        run.created = created
        run.updated = updated
        run.skipped = skipped
        run.failed = failed
        run.errors_json = errors
        run.warnings_json = warnings
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

    @staticmethod
    def _row_message(
        row: FinancialReportStructuredInput,
        row_index: int,
        message: str,
        *,
        period_quarter: int | None = None,
    ) -> dict[str, Any]:
        return {
            "row_index": row_index,
            "company_id": row.company_id,
            "company_ticker": row.company_ticker,
            "company_inn": row.company_inn,
            "period_year": row.period_year,
            "period_quarter": (
                period_quarter
                if period_quarter is not None
                else row.period_quarter
            ),
            "message": message,
        }

    @staticmethod
    def _result(run: FinancialReportImportRun) -> FinancialReportIngestResult:
        return FinancialReportIngestResult(
            run_id=run.id,
            status=run.status,
            total_rows=run.total_rows,
            created=run.created,
            updated=run.updated,
            skipped=run.skipped,
            failed=run.failed,
            errors=[
                FinancialReportIngestError(**error)
                for error in run.errors_json
            ],
            warnings=[
                FinancialReportIngestWarning(**warning)
                for warning in run.warnings_json
            ],
        )

    def _count(self, model: type[Any]) -> int:
        return int(self.db.scalar(select(func.count()).select_from(model)) or 0)
