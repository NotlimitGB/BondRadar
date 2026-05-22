from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.financial_report_ingestion import (
    FinancialReportImportRunRead,
    FinancialReportIngestRequest,
    FinancialReportIngestResult,
    FinancialReportPreviewResult,
    FinancialReportStatsRead,
    FinancialReportSourceDocumentRead,
)
from app.services.financial_report_ingestion_service import (
    FinancialReportIngestionService,
)
from app.services.financial_report_diagnostics_service import (
    FinancialReportDiagnosticsService,
)


router = APIRouter()


@router.post("/preview", response_model=FinancialReportPreviewResult)
def preview_financial_reports(
    request: FinancialReportIngestRequest,
    db: Session = Depends(get_db),
) -> FinancialReportPreviewResult:
    return FinancialReportIngestionService(db).preview(request)


@router.post("/ingest", response_model=FinancialReportIngestResult)
def ingest_financial_reports(
    request: FinancialReportIngestRequest,
    db: Session = Depends(get_db),
) -> FinancialReportIngestResult:
    return FinancialReportIngestionService(db).ingest(request)


@router.get("/stats", response_model=FinancialReportStatsRead)
def get_financial_report_stats(
    db: Session = Depends(get_db),
) -> FinancialReportStatsRead:
    return FinancialReportIngestionService(db).stats()


@router.get("/diagnostics/company/{company_id}", response_model=dict[str, Any])
def get_company_financial_report_diagnostics(
    company_id: int,
    include_duplicate_context: bool = Query(default=True),
    include_derived_metrics: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return FinancialReportDiagnosticsService(db).get_company_financial_report_diagnostics(
        company_id,
        include_duplicate_context=include_duplicate_context,
        include_derived_metrics=include_derived_metrics,
    )


@router.get("/import-runs", response_model=list[FinancialReportImportRunRead])
def list_financial_report_import_runs(
    source: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[FinancialReportImportRunRead]:
    return FinancialReportIngestionService(db).list_runs(
        source=source,
        limit=limit,
    )


@router.get("/import-runs/{run_id}", response_model=FinancialReportImportRunRead)
def get_financial_report_import_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> FinancialReportImportRunRead:
    return FinancialReportIngestionService(db).get_run(run_id)


@router.get("/source-documents", response_model=list[FinancialReportSourceDocumentRead])
def list_financial_report_source_documents(
    company_id: int | None = Query(default=None, ge=1),
    source: str | None = Query(default=None, min_length=1, max_length=64),
    period_year: int | None = Query(default=None, ge=1900, le=2100),
    period_quarter: int | None = Query(default=None, ge=0, le=4),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[FinancialReportSourceDocumentRead]:
    return FinancialReportIngestionService(db).list_source_documents(
        company_id=company_id,
        source=source,
        period_year=period_year,
        period_quarter=period_quarter,
        limit=limit,
    )
